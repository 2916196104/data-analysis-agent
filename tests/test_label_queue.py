"""真实隔离 PostgreSQL + Redis 协议模拟 + 模拟模型，不读取业务库或模型密钥。"""

import json
import os
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg
from psycopg import sql
from openai import OpenAI

from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.jobs import label_queue as store
from data_analysis_agent.jobs.label_stream import LabelStream
from data_analysis_agent.jobs.label_worker import LabelWorker
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.store import init_database, import_amazon_jsonl

SETTINGS = AgentSettings("simulated-model", "not-a-real-key", "https://mock.invalid/v1", timeout_seconds=10)


def model_response(request):
    body = json.loads(request.content)
    user = json.loads(body["messages"][1]["content"])
    opinions = lambda text: [{"aspect": "quality", "sentiment": "positive", "quote": text[:4]}]
    data = ({"items": [{"review_id": r["review_id"], "opinions": opinions(r["review_text"])} for r in user["items"]]}
            if "items" in user else {"opinions": opinions(user["review_text"])})
    return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": "mock",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(data)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_ADMIN_URL"), "use scripts/queue_acceptance.py for isolated PostgreSQL")
class QueueStage1Tests(unittest.TestCase):
    def setUp(self):
        import fakeredis

        admin = os.environ["TEST_POSTGRES_ADMIN_URL"]
        name = "agent_test_" + uuid.uuid4().hex
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        def cleanup():
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        self.addCleanup(cleanup)
        self.db = urlunsplit(urlsplit(admin)._replace(path="/" + name))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        init_database(self.db)
        init_labeling(self.db)
        self.assertTrue(store.init_queue(self.db)["created"])
        self.assertFalse(store.init_queue(self.db)["created"])
        source = self.root / "simulated.jsonl"
        source.write_text("".join(json.dumps({"parent_asin": "SIMULATED", "user_id": str(i),
            "text": f"质量很好，模拟评论 {i}", "timestamp": 1693526400123, "rating": 5}) + "\n" for i in range(12)), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source)
        if os.environ.get("TEST_REDIS_URL"):
            from redis import Redis

            self.redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        else:
            self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.addCleanup(self.redis.close)
        self.stream = LabelStream(self.redis, name="test-labels:" + uuid.uuid4().hex, reclaim_ms=0)
        self.addCleanup(self.redis.delete, self.stream.name)
        self.run_id = store.create_run(self.db, SETTINGS)
        self.calls = 0

    def scalar(self, query, params=()):
        with store.queue_connection(self.db) as c:
            return c.execute(query, params).fetchone()[0]

    def execute(self, query, params=()):
        with store.queue_connection(self.db) as c, c:
            c.execute(query, params)

    def worker(self, handler=None, **kwargs):
        def model(request):
            self.calls += 1
            return (handler or model_response)(request)
        client = OpenAI(api_key=SETTINGS.api_key, base_url=SETTINGS.base_url, max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(model)))
        self.addCleanup(client.close)
        return LabelWorker(self.db, self.stream, SETTINGS, client=client, **kwargs)

    def seed(self):
        while not store.run_state(self.db, self.run_id)["seeded"]:
            store.seed_run(self.db, self.run_id, chunk_size=5)
        store.publish_outbox(self.db, self.stream)

    def test_stage1_chunked_delivery_commit_ack_and_idempotency(self):
        self.seed()
        worker = self.worker(chunk_size=5)
        for _ in range(4):
            worker.step()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_opinions"), 12)
        self.assertEqual(self.stream.health()["pending_messages"], 0)
        self.assertEqual(self.redis.xlen(self.stream.name), 0)
        calls = self.calls
        with store.queue_connection(self.db) as c:
            tid = c.execute("SELECT task_id FROM label_tasks LIMIT 1").fetchone()[0]
        self.stream.send(tid)
        worker.step()
        self.assertEqual(self.calls, calls)
        second = store.create_run(self.db, SETTINGS)
        self.assertEqual(store.seed_run(self.db, second), 0)

    def test_stage1_worker_skips_other_model_url_and_prompt_without_claiming(self):
        from data_analysis_agent.core.serialization import content_sha256
        from data_analysis_agent.reviews.prompts import PROMPT_VERSION

        foreign_profiles = []
        for settings, prompt in ((replace(SETTINGS, model="old-model"), PROMPT_VERSION),
                                 (replace(SETTINGS, base_url="https://other.invalid/v1"), PROMPT_VERSION),
                                 (SETTINGS, "old-label-prompt")):
            with patch.object(store, "PROMPT_VERSION", prompt):
                run_id = store.create_run(self.db, settings)
            store.seed_run(self.db, run_id)
            foreign_profiles.append(content_sha256([settings.model, settings.base_url, prompt]))
        self.seed()
        # 模拟上次退出留下的过期租约，当前身份不应接管或修改它。
        self.execute("""UPDATE label_tasks SET status='running',worker_id='old-worker',
            lease_token='old-token',lease_until=0 WHERE profile_id=?""", (foreign_profiles[0],))
        worker = self.worker()
        with self.assertLogs("data_analysis_agent.jobs.label_worker", level="WARNING"):
            self.assertEqual(worker.step(), 12)
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE attempts=0"), 36)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE lease_token='old-token'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_outbox"), 36)
        self.assertEqual(self.stream.health()["pending_messages"], 0)
        self.assertEqual(self.redis.xlen(self.stream.name), 0)
        # 仅向当前身份补发，旧任务的发件箱投递次数保持不变。
        deliveries = self.scalar("SELECT SUM(deliveries) FROM label_outbox")
        self.execute("UPDATE label_outbox SET next_at=0")
        self.assertEqual(store.publish_outbox(self.db, self.stream, profile_id=worker.profile_id), 0)
        self.assertEqual(self.scalar("SELECT SUM(deliveries) FROM label_outbox"), deliveries)

    def test_stage1_foreign_backlog_does_not_block_current_and_original_worker_recovers(self):
        previous = replace(SETTINGS, model="previous-model")
        foreign_run = store.create_run(self.db, previous)
        store.seed_run(self.db, foreign_run)
        store.publish_outbox(self.db, self.stream)
        self.stream.max_backlog = 12
        store.seed_run(self.db, self.run_id)
        worker = self.worker()
        with self.assertLogs("data_analysis_agent.jobs.label_worker", level="WARNING"):
            self.assertEqual(worker.run(run_id=self.run_id, max_seconds=10), "completed")
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='queued'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_outbox"), 12)
        # 旧通知释放后仍由 PostgreSQL 补发，不需要删除/重建旧任务。
        self.execute("UPDATE label_outbox SET next_at=0")
        original = LabelWorker(self.db, self.stream, previous, client=worker.client)
        self.assertEqual(original.run(run_id=foreign_run, max_seconds=10), "completed")
        self.assertEqual(self.calls, 6)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 24)
        self.assertEqual(self.scalar("SELECT COUNT(DISTINCT requested_model) FROM review_annotations"), 2)

    def test_stage1_explicit_run_mismatch_and_last_guard_fail_before_model(self):
        from data_analysis_agent.core.errors import ReviewDataError

        run_id = store.create_run(self.db, replace(SETTINGS, model="old-model"))
        worker = self.worker()
        with self.assertRaisesRegex(ReviewDataError, "身份不匹配"):
            worker.run(run_id=run_id, max_seconds=1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks"), 0)
        with self.assertRaisesRegex(ReviewDataError, "身份不匹配"):
            worker._one([{"model": SETTINGS.model, "base_url": SETTINGS.base_url,
                          "prompt_version": "old-label-prompt"}])
        self.assertEqual(self.calls, 0)

    def test_stage1_foreign_notification_without_durable_outbox_is_not_acknowledged(self):
        foreign_run = store.create_run(self.db, replace(SETTINGS, model="old-model"))
        store.seed_run(self.db, foreign_run)
        store.publish_outbox(self.db, self.stream)
        self.execute("DELETE FROM label_outbox")
        self.assertEqual(self.worker().step(), 0)
        self.assertEqual(self.stream.health()["pending_messages"], 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='queued'"), 12)
        self.assertEqual(self.calls, 0)

    def test_stage1_queued_body_fingerprint_survives_content_change_and_restart(self):
        self.seed()
        self.execute("UPDATE reviews SET text='模拟正文已经变化' WHERE id=1")
        self.worker().step()
        self.assertEqual(self.scalar("SELECT error_code FROM label_tasks WHERE review_id=1"), "content_changed")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE review_id=1"), 0)

    def test_stage1_crashed_worker_lease_takeover(self):
        self.seed()
        messages = self.stream.receive("crashed", 4)
        tasks = store.claim_tasks(self.db, [tid for _, tid in messages], "crashed", lease_seconds=1)
        self.assertEqual(len(tasks), 4)
        store.prepare_request(self.db, tasks, "crashed")
        self.execute("UPDATE label_tasks SET lease_until=0 WHERE worker_id='crashed'")
        worker = self.worker()
        for _ in range(4):
            worker.step()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_requests WHERE status='started'"), 1)

    def test_stage1_outbox_failure_and_redis_loss_recover(self):
        store.seed_run(self.db, self.run_id)
        with patch.object(self.stream, "send", side_effect=ConnectionError("redis unavailable")):
            with self.assertRaises(ConnectionError):
                store.publish_outbox(self.db, self.stream)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_outbox WHERE sent_at IS NULL"), 12)
        store.publish_outbox(self.db, self.stream, repair_seconds=0)
        self.redis.delete(self.stream.name)
        store.publish_outbox(self.db, self.stream)
        self.worker().step()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)

    def test_stage1_database_failure_never_acknowledges(self):
        self.seed()
        worker = self.worker()
        from data_analysis_agent.core.database import PostgresConnection, DatabaseError

        original = PostgresConnection.commit
        def fail_result_commit(connection):
            # 只在结果事务提交时注入故障，真实地回滚结果和任务状态。
            if connection.in_transaction and connection.execute("SELECT COUNT(*) FROM review_annotations").fetchone()[0]:
                raise DatabaseError("injected commit failure")
            return original(connection)
        with patch.object(PostgresConnection, "commit", fail_result_commit):
            with self.assertRaises(DatabaseError):
                worker.step()
        self.assertEqual(self.stream.health()["pending_messages"], 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 0)
        self.execute("UPDATE label_tasks SET lease_until=0")
        worker.step()
        worker.step()  # 先完成未尝试项，再恢复提交失败的那批。
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)

    def test_stage2_multiple_workers_shared_concurrency_and_no_duplicate_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from data_analysis_agent.jobs.label_limiter import GlobalLimiter

        self.seed()
        self.stream.reclaim_ms = 120000
        active, peak = 0, 0
        lock = threading.Lock()
        overlap = threading.Event()
        def handler(request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    overlap.set()
            overlap.wait(5)
            time.sleep(0.05)
            response = model_response(request)
            with lock:
                active -= 1
            return response
        limiters = [GlobalLimiter(self.redis, scope=self.run_id, rpm=1000, tpm=1000000,
                                 concurrency=2, cold_start_seconds=0, server_epoch="test-server") for _ in range(4)]
        self.addCleanup(self.redis.delete, *limiters[0].keys)
        workers = [self.worker(handler, chunk_size=4, limiter=limiter) for limiter in limiters]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda w: w.step(), workers))
        self.assertEqual(peak, 2)
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)
        self.assertEqual(self.scalar("SELECT MAX(attempts) FROM label_tasks"), 1)

    def test_stage2_transient_failure_has_bounded_separate_recovery(self):
        self.seed()
        def timeout(request):
            raise httpx.ReadTimeout("simulated")
        worker = self.worker(timeout, max_retries=2, backoff_seconds=0)
        for _ in range(6):
            store.publish_outbox(self.db, self.stream, repair_seconds=0)
            worker.step()
        self.assertEqual(self.calls, 9)  # 3 批 × (首轮 + 两次恢复)，SDK 重试为 0。
        self.assertEqual(self.scalar("SELECT MAX(attempts) FROM label_tasks"), 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='failed'"), 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_requests WHERE error_code='model_timeout'"), 9)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_outbox"), 0)

    def test_stage2_authentication_pauses_and_manual_resubmit_retains_history(self):
        self.seed()
        worker = self.worker(lambda request: httpx.Response(401, json={"error": {"message": "denied"}}))
        worker.step()
        self.assertEqual(self.calls, 1)
        self.assertEqual(store.run_state(self.db, self.run_id)["paused_reason"], "authentication_error")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='manual'"), 4)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='queued'"), 8)
        with store.queue_connection(self.db) as c:
            task = c.execute("SELECT task_id,profile_id FROM label_tasks WHERE status='manual' LIMIT 1").fetchone()
        store.resume_profile(self.db, task[1])
        store.resubmit(self.db, task[0])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE error_code='authentication_error'"), 4)

    def test_stage2_invalid_batch_splits_with_per_review_call_budget(self):
        self.seed()
        worker = self.worker(lambda request: httpx.Response(200, json={"id":"mock", "object":"chat.completion",
            "created":0, "model":"mock", "choices":[{"index":0,"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}), max_calls=2)
        worker.step()
        self.assertEqual(self.calls, 9)  # 每个四条批次：4 -> 2 + 2，剩余调用预算耗尽。
        self.assertEqual(self.scalar("SELECT MAX(attempts) FROM label_tasks"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='failed'"), 12)

    def test_stage3_request_metrics_use_whole_request_latency_and_tokens(self):
        from data_analysis_agent.jobs.label_metrics import run_metrics

        self.seed()
        self.worker().step()
        self.execute("""UPDATE label_requests SET elapsed_ms=CASE review_count WHEN 4 THEN 1000 ELSE 2000 END""")
        result = run_metrics(self.db, self.run_id, finish=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["model_requests"], 3)
        self.assertEqual(result["successful_reviews"], 12)
        self.assertEqual(result["p95_request_ms"], 1000)
        self.assertEqual((result["input_tokens"], result["output_tokens"]), (300, 60))
        self.assertEqual(result["sdk_max_retries"], 0)
        json.dumps(result, allow_nan=False)

    def test_stage3_worker_continuously_drains_chunked_range(self):
        from data_analysis_agent.jobs.label_metrics import run_metrics

        worker = self.worker(chunk_size=5)
        self.assertEqual(worker.run(run_id=self.run_id, max_seconds=30), "completed")
        self.assertEqual(run_metrics(self.db, self.run_id)["successful_reviews"], 12)

    def test_stage3_heartbeat_does_not_conflict_with_result_transactions(self):
        def slow(request):
            time.sleep(0.4)
            return model_response(request)
        worker = self.worker(slow, chunk_size=12, lease_seconds=1)
        self.assertEqual(worker.run(run_id=self.run_id, max_seconds=20), "completed")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)

    def test_stage3_existing_entry_redis_mode_uses_queue_without_local_model_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        from data_analysis_agent.reviews.labeling import run_labeling

        worker = self.worker()
        values = {"LABEL_EXECUTION_MODE":"redis", "LABEL_REDIS_URL":"redis://unused.invalid/0",
                  "LABEL_QUOTA_SCOPE":"SIMULATED", "LABEL_RPM":"100", "LABEL_TPM":"100000"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run, max_seconds=20)
            try:
                with patch("data_analysis_agent.reviews.labeling.read_settings", return_value=values), patch(
                    "data_analysis_agent.reviews.labeling.OpenAI", side_effect=AssertionError("local model bypass")):
                    result = run_labeling(self.db, settings=SETTINGS, limit=12)
                self.assertEqual(result["successful_reviews"], 12)
                self.assertEqual(result["execution_mode"], "redis")
                self.assertEqual(len(result["results"]), 12)
            finally:
                worker.stop.set()
                future.result()

    def test_stage3_import_deletion_checks_active_queue_and_cleans_terminal_dependencies(self):
        from data_analysis_agent.reviews.store import delete_import_runs
        from data_analysis_agent.reviews.human_review import init_workbench
        from data_analysis_agent.core.errors import ReviewDataError
        from data_analysis_agent.jobs.label_metrics import run_metrics

        init_workbench(self.db)
        self.seed()
        with self.assertRaises(ReviewDataError):
            delete_import_runs(self.db, [1])
        self.worker().step()
        old_task = self.scalar("SELECT task_id FROM label_tasks LIMIT 1")
        self.assertEqual(delete_import_runs(self.db, [1])["deleted_reviews"], 12)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_requests"), 3)
        self.assertEqual(run_metrics(self.db, self.run_id)["deleted_reviews"], 12)
        self.stream.send(old_task)
        self.worker().step()
        self.assertEqual(self.stream.health()["pending_messages"], 0)

    def test_stage2_rate_limit_uses_server_retry_after_without_immediate_retry(self):
        self.seed()
        worker = self.worker(lambda request: httpx.Response(429, headers={"retry-after": "2"},
                             json={"error": {"message": "slow down"}}), backoff_seconds=0)
        worker.step()
        self.assertEqual(self.calls, 3)
        self.assertGreaterEqual(self.scalar("""SELECT MIN(t.available_at-r.finished_at) FROM label_tasks t
            JOIN label_request_tasks rt USING(task_id) JOIN label_requests r USING(request_id)"""), 1.9)

    def test_stage2_stale_owner_cannot_commit_after_lease_takeover(self):
        from data_analysis_agent.reviews.labeling import label_batch_once
        from data_analysis_agent.core.errors import ReviewDataError

        self.seed()
        messages = self.stream.receive("old", 4)
        tasks = store.claim_tasks(self.db, [tid for _, tid in messages], "old")
        store.prepare_request(self.db, tasks, "old")
        worker = self.worker()
        result = label_batch_once(worker.client, tasks, SETTINGS)
        self.execute("UPDATE label_tasks SET lease_until=0 WHERE worker_id='old'")
        # 所有首次评论先完成，之后才能接管 old 的恢复批次。
        worker.step()
        worker.step()
        with self.assertRaises(ReviewDataError):
            store.finish_tasks(self.db, tasks, result)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)

    def test_stage1_ack_failure_after_commit_does_not_repeat_model(self):
        self.seed()
        worker = self.worker()
        with patch.object(self.stream, "ack", side_effect=ConnectionError("simulated lost ack")):
            with self.assertRaises(ConnectionError):
                worker.step()
        self.assertEqual(self.calls, 3)
        self.worker().step()
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.stream.health()["pending_messages"], 0)

    def test_stage1_publish_before_outbox_commit_can_duplicate_safely(self):
        store.seed_run(self.db, self.run_id)
        send = self.stream.send
        def sent_then_crashed(task_id):
            send(task_id)
            raise ConnectionError("simulated crash before outbox commit")
        with patch.object(self.stream, "send", side_effect=sent_then_crashed):
            with self.assertRaises(ConnectionError):
                store.publish_outbox(self.db, self.stream)
        store.publish_outbox(self.db, self.stream)
        self.assertEqual(self.redis.xlen(self.stream.name), 13)
        self.worker().step()
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)

    def test_stage1_crash_after_model_response_can_repeat_model_call(self):
        self.seed()
        with patch.object(store, "finish_tasks", side_effect=InterruptedError("simulated process crash")):
            with self.assertRaises(InterruptedError):
                self.worker().step()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations"), 0)
        self.execute("UPDATE label_tasks SET lease_until=0")
        worker = self.worker()
        worker.step()
        worker.step()
        self.assertEqual(self.calls, 4)  # 一次已响应但未提交的请求被重新调用。
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)

    def test_stage3_rollback_keeps_original_annotations_and_can_reinitialize(self):
        self.seed()
        self.worker().step()
        rollback = Path(__file__).resolve().parents[1] / "docs/sql/label-queue-down.sql"
        with store.queue_connection(self.db) as c:
            c.executescript(rollback.read_text(encoding="utf-8"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM review_annotations WHERE status='success'"), 12)
        self.assertTrue(store.init_queue(self.db)["created"])

    @unittest.skipUnless(os.environ.get("TEST_REDIS_URL"), "requires real isolated Redis")
    def test_stage3_two_windows_worker_processes_use_shared_postgres_and_redis(self):
        import subprocess
        import sys
        import threading
        from dataclasses import replace
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from data_analysis_agent.jobs.label_limiter import GlobalLimiter

        active, peak, calls = 0, 0, 0
        lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                nonlocal active, peak, calls
                body = handler.rfile.read(int(handler.headers["Content-Length"]))
                with lock:
                    active += 1
                    peak = max(peak, active)
                    calls += 1
                try:
                    time.sleep(0.3)
                    response = model_response(httpx.Request("POST", "http://mock", content=body))
                    handler.send_response(200)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", str(len(response.content)))
                    handler.end_headers()
                    handler.wfile.write(response.content)
                finally:
                    with lock:
                        active -= 1
            def log_message(handler, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        settings = replace(SETTINGS, base_url=f"http://127.0.0.1:{server.server_port}/v1")
        self.run_id = store.create_run(self.db, settings)
        self.seed()
        scope = uuid.uuid4().hex
        limiter = GlobalLimiter(self.redis, scope=scope, rpm=10000, tpm=10000000, cold_start_seconds=0)
        self.addCleanup(self.redis.delete, *limiter.keys)
        token, _ = limiter.reserve(1)
        limiter.release(token, {})  # 只为隔离测试预热；生产仍执行冷启动保护。
        env_file = self.root / "worker.env"
        env_file.write_text("\n".join([
            "LLM_MODEL_ID=" + settings.model, "LLM_API_KEY=not-a-real-key", "LLM_BASE_URL=" + settings.base_url,
            "LABEL_REDIS_URL=" + os.environ["TEST_REDIS_URL"], "LABEL_QUOTA_SCOPE=" + scope,
            "LABEL_RPM=10000", "LABEL_TPM=10000000", "LABEL_STREAM=" + self.stream.name,
            "LABEL_GROUP=" + self.stream.group]), encoding="utf-8")
        env = {k:v for k,v in os.environ.items() if not k.startswith(("LLM_", "LABEL_"))}
        env["NO_PROXY"] = "127.0.0.1,localhost"
        processes, logs = [], []
        try:
            for i in range(2):
                path = self.root / f"worker-{i}.log"
                handle = path.open("w", encoding="utf-8")
                logs.append((path, handle))
                processes.append(subprocess.Popen([sys.executable, "-m", "data_analysis_agent.cli.labeling", "queue", "worker",
                    "--db", self.db, "--env-file", str(env_file), "--run-id", self.run_id, "--max-seconds", "60"],
                    cwd=Path(__file__).resolve().parents[1], env=env, stdout=handle, stderr=subprocess.STDOUT))
            for process in processes:
                process.wait(timeout=65)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            for _, handle in logs:
                handle.close()
        for process, (path, _) in zip(processes, logs):
            self.assertEqual(process.returncode, 0, path.read_text(encoding="utf-8"))
        self.assertEqual(calls, 3)
        self.assertLessEqual(peak, 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_workers"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM label_tasks WHERE status='succeeded'"), 12)


class SharedLimiterTests(unittest.TestCase):
    def setUp(self):
        import fakeredis
        from data_analysis_agent.jobs.label_limiter import GlobalLimiter

        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.make = lambda **kw: GlobalLimiter(self.redis, scope="quota", cold_start_seconds=0, server_epoch="test-server", **kw)

    def test_stage2_rpm_tpm_concurrency_shared_and_bounded(self):
        first = self.make(rpm=2, tpm=100, concurrency=2, window_ms=100)
        second = self.make(rpm=2, tpm=100, concurrency=2, window_ms=100)
        a, _ = first.reserve(60)
        self.assertIsNotNone(a)
        self.assertIsNone(second.reserve(60)[0])
        first.release(a, {"total_tokens": 20})
        b, _ = second.reserve(60)
        self.assertIsNotNone(b)
        second.release(b, {})
        self.assertIsNone(first.reserve(1)[0])  # 即使没有正在执行的请求，RPM 仍然生效。
        time.sleep(0.12)
        self.assertIsNotNone(first.reserve(1)[0])

    def test_stage2_configuration_mismatch_and_oversized_request_fail_closed(self):
        from data_analysis_agent.core.config import ConfigurationError

        first = self.make(rpm=10, tpm=100)
        first.reserve(10)
        with self.assertRaises(ConfigurationError):
            self.make(rpm=11, tpm=100).reserve(10)
        with self.assertRaises(ConfigurationError):
            first.reserve(101)

    def test_stage2_global_retry_after_and_restart_cold_start(self):
        from data_analysis_agent.jobs.label_limiter import GlobalLimiter

        first = self.make(rpm=10, tpm=100)
        a, _ = first.reserve(10)
        first.release(a, {})
        first.cooldown(0.1)
        self.assertIsNone(self.make(rpm=10, tpm=100).reserve(1)[0])
        time.sleep(0.12)
        self.assertIsNotNone(first.reserve(1)[0])
        self.redis.flushdb()
        cold = GlobalLimiter(self.redis, scope="quota", rpm=10, tpm=100, cold_start_seconds=0.1, server_epoch="test-server")
        self.assertIsNone(cold.reserve(1)[0])

    @unittest.skipUnless(os.environ.get("TEST_REDIS_URL"), "requires real isolated Redis")
    def test_stage2_persisted_quota_config_detects_new_redis_server_epoch(self):
        from redis import Redis
        from data_analysis_agent.jobs.label_limiter import GlobalLimiter

        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        self.addCleanup(redis.close)
        limiter = GlobalLimiter(redis, scope=uuid.uuid4().hex, rpm=100, tpm=10000, cold_start_seconds=0.1)
        self.addCleanup(redis.delete, *limiter.keys)
        self.assertIsNone(limiter.reserve(1)[0])
        time.sleep(0.12)
        token, _ = limiter.reserve(1)
        self.assertIsNotNone(token)
        limiter.release(token, {})
        redis.hset(limiter.keys[0], "server_epoch", "previous-redis-process")
        self.assertIsNone(limiter.reserve(1)[0])
        self.assertEqual(redis.hget(limiter.keys[0], "server_epoch"), redis.info("server")["run_id"])


if __name__ == "__main__":
    unittest.main()
