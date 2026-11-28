"""P1-3 多轮上下文持久化与路由智能增强回归测试。"""

from __future__ import annotations

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

from test_agent import SETTINGS, completion
from test_review_workbench import IDENTITY, WorkbenchFixture
from data_analysis_agent.agents.chat import bounded_history_with_meta
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.api.app import create_app
from data_analysis_agent.core.database import close_database_pools
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.store import (
    claim_task,
    enqueue,
    finish_task,
    get_conversation_tasks,
    init_tasks,
    list_conversations,
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


class P13ContextAndRoutingTests(WorkbenchFixture, unittest.TestCase):
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
        worker_started(self.task_db, "worker-p13")

    def _run_and_save(self, question: str, *, conversation_id: str, context_task_id: str | None = None,
                      scope: dict | None = None, audit_split_threshold: int = 10_000_000):
        bound_scope = dict(scope or self.base_scope)
        params = {
            **bound_scope,
            "question": question,
            "conversation_id": conversation_id,
            **({"context_task_id": context_task_id} if context_task_id else {}),
            "selection_scope": bound_scope,
        }
        ctx = load_analysis_context(self.task_db, params, max_turns=8) if context_task_id else None
        result = run_review_agent(
            self.db,
            question,
            scope=bound_scope,
            settings=SETTINGS,
            conversation_context=ctx,
        )
        task_id = enqueue(self.task_db, "analysis", params)["task"]["task_id"]
        claim_task(self.task_db, "worker-p13")
        finish_task(
            self.task_db,
            task_id,
            "worker-p13",
            status="succeeded" if result["status"] in ("ok", "unsupported") else "failed",
            result=result,
            audit_split_threshold=audit_split_threshold,
        )
        return task_id, result

    def test_server_conversations_list_restore_lightweight_and_public_isolation(self):
        t1, _ = self._run_and_save(
            "这个商品主要有哪些问题？",
            conversation_id="conv-a",
            audit_split_threshold=100,  # 强制触发 task_audit_blobs 拆分
        )
        t2, _ = self._run_and_save(
            "那好评呢？",
            conversation_id="conv-a",
            context_task_id=t1,
        )
        t3, _ = self._run_and_save(
            "查看缺失正文、失败标注和覆盖率",
            conversation_id="conv-b",
        )
        # 非 Agent 的结构化分析任务（无 question / conversation_id）与导入任务不得混入会话列表。
        enqueue(self.task_db, "analysis", {**self.base_scope})
        enqueue(self.task_db, "import", {"reviews_path": "a.jsonl"})

        listed = list_conversations(self.task_db, limit=30)
        self.assertEqual(listed["total"], 2)
        self.assertEqual([item["conversation_id"] for item in listed["items"]], ["conv-b", "conv-a"])
        conv_a = next(item for item in listed["items"] if item["conversation_id"] == "conv-a")
        self.assertEqual(conv_a["turn_count"], 2)
        self.assertEqual(conv_a["title"], "这个商品主要有哪些问题？")
        self.assertEqual(conv_a["last_task_id"], t2)

        detail = get_conversation_tasks(self.task_db, "conv-a")
        self.assertEqual(detail["total"], 2)
        self.assertEqual([item["task_id"] for item in detail["items"]], [t1, t2])
        # 会话恢复走轻量读取（include_audit=False），保留 _audit_ref 而不强行水合重型审计块。
        self.assertIn("_audit_ref", detail["items"][0]["result"])

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            r_list = client.get("/api/v1/conversations")
            self.assertEqual(r_list.status_code, 200)
            self.assertEqual(r_list.json()["result"]["total"], 2)
            r_detail = client.get("/api/v1/conversations/conv-a")
            self.assertEqual(r_detail.status_code, 200)
            self.assertEqual(len(r_detail.json()["result"]["items"]), 2)
            self.assertEqual(client.get("/api/v1/conversations/missing-conv").status_code, 404)

        public_app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, public_only=True)
        with TestClient(public_app, base_url="http://127.0.0.1") as pub:
            self.assertEqual(pub.get("/api/v1/conversations").status_code, 404)
            self.assertEqual(pub.get("/api/v1/conversations/conv-a").status_code, 404)

    def test_context_lightweight_rehydration_history_and_8_turn_override(self):
        # Turn 1：完整六维负面排名（带审计拆分，验证轻量初筛 + 命中时按需水合 + ranking_basis）。
        t1, r1 = self._run_and_save(
            "这个商品主要有哪些问题？",
            conversation_id="conv-rank",
            audit_split_threshold=100,
        )
        self.assertEqual(r1["status"], "ok")
        calls: list[bool] = []
        from data_analysis_agent.jobs import analysis_context as ctx_mod
        original_get_task = ctx_mod.get_task

        def tracked_get_task(path, task_id, *, include_audit=True):
            calls.append(include_audit)
            return original_get_task(path, task_id, include_audit=include_audit)

        # 跨会话入口在轻量初筛即被拦截，绝不触发重型 include_audit=True 读取。
        with patch.object(ctx_mod, "get_task", side_effect=tracked_get_task):
            wrong_ctx = load_analysis_context(
                self.task_db,
                {**self.base_scope, "conversation_id": "other-conv", "context_task_id": t1, "selection_scope": self.base_scope},
            )
        self.assertIsNone(wrong_ctx)
        self.assertEqual(calls, [False])

        # 同会话 Turn 1 直接作为上文时，完整六维负面排名被恢复为 ranking_basis。
        ctx_t1 = load_analysis_context(
            self.task_db,
            {**self.base_scope, "conversation_id": "conv-rank", "context_task_id": t1, "selection_scope": self.base_scope},
        )
        self.assertIsNotNone(ctx_t1)
        self.assertEqual(ctx_t1["analysis_state"]["ranking_basis"]["source"]["task_id"], t1)

        # Turn 2：正向维度统计（更新 focus，不把旧轮负面排名伪装成当前焦点，同时在 validated_history 保留两轮）。
        t2, r2 = self._run_and_save("那好评呢？", conversation_id="conv-rank", context_task_id=t1)
        self.assertEqual(r2["status"], "ok")

        ctx_t2 = load_analysis_context(
            self.task_db,
            {**self.base_scope, "conversation_id": "conv-rank", "context_task_id": t2, "selection_scope": self.base_scope},
        )
        self.assertIsNotNone(ctx_t2)
        self.assertEqual(ctx_t2["validated"]["task_id"], t2)
        self.assertEqual(ctx_t2["analysis_state"]["focus"]["source"]["task_id"], t2)
        self.assertIsNone(ctx_t2["analysis_state"]["ranking_basis"])
        self.assertEqual([item["task_id"] for item in ctx_t2["validated_history"]], [t2, t1])

        # 显式传入 max_turns=6 时可回溯 6 轮（默认仍保持 4 轮兼容既有边界）。
        parent = None
        for idx in range(6):
            tid = enqueue(
                self.task_db,
                "analysis",
                {
                    **self.base_scope,
                    "question": f"失败轮 {idx}",
                    "conversation_id": "conv-deep",
                    **({"context_task_id": parent} if parent else {}),
                },
            )["task"]["task_id"]
            claim_task(self.task_db, "worker-p13")
            finish_task(self.task_db, tid, "worker-p13", status="failed", result={"agent": True, "status": "error"})
            parent = tid
        params = {**self.base_scope, "conversation_id": "conv-deep", "context_task_id": parent}
        self.assertEqual(load_analysis_context(self.task_db, params)["turns_read"], 4)
        self.assertEqual(load_analysis_context(self.task_db, params, max_turns=6)["turns_read"], 6)

    def test_question_scope_error_propagates_structured_clarification_choices(self):
        # 场景 1：仅规则模式下语义模糊提问 -> 返回 3 项结构化澄清选项
        ambiguous = run_review_agent(
            self.db,
            "帮我随便看看情况",
            scope=self.base_scope,
            settings=replace(SETTINGS, semantic_parser_enabled=False),
        )
        self.assertEqual(ambiguous["status"], "unsupported")
        self.assertEqual(ambiguous["public_answer"]["unsupported_code"], "clarification_needed")
        self.assertGreaterEqual(len(ambiguous["public_answer"]["clarification_choices"]), 3)

        # 场景 2：单商品差评主题追问缺少评价维度 -> 返回 6 大评价维度澄清按钮
        t1, _ = self._run_and_save("这个商品主要有哪些问题？", conversation_id="conv-clarify")
        ctx = load_analysis_context(
            self.task_db,
            {**self.base_scope, "conversation_id": "conv-clarify", "context_task_id": t1, "selection_scope": self.base_scope},
        )
        missing_aspect = run_review_agent(
            self.db,
            "差评具体体现在哪里？",
            scope=self.base_scope,
            settings=SETTINGS,
            conversation_context=ctx,
        )
        self.assertEqual(missing_aspect["status"], "unsupported")
        self.assertEqual(missing_aspect["public_answer"]["unsupported_code"], "clarification_needed")
        choices = missing_aspect["public_answer"]["clarification_choices"]
        self.assertEqual(len(choices), 6)
        self.assertTrue(any(item["label"] == "物流" and "物流差评" in item["question"] for item in choices))

    def test_expanded_fast_path_followups_and_enriched_llm_intent_context(self):
        t1, _ = self._run_and_save("这个商品主要有哪些问题？", conversation_id="conv-fast")
        ctx = load_analysis_context(
            self.task_db,
            {**self.base_scope, "conversation_id": "conv-fast", "context_task_id": t1, "selection_scope": self.base_scope},
        )
        products = [{"product_id": "P1", "title": "测试空调"}]

        for question, expected_tool in (
            ("和基线比呢？", "compare_time_windows"),
            ("那覆盖率呢？", "get_data_quality"),
            ("那星级分布呢？", "get_rating_distribution"),
        ):
            with self.subTest(question=question):
                diag = {}
                _, plan, direct = resolve_review_request(
                    question,
                    self.base_scope,
                    products,
                    settings=replace(SETTINGS, semantic_parser_enabled=False),
                    context=ctx,
                    diagnostics=diag,
                )
                self.assertTrue(direct)
                self.assertEqual(diag["model_calls"], 0)
                self.assertEqual(plan.required_tools, [expected_tool])

        # 原文条数快捷追问：在 evidence 上下文中“再来 5 条原文”零模型调用直达 page_size=5
        ev_ctx = {
            **ctx,
            "validated": {
                **ctx["validated"],
                "analysis_type": "evidence",
                "queries": [{"kind": "evidence", "parameters": {
                    "product_ids": ["P1"], "aspect": "logistics", "sentiment": "negative", "page": 1, "page_size": 3, "window": "current",
                }}],
            },
        }
        _, ev_plan, _ = resolve_review_request(
            "再来 5 条原文",
            self.base_scope,
            products,
            settings=replace(SETTINGS, semantic_parser_enabled=False),
            context=ev_ctx,
            diagnostics={},
        )
        self.assertEqual(ev_plan.required_tools, ["get_evidence"])
        self.assertEqual(ev_plan.requirements[0]["parameters"]["page_size"], 5)

        # 验证 _llm_intent 的 compact_context 携带了 validated_history 与 ranking_top_aspects
        captured_payloads = []

        def fake_handler(request):
            body = json.loads(request.content)
            captured_payloads.append(json.loads(body["messages"][-1]["content"]))
            return completion({
                "role": "assistant",
                "content": json.dumps({"query_type": "quality", "needs_clarification": False}),
            })

        def fake_client(**kwargs):
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(fake_handler)))

        with patch("openai.OpenAI", side_effect=fake_client):
            _, llm_plan, _ = resolve_review_request(
                "这批数据的整体健康度如何？",
                self.base_scope,
                products,
                settings=SETTINGS,
                context=ctx,
                diagnostics={},
            )
        self.assertEqual(llm_plan.required_tools, ["get_data_quality"])
        self.assertEqual(len(captured_payloads), 1)
        compact = captured_payloads[0]["compact_context"]
        self.assertIn("validated_history", compact)
        self.assertIn("ranking_top_aspects", compact)
        self.assertGreaterEqual(len(compact["validated_history"]), 1)

    def test_chat_stream_reports_history_truncation_metadata(self):
        history = [{"question": f"问题 {i}", "answer": f"回答 {i}"} for i in range(8)]
        selected, meta = bounded_history_with_meta(history)
        self.assertEqual(len(selected), 6)
        self.assertTrue(meta["truncated"])
        self.assertEqual(meta["included_turns"], 6)
        self.assertEqual(meta["total_turns"], 8)

        app = create_app(self.db, self.task_db, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with patch(
            "data_analysis_agent.api.routes.chat.stream_answer_message",
            return_value=iter(["收到。"]),
        ), TestClient(app, base_url="http://127.0.0.1") as client:
            resp = client.post("/api/v1/chat/stream", json={"message": "继续问", "history": history})
        self.assertEqual(resp.status_code, 200)
        events = _parse_sse(resp.text)
        self.assertEqual(events[0][0], "start")
        self.assertTrue(events[0][1]["history_truncated"])
        self.assertEqual(events[0][1]["included_history_turns"], 6)


@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_ADMIN_URL"),
    "set TEST_POSTGRES_ADMIN_URL to run live PostgreSQL conversation tests",
)
class PostgresServerConversationsLiveTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ["TEST_POSTGRES_ADMIN_URL"]
        self.db_name = f"p1_3_conv_{uuid.uuid4().hex[:10]}"
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
        close_database_pools(self.target)
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.db_name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{self.db_name}"')

    def test_live_pg_conversations_endpoints_list_and_restore(self):
        worker_started(self.target, "pg-conv-worker")
        base_params = {
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
        }
        t1 = enqueue(self.target, "analysis", {**base_params, "question": "PG 会话首轮", "conversation_id": "pg-conv-1"})["task"]["task_id"]
        claim_task(self.target, "pg-conv-worker")
        finish_task(self.target, t1, "pg-conv-worker", status="succeeded", result={"agent": True, "status": "ok"})

        t2 = enqueue(self.target, "analysis", {
            **base_params, "question": "PG 会话追问", "conversation_id": "pg-conv-1", "context_task_id": t1,
        })["task"]["task_id"]
        claim_task(self.target, "pg-conv-worker")
        finish_task(self.target, t2, "pg-conv-worker", status="succeeded", result={"agent": True, "status": "ok"})

        app = create_app(self.target, self.target, import_root=self.import_root, env_file=self.env_file, embedded_worker=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            res_list = client.get("/api/v1/conversations")
            self.assertEqual(res_list.status_code, 200)
            items = res_list.json()["result"]["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["conversation_id"], "pg-conv-1")
            self.assertEqual(items[0]["turn_count"], 2)

            res_detail = client.get("/api/v1/conversations/pg-conv-1")
            self.assertEqual(res_detail.status_code, 200)
            self.assertEqual([row["task_id"] for row in res_detail.json()["result"]["items"]], [t1, t2])


if __name__ == "__main__":
    unittest.main()
