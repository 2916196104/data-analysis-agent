"""组装本地 API、生命周期、通用错误处理和请求策略。"""

import logging
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path as FilePath

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware

from data_analysis_agent.api.responses import ErrorBody, ErrorResult, Result, error_response, ok_result
from data_analysis_agent.api.routes import analytics, monitoring, reviews, tasks
from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.core.database import DatabaseError, database_target
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitor_store import check_monitoring_schema
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.jobs.store import TaskConflict, TaskNotFound, check_task_database, queue_health
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.human_review import ReviewConflict, ReviewNotFound, check_workbench_schema

logger = logging.getLogger(__name__)


def check_business_database(db):
    with closing(connect_review_database(db, readonly=True)) as connection:
        check_workbench_schema(connection)
        check_monitoring_schema(connection)


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
            return error_response(403, "host_not_allowed", "首版 API 只接受本机 Host。")
        origin = request.headers.get("origin")
        own = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin is not None and origin != own and origin not in origins:
            return error_response(403, "origin_not_allowed", "请求来源未配置。")
        if origin is None and request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(403, "origin_not_allowed", "不接受跨站请求。")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
                   for item in exc.errors()]
        return error_response(422, "validation_error", "请求参数不符合接口约定。", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(ReviewDataError)
    async def business_error(request, exc):
        if isinstance(exc, (TaskNotFound, ReviewNotFound)):
            return error_response(404, "not_found", str(exc))
        if isinstance(exc, (TaskConflict, ReviewConflict)):
            return error_response(409, "conflict", str(exc))
        return error_response(400, "business_error", str(exc))

    @app.exception_handler(ConfigurationError)
    async def configuration_error(request, exc):
        return error_response(503, "configuration_error", str(exc))

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(DatabaseError)
    async def database_error(request, exc):
        return error_response(503, "database_error", "数据库暂不可用，请稍后重试。")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("HTTP request failed (%s)", type(exc).__name__)
        return error_response(500, "internal_error", "请求执行失败，请检查服务配置。")

    router = APIRouter(prefix="/api/v1", default_response_class=JSONResponse,
                       responses={code: {"model": ErrorResult} for code in (400, 403, 404, 409, 422, 500, 503)})

    @router.get("/health", response_model=Result, tags=["服务"])
    def health():
        with closing(connect_review_database(db, readonly=True)):
            pass
        queue = queue_health(task_db)
        info = queue["worker"]
        available = bool(info and not info["stopped_at"] and
                         (datetime.now(timezone.utc) - datetime.fromisoformat(info["heartbeat_at"])).total_seconds() < 30)
        return ok_result({"business_database": "ok", "queue": queue, "worker_available": available})

    router.include_router(reviews.create_router(db))
    router.include_router(analytics.create_router(db))
    router.include_router(monitoring.create_router(db))
    router.include_router(tasks.create_router(task_db, db, import_root=import_root, env_file=env_file))
    app.include_router(router)
    return app
