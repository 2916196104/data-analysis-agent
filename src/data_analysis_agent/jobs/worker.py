"""每个业务库只运行一个持久任务 worker，具体工作委托给现有业务函数。"""

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.core.database import (
    DATABASE_ERRORS,
    PostgresConnection,
    connect_database,
    database_target,
    is_postgres,
)
from data_analysis_agent.jobs.parameters import PARAMETERS
# 保留这些导出，兼容旧入口；模型 SDK 仅在执行标注或 Agent 问答时加载。
from data_analysis_agent.jobs.submission import configured_agent_identity, configured_identity, import_path, prepare_parameters, retry_task
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitoring import replay_monthly, run_analysis
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.jobs.store import (
    TaskConflict, check_task_database, claim_task, finish_task,
    heartbeat, update_progress, worker_started,
)

logger = logging.getLogger(__name__)


class TaskInterrupted(RuntimeError):
    """worker 停止时中断当前任务的进度回调。"""

    pass


class WorkerLock:
    """在 worker 整个生命周期内持有操作系统文件锁或 PostgreSQL 会话锁。"""

    def __init__(self, business_db: str | Path):
        self.business_db = database_target(business_db)
        self.path = None if is_postgres(business_db) else Path(str(self.business_db) + ".worker.lock")
        self.handle = None

    def acquire(self) -> None:
        if is_postgres(self.business_db):
            connection = connect_database(self.business_db, namespace="worker")
            try:
                acquired = connection.try_session_lock()
                if not acquired:
                    raise TaskConflict(
                        "该业务库已有后台 worker；请停止旧 worker，"
                        "或用 serve --no-worker 连接它。"
                    )
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
            raise TaskConflict(
                "该业务库已有后台 worker；请停止旧 worker，"
                "或用 serve --no-worker 连接它。"
            ) from exc
        self.handle = handle

    def release(self) -> None:
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
    """串行领取持久任务，并维护 worker 心跳和任务最终状态。"""

    def __init__(
        self,
        business_db: str | Path,
        task_db: str | Path,
        *,
        import_root: str | Path,
        env_file: str | Path,
    ) -> None:
        self.business_db = database_target(business_db)
        self.task_db = database_target(task_db)
        self.import_root = Path(import_root).resolve()
        self.env_file = Path(env_file).resolve()
        self.worker_id = uuid.uuid4().hex
        self.lock = WorkerLock(self.business_db)
        self.stop_requested = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.thread = None

    def start(self) -> None:
        if self.thread is not None:
            raise TaskConflict("同一个 worker 实例只能启动一次。")
        if not self.import_root.is_dir():
            raise ReviewDataError("import-root 目录不存在。")
        check_task_database(self.task_db, self.business_db)
        self.lock.acquire()
        try:
            worker_started(self.task_db, self.worker_id)
            logger.info("后台执行器启动 worker_id=%s", self.worker_id)
            self.thread = threading.Thread(target=self._loop, name="review-task-worker")
            self.thread.start()
        except Exception:
            self.lock.release()
            raise

    def stop(self) -> None:
        self.stop_requested.set()
        if self.thread is not None:
            self.thread.join()

    def _heartbeat(self) -> None:
        while not self.heartbeat_stop.wait(5):
            try:
                heartbeat(self.task_db, self.worker_id)
            except Exception as exc:
                logger.error("Worker heartbeat failed (%s)", type(exc).__name__)
                self.stop_requested.set()
                return

    def _loop(self) -> None:
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
            # 不要记录异常文本：模型或服务商错误可能包含凭据。
            logger.error("Worker stopped unexpectedly (%s); restart to recover task state", type(exc).__name__)
        finally:
            self.stop_requested.set()
            self.heartbeat_stop.set()
            pulse.join()
            try:
                heartbeat(self.task_db, self.worker_id, stopped=True)
            finally:
                self.lock.release()
                logger.info("后台执行器停止 worker_id=%s", self.worker_id)

    def _execute(self, task: dict[str, Any]) -> None:
        """执行一个已领取任务；所有异常都转换成可持久化的稳定错误码。"""
        started = time.monotonic()
        logger.info("任务开始 task_id=%s kind=%s", task["task_id"], task["kind"])

        def report_progress(value: dict[str, Any]) -> None:
            if isinstance(self.lock.handle, PostgresConnection):
                self.lock.handle.execute("SELECT 1")
            if self.stop_requested.is_set():
                raise TaskInterrupted()
            update_progress(self.task_db, task["task_id"], self.worker_id, value)
            if value["stage"] == "labeling":
                logger.info(
                    "标注进度 task_id=%s %s/%s 成功=%s 失败=%s 模型调用=%s",
                    task["task_id"], value["completed"], value["total"],
                    value.get("successful_reviews", 0), value.get("failed_reviews", 0),
                    value.get("model_calls", 0),
                )
            else:
                total = value["total"] if value["total"] is not None else "未知"
                logger.info(
                    "任务进度 task_id=%s stage=%s 已处理=%s/%s unit=%s",
                    task["task_id"], value["stage"], value["completed"], total, value["unit"],
                )

        try:
            report_progress({"stage": "starting", "completed": 0, "total": None, "unit": "items"})
            parameters = dict(task["parameters"])
            queued_identity = parameters.pop("model_identity", None)
            parameters = PARAMETERS[task["kind"]].model_validate(parameters).model_dump()

            if task["kind"] == "import":
                for name in ("reviews_path", "meta_path"):
                    if parameters[name] is not None:
                        parameters[name] = import_path(parameters[name], self.import_root, "meta" if name == "meta_path" else "reviews")
                result = import_amazon_jsonl(
                    self.business_db, **parameters, progress=report_progress,
                )
            elif task["kind"] == "label":
                from data_analysis_agent.reviews.labeling import run_labeling

                settings, current_identity = configured_identity(self.env_file)
                if queued_identity != current_identity:
                    raise ConfigurationError("模型配置与入队时不同，任务未调用模型；请重新提交标注任务。")
                if task["parent_task_id"] is not None and not parameters["retry_failed"]:
                    parameters["retry_transient"] = True
                result = run_labeling(
                    self.business_db,
                    **parameters,
                    settings=settings,
                    progress=report_progress,
                )
            elif task["kind"] == "analysis":
                question = parameters.pop("question", None)
                if question is not None:
                    from data_analysis_agent.agents.reviews import run_review_agent

                    settings, current_identity = configured_agent_identity(self.env_file)
                    if queued_identity != current_identity:
                        raise ConfigurationError("Agent 模型配置与入队时不同，请重新提交问答任务。")
                    parameters.pop("rules")
                    result = run_review_agent(self.business_db, question, scope=parameters,
                                              settings=settings, progress=report_progress)
                    if result.get("status") not in ("ok", "unsupported") or (
                        result.get("validation", {}).get("status") == "failed"
                    ):
                        result["status"] = "error"
                        result.setdefault("error", {"code": "agent_error", "message": "分析结果未通过校验，请重试。"})
                        # 部分工具结果保留用于排障；未经确认的正文不发布。
                        result.pop("answer", None)
                        result.pop("answer_sections", None)
                        finish_task(self.task_db, task["task_id"], self.worker_id,
                                    status="failed", result=result, error=result["error"])
                        logger.warning("任务失败 task_id=%s kind=analysis error_code=%s 耗时=%.2fs",
                                       task["task_id"], result["error"]["code"], time.monotonic() - started)
                        return
                    report_progress({"stage": "agent_completed", "completed": result["tool_calls"],
                                     "total": result["tool_calls"], "unit": "tools"})
                else:
                    report_progress({"stage": "analysis", "completed": 0, "total": 1, "unit": "windows"})
                    result = run_analysis(self.business_db, **parameters)
                    report_progress({
                        "stage": "analysis", "completed": 1, "total": 1,
                        "unit": "windows", "run_id": result["run_id"],
                    })
            else:
                result = replay_monthly(
                    self.business_db, **parameters, progress=report_progress,
                )

            partial = result.get("status") == "partial" or result.get("complete") is False
            partial_error = None
            if partial:
                partial_error = {
                    "code": "partial_result",
                    "message": "部分记录或月份未完成，请查看 result 后显式重试。",
                }
            finish_task(
                self.task_db,
                task["task_id"],
                self.worker_id,
                status="partial" if partial else "succeeded",
                result=result,
                error=partial_error,
            )
            for item in result.get("imports", []):
                logger.info("导入结果 task_id=%s kind=%s 新增=%s 重复=%s 无效=%s",
                            task["task_id"], item["kind"], item["inserted_records"],
                            item["duplicate_records"], item["invalid_records"])
            logger.log(logging.WARNING if partial else logging.INFO,
                       "任务结束 task_id=%s kind=%s status=%s 耗时=%.2fs stopped_reason=%s",
                       task["task_id"], task["kind"], "partial" if partial else "succeeded",
                       time.monotonic() - started, result.get("stopped_reason") or "-")
        except Exception as exc:
            if isinstance(exc, TaskInterrupted):
                error = {"code": "worker_interrupted", "message": "worker 正在停止；已提交的业务结果保留，请核查后显式重试。"}
            elif isinstance(exc, (ConfigurationError, ReviewDataError)):
                error_code = "configuration_error" if isinstance(exc, ConfigurationError) else "business_error"
                error = {"code": error_code, "message": str(exc)}
            elif isinstance(exc, DATABASE_ERRORS):
                error = {"code": "database_error", "message": "数据库忙、结构不匹配或写入失败，请检查后重试。"}
            elif isinstance(exc, OSError):
                error = {"code": "file_error", "message": "文件读写失败，请检查导入文件、磁盘和目录权限。"}
            elif isinstance(exc, ValidationError):
                error = {"code": "validation_error", "message": "已保存的任务参数不符合当前版本，请重新提交任务。"}
            else:
                error = {"code": "execution_error", "message": "任务执行失败，请检查文件、数据库及服务配置后重试。"}
            finish_task(self.task_db, task["task_id"], self.worker_id, status="failed", error=error)
            logger.warning("任务失败 task_id=%s kind=%s error_code=%s exception=%s 耗时=%.2fs",
                           task["task_id"], task["kind"], error["code"], type(exc).__name__, time.monotonic() - started)
