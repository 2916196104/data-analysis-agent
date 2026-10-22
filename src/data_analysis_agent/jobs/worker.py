"""One durable task worker per business database; existing business functions do the work."""

import logging
import os
import threading
import uuid
from pathlib import Path

from pydantic import ValidationError

from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.core.database import DATABASE_ERRORS, PostgresConnection, connect_database, database_target, is_postgres
from data_analysis_agent.jobs.parameters import PARAMETERS
# Keep previous task-worker imports available without loading the model SDK.
from data_analysis_agent.jobs.submission import configured_identity, import_path, prepare_parameters, retry_task
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitoring import replay_monthly, run_analysis
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.jobs.store import (
    TaskConflict, check_task_database, claim_task, finish_task,
    heartbeat, update_progress, worker_started,
)

logger = logging.getLogger(__name__)


class TaskInterrupted(RuntimeError):
    pass


class WorkerLock:
    """Hold an OS file lock or PostgreSQL session lock for the worker's lifetime."""

    def __init__(self, business_db):
        self.business_db = database_target(business_db)
        self.path = None if is_postgres(business_db) else Path(str(self.business_db) + ".worker.lock")
        self.handle = None

    def acquire(self):
        if is_postgres(self.business_db):
            connection = connect_database(self.business_db, namespace="worker")
            try:
                acquired = connection.try_session_lock()
                if not acquired:
                    raise TaskConflict("该业务库已有后台 worker；请停止旧 worker，或用 serve --no-worker 连接它。")
            except Exception:
                connection.close()
                raise
            self.handle = connection
            return
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise TaskConflict("该业务库已有后台 worker；请停止旧 worker，或用 serve --no-worker 连接它。") from exc
        self.handle = handle

    def release(self):
        if self.handle is not None:
            handle = self.handle
            if isinstance(handle, PostgresConnection):
                handle.close()
                self.handle = None
                return
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                self.handle = None


class TaskWorker:
    def __init__(self, business_db, task_db, *, import_root, env_file):
        self.business_db = database_target(business_db)
        self.task_db = database_target(task_db)
        self.import_root = Path(import_root).resolve()
        self.env_file = Path(env_file).resolve()
        self.worker_id = uuid.uuid4().hex
        self.lock = WorkerLock(self.business_db)
        self.stop_requested = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is not None:
            raise TaskConflict("同一个 worker 实例只能启动一次。")
        if not self.import_root.is_dir():
            raise ReviewDataError("import-root 目录不存在。")
        check_task_database(self.task_db, self.business_db)
        self.lock.acquire()
        try:
            worker_started(self.task_db, self.worker_id)
            self.thread = threading.Thread(target=self._loop, name="review-task-worker")
            self.thread.start()
        except Exception:
            self.lock.release()
            raise

    def stop(self):
        self.stop_requested.set()
        if self.thread is not None:
            self.thread.join()

    def _heartbeat(self):
        while not self.heartbeat_stop.wait(5):
            try:
                heartbeat(self.task_db, self.worker_id)
            except Exception as exc:
                logger.error("Worker heartbeat failed (%s)", type(exc).__name__)
                self.stop_requested.set()
                return

    def _loop(self):
        pulse = threading.Thread(target=self._heartbeat, name="review-task-heartbeat")
        pulse.start()
        try:
            while not self.stop_requested.is_set():
                if isinstance(self.lock.handle, PostgresConnection):
                    self.lock.handle.execute("SELECT 1")
                task = claim_task(self.task_db, self.worker_id)
                if task is None:
                    self.stop_requested.wait(1)
                    continue
                self._execute(task)
        except Exception as exc:
            # Do not log exception text: model/provider errors may contain credentials.
            logger.error("Worker stopped unexpectedly (%s); restart to recover task state", type(exc).__name__)
        finally:
            self.stop_requested.set()
            self.heartbeat_stop.set()
            pulse.join()
            try:
                heartbeat(self.task_db, self.worker_id, stopped=True)
            finally:
                self.lock.release()

    def _execute(self, task):
        def progress(value):
            if isinstance(self.lock.handle, PostgresConnection):
                self.lock.handle.execute("SELECT 1")
            if self.stop_requested.is_set():
                raise TaskInterrupted()
            update_progress(self.task_db, task["task_id"], self.worker_id, value)

        try:
            progress({"stage": "starting", "completed": 0, "total": None, "unit": "items"})
            values = dict(task["parameters"])
            identity = values.pop("model_identity", None)
            values = PARAMETERS[task["kind"]].model_validate(values).model_dump()
            if task["kind"] == "import":
                for name in ("reviews_path", "meta_path"):
                    if values[name] is not None:
                        values[name] = import_path(values[name], self.import_root)
                result = import_amazon_jsonl(self.business_db, **values, progress=progress)
            elif task["kind"] == "label":
                from data_analysis_agent.reviews.labeling import run_labeling

                settings, current_identity = configured_identity(self.env_file)
                if identity != current_identity:
                    raise ConfigurationError("模型配置与入队时不同，任务未调用模型；请重新提交标注任务。")
                if task["parent_task_id"] is not None:
                    values["retry_failed"] = True
                result = run_labeling(self.business_db, **values, settings=settings, progress=progress)
            elif task["kind"] == "analysis":
                progress({"stage": "analysis", "completed": 0, "total": 1, "unit": "windows"})
                result = run_analysis(self.business_db, **values)
                progress({"stage": "analysis", "completed": 1, "total": 1, "unit": "windows",
                          "run_id": result["run_id"]})
            else:
                result = replay_monthly(self.business_db, **values, progress=progress)
            partial = result.get("status") == "partial" or result.get("complete") is False
            finish_task(self.task_db, task["task_id"], self.worker_id,
                        status="partial" if partial else "succeeded", result=result,
                        error={"code": "partial_result", "message": "部分记录或月份未完成，请查看 result 后显式重试。"} if partial else None)
        except Exception as exc:
            if isinstance(exc, TaskInterrupted):
                error = {"code": "worker_interrupted", "message": "worker 正在停止；已提交的业务结果保留，请核查后显式重试。"}
            elif isinstance(exc, (ConfigurationError, ReviewDataError)):
                error = {"code": "configuration_error" if isinstance(exc, ConfigurationError) else "business_error",
                         "message": str(exc)}
            elif isinstance(exc, DATABASE_ERRORS):
                error = {"code": "database_error", "message": "数据库忙、结构不匹配或写入失败，请检查后重试。"}
            elif isinstance(exc, OSError):
                error = {"code": "file_error", "message": "文件读写失败，请检查导入文件、磁盘和目录权限。"}
            elif isinstance(exc, ValidationError):
                error = {"code": "validation_error", "message": "已保存的任务参数不符合当前版本，请重新提交任务。"}
            else:
                error = {"code": "execution_error", "message": "任务执行失败，请检查文件、数据库及服务配置后重试。"}
            logger.warning("Task %s failed (%s)", task["task_id"], type(exc).__name__)
            finish_task(self.task_db, task["task_id"], self.worker_id, status="failed", error=error)
