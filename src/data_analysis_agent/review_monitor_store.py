"""Explicit monitoring schema, immutable runs and auditable alert decisions."""

import hashlib
import json
from contextlib import closing
from datetime import datetime

from .database import insert_id, table_names

from .review_label_store import _check_labels
from .review_labeler import ASPECTS
from .review_loader import ReviewDataError
from .review_store import _connect, _now

TABLES = (
    "review_monitor_schema", "review_analysis_runs", "review_alerts",
    "review_alert_occurrences", "review_alert_actions",
)
SCHEMA = """
CREATE TABLE review_monitor_schema (version INTEGER PRIMARY KEY);
INSERT INTO review_monitor_schema VALUES (1);
CREATE TABLE review_analysis_runs (
    run_id TEXT PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    request_json TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    results_json TEXT NOT NULL
);
CREATE TABLE review_alerts (
    id INTEGER PRIMARY KEY,
    series_key TEXT NOT NULL,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    product_id TEXT NOT NULL,
    aspect TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','confirmed','ignored')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX review_alert_series ON review_alerts(series_key, first_seen);
CREATE TABLE review_alert_occurrences (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES review_alerts(id),
    run_id TEXT NOT NULL REFERENCES review_analysis_runs(run_id),
    series_key TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    is_new_alert INTEGER NOT NULL CHECK(is_new_alert IN (0,1)),
    details_json TEXT NOT NULL,
    UNIQUE(run_id, series_key)
);
CREATE INDEX review_alert_occurrence ON review_alert_occurrences(alert_id, detected_at);
CREATE TABLE review_alert_actions (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES review_alerts(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL CHECK(to_status IN ('confirmed','ignored')),
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _integer(value, name, minimum=1, maximum=2147483647):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ReviewDataError(f"{name} 必须是 {minimum}—{maximum} 的整数。")
    return value


def _text(value, name, maximum=200, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ReviewDataError(f"{name} 必须是长度不超过 {maximum} 的{'文本' if allow_empty else '非空文本'}。")
    return value.strip()


def _check_monitor(connection):
    _check_labels(connection)
    names = table_names(connection) & set(TABLES)
    if names != set(TABLES):
        raise ReviewDataError("监控表未初始化或不完整，请执行 review_monitor_cli init 或检查数据库结构。")
    versions = connection.execute("SELECT version FROM review_monitor_schema").fetchall()
    if len(versions) != 1 or versions[0][0] != 1:
        raise ReviewDataError("监控表版本不支持。")


def init_monitoring(db_path):
    """Add monitoring tables explicitly, without changing review/label/human tables."""
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_labels(connection)
        if table_names(connection) & set(TABLES):
            _check_monitor(connection)
            return {"created": False, "monitor_schema_version": 1}
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
    return {"created": True, "monitor_schema_version": 1}


def _run_summary(row):
    return {"run_id": row["run_id"], "run_key": row["run_key"], "created_at": row["created_at"],
            "as_of": row["as_of"], "request": json.loads(row["request_json"])}


def _read_run(connection, run_id):
    row = connection.execute("SELECT * FROM review_analysis_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise ReviewDataError("分析运行记录不存在。")
    links = [dict(r) for r in connection.execute(
        "SELECT id occurrence_id, alert_id, detected_at, is_new_alert FROM review_alert_occurrences "
        "WHERE run_id=? ORDER BY id", (run_id,))]
    return {**_run_summary(row), "inputs": json.loads(row["inputs_json"]),
            "results": json.loads(row["results_json"]), "alert_occurrences": links}


def get_analysis_run(db_path, run_id):
    run_id = _text(run_id, "run_id")
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        _check_monitor(connection)
        return _read_run(connection, run_id)


def list_analysis_runs(db_path, *, page=1, page_size=20):
    _integer(page, "page", maximum=1000000)
    _integer(page_size, "page_size", maximum=100)
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        _check_monitor(connection)
        total = connection.execute("SELECT COUNT(*) FROM review_analysis_runs").fetchone()[0]
        rows = connection.execute(
            "SELECT run_id,run_key,created_at,as_of,request_json FROM review_analysis_runs "
            "ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size))
        return {"total": total, "page": page, "page_size": page_size,
                "items": [_run_summary(row) for row in rows]}


def _attach_alert(connection, *, run_id, as_of, request, product, evaluation):
    """Same instant merges; later hits inside the first-hit cooldown share an alert."""
    identity = {key: request["scope"][key] for key in ("model", "base_url", "prompt_version")}
    series = _digest({"engine_version": request["engine_version"], "rules": request["rules"],
                      "identity": identity, "product": product, "aspect": evaluation["aspect"]})
    row = connection.execute(
        "SELECT * FROM review_alerts WHERE series_key=? AND first_seen<=? "
        "ORDER BY first_seen DESC,id DESC LIMIT 1", (series, as_of)).fetchone()
    age = (datetime.fromisoformat(as_of) - datetime.fromisoformat(row["first_seen"])).total_seconds() if row else None
    merge = row is not None and (age == 0 or age < request["rules"]["cooldown_days"] * 86400)
    now = _now()
    if merge:
        alert_id = row["id"]
        connection.execute("UPDATE review_alerts SET last_seen=CASE WHEN last_seen>? THEN last_seen ELSE ? END,updated_at=? WHERE id=?",
                           (as_of, as_of, now, alert_id))
    else:
        alert_id = insert_id(connection,
            "INSERT INTO review_alerts(series_key,source,dataset_version,product_id,aspect,"
            "first_seen,last_seen,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
            (series, product["source"], product["dataset_version"], product["product_id"],
             evaluation["aspect"], as_of, as_of, now, now))
    connection.execute(
        "INSERT INTO review_alert_occurrences(alert_id,run_id,series_key,detected_at,is_new_alert,details_json) "
        "VALUES (?,?,?,?,?,?)",
        (alert_id, run_id, series, as_of, int(not merge), _json({"product": product, **evaluation})))


def list_alerts(db_path, *, status=None, product_id=None, aspect=None, source=None, dataset_version=None,
                run_id=None, page=1, page_size=20):
    _integer(page, "page", maximum=1000000)
    _integer(page_size, "page_size", maximum=100)
    if status is not None and status not in ("pending", "confirmed", "ignored"):
        raise ReviewDataError("status 必须是 pending、confirmed 或 ignored。")
    if aspect is not None and (not isinstance(aspect, str) or aspect not in ASPECTS):
        raise ReviewDataError("aspect 必须是六个有效维度之一。")
    clauses, values = ["TRUE"], []
    for name, value in (("status", status), ("product_id", product_id), ("aspect", aspect),
                        ("source", source), ("dataset_version", dataset_version)):
        if value is not None:
            clauses.append(f"a.{name}=?")
            values.append(_text(value, name, maximum=1000))
    if run_id is not None:
        clauses.append("EXISTS (SELECT 1 FROM review_alert_occurrences o WHERE o.alert_id=a.id AND o.run_id=?)")
        values.append(_text(run_id, "run_id"))
    where = " AND ".join(clauses)
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        _check_monitor(connection)
        total = connection.execute("SELECT COUNT(*) FROM review_alerts a WHERE " + where, values).fetchone()[0]
        rows = connection.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM review_alert_occurrences o WHERE o.alert_id=a.id) occurrence_count "
            "FROM review_alerts a WHERE " + where + " ORDER BY a.last_seen DESC,a.id DESC LIMIT ? OFFSET ?",
            (*values, page_size, (page - 1) * page_size))
        return {"total": total, "page": page, "page_size": page_size, "items": [dict(r) for r in rows]}


def get_alert(db_path, alert_id):
    _integer(alert_id, "alert_id")
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        _check_monitor(connection)
        row = connection.execute("SELECT * FROM review_alerts WHERE id=?", (alert_id,)).fetchone()
        if row is None:
            raise ReviewDataError("告警不存在。")
        occurrences = []
        for hit in connection.execute(
                "SELECT o.*,r.request_json FROM review_alert_occurrences o "
                "JOIN review_analysis_runs r ON r.run_id=o.run_id WHERE o.alert_id=? "
                "ORDER BY o.detected_at,o.id", (alert_id,)):
            occurrences.append({"occurrence_id": hit["id"], "run_id": hit["run_id"],
                                "is_new_alert": hit["is_new_alert"],
                                "detected_at": hit["detected_at"], "request": json.loads(hit["request_json"]),
                                "details": json.loads(hit["details_json"])})
        return {**dict(row), "occurrences": occurrences, "actions": [dict(r) for r in connection.execute(
            "SELECT * FROM review_alert_actions WHERE alert_id=? ORDER BY id", (alert_id,))]}


def set_alert_status(db_path, alert_id, *, status, actor, note=""):
    """Record explicit decisions; repeating the current status is an idempotent no-op."""
    _integer(alert_id, "alert_id")
    if status not in ("confirmed", "ignored"):
        raise ReviewDataError("只能确认 confirmed 或忽略 ignored 告警。")
    actor, note = _text(actor, "actor"), _text(note, "note", maximum=2000, allow_empty=True)
    with closing(_connect(db_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_monitor(connection)
        row = connection.execute("SELECT status FROM review_alerts WHERE id=?", (alert_id,)).fetchone()
        if row is None:
            raise ReviewDataError("告警不存在。")
        changed = row["status"] != status
        if changed:
            now = _now()
            connection.execute(
                "INSERT INTO review_alert_actions(alert_id,from_status,to_status,actor,note,created_at) VALUES (?,?,?,?,?,?)",
                (alert_id, row["status"], status, actor, note, now))
            connection.execute("UPDATE review_alerts SET status=?,updated_at=? WHERE id=?", (status, now, alert_id))
    return {"alert_id": alert_id, "status": status, "changed": changed}
