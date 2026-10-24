"""显式 API 初始化、本地服务器、独立 worker 和任务检查 CLI。"""

import argparse
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

if __name__ == "__main__" and not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.config import ConfigurationError, read_settings
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.core.database import DATABASE_ERRORS, configure_sql_logging, database_argument, is_postgres
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import init_database
from data_analysis_agent.jobs.store import KINDS, STATUSES, check_task_database, get_task, init_tasks, list_tasks
from data_analysis_agent.jobs.submission import retry_task
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.human_review import init_workbench


def default_task_database(db):
    if is_postgres(db):
        return db
    db = Path(db).resolve()
    return db.with_name(db.stem + ".tasks.sqlite3")


def main():
    parser = argparse.ArgumentParser(description="商品口碑统一 HTTP API 与 SQLite / PostgreSQL 后台任务")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="显式初始化业务表和任务表，不清空数据")
    serve = commands.add_parser("serve", help="启动本机 HTTP API，默认同时启动串行 worker")
    worker_command = commands.add_parser("worker", help="仅启动独立 worker，配合 serve --no-worker")
    tasks = commands.add_parser("tasks", help="查询任务列表，不执行任务")
    task = commands.add_parser("task", help="查询单个任务进度、结果与错误")
    retry = commands.add_parser("retry", help="显式重试失败或部分完成的任务，创建关联的新任务")
    for command in (setup, serve, worker_command, tasks, task, retry):
        command.add_argument("--db", type=database_argument, required=True)
        command.add_argument("--task-db", type=database_argument, help="SQLite 默认同目录任务库；PostgreSQL 默认与业务表同库")
    for command in (serve, worker_command, retry):
        command.add_argument("--import-root", type=Path, default=Path("data/private"))
        command.add_argument("--env-file", type=Path, default=Path(".env"))
    serve.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-worker", action="store_true", help="只运行 API，由独立 worker 消费任务")
    serve.add_argument("--allow-origin", action="append", default=[], help="可重复，仅允许指定 HTTP(S) Origin")
    tasks.add_argument("--status", choices=STATUSES)
    tasks.add_argument("--kind", choices=KINDS)
    tasks.add_argument("--page", type=int, default=1)
    tasks.add_argument("--page-size", type=int, default=20)
    for command in (task, retry):
        command.add_argument("--task-id", required=True)
    retry.add_argument("--idempotency-key")
    args = parser.parse_args()
    if args.command in ("serve", "worker"):
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
        logging.getLogger("data_analysis_agent").setLevel(logging.INFO)
    task_db = args.task_db or default_task_database(args.db)
    try:
        if args.command in ("serve", "worker"):
            settings = read_settings(args.env_file)
            configure_sql_logging(settings.get("DATABASE_SQL_LOG", "0").strip().lower() in ("1", "true", "yes", "on"))
        if args.command != "init":
            check_task_database(task_db, args.db)
        if args.command == "init":
            result = {"business": init_database(args.db), "labeling": init_labeling(args.db),
                      "workbench": init_workbench(args.db), "monitoring": init_monitoring(args.db),
                      "tasks": init_tasks(task_db, args.db)}
        elif args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ReviewDataError("port 必须是 1—65535。")
            for origin in args.allow_origin:
                parsed = urlsplit(origin)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                        or parsed.path or parsed.query or parsed.fragment):
                    raise ReviewDataError("allow-origin 必须是无路径和凭据的 HTTP(S) Origin。")
                parsed.port
            try:
                import uvicorn

                from data_analysis_agent.api.app import create_app
            except ModuleNotFoundError as exc:
                raise ReviewDataError("HTTP 依赖未安装，请执行 .venv/Scripts/python.exe -m pip install -e .") from exc
            app = create_app(args.db, task_db, import_root=args.import_root, env_file=args.env_file,
                             embedded_worker=not args.no_worker, allowed_origins=args.allow_origin)
            uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=True)
            return 0
        elif args.command == "worker":
            worker = TaskWorker(args.db, task_db, import_root=args.import_root, env_file=args.env_file)
            worker.start()
            try:
                while worker.thread.is_alive():
                    worker.thread.join(timeout=1)
                return 1
            except KeyboardInterrupt:
                return 0
            finally:
                worker.stop()
        elif args.command == "tasks":
            result = list_tasks(task_db, status=args.status, kind=args.kind, page=args.page, page_size=args.page_size)
        elif args.command == "task":
            result = get_task(task_db, args.task_id)
        else:
            result = retry_task(task_db, args.task_id, import_root=args.import_root, env_file=args.env_file,
                                idempotency_key=args.idempotency_key)
        print(json.dumps({"status": "ok", "result": result}, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ReviewDataError, ConfigurationError, OSError, *DATABASE_ERRORS, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
