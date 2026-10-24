"""评论级持久任务、事务发件箱和租约；PostgreSQL 是唯一事实来源。"""

import hashlib
import json
import time
import uuid
from contextvars import ContextVar
from contextlib import closing, contextmanager

from data_analysis_agent.core.database import connect_database, is_postgres
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.reviews.label_store import (
    RECOVERABLE_ERROR_CODES, append_annotation_on_connection, check_labeling_schema,
)
from data_analysis_agent.reviews.prompts import PROMPT_VERSION
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.reviews.store import connect_review_database

SCHEMA = """
CREATE TABLE label_queue_schema(version INTEGER PRIMARY KEY);
INSERT INTO label_queue_schema VALUES (1);
CREATE TABLE label_profiles (
    profile_id TEXT PRIMARY KEY, model TEXT NOT NULL, base_url TEXT NOT NULL,
    prompt_version TEXT NOT NULL, paused_reason TEXT
);
CREATE TABLE label_runs (
    run_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES label_profiles,
    scope_json TEXT NOT NULL, selection TEXT NOT NULL CHECK(selection IN ('first','transient','failed')),
    upper_id BIGINT NOT NULL, cursor_id BIGINT NOT NULL DEFAULT 0,
    review_limit BIGINT, selected BIGINT NOT NULL DEFAULT 0, seeded INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running', started_at DOUBLE PRECISION NOT NULL,
    finished_at DOUBLE PRECISION
);
CREATE TABLE label_tasks (
    task_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES label_profiles,
    origin_run_id TEXT NOT NULL REFERENCES label_runs,
    review_id INTEGER NOT NULL REFERENCES reviews(id), content_sha256 TEXT NOT NULL, body_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','retry','succeeded','failed','manual')),
    attempts INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
    split_count INTEGER NOT NULL DEFAULT 0, available_at DOUBLE PRECISION NOT NULL,
    lease_until DOUBLE PRECISION, lease_token TEXT, worker_id TEXT,
    annotation_id INTEGER REFERENCES review_annotations(id), error_code TEXT,
    created_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION,
    UNIQUE(review_id,content_sha256,body_sha256,profile_id)
);
CREATE INDEX label_tasks_ready ON label_tasks(status,available_at);
CREATE INDEX label_tasks_lease ON label_tasks(lease_until) WHERE status='running';
CREATE TABLE label_run_tasks (
    run_id TEXT NOT NULL REFERENCES label_runs, task_id TEXT NOT NULL REFERENCES label_tasks,
    PRIMARY KEY(run_id,task_id)
);
CREATE INDEX label_run_task_lookup ON label_run_tasks(task_id,run_id);
CREATE TABLE label_outbox (
    task_id TEXT PRIMARY KEY REFERENCES label_tasks, next_at DOUBLE PRECISION NOT NULL,
    sent_at DOUBLE PRECISION, deliveries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX label_outbox_due ON label_outbox(next_at,task_id);
CREATE TABLE label_requests (
    request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES label_runs,
    worker_id TEXT NOT NULL, review_count INTEGER NOT NULL, started_at DOUBLE PRECISION NOT NULL,
    finished_at DOUBLE PRECISION, elapsed_ms DOUBLE PRECISION, status TEXT NOT NULL,
    error_code TEXT, input_tokens BIGINT, output_tokens BIGINT, total_tokens BIGINT,
    retry_after DOUBLE PRECISION, is_retry INTEGER NOT NULL, sdk_max_retries INTEGER NOT NULL DEFAULT 0
    ,was_split INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX label_request_run ON label_requests(run_id,started_at);
CREATE TABLE label_request_tasks (
    request_id TEXT NOT NULL REFERENCES label_requests, task_id TEXT NOT NULL REFERENCES label_tasks,
    PRIMARY KEY(request_id,task_id)
);
CREATE TABLE label_workers (
    worker_id TEXT PRIMARY KEY, heartbeat_at DOUBLE PRECISION NOT NULL, stopped_at DOUBLE PRECISION
);
"""

_session = ContextVar("label_queue_connection", default=None)


@contextmanager
def queue_session(db):
    """一个 Worker 线程复用一个连接；心跳线程始终使用独立连接。"""
    with closing(connect_database(db, namespace="label_queue")) as connection:
        token = _session.set((db, connection))
        try:
            yield
        finally:
            _session.reset(token)


@contextmanager
def queue_connection(db):
    if not is_postgres(db):
        raise ReviewDataError("持久标注队列只支持 PostgreSQL。")
    session = _session.get()
    if session and session[0] == db:
        try:
            yield session[1]
        except Exception:
            session[1].rollback()
            raise
    else:
        with closing(connect_database(db, namespace="label_queue")) as connection:
            yield connection


def init_queue(db):
    with closing(connect_review_database(db, readonly=True)) as connection:
        check_labeling_schema(connection)
    with queue_connection(db) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT to_regclass('public.label_queue_schema')").fetchone()[0]
        if row:
            if connection.execute("SELECT version FROM label_queue_schema").fetchall() != [(1,)]:
                raise ReviewDataError("持久标注队列版本不支持。")
            return {"created": False, "version": 1}
        connection.executescript(SCHEMA)
    return {"created": True, "version": 1}


def create_run(db, settings, *, limit=None, selection="first", **filters):
    if limit is not None:
        validate_integer(limit, "limit", maximum=1000000000)
    if selection not in {"first", "transient", "failed"}:
        raise ReviewDataError("selection 必须为 first、transient 或 failed。")
    _, _, scope = review_scope(**filters)
    profile = content_sha256([settings.model, settings.base_url, PROMPT_VERSION])
    run_id = uuid.uuid4().hex
    with queue_connection(db) as connection, connection:
        connection.execute("INSERT INTO label_profiles VALUES (?,?,?,?,NULL) ON CONFLICT DO NOTHING",
                           (profile, settings.model, settings.base_url, PROMPT_VERSION))
        upper = connection.execute("SELECT COALESCE(MAX(id),0) FROM reviews").fetchone()[0]
        connection.execute("""INSERT INTO label_runs
            (run_id,profile_id,scope_json,selection,upper_id,review_limit,started_at)
            VALUES (?,?,?,?,?,?,?)""", (run_id, profile, canonical_json(scope), selection, upper, limit, time.time()))
    return run_id


def seed_run(db, run_id, *, chunk_size=200):
    """按主键游标逐块创建任务和发件箱；游标也在同一事务内提交。"""
    validate_integer(chunk_size, "chunk_size", maximum=1000)
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        run = c.execute("SELECT * FROM label_runs WHERE run_id=? FOR UPDATE", (run_id,)).fetchone()
        if run is None:
            raise ReviewDataError("运行 ID 不存在。")
        if run["seeded"]:
            return 0
        scope = json.loads(run["scope_json"])
        where, params, _ = review_scope(**{k: v for k, v in scope.items() if k != "timezone"})
        take = min(chunk_size, run["review_limit"] - run["selected"]) if run["review_limit"] else chunk_size
        identity = c.execute("SELECT * FROM label_profiles WHERE profile_id=?", (run["profile_id"],)).fetchone()
        key = (identity["model"], identity["base_url"], identity["prompt_version"])
        match = ("a.review_id=r.id AND a.content_sha256=r.content_sha256 AND "
                 "a.requested_model=? AND a.base_url=? AND a.prompt_version=?")
        conditions = [where, "r.id>? AND r.id<=?", "r.has_text=1"]
        params = [*params, run["cursor_id"], run["upper_id"]]
        if run["selection"] == "first":
            conditions.append("NOT EXISTS (SELECT 1 FROM review_annotations a WHERE " + match + ")")
            params.extend(key)
        else:
            conditions.append("NOT EXISTS (SELECT 1 FROM review_annotations a WHERE " + match + " AND a.status='success')")
            params.extend(key)
            conditions.append("(SELECT a.error_code FROM review_annotations a WHERE " + match + " ORDER BY a.id DESC LIMIT 1) "
                              + ("IN (?,?,?)" if run["selection"] == "transient" else "IS NOT NULL"))
            params.extend(key)
            if run["selection"] == "transient":
                params.extend(RECOVERABLE_ERROR_CODES)
        rows = list(c.execute("SELECT r.id,r.content_sha256,encode(sha256(convert_to(r.text,'UTF8')),'hex') body_sha256 FROM reviews r WHERE "
                              + " AND ".join(conditions) + " ORDER BY r.id LIMIT ?", (*params, max(0, take))))
        now = time.time()
        tasks = [(content_sha256([r[0], r[1], r[2], *key]), run["profile_id"], run_id, r[0], r[1], r[2], now, now) for r in rows]
        if tasks:
            c.executemany("""INSERT INTO label_tasks
                (task_id,profile_id,origin_run_id,review_id,content_sha256,body_sha256,status,available_at,created_at)
                VALUES (?,?,?,?,?,?,'queued',?,?) ON CONFLICT DO NOTHING""", tasks)
            if run["selection"] == "failed":
                c.executemany("""UPDATE label_tasks SET status='queued',attempts=0,retry_count=0,finished_at=NULL,
                    origin_run_id=?,available_at=? WHERE task_id=? AND status IN ('failed','manual')""",
                              [(run_id, now, t[0]) for t in tasks])
            c.executemany("INSERT INTO label_run_tasks VALUES (?,?) ON CONFLICT DO NOTHING",
                          [(run_id, t[0]) for t in tasks])
            c.executemany("INSERT INTO label_outbox(task_id,next_at) VALUES (?,?) ON CONFLICT DO NOTHING",
                          [(t[0], now) for t in tasks])
        seeded = len(rows) < take or (run["review_limit"] is not None and run["selected"] + len(rows) >= run["review_limit"])
        c.execute("UPDATE label_runs SET cursor_id=?,selected=selected+?,seeded=? WHERE run_id=?",
                  (rows[-1][0] if rows else run["upper_id"], len(rows), int(seeded), run_id))
        return len(rows)


def publish_outbox(db, stream, *, limit=200, repair_seconds=120):
    """XADD 与提交间崩溃允许重复；周期补发还覆盖 Redis 重启或消息丢失。"""
    now = time.time()
    limit = min(limit, max(0, stream.max_backlog - stream.redis.xlen(stream.name)))
    if not limit:
        return 0
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        rows = c.execute("""SELECT o.task_id FROM label_outbox o JOIN label_tasks t USING(task_id)
            JOIN label_profiles p USING(profile_id)
            WHERE o.next_at<=? AND p.paused_reason IS NULL AND
            (t.status IN ('queued','retry') OR (t.status='running' AND t.lease_until<=?))
            ORDER BY o.next_at,o.task_id LIMIT ? FOR UPDATE OF o SKIP LOCKED""", (now, now, limit)).fetchall()
        for row in rows:
            stream.send(row[0])
        if rows:
            c.executemany("UPDATE label_outbox SET sent_at=?,next_at=?,deliveries=deliveries+1 WHERE task_id=?",
                          [(now, now + repair_seconds, r[0]) for r in rows])
        return len(rows)


def claim_tasks(db, ids, worker_id, *, lease_seconds=120):
    """数据库租约抵御重复消息与多个消费者同时接管；返回固定大小的数据块。"""
    if not ids:
        return []
    ids = list(dict.fromkeys(ids))
    token, now = uuid.uuid4().hex, time.time()
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        rows = c.execute("""SELECT t.*,r.text,p.model,p.base_url,p.prompt_version,
            t.origin_run_id run_id
            FROM label_tasks t JOIN reviews r ON r.id=t.review_id JOIN label_profiles p USING(profile_id)
            WHERE t.task_id IN (""" + ",".join("?" for _ in ids) + """ ) AND p.paused_reason IS NULL
            AND ((t.status IN ('queued','retry') AND t.available_at<=?)
                OR (t.status='running' AND t.lease_until<=?))
            ORDER BY t.task_id FOR UPDATE OF t SKIP LOCKED""", (*ids, now, now)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            # 首轮未完成时，恢复任务不抢占正常评论。
            if item["attempts"] and c.execute("""SELECT 1 FROM label_run_tasks rt JOIN label_runs u USING(run_id)
                WHERE rt.task_id=? AND (u.seeded=0 OR EXISTS
                    (SELECT 1 FROM label_run_tasks rt2 JOIN label_tasks t2 USING(task_id)
                     WHERE rt2.run_id=u.run_id AND t2.attempts=0 AND t2.status IN ('queued','running'))) LIMIT 1""",
                                            (item["task_id"],)).fetchone():
                continue
            c.execute("UPDATE label_tasks SET status='running',lease_token=?,lease_until=?,worker_id=? WHERE task_id=?",
                      (token, now + lease_seconds, worker_id, item["task_id"]))
            item.update(lease_token=token, id=item["review_id"])
            result.append(item)
        return result


def success_id(c, task):
    return c.execute("""SELECT id FROM review_annotations WHERE review_id=? AND content_sha256=?
        AND requested_model=? AND base_url=? AND prompt_version=? AND status='success'""",
                     (task["review_id"], task["content_sha256"], task["model"], task["base_url"], task["prompt_version"])).fetchone()


def prepare_request(db, tasks, worker_id, *, max_calls=6):
    """调用前再次检查成功、配置暂停和执行权；预先持久化调用次数，崩溃也占预算。"""
    ready, now = [], time.time()
    request_id = uuid.uuid4().hex
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        for task in sorted(tasks, key=lambda t: t["task_id"]):
            row = c.execute("SELECT * FROM label_tasks WHERE task_id=? FOR UPDATE", (task["task_id"],)).fetchone()
            if row["lease_token"] != task["lease_token"] or row["status"] != "running" or row["lease_until"] <= now:
                continue
            completed = success_id(c, task)
            if completed:
                c.execute("UPDATE label_tasks SET status='succeeded',annotation_id=?,finished_at=? WHERE task_id=?",
                          (completed[0], now, task["task_id"]))
                c.execute("DELETE FROM label_outbox WHERE task_id=?", (task["task_id"],))
                continue
            if c.execute("SELECT paused_reason FROM label_profiles WHERE profile_id=?", (task["profile_id"],)).fetchone()[0]:
                c.execute("UPDATE label_tasks SET status='queued',lease_token=NULL,lease_until=NULL WHERE task_id=?", (task["task_id"],))
                continue
            current = c.execute("SELECT content_sha256,text FROM reviews WHERE id=?", (task["review_id"],)).fetchone()
            error = "content_changed" if not current or tuple(current) != (task["content_sha256"], task["text"]) else None
            if current and hashlib.sha256(current[1].encode("utf-8")).hexdigest() != task["body_sha256"]:
                error = "content_changed"
            from data_analysis_agent.reviews.prompts import MAX_TEXT_CHARS

            if len(task["text"]) > MAX_TEXT_CHARS:
                error = "input_too_long"
            if row["attempts"] >= max_calls:
                error = "call_budget_exhausted"
            if error:
                c.execute("UPDATE label_tasks SET status='failed',error_code=?,finished_at=? WHERE task_id=?", (error, now, task["task_id"]))
                c.execute("DELETE FROM label_outbox WHERE task_id=?", (task["task_id"],))
                continue
            task["attempts"] = row["attempts"] + 1
            c.execute("UPDATE label_tasks SET attempts=attempts+1 WHERE task_id=?", (task["task_id"],))
            ready.append(task)
        if ready:
            c.execute("""INSERT INTO label_requests
                (request_id,run_id,worker_id,review_count,started_at,status,is_retry)
                VALUES (?,?,?,?,?,'started',?)""", (request_id, ready[0]["run_id"], worker_id, len(ready), now,
                                                  int(any(t["attempts"] > 1 for t in ready))))
            c.executemany("INSERT INTO label_request_tasks VALUES (?,?)", [(request_id, t["task_id"]) for t in ready])
    return request_id, ready


def request_finished(db, request_id, *, elapsed_ms, error_code=None, usage=None, retry_after=None, was_split=False):
    usage = usage or {}
    with queue_connection(db) as c, c:
        c.execute("""UPDATE label_requests SET finished_at=?,elapsed_ms=?,status=?,error_code=?,
            input_tokens=?,output_tokens=?,total_tokens=?,retry_after=?,was_split=? WHERE request_id=?""",
                  (time.time(), elapsed_ms, "failed" if error_code else "succeeded", error_code,
                   usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"), retry_after, int(was_split), request_id))


def finish_tasks(db, tasks, outcome, *, max_calls=6, max_retries=2, backoff_seconds=10, retry_after=0):
    """标注结果、失败历史、任务状态和下一次发件箱在一个事务中提交。"""
    task_map = {t["review_id"]: t for t in tasks}
    now = time.time()
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        for item in sorted(outcome.items, key=lambda i: i["review"]["task_id"]):
            task = task_map[item["review"]["id"]]
            row = c.execute("SELECT * FROM label_tasks WHERE task_id=? FOR UPDATE", (task["task_id"],)).fetchone()
            if row["lease_token"] != task["lease_token"] or row["status"] != "running" or row["lease_until"] <= now:
                raise ReviewDataError("任务租约已失效；结果未覆盖新的执行者。")
            code = item["error_code"]
            annotation = append_annotation_on_connection(
                c, task, model=task["model"], base_url=task["base_url"], prompt_version=task["prompt_version"],
                status="failed" if code else "success", opinions=item["opinions"], error_code=code,
                elapsed_ms=item["elapsed_ms"], **item["metadata"],
            )
            completed = success_id(c, task)
            retry = (not completed and code in {*RECOVERABLE_ERROR_CODES, "rate_limit"}
                     and row["retry_count"] < max_retries and row["attempts"] < max_calls)
            status = ("succeeded" if completed else "retry" if retry else "manual" if code in {
                "authentication_error", "model_api_error", "input_too_long", "quota_request_too_large"} else "failed")
            delay = max(retry_after, min(3600, backoff_seconds * 2 ** row["retry_count"]))
            c.execute("""UPDATE label_tasks SET status=?,annotation_id=?,error_code=?,retry_count=retry_count+?,
                available_at=?,lease_token=NULL,lease_until=NULL,finished_at=? WHERE task_id=?""",
                      (status, completed[0] if completed else annotation, None if completed else code, int(retry),
                       now + delay, None if retry else now, task["task_id"]))
            if retry:
                c.execute("""INSERT INTO label_outbox(task_id,next_at) VALUES (?,?) ON CONFLICT(task_id)
                    DO UPDATE SET next_at=excluded.next_at,sent_at=NULL""", (task["task_id"], now + delay))
            else:
                c.execute("DELETE FROM label_outbox WHERE task_id=?", (task["task_id"],))
            if code == "authentication_error":
                c.execute("UPDATE label_profiles SET paused_reason='authentication_error' WHERE profile_id=?", (task["profile_id"],))


def record_split(db, tasks):
    with queue_connection(db) as c, c:
        c.executemany("UPDATE label_tasks SET split_count=split_count+1 WHERE task_id=? AND lease_token=?",
                      [(t["task_id"], t["lease_token"]) for t in tasks])


def pulse(db, worker_id, *, lease_seconds=120, stopped=False):
    now = time.time()
    with queue_connection(db) as c, c:
        c.execute("""INSERT INTO label_workers VALUES (?,?,?) ON CONFLICT(worker_id)
            DO UPDATE SET heartbeat_at=excluded.heartbeat_at,stopped_at=excluded.stopped_at""",
                  (worker_id, now, now if stopped else None))
        if not stopped:
            c.execute("""WITH owned AS (SELECT task_id FROM label_tasks WHERE worker_id=?
                AND status='running' AND lease_until>? ORDER BY task_id FOR UPDATE SKIP LOCKED)
                UPDATE label_tasks t SET lease_until=? FROM owned WHERE t.task_id=owned.task_id""",
                      (worker_id, now, now + lease_seconds))


def terminal_ids(db, ids):
    if not ids:
        return set()
    with queue_connection(db) as c:
        rows = list(c.execute("SELECT task_id,status FROM label_tasks WHERE task_id IN ("
                              + ",".join("?" for _ in ids) + ")", ids))
        active = {r[0] for r in rows if r[1] in {"queued", "running"}}
        # 已随导入删除的最终任务也可丢弃遗留消息，避免形成永久 pending。
        return set(ids) - active


def resubmit(db, task_id):
    """人工明确重开最终失败的同一身份；保留历史请求和失败标注。"""
    with queue_connection(db) as c, c:
        c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        row = c.execute("SELECT * FROM label_tasks WHERE task_id=? FOR UPDATE", (task_id,)).fetchone()
        if not row or row["status"] not in {"failed", "manual"}:
            raise ReviewDataError("只能人工重新提交 failed 或 manual 任务。")
        c.execute("""UPDATE label_tasks SET status='queued',attempts=0,retry_count=0,lease_token=NULL,
            lease_until=NULL,finished_at=NULL,available_at=? WHERE task_id=?""", (time.time(), task_id))
        c.execute("INSERT INTO label_outbox(task_id,next_at) VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET next_at=excluded.next_at",
                  (task_id, time.time()))
        c.execute("UPDATE label_runs SET status='running',finished_at=NULL WHERE run_id IN (SELECT run_id FROM label_run_tasks WHERE task_id=?)", (task_id,))


def resume_profile(db, profile_id):
    with queue_connection(db) as c, c:
        c.execute("UPDATE label_profiles SET paused_reason=NULL WHERE profile_id=?", (profile_id,))


def repair_outbox(db):
    """Redis 重启后的显式快速补发；自动补发也会在 repair_seconds 内触发。"""
    now = time.time()
    with queue_connection(db) as c, c:
        c.execute("""INSERT INTO label_outbox(task_id,next_at)
            SELECT task_id,? FROM label_tasks WHERE status IN ('queued','retry','running')
            ON CONFLICT(task_id) DO UPDATE SET next_at=excluded.next_at""", (now,))


def release_worker(db, worker_id):
    with queue_connection(db) as c, c:
        c.execute("""UPDATE label_tasks SET status=CASE WHEN attempts=0 THEN 'queued' ELSE 'retry' END,
            lease_token=NULL,lease_until=NULL WHERE worker_id=? AND status='running'""", (worker_id,))


def run_state(db, run_id):
    with queue_connection(db) as c:
        row = c.execute("SELECT u.*,p.paused_reason FROM label_runs u JOIN label_profiles p USING(profile_id) WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ReviewDataError("运行 ID 不存在。")
        return dict(row)


def completion_state(db, run_id):
    """轮询只检查是否仍有工作，不反复排序全部请求延迟。"""
    state = run_state(db, run_id)
    if state["status"] == "data_deleted":
        return "partial"
    if state["paused_reason"]:
        return "paused"
    if not state["seeded"]:
        return "running"
    with queue_connection(db) as c, c:
        if c.execute("""SELECT 1 FROM label_run_tasks rt JOIN label_tasks t USING(task_id)
            WHERE rt.run_id=? AND t.status IN ('queued','retry','running') LIMIT 1""", (run_id,)).fetchone():
            return "running"
        failed = c.execute("""SELECT 1 FROM label_run_tasks rt JOIN label_tasks t USING(task_id)
            WHERE rt.run_id=? AND t.status IN ('failed','manual') LIMIT 1""", (run_id,)).fetchone()
        status = "partial" if failed else "completed"
        c.execute("UPDATE label_runs SET status=?,finished_at=COALESCE(finished_at,?) WHERE run_id=?", (status, time.time(), run_id))
        return status


def delete_review_tasks_on_connection(connection, review_ids):
    """配合现有导入删除事务清理最终任务；执行中或等待恢复的评论拒绝删除。"""
    for start in range(0, len(review_ids), 500):
        ids = review_ids[start:start + 500]
        marks = ",".join("?" for _ in ids)
        tasks = connection.execute("SELECT task_id,status FROM label_tasks WHERE review_id IN ("
                                   + marks + ") ORDER BY task_id FOR UPDATE", ids).fetchall()
        if any(t[1] in {"queued", "retry", "running"} for t in tasks):
            raise ReviewDataError("该导入批次仍有排队、执行或恢复中的标注任务，请等待标注结束后再删除。")
        if not tasks:
            continue
        selected = "SELECT task_id FROM label_tasks WHERE review_id IN (" + marks + ")"
        connection.execute("UPDATE label_runs SET status='data_deleted' WHERE run_id IN "
                           "(SELECT run_id FROM label_run_tasks WHERE task_id IN (" + selected + "))", ids)
        for table in ("label_request_tasks", "label_outbox", "label_run_tasks"):
            connection.execute("DELETE FROM " + table + " WHERE task_id IN (" + selected + ")", ids)
        connection.execute("DELETE FROM label_tasks WHERE review_id IN (" + marks + ")", ids)
