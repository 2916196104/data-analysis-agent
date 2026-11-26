"""SSE 流式问答、任务 SSE 竞态修复与 BM25 + 向量混合检索 + Reranker 专项测试。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from openai import OpenAI as RealOpenAI

from data_analysis_agent.agents.review_retrieval import execute_retrieval, validate_retrieval
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.config import AgentSettings, ConfigurationError
from data_analysis_agent.core.llm_gateway import close_llm_clients, llm_gateway_stats, reset_llm_gateway_stats
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs.store import claim_task, enqueue, finish_task, init_tasks, worker_started
from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.human_review import init_workbench
from data_analysis_agent.reviews.hybrid_retrieval import (
    BM25Index,
    CROSS_ENCODER_RERANK_POLICY,
    CrossEncoderReranker,
    LEXICAL_SEMANTIC_RERANK_POLICY,
    LexicalSemanticReranker,
    reciprocal_rank_fusion,
    tokenize_for_bm25,
)
from data_analysis_agent.reviews.label_store import init_labeling
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.rag_corpus import RagCorpus, _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import (
    ReviewSource,
    SemanticRetrievalError,
    SemanticRetrievalService,
    require_source_validated_result,
)
from data_analysis_agent.reviews.store import init_database
from test_agent import FAKE_KEY, SETTINGS
import test_semantic_retrieval as srtests


def _make_source(
    rid: int,
    *,
    quote: str,
    review_text: str | None = None,
    aspect: str = "effectiveness",
    sentiment: str = "negative",
    day: str = "2031-01-01T23:59:59.999+00:00",
) -> ReviewSource:
    text = review_text if review_text is not None else quote
    identity = tuple(srtests.OPTIONS[k] for k in ("model", "base_url", "prompt_version"))
    profile = content_sha256(list(identity))
    row = dict(
        source=srtests.OPTIONS["source"],
        dataset_version=srtests.OPTIONS["dataset_version"],
        product_id=srtests.OPTIONS["product_ids"][0],
        review_id=rid,
        annotation_id=100 + rid,
        human_revision_id=None,
        processing_status="success",
        human_status="unreviewed",
        error_code=None,
        opinion_source="model",
        review_content_sha256=content_sha256({"synthetic_record": text, "published": day}),
        published_at=day,
        review_text=text,
        import_run_id=3,
        line_number=10 + rid,
        file_sha256=content_sha256("SYNTHETIC-FILE"),
    )
    start = text.index(quote)
    doc = _document(
        dict(
            **row,
            model_opinion_id=500 + 10 * rid,
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


class _HybridFakeSnapshot(srtests.FakeSnapshot):
    def __init__(self, sources: tuple[ReviewSource, ...], matches: tuple[OpinionMatch, ...]):
        super().__init__([], list(sources))
        self.matches = list(matches)
        self._all_sources = sources

    def corpus(self):
        return RagCorpus(
            self.scope.options(),
            self._all_sources[0].opinions[0].label_profile_key,
            tuple(d for s in self._all_sources for d in s.opinions),
            tuple(s.state for s in self._all_sources),
        )


def _sse_openai_response(chunks: list[dict]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode("utf-8"))


def _chunk(content: str | None = None, *, finish_reason: str | None = None, tool_calls=None, usage=None) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    payload: dict = {
        "id": "chatcmpl-stream-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": SETTINGS.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def _parse_sse_events(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event_name = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


class ChatAndTaskSseTests(unittest.TestCase):
    def setUp(self):
        close_llm_clients()
        reset_llm_gateway_stats()
        self.addCleanup(close_llm_clients)
        self.addCleanup(reset_llm_gateway_stats)
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

    def _mock_stream(self, handler):
        def factory(**kwargs):
            return RealOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

        return patch("openai.OpenAI", side_effect=factory)

    def test_chat_stream_sse_emits_deltas_and_redacts_secret_split_across_chunks(self):
        secret_left = FAKE_KEY[:12]
        secret_right = FAKE_KEY[12:]
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(body)
            self.assertTrue(body["stream"])
            return _sse_openai_response([
                _chunk("你好，"),
                _chunk(f"密钥测试:{secret_left}"),
                _chunk(f"{secret_right}，结束。"),
                _chunk(None, finish_reason="stop", usage={"prompt_tokens": 11, "completion_tokens": 9, "total_tokens": 20}),
            ])

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with self._mock_stream(handler), TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "测试流式输出"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertNotIn(FAKE_KEY, resp.text)

        events = _parse_sse_events(resp.text)
        self.assertEqual(events[0], ("start", {"mode": "basic"}))
        deltas = [payload["delta"] for name, payload in events if name == "delta"]
        # 正常首片“你好，”不应因长密钥被缓冲，须独立即时推送；密钥前缀跨片仍被完整脱敏。
        self.assertGreaterEqual(len(deltas), 3)
        self.assertEqual(deltas[0], "你好，")
        self.assertEqual("".join(deltas), "你好，密钥测试:[REDACTED]，结束。")
        self.assertEqual(events[-1], ("done", {"answer": "你好，密钥测试:[REDACTED]，结束。"}))
        self.assertEqual(len(requests), 1)
        stats = llm_gateway_stats()
        self.assertEqual(stats["by_role"].get("chat"), 1)
        self.assertEqual(stats["total_tokens"], 20)

    def test_chat_stream_rejects_truncated_and_timeout_responses_with_error_event(self):
        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)

        with self._mock_stream(lambda _r: _sse_openai_response([_chunk("半截回答"), _chunk(None, finish_reason="length")])), \
             TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "截断测试"})
        events = _parse_sse_events(resp.text)
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "invalid_model_response")
        self.assertFalse(any(name == "done" for name, _ in events))

        with self._mock_stream(lambda _r: (_ for _ in ()).throw(httpx.ReadTimeout(FAKE_KEY))), \
             TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "超时测试"})
        events = _parse_sse_events(resp.text)
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "model_timeout")
        self.assertNotIn(FAKE_KEY, resp.text)

        # 未配置密钥时在建流前即返回标准 503 configuration_error
        broken_env = self.root / "empty.env"
        broken_env.write_text("", encoding="utf-8")
        app_bad = create_app(self.db, self.task_db, import_root=self.import_root, env_file=broken_env, embedded_worker=False)
        with patch.dict("os.environ", {"LLM_MODEL_ID": "", "LLM_API_KEY": "", "LLM_BASE_URL": ""}), \
             TestClient(app_bad, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "无配置"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"]["code"], "configuration_error")

    def test_task_events_sse_includes_retry_id_and_terminal_done(self):
        worker_started(self.task_db, "worker-test")
        submission = enqueue(self.task_db, "analysis", {"source": "reviews_csv", "product_ids": ["P1"]})
        task_id = submission["task"]["task_id"]
        claimed = claim_task(self.task_db, "worker-test")
        self.assertEqual(claimed["task_id"], task_id)
        finish_task(self.task_db, task_id, "worker-test", status="succeeded", result={"ok": True}, error=None)

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.get(f"/api/v1/tasks/{task_id}/events")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("retry: 1500", resp.text)
        self.assertIn("id: 1", resp.text)
        events = _parse_sse_events(resp.text)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "done")
        self.assertEqual(events[0][1]["status"], "succeeded")


class HybridRetrievalAndRerankerTests(unittest.TestCase):
    def test_hybrid_config_flags_and_validation(self):
        base = {"LLM_MODEL_ID": "m", "LLM_API_KEY": FAKE_KEY, "LLM_BASE_URL": "https://offline.invalid/v1"}
        default_cfg = AgentSettings.from_values(base)
        self.assertFalse(default_cfg.rag_hybrid_bm25)
        self.assertFalse(default_cfg.rag_rerank_enabled)
        self.assertEqual(default_cfg.rag_hybrid_rrf_k, 60)
        self.assertIsNone(default_cfg.rag_rerank_model_cache)

        enabled = AgentSettings.from_values({
            **base,
            "RAG_HYBRID_BM25": "1",
            "RAG_RERANK_ENABLED": "1",
            "RAG_HYBRID_RRF_K": "40",
            "RAG_RERANK_MODEL_CACHE": "/tmp/reranker",
        })
        self.assertTrue(enabled.rag_hybrid_bm25)
        self.assertTrue(enabled.rag_rerank_enabled)
        self.assertEqual(enabled.rag_hybrid_rrf_k, 40)
        self.assertEqual(enabled.rag_rerank_model_cache, "/tmp/reranker")

        with self.assertRaises(ConfigurationError):
            AgentSettings.from_values({**base, "RAG_HYBRID_RRF_K": "0"})

    def test_bm25_tokenization_rrf_fusion_and_lexical_semantic_rerank(self):
        tokens = tokenize_for_bm25("空调制冷很慢，型号 KFR35 噪音大")
        self.assertIn("制冷", tokens)
        self.assertIn("很慢", tokens)
        self.assertIn("kfr35", tokens)

        s1 = _make_source(1, quote="制冷很慢，开了半小时才凉快", review_text="制冷很慢，开了半小时才凉快，但物流很快")
        s2 = _make_source(2, quote="外机噪音很大，晚上睡不好", review_text="外机噪音很大，晚上睡不好")
        s3 = _make_source(3, quote="制冷速度非常慢，不凉快", review_text="制冷速度非常慢，不凉快")
        docs = [s1.opinions[0], s2.opinions[0], s3.opinions[0]]

        bm25_hits = BM25Index(docs).search("有没有制冷很慢的反馈？", top_k=5)
        self.assertEqual([h.document.review_id for h in bm25_hits[:2]], [1, 3])
        self.assertTrue(all(h.bm25_score > 0 for h in bm25_hits))

        # 向量通道把 s2 错排第 1、s1 排第 2（漏掉 s3），BM25 召回 s1 第 1、s3 第 2 -> RRF 融合后 s1 共识升至第 1 且 s3 被 BM25 救回
        vec_hits = [OpinionMatch(s2.opinions[0], 0.12), OpinionMatch(s1.opinions[0], 0.18)]
        fused = reciprocal_rank_fusion(vec_hits, bm25_hits, k=60, top_k=5)
        self.assertEqual(fused[0].document.review_id, 1)
        self.assertEqual(fused[0].channel, "hybrid")
        self.assertEqual(fused[0].vector_rank, 2)
        self.assertEqual(fused[0].bm25_rank, 1)
        by_rid = {row.document.review_id: row for row in fused}
        self.assertEqual(by_rid[2].channel, "vector")
        self.assertIsNone(by_rid[2].bm25_rank)
        self.assertEqual(by_rid[3].channel, "bm25")
        self.assertIsNone(by_rid[3].cosine_distance)

    def test_semantic_service_hybrid_bm25_with_lexical_and_cross_encoder_rerankers(self):
        s1 = _make_source(1, quote="制冷很慢")
        s2 = _make_source(2, quote="制热很快", sentiment="positive")
        s3 = _make_source(3, quote="空调制冷慢吞吞不凉快")

        matches = (OpinionMatch(s2.opinions[0], 0.10), OpinionMatch(s1.opinions[0], 0.16))
        snap = _HybridFakeSnapshot((s1, s2, s3), matches)
        service = SemanticRetrievalService(
            encoder=srtests.FakeEncoder([]),
            repository=srtests.FakeRepository([], snap),
            scope=srtests.OPTIONS,
        )

        result = service.search(
            {"question": "有没有制冷很慢的反馈？", "top_k": 3},
            hybrid_bm25=True,
            reranker=LexicalSemanticReranker(),
        )
        require_source_validated_result(result)
        payload = result.to_dict()
        self.assertEqual(payload["version"], "semantic-retrieval-v4")
        self.assertEqual(payload["hybrid_retrieval"]["rerank_policy"], LEXICAL_SEMANTIC_RERANK_POLICY)
        self.assertTrue(payload["hybrid_retrieval"]["bm25_enabled"])
        self.assertEqual(payload["evidence"][0]["review_id"], 1)
        self.assertEqual(payload["evidence"][0]["channel"], "hybrid")
        self.assertIn("rerank_score", payload["evidence"][0])
        self.assertIn("bm25_search_ms", service.timings)
        self.assertIn("rrf_fusion_ms", service.timings)
        self.assertIn("rerank_ms", service.timings)
        s3_row = next(r for r in payload["evidence"] if r["review_id"] == 3)
        self.assertEqual(s3_row["channel"], "bm25")
        self.assertIsNone(s3_row["cosine_distance"])

        # CrossEncoderReranker 注入打分将 s3 排至第 1
        cross_reranker = CrossEncoderReranker(
            score_fn=lambda pairs: [3.0 if "慢吞吞" in quote else -1.0 if "制热" in quote else 1.0 for _q, quote in pairs]
        )
        result_ce = service.search(
            {"question": "有没有制冷很慢的反馈？", "top_k": 3},
            hybrid_bm25=True,
            reranker=cross_reranker,
        )
        payload_ce = result_ce.to_dict()
        self.assertEqual(payload_ce["hybrid_retrieval"]["rerank_policy"], CROSS_ENCODER_RERANK_POLICY)
        self.assertEqual(payload_ce["evidence"][0]["review_id"], 3)
        self.assertGreater(payload_ce["evidence"][0]["rerank_score"], payload_ce["evidence"][1]["rerank_score"])

        # 非法 NaN 打分必须 fail-closed
        bad_reranker = CrossEncoderReranker(score_fn=lambda pairs: [float("nan")] * len(pairs))
        with self.assertRaises(SemanticRetrievalError) as caught:
            service.search({"question": "有没有制冷很慢的反馈？", "top_k": 3}, hybrid_bm25=True, reranker=bad_reranker)
        self.assertEqual(caught.exception.code, "rerank_failed")

    def test_execute_retrieval_and_validate_retrieval_support_v4_and_reject_tampering(self):
        s1 = _make_source(1, quote="制冷很慢")
        s2 = _make_source(2, quote="物流速度较慢", aspect="logistics")
        snap = _HybridFakeSnapshot(
            (s1, s2),
            (OpinionMatch(s2.opinions[0], 0.11), OpinionMatch(s1.opinions[0], 0.19)),
        )

        class FakeContext:
            question = "有没有制冷很慢的反馈？"
            connection = object()
            settings = replace(SETTINGS, rag_hybrid_bm25=True, rag_rerank_enabled=True)

            def semantic_service_factory(self, conn, scope):
                return SemanticRetrievalService(
                    encoder=srtests.FakeEncoder([]),
                    repository=srtests.FakeRepository([], snap),
                    scope=scope,
                )

        data = execute_retrieval(FakeContext(), srtests.OPTIONS)
        self.assertEqual(data["retrieval"]["version"], "semantic-retrieval-v4")
        self.assertEqual(data["retrieval"]["evidence"][0]["review_id"], 1)
        self.assertTrue(validate_retrieval(data, FakeContext.question))

        # 篡改 hybrid_retrieval 元数据或 rerank_score 时必须校验失败
        tampered = deepcopy(data)
        tampered["retrieval"]["evidence"][0]["rerank_score"] = "forged"
        self.assertFalse(validate_retrieval(tampered, FakeContext.question))

        tampered2 = deepcopy(data)
        tampered2["retrieval"]["evidence"][0]["channel"] = "bm25"  # 保留非 None cosine_distance
        self.assertFalse(validate_retrieval(tampered2, FakeContext.question))


if __name__ == "__main__":
    unittest.main()
