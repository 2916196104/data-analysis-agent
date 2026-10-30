"""公开分析读取仅返回白名单数据；与本机所有者的旧任务审计接口分离。"""

from typing import Annotated

from fastapi import APIRouter, Path

from data_analysis_agent.agents.review_compression import read_public_analysis
from data_analysis_agent.api.responses import Result, ok_result
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.store import get_task


def create_router(task_db):
    router = APIRouter()

    @router.get("/public/tasks/{task_id}", response_model=Result, tags=["公开分析"])
    def public_task(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        task = get_task(task_db, task_id)
        result = task.get("result")
        is_agent = isinstance(result, dict) and result.get("agent") is True
        if task["kind"] != "analysis" or not (is_agent or isinstance(task.get("parameters", {}).get("question"), str)):
            raise ReviewDataError("该任务不是评论 Agent 分析。")
        return ok_result({"task_id": task["task_id"], "status": task["status"], "public_analysis": read_public_analysis(task)})

    return router
