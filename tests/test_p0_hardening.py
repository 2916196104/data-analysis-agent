"""P0 四项工程加固专项测试：
1. 嵌入子进程自愈重建、预热失败清理与批量编码；
2. pgvector HNSW 004 迁移与 O(top_k) 定向有效快照检索（无全量 JSONB 搬运）；
3. PostgreSQL 轻量连接池与 Schema 校验缓存；
4. TaskWorker 读写/批处理双通道隔离（消除队头阻塞）与并发分析执行。
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from psycopg.pq import TransactionStatus

from data_analysis_agent.core.database import (
    PostgresConnection,
    check_postgres_schema,
    close_database_pools,
    connect_database,
    database_pool_stats,
    invalidate_schema_cache,
)
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.store import (
    claim_task,
    enqueue,
    get_task,
    init_tasks,
    worker_started,
)
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.embedding_process import (
    PersistentEmbeddingProcess,
    close_embedding_processes,
    shared_embedding_process,
    start_embedding_warmup,
)
from data_analysis_agent.reviews.embedding_store import (
    MIGRATION_ROOT,
    SPACE_ID,
    EmbeddingStoreError,
    migrate_embedding_index,
    search_effective_opinions,
)
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalService
from data_analysis_agent.reviews.store import init_database
import test_semantic_retrieval as srtests
from test_review_workbench import IDENTITY, WorkbenchFixture

WORKER_FIXTURE = Path(__file__).parent / "fixtures/synthetic_embedding_worker.py"


class EmbeddingProcessHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.addCleanup(close_embedding_processes)

    def test_self_healing_recovers_after_crash_and_latches_after_budget_exhausted(self):
        client = PersistentEmbeddingProcess(
            sys.executable,
            self.directory,
            timeout=10,
            worker=WORKER_FIXTURE,
            max_restarts=2,
            restart_backoff_seconds=0.01,
        )
        self.addCleanup(client.close)

        first = client.embed_texts(["初始查询"])
        first_pid = client.last_metrics["process_id"]

        # 进程崩溃：本次请求必须失败关闭，但后续调用可在预算内自动重建。
        with self.assertRaises(EmbeddingStoreError):
            client.embed_texts(["crash"])
        self.assertTrue(client.failed)
        self.assertEqual(client.consecutive_failures, 1)

        recovered = client.embed_texts(["初始查询"])
        self.assertEqual(recovered, first)
        self.assertFalse(client.failed)
        self.assertEqual(client.consecutive_failures, 0)
        self.assertEqual(client.restart_count, 1)
        self.assertNotEqual(client.last_metrics["process_id"], first_pid)
        self.assertEqual(client.last_metrics["restarts"], 1)

        # 连续崩溃超过 max_restarts 后必须锁定失败，不再无限重启。
        for _ in range(3):
            with self.assertRaises(EmbeddingStoreError):
                client.embed_texts(["crash"])
        restarts_before_latch = client.restart_count
        with self.assertRaises(EmbeddingStoreError):
            client.embed_texts(["正常查询"])
        self.assertEqual(client.restart_count, restarts_before_latch)

    def test_warmup_failure_evicts_poisoned_singleton_for_next_request(self):
        broken_script = self.directory / "broken_worker.py"
        broken_script.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")

        def make_broken(python, cache_dir, timeout=600):
            proc = shared_embedding_process(python, cache_dir, timeout, max_restarts=0)
            proc.worker = broken_script
            return proc

        with patch(
            "data_analysis_agent.reviews.embedding_process.shared_embedding_process",
            side_effect=make_broken,
        ):
            thread = start_embedding_warmup(sys.executable, self.directory, timeout=5)
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

        # 预热失败后原失败单例已被清理；下次获取会建立全新实例。
        fresh = shared_embedding_process(sys.executable, self.directory, 5)
        self.assertFalse(fresh.failed)
        self.assertEqual(fresh.max_restarts, 3)

    def test_batch_query_encoding_runs_single_ipc_and_semantic_service_batches_variants(self):
        client = PersistentEmbeddingProcess(
            sys.executable,
            self.directory,
            timeout=10,
            worker=WORKER_FIXTURE,
        )
        self.addCleanup(client.close)

        queries = ["制冷很慢", "降温速度迟缓", "开了很久不凉快"]
        batch_vectors = client.embed_texts(queries)
        self.assertEqual(len(batch_vectors), 3)
        self.assertEqual(client.last_metrics["batch_size"], 3)
        for text, vec in zip(queries, batch_vectors):
            self.assertEqual(vec, client.embed_texts([text])[0])

        # SemanticRetrievalService.prepare() 面对批量编码器时，应单次调用编码全部 query + variants。
        service, encoder, _, _, _ = srtests.fixture()
        encoder.supports_batch_encode = True
        encoder.embed_texts = MagicMock(
            side_effect=lambda texts: [srtests.VECTOR[:] for _ in texts]
        )
        service.prepare(
            {"question": "有没有制冷很慢的反馈？"},
            query_variants=("有没有制冷速度迟缓的反馈？", "有没有空调开了好一会儿才凉快的评论？"),
        )
        encoder.embed_texts.assert_called_once_with(
            [
                "有没有制冷很慢的反馈？",
                "有没有制冷速度迟缓的反馈？",
                "有没有空调开了好一会儿才凉快的评论？",
            ]
        )

        # 批量扩展编码失败时，仍安全回退至仅编码主问题。
        fallback_calls = []

        def fail_batch(texts):
            fallback_calls.append(list(texts))
            if len(texts) > 1:
                raise RuntimeError("transient batch failure")
            return [srtests.VECTOR[:]]

        encoder.embed_texts = fail_batch
        service.prepare(
            {"question": "有没有制冷很慢的反馈？"},
            query_variants=("有没有制冷速度迟缓的反馈？",),
        )
        self.assertEqual(
            fallback_calls,
            [
                ["有没有制冷很慢的反馈？", "有没有制冷速度迟缓的反馈？"],
                ["有没有制冷很慢的反馈？"],
            ],
        )


class PgvectorOptimizationTests(WorkbenchFixture, unittest.TestCase):
    def test_004_hnsw_migration_scripts_and_explicit_migrator(self):
        up = (MIGRATION_ROOT / "004_review_opinion_embeddings_hnsw.up.sql").read_text(encoding="utf-8")
        down = (MIGRATION_ROOT / "004_review_opinion_embeddings_hnsw.down.sql").read_text(encoding="utf-8")
        self.assertIn("USING hnsw (embedding vector_cosine_ops)", up)
        self.assertIn("WHERE invalidated_at IS NULL", up)
        self.assertIn("review_embeddings_active_scope", up)
        self.assertIn("DROP INDEX IF EXISTS review_embeddings_hnsw_cosine", down)

        executed = []
        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.raw.execute.side_effect = lambda script, prepare=False: executed.append((script, prepare))
        with patch("data_analysis_agent.reviews.embedding_store._connect", return_value=fake_conn):
            migrate_embedding_index("postgresql://offline.invalid/demo", rollback=False)
            migrate_embedding_index("postgresql://offline.invalid/demo", rollback=True)
        self.assertEqual(len(executed), 2)
        self.assertIn("review_embeddings_hnsw_cosine", executed[0][0])
        self.assertIn("DROP INDEX IF EXISTS", executed[1][0])

    def test_targeted_effective_corpus_matches_full_corpus_per_review(self):
        options = dict(
            **IDENTITY,
            source="amazon_reviews_2023",
            dataset_version="amazon-reviews-2023",
            product_ids=["P1"],
            date_from="2023-01-01",
            date_to="2023-01-06",
        )
        full = read_effective_corpus(self.db, **options)
        self.assertTrue(full.reviews)
        target_rid = full.reviews[0].review_id
        subset = read_effective_corpus(self.db, **options, review_ids=[target_rid])
        self.assertEqual([r for r in full.reviews if r.review_id == target_rid], list(subset.reviews))
        self.assertEqual([d for d in full.documents if d.review_id == target_rid], list(subset.documents))
        empty = read_effective_corpus(self.db, **options, review_ids=[])
        self.assertEqual((empty.reviews, empty.documents), ((), ()))

    def test_hnsw_search_avoids_jsonb_filters_stale_snapshots_and_rejects_tampering(self):
        self.enterContext(patch("data_analysis_agent.reviews.analytics.check_labeling_schema"))
        sources = [srtests.source(1), srtests.source(2)]
        corpus = srtests.RagCorpus(
            srtests.analysis_scope(**srtests.OPTIONS)[3],
            sources[0].opinions[0].label_profile_key,
            tuple(d for s in sources for d in s.opinions),
            tuple(s.state for s in sources),
        )
        stale_row = dict(
            asdict(sources[0].opinions[0]),
            effective_snapshot_sha256="0" * 64,  # 模拟人工复核后快照摘要已变化
            distance=0.05,
        )
        valid_row = dict(
            asdict(sources[1].opinions[0]),
            effective_snapshot_sha256=sources[1].state.effective_snapshot_sha256,
            distance=0.12,
        )

        class Rows:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

        class RecordingPG(srtests.FakePG):
            def __init__(self, returned_rows):
                super().__init__(active=True)
                self.returned_rows = returned_rows
                self.sql_queries = []

            def execute(self, query, parameters=None):
                if "embedding <=>" in query:
                    self.sql_queries.append((query, parameters))
                    return Rows(self.returned_rows)
                return super().execute(query, parameters)

        conn = RecordingPG([stale_row, valid_row])
        read_calls = []

        def targeted_read(db, **kwargs):
            read_calls.append(kwargs.get("review_ids"))
            return corpus

        with patch("data_analysis_agent.reviews.embedding_store.read_effective_corpus", side_effect=targeted_read):
            matches = search_effective_opinions(
                conn,
                srtests.VECTOR,
                options=srtests.OPTIONS,
                embedding_space_id=SPACE_ID,
                top_k=5,
            )

        self.assertEqual(len(conn.sql_queries), 1)
        sql_text = conn.sql_queries[0][0]
        self.assertNotIn("jsonb_to_recordset", sql_text)
        self.assertIn("ORDER BY r.embedding <=> ?::vector(1024) LIMIT ?", sql_text)
        self.assertEqual(read_calls, [(1, 2)])
        self.assertEqual([m.document.review_id for m in matches], [2])

        # 若关键来源字段被篡改（即使 key 与 snapshot 一致），仍必须 fail-closed 抛错。
        tampered_row = {**valid_row, "quote_start": 99}
        conn_tampered = RecordingPG([tampered_row])
        with patch("data_analysis_agent.reviews.embedding_store.read_effective_corpus", return_value=corpus):
            with self.assertRaisesRegex(EmbeddingStoreError, "stored_provenance_mismatch"):
                search_effective_opinions(
                    conn_tampered,
                    srtests.VECTOR,
                    options=srtests.OPTIONS,
                    embedding_space_id=SPACE_ID,
                )


class PostgresPoolAndSchemaCacheTests(unittest.TestCase):
    TARGET = "postgresql://user:secret@127.0.0.1:5432/p0_pool_test"

    def setUp(self):
        close_database_pools()
        invalidate_schema_cache()
        self.addCleanup(close_database_pools)
        self.addCleanup(invalidate_schema_cache)

    def _make_fake_raw(self):
        raw = MagicMock()
        raw.closed = False
        raw.broken = False
        raw.info.transaction_status = TransactionStatus.IDLE
        return raw

    def test_pool_reuses_healthy_connections_isolates_modes_and_skips_worker_namespace(self):
        created = []

        def fake_connect(*args, **kwargs):
            raw = self._make_fake_raw()
            created.append(raw)
            return raw

        before = database_pool_stats()
        with (
            patch("data_analysis_agent.core.database.psycopg.connect", side_effect=fake_connect) as patched_connect,
            patch("data_analysis_agent.core.database._REAL_PSYCOPG_CONNECT", patched_connect),
        ):
            # 1) 同目标只读连接归还后被复用
            with closing(connect_database(self.TARGET, readonly=True, namespace="business")) as c1:
                raw1 = c1.raw
            with closing(connect_database(self.TARGET, readonly=True, namespace="business")) as c2:
                self.assertIs(c2.raw, raw1)
            self.assertEqual(len(created), 1)
            raw1.close.assert_not_called()

            # 2) 读写连接与只读连接严格分桶，不能复用只读连接
            with closing(connect_database(self.TARGET, readonly=False, namespace="business")) as c_rw:
                self.assertIsNot(c_rw.raw, raw1)
            self.assertEqual(len(created), 2)

            # 3) namespace="worker" 持有会话锁，绝不进入连接池
            with closing(connect_database(self.TARGET, readonly=False, namespace="worker")) as c_worker:
                worker_raw = c_worker.raw
            worker_raw.close.assert_called_once()
            self.assertEqual(len(created), 3)

            # 4) 处于未结束事务的连接归还池时自动 rollback
            with closing(connect_database(self.TARGET, readonly=True, namespace="business")) as c3:
                c3.raw.info.transaction_status = TransactionStatus.INTRANS

                def reset_status():
                    c3.raw.info.transaction_status = TransactionStatus.IDLE

                c3.raw.rollback.side_effect = reset_status
            raw1.rollback.assert_called_once()

            # 5) 损坏连接归还时直接销毁而不回池
            with closing(connect_database(self.TARGET, readonly=True, namespace="business")) as c4:
                c4.raw.broken = True
            raw1.close.assert_called_once()

        after = database_pool_stats()
        self.assertGreaterEqual(after["hits"] - before["hits"], 3)
        self.assertGreaterEqual(after["discards"] - before["discards"], 1)

        closed_count = close_database_pools(self.TARGET)
        self.assertGreaterEqual(closed_count, 1)
        self.assertEqual(database_pool_stats()["idle_connections"], 0)

    def test_schema_cache_skips_repeated_catalog_checks_and_never_caches_failure(self):
        raw = self._make_fake_raw()
        with patch("data_analysis_agent.core.database.psycopg.connect", return_value=raw):
            conn = PostgresConnection(self.TARGET, readonly=True, namespace="business")
        self.addCleanup(conn.close)

        # 未初始化数据库必须报错且不被缓存
        with patch("data_analysis_agent.core.database.table_names", return_value=set()) as tables:
            for _ in range(2):
                with self.assertRaises(ReviewDataError):
                    check_postgres_schema(conn, "business")
            self.assertEqual(tables.call_count, 2)

        # 初始化后首次校验通过并写入 TTL 缓存，第二次不再查表元数据
        raw.execute.reset_mock()
        raw.execute.return_value.fetchone.return_value = (1,)
        with patch("data_analysis_agent.core.database.table_names", return_value={"review_database_schema"}) as tables:
            check_postgres_schema(conn, "business")
            check_postgres_schema(conn, "business")
            self.assertEqual(tables.call_count, 1)
            self.assertEqual(raw.execute.call_count, 1)

            invalidate_schema_cache(self.TARGET, "business")
            check_postgres_schema(conn, "business")
            self.assertEqual(tables.call_count, 2)


class WorkerLaneIsolationTests(unittest.TestCase):
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
            "LLM_MODEL_ID=demo\nLLM_API_KEY=fake-key\nLLM_BASE_URL=https://offline.invalid/v1\n",
            encoding="utf-8",
        )
        init_database(self.db)
        init_labeling(self.db)
        init_workbench(self.db)
        init_monitoring(self.db)
        init_tasks(self.task_db, self.db)

        self.sample_file = self.import_root / "sample.jsonl"
        self.sample_file.write_text("{}\n", encoding="utf-8")
        self.analysis_params = {
            "model": "demo",
            "base_url": "https://offline.invalid/v1",
            "prompt_version": "v1",
            "product_ids": ["P1"],
            "source": "reviews_csv",
            "dataset_version": "reviews-csv",
            "date_from": "2023-01-01",
            "date_to": "2023-01-31",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
        }

    def test_claim_task_filters_by_kind_and_is_atomic_under_concurrency(self):
        worker_started(self.task_db, "w1")
        imp = enqueue(self.task_db, "import", {"reviews_path": str(self.sample_file)})["task"]["task_id"]
        a1 = enqueue(self.task_db, "analysis", self.analysis_params)["task"]["task_id"]
        a2 = enqueue(self.task_db, "analysis", {**self.analysis_params, "date_to": "2023-01-20"})["task"]["task_id"]

        # 仅排除 analysis 时只能领到 import
        self.assertEqual(claim_task(self.task_db, "w1", exclude_kinds=("analysis",))["task_id"], imp)
        self.assertIsNone(claim_task(self.task_db, "w1", exclude_kinds=("analysis",)))

        # 多个分析线程并发抢占两个 analysis 任务，不得重复领取
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(lambda _: claim_task(self.task_db, "w1", kinds=("analysis",)), range(4)))
        ids = [row["task_id"] for row in claimed if row is not None]
        self.assertEqual(sorted(ids), sorted([a1, a2]))

    def test_blocked_batch_task_does_not_block_concurrent_analysis_tasks(self):
        batch_entered = threading.Event()
        release_batch = threading.Event()
        analysis_barrier = threading.Barrier(2, timeout=5)

        def slow_import(*args, **kwargs):
            batch_entered.set()
            if not release_batch.wait(timeout=8):
                raise RuntimeError("test_batch_release_timeout")
            return {"imports": []}

        def parallel_analysis(*args, **kwargs):
            # 两个 analysis 任务必须真正并发进入此处才能同时穿过 Barrier。
            analysis_barrier.wait()
            return {"run_id": "synthetic-run", "status": "ok"}

        worker = TaskWorker(
            self.db,
            self.task_db,
            import_root=self.import_root,
            env_file=self.env_file,
            analysis_concurrency=2,
        )
        with patch("data_analysis_agent.jobs.worker.import_amazon_jsonl", side_effect=slow_import), patch(
            "data_analysis_agent.jobs.worker.run_analysis", side_effect=parallel_analysis
        ):
            worker.start()
            self.addCleanup(worker.stop)
            try:
                imp_id = enqueue(self.task_db, "import", {"reviews_path": str(self.sample_file)})["task"]["task_id"]
                self.assertTrue(batch_entered.wait(timeout=5), "批处理任务未进入执行态")

                # 批处理任务被阻塞期间，提交两条 analysis 任务必须并发且立即完成。
                a1_id = enqueue(self.task_db, "analysis", self.analysis_params)["task"]["task_id"]
                a2_id = enqueue(
                    self.task_db,
                    "analysis",
                    {**self.analysis_params, "date_to": "2023-01-15"},
                )["task"]["task_id"]

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    s1 = get_task(self.task_db, a1_id)["status"]
                    s2 = get_task(self.task_db, a2_id)["status"]
                    if s1 == "succeeded" and s2 == "succeeded":
                        break
                    time.sleep(0.05)

                self.assertEqual(get_task(self.task_db, a1_id)["status"], "succeeded")
                self.assertEqual(get_task(self.task_db, a2_id)["status"], "succeeded")
                # 此时批处理任务仍在运行中，证明分析通道完全未被队头阻塞。
                self.assertEqual(get_task(self.task_db, imp_id)["status"], "running")
            finally:
                release_batch.set()

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if get_task(self.task_db, imp_id)["status"] == "succeeded":
                    break
                time.sleep(0.05)
            self.assertEqual(get_task(self.task_db, imp_id)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
