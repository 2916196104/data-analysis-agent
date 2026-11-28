"""在显式初始化的 SQLite/PostgreSQL 数据表中实现持久化单 worker 队列。"""

import json
from datetime import datetime
from threading import Lock
from time import monotonic
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
from data_analysis_agent.jobs.events import PG_TASK_EVENT_CHANNEL, notify_queue_update, notify_task_update
from data_analysis_agent.jobs.parameters import TaskKind, TaskStatus
from data_analysis_agent.jobs.result_storage import (
    AUDIT_SPLIT_THRESHOLD_BYTES,
    audit_ref_from_result,
    rehydrate_task_result,
    split_task_result,
)
from data_analysis_agent.reviews.store import connect_review_database as business_connection

APPLICATION_ID = 0x52544B31
STATUSES = get_args(TaskStatus)
KINDS = get_args(TaskKind)
SUMMARY_COLUMNS = (
    "task_id, kind, status, parent_task_id, created_at, started_at, "
    "finished_at, updated_at, progress_json, error_json"
)
AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS task_audit_blobs (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE CASCADE,
    storage_version TEXT NOT NULL,
    audit_sha256 TEXT NOT NULL,
    audit_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
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
""" + AUDIT_TABLE_DDL


class TaskNotFound(ReviewDataError):
    pass


class TaskConflict(ReviewDataError):
    pass


def check_task_schema(connection):
    """在不修改连接的情况下校验已有队列数据表结构。"""
    if isinstance(connection, PostgresConnection):
        check_postgres_schema(connection, "tasks")
        return
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != 1):
        raise ReviewDataError("任务库不是受支持的任务数据库，请执行 review_api_cli init。")


def connect_task_database(path, *, readonly=False):
    """打开已校验的任务数据库；事务和 close() 由调用方负责。"""
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


_AUDIT_TABLE_CACHE_LOCK = Lock()
_AUDIT_TABLE_CACHE: dict[str, float] = {}
_AUDIT_TABLE_CACHE_TTL_SECONDS = 60.0


def _has_audit_table(connection, *, use_cache=True):
    if isinstance(connection, PostgresConnection):
        identity = getattr(connection, "identity", None)
        if use_cache and identity:
            with _AUDIT_TABLE_CACHE_LOCK:
                verified_at = _AUDIT_TABLE_CACHE.get(identity)
            if verified_at is not None and monotonic() - verified_at < _AUDIT_TABLE_CACHE_TTL_SECONDS:
                return True
        row = connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='task_audit_blobs'"
        ).fetchone()
        exists = row is not None
        if identity:
            with _AUDIT_TABLE_CACHE_LOCK:
                if exists:
                    _AUDIT_TABLE_CACHE[identity] = monotonic()
                else:
                    _AUDIT_TABLE_CACHE.pop(identity, None)
        return exists
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_audit_blobs'"
    ).fetchone()
    return row is not None


def _ensure_audit_table(connection):
    connection.execute(AUDIT_TABLE_DDL)
    if isinstance(connection, PostgresConnection):
        identity = getattr(connection, "identity", None)
        if identity:
            with _AUDIT_TABLE_CACHE_LOCK:
                _AUDIT_TABLE_CACHE[identity] = monotonic()


def _read_audit_row(connection, task_id):
    if not _has_audit_table(connection):
        return None
    return connection.execute(
        "SELECT storage_version, audit_sha256, audit_json, created_at FROM task_audit_blobs WHERE task_id=?",
        (task_id,),
    ).fetchone()


def _task(row, *, include_result=True, connection=None, include_audit=True):
    result = {key: row[key] for key in (
        "task_id", "kind", "status", "parent_task_id", "created_at", "started_at", "finished_at", "updated_at")}
    result["progress"] = json.loads(row["progress_json"])
    result["error"] = json.loads(row["error_json"]) if row["error_json"] else None
    if include_result:
        result["parameters"] = json.loads(row["parameters_json"])
        parsed_result = json.loads(row["result_json"]) if row["result_json"] else None
        if include_audit and connection is not None and audit_ref_from_result(parsed_result) is not None:
            audit_row = _read_audit_row(connection, row["task_id"])
            if audit_row is not None:
                try:
                    parsed_audit = json.loads(audit_row["audit_json"])
                except (TypeError, ValueError):
                    parsed_audit = None
                parsed_result = rehydrate_task_result(parsed_result, parsed_audit)
        result["result"] = parsed_result
    return result


def _read(connection, task_id):
    row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFound("任务不存在。")
    return row


def get_task(path, task_id, *, include_audit=True):
    with closing(connect_task_database(path, readonly=True)) as connection:
        return _task(_read(connection, task_id), connection=connection, include_audit=include_audit)


def get_task_summary(path, task_id):
    """仅读取任务状态、时间戳与进度元数据，不读取 result_json 或 parameters_json。"""
    with closing(connect_task_database(path, readonly=True)) as connection:
        row = connection.execute(f"SELECT {SUMMARY_COLUMNS} FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound("任务不存在。")
        return _task(row, include_result=False)


def get_task_audit(path, task_id):
    """按需读取分离存储的重型审计证据载荷。"""
    with closing(connect_task_database(path, readonly=True)) as connection:
        row = _read(connection, task_id)
        light = json.loads(row["result_json"]) if row["result_json"] else None
        ref = audit_ref_from_result(light)
        audit_row = _read_audit_row(connection, task_id) if ref is not None else None
        if ref is None or audit_row is None:
            return {
                "task_id": task_id,
                "stored_separately": False,
                "storage_version": None,
                "audit_sha256": None,
                "audit_bytes": 0,
                "fields": [],
                "audit": None,
            }
        return {
            "task_id": task_id,
            "stored_separately": True,
            "storage_version": audit_row["storage_version"],
            "audit_sha256": audit_row["audit_sha256"],
            "audit_bytes": ref.get("audit_bytes", len(audit_row["audit_json"].encode("utf-8"))),
            "fields": list(ref.get("fields", [])),
            "audit": json.loads(audit_row["audit_json"]),
        }


def delete_task(path, task_id):
    """删除任务及其重试子链；活动任务必须先结束。"""
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _read(connection, task_id)
        ids = [task_id]
        index = 0
        while index < len(ids):
            children = connection.execute(
                "SELECT task_id FROM tasks WHERE parent_task_id=? ORDER BY created_at DESC, task_id DESC",
                (ids[index],),
            ).fetchall()
            ids.extend(row[0] for row in children)
            index += 1
        active = connection.execute(
            f"SELECT task_id FROM tasks WHERE task_id IN ({','.join('?' for _ in ids)}) "
            "AND status IN ('queued','running') LIMIT 1", ids,
        ).fetchone()
        if active:
            raise TaskConflict("运行中的任务不能删除，请等待任务结束后重试。")
        has_audit = _has_audit_table(connection, use_cache=False)
        for item in reversed(ids):
            if has_audit:
                connection.execute("DELETE FROM task_audit_blobs WHERE task_id=?", (item,))
            connection.execute("DELETE FROM tasks WHERE task_id=?", (item,))
        return {"deleted_task_ids": ids, "deleted_tasks": len(ids)}


def _pg_notify_task(connection, task_id):
    """在 PostgreSQL 事务内发送事务型 NOTIFY，提交后立即唤醒跨进程 API 的 SSE 监听。"""
    if isinstance(connection, PostgresConnection) and task_id:
        connection.execute("SELECT pg_notify(?, ?)", (PG_TASK_EVENT_CHANNEL, str(task_id)))


def cancel_task(path, task_id):
    """手动取消排队或运行中的任务；复用 failed 终态 + task_cancelled 错误码以保持库表兼容并支持重试。"""
    error = {"code": "task_cancelled", "message": "任务已手动取消。"}
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _read(connection, task_id)
        if row["status"] in ("succeeded", "partial", "failed"):
            existing_error = json.loads(row["error_json"]) if row["error_json"] else None
            if (
                row["status"] == "failed"
                and isinstance(existing_error, dict)
                and existing_error.get("code") == "task_cancelled"
            ):
                return {"task": _task(row, include_result=False), "cancelled": True, "already_terminal": True}
            raise TaskConflict("已结束的任务不能取消。")
        now = utc_now()
        changed = connection.execute(
            "UPDATE tasks SET status='failed',finished_at=?,updated_at=?,error_json=? "
            "WHERE task_id=? AND status IN ('queued','running')",
            (now, now, canonical_json(error), task_id),
        ).rowcount
        if not changed:
            raise TaskConflict("任务状态已变化，无法取消。")
        cancelled = _task(_read(connection, task_id), include_result=False)
        _pg_notify_task(connection, task_id)
    notify_task_update(task_id)
    notify_queue_update()
    return {"task": cancelled, "cancelled": True, "already_terminal": False}


def is_task_cancelled(path, task_id) -> bool:
    """检查任务是否已被手动取消。"""
    try:
        summary = get_task_summary(path, task_id)
    except TaskNotFound:
        return False
    err = summary.get("error")
    return summary["status"] == "failed" and isinstance(err, dict) and err.get("code") == "task_cancelled"


def mark_import_data_deleted(path, task_id, deletion):
    """在业务数据删除成功后给任务结果留下可恢复的删除标记。"""
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _read(connection, task_id)
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        result["import_data_deleted"] = True
        result["import_data_deletion"] = deletion
        connection.execute("UPDATE tasks SET result_json=?,updated_at=? WHERE task_id=?",
                           (canonical_json(result), utc_now(), task_id))
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
        rows = connection.execute(f"SELECT {SUMMARY_COLUMNS} FROM tasks WHERE " + where
                                  + " ORDER BY created_at DESC,task_id DESC LIMIT ? OFFSET ?",
                                  (*values, page_size, (page - 1) * page_size))
        return {"total": total, "page": page, "page_size": page_size,
                "items": [_task(row, include_result=False) for row in rows]}


def list_conversations(path, *, limit=30):
    """按 conversation_id 聚合服务端已持久化的分析会话，只读轻量列，不加载重型审计载荷。"""
    validate_integer(limit, "limit", maximum=100)
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT task_id, status, created_at, updated_at, finished_at, parameters_json "
            "FROM tasks WHERE kind='analysis' ORDER BY created_at ASC, task_id ASC"
        ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        try:
            params = json.loads(row["parameters_json"]) if row["parameters_json"] else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(params, dict):
            continue
        conversation_id = params.get("conversation_id")
        question = params.get("question")
        if not isinstance(conversation_id, str) or not conversation_id.strip() or not isinstance(question, str) or not question.strip():
            continue
        cid = conversation_id.strip()
        entry = grouped.get(cid)
        product_ids = list(params.get("product_ids")) if isinstance(params.get("product_ids"), list) else []
        if entry is None:
            grouped[cid] = {
                "conversation_id": cid,
                "title": question.strip()[:60],
                "turn_count": 1,
                "last_task_id": row["task_id"],
                "last_status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "source": params.get("source"),
                "dataset_version": params.get("dataset_version"),
                "product_ids": product_ids,
            }
        else:
            entry["turn_count"] += 1
            entry["last_task_id"] = row["task_id"]
            entry["last_status"] = row["status"]
            if row["updated_at"] >= entry["updated_at"]:
                entry["updated_at"] = row["updated_at"]
            entry["source"] = params.get("source")
            entry["dataset_version"] = params.get("dataset_version")
            entry["product_ids"] = product_ids
    ordered = sorted(grouped.values(), key=lambda item: (item["updated_at"], item["conversation_id"]), reverse=True)
    return {"total": len(ordered), "items": ordered[:limit]}


def get_conversation_tasks(path, conversation_id, *, limit=50):
    """按时间正序读取指定会话的轻量分析任务列表（include_audit=False），供跨端恢复多轮会话。"""
    if not isinstance(conversation_id, str) or not 1 <= len(conversation_id.strip()) <= 200:
        raise ReviewDataError("无效的会话 ID。")
    validate_integer(limit, "limit", maximum=200)
    cid = conversation_id.strip()
    escaped = cid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT * FROM tasks WHERE kind='analysis' AND parameters_json LIKE ? ESCAPE '\\' "
            "ORDER BY created_at ASC, task_id ASC",
            (f"%{escaped}%",),
        ).fetchall()
    matched = []
    for row in rows:
        task = _task(row, include_result=True, include_audit=False)
        params = task.get("parameters") or {}
        if params.get("conversation_id") == cid and isinstance(params.get("question"), str) and params["question"].strip():
            matched.append(task)
    if not matched:
        raise TaskNotFound("会话不存在或暂无已保存的分析任务。")
    return {"conversation_id": cid, "total": len(matched), "items": matched[-limit:]}


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
        created_task = _task(_read(connection, task_id))
    notify_queue_update()
    return {"task": created_task, "reused": False}


def worker_started(path, worker_id):
    """仅在持有操作系统 worker 锁时调用：此时旧 worker 不可能仍在执行。"""
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


def claim_task(path, worker_id, *, kinds=None, exclude_kinds=None):
    if kinds is not None and exclude_kinds is not None:
        raise ValueError("kinds 与 exclude_kinds 不能同时指定。")
    filter_sql = ""
    filter_values = ()
    if kinds is not None:
        selected = tuple(dict.fromkeys(kinds))
        if not selected or any(item not in KINDS for item in selected):
            raise ReviewDataError("无效的任务类型筛选。")
        filter_sql = " AND kind IN (" + ",".join("?" for _ in selected) + ")"
        filter_values = selected
    elif exclude_kinds is not None:
        excluded = tuple(dict.fromkeys(exclude_kinds))
        if not excluded or any(item not in KINDS for item in excluded):
            raise ReviewDataError("无效的任务类型排除。")
        filter_sql = " AND kind NOT IN (" + ",".join("?" for _ in excluded) + ")"
        filter_values = excluded
    with closing(connect_task_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        order = "created_at,task_id" if isinstance(connection, PostgresConnection) else "rowid"
        row = connection.execute(
            "SELECT * FROM tasks WHERE status='queued'" + filter_sql + " ORDER BY " + order + " LIMIT 1",
            filter_values,
        ).fetchone()
        if row is None:
            return None
        now = utc_now()
        connection.execute("UPDATE tasks SET status='running',worker_id=?,started_at=?,updated_at=? WHERE task_id=?",
                           (worker_id, now, now, row["task_id"]))
        claimed = _task(_read(connection, row["task_id"]), connection=connection)
        _pg_notify_task(connection, claimed["task_id"])
    notify_task_update(claimed["task_id"])
    return claimed


def update_progress(path, task_id, worker_id, progress):
    with closing(connect_task_database(path)) as connection, connection:
        changed = connection.execute("UPDATE tasks SET progress_json=?,updated_at=? "
                                     "WHERE task_id=? AND status='running' AND worker_id=?",
                                     (canonical_json(progress), utc_now(), task_id, worker_id)).rowcount
        if not changed:
            raise TaskConflict("任务执行权已失效。")
        _pg_notify_task(connection, task_id)
    notify_task_update(task_id)


def analysis_result_integrity(task_id, status, parameters, result):
    """服务端完成时的摘要；同时绑定任务、终态、会话/范围参数及原始结果。"""
    version = 'analysis-task-integrity-v1'
    return {'version': version, 'sha256': content_sha256([version, task_id, status, parameters, result])}


def finish_task(path, task_id, worker_id, *, status, result=None, error=None,
                audit_split_threshold=AUDIT_SPLIT_THRESHOLD_BYTES):
    if status not in ("succeeded", "partial", "failed"):
        raise ValueError("Invalid terminal task status")
    now = utc_now()
    with closing(connect_task_database(path)) as connection, connection:
        try:
            row = _read(connection, task_id)
        except TaskNotFound:
            raise TaskConflict("任务执行权已失效，结果没有覆盖原记录。") from None
        progress = json.loads(row['progress_json'])
        if row['kind'] == 'analysis' and isinstance(result, dict):
            progress['analysis_integrity'] = analysis_result_integrity(task_id, status, json.loads(row['parameters_json']), result)
        stored_result, audit_payload, audit_sha256 = split_task_result(result, threshold_bytes=audit_split_threshold)
        if audit_payload is not None and audit_sha256 is not None:
            _ensure_audit_table(connection)
        changed = connection.execute("UPDATE tasks SET status=?,result_json=?,error_json=?,finished_at=?,updated_at=?,progress_json=? "
                                     "WHERE task_id=? AND status='running' AND worker_id=?",
                                     (status, canonical_json(stored_result) if stored_result is not None else None,
                                      canonical_json(error) if error else None, now, now, canonical_json(progress), task_id, worker_id)).rowcount
        if not changed:
            raise TaskConflict("任务执行权已失效，结果没有覆盖原记录。")
        if audit_payload is not None and audit_sha256 is not None:
            connection.execute(
                "INSERT INTO task_audit_blobs(task_id, storage_version, audit_sha256, audit_json, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                "storage_version=excluded.storage_version, audit_sha256=excluded.audit_sha256, "
                "audit_json=excluded.audit_json, created_at=excluded.created_at",
                (task_id, audit_payload['version'], audit_sha256, canonical_json(audit_payload), now),
            )
        _pg_notify_task(connection, task_id)
    notify_task_update(task_id)


def queue_health(path):
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        counts = {status: 0 for status in STATUSES}
        counts.update(dict(connection.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status")))
        row = connection.execute("SELECT * FROM worker_state WHERE singleton=1").fetchone()
        return {"counts": counts, "worker": dict(row) if row else None}


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio + 0.999999)))
    return round(ordered[index], 1)


def _duration_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "p50": None, "p95": None}
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 1),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def task_observability_summary(path, *, recent_limit: int = 200) -> dict:
    """只读聚合任务队列状态、类型分布与近期分析任务耗时/失败率（零 DDL、不读重型 result_json）。"""
    validate_integer(recent_limit, "recent_limit", maximum=1000)
    with closing(connect_task_database(path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        by_status = {status: 0 for status in STATUSES}
        by_status.update(dict(connection.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")))
        by_kind = {kind: 0 for kind in KINDS}
        by_kind.update(dict(connection.execute("SELECT kind, COUNT(*) FROM tasks GROUP BY kind")))
        recent_rows = connection.execute(
            "SELECT kind, status, created_at, started_at, finished_at, error_json "
            "FROM tasks WHERE status IN ('succeeded', 'partial', 'failed') "
            "ORDER BY finished_at DESC, task_id DESC LIMIT ?",
            (recent_limit,),
        ).fetchall()

    terminal_count = len(recent_rows)
    failed_count = 0
    cancelled_count = 0
    analysis_durations_ms: list[float] = []
    analysis_queue_wait_ms: list[float] = []

    for row in recent_rows:
        status = row["status"]
        if status == "failed":
            failed_count += 1
            try:
                err = json.loads(row["error_json"]) if row["error_json"] else None
            except (TypeError, ValueError):
                err = None
            if isinstance(err, dict) and err.get("code") == "task_cancelled":
                cancelled_count += 1
        if row["kind"] == "analysis":
            try:
                if row["started_at"] and row["finished_at"]:
                    elapsed = (
                        datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])
                    ).total_seconds() * 1000
                    if elapsed >= 0:
                        analysis_durations_ms.append(elapsed)
                if row["created_at"] and row["started_at"]:
                    waited = (
                        datetime.fromisoformat(row["started_at"]) - datetime.fromisoformat(row["created_at"])
                    ).total_seconds() * 1000
                    if waited >= 0:
                        analysis_queue_wait_ms.append(waited)
            except (TypeError, ValueError):
                pass

    non_cancelled_failed = max(0, failed_count - cancelled_count)
    failure_rate = round(non_cancelled_failed / terminal_count, 4) if terminal_count else None
    return {
        "counts_by_status": by_status,
        "counts_by_kind": by_kind,
        "recent_window_size": recent_limit,
        "recent_terminal_count": terminal_count,
        "recent_failed_count": failed_count,
        "recent_cancelled_count": cancelled_count,
        "recent_failure_rate": failure_rate,
        "analysis_duration_ms": _duration_stats(analysis_durations_ms),
        "analysis_queue_wait_ms": _duration_stats(analysis_queue_wait_ms),
    }
