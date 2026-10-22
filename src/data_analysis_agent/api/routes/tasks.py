"""HTTP endpoints for tasks; business operations stay in their services."""

from typing import Annotated
from fastapi import APIRouter, Body, Header, Path, Query, Response
from data_analysis_agent.api.schemas import TaskInput, TaskQuery
from data_analysis_agent.jobs.store import enqueue, get_task, list_tasks
from data_analysis_agent.jobs.worker import prepare_parameters, retry_task
from data_analysis_agent.api.responses import Result, ok_result


def create_router(task_db, *, import_root, env_file):
    """Build routes bound to this application, without global database state."""
    router = APIRouter()

    @router.post("/tasks", response_model=Result, status_code=202, tags=["后台任务"])
    def submit_task(body: Annotated[TaskInput, Body(discriminator="kind")], response: Response,
                    idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        parameters = prepare_parameters(body.kind, body.parameters.model_dump(), import_root=import_root, env_file=env_file)
        result = enqueue(task_db, body.kind, parameters, idempotency_key=idempotency_key)
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return ok_result(result)

    @router.get("/tasks", response_model=Result, tags=["后台任务"])
    def tasks(query: Annotated[TaskQuery, Query()]):
        return ok_result(list_tasks(task_db, **query.model_dump()))

    @router.get("/tasks/{task_id}", response_model=Result, tags=["后台任务"])
    def task_detail(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        return ok_result(get_task(task_db, task_id))

    @router.post("/tasks/{task_id}/retry", response_model=Result, status_code=202, tags=["后台任务"])
    def retry(task_id: Annotated[str, Path(min_length=1, max_length=1000)], response: Response,
              idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        result = retry_task(task_db, task_id, import_root=import_root, env_file=env_file,
                            idempotency_key=idempotency_key)
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return ok_result(result)

    return router
