"""HTTP endpoints for monitoring; business operations stay in their services."""

from typing import Annotated
from fastapi import APIRouter, Path, Query
from data_analysis_agent.api.schemas import AlertAction, AlertQuery, Page
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitor_store import get_alert, get_analysis_run, list_alerts, list_analysis_runs, set_alert_status
from data_analysis_agent.jobs.store import TaskNotFound
from data_analysis_agent.api.responses import Result, ok_result


def create_router(db):
    """Build routes bound to this application, without global database state."""
    router = APIRouter()

    @router.get("/analysis-runs", response_model=Result, tags=["分析运行与告警"])
    def runs(query: Annotated[Page, Query()]):
        return ok_result(list_analysis_runs(db, **query.model_dump()))

    @router.get("/analysis-runs/{run_id}", response_model=Result, tags=["分析运行与告警"])
    def run_detail(run_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        try:
            return ok_result(get_analysis_run(db, run_id))
        except ReviewDataError as exc:
            if str(exc) == "分析运行记录不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    @router.get("/alerts", response_model=Result, tags=["分析运行与告警"])
    def alerts(query: Annotated[AlertQuery, Query()]):
        return ok_result(list_alerts(db, **query.model_dump()))

    @router.get("/alerts/{alert_id}", response_model=Result, tags=["分析运行与告警"])
    def alert(alert_id: Annotated[int, Path(ge=1)]):
        try:
            return ok_result(get_alert(db, alert_id))
        except ReviewDataError as exc:
            if str(exc) == "告警不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    @router.post("/alerts/{alert_id}/actions", response_model=Result, tags=["分析运行与告警"])
    def alert_action(alert_id: Annotated[int, Path(ge=1)], body: AlertAction):
        try:
            return ok_result(set_alert_status(db, alert_id, **body.model_dump()))
        except ReviewDataError as exc:
            if str(exc) == "告警不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    return router
