"""请求级延迟与用量、评论级完成度分别统计；不在 Python 中汇集所有结果。"""

import time

from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.jobs.label_queue import queue_connection, run_state


def run_metrics(db, run_id, *, finish=False):
    state = run_state(db, run_id)
    now = time.time()
    with queue_connection(db) as c, c:
        counts = dict(c.execute("""SELECT t.status,COUNT(*) FROM label_run_tasks rt
            JOIN label_tasks t USING(task_id) WHERE run_id=? GROUP BY t.status""", (run_id,)))
        active = sum(counts.get(s, 0) for s in ("queued", "running", "retry"))
        status = ("partial" if state["status"] == "data_deleted" else "paused" if state["paused_reason"] else
                  ("partial" if counts.get("failed", 0) + counts.get("manual", 0) else "completed")
                  if state["seeded"] and not active else "running")
        if finish and status in {"completed", "partial"} and state["finished_at"] is None:
            c.execute("UPDATE label_runs SET status=?,finished_at=? WHERE run_id=?", (status, now, run_id))
            state["finished_at"] = now
        request = c.execute("""SELECT COUNT(*) model_requests,
            COUNT(*) FILTER (WHERE status='started') uncertain_requests,
            AVG(elapsed_ms) average_request_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY elapsed_ms) p95_request_ms,
            COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,
            COALESCE(SUM(total_tokens),0) total_tokens,
            COUNT(*) FILTER (WHERE input_tokens IS NULL OR output_tokens IS NULL) requests_without_usage,
            COALESCE(SUM(is_retry),0) repeated_requests,COALESCE(SUM(was_split),0) batch_splits
            FROM label_requests WHERE run_id=?""", (run_id,)).fetchone()
        request_values = dict(request)
        # PostgreSQL SUM(BIGINT) 返回 Decimal；持久统计对外仍使用 JSON 整数。
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            request_values[name] = int(request_values[name])
        errors = dict(c.execute("SELECT error_code,COUNT(*) FROM label_requests WHERE run_id=? AND error_code IS NOT NULL GROUP BY error_code", (run_id,)))
        recent = c.execute("""SELECT COUNT(*) FROM label_tasks t JOIN label_run_tasks rt USING(task_id)
            WHERE rt.run_id=? AND t.status='succeeded' AND t.finished_at>=?""", (run_id, now - 60)).fetchone()[0]
        elapsed = max(0.001, (state["finished_at"] or now) - state["started_at"])
        success = counts.get("succeeded", 0)
        return {"run_id": run_id, "profile_id": state["profile_id"], "status": status,
                "paused_reason": state["paused_reason"], "seeded": bool(state["seeded"]),
                "selected_reviews": state["selected"], "successful_reviews": success,
                "deleted_reviews": max(0, state["selected"] - sum(counts.values())),
                "failed_reviews": counts.get("failed", 0) + counts.get("manual", 0),
                "processed_reviews": success + counts.get("failed", 0) + counts.get("manual", 0),
                "counts": counts, "elapsed_seconds": elapsed,
                "successful_reviews_per_minute": success * 60 / elapsed,
                "successful_reviews_last_60_seconds": recent, **request_values, "request_errors": errors,
                "sdk_max_retries": 0, "request_count_semantics": "persisted_attempts_including_crash_uncertainty"}


def queue_metrics(db):
    with queue_connection(db) as c:
        counts = dict(c.execute("SELECT status,COUNT(*) FROM label_tasks GROUP BY status"))
        outbox = c.execute("SELECT COUNT(*) FROM label_outbox").fetchone()[0]
        workers = [dict(r) for r in c.execute("SELECT * FROM label_workers ORDER BY heartbeat_at DESC LIMIT 100")]
        for w in workers:
            w["alive"] = w["stopped_at"] is None and time.time() - w["heartbeat_at"] < 15
        return {"task_counts": counts, "backlog_reviews": counts.get("queued", 0) + counts.get("retry", 0),
                "processing_reviews": counts.get("running", 0), "outbox_rows": outbox, "workers": workers}


def failures(db, *, run_id=None, after_id="", limit=100):
    validate_integer(limit, "limit", maximum=1000)
    with queue_connection(db) as c:
        return [dict(r) for r in c.execute("""SELECT task_id,review_id,profile_id,status,attempts,retry_count,
            split_count,error_code,finished_at FROM label_tasks t WHERE t.status IN ('failed','manual') AND task_id>?
            """ + (" AND EXISTS (SELECT 1 FROM label_run_tasks rt WHERE rt.task_id=t.task_id AND rt.run_id=?)" if run_id else "")
            + " ORDER BY task_id LIMIT ?", (after_id, *((run_id,) if run_id else ()), limit))]
