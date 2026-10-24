"""旧 CLI/API 在 Redis 模式下只提交和观察，模型调用统一交给队列 Worker。"""

import time

from data_analysis_agent.core.config import LabelQueueSettings
from data_analysis_agent.jobs import label_queue as store
from data_analysis_agent.jobs.label_metrics import run_metrics
from data_analysis_agent.reviews.prompts import PROMPT_VERSION


def run_queued_labeling(db, settings, values, *, limit, retry_failed=False, retry_transient=False,
                        scope=None, progress=None):
    config = LabelQueueSettings.from_values(values, timeout_seconds=settings.timeout_seconds)
    filters = {k: v for k, v in scope.items() if k != "timezone"}
    selection = "failed" if retry_failed else "transient" if retry_transient else "first"
    run_id = store.create_run(db, settings, limit=limit, selection=selection, **filters)
    started = time.monotonic()
    with store.queue_session(db):
        while not store.run_state(db, run_id)["seeded"]:
            store.seed_run(db, run_id, chunk_size=config.chunk_size)
        while True:
            result = run_metrics(db, run_id, finish=True)
            if progress:
                progress({"stage": "labeling", "completed": result["processed_reviews"], "total": result["selected_reviews"],
                          "unit": "reviews", "successful_reviews": result["successful_reviews"],
                          "failed_reviews": result["failed_reviews"], "model_calls": result["model_requests"], "run_id": run_id})
            if result["status"] in {"completed", "partial", "paused"}:
                break
            if time.monotonic() - started >= config.coordinator_timeout_seconds:
                result["paused_reason"] = "coordinator_timeout"
                break
            time.sleep(0.5)
        with store.queue_connection(db) as c:
            # 兼容旧入口最多 1000 条的结果契约；大范围 queue 命令只返回聚合统计。
            items = [dict(row) for row in c.execute("""SELECT t.review_id,t.annotation_id,
                CASE WHEN t.status='succeeded' THEN 'success' ELSE 'failed' END status,t.error_code,
                (SELECT COUNT(*) FROM review_opinions o WHERE o.annotation_id=t.annotation_id) opinion_count
                FROM label_run_tasks rt JOIN label_tasks t USING(task_id) WHERE rt.run_id=? ORDER BY t.review_id LIMIT 1000""", (run_id,))]
        return {**result, "status": "ok" if result["status"] == "completed" else "partial",
                "stopped_reason": result["paused_reason"], "model": settings.model, "base_url": settings.base_url,
                "prompt_version": PROMPT_VERSION, "batch_size": config.batch_size,
                "model_calls": result["model_requests"], "opinion_count": sum(i["opinion_count"] for i in items),
                "already_completed": 0, "scope": scope, "results": items, "execution_mode": "redis"}
