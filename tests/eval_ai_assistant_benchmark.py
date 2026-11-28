"""P1-4 离线可重复评估基准套件（零付费模型调用）。

量化评估三大核心维度并校验回归门禁：
1. 混合语义检索召回质量（P1-2）：对比纯向量基线与 Multi-Query + BM25 + RRF + 极性惩罚重排的 Recall@5 / MRR@5 / nDCG@5 / 极性错召回率；
2. 多轮路由、结构化澄清与安全边界（P1-1 & P1-3）：评估路由准确率、零模型快路径命中率、结构化澄清覆盖率与越界零泄露率；
3. 传输唤醒与轻量载荷效率（P0 & P1-1 & P1-3）：评估高并发异步 SSE 唤醒延迟、审计拆分轻量压缩比与跨会话轻筛零水合率。

可直接执行：
    python tests/eval_ai_assistant_benchmark.py --output .cache/p1-eval/benchmark-report.json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs import analysis_context as ctx_mod
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.events import async_wait_task_update, notify_task_update, task_event_version
from data_analysis_agent.jobs.store import claim_task, enqueue, finish_task, get_task, init_tasks, worker_started
from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.hybrid_retrieval import (
    BM25Index,
    LexicalSemanticReranker,
    RerankItem,
    reciprocal_rank_fusion,
)
from data_analysis_agent.reviews.loader import DATASET_VERSION, SOURCE
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SemanticRetrievalService
from test_agent import SETTINGS
from test_review_workbench import IDENTITY, WorkbenchFixture


# ---------------------------------------------------------------------------
# Benchmark 1: Hybrid Semantic Retrieval Quality (P1-2)
# ---------------------------------------------------------------------------

_CORPUS_SPEC = [
    # (review_id, aspect, sentiment, quote, review_text, topic_tags)
    (101, "effectiveness", "negative", "制冷效果不行，开半天屋里还是热", "买了两天，制冷效果不行，开半天屋里还是热，吹出来的风不凉。", ("cool",)),
    (102, "effectiveness", "positive", "制冷很快，三分钟就凉快了", "开机后制冷很快，三分钟就凉快了，非常满意。", ("cool",)),
    (103, "effectiveness", "negative", "完全不制冷还吹热风", "大热天开到16度完全不制冷还吹热风，太失望了。", ("cool",)),
    (104, "quality", "negative", "外机嗡嗡响声音太大", "晚上外机嗡嗡响声音太大，吵得睡不着觉。", ("noise",)),
    (105, "quality", "positive", "运行非常静音几乎听不到噪音", "夜间睡眠模式运行非常静音几乎听不到噪音，体验很好。", ("noise",)),
    (106, "quality", "negative", "内机滴水漏水把墙皮泡湿了", "用了半个月内机滴水漏水把墙皮泡湿了，需要维修。", ("leak",)),
    (107, "quality", "positive", "安装扎实严密完全不漏水", "师傅安装扎实严密完全不漏水，做工不错。", ("leak",)),
    (108, "quality", "negative", "遥控器按键失灵不灵敏", "自带的遥控器按键失灵不灵敏，按好几次才有反应。", ("remote",)),
    (109, "quality", "negative", "铜管偷工减料太细容易折", "拆开看连接铜管偷工减料太细容易折，用料缩水。", ("pipe",)),
    (110, "logistics", "negative", "送货太慢等了一周才派送", "下单后送货太慢等了一周才派送，催了好几次。", ("delivery",)),
    (111, "logistics", "positive", "物流神速次日达送货上门", "物流神速次日达送货上门，快递员很负责。", ("delivery",)),
    (112, "logistics", "negative", "预约配送爽约放鸽子", "说好周六送货结果预约配送爽约放鸽子，白等一天。", ("delivery",)),
    (113, "packaging", "negative", "包装箱破损有压痕泡沫都碎了", "收到时外包装箱破损有压痕泡沫都碎了，担心磕碰。", ("pack",)),
    (114, "packaging", "positive", "双层纸箱包装完好无损", "双层纸箱包装完好无损，边角保护很到位。", ("pack",)),
    (115, "packaging", "negative", "外包装湿透浸水严重", "雨天运输外包装湿透浸水严重，纸箱都软了。", ("pack",)),
    (116, "service", "negative", "客服推诿不处理售后态度差", "反馈故障后客服推诿不处理售后态度差，来回踢皮球。", ("cs",)),
    (117, "service", "positive", "客服响应很快态度很好", "咨询安装问题客服响应很快态度很好，解答耐心。", ("cs",)),
    (118, "service", "negative", "安装师傅乱收高空费和材料费", "上门安装师傅乱收高空费和材料费，提前没说明。", ("install_fee",)),
    (119, "price", "negative", "刚买就降价保价退差价被拒绝", "刚买就降价保价退差价被拒绝，体验很差。", ("price_drop",)),
    (120, "price", "positive", "性价比很高价格实惠划算", "对比好几个品牌这款性价比很高价格实惠划算。", ("price_drop",)),
    (121, "effectiveness", "negative", "制热速度慢冬天屋里暖不起来", "冬天制热速度慢冬天屋里暖不起来，出风口温度低。", ("heat",)),
    (122, "effectiveness", "positive", "制热效果强劲冬天很暖和", "制热效果强劲冬天很暖和，升温很快。", ("heat",)),
    (123, "quality", "negative", "压缩机异响伴随剧烈震动", "高风速时压缩机异响伴随剧烈震动，窗户跟着抖。", ("noise",)),
    (124, "service", "negative", "预约安装排队太久一直没人联系", "到货四天了预约安装排队太久一直没人联系。", ("install_fee",)),
]

# 每条查询定义：query, gold_ids, opposite_polarity_ids, vector_ranking (模拟纯向量常犯的极性混淆与长尾词漏召回)
_RETRIEVAL_QUERIES = [
    {
        "query": "这个商品不制冷的问题有哪些？",
        "gold_ids": {101, 103},
        "opposite_ids": {102},
        # 模拟稠密向量典型痛点：正向“制冷很快”与“不制冷”主题词高度接近导致排第一
        "vector_order": [(102, 0.14), (101, 0.18), (122, 0.23), (103, 0.25), (121, 0.29)],
    },
    {
        "query": "用户吐槽噪音大吵得睡不着",
        "gold_ids": {104, 123},
        "opposite_ids": {105},
        "vector_order": [(105, 0.13), (104, 0.17), (123, 0.24), (101, 0.34), (106, 0.39)],
    },
    {
        "query": "有没有内机漏水滴水的差评",
        "gold_ids": {106},
        "opposite_ids": {107},
        "vector_order": [(107, 0.12), (106, 0.16), (115, 0.26), (104, 0.35), (108, 0.41)],
    },
    {
        "query": "遥控器失灵不灵敏",
        "gold_ids": {108},
        "opposite_ids": set(),
        # 长尾精确部件词在纯向量里掉出 Top-5，依赖 BM25 词法捞回
        "vector_order": [(104, 0.29), (101, 0.31), (116, 0.33), (123, 0.36), (106, 0.38)],
    },
    {
        "query": "铜管偷工减料用料太细",
        "gold_ids": {109},
        "opposite_ids": set(),
        "vector_order": [(106, 0.28), (107, 0.30), (114, 0.33), (104, 0.36), (120, 0.39)],
    },
    {
        "query": "送货慢物流派送延迟",
        "gold_ids": {110, 112},
        "opposite_ids": {111},
        "vector_order": [(111, 0.15), (110, 0.19), (112, 0.24), (124, 0.29), (113, 0.34)],
    },
    {
        "query": "包装破损泡沫碎了",
        "gold_ids": {113, 115},
        "opposite_ids": {114},
        "vector_order": [(114, 0.14), (113, 0.18), (115, 0.23), (110, 0.33), (111, 0.37)],
    },
    {
        "query": "客服售后推诿态度差",
        "gold_ids": {116},
        "opposite_ids": {117},
        "vector_order": [(117, 0.13), (116, 0.17), (124, 0.25), (118, 0.29), (119, 0.34)],
    },
    {
        "query": "安装乱收高空费和材料费",
        "gold_ids": {118},
        "opposite_ids": set(),
        "vector_order": [(124, 0.24), (107, 0.27), (119, 0.31), (120, 0.34), (116, 0.37)],
    },
    {
        "query": "买完降价不给保价退差价",
        "gold_ids": {119},
        "opposite_ids": {120},
        "vector_order": [(120, 0.16), (119, 0.20), (118, 0.29), (116, 0.33), (117, 0.38)],
    },
    {
        "query": "冬天制热慢暖不起来",
        "gold_ids": {121},
        "opposite_ids": {122},
        "vector_order": [(122, 0.14), (121, 0.19), (101, 0.24), (103, 0.28), (102, 0.33)],
    },
    {
        "query": "预约安装放鸽子或者没人联系",
        "gold_ids": {112, 124},
        "opposite_ids": set(),
        "vector_order": [(117, 0.22), (110, 0.26), (118, 0.29), (112, 0.31), (116, 0.35)],
    },
]


def _build_eval_documents():
    identity = (IDENTITY["model"], IDENTITY["base_url"], IDENTITY["prompt_version"])
    profile = content_sha256(list(identity))
    docs_by_rid = {}
    texts_by_rid = {}
    for rid, aspect, sentiment, quote, review_text, _tags in _CORPUS_SPEC:
        start = review_text.index(quote)
        row = {
            "source": SOURCE,
            "dataset_version": DATASET_VERSION,
            "product_id": "P1",
            "review_id": rid,
            "annotation_id": 2000 + rid,
            "human_revision_id": None,
            "processing_status": "success",
            "human_status": "unreviewed",
            "error_code": None,
            "opinion_source": "model",
            "review_content_sha256": hashlib.sha256(review_text.encode("utf-8")).hexdigest(),
            "published_at": f"2023-01-{(rid % 20) + 1:02d}T12:00:00+00:00",
            "review_text": review_text,
            "import_run_id": 1,
            "line_number": rid,
            "file_sha256": "1" * 64,
            "aspect": aspect,
            "sentiment": sentiment,
            "quote": quote,
            "quote_start": start,
            "quote_end": start + len(quote),
            "model_opinion_id": rid * 10,
            "human_opinion_id": None,
        }
        doc = _document(row, identity, profile)
        docs_by_rid[rid] = doc
        texts_by_rid[rid] = review_text
    return docs_by_rid, texts_by_rid


def _eval_ranking_metrics(ranked_ids: list[int], gold_ids: set[int], opposite_ids: set[int], k: int = 5) -> dict:
    top_k = ranked_ids[:k]
    hits = [rid for rid in top_k if rid in gold_ids]
    recall = len(hits) / max(1, len(gold_ids))
    mrr = 0.0
    for idx, rid in enumerate(top_k, 1):
        if rid in gold_ids:
            mrr = 1.0 / idx
            break
    dcg = sum((1.0 / math.log2(idx + 1)) for idx, rid in enumerate(top_k, 1) if rid in gold_ids)
    ideal_len = min(k, len(gold_ids))
    idcg = sum(1.0 / math.log2(idx + 1) for idx in range(1, ideal_len + 1))
    ndcg = dcg / idcg if idcg > 0 else 0.0
    polarity_at_1 = 1.0 if (top_k and top_k[0] in opposite_ids) else 0.0
    polarity_at_3 = 1.0 if any(rid in opposite_ids for rid in top_k[:3]) else 0.0
    return {
        "recall_at_5": recall,
        "mrr_at_5": mrr,
        "ndcg_at_5": ndcg,
        "polarity_confusion_at_1": polarity_at_1,
        "polarity_confusion_at_3": polarity_at_3,
    }


def run_retrieval_benchmark() -> dict:
    docs_by_rid, texts_by_rid = _build_eval_documents()
    bm25 = BM25Index(tuple(docs_by_rid.values()))
    reranker = LexicalSemanticReranker()

    baseline_rows = []
    hybrid_rows = []
    bm25_rescued_gold = 0

    for case in _RETRIEVAL_QUERIES:
        query = case["query"]
        gold = case["gold_ids"]
        opposite = case["opposite_ids"]
        vector_matches = tuple(OpinionMatch(docs_by_rid[rid], dist) for rid, dist in case["vector_order"])
        baseline_ids = [rid for rid, _ in case["vector_order"]]
        baseline_rows.append(_eval_ranking_metrics(baseline_ids, gold, opposite, k=5))

        lexical_matches = bm25.search(query, top_k=10)
        fused = reciprocal_rank_fusion(vector_matches, lexical_matches, k=60, top_k=10)
        rerank_items = [
            RerankItem(
                key=item.document.stable_versioned_opinion_key,
                quote=item.document.quote,
                review_text=texts_by_rid[item.document.review_id],
                cosine_distance=item.cosine_distance,
                bm25_score=item.bm25_score,
                rrf_score=item.rrf_score,
                channel=item.channel,
            )
            for item in fused
        ]
        score_map = reranker.score(query, rerank_items)
        ordered = sorted(fused, key=lambda item: (-score_map[item.document.stable_versioned_opinion_key], item.document.review_id))
        hybrid_ids = [item.document.review_id for item in ordered[:5]]
        hybrid_rows.append(_eval_ranking_metrics(hybrid_ids, gold, opposite, k=5))
        if any(rid in gold and rid not in baseline_ids for rid in hybrid_ids):
            bm25_rescued_gold += 1

    def _avg(rows, key):
        return round(sum(row[key] for row in rows) / len(rows), 4)

    baseline_summary = {key: _avg(baseline_rows, key) for key in baseline_rows[0]}
    hybrid_summary = {key: _avg(hybrid_rows, key) for key in hybrid_rows[0]}
    gates = {
        "hybrid_recall_at_5_ge_0_90": hybrid_summary["recall_at_5"] >= 0.90,
        "hybrid_beats_baseline_recall": hybrid_summary["recall_at_5"] > baseline_summary["recall_at_5"],
        "hybrid_beats_baseline_mrr": hybrid_summary["mrr_at_5"] > baseline_summary["mrr_at_5"],
        "hybrid_zero_polarity_confusion_at_1": hybrid_summary["polarity_confusion_at_1"] == 0.0,
        "hybrid_reduces_polarity_confusion_at_3": (
            hybrid_summary["polarity_confusion_at_3"] < baseline_summary["polarity_confusion_at_3"]
        ),
    }
    return {
        "queries_evaluated": len(_RETRIEVAL_QUERIES),
        "bm25_rescued_gold_queries": bm25_rescued_gold,
        "baseline_vector_only": baseline_summary,
        "p1_2_hybrid_rerank": hybrid_summary,
        "gates": gates,
        "passed": all(gates.values()),
    }


# ---------------------------------------------------------------------------
# Benchmark 2: Multi-Turn Routing, Clarification & Safety Guard (P1-1 & P1-3)
# ---------------------------------------------------------------------------

class _EvalWorkbench(WorkbenchFixture):
    """独立于 unittest.TestCase 运行的 WorkbenchFixture 封装，确保临时目录可靠回收。"""

    def __init__(self):
        self._cleanups = []

    def addCleanup(self, func, *args, **kwargs):
        self._cleanups.append((func, args, kwargs))

    def cleanup(self):
        for func, args, kwargs in reversed(self._cleanups):
            func(*args, **kwargs)
        self._cleanups.clear()


def run_routing_and_safety_benchmark() -> dict:
    fixture = _EvalWorkbench()
    fixture.setUp()
    try:
        init_monitoring(fixture.db)
        task_db = fixture.root / "eval_tasks.sqlite3"
        init_tasks(task_db, fixture.db)
        worker_started(task_db, "eval-worker")
        base_scope = {
            **IDENTITY,
            "product_ids": ["P1"],
            "source": SOURCE,
            "dataset_version": DATASET_VERSION,
            "date_from": "2023-01-01",
            "date_to": "2023-01-06",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
        }
        rules_only = replace(SETTINGS, semantic_parser_enabled=False)
        products = [{"product_id": "P1", "title": "评测空调"}]

        def save_turn(question: str, *, conversation_id: str, context_task_id: str | None = None):
            params = {
                **base_scope,
                "question": question,
                "conversation_id": conversation_id,
                **({"context_task_id": context_task_id} if context_task_id else {}),
                "selection_scope": base_scope,
            }
            ctx = load_analysis_context(task_db, params, max_turns=8) if context_task_id else None
            res = run_review_agent(fixture.db, question, scope=base_scope, settings=rules_only, conversation_context=ctx)
            tid = enqueue(task_db, "analysis", params)["task"]["task_id"]
            claim_task(task_db, "eval-worker")
            finish_task(
                task_db,
                tid,
                "eval-worker",
                status="succeeded" if res["status"] in ("ok", "unsupported") else "failed",
                result=res,
            )
            return tid, res

        t1, r1 = save_turn("这个商品主要有哪些问题？", conversation_id="eval-conv")
        t2, r2 = save_turn("那好评呢？", conversation_id="eval-conv", context_task_id=t1)
        ctx_t1 = load_analysis_context(
            task_db,
            {**base_scope, "conversation_id": "eval-conv", "context_task_id": t1, "selection_scope": base_scope},
        )
        ctx_t2 = load_analysis_context(
            task_db,
            {**base_scope, "conversation_id": "eval-conv", "context_task_id": t2, "selection_scope": base_scope},
        )

        # 1. 快路径路由断言
        fast_path_checks = []
        fast_path_cases = [
            ("和基线比呢？", ctx_t1, "compare_time_windows"),
            ("那覆盖率呢？", ctx_t1, "get_data_quality"),
            ("那星级分布呢？", ctx_t1, "get_rating_distribution"),
        ]
        for question, ctx, expected_tool in fast_path_cases:
            diag = {}
            _, plan, direct = resolve_review_request(
                question, base_scope, products, settings=rules_only, context=ctx, diagnostics=diag
            )
            fast_path_checks.append(
                bool(direct) and diag.get("model_calls", 0) == 0 and plan.required_tools == [expected_tool]
            )
        ev_ctx = {
            **ctx_t1,
            "validated": {
                **ctx_t1["validated"],
                "analysis_type": "evidence",
                "queries": [{"kind": "evidence", "parameters": {
                    "product_ids": ["P1"], "aspect": "logistics", "sentiment": "negative",
                    "page": 1, "page_size": 3, "window": "current",
                }}],
            },
        }
        diag_ev = {}
        _, ev_plan, ev_direct = resolve_review_request(
            "再来 5 条原文", base_scope, products, settings=rules_only, context=ev_ctx, diagnostics=diag_ev
        )
        fast_path_checks.append(
            (bool(ev_direct) or ev_plan.deterministic() or ev_plan.analysis_type == "evidence")
            and diag_ev.get("model_calls", 0) == 0
            and ev_plan.required_tools == ["get_evidence"]
            and ev_plan.requirements[0]["parameters"]["page_size"] == 5
        )
        fast_path_checks.append(r1["status"] == "ok" and r1["total_model_calls"] == 0)
        fast_path_checks.append(r2["status"] == "ok" and r2["total_model_calls"] == 0)

        # 2. 结构化澄清覆盖率
        clarify_checks = []
        amb = run_review_agent(fixture.db, "帮我随便看看情况", scope=base_scope, settings=rules_only)
        clarify_checks.append(
            amb["status"] == "unsupported"
            and amb["public_answer"]["unsupported_code"] == "clarification_needed"
            and len(amb["public_answer"].get("clarification_choices", [])) >= 3
        )
        missing_aspect = run_review_agent(
            fixture.db, "差评具体体现在哪里？", scope=base_scope, settings=rules_only, conversation_context=ctx_t1
        )
        clarify_checks.append(
            missing_aspect["status"] == "unsupported"
            and missing_aspect["public_answer"]["unsupported_code"] == "clarification_needed"
            and len(missing_aspect["public_answer"].get("clarification_choices", [])) == 6
        )

        # 3. 安全边界与零泄露守卫
        safety_checks = []
        # 3a. 跨会话拒绝恢复
        cross_ctx = load_analysis_context(
            task_db,
            {**base_scope, "conversation_id": "other-conv", "context_task_id": t1, "selection_scope": base_scope},
        )
        safety_checks.append(cross_ctx is None)
        # 3b. 新一轮非排名焦点清空旧 ranking_basis，同时保留 validated_history
        safety_checks.append(
            ctx_t2 is not None
            and ctx_t2["analysis_state"]["ranking_basis"] is None
            and len(ctx_t2["validated_history"]) == 2
        )
        # 3c. 提及不存在的外部商品时拒答且不静默退回当前商品
        missing_prod = run_review_agent(
            fixture.db, "格力KFR-35GW空调差评有哪些？", scope=base_scope, settings=rules_only
        )
        safety_checks.append(missing_prod["status"] == "unsupported" and missing_prod["query_executions"] == 0)

        routing_accuracy = round(sum(fast_path_checks) / len(fast_path_checks), 4)
        clarification_coverage = round(sum(clarify_checks) / len(clarify_checks), 4)
        safety_pass_rate = round(sum(safety_checks) / len(safety_checks), 4)
        gates = {
            "routing_accuracy_eq_1": routing_accuracy == 1.0,
            "clarification_choices_coverage_eq_1": clarification_coverage == 1.0,
            "safety_guard_pass_rate_eq_1": safety_pass_rate == 1.0,
        }
        return {
            "fast_path_cases": len(fast_path_checks),
            "clarification_cases": len(clarify_checks),
            "safety_cases": len(safety_checks),
            "routing_accuracy": routing_accuracy,
            "clarification_choices_coverage": clarification_coverage,
            "safety_guard_pass_rate": safety_pass_rate,
            "gates": gates,
            "passed": all(gates.values()),
        }
    finally:
        fixture.cleanup()


# ---------------------------------------------------------------------------
# Benchmark 3: Transport Wake Latency & Lightweight Payload Efficiency
# ---------------------------------------------------------------------------

def run_transport_and_payload_benchmark() -> dict:
    # 1. 高并发异步 SSE 唤醒延迟（20 个并发协程订阅者）
    async def _measure_async_wake(concurrency: int = 20) -> list[float]:
        latencies = []
        task_id = "eval-sse-wake"
        version = task_event_version(task_id)

        async def _waiter():
            t0 = perf_counter()
            updated = await async_wait_task_update(task_id, version, timeout=2.0)
            return (perf_counter() - t0) * 1000, updated > version

        waiters = [asyncio.create_task(_waiter()) for _ in range(concurrency)]
        await asyncio.sleep(0.01)
        t_notify = perf_counter()
        notify_task_update(task_id)
        results = await asyncio.gather(*waiters)
        notify_to_done_ms = (perf_counter() - t_notify) * 1000
        for _, woke in results:
            if woke:
                latencies.append(round(notify_to_done_ms, 2))
        return latencies

    wake_samples = asyncio.run(_measure_async_wake(20))
    wake_p95_ms = sorted(wake_samples)[int((len(wake_samples) - 1) * 0.95)] if wake_samples else 9999.0

    # 2. include_audit=False 载荷压缩比 & 跨会话轻筛零水合
    fixture = _EvalWorkbench()
    fixture.setUp()
    try:
        init_monitoring(fixture.db)
        task_db = fixture.root / "perf_tasks.sqlite3"
        init_tasks(task_db, fixture.db)
        worker_started(task_db, "perf-worker")
        base_scope = {
            **IDENTITY,
            "product_ids": ["P1"],
            "source": SOURCE,
            "dataset_version": DATASET_VERSION,
            "date_from": "2023-01-01",
            "date_to": "2023-01-06",
            "baseline_from": "2022-12-01",
            "baseline_to": "2022-12-31",
        }
        res = run_review_agent(
            fixture.db,
            "这个商品主要有哪些问题？",
            scope=base_scope,
            settings=replace(SETTINGS, semantic_parser_enabled=False),
        )
        # 附加典型大体积审计追踪以测量拆分压缩效果
        res["trace"] = [{"event": "tool_call", "tool": "get_evidence", "status": "success", "detail": "x" * 500} for _ in range(100)]
        tid = enqueue(task_db, "analysis", {**base_scope, "question": "问题分布", "conversation_id": "perf-conv"})["task"]["task_id"]
        claim_task(task_db, "perf-worker")
        finish_task(task_db, tid, "perf-worker", status="succeeded", result=res, audit_split_threshold=256)

        full_bytes = len(json.dumps(get_task(task_db, tid, include_audit=True), ensure_ascii=False).encode("utf-8"))
        light_bytes = len(json.dumps(get_task(task_db, tid, include_audit=False), ensure_ascii=False).encode("utf-8"))
        reduction_ratio = round(1.0 - (light_bytes / max(1, full_bytes)), 4)

        audit_hydration_calls = []
        orig_get_task = ctx_mod.get_task

        def tracked_get_task(path, task_id, *, include_audit=True):
            audit_hydration_calls.append(include_audit)
            return orig_get_task(path, task_id, include_audit=include_audit)

        with patch.object(ctx_mod, "get_task", side_effect=tracked_get_task):
            load_analysis_context(
                task_db,
                {**base_scope, "conversation_id": "other-conv", "context_task_id": tid, "selection_scope": base_scope},
            )
        zero_rehydration_on_reject = audit_hydration_calls == [False]

        gates = {
            "async_sse_all_waiters_woken": len(wake_samples) == 20,
            "async_sse_p95_under_500ms": wake_p95_ms < 500.0,
            "lightweight_payload_reduces_at_least_30pct": reduction_ratio >= 0.30,
            "cross_conv_rejects_without_audit_hydration": zero_rehydration_on_reject,
        }
        return {
            "concurrent_waiters_woken": len(wake_samples),
            "async_sse_wake_p95_ms": wake_p95_ms,
            "full_task_bytes": full_bytes,
            "lightweight_task_bytes": light_bytes,
            "payload_reduction_ratio": reduction_ratio,
            "zero_rehydration_on_reject": zero_rehydration_on_reject,
            "gates": gates,
            "passed": all(gates.values()),
        }
    finally:
        fixture.cleanup()


def run_all_benchmarks() -> dict:
    started = perf_counter()
    retrieval = run_retrieval_benchmark()
    routing = run_routing_and_safety_benchmark()
    transport = run_transport_and_payload_benchmark()
    report = {
        "schema_version": "p1-eval-v1",
        "generated_at": utc_now(),
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "all_passed": retrieval["passed"] and routing["passed"] and transport["passed"],
        "retrieval": retrieval,
        "routing_and_safety": routing,
        "transport_and_payload": transport,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 AI 助手 P1-4 离线评估基准并校验回归门禁。")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".cache/p1-eval/benchmark-report.json",
        help="JSON 报告输出路径（默认写入 .cache/p1-eval/benchmark-report.json）",
    )
    args = parser.parse_args()
    report = run_all_benchmarks()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ret = report["retrieval"]
    rt = report["routing_and_safety"]
    tr = report["transport_and_payload"]
    print(f"=== AI Assistant P1-4 Offline Benchmark ({report['elapsed_ms']} ms) ===")
    print(
        f"[Retrieval] Recall@5: {ret['baseline_vector_only']['recall_at_5']:.3f} -> {ret['p1_2_hybrid_rerank']['recall_at_5']:.3f} | "
        f"MRR@5: {ret['baseline_vector_only']['mrr_at_5']:.3f} -> {ret['p1_2_hybrid_rerank']['mrr_at_5']:.3f} | "
        f"Polarity@1: {ret['baseline_vector_only']['polarity_confusion_at_1']:.3f} -> {ret['p1_2_hybrid_rerank']['polarity_confusion_at_1']:.3f} | "
        f"PASS={ret['passed']}"
    )
    print(
        f"[Routing]   Accuracy: {rt['routing_accuracy']:.3f} | "
        f"ClarificationChoices: {rt['clarification_choices_coverage']:.3f} | "
        f"SafetyGuard: {rt['safety_guard_pass_rate']:.3f} | PASS={rt['passed']}"
    )
    print(
        f"[Transport] AsyncSSE P95: {tr['async_sse_wake_p95_ms']} ms ({tr['concurrent_waiters_woken']}/20) | "
        f"PayloadReduction: {tr['payload_reduction_ratio'] * 100:.1f}% | PASS={tr['passed']}"
    )
    print(f"Report written to: {args.output}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
