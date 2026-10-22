"""Durable single-worker queue in explicitly initialized SQLite/PostgreSQL tables."""

import json
import uuid
from contextlib import closing
from pathlib import Path
from typing import get_args

from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import (PostgresConnection, check_postgres_schema, connect_database, database_identity,
                       database_target, init_postgres, is_postgres)

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.jobs.parameters import TaskKind, TaskStatus
from data_analysis_agent.reviews.store import connect_review_database as business_connection

APPLICATION_ID = 0x52544B31
STATUSES = get_args(TaskStatus)
KINDS = get_args(TaskKind)
SCHEMA = """
CREATE TABLE task_context (business_path TEXT NOT NULL);
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('import','label','analysis','replay')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','partial','failed')),
    parameters_json TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    parent_task_id TEXT REFERENCES tasks(task_id) DEFERRABLE INITIALLY DEFERRED,
    worker_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT
);
CREATE INDEX tasks_queue ON tasks(status,created_at,task_id);
CREATE UNIQUE INDEX tasks_one_active_retry ON tasks(parent_task_id)
    WHERE parent_task_id IS NOT NULL AND status IN ('queued','running');
CREATE TABLE worker_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    worker_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT
);
"""


class TaskNotFound(ReviewDataError):
    pass


class TaskConflict(ReviewDataError):
    pass


def check_task_schema(connection):
    """Validate the queue schema on an existing connection without changing it."""
    if isinstance(connection, PostgresConnection):
        check_postgres_schema(connection, "tasks")
        return
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise ReviewDataError("任务库不是受支持的任务数据库，请执行 review_api_cli init。")


def connect_task_database(path, *, readonly=False):
    """Open a validated task database; the caller owns transactions and close()."""
    if not is_postgres(path) and not Path(path).is_file():
        raise ReviewDataError("任务库不存在，请先执行 review_api_cli init。")
    connection = connect_database(path, readonly=readonly, namespace="tasks", timeout=10)
    try:
        check_task_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def check_task_database(path, business_path):
    with closing(connect_task_database(path, readonly=True)) as connection:
        rows = connection.execute("SELECT business_path FROM task_context").fetchall()
        if len(rows) != 1 or rows[0][0] != database_identity(business_path):
            raise ReviewDataError("任务库绑定的业务数据库不匹配，请使用原业务库或新任务库。")


def init_tasks(path, business_path):
    path, business_path = database_target(path), database_target(business_path)
    with closing(business_connection(business_path, readonly=True)):
        pass
    if is_postgres(path):
        created = init_postgres(path, SCHEMA, namespace="tasks", context=database_identity(business_path))
        check_task_database(path, business_path)
        return {"created": created, "task_database": database_identity(path), "task_schema_version": 1}
    if path in {business_path, *(Path(str(business_path) + s) for s in ("-wal", "-shm", "-journal"))}:
        raise ReviewDataError("任务库必须与业务库及其辅助文件使用不同路径。")
    if not path.parent.is_dir():
        raise ReviewDataError("任务库父目录不存在，请先创建目录。")
    if path.exists():
        check_task_database(path, business_path)
        return {"created": False, "task_database": str(path), "task_schema_version": 1}
    with path.open("xb"):
        pass
    with closing(connect_database(path, namespace="tasks")) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            connection.execute("INSERT INTO task_context VALUES (?)", (database_identity(business_path),))
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"created": True, "task_database": str(path), "task_schema_version": 1}


def _task(row, *, include_result=True):
    result = {key: row[key] for key in (
        "task_id", "kind", "status", "parent_task_id", "created_at", "started_at", "finished_at", "updated_at")}
    result["progress"] = json.loads(row["progress_json"])
    result["error"] = json.loads(row["error_json"]) if row["error_json"] else None
    if include_result:
        result["parameters"] = json.loads(row["parameters_json"])
        result["result"] = json.loads(row["result_json"]) if row["result_json"] else None
    return result


def _read(connection, task_id):
    row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFound("任务不存在。")
    return row


def get_task(path, task_id):
    with closing(connect_task_database(path, readonly=True)) as connection:
        return _task(_read(connection, task_id))


def list_tasks(path, *, status=None, kind=None, page=1, page_size=20):
    validate_integer(page, "page", maximum=1000000)
    validate_integer(page_size, "page_size", maximum=100)
    conditions, values = ["TRUE"], []
    for name, value, choices in (("status", status, STATUSES), ("kind", kind, KINDS)):
        if value is not None:
            if value not in choices:
                raise ReviewDataError(f"无效的任务 {name}。")
            conditions.append(f"{name}=?")
            values.append(value)
    where = " AND ".join(conditions)
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM tasks WHERE " + where, values).fetchone()[0]
        rows = connection.execute("SELECT * FROM tasks WHERE " + where
                                  + " ORDER BY created_at DESC,task_id DESC LIMIT ? OFFSET ?",
                                  (*values, page_size, (page - 1) * page_size))
        return {"total": total, "page": page, "page_size": page_size,
                "items": [_task(row, include_result=False) for row in rows]}


def enqueue(path, kind, parameters, *, idempotency_key=None, parent_task_id=None):
    if kind not in KINDS:
        raise ReviewDataError("不支持的任务类型。")
    if idempotency_key is not None and (not isinstance(idempotency_key, str)
                                       or not 1 <= len(idempotency_key) <= 200
                                       or not idempotency_key.isascii() or not idempotency_key.strip()):
        raise ReviewDataError("Idempotency-Key 必须是 1—200 字符的非空 ASCII 文本。")
    encoded = canonical_json(parameters)
    digest = content_sha256([kind, parameters, parent_task_id])
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if idempotency_key is not None:
            previous = connection.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise TaskConflict("同一个 Idempotency-Key 已用于不同任务参数。")
                return {"task": _task(previous), "reused": True}
        if parent_task_id:
            parent = _read(connection, parent_task_id)
            if parent["status"] not in ("failed", "partial"):
                raise TaskConflict("只能重试失败或部分完成的任务。")
            if parent["kind"] != kind or parent["parameters_json"] != encoded:
                raise TaskConflict("重试必须使用原任务参数。")
            active = connection.execute("SELECT * FROM tasks WHERE parent_task_id=? AND status IN ('queued','running')",
                                        (parent_task_id,)).fetchone()
            if active:
                return {"task": _task(active), "reused": True}
        task_id, now = uuid.uuid4().hex, utc_now()
        connection.execute("""INSERT INTO tasks(task_id,kind,status,parameters_json,request_hash,idempotency_key,
            parent_task_id,created_at,updated_at,progress_json) VALUES (?,?,'queued',?,?,?,?,?,?,?)""",
            (task_id, kind, encoded, digest, idempotency_key, parent_task_id, now, now,
             canonical_json({"stage": "queued", "completed": 0, "total": None, "unit": "items"})))
        return {"task": _task(_read(connection, task_id)), "reused": False}


def worker_started(path, worker_id):
    """Call only while holding the OS worker lock: no old worker can still execute."""
    now = utc_now()
    error = canonical_json({"code": "worker_interrupted", "message": "执行进程已中断；已完成的业务写入保留，请核查后显式重试。"})
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE tasks SET status='failed',finished_at=?,updated_at=?,error_json=? WHERE status='running'",
                           (now, now, error))
        connection.execute("INSERT INTO worker_state VALUES (1,?,?,?,NULL) ON CONFLICT(singleton) DO UPDATE SET "
                           "worker_id=excluded.worker_id,started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,stopped_at=NULL",
                           (worker_id, now, now))


def heartbeat(path, worker_id, *, stopped=False):
    now = utc_now()
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("UPDATE worker_state SET heartbeat_at=?,stopped_at=? WHERE singleton=1 AND worker_id=?",
                           (now, now if stopped else None, worker_id))


def claim_task(path, worker_id):
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        order = "created_at,task_id" if isinstance(connection, PostgresConnection) else "rowid"
        row = connection.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY " + order + " LIMIT 1").fetchone()
        if row is None:
            return None
        now = utc_now()
        connection.execute("UPDATE tasks SET status='running',worker_id=?,started_at=?,updated_at=? WHERE task_id=?",
                           (worker_id, now, now, row["task_id"]))
        return _task(_read(connection, row["task_id"]))


def update_progress(path, task_id, worker_id, progress):
    with closing(connect_task_database(path)) as connection, connection:
        changed = connection.execute("UPDATE tasks SET progress_json=?,updated_at=? "
                                     "WHERE task_id=? AND status='running' AND worker_id=?",
                                     (canonical_json(progress), utc_now(), task_id, worker_id)).rowcount
        if not changed:
            raise TaskConflict("任务执行权已失效。")


def finish_task(path, task_id, worker_id, *, status, result=None, error=None):
    if status not in ("succeeded", "partial", "failed"):
        raise ValueError("Invalid terminal task status")
    now = utc_now()
    with closing(connect_task_database(path)) as connection, connection:
        changed = connection.execute("UPDATE tasks SET status=?,result_json=?,error_json=?,finished_at=?,updated_at=? "
                                     "WHERE task_id=? AND status='running' AND worker_id=?",
                                     (status, canonical_json(result) if result is not None else None,
                                      canonical_json(error) if error else None, now, now, task_id, worker_id)).rowcount
        if not changed:
            raise TaskConflict("任务执行权已失效，结果没有覆盖原记录。")


def queue_health(path):
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        counts = {status: 0 for status in STATUSES}
        counts.update(dict(connection.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status")))
        row = connection.execute("SELECT * FROM worker_state WHERE singleton=1").fetchone()
        return {"counts": counts, "worker": dict(row) if row else None}
