"""P0 稳定性修复专项回归测试：
1. 向量同步突破 300 条上限的多批增量推进与陈旧快照优先补齐；
2. pgvector HNSW 迭代扫描配置与无 iterative_scan 时的精确兜底防饥饿；
3. SQLite → PostgreSQL 迁移包含 task_audit_blobs；
4. 嵌入常驻子进程可恢复输入错误保活与熔断冷却半开恢复；
5. TaskWorker 心跳/通道瞬时数据库异常退避恢复、任务取消接口与优雅中断；
6. LLM Gateway 流式调用默认携带 stream_options.include_usage 及 400 自动降级兼容。
"""

from __future__ import annotations

from dataclasses import asdict, replace
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit, urlunsplit
import uuid

from fastapi.testclient import TestClient
import httpx
from openai import OpenAI as RealOpenAI
import psycopg
from psycopg import sql

from data_analysis_agent.api.app import create_app
from data_analysis_agent.cli import migrate as migrate_cli
from data_analysis_agent.core.database import DatabaseError, close_database_pools
from data_analysis_agent.core.llm_gateway import (
    chat_completion_stream,
    close_llm_clients,
    llm_gateway_stats,
    reset_llm_gateway_stats,
)
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs.analysis_state import has_saved_integrity
from data_analysis_agent.jobs.store import (
    TaskConflict,
    cancel_task,
    claim_task,
    enqueue,
    finish_task,
    get_task,
    get_task_audit,
    init_tasks,
    worker_started,
)
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.embedding_process import (
    PersistentEmbeddingProcess,
    close_embedding_processes,
)
from data_analysis_agent.reviews import embedding_store as store
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling, save_annotation
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import RagCorpus
from data_analysis_agent.reviews.store import init_database
from test_agent import FAKE_KEY, SETTINGS
import test_semantic_retrieval as srtests
from test_review_workbench import IDENTITY, WorkbenchFixture, opinion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import persistent_embedding_worker as pew  # noqa: E402


UNIT = [1.0] + [0.0] * 1023


class EmbeddingSyncPaginationAndHnswTests(unittest.TestCase):
    def _synthetic_corpus(self, review_count: int = 250, opinions_per_review: int = 2) -> RagCorpus:
        base = srtests.source(1)
        template_doc = base.opinions[0]
        template_state = base.state
        docs = []
        reviews = []
        idx = 0
        for rid in range(1, review_count + 1):
            r_docs = []
            for j in range(opinions_per_review):
                idx += 1
                doc = replace(
                    template_doc,
                    review_id=rid,
                    annotation_id=1000 + rid,
                    model_opinion_id=idx,
                    stable_versioned_opinion_key=f"syn-key:{rid}:{j}",
                    line_number=rid,
                )
                r_docs.append(doc)
                docs.append(doc)
            reviews.append(
                replace(
                    template_state,
                    review_id=rid,
                    annotation_id=1000 + rid,
                    opinion_count=opinions_per_review,
                    effective_snapshot_sha256=content_sha256([rid, opinions_per_review]),
                )
            )
        return RagCorpus(
            srtests.OPTIONS,
            template_doc.label_profile_key,
            tuple(docs),
            tuple(reviews),
        )

    def test_select_uncovered_advances_across_batches_beyond_300_opinions(self):
        corpus = self._synthetic_corpus(review_count=250, opinions_per_review=2)  # 500 opinions
        covered: set[str] = set()

        batch1 = store._select_uncovered(corpus.documents, 200, covered)
        self.assertEqual(len(batch1), 200)
        self.assertEqual([d.review_id for d in batch1[:2]], [1, 1])
        self.assertEqual(batch1[-1].review_id, 100)
        covered.update(d.stable_versioned_opinion_key for d in batch1)

        # 第二批跳过已覆盖的前 100 条评论，推进至第 101..200 条评论（观点 201..400）。
        batch2 = store._select_uncovered(corpus.documents, 200, covered)
        self.assertEqual(len(batch2), 200)
        self.assertEqual(batch2[0].review_id, 101)
        self.assertEqual(batch2[-1].review_id, 200)
        covered.update(d.stable_versioned_opinion_key for d in batch2)

        # 第三批推进至第 201..250 条评论（观点 401..500）。
        batch3 = store._select_uncovered(corpus.documents, 200, covered)
        self.assertEqual(len(batch3), 100)
        self.assertEqual(batch3[0].review_id, 201)
        self.assertEqual(batch3[-1].review_id, 250)
        covered.update(d.stable_versioned_opinion_key for d in batch3)

        # 全部已覆盖后回退为幂等检查（仍遵循 limit 上限）。
        batch4 = store._select_uncovered(corpus.documents, 200, covered)
        self.assertEqual(len(batch4), 200)
        self.assertEqual(batch4[0].review_id, 1)

        # 若第 2 条评论的快照失效（任一观点不在 covered），下一批会优先选中完整评论组修复。
        covered.remove("syn-key:2:0")
        repair = store._select_uncovered(corpus.documents, 200, covered)
        self.assertEqual([d.review_id for d in repair], [2, 2])

    def test_hnsw_configures_iterative_scan_and_falls_back_when_unsupported(self):
        self.enterContext(patch("data_analysis_agent.reviews.analytics.check_labeling_schema"))
        sources = [srtests.source(1), srtests.source(2)]
        corpus = RagCorpus(
            srtests.analysis_scope(**srtests.OPTIONS)[3],
            sources[0].opinions[0].label_profile_key,
            tuple(d for s in sources for d in s.opinions),
            tuple(s.state for s in sources),
        )
        valid_rows = [
            dict(
                asdict(sources[0].opinions[0]),
                effective_snapshot_sha256=sources[0].state.effective_snapshot_sha256,
                distance=0.08,
            ),
            dict(
                asdict(sources[1].opinions[0]),
                effective_snapshot_sha256=sources[1].state.effective_snapshot_sha256,
                distance=0.12,
            ),
        ]

        class Rows:
            def __init__(self, data):
                self._data = data

            def fetchall(self):
                return self._data

        class RealLikePG(srtests.FakePG):
            def __init__(self, *, pg_settings_rows, bounded_rows, unbounded_rows):
                super().__init__(active=True)
                self.raw = object()  # 标识为真实 PG 连接
                self.pg_settings_rows = pg_settings_rows
                self.bounded_rows = bounded_rows
                self.unbounded_rows = unbounded_rows
                self.set_configs = []
                self.vector_queries = []

            def execute(self, query, parameters=None):
                if "FROM pg_settings" in query:
                    return Rows(self.pg_settings_rows)
                if "set_config(" in query:
                    self.set_configs.append((query, parameters))
                    return Rows([("ok",)])
                if "embedding <=>" in query:
                    self.vector_queries.append(query)
                    if "LIMIT ?" in query:
                        return Rows(self.bounded_rows)
                    return Rows(self.unbounded_rows)
                return super().execute(query, parameters)

        # 场景 A：pgvector 0.8+ 支持 hnsw.iterative_scan，配置 strict_order 与 ef_search，无需额外全扫描。
        conn_v8 = RealLikePG(
            pg_settings_rows=[("hnsw.ef_search", "40"), ("hnsw.iterative_scan", "off")],
            bounded_rows=[valid_rows[0]],
            unbounded_rows=valid_rows,
        )
        with patch("data_analysis_agent.reviews.embedding_store.read_effective_corpus", return_value=corpus):
            matches = store.search_effective_opinions(
                conn_v8, srtests.VECTOR, options=srtests.OPTIONS, embedding_space_id=store.SPACE_ID, top_k=5
            )
        self.assertEqual([m.document.review_id for m in matches], [1])
        self.assertEqual(len(conn_v8.vector_queries), 1)
        self.assertTrue(any("hnsw.ef_search" in q and p == ("64",) for q, p in conn_v8.set_configs))
        self.assertTrue(any("hnsw.iterative_scan" in q for q, _ in conn_v8.set_configs))

        # 场景 B：旧版 pgvector 不支持 hnsw.iterative_scan，且 bounded HNSW 过滤返回空集 -> 自动兜底精确全扫描。
        conn_legacy = RealLikePG(
            pg_settings_rows=[("hnsw.ef_search", "40")],
            bounded_rows=[],
            unbounded_rows=valid_rows,
        )
        with patch("data_analysis_agent.reviews.embedding_store.read_effective_corpus", return_value=corpus):
            recovered = store.search_effective_opinions(
                conn_legacy, srtests.VECTOR, options=srtests.OPTIONS, embedding_space_id=store.SPACE_ID, top_k=5
            )
        self.assertEqual([m.document.review_id for m in recovered], [1, 2])
        self.assertEqual(len(conn_legacy.vector_queries), 2)
        self.assertNotIn("LIMIT ?", conn_legacy.vector_queries[-1])


class MigrateTaskAuditBlobsTests(unittest.TestCase):
    def test_task_tables_include_task_audit_blobs(self):
        self.assertEqual(migrate_cli.TASK_TABLES, ("tasks", "task_audit_blobs"))


class EmbeddingWorkerResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.addCleanup(close_embedding_processes)

    def test_worker_serve_keeps_process_alive_on_recoverable_value_error(self):
        class StubModel:
            space = store.SPACE

            def embed_texts(self, texts):
                if any("超长" in t for t in texts):
                    raise ValueError("输入超出 max_input_tokens，拒绝隐式截断。")
                return [UNIT[:] for _ in texts]

        lines = "\n".join([
            json.dumps({"request_id": "1", "operation": "initialize", "cache_dir": str(self.directory)}),
            json.dumps({"request_id": "2", "operation": "encode", "texts": ["超长文本"]}),
            json.dumps({"request_id": "3", "operation": "encode", "texts": ["正常文本"]}),
        ]) + "\n"
        output = io.StringIO()
        code = pew.serve(io.StringIO(lines), output, lambda _cache: StubModel())
        self.assertEqual(code, 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["status"], "ready")
        self.assertEqual(responses[1]["error"], "query_input_rejected")
        self.assertTrue(responses[1]["recoverable"])
        self.assertEqual(len(responses[2]["vectors"]), 1)

    def test_persistent_process_survives_recoverable_error_and_recovers_after_cooldown(self):
        worker_script = self.directory / "resilient_worker.py"
        worker_script.write_text(
            f"""import os, sys
sys.path.insert(0, {str(ROOT / 'src')!r})
sys.path.insert(0, {str(ROOT / 'scripts')!r})
from persistent_embedding_worker import serve
from data_analysis_agent.reviews.embedding_store import SPACE

class Encoder:
    space = SPACE
    def embed_texts(self, texts):
        if 'overlong' in texts:
            raise ValueError('输入超出 max_input_tokens')
        if 'crash' in texts:
            os._exit(9)
        return [[1.0] + [0.0] * 1023 for _ in texts]

raise SystemExit(serve(sys.stdin, sys.stdout, lambda _c: Encoder()))
""",
            encoding="utf-8",
        )
        client = PersistentEmbeddingProcess(
            sys.executable,
            self.directory,
            timeout=10,
            worker=worker_script,
            max_restarts=1,
            restart_backoff_seconds=0.01,
            restart_cooldown_seconds=0.15,
        )
        self.addCleanup(client.close)

        first = client.embed_texts(["正常查询"])
        first_pid = client.last_metrics["process_id"]

        # 单次超长查询抛出可恢复错误码，但子进程不退出、不计入故障次数、不重建模型。
        with self.assertRaisesRegex(store.EmbeddingStoreError, "local_embedding_query_rejected"):
            client.embed_texts(["overlong"])
        self.assertFalse(client.failed)
        self.assertEqual(client.consecutive_failures, 0)
        self.assertEqual(client.restart_count, 0)

        second = client.embed_texts(["正常查询"])
        self.assertEqual(second, first)
        self.assertEqual(client.last_metrics["process_id"], first_pid)

        # 连续崩溃超过 max_restarts 后立即锁定，但冷却时间过后可半开探测并自动恢复。
        for _ in range(2):
            with self.assertRaises(store.EmbeddingStoreError):
                client.embed_texts(["crash"])
        restarts_when_latched = client.restart_count
        with self.assertRaises(store.EmbeddingStoreError):
            client.embed_texts(["正常查询"])
        self.assertEqual(client.restart_count, restarts_when_latched)

        time.sleep(0.18)
        healed = client.embed_texts(["正常查询"])
        self.assertEqual(healed, first)
        self.assertFalse(client.failed)
        self.assertEqual(client.consecutive_failures, 0)
        self.assertEqual(client.restart_count, restarts_when_latched + 1)


class WorkerResilienceAndTaskCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "business.sqlite3"
        self.task_db = self.root / "tasks.sqlite3"
        self.import_root = self.root / "imports"
        self.import_root.mkdir()
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            f"LLM_MODEL_ID={SETTINGS.model}\nLLM_API_KEY={FAKE_KEY}\nLLM_BASE_URL={SETTINGS.base_url}\n",
            encoding="utf-8",
        )
        init_database(self.db)
        init_labeling(self.db)
        init_workbench(self.db)
        init_monitoring(self.db)
        init_tasks(self.task_db, self.db)
        self.analysis_params = {
            "model": SETTINGS.model,
            "base_url": SETTINGS.base_url,
            "prompt_version": "v1",
            "product_ids": ["P1"],
            "source": "reviews_csv",
            "dataset_version": "reviews-csv",
            "date_from": "2023-01-01",
            "date_to": "2023-01-31",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
        }

    def test_cancel_task_api_store_and_retry_flow(self):
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        task_id = enqueue(self.task_db, "analysis", self.analysis_params)["task"]["task_id"]

        with TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post(f"/api/v1/tasks/{task_id}/cancel")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()["result"]
            self.assertTrue(body["cancelled"])
            self.assertFalse(body["already_terminal"])
            self.assertEqual(body["task"]["status"], "failed")
            self.assertEqual(body["task"]["error"]["code"], "task_cancelled")

            # 幂等重复取消同一任务仍返回 200。
            again = client.post(f"/api/v1/tasks/{task_id}/cancel")
            self.assertEqual(again.status_code, 200)
            self.assertTrue(again.json()["result"]["already_terminal"])

            # 已成功任务禁止取消（409）。
            done_id = enqueue(self.task_db, "analysis", {**self.analysis_params, "date_to": "2023-01-20"})["task"]["task_id"]
            worker_started(self.task_db, "w-test")
            claim_task(self.task_db, "w-test")
            finish_task(self.task_db, done_id, "w-test", status="succeeded", result={"run_id": "r1"})
            conflict = client.post(f"/api/v1/tasks/{done_id}/cancel")
            self.assertEqual(conflict.status_code, 409)

    def test_worker_handles_running_task_cancel_and_keeps_serving_next_task(self):
        entered = threading.Event()
        cancelled_event = threading.Event()

        def slow_replay(_db, *, progress=None, **_kwargs):
            entered.set()
            cancelled_event.wait(timeout=5)
            # 取消后的下一次进度上报应抛出 TaskCancelled 并安全退出。
            progress({"stage": "replay", "completed": 1, "total": 2, "unit": "months"})
            return {"status": "ok"}

        worker = TaskWorker(
            self.db,
            self.task_db,
            import_root=self.import_root,
            env_file=self.env_file,
            analysis_concurrency=1,
            retry_backoff_seconds=0.01,
        )
        with patch("data_analysis_agent.jobs.worker.replay_monthly", side_effect=slow_replay), patch(
            "data_analysis_agent.jobs.worker.run_analysis", return_value={"run_id": "ok-after-cancel", "status": "ok"}
        ):
            worker.start()
            self.addCleanup(worker.stop)

            t1 = enqueue(
                self.task_db,
                "replay",
                {
                    "model": SETTINGS.model,
                    "base_url": SETTINGS.base_url,
                    "prompt_version": "v1",
                    "product_ids": ["P1"],
                    "source": "reviews_csv",
                    "dataset_version": "reviews-csv",
                    "month_from": "2023-01",
                    "month_to": "2023-02",
                    "baseline_months": 1,
                },
            )["task"]["task_id"]
            self.assertTrue(entered.wait(timeout=5))
            cancel_task(self.task_db, t1)
            cancelled_event.set()

            # 取消正在执行的任务后，worker 不应停止，后续分析任务仍正常完成。
            t2 = enqueue(self.task_db, "analysis", self.analysis_params)["task"]["task_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if get_task(self.task_db, t2)["status"] == "succeeded":
                    break
                time.sleep(0.05)

            self.assertEqual(get_task(self.task_db, t1)["error"]["code"], "task_cancelled")
            self.assertEqual(get_task(self.task_db, t2)["status"], "succeeded")
            self.assertFalse(worker.stop_requested.is_set())

    def test_worker_survives_transient_heartbeat_and_lane_db_errors(self):
        hb_calls = {"count": 0}
        real_heartbeat = __import__("data_analysis_agent.jobs.store", fromlist=["heartbeat"]).heartbeat

        def flaky_heartbeat(path, worker_id, *, stopped=False):
            if not stopped:
                hb_calls["count"] += 1
                if hb_calls["count"] <= 2:
                    raise DatabaseError("transient heartbeat failure")
            return real_heartbeat(path, worker_id, stopped=stopped)

        claim_calls = {"count": 0}
        real_claim = claim_task

        def flaky_claim(path, worker_id, *, kinds=None, exclude_kinds=None):
            claim_calls["count"] += 1
            if claim_calls["count"] <= 2:
                raise DatabaseError("transient claim failure")
            return real_claim(path, worker_id, kinds=kinds, exclude_kinds=exclude_kinds)

        worker = TaskWorker(
            self.db,
            self.task_db,
            import_root=self.import_root,
            env_file=self.env_file,
            analysis_concurrency=1,
            heartbeat_interval_seconds=0.03,
            max_heartbeat_failures=5,
            max_lane_db_failures=5,
            retry_backoff_seconds=0.01,
        )
        with patch("data_analysis_agent.jobs.worker.heartbeat", side_effect=flaky_heartbeat), patch(
            "data_analysis_agent.jobs.worker.claim_task", side_effect=flaky_claim
        ), patch(
            "data_analysis_agent.jobs.worker.run_analysis", return_value={"run_id": "survived", "status": "ok"}
        ):
            worker.start()
            self.addCleanup(worker.stop)

            task_id = enqueue(self.task_db, "analysis", self.analysis_params)["task"]["task_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if get_task(self.task_db, task_id)["status"] == "succeeded" and hb_calls["count"] >= 3:
                    break
                time.sleep(0.03)

            self.assertGreaterEqual(hb_calls["count"], 3)
            self.assertEqual(get_task(self.task_db, task_id)["status"], "succeeded")
            self.assertFalse(worker.stop_requested.is_set())


class LlmGatewayStreamUsageTests(unittest.TestCase):
    def setUp(self):
        close_llm_clients()
        reset_llm_gateway_stats()
        self.addCleanup(close_llm_clients)
        self.addCleanup(reset_llm_gateway_stats)

    def _sse(self, chunks: list[dict]) -> httpx.Response:
        body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode("utf-8"))

    def test_stream_requests_include_usage_and_falls_back_on_400(self):
        bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "stream_options" in body:
                return httpx.Response(
                    400,
                    json={"error": {"message": "unknown field stream_options", "type": "invalid_request_error"}},
                )
            return self._sse([
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": SETTINGS.model,
                    "choices": [{"index": 0, "delta": {"content": "降级成功"}, "finish_reason": None}],
                },
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": SETTINGS.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ])

        def factory(**kwargs):
            return RealOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        chunks = list(
            chat_completion_stream(
                SETTINGS,
                role="chat",
                messages=[{"role": "user", "content": "hi"}],
                client_factory=factory,
            )
        )
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["stream_options"], {"include_usage": True})
        self.assertNotIn("stream_options", bodies[1])
        self.assertEqual("".join(c.choices[0].delta.content or "" for c in chunks), "降级成功")
        self.assertEqual(llm_gateway_stats()["errors"], 0)


@unittest.skipUnless(os.environ.get("TEST_PGVECTOR_ADMIN_URL"), "set isolated TEST_PGVECTOR_ADMIN_URL")
class PostgresP0StabilityLiveTests(WorkbenchFixture, unittest.TestCase):
    """在独立临时 PostgreSQL + pgvector 库上验证 P0 多批增量同步、HNSW 迭代扫描与审计表迁移。"""

    def setUp(self):
        super().setUp()
        self.sqlite_db = self.db
        self.admin_url = os.environ["TEST_PGVECTOR_ADMIN_URL"]
        self.created_dbs: list[str] = []
        self.addCleanup(self._cleanup_dbs)

    def _new_pg_db(self) -> str:
        name = "rag_vector_test_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        self.created_dbs.append(name)
        return urlunsplit(urlsplit(self.admin_url)._replace(path="/" + name))

    def _cleanup_dbs(self):
        close_database_pools()
        with psycopg.connect(self.admin_url, autocommit=True) as admin:
            for name in reversed(self.created_dbs):
                assert name.startswith("rag_vector_test_")
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def test_live_pg_multi_batch_sync_and_hnsw_iterative_scan(self):
        target = self._new_pg_db()
        migrate_cli.migrate_sqlite(self.sqlite_db, target)
        with psycopg.connect(target, autocommit=True) as c:
            c.execute("CREATE EXTENSION vector")
        store.migrate_embedding_store(target, with_hnsw_index=True)

        # 在隔离 PG 库中为 P1 追加 9 条（共 10 条 × 2 观点 = 20 条观点），并为 P2 插入 15 条干扰评论，构造选择性切片。
        with psycopg.connect(target) as c:
            import_run_id = c.execute("SELECT id FROM import_runs LIMIT 1").fetchone()[0]
            # 先把原 review 1 的 3 条观点替换为 2 条，便于整除断言。
            c.execute("DELETE FROM review_opinions WHERE annotation_id=%s", (self.annotations[1],))
            c.execute("DELETE FROM review_annotations WHERE review_id=1")
            p1_rids = [1]
            c.execute("UPDATE reviews SET text=%s, content_sha256=%s WHERE id=1",
                      ("物流很慢，包装破损。", content_sha256("p1-rev-1")))
            for idx in range(2, 11):
                txt = f"第{idx}条评论：物流很慢，包装破损。"
                sha = content_sha256(f"p1-rev-{idx}")
                rid = c.execute(
                    """INSERT INTO reviews(source,dataset_version,source_record_id,product_id,asin,user_id,title,text,
                       has_text,rating,published_at,fetched_at,raw_json,content_sha256,import_run_id,line_number)
                       VALUES ('amazon_reviews_2023','amazon-reviews-2023',%s,'P1','ASIN-P1',%s,'',%s,
                       1,1.0,'2023-01-02T00:00:00+00:00','2023-01-03T00:00:00+00:00','{}',%s,%s,%s) RETURNING id""",
                    (f"rec-p1-{idx}", f"u-p1-{idx}", txt, sha, import_run_id, 100 + idx),
                ).fetchone()[0]
                p1_rids.append(rid)
            p2_rids = []
            for idx in range(1, 16):
                txt = f"干扰评论{idx}：客服态度很好，使用效果满意。"
                sha = content_sha256(f"p2-rev-{idx}")
                rid = c.execute(
                    """INSERT INTO reviews(source,dataset_version,source_record_id,product_id,asin,user_id,title,text,
                       has_text,rating,published_at,fetched_at,raw_json,content_sha256,import_run_id,line_number)
                       VALUES ('amazon_reviews_2023','amazon-reviews-2023',%s,'P2','ASIN-P2',%s,'',%s,
                       1,5.0,'2023-01-02T00:00:00+00:00','2023-01-03T00:00:00+00:00','{}',%s,%s,%s) RETURNING id""",
                    (f"rec-p2-{idx}", f"u-p2-{idx}", txt, sha, import_run_id, 200 + idx),
                ).fetchone()[0]
                p2_rids.append(rid)

        with psycopg.connect(target) as c:
            p1_rows = c.execute("SELECT id, text, content_sha256 FROM reviews WHERE id = ANY(%s)", (p1_rids,)).fetchall()
            p2_rows = c.execute("SELECT id, text, content_sha256 FROM reviews WHERE id = ANY(%s)", (p2_rids,)).fetchall()

        for rid, txt, sha in p1_rows:
            ops = validate_opinions(
                json.dumps({"opinions": [opinion("logistics", "negative", "物流很慢"), opinion("packaging", "negative", "包装破损")]}),
                txt,
            )
            save_annotation(target, {"id": rid, "text": txt, "content_sha256": sha}, **IDENTITY, status="success", opinions=ops)

        for rid, txt, sha in p2_rows:
            ops = validate_opinions(
                json.dumps({"opinions": [opinion("service", "positive", "客服态度很好"), opinion("effectiveness", "positive", "使用效果满意")]}),
                txt,
            )
            save_annotation(target, {"id": rid, "text": txt, "content_sha256": sha}, **IDENTITY, status="success", opinions=ops)

        def make_vec(text: str) -> list[float]:
            v = [0.0] * 1024
            if "物流" in text:
                v[0], v[1] = 0.98, 0.198997487421324
            elif "包装" in text:
                v[1] = 1.0
            else:
                v[0] = 1.0  # 故意让 P2 向量更接近 [1,0,...] 以测试 HNSW 过滤召回不饥饿
            norm = math.sqrt(sum(x * x for x in v))
            return [x / norm for x in v]

        class DeterministicEncoder:
            def __init__(self):
                self.calls = []

            def encode(self, texts):
                self.calls.append(list(texts))
                return {"space_id": store.SPACE_ID, "vectors": [make_vec(t) for t in texts]}

        encoder = DeterministicEncoder()
        p1_opts = dict(
            **IDENTITY,
            source="amazon_reviews_2023",
            dataset_version="amazon-reviews-2023",
            product_ids=["P1"],
            date_from="2023-01-01",
            date_to="2023-01-06",
        )
        p2_opts = {**p1_opts, "product_ids": ["P2"]}

        # 多批推进同步 P1（20 条观点，limit=8 -> 8 -> 16 -> 20）
        r1 = store.sync_effective_opinions(target, options=p1_opts, encoder=encoder, limit=8, execute=True, encoding_authorized=True)
        self.assertEqual((r1["status"], r1["metrics"]["attempted_count"], r1["metrics"]["indexed_current_after"]), ("partial", 8, 8))

        r2 = store.sync_effective_opinions(target, options=p1_opts, encoder=encoder, limit=8, execute=True, encoding_authorized=True)
        self.assertEqual((r2["status"], r2["metrics"]["attempted_count"], r2["metrics"]["indexed_current_after"]), ("partial", 8, 16))

        r3 = store.sync_effective_opinions(target, options=p1_opts, encoder=encoder, limit=8, execute=True, encoding_authorized=True)
        self.assertEqual((r3["status"], r3["metrics"]["attempted_count"], r3["metrics"]["indexed_current_after"]), ("succeeded", 4, 20))

        r4 = store.sync_effective_opinions(target, options=p1_opts, encoder=encoder, limit=8, execute=True, encoding_authorized=True)
        self.assertEqual((r4["status"], r4["metrics"]["attempted_count"], r4["metrics"]["reused_count"]), ("succeeded", 0, 8))

        # 同步全部 P2 干扰向量（30 条）写入同一全局 HNSW 索引
        rp2 = store.sync_effective_opinions(target, options=p2_opts, encoder=encoder, limit=50, execute=True, encoding_authorized=True)
        self.assertEqual(rp2["status"], "succeeded")

        # 在真实 HNSW 索引 + 迭代扫描下执行高选择性过滤查询（P1 + logistics + negative）
        hits = store.search_effective_opinions(
            target,
            UNIT,
            options=p1_opts,
            embedding_space_id=store.SPACE_ID,
            top_k=5,
            aspect="logistics",
            sentiment="negative",
        )
        self.assertEqual(len(hits), 5)
        self.assertTrue(all(h.document.product_id == "P1" and h.document.aspect == "logistics" and h.document.sentiment == "negative" for h in hits))

    def test_live_pg_migrate_sqlite_preserves_task_audit_blobs(self):
        target = self._new_pg_db()
        task_sqlite = Path(self.sqlite_db).parent / "tasks_with_audit.sqlite3"
        init_tasks(task_sqlite, self.sqlite_db)
        worker_started(task_sqlite, "sqlite-w1")
        params = {
            **IDENTITY,
            "source": "reviews_csv",
            "dataset_version": "reviews-csv",
            "product_ids": ["P1"],
            "date_from": "2023-01-01",
            "date_to": "2023-01-31",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
            "question": "迁移审计证据验证",
        }
        task_id = enqueue(task_sqlite, "analysis", params)["task"]["task_id"]
        claim_task(task_sqlite, "sqlite-w1")
        full_result = {
            "agent": True,
            "status": "ok",
            "question": params["question"],
            "scope": {k: v for k, v in params.items() if k != "question"},
            "result_ids": ["r1"],
            "results": {
                "r1": {
                    "result_id": "r1",
                    "kind": "evidence",
                    "parameters": {},
                    "data": {
                        "total": 40,
                        "page": 1,
                        "page_size": 40,
                        "items": [{"review_id": i, "text": f"评论正文 {i} " * 8} for i in range(1, 41)],
                    },
                }
            },
            "trace": [{"event": "tool_call", "tool": "get_evidence", "status": "success"}],
            "answer_sections": [{"result_id": "r1", "kind": "evidence", "text": "详情段落" * 20}],
        }
        finish_task(task_sqlite, task_id, "sqlite-w1", status="succeeded", result=full_result, audit_split_threshold=512)
        self.assertTrue(get_task_audit(task_sqlite, task_id)["stored_separately"])

        report = migrate_cli.migrate_sqlite(self.sqlite_db, target, source_tasks=task_sqlite)
        self.assertIn("tasks", report["verified_tables"])
        self.assertIn("task_audit_blobs", report["verified_tables"])
        self.assertEqual(report["verified_tables"]["task_audit_blobs"]["rows"], 1)

        # 验证迁移后的 PostgreSQL 库完整保留分离审计证据并通过完整性校验。
        pg_audit = get_task_audit(target, task_id)
        self.assertTrue(pg_audit["stored_separately"])
        pg_light = get_task(target, task_id, include_audit=False)
        self.assertIn("_audit_ref", pg_light["result"])
        pg_full = get_task(target, task_id, include_audit=True)
        self.assertEqual(pg_full["result"], full_result)
        self.assertTrue(has_saved_integrity(pg_full))


if __name__ == "__main__":
    unittest.main()
