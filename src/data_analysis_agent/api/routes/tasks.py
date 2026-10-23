"""任务 HTTP 端点；业务操作仍位于各自服务中。"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Header, Path, Query, Response

from data_analysis_agent.api.responses import Result, ok_result
from data_analysis_agent.api.schemas import TaskInput, TaskQuery
from data_analysis_agent.jobs.store import TaskConflict
from data_analysis_agent.jobs.store import (
    delete_task, enqueue, get_task, list_tasks, mark_import_data_deleted,
)
from data_analysis_agent.jobs.submission import prepare_parameters, retry_task
from data_analysis_agent.reviews.store import delete_import_runs

logger = logging.getLogger(__name__)


def create_router(task_db, business_db, *, import_root, env_file):
    """构建绑定到当前应用的路由，不使用全局数据库状态。"""
    router = APIRouter()

    @router.post("/tasks", response_model=Result, status_code=202, tags=["后台任务"])
    def submit_task(body: Annotated[TaskInput, Body(discriminator="kind")], response: Response,
                    idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        parameters = prepare_parameters(body.kind, body.parameters.model_dump(), import_root=import_root, env_file=env_file)
        result = enqueue(task_db, body.kind, parameters, idempotency_key=idempotency_key)
        logger.info("任务提交 task_id=%s kind=%s reused=%s 商品数=%s limit=%s workers=%s",
                    result["task"]["task_id"], body.kind, result["reused"],
                    len(parameters.get("product_ids", [])) or "全部",
                    parameters.get("limit", parameters.get("max_reviews", "不限")), parameters.get("workers", "-"))
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return ok_result(result)

    @router.get("/tasks", response_model=Result, tags=["后台任务"])
    def tasks(query: Annotated[TaskQuery, Query()]):
        return ok_result(list_tasks(task_db, **query.model_dump()))

    @router.get("/tasks/{task_id}", response_model=Result, tags=["后台任务"])
    def task_detail(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        return ok_result(get_task(task_db, task_id))

    @router.delete("/tasks/{task_id}/imported-data", response_model=Result, tags=["后台任务"])
    def delete_imported_data(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        task = get_task(task_db, task_id)
        if task["kind"] != "import":
            raise TaskConflict("只有导入任务可以删除导入数据。")
        if task["status"] in ("queued", "running"):
            raise TaskConflict("运行中的导入任务不能删除数据，请等待任务结束后重试。")
        if task.get("result", {}).get("import_data_deleted"):
            return ok_result({"task": task, "reused": True})
        imports = task.get("result", {}).get("imports") or []
        ids = [item.get("import_run_id") for item in imports if isinstance(item.get("import_run_id"), int)]
        if not ids:
            raise TaskConflict("该导入任务没有可删除的已保存导入批次。")
        deletion = delete_import_runs(business_db, ids)
        updated = mark_import_data_deleted(task_db, task_id, deletion)
        logger.info("删除导入数据 task_id=%s import_run_ids=%s reviews=%s",
                    task_id, ids, deletion["deleted_reviews"])
        return ok_result({"task": updated, "deletion": deletion, "reused": False})

    @router.delete("/tasks/{task_id}", response_model=Result, tags=["后台任务"])
    def delete_task_record(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        deletion = delete_task(task_db, task_id)
        logger.info("删除任务记录 task_id=%s deleted_tasks=%s", task_id, deletion["deleted_tasks"])
        return ok_result(deletion)

    @router.post("/tasks/{task_id}/retry", response_model=Result, status_code=202, tags=["后台任务"])
    def retry(task_id: Annotated[str, Path(min_length=1, max_length=1000)], response: Response,
              idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        result = retry_task(task_db, task_id, import_root=import_root, env_file=env_file,
                            idempotency_key=idempotency_key)
        logger.info("任务重试 task_id=%s parent_task_id=%s kind=%s reused=%s",
                    result["task"]["task_id"], result["task"]["parent_task_id"],
                    result["task"]["kind"], result["reused"])
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return ok_result(result)

    return router
