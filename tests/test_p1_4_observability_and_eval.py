"""P1-4 可观测性、单任务用量归因与离线评估基准回归测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from fastapi.testclient import TestClient
import httpx
from openai import OpenAI
import psycopg

from eval_ai_assistant_benchmark import run_all_benchmarks
from test_agent import SETTINGS, completion
from test_review_workbench import IDENTITY, WorkbenchFixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.database import close_database_pools
from data_analysis_agent.core.llm_gateway import chat_completion, reset_llm_gateway_stats, track_llm_usage
from data_analysis_agent.jobs.events import ensure_pg_task_listener, pg_task_listener_active, stop_pg_task_listeners
from data_analysis_agent.jobs.store import (
    cancel_task,
    claim_task,
    enqueue,
    finish_task,
    init_tasks,
    task_observability_summary,
    worker_started,
)
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.loader import DATASET_VERSION, SOURCE
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import init_database


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    event_name = "message"
    data_lines: list[str] = []
    for line in raw.splitlines():
        if not line:
            if data_lines:
                events.append((event_name, json.loads("\n".join(data_lines))))
                event_name = "message"
                data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    return events


class P14ObservabilityAndEvalTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        init_monitoring(self.db)
        self.task_db = self.root / "tasks.sqlite3"
        init_tasks(self.task_db, self.db)
        self.import_root = self.root / "imports"
        self.import_root.mkdir()
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "LLM_MODEL_ID=demo\nLLM_API_KEY=sk-secret-do-not-leak\nLLM_BASE_URL=https://offline.invalid/v1\n",
            encoding="utf-8",
        )
        self.base_scope = {
            **IDENTITY,
            "product_ids": ["P1"],
            "source": SOURCE,
            "dataset_version": DATASET_VERSION,
            "date_from": "2023-01-01",
            "date_to": "2023-01-06",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
        }
        worker_started(self.task_db, "worker-p14")
        reset_llm_gateway_stats()

    def test_track_llm_usage_is_isolated_across_concurrent_threads(self):
        def fake_handler(request):
            body = json.loads(request.content)
            prompt = body["messages"][-1]["content"]
            tokens = 100 if prompt == "task-a" else 250
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-iso",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "demo",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": tokens, "completion_tokens": 20, "total_tokens": tokens + 20},
                },
            )

        def worker_call(tag: str, role: str):
            def factory(**kwargs):
                return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(fake_handler)))

            with track_llm_usage() as tracker:
                chat_completion(
                    SETTINGS,
                    messages=[{"role": "user", "content": tag}],
                    role=role,
                    pooled=False,
                    client_factory=factory,
                )
            return tracker.snapshot()

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_a = pool.submit(worker_call, "task-a", "intent")
            f_b = pool.submit(worker_call, "task-b", "answer")
            snap_a = f_a.result()
            snap_b = f_b.result()

        self.assertEqual(snap_a["total_tokens"], 120)
        self.assertEqual(snap_a["by_role"]["intent"]["calls"], 1)
        self.assertNotIn("answer", snap_a["by_role"])
        self.assertEqual(snap_b["total_tokens"], 270)
        self.assertEqual(snap_b["by_role"]["answer"]["calls"], 1)
        self.assertNotIn("intent", snap_b["by_role"])

    def test_run_review_agent_attributes_per_task_llm_telemetry_and_preserves_zero_llm_contract(self):
        # 1. 零模型规则路径：llm_telemetry.calls == 0 且 usage 保持 None
        rule_result = run_review_agent(
            self.db,
            "这个商品主要有哪些问题？",
            scope=self.base_scope,
            settings=replace(SETTINGS, semantic_parser_enabled=False),
        )
        self.assertEqual(rule_result["status"], "ok")
        self.assertIsNone(rule_result["usage"])
        self.assertEqual(rule_result["llm_telemetry"]["calls"], 0)
        self.assertEqual(rule_result["llm_telemetry"]["total_tokens"], 0)

        # 2. 经由 _llm_intent 调用的模型 Token 归因到单任务 llm_telemetry 与 usage
        def intent_handler(request):
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-intent",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "demo",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"query_type": "quality", "needs_clarification": False}),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 180, "completion_tokens": 35, "total_tokens": 215},
                },
            )

        def fake_client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(intent_handler)))

        with patch("openai.OpenAI", side_effect=fake_client):
            llm_result = run_review_agent(
                self.db,
                "这批评论的数据整体健康状况怎么样？",
                scope=self.base_scope,
                settings=SETTINGS,
            )
        self.assertEqual(llm_result["status"], "ok")
        self.assertEqual(llm_result["llm_telemetry"]["calls"], 1)
        self.assertEqual(llm_result["llm_telemetry"]["total_tokens"], 215)
        self.assertEqual(llm_result["llm_telemetry"]["by_role"]["intent"]["total_tokens"], 215)
        self.assertEqual(llm_result["usage"]["total_tokens"], 215)

    def test_chat_stream_done_event_includes_duration_and_usage(self):
        def fake_stream(request):
            sse = (
                'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"demo",'
                '"choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
                'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"demo",'
                '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"demo",'
                '"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":12,"total_tokens":52}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        def fake_client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(fake_stream)))

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with patch("openai.OpenAI", side_effect=fake_client), TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "你好"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("x-request-id"))
        events = _parse_sse(resp.text)
        done_payload = next(payload for name, payload in events if name == "done")
        self.assertEqual(done_payload["answer"], "你好")
        self.assertIn("duration_ms", done_payload)
        self.assertEqual(done_payload["usage"]["total_tokens"], 52)

    def test_observability_summary_request_id_secret_safety_and_public_isolation(self):
        # 制造 1 个成功分析任务、1 个失败任务与 1 个取消任务
        t_ok = enqueue(self.task_db, "analysis", {**self.base_scope, "question": "Q1", "conversation_id": "c1"})["task"]["task_id"]
        claim_task(self.task_db, "worker-p14")
        finish_task(self.task_db, t_ok, "worker-p14", status="succeeded", result={"agent": True, "status": "ok"})

        t_fail = enqueue(self.task_db, "analysis", {**self.base_scope, "question": "Q2", "conversation_id": "c1"})["task"]["task_id"]
        claim_task(self.task_db, "worker-p14")
        finish_task(self.task_db, t_fail, "worker-p14", status="failed", error={"code": "execution_error", "message": "boom"})

        t_cancel = enqueue(self.task_db, "analysis", {**self.base_scope, "question": "Q3", "conversation_id": "c1"})["task"]["task_id"]
        cancel_task(self.task_db, t_cancel)

        summary_direct = task_observability_summary(self.task_db)
        self.assertEqual(summary_direct["counts_by_status"]["succeeded"], 1)
        self.assertEqual(summary_direct["counts_by_status"]["failed"], 2)
        self.assertEqual(summary_direct["recent_cancelled_count"], 1)
        # 取消任务不计入异常失败率分子：1 / 3 = 0.3333
        self.assertAlmostEqual(summary_direct["recent_failure_rate"], 1 / 3, places=3)
        self.assertEqual(summary_direct["analysis_duration_ms"]["count"], 2)

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            r_health = client.get("/api/v1/health")
            self.assertEqual(r_health.status_code, 200)
            self.assertTrue(r_health.headers.get("x-request-id"))
            self.assertIn("pg_listener_active", r_health.json()["result"])

            r_obs = client.get("/api/v1/observability/summary")
            self.assertEqual(r_obs.status_code, 200)
            self.assertTrue(r_obs.headers.get("x-request-id"))
            body_text = r_obs.text
            # 严禁泄露密钥或本地文件系统路径
            self.assertNotIn("sk-secret-do-not-leak", body_text)
            payload = r_obs.json()["result"]
            self.assertIn("llm_gateway", payload)
            self.assertIn("events", payload)
            self.assertIn("embedding_process", payload)
            self.assertIn("reranker_cache", payload)
            self.assertEqual(payload["tasks"]["recent_terminal_count"], 3)

        public_app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, public_only=True)
        with TestClient(public_app, base_url="http://127.0.0.1") as pub:
            self.assertEqual(pub.get("/api/v1/observability/summary").status_code, 404)
            self.assertTrue(pub.get("/api/v1/observability/summary").headers.get("x-request-id"))

    def test_offline_evaluation_benchmark_gates_all_pass(self):
        report = run_all_benchmarks()
        self.assertTrue(report["all_passed"], json.dumps(report, ensure_ascii=False, indent=2))
        self.assertTrue(report["retrieval"]["passed"])
        self.assertTrue(report["routing_and_safety"]["passed"])
        self.assertTrue(report["transport_and_payload"]["passed"])

    def test_quality_and_ratings_followups_after_negative_evidence_do_not_inherit_sentiment_conflict(self):
        from data_analysis_agent.jobs.analysis_context import load_analysis_context

        rules_only = replace(SETTINGS, semantic_parser_enabled=False)
        first = run_review_agent(self.db, "给我看 3 条差评原文。", scope=self.base_scope, settings=rules_only)
        self.assertEqual(first["status"], "ok")
        t1 = enqueue(
            self.task_db,
            "analysis",
            {**self.base_scope, "question": "给我看 3 条差评原文。", "conversation_id": "conv-q-follow", "selection_scope": self.base_scope},
        )["task"]["task_id"]
        claim_task(self.task_db, "worker-p14")
        finish_task(self.task_db, t1, "worker-p14", status="succeeded", result=first)

        ctx = load_analysis_context(
            self.task_db,
            {**self.base_scope, "conversation_id": "conv-q-follow", "context_task_id": t1, "selection_scope": self.base_scope},
        )
        for followup_q, expected_intent in (("那覆盖率呢？", "quality"), ("那星级分布呢？", "ratings")):
            with self.subTest(question=followup_q):
                res = run_review_agent(
                    self.db,
                    followup_q,
                    scope=self.base_scope,
                    settings=rules_only,
                    conversation_context=ctx,
                )
                self.assertEqual(res["status"], "ok", res.get("error"))
                self.assertEqual(res["analysis_intent"], expected_intent)
                self.assertEqual(res["total_model_calls"], 0)


@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_ADMIN_URL"),
    "set TEST_POSTGRES_ADMIN_URL to run live PostgreSQL observability tests",
)
class PostgresObservabilityLiveTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
        self.db_name = f"p1_4_obs_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.db_name}"')
        parts = urlsplit(self.admin_url)
        self.target = urlunsplit((parts.scheme, parts.netloc, "/" + self.db_name, parts.query, ""))
        self.addCleanup(self._drop_database)

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.import_root = self.root / "imports"
        self.import_root.mkdir()
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n",
            encoding="utf-8",
        )
        init_database(self.target)
        init_labeling(self.target)
        init_workbench(self.target)
        init_monitoring(self.target)
        init_tasks(self.target, self.target)

    def _drop_database(self):
        stop_pg_task_listeners(self.target)
        close_database_pools(self.target)
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{self.db_name}"')

    def test_live_pg_observability_summary_and_health_listener_status(self):
        app = create_app(self.target, self.target, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            # 等待后台 PG LISTEN 线程连接就绪
            for _ in range(30):
                if pg_task_listener_active(self.target):
                    break
                import time
                time.sleep(0.05)

            health = client.get("/api/v1/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["result"]["pg_listener_active"])

            obs = client.get("/api/v1/observability/summary")
            self.assertEqual(obs.status_code, 200)
            body = obs.json()["result"]
            self.assertTrue(body["events"]["pg_listener_configured"])
            self.assertTrue(body["events"]["pg_listener_active"])
            self.assertEqual(body["tasks"]["recent_terminal_count"], 0)


if __name__ == "__main__":
    unittest.main()
