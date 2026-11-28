"""P1-1 传输与协议底座升级回归测试：异步 SSE、Last-Event-ID、PG LISTEN/NOTIFY、细粒度进度与结构化拒答。"""

import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from fastapi.testclient import TestClient
import psycopg

from test_agent import SETTINGS
from test_review_workbench import IDENTITY, WorkbenchFixture
import test_semantic_retrieval as srtests
from data_analysis_agent.agents.review_answers import (
    PublicAnswer,
    read_public_answer,
    unavailable_answer,
)
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import close_database_pools
from data_analysis_agent.jobs.events import (
    PG_TASK_EVENT_CHANNEL,
    _ASYNC_SUBSCRIBERS,
    async_wait_task_update,
    notify_task_update,
    pg_task_listener_active,
    stop_pg_task_listeners,
    task_event_version,
)
from data_analysis_agent.jobs.store import (
    claim_task,
    enqueue,
    finish_task,
    get_task_summary,
    init_tasks,
    update_progress,
    worker_started,
)
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.loader import DATASET_VERSION, SOURCE
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticRetrievalService,
    _scope as semantic_scope,
)
from data_analysis_agent.reviews.store import init_database


def _parse_sse_frames(raw: str) -> list[dict]:
    frames: list[dict] = []
    event_id = None
    event_name = "message"
    data_lines: list[str] = []
    for line in raw.splitlines():
        if not line:
            if data_lines:
                frames.append({
                    "id": event_id,
                    "event": event_name,
                    "data": json.loads("\n".join(data_lines)),
                })
                event_id = None
                event_name = "message"
                data_lines = []
            continue
        if line.startswith("id:"):
            event_id = line.split(":", 1)[1].strip()
        elif line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    return frames


class _EmptyFakeSnapshot:
    embedding_space_id = srtests.SPACE_ID
    text_builder_version = "quote-utf8-v1"

    def __init__(self):
        self.events = []
        self.sources = ()
        self.info = srtests.INFO
        self.scope = None
        self.matches = ()

    def search(self, vector, *, top_k, aspect, sentiment):
        return ()

    def load_sources(self, keys):
        return ()


class _MultiQueryFakeEncoder(srtests.FakeEncoder):
    def embed_texts(self, texts):
        self.events.append("encode")
        self.calls.append(texts)
        return [srtests.VECTOR for _ in texts]


def _stub_semantic_service(_conn, bound_scope: dict):
    return SemanticRetrievalService(
        encoder=_MultiQueryFakeEncoder([]),
        repository=srtests.FakeRepository([], _EmptyFakeSnapshot()),
        scope=bound_scope,
    )


class P11TransportAndProtocolTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        init_monitoring(self.db)
        self.task_db = self.root / "tasks.sqlite3"
        init_tasks(self.task_db, self.db)
        self.import_root = self.root / "imports"
        self.import_root.mkdir()
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n",
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

    def test_async_wait_high_concurrency_wakes_without_blocking_threadpool(self):
        task_id = "high-concurrency-async-task"
        initial_version = task_event_version(task_id)

        async def _exercise_event_bus():
            waiters = [
                asyncio.create_task(async_wait_task_update(task_id, initial_version, timeout=5.0))
                for _ in range(60)
            ]
            await asyncio.sleep(0.03)
            started = time.perf_counter()
            notifier = threading.Thread(target=notify_task_update, args=(task_id,))
            notifier.start()
            versions = await asyncio.gather(*waiters)
            notifier.join(timeout=2)
            elapsed = time.perf_counter() - started
            return versions, elapsed

        versions, elapsed = asyncio.run(_exercise_event_bus())
        self.assertEqual(len(versions), 60)
        self.assertTrue(all(v == initial_version + 1 for v in versions))
        self.assertLess(elapsed, 0.5)
        self.assertNotIn(task_id, _ASYNC_SUBSCRIBERS)

        # 50 个并发 SSE 连接超过默认 40 线程 AnyIO 池大小；因为等待为纯异步，同步 /health 仍可立即响应。
        worker_started(self.task_db, "worker-concurrent")
        queued = enqueue(self.task_db, "analysis", {**self.base_scope, "question": "并发SSE测试"})["task"]
        live_id = queued["task_id"]
        claim_task(self.task_db, "worker-concurrent")
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)

        async def _exercise_50_sse_clients():
            ready = asyncio.Event()
            opened = 0

            def _http_scope(path: str) -> dict:
                return {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": b"",
                    "headers": [(b"host", b"127.0.0.1")],
                    "client": ("127.0.0.1", 50000),
                    "server": ("127.0.0.1", 80),
                }

            async def consume_sse():
                nonlocal opened
                status_code = None
                chunks: list[str] = []
                marked_open = False
                disconnect_event = asyncio.Event()

                async def receive():
                    await disconnect_event.wait()
                    return {"type": "http.disconnect"}

                async def send(message):
                    nonlocal status_code, opened, marked_open
                    if message["type"] == "http.response.start":
                        status_code = message["status"]
                    elif message["type"] == "http.response.body":
                        body = message.get("body", b"").decode("utf-8")
                        if body:
                            chunks.append(body)
                            if not marked_open and "event: progress" in "".join(chunks):
                                marked_open = True
                                opened += 1
                                if opened == 50:
                                    ready.set()

                await app(_http_scope(f"/api/v1/tasks/{live_id}/events"), receive, send)
                assert status_code == 200
                return _parse_sse_frames("".join(chunks))

            async def call_health():
                status_code = None
                chunks: list[bytes] = []

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(message):
                    nonlocal status_code
                    if message["type"] == "http.response.start":
                        status_code = message["status"]
                    elif message["type"] == "http.response.body":
                        chunks.append(message.get("body", b""))

                await app(_http_scope("/api/v1/health"), receive, send)
                return status_code, b"".join(chunks)

            sse_tasks = [asyncio.create_task(consume_sse()) for _ in range(50)]
            await asyncio.wait_for(ready.wait(), timeout=5.0)
            health_started = time.perf_counter()
            health_status, _ = await asyncio.wait_for(call_health(), timeout=2.0)
            health_elapsed = time.perf_counter() - health_started
            finish_task(self.task_db, live_id, "worker-concurrent", status="succeeded", result={"status": "ok"})
            results = await asyncio.wait_for(asyncio.gather(*sse_tasks), timeout=5.0)
            return health_status, health_elapsed, results

        health_status, health_elapsed, results = asyncio.run(_exercise_50_sse_clients())
        self.assertEqual(health_status, 200)
        self.assertLess(health_elapsed, 1.5)
        self.assertEqual(len(results), 50)
        self.assertTrue(all(frames[-1]["event"] == "done" and frames[-1]["data"]["status"] == "succeeded" for frames in results))

    def test_task_events_honors_last_event_id_and_avoids_idle_db_polling_storm(self):
        worker_started(self.task_db, "worker-id")
        queued = enqueue(self.task_db, "analysis", {**self.base_scope, "question": "续传测试"})["task"]
        task_id = queued["task_id"]
        claim_task(self.task_db, "worker-id")
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)

        summary_calls = 0
        real_get_summary = get_task_summary

        def counted_summary(*args, **kwargs):
            nonlocal summary_calls
            summary_calls += 1
            return real_get_summary(*args, **kwargs)

        def drive():
            # 空闲等待 0.65s：旧版 0.5s 轮询会多查数据库；新版在无事件期间不额外查库，唤醒后立即推送。
            time.sleep(0.65)
            update_progress(
                self.task_db,
                task_id,
                "worker-id",
                {"stage": "semantic_retrieving", "completed": 0, "total": 1, "unit": "queries"},
            )
            time.sleep(0.05)
            finish_task(self.task_db, task_id, "worker-id", status="succeeded", result={"status": "ok"})

        with patch("data_analysis_agent.api.routes.tasks.get_task_summary", side_effect=counted_summary):
            with TestClient(app, base_url="http://127.0.0.1") as client:
                driver = threading.Thread(target=drive)
                driver.start()
                try:
                    with client.stream(
                        "GET",
                        f"/api/v1/tasks/{task_id}/events",
                        headers={"Last-Event-ID": "7"},
                    ) as resp:
                        self.assertEqual(resp.status_code, 200)
                        raw = "".join(resp.iter_text())
                finally:
                    driver.join(timeout=5)

        frames = _parse_sse_frames(raw)
        self.assertGreaterEqual(len(frames), 3)
        self.assertEqual([frame["id"] for frame in frames[:3]], ["8", "9", "10"])
        self.assertEqual(frames[0]["event"], "progress")
        self.assertEqual(frames[1]["event"], "progress")
        self.assertEqual(frames[1]["data"]["progress"]["stage"], "semantic_retrieving")
        self.assertEqual(frames[-1]["event"], "done")
        # 初始 1 次 + progress 唤醒 1 次 + done 唤醒 1 次（最多 3 次，绝不触发 0.5s 轮询风暴）
        self.assertLessEqual(summary_calls, 3)

        # 非法 Last-Event-ID 安全退回从 1 开始
        with TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.get(f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "not-a-number"})
        invalid_frames = _parse_sse_frames(resp.text)
        self.assertEqual(invalid_frames[0]["id"], "1")
        self.assertEqual(invalid_frames[0]["event"], "done")

    def test_review_agent_emits_granular_progress_stages(self):
        sql_stages: list[tuple[str, int | None, int | None]] = []
        result = run_review_agent(
            self.db,
            "好评主要集中在哪些方面？",
            scope=self.base_scope,
            settings=SETTINGS,
            progress=lambda value: sql_stages.append((value.get("stage"), value.get("completed"), value.get("total"))),
        )
        self.assertEqual(result["status"], "ok")
        stage_names = [row[0] for row in sql_stages]
        self.assertIn("intent_resolving", stage_names)
        self.assertIn("validating", stage_names)
        self.assertIn("answer_generating", stage_names)
        self.assertLess(stage_names.index("intent_resolving"), stage_names.index("validating"))
        self.assertLess(stage_names.index("validating"), stage_names.index("answer_generating"))
        self.assertIn(("validating", 0, 1), sql_stages)
        self.assertIn(("validating", 1, 1), sql_stages)

        semantic_stages: list[tuple[str, int | None, int | None]] = []
        semantic_result = run_review_agent(
            self.db,
            "有没有制冷很慢的反馈？",
            scope=self.base_scope,
            settings=SETTINGS,
            progress=lambda value: semantic_stages.append((value.get("stage"), value.get("completed"), value.get("total"))),
            semantic_service_factory=_stub_semantic_service,
        )
        self.assertIn(semantic_result["status"], ("ok", "unsupported"))
        sem_names = [row[0] for row in semantic_stages]
        self.assertIn("intent_resolving", sem_names)
        self.assertIn("semantic_retrieving", sem_names)
        self.assertLess(sem_names.index("intent_resolving"), sem_names.index("semantic_retrieving"))
        self.assertIn(("semantic_retrieving", 0, 1), semantic_stages)
        self.assertIn(("semantic_retrieving", 1, 1), semantic_stages)

    def test_structured_refusal_contract_and_historical_backward_compatibility(self):
        cases = [
            ("预测下个月空调销量", "forecast", self.base_scope),
            ("为什么空调制冷很慢？", "cause_check", self.base_scope),
            ("制冷差评有多少条？", "subtopic_count", self.base_scope),
            ("物流好评占正向观点评论和占成功标注评论的比例各是多少？", "denominator_conflict", self.base_scope),
            ("这款空调好评主要有哪些？", "missing_scope", {**self.base_scope, "product_ids": ["P1", "P2"]}),
        ]
        for question, expected_code, scope in cases:
            with self.subTest(question=question):
                res = run_review_agent(self.db, question, scope=scope, settings=SETTINGS)
                self.assertEqual(res["status"], "unsupported")
                public_answer = res["public_answer"]
                PublicAnswer.model_validate(public_answer)
                self.assertEqual(public_answer["status"], "unsupported")
                self.assertEqual(public_answer["unsupported_code"], expected_code)
                self.assertGreaterEqual(len(public_answer["suggested_questions"]), 1)
                self.assertEqual(res["unsupported_code"], expected_code)

        # 旧版持久化 PublicAnswer JSON（不含 P1-1 新字段）保持完全兼容。
        legacy_answer = {
            "schema_version": "1.0",
            "status": "unsupported",
            "summary": "历史拒答文本",
            "highlights": [],
            "metrics": [],
            "sample_note": "",
            "evidence_refs": [],
            "caveats": [],
            "body_caveats": None,
            "generation_status": "unavailable",
            "source_result_ids": [],
            "scope_summary": [],
            "details_available": False,
            "insights": [],
            "evidence_relations": [],
        }
        validated_legacy = PublicAnswer.model_validate(legacy_answer)
        self.assertIsNone(validated_legacy.unsupported_code)
        self.assertEqual(validated_legacy.suggested_questions, [])
        self.assertEqual(validated_legacy.clarification_choices, [])

        # 历史未落库 unsupported_code 的任务通过 read_public_answer 只读恢复结构化编码与建议。
        rebuilt = read_public_answer({
            "status": "succeeded",
            "parameters": {"question": "预测下个月销量"},
            "result": {
                "agent": True,
                "status": "unsupported",
                "question": "预测下个月销量",
                "answer": "当前评论助手不支持销量或销售额预测。",
            },
        })
        self.assertEqual(rebuilt["status"], "unsupported")
        self.assertEqual(rebuilt["unsupported_code"], "forecast")
        self.assertTrue(rebuilt["suggested_questions"])

        # 显式 clarification_choices 透传与校验。
        with_choices = unavailable_answer(
            "unsupported",
            "请先选择要分析的商品或维度。",
            unsupported_code="clarification_needed",
            clarification_choices=[
                {"label": "看物流差评", "question": "物流差评具体体现在哪里？"},
                {"label": "看客服差评", "question": "客服差评具体体现在哪里？"},
            ],
        )
        self.assertEqual(with_choices["unsupported_code"], "clarification_needed")
        self.assertEqual(len(with_choices["clarification_choices"]), 2)


@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_ADMIN_URL"),
    "set TEST_POSTGRES_ADMIN_URL to run live PostgreSQL LISTEN/NOTIFY SSE tests",
)
class PostgresTaskListenNotifyTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
        self.db_name = f"p1_1_listen_{uuid.uuid4().hex[:10]}"
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

    def test_cross_process_pg_notify_wakes_sse_without_local_event(self):
        params = {
            "model": "demo",
            "base_url": "https://offline.invalid/v1",
            "prompt_version": "v1",
            "product_ids": ["P1"],
            "source": "reviews_csv",
            "dataset_version": "reviews-csv",
            "date_from": "2023-01-01",
            "date_to": "2023-03-31",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
            "question": "跨进程 PG NOTIFY 测试",
        }
        worker_started(self.target, "pg-worker-remote")
        task_id = enqueue(self.target, "analysis", params)["task"]["task_id"]
        claim_task(self.target, "pg-worker-remote")

        app = create_app(self.target, self.target, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            deadline = time.monotonic() + 5.0
            while not pg_task_listener_active(self.target) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(pg_task_listener_active(self.target), "PG LISTEN background listener did not become active")

            # 使用独立原始 psycopg 连接模拟跨进程 Worker：直接写库并 pg_notify，绝不调用进程内 notify_task_update。
            def external_worker_write():
                time.sleep(0.15)
                with psycopg.connect(self.target, autocommit=False) as ext:
                    ext.execute(
                        "UPDATE tasks SET progress_json=%s, updated_at=%s WHERE task_id=%s",
                        (
                            json.dumps({"stage": "validating", "completed": 0, "total": 1, "unit": "checks"}),
                            utc_now(),
                            task_id,
                        ),
                    )
                    ext.execute("SELECT pg_notify(%s, %s)", (PG_TASK_EVENT_CHANNEL, task_id))
                    ext.commit()
                time.sleep(0.15)
                with psycopg.connect(self.target, autocommit=False) as ext:
                    now = utc_now()
                    ext.execute(
                        "UPDATE tasks SET status='succeeded', result_json=%s, finished_at=%s, updated_at=%s WHERE task_id=%s",
                        (json.dumps({"status": "ok"}), now, now, task_id),
                    )
                    ext.execute("SELECT pg_notify(%s, %s)", (PG_TASK_EVENT_CHANNEL, task_id))
                    ext.commit()

            started = time.perf_counter()
            writer = threading.Thread(target=external_worker_write)
            writer.start()
            try:
                with client.stream("GET", f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "2"}) as resp:
                    self.assertEqual(resp.status_code, 200)
                    raw = "".join(resp.iter_text())
            finally:
                writer.join(timeout=5)
            elapsed = time.perf_counter() - started

        # 退出 lifespan 后监听线程应自动释放连接。
        self.assertFalse(pg_task_listener_active(self.target))
        frames = _parse_sse_frames(raw)
        self.assertGreaterEqual(len(frames), 3)
        self.assertEqual(frames[0]["id"], "3")
        self.assertEqual(frames[1]["data"]["progress"]["stage"], "validating")
        self.assertEqual(frames[-1]["event"], "done")
        self.assertEqual(frames[-1]["data"]["status"], "succeeded")
        # 远低于 15s keepalive 间隔，证明由 PG LISTEN/NOTIFY 实时唤醒。
        self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
