"""P1-2 混合语义检索与召回质量升级回归测试：
1. 问句外壳剥离、否定语义保留与极性冲突惩罚；
2. Multi-Query + BM25 + RRF + Rerank 联合流水线及篡改校验；
3. CrossEncoderReranker 进程级模型缓存；
4. 多领域查询扩展规则；
5. 隔离真实 PostgreSQL 18 + pgvector 词法 SQL 下推（不触发全表 corpus 扫描）与 BM25 救回。
"""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg

from test_agent import SETTINGS
import test_semantic_retrieval as srtests
from data_analysis_agent.agents.review_domain_rules import domain_rules
from data_analysis_agent.agents.review_retrieval import (
    execute_retrieval,
    retrieval_query_variants,
    validate_retrieval,
)
from data_analysis_agent.core.database import close_database_pools
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_store import (
    OpinionMatch,
    SPACE_ID,
    migrate_embedding_store,
    sync_effective_opinions,
)
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.hybrid_retrieval import (
    BM25Index,
    CrossEncoderReranker,
    LEXICAL_SEMANTIC_RERANK_POLICY,
    LexicalSemanticReranker,
    RerankItem,
    clear_reranker_cache,
    extract_lexical_terms,
    strip_question_shell,
)
from data_analysis_agent.reviews.label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.reviews.loader import SOURCE
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import RagCorpus, _document, _review_state
from data_analysis_agent.reviews.scope import analysis_scope
from data_analysis_agent.reviews.semantic_retrieval import (
    PostgresSemanticRepository,
    ReviewSource,
    SemanticRetrievalService,
)
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl, init_database


def _make_source(rid: int, *, quote: str, review_text: str | None = None, aspect: str = "effectiveness", sentiment: str = "negative"):
    text = review_text or quote
    identity = tuple(srtests.OPTIONS[k] for k in ("model", "base_url", "prompt_version"))
    profile = content_sha256(list(identity))
    day = f"2031-01-01T12:{rid:02d}:00+00:00"
    row = dict(
        source=srtests.OPTIONS["source"],
        dataset_version=srtests.OPTIONS["dataset_version"],
        product_id=srtests.OPTIONS["product_ids"][0],
        review_id=rid,
        annotation_id=300 + rid,
        human_revision_id=None,
        processing_status="success",
        human_status="unreviewed",
        error_code=None,
        opinion_source="model",
        review_content_sha256=content_sha256({"p12_review": text, "rid": rid}),
        published_at=day,
        review_text=text,
        import_run_id=9,
        line_number=20 + rid,
        file_sha256=content_sha256("P12-HYBRID-FILE"),
    )
    start = text.index(quote)
    doc = _document(
        dict(
            **row,
            model_opinion_id=800 + rid,
            human_opinion_id=None,
            quote=quote,
            quote_start=start,
            quote_end=start + len(quote),
            aspect=aspect,
            sentiment=sentiment,
        ),
        identity,
        profile,
    )
    return ReviewSource(_review_state(row, [doc], profile), (doc,), text)


class _MultiQuerySnapshot(srtests.FakeSnapshot):
    def __init__(self, sources, channel_matches):
        super().__init__([], list(sources))
        self._channel_matches = list(channel_matches)
        self._call_index = 0
        self.matches = list(channel_matches[0]) if channel_matches else []

    def search(self, vector, *, top_k, aspect, sentiment):
        idx = min(self._call_index, len(self._channel_matches) - 1)
        self._call_index += 1
        self.matches = list(self._channel_matches[idx])
        return super().search(vector, top_k=top_k, aspect=aspect, sentiment=sentiment)

    def corpus(self):
        return RagCorpus(
            analysis_scope(**self.scope.options())[3],
            self.sources[0].opinions[0].label_profile_key,
            tuple(d for s in self.sources for d in s.opinions),
            tuple(s.state for s in self.sources),
        )


class _MultiEncoder(srtests.FakeEncoder):
    supports_batch_encode = True

    def embed_texts(self, texts):
        self.events.append("encode")
        self.calls.append(texts)
        return [srtests.VECTOR for _ in texts]


class P12HybridRetrievalUpgradeTests(unittest.TestCase):
    def tearDown(self):
        clear_reranker_cache()

    def test_strip_question_shell_and_negation_preserved_in_bm25_and_terms(self):
        self.assertEqual(strip_question_shell("商品100013588055有没有没收到货的反馈？"), "没收到货")
        self.assertEqual(strip_question_shell("2023-01-01至2023-03-31期间有没有包装破损的买家评论？"), "包装破损")
        terms = extract_lexical_terms("商品100013588055有没有没收到货的反馈？")
        self.assertIn("没收", terms)
        self.assertIn("收到", terms)
        self.assertNotIn("反馈", terms)
        self.assertNotIn("有没", terms)

        # BM25 不应被“反馈/有没有”问句外壳误召回“客服反馈很快”，应准确召回“一直没收到货”。
        s_noise = _make_source(1, quote="客服反馈很快，服务很好", aspect="service", sentiment="positive")
        s_target = _make_source(2, quote="下单五天一直没收到货", aspect="logistics", sentiment="negative")
        s_pos = _make_source(3, quote="已经收到货了，物流满意", aspect="logistics", sentiment="positive")
        hits = BM25Index([s_noise.opinions[0], s_target.opinions[0], s_pos.opinions[0]]).search(
            "有没有没收到货的反馈？",
            top_k=5,
        )
        self.assertEqual(hits[0].document.review_id, 2)
        self.assertTrue(all(h.document.review_id != 1 for h in hits))

    def test_lexical_semantic_reranker_polarity_penalty_and_bm25_rescue_balance(self):
        reranker = LexicalSemanticReranker()
        # 1) 肯定故障问法 vs 否定澄清引文：“外包装破损”必须明显高于“外包装没有破损”
        scores = reranker.score(
            "有没有包装破损的反馈？",
            [
                RerankItem(
                    key="negated",
                    quote="外包装没有破损",
                    review_text="快递很快，外包装没有破损。",
                    cosine_distance=0.10,
                    bm25_score=3.0,
                    rrf_score=0.032,
                    channel="hybrid",
                ),
                RerankItem(
                    key="damaged",
                    quote="外包装破损严重",
                    review_text="收到时外包装破损严重，边角压瘪了。",
                    cosine_distance=0.18,
                    bm25_score=3.0,
                    rrf_score=0.031,
                    channel="hybrid",
                ),
            ],
        )
        self.assertGreater(scores["damaged"], scores["negated"])

        # 2) 否定诉求问法 vs 肯定引文：“一直没收到货”必须明显高于“已经收到货了”
        timing_scores = reranker.score(
            "有没有还没收到货的反馈？",
            [
                RerankItem(
                    key="received",
                    quote="已经收到货了",
                    review_text="已经收到货了，很满意。",
                    cosine_distance=0.12,
                    bm25_score=2.2,
                    rrf_score=0.030,
                    channel="hybrid",
                ),
                RerankItem(
                    key="not_received",
                    quote="下单五天一直没收到货",
                    review_text="下单五天一直没收到货，物流停滞。",
                    cosine_distance=0.16,
                    bm25_score=3.4,
                    rrf_score=0.031,
                    channel="hybrid",
                ),
            ],
        )
        self.assertGreater(timing_scores["not_received"], timing_scores["received"])

        # 3) BM25-only 精确词法救回（无向量距离）不应被弱相关 vector-only 候选系统性压低
        rescue_scores = reranker.score(
            "有没有 KFR35 噪音大的反馈？",
            [
                RerankItem(
                    key="vec_weak",
                    quote="外观设计简洁漂亮",
                    review_text="外观设计简洁漂亮，家人喜欢。",
                    cosine_distance=0.22,
                    bm25_score=None,
                    rrf_score=0.016,
                    channel="vector",
                ),
                RerankItem(
                    key="bm25_exact",
                    quote="KFR35 外机噪音大",
                    review_text="这台 KFR35 外机噪音大，晚上吵。",
                    cosine_distance=None,
                    bm25_score=5.2,
                    rrf_score=0.016,
                    channel="bm25",
                ),
            ],
        )
        self.assertGreater(rescue_scores["bm25_exact"], rescue_scores["vec_weak"])

    def test_unified_multi_query_plus_hybrid_bm25_and_reranker_pipeline(self):
        # 噪音高发场景：向量主路漏掉 s3（外机嗡嗡响），扩展变体 + BM25 联合召回并重排置顶。
        s1 = _make_source(1, quote="空调噪音很大，晚上睡不好", aspect="effectiveness", sentiment="negative")
        s2 = _make_source(2, quote="外观一般，颜色偏白", aspect="quality", sentiment="negative")
        s3 = _make_source(3, quote="外机嗡嗡响声音大，运行声音很吵", aspect="effectiveness", sentiment="negative")
        s4 = _make_source(4, quote="空调运行噪音不大，很安静", aspect="effectiveness", sentiment="positive")

        snap = _MultiQuerySnapshot(
            (s1, s2, s3, s4),
            [
                (OpinionMatch(s2.opinions[0], 0.14), OpinionMatch(s1.opinions[0], 0.19)),
                (OpinionMatch(s3.opinions[0], 0.11), OpinionMatch(s1.opinions[0], 0.17)),
                (OpinionMatch(s3.opinions[0], 0.12), OpinionMatch(s2.opinions[0], 0.25)),
            ],
        )
        encoder = _MultiEncoder([])

        class FakeContext:
            question = "有没有噪音很大的反馈？"
            connection = object()
            settings = replace(
                SETTINGS,
                rag_multi_query=True,
                rag_hybrid_bm25=True,
                rag_rerank_enabled=True,
            )

            def semantic_service_factory(self, conn, scope):
                return SemanticRetrievalService(
                    encoder=encoder,
                    repository=srtests.FakeRepository([], snap),
                    scope=scope,
                )

        data = execute_retrieval(FakeContext(), srtests.OPTIONS)
        retrieval = data["retrieval"]
        self.assertEqual(retrieval["version"], "semantic-retrieval-v4")
        self.assertEqual(retrieval["hybrid_retrieval"]["rerank_policy"], LEXICAL_SEMANTIC_RERANK_POLICY)
        self.assertTrue(retrieval["hybrid_retrieval"]["bm25_enabled"])
        self.assertEqual(
            retrieval["hybrid_retrieval"]["query_variants"],
            [
                "有没有空调运行声音很吵的反馈？",
                "有没有外机或室内机嗡嗡响声音大的评论？",
            ],
        )
        # 批量编码一次性送入主 Query + 2 条变体
        self.assertEqual(len(encoder.calls), 1)
        self.assertEqual(len(encoder.calls[0]), 3)
        # s1 和 s3 都应通过融合与重排位于最前，压过不相关的 s2
        top_ids = [row["review_id"] for row in retrieval["evidence"][:2]]
        self.assertEqual(set(top_ids), {1, 3})
        self.assertTrue(validate_retrieval(data, FakeContext.question))

        # 篡改 hybrid_retrieval.query_variants 必须 fail-closed 拒绝
        tampered = deepcopy(data)
        tampered["retrieval"]["hybrid_retrieval"]["query_variants"] = ["伪造扩展问法"]
        self.assertFalse(validate_retrieval(tampered, FakeContext.question))

    def test_cross_encoder_reranker_caches_model_across_instances(self):
        fake_model = MagicMock()
        fake_model.predict.return_value = [1.5, -0.5]
        fake_cls = MagicMock(return_value=fake_model)
        fake_module = types.ModuleType("sentence_transformers")
        fake_module.CrossEncoder = fake_cls

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            items = [
                RerankItem("k1", "制冷很慢", "制冷很慢", 0.1, 2.0, 0.03, "hybrid"),
                RerankItem("k2", "外观漂亮", "外观漂亮", 0.2, None, 0.01, "vector"),
            ]
            r1 = CrossEncoderReranker(cache_dir=tmpdir, batch_size=16)
            r2 = CrossEncoderReranker(cache_dir=tmpdir, batch_size=16)
            s1 = r1.score("有没有制冷很慢的反馈？", items)
            s2 = r2.score("有没有制冷很慢的反馈？", items)
            self.assertEqual(s1, s2)
            self.assertGreater(s1["k1"], s1["k2"])
            # 两个独立 Reranker 实例共享同一个已加载模型，仅加载一次磁盘权重。
            fake_cls.assert_called_once()
            self.assertEqual(fake_model.predict.call_count, 2)
            self.assertEqual(fake_model.predict.call_args.kwargs.get("batch_size"), 16)

            clear_reranker_cache()
            CrossEncoderReranker(cache_dir=tmpdir).score("有没有制冷很慢的反馈？", items)
            self.assertEqual(fake_cls.call_count, 2)

    def test_expanded_domain_rules_cover_core_review_scenarios_without_regressions(self):
        bundle = domain_rules()
        enabled = replace(SETTINGS, rag_multi_query=True)
        scope = {"product_ids": ["P1"], "date_from": "2023-01-01", "date_to": "2023-03-31"}
        expected_domains = (
            "有没有噪音很大的反馈？",
            "有没有包装破损的反馈？",
            "有没有客服态度差的反馈？",
            "有没有安装师傅态度差的反馈？",
            "有没有制热效果差的反馈？",
            "有没有还没收到货的反馈？",
            "有没有收到货后还在等师傅上门安装的反馈？",
        )
        for question in expected_domains:
            with self.subTest(question=question):
                variants = retrieval_query_variants(question, scope, enabled, rules=bundle)
                self.assertEqual(len(variants), 2)


@unittest.skipUnless(
    os.environ.get("TEST_PGVECTOR_ADMIN_URL"),
    "set TEST_PGVECTOR_ADMIN_URL to run live PostgreSQL + pgvector lexical pushdown tests",
)
class PostgresHybridLexicalPushdownTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ["TEST_PGVECTOR_ADMIN_URL"]
        self.db_name = f"p1_2_hybrid_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.db_name}"')
        parts = urlsplit(self.admin_url)
        self.target = urlunsplit((parts.scheme, parts.netloc, "/" + self.db_name, parts.query, ""))
        self.addCleanup(self._drop_database)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

        init_database(self.target)
        init_labeling(self.target)
        init_workbench(self.target)
        with psycopg.connect(self.target, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION vector")
        migrate_embedding_store(self.target, with_hnsw_index=True)

        self.identity = {
            "model": "SIMULATED-p12",
            "base_url": "https://offline.invalid/v1",
            "prompt_version": "prompt-p12",
        }
        self.scope = {
            **self.identity,
            "source": SOURCE,
            "dataset_version": "p12-live",
            "product_ids": ["P1"],
            "date_from": "2023-01-01",
            "date_to": "2023-03-31",
        }

        records = [
            ("外包装破损严重，边角摔烂了。", [("packaging", "negative", "外包装破损严重")]),
            ("快递纸箱破损，打开后有点变形。", [("packaging", "negative", "快递纸箱破损")]),
            ("外包装没有破损，保护得很好。", [("packaging", "positive", "外包装没有破损")]),
        ]
        for idx in range(37):
            records.append((
                f"第{idx + 1}条普通评论：制冷效果稳定，颜色耐看。",
                [("effectiveness", "positive", "制冷效果稳定")],
            ))

        jsonl_path = self.root / "p12.jsonl"
        lines = []
        for idx, (text, _) in enumerate(records):
            ts = int(datetime(2023, 1, 1 + (idx % 28), 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
            lines.append(json.dumps({
                "parent_asin": "P1",
                "user_id": f"u-{idx}",
                "text": text,
                "timestamp": ts,
                "rating": 5 if idx >= 2 else 1,
            }, ensure_ascii=False))
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        import_amazon_jsonl(self.target, reviews_path=jsonl_path, dataset_version="p12-live")

        by_text = dict(records)
        for review in pending_reviews(self.target, **self.identity, dataset_version="p12-live", limit=100):
            opinions = [
                {"aspect": aspect, "sentiment": sentiment, "quote": quote}
                for aspect, sentiment, quote in by_text[review["text"]]
            ]
            save_annotation(
                self.target,
                review,
                **self.identity,
                status="success",
                opinions=validate_opinions(json.dumps({"opinions": opinions}, ensure_ascii=False), review["text"]),
            )

        # 刻意让向量空间把“包装破损”排在远离查询向量的位置，验证 PG SQL 词法下推 + BM25 救回 + 极性重排。
        class _BiasedEncoder:
            space = srtests.SPACE
            supports_batch_encode = True

            def embed_texts(self, texts):
                vectors = []
                for text in texts:
                    if "包装破损" in text or "纸箱破损" in text:
                        vectors.append([0.0, 1.0] + [0.0] * 1022)
                    elif "没有破损" in text:
                        vectors.append([0.95, 0.3122499] + [0.0] * 1022)
                    else:
                        vectors.append([1.0, 0.0] + [0.0] * 1022)
                return vectors

            def encode(self, texts):
                return {"space_id": SPACE_ID, "vectors": self.embed_texts(texts)}

        self.encoder = _BiasedEncoder()
        sync_effective_opinions(
            self.target,
            options=self.scope,
            encoder=self.encoder,
            limit=100,
            execute=True,
            encoding_authorized=True,
        )

    def _drop_database(self):
        close_database_pools(self.target)
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{self.db_name}"')

    def test_live_pg_lexical_pushdown_avoids_full_corpus_scan_and_rescues_bm25_hits(self):
        with closing(connect_review_database(self.target, readonly=True)) as conn:
            conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                service = SemanticRetrievalService(
                    encoder=self.encoder,
                    repository=PostgresSemanticRepository(conn),
                    scope=self.scope,
                )

                class LiveContext:
                    question = "有没有包装破损的反馈？"
                    connection = conn
                    settings = replace(
                        SETTINGS,
                        rag_multi_query=True,
                        rag_hybrid_bm25=True,
                        rag_rerank_enabled=True,
                    )

                    def semantic_service_factory(self, _c, _s):
                        return service

                data = execute_retrieval(LiveContext(), self.scope)
            finally:
                conn.rollback()

        retrieval = data["retrieval"]
        timings = data["timings"]
        self.assertEqual(retrieval["version"], "semantic-retrieval-v4")
        self.assertTrue(validate_retrieval(data, "有没有包装破损的反馈？"))
        # 词法下推仅预筛出含相关词元的少量候选评论，绝未拉取全部 40 条评论！
        self.assertIn("bm25_sql_prefilter_ms", timings)
        self.assertLess(timings["bm25_candidate_reviews"], 10)
        self.assertGreaterEqual(timings["bm25_candidate_reviews"], 2)

        quotes = [row["quote"] for row in retrieval["evidence"]]
        # BM25 成功救回被向量漏掉的“外包装破损严重”与“快递纸箱破损”，且排在“外包装没有破损”之前。
        self.assertIn("外包装破损严重", quotes)
        self.assertIn("快递纸箱破损", quotes)
        if "外包装没有破损" in quotes:
            self.assertLess(quotes.index("外包装破损严重"), quotes.index("外包装没有破损"))


if __name__ == "__main__":
    unittest.main()
