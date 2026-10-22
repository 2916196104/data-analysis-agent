"""One durable task worker per business database; existing business functions do the work."""

import logging
import os
import threading
import uuid
from pathlib import Path

from pydantic import ValidationError

from .config import AgentSettings, ConfigurationError, read_settings
from .database import DATABASE_ERRORS, PostgresConnection, database_target, is_postgres
from .review_api_schemas import AnalysisParameters, ImportParameters, LabelParameters, ReplayParameters
from .review_labeler import PROMPT_VERSION, run_labeling
from .review_loader import ReviewDataError
from .review_monitor import replay_monthly, run_analysis
from .review_store import import_amazon_jsonl
from .review_task_store import (
    TaskConflict, check_task_database, claim_task, enqueue, finish_task, get_task,
    heartbeat, update_progress, worker_started,
)

logger = logging.getLogger(__name__)
PARAMETERS = {"import": ImportParameters, "label": LabelParameters,
              "analysis": AnalysisParameters, "replay": ReplayParameters}


class TaskInterrupted(RuntimeError):
    pass


def import_path(value, root):
    """HTTP imports can only use files inside the server-selected import directory."""
    root = Path(root).resolve()
    candidate = Path(value)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReviewDataError("导入文件不存在或不在服务配置的 import-root 内。")
    if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")):
        raise ReviewDataError("只支持 .jsonl 或 .jsonl.gz 导入文件。")
    return str(path)


def configured_identity(env_file):
    settings = AgentSettings.from_values(read_settings(env_file))
    return settings, {"model": settings.model, "base_url": settings.base_url, "prompt_version": PROMPT_VERSION}


def prepare_parameters(kind, parameters, *, import_root, env_file):
    values = PARAMETERS[kind].model_validate(parameters).model_dump()
    if kind == "import":
        for name in ("reviews_path", "meta_path"):
            if values[name] is not None:
                values[name] = import_path(values[name], import_root)
    elif kind == "label":
        _, values["model_identity"] = configured_identity(env_file)
    return values


def retry_task(task_db, task_id, *, import_root, env_file, idempotency_key=None):
    task = get_task(task_db, task_id)
    if task["status"] not in ("failed", "partial"):
        raise TaskConflict("只能重试失败或部分完成的任务。")
    parameters = dict(task["parameters"])
    if task["kind"] == "label":
        _, identity = configured_identity(env_file)
        if parameters["model_identity"] != identity:
            raise TaskConflict("模型配置已变化，请使用新配置提交新标注任务。")
    elif task["kind"] == "import":
        for name in ("reviews_path", "meta_path"):
            if parameters.get(name) is not None:
                import_path(parameters[name], import_root)
    return enqueue(task_db, task["kind"], parameters,
                   idempotency_key=idempotency_key, parent_task_id=task_id)


class WorkerLock:
    """Hold an OS file lock or PostgreSQL session lock for the worker's lifetime."""

    def __init__(self, business_db):
        self.business_db = database_target(business_db)
        self.path = None if is_postgres(business_db) else Path(str(self.business_db) + ".worker.lock")
        self.handle = None

    def acquire(self):
        if is_postgres(self.business_db):
            connection = PostgresConnection(self.business_db, namespace="worker")
            try:
                acquired = connection.execute("SELECT pg_try_advisory_lock(?)", (connection._lock_key(),)).fetchone()[0]
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
