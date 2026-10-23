"""将已停止使用的 SQLite 数据库原子复制到空的 PostgreSQL 数据库。"""

import argparse
import hashlib
import json
import psycopg
from contextlib import ExitStack, closing
from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.database import (DATABASE_ERRORS, DatabaseError, connect_database, database_argument,
                       database_identity, is_postgres, table_names)
from data_analysis_agent.reviews.label_store import LABEL_SCHEMA, check_labeling_schema
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.reviews.monitor_store import SCHEMA as MONITOR_SCHEMA, check_monitoring_schema
from data_analysis_agent.reviews.store import SCHEMA, connect_review_database
from data_analysis_agent.jobs.store import SCHEMA as TASK_SCHEMA, connect_task_database as task_connection, check_task_database
from data_analysis_agent.reviews.human_review import SCHEMA as HUMAN_SCHEMA, check_workbench_schema

BUSINESS_TABLES = (
    "import_runs", "products", "reviews", "import_errors", "review_annotations", "review_opinions",
    "review_human_reviews", "review_human_opinions", "review_analysis_runs", "review_alerts",
    "review_alert_occurrences", "review_alert_actions",
)
TASK_TABLES = ("tasks",)
VERSION_TABLES = ("review_labeling_schema", "review_workbench_schema", "review_monitor_schema")


def _digest_row(digest, row):
    values = [value.hex() if isinstance(value, (bytes, memoryview)) else value for value in row]
    digest.update(json.dumps(values, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode())
    digest.update(b"\n")


def _copy_table(source, target, table):
    info = source.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = [row[1] for row in info]
    expected = [row[0] for row in target.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=? "
        "ORDER BY ordinal_position", (table,))]
    if columns != expected:
        raise ReviewDataError(f"源表字段与当前版本不一致：{table}，迁移已取消。")
    primary = sorted((row[5], row[1]) for row in info if row[5])
    order = ",".join(f'"{name}"' for _, name in primary)
    if not order:
        raise ReviewDataError(f"迁移表缺少主键：{table}。")
    selected = ",".join(f'"{name}"' for name in columns)
    query = f'SELECT {selected} FROM "{table}" ORDER BY {order}'
    types = {row[1]: row[2] for row in info}
    pg_order = ",".join(f'"{name}"' + (' COLLATE "C"' if types[name] == "TEXT" else '') for _, name in primary)
    reader = source.execute(query)
    digest, count = hashlib.sha256(), 0
    while rows := reader.fetchmany(500):
        for row in rows:
            if any(isinstance(value, str) and "\x00" in value for value in row):
                raise ReviewDataError(f"{table} 含 PostgreSQL TEXT 不支持的空字符，迁移已取消；请先核查源数据。")
            _digest_row(digest, row)
        target.executemany(f'INSERT INTO "{table}" ({selected}) VALUES (' + ",".join("?" for _ in columns) + ")",
                           [tuple(row) for row in rows])
        count += len(rows)
    copied_digest = hashlib.sha256()
    copied_count = 0
    # 服务器游标可将大型评论归档的校验内存限制在固定范围内。
    try:
        with target.raw.cursor(name="verify_" + table) as cursor:
            cursor.execute(f'SELECT {selected} FROM "{table}" ORDER BY {pg_order}')
            for row in cursor:
                _digest_row(copied_digest, row)
                copied_count += 1
    except psycopg.Error:
        raise DatabaseError(f"迁移读取校验失败：{table}，事务已取消。") from None
    if count != copied_count or digest.hexdigest() != copied_digest.hexdigest():
        raise ReviewDataError(f"迁移校验失败：{table}，目标事务已取消。")
    if "id" in columns:
        maximum = target.execute(f'SELECT MAX(id) FROM "{table}"').fetchone()[0]
        target.execute("SELECT setval(pg_get_serial_sequence(?, 'id'), ?, ?)",
                       (table, max(1, maximum or 0), maximum is not None and maximum >= 1))
    return {"rows": count, "sha256": digest.hexdigest()}


def migrate_sqlite(source_db, target_db, *, source_tasks=None):
    """绝不写入源文件、合并数据、调用模型或执行队列任务。"""
    if is_postgres(source_db) or not is_postgres(target_db):
        raise ReviewDataError("迁移方向必须是 SQLite 文件 → PostgreSQL URL。")
    with ExitStack() as stack:
        source = stack.enter_context(closing(connect_review_database(source_db, readonly=True)))
        source.execute("BEGIN")
        if source.execute("PRAGMA foreign_key_check").fetchone():
            raise ReviewDataError("源评论库外键检查失败，迁移已取消。")
        names = table_names(source)
        unknown = names - set(BUSINESS_TABLES + VERSION_TABLES) - {"sqlite_sequence"}
        if unknown:
            raise ReviewDataError("源评论库含未知表，迁移已取消；请核查是否为专用评论数据库。")
        for tables, check in (({"review_labeling_schema", "review_annotations", "review_opinions"}, check_labeling_schema),
                              ({"review_workbench_schema", "review_human_reviews", "review_human_opinions"}, check_workbench_schema),
                              ({"review_monitor_schema", "review_analysis_runs", "review_alerts",
                                "review_alert_occurrences", "review_alert_actions"}, check_monitoring_schema)):
            if names & tables:
                check(source)
        tasks = None
        if source_tasks is not None:
            check_task_database(source_tasks, source_db)
            tasks = stack.enter_context(closing(task_connection(source_tasks, readonly=True)))
            tasks.execute("BEGIN")
            if tasks.execute("PRAGMA foreign_key_check").fetchone():
                raise ReviewDataError("源任务库外键检查失败，迁移已取消。")
            if tasks.execute("SELECT 1 FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ReviewDataError("源任务库仍有排队或执行中的任务；请先完成任务并停止服务，或不迁移任务历史。")
        target = stack.enter_context(closing(connect_database(target_db)))
        with target:
            target.execute("BEGIN IMMEDIATE")
            occupied = table_names(target) & (set(BUSINESS_TABLES + TASK_TABLES + VERSION_TABLES)
                                              | {"task_context", "worker_state", "review_database_schema"})
            if occupied:
                raise ReviewDataError("目标已有项目表；迁移只接受未初始化的空目标，请使用新的专用数据库。")
            target.executescript(SCHEMA + LABEL_SCHEMA + HUMAN_SCHEMA + MONITOR_SCHEMA + TASK_SCHEMA)
            target.execute("CREATE TABLE review_database_schema (namespace TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            target.execute("INSERT INTO review_database_schema VALUES ('business',1),('tasks',1)")
            target.execute("INSERT INTO task_context VALUES (?)", (database_identity(target_db),))
            copied = {table: _copy_table(source, target, table) for table in BUSINESS_TABLES if table in names}
            if tasks is not None:
                copied.update({table: _copy_table(tasks, target, table) for table in TASK_TABLES})
            check_labeling_schema(target)
            check_workbench_schema(target)
            check_monitoring_schema(target)
        return {"status": "ok", "database": database_identity(target_db), "verified_tables": copied,
                "task_history_migrated": tasks is not None,
                "worker_state_reset": tasks is not None}


def main():
    parser = argparse.ArgumentParser(description="离线迁移 SQLite → 空 PostgreSQL；保留 ID 并逐表校验")
    parser.add_argument("--source", type=Path, required=True, help="已停止写入的原评论 SQLite 文件")
    parser.add_argument("--source-tasks", type=Path, help="可选：原任务 SQLite，必须无 queued/running 任务")
    parser.add_argument("--target", type=database_argument, required=True, help="PostgreSQL URL 或 env:DATABASE_URL")
    args = parser.parse_args()
    try:
        result = migrate_sqlite(args.source, args.target, source_tasks=args.source_tasks)
    except (ReviewDataError, OSError, *DATABASE_ERRORS) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
