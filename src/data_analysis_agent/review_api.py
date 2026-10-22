"""Versioned local HTTP API; routes delegate to the existing review services."""

import logging
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Body, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from .config import ConfigurationError
from .database import DatabaseError, database_target
from .review_api_schemas import (
    AlertAction, AlertQuery, AnalysisQuery, EvidenceQuery, HumanReview, IssueQuery,
    OpinionQuery, Page, ProductQuery, ReviewQuery, StatusQuery, TaskInput, TaskQuery, TrendQuery,
)
from .review_analytics import compare_products, get_evidence, get_top_issues, get_trend
from .review_catalog import list_products
from .review_label_store import labeling_status, opinion_evidence, query_review_opinions
from .review_loader import ReviewDataError
from .review_monitor_store import _check_monitor, get_alert, get_analysis_run, list_alerts, list_analysis_runs, set_alert_status
from .review_store import _connect, get_quality_summary
from .review_task_store import (
    TaskConflict, TaskNotFound, check_task_database, enqueue, get_task, list_tasks, queue_health,
)
from .review_task_worker import TaskWorker, prepare_parameters, retry_task
from .review_workbench_store import (
    ReviewConflict, ReviewNotFound, check_workbench, list_reviews, review_detail, save_human_review, workbench_options,
)

logger = logging.getLogger(__name__)


class Result(BaseModel):
    status: str = "ok"
    result: dict


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResult(BaseModel):
    status: str = "error"
    error: ErrorBody


def _ok(result):
    return {"status": "ok", "result": result}


def _error(status, code, message, details=None):
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content={"status": "error", "error": body})


def check_business_database(db):
    check_workbench(db)
    with closing(_connect(db, readonly=True)) as connection:
        _check_monitor(connection)


def create_app(db_path, task_db_path, *, import_root="data/private", env_file=".env",
               embedded_worker=True, allowed_origins=()):
    db, task_db = database_target(db_path), database_target(task_db_path)
    import_root, env_file = FilePath(import_root).resolve(), FilePath(env_file).resolve()
    origins = set(allowed_origins)
    worker = TaskWorker(db, task_db, import_root=import_root, env_file=env_file) if embedded_worker else None

    @asynccontextmanager
    async def lifespan(app):
        check_business_database(db)
        check_task_database(task_db, db)
        if not import_root.is_dir():
            raise ReviewDataError("import-root 目录不存在。")
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(title="商品口碑监控 API", version="1.0.0", lifespan=lifespan,
                  description="复用现有评论业务函数。长任务持久化到 SQLite / PostgreSQL，由串行 worker 执行。")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=sorted(origins), allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "Idempotency-Key"], expose_headers=["Location"])

    @app.middleware("http")
    async def local_request(request: Request, call_next):
        if request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
            return _error(403, "host_not_allowed", "首版 API 只接受本机 Host。")
        origin = request.headers.get("origin")
        own = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin is not None and origin != own and origin not in origins:
            return _error(403, "origin_not_allowed", "请求来源未配置。")
        if origin is None and request.headers.get("sec-fetch-site") == "cross-site":
            return _error(403, "origin_not_allowed", "不接受跨站请求。")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
                   for item in exc.errors()]
        return _error(422, "validation_error", "请求参数不符合接口约定。", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(ReviewDataError)
    async def business_error(request, exc):
        if isinstance(exc, (TaskNotFound, ReviewNotFound)):
            return _error(404, "not_found", str(exc))
        if isinstance(exc, (TaskConflict, ReviewConflict)):
            return _error(409, "conflict", str(exc))
        return _error(400, "business_error", str(exc))

    @app.exception_handler(ConfigurationError)
    async def configuration_error(request, exc):
        return _error(503, "configuration_error", str(exc))

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(DatabaseError)
    async def database_error(request, exc):
        return _error(503, "database_error", "数据库暂不可用，请稍后重试。")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("HTTP request failed (%s)", type(exc).__name__)
        return _error(500, "internal_error", "请求执行失败，请检查服务配置。")

    router = APIRouter(prefix="/api/v1", default_response_class=JSONResponse,
                       responses={code: {"model": ErrorResult} for code in (400, 403, 404, 409, 422, 500, 503)})

    @router.get("/health", response_model=Result, tags=["服务"])
    def health():
        with closing(_connect(db, readonly=True)):
            pass
        queue = queue_health(task_db)
        info = queue["worker"]
        available = bool(info and not info["stopped_at"] and
                         (datetime.now(timezone.utc) - datetime.fromisoformat(info["heartbeat_at"])).total_seconds() < 30)
        return _ok({"business_database": "ok", "queue": queue, "worker_available": available})

    @router.get("/quality", response_model=Result, tags=["商品与评论"])
    def quality():
        return _ok(get_quality_summary(db))

    @router.get("/products", response_model=Result, tags=["商品与评论"])
    def products(query: Annotated[ProductQuery, Query()]):
        return _ok(list_products(db, **query.model_dump()))

    @router.get("/options", response_model=Result, tags=["商品与评论"])
    def options(profile_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        return _ok(workbench_options(db, profile_id))

    @router.get("/reviews", response_model=Result, tags=["商品与评论"])
    def reviews(query: Annotated[ReviewQuery, Query()]):
        return _ok(list_reviews(db, **query.model_dump()))

    @router.get("/reviews/{review_id}", response_model=Result, tags=["商品与评论"])
    def review(review_id: Annotated[int, Path(ge=1, le=2147483647)],
               profile_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None):
        return _ok(review_detail(db, review_id, profile_id))

    @router.post("/reviews/{review_id}/human-reviews", response_model=Result, status_code=201, tags=["人工复核"])
    def human_review(review_id: Annotated[int, Path(ge=1, le=2147483647)], body: HumanReview):
        return _ok(save_human_review(db, review_id, **body.model_dump()))

    @router.get("/labeling/status", response_model=Result, tags=["标注"])
    def label_status(query: Annotated[StatusQuery, Query()]):
        return _ok(labeling_status(db, **query.model_dump()))

    @router.get("/labeling/opinions", response_model=Result, tags=["标注"])
    def opinions(query: Annotated[OpinionQuery, Query()]):
        return _ok(query_review_opinions(db, **query.model_dump()))

    @router.get("/labeling/evidence/{review_id}", response_model=Result, tags=["标注"])
    def labeling_evidence(review_id: Annotated[int, Path(ge=1, le=2147483647)]):
        try:
            return _ok(opinion_evidence(db, review_id))
        except ReviewDataError as exc:
            if str(exc) == "评论 ID 不存在。":
                raise ReviewNotFound(str(exc)) from exc
            raise

    @router.get("/analytics/trend", response_model=Result, tags=["统计分析"])
    def trend(query: Annotated[TrendQuery, Query()]):
        return _ok(get_trend(db, **query.model_dump()))

    @router.get("/analytics/top-issues", response_model=Result, tags=["统计分析"])
    def issues(query: Annotated[IssueQuery, Query()]):
        return _ok(get_top_issues(db, **query.model_dump()))

    @router.get("/analytics/compare-products", response_model=Result, tags=["统计分析"])
    def comparison(query: Annotated[AnalysisQuery, Query()]):
        return _ok(compare_products(db, **query.model_dump()))

    @router.get("/analytics/evidence", response_model=Result, tags=["统计分析"])
    def evidence(query: Annotated[EvidenceQuery, Query()]):
        values = query.model_dump()
        if values["sentiment"] == "all":
            values["sentiment"] = None
        return _ok(get_evidence(db, **values))

    @router.get("/analysis-runs", response_model=Result, tags=["分析运行与告警"])
    def runs(query: Annotated[Page, Query()]):
        return _ok(list_analysis_runs(db, **query.model_dump()))

    @router.get("/analysis-runs/{run_id}", response_model=Result, tags=["分析运行与告警"])
    def run_detail(run_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        try:
            return _ok(get_analysis_run(db, run_id))
        except ReviewDataError as exc:
            if str(exc) == "分析运行记录不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    @router.get("/alerts", response_model=Result, tags=["分析运行与告警"])
    def alerts(query: Annotated[AlertQuery, Query()]):
        return _ok(list_alerts(db, **query.model_dump()))

    @router.get("/alerts/{alert_id}", response_model=Result, tags=["分析运行与告警"])
    def alert(alert_id: Annotated[int, Path(ge=1)]):
        try:
            return _ok(get_alert(db, alert_id))
        except ReviewDataError as exc:
            if str(exc) == "告警不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    @router.post("/alerts/{alert_id}/actions", response_model=Result, tags=["分析运行与告警"])
    def alert_action(alert_id: Annotated[int, Path(ge=1)], body: AlertAction):
        try:
            return _ok(set_alert_status(db, alert_id, **body.model_dump()))
        except ReviewDataError as exc:
            if str(exc) == "告警不存在。":
                raise TaskNotFound(str(exc)) from exc
            raise

    @router.post("/tasks", response_model=Result, status_code=202, tags=["后台任务"])
    def submit_task(body: Annotated[TaskInput, Body(discriminator="kind")], response: Response,
                    idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        parameters = prepare_parameters(body.kind, body.parameters.model_dump(), import_root=import_root, env_file=env_file)
        result = enqueue(task_db, body.kind, parameters, idempotency_key=idempotency_key)
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return _ok(result)

    @router.get("/tasks", response_model=Result, tags=["后台任务"])
    def tasks(query: Annotated[TaskQuery, Query()]):
        return _ok(list_tasks(task_db, **query.model_dump()))

    @router.get("/tasks/{task_id}", response_model=Result, tags=["后台任务"])
    def task_detail(task_id: Annotated[str, Path(min_length=1, max_length=1000)]):
        return _ok(get_task(task_db, task_id))

    @router.post("/tasks/{task_id}/retry", response_model=Result, status_code=202, tags=["后台任务"])
    def retry(task_id: Annotated[str, Path(min_length=1, max_length=1000)], response: Response,
              idempotency_key: Annotated[str | None, Header(max_length=200)] = None):
        result = retry_task(task_db, task_id, import_root=import_root, env_file=env_file,
                            idempotency_key=idempotency_key)
        response.headers["Location"] = "/api/v1/tasks/" + result["task"]["task_id"]
        return _ok(result)

    app.include_router(router)
    return app
