"""同步受控编排 + 隔离 SQLite；不依赖 Windows asyncio 或外部供应商。"""

from contextlib import closing, contextmanager
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
import test_review_agent as agent_fixture
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, get_task
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.store import connect_review_database


class QueryPlanTests(WorkbenchFixture, unittest.TestCase):
    scope = agent_fixture.ReviewAgentTests.scope

    @contextmanager
    def registry(self, question, limit=12, **scope_changes):
        scope = self.scope(**scope_changes)
        plan = build_query_plan(question, scope)
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            context = ReviewToolContext(connection, scope, self.root, limit,
                                        sentiment_intent=plan.sentiment, query_plan=plan)
            registry = create_review_registry(context)
            registry.execute_tool("get_analysis_context", "{}", automatic=True)
            yield plan, context, registry

    def execute_plan(self, plan, registry):
        for query in plan.requirements:
            reply = registry.execute_tool(query["tool"], json.dumps(query["parameters"])).to_dict()
            self.assertEqual(reply["status"], "success", reply)

    def test_simple_positive_only_aggregate_and_no_sql_write(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with self.registry("美的空调好评主要集中在哪些方面？") as (plan, context, registry):
            self.assertEqual(plan.analysis_type, "positive_aspects")
            self.assertEqual(plan.required_tools, ["get_positive_aspects"])
            self.assertEqual(plan.max_evidence_items, 0)
            self.execute_plan(plan, registry)
            self.assertEqual(context.model_tool_attempts, 1)
            self.assertEqual(context.query_executions, 2)  # 含一次自动上下文
            self.assertEqual(context.results["r1"]["data"]["summary"]["positive_review_count"], 3)
            self.assertEqual(registry.execute_tool("get_evidence", "{}").to_dict()["status"], "error")
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_task_type_is_independent_of_sentiment(self):
        for question, tools in [("差评主要集中在哪些方面", ["get_top_issues"]),
                                ("好评原文", ["get_evidence"]),
                                ("按月查看包装和物流问题变化", ["get_aspect_trend"]),
                                ("好评主要方面，并给出3条证据", ["get_positive_aspects", "get_evidence"])]:
            with self.subTest(question=question), self.registry(question) as (plan, context, registry):
                self.assertEqual(plan.required_tools, tools)
                self.execute_plan(plan, registry)
                self.assertTrue(plan.satisfied(context.results.values()))
                self.assertEqual(validate_report(context.results, list(context.results), query_plan=plan)["status"], "passed")

    def test_explicit_evidence_budget_and_forbidden_second_page(self):
        with self.registry("好评主要方面，并给出3条证据") as (plan, context, registry):
            self.execute_plan(plan, registry)
            self.assertEqual(context.evidence_queries, 1)
            self.assertLessEqual(context.evidence_items, 3)
            params = {**plan.requirements[-1]["parameters"], "page": 2}
            self.assertEqual(registry.execute_tool("get_evidence", json.dumps(params)).to_dict()["status"], "error")
            params.update(page=1, page_size=10)
            self.assertEqual(registry.execute_tool("get_evidence", json.dumps(params)).to_dict()["status"], "error")
            self.assertEqual(context.evidence_queries, 1)
        self.assertEqual(build_query_plan("给我10条物流差评", self.scope()).max_evidence_items, 10)
        self.assertIsNotNone(build_query_plan("给我100条差评原文", self.scope()).unsupported_reason)

    def test_explicit_next_page_preserves_filters_and_does_not_repeat(self):
        # 追加到临时 fixture，实际走导入、标注和证据 SQL；生产库不参与。
        source = self.root / "evidence.jsonl"
        source.write_text("\n".join(json.dumps({**self.records[0], "text": "物流太慢", "user_id": f"extra-{i}"})
                                    for i in range(6)), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source)
        for review in pending_reviews(self.db, **IDENTITY, limit=100):
            if review["text"] == "物流太慢":
                save_annotation(self.db, review, **IDENTITY, status="success", opinions=validate_opinions(
                    json.dumps({"opinions": [opinion("logistics", "negative", "物流太慢")]}), review["text"]))
        first_query = {"tool": "get_evidence", "parameters": {
            "sentiment": "negative", "aspect": "logistics", "window": "current", "page_size": 3, "page": 1}}
        ids = []
        for page in (1, 2):
            first_query["parameters"]["page"] = page
            question = ("给我看3条物流差评" if page == 1 else
                        "执行本轮已确认的只读查询\n" + json.dumps(first_query))
            with self.registry(question) as (plan, context, registry):
                self.execute_plan(plan, registry)
                evidence = context.results["r1"]["data"]
                self.assertEqual(evidence["page"], page)
                self.assertEqual(evidence["sentiment"], "negative")
                self.assertEqual(evidence["aspect"], "logistics")
                self.assertEqual(len(evidence["items"]), 3)
                ids.extend(item["review_id"] for item in evidence["items"])
        self.assertEqual(len(set(ids)), 6)

    def test_cache_normalizes_products_and_budget_still_stops_loop(self):
        with self.registry("好评主要方面", limit=4) as (plan, context, registry):
            with patch("data_analysis_agent.agents.review_tools.analytics.get_positive_aspects",
                       wraps=analytics.get_positive_aspects) as query:
                first = registry.execute_tool("get_positive_aspects", "{}").to_dict()["data"]
                for products in (["P3", "P1", "P2"], ["P1", "P2", "P3"]):
                    self.assertEqual(first, registry.execute_tool("get_positive_aspects", json.dumps({"product_ids": products})).to_dict()["data"])
                self.assertEqual(query.call_count, 1)
                self.assertEqual(context.cache_hits, 2)
                self.assertEqual(context.query_executions, 2)
                self.assertEqual(registry.execute_tool("get_positive_aspects", "{}").to_dict()["status"], "error")
                self.assertTrue(context.limit_reached)
                self.assertEqual(context.model_tool_attempts, 4)

    def test_controlled_before_after_unrequested_queries(self):
        question = "好评主要集中在哪些方面"
        counts = []
        for planned in (False, True):
            scope = self.scope()
            plan = build_query_plan(question, scope) if planned else None
            with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
                connection.execute("BEGIN")
                context = ReviewToolContext(connection, scope, self.root, 12,
                                            sentiment_intent="positive", query_plan=plan)
                registry = create_review_registry(context)
                registry.execute_tool("get_analysis_context", "{}", automatic=True)
                for name, params in [("get_positive_aspects", {}), ("get_evidence", {"sentiment": "positive", "page": 1}),
                                     ("get_evidence", {"sentiment": "positive", "page": 2}), ("get_data_quality", {})]:
                    registry.execute_tool(name, json.dumps(params))
                counts.append((context.model_tool_attempts, context.query_executions, context.evidence_queries))
        self.assertEqual(counts, [(4, 5, 2), (4, 2, 0)])

    def test_evidence_cannot_satisfy_ranking_and_overview_cannot_satisfy_trend(self):
        with self.registry("好评主要方面，并给出3条证据") as (plan, context, registry):
            self.execute_plan(plan, registry)
            evidence = {key: item for key, item in context.results.items() if item["kind"] == "evidence"}
            self.assertEqual(validate_report(evidence, list(evidence), sentiment_intent="positive", query_plan=plan)["status"], "failed")
        with self.registry("按月查看包装趋势") as (plan, context, registry):
            self.assertFalse(plan.satisfied(context.results.values()))
            fake = {"kind": "aspect_trend", "parameters": {"aspect": "packaging", "period": "month"},
                    "data": {"summary": {}}}
            self.assertFalse(plan.satisfied([fake]))

    def test_unsupported_positive_trend_and_narrow_evidence_stop_before_model(self):
        for question in ("最近半年好评趋势", "最近90天好评趋势"):
            with patch("data_analysis_agent.agents.reviews.create_llm") as model:
                result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["query_executions"], 0)
            self.assertEqual(result["model_calls"], 0)
            model.assert_not_called()

    def test_unrepresented_installation_uses_text_not_parent_dimension_counts(self):
        plan = build_query_plan("安装服务有哪些问题", self.scope())
        self.assertIsNone(plan.unsupported_reason)
        self.assertEqual(plan.required_tools, ['get_opinion_breakdown'])
        with patch("data_analysis_agent.agents.reviews.create_llm") as model:
            result = run_review_agent(self.db, "安装服务有哪些问题", scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result['results']['r1']['data']['total'], 0)
        self.assertEqual(result["model_calls"], 0)
        model.assert_not_called()

    def test_confirmed_followup_runs_without_planning_model_and_records_timings(self):
        explicit = "执行本轮已确认的只读查询\n" + json.dumps({"tool": "get_positive_aspects", "parameters": {}})
        with patch("data_analysis_agent.agents.reviews.create_llm", side_effect=AssertionError("followup must not create planner")):
            result = run_review_agent(self.db, explicit, scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["model_tool_attempts"], 0)
        self.assertEqual(result["query_executions"], 2)
        for key in ("planning_duration_ms", "query_duration_ms", "validation_duration_ms",
                    "compression_duration_ms", "answer_duration_ms", "total_duration_ms"):
            self.assertIn(key, result["performance"])
            self.assertGreaterEqual(result["performance"][key], 0)

    def run_sync(self, question, *, timeout=False, final_ids=None):
        llm = SimpleNamespace(budget=SimpleNamespace(calls=0))
        @contextmanager
        def create_llm(settings):
            yield llm
        def build(settings, context, model, **kwargs):
            registry = kwargs["tool_registry"]
            class ScriptedAgent:
                last_run = None
                def run(self, prompt, **kwargs):
                    llm.budget.calls += 1
                    self.last_run = SimpleNamespace(status="completed", model_calls=1, usage={})
                    self_outer.execute_plan(context.query_plan, registry)
                    if timeout:
                        raise TimeoutError("simulated timeout after successful tools")
                    return json.dumps({"status": "ok", "result_ids": final_ids or [k for k in context.results if k != "overview"]})
            return ScriptedAgent()
        self_outer = self
        with patch("data_analysis_agent.agents.reviews.create_llm", create_llm), patch("data_analysis_agent.agents.reviews.build_agent", build), \
                patch("openai.OpenAI", side_effect=RuntimeError("isolated planning fixture: no answer transport")):
            return run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)

    def test_sync_full_orchestration_and_window_scope_compatibility(self):
        result = self.run_sync("美的空调好评主要集中在哪些方面？")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertEqual(result["model_tool_attempts"], 1)
        self.assertNotIn("负面占比", result["answer"])
        self.assertEqual(self.run_sync("比较当前与基线窗口")["status"], "ok")

    def test_timeout_after_query_is_failed_and_diagnostics_preserved(self):
        result = self.run_sync("好评主要方面", timeout=True)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "timeout")
        self.assertIn("r1", result["results"])
        self.assertNotIn("answer", result)
        tasks = self.root / "tasks.db"
        init_tasks(tasks, self.db)
        worker = TaskWorker(self.db, tasks, import_root=self.root, env_file=self.root / "unused")
        identity = {**IDENTITY, "prompt_version": "agent-test"}
        submitted = enqueue(tasks, "analysis", {**self.scope(), "question": "好评主要方面", "model_identity": identity})
        worker_started(tasks, worker.worker_id)
        with patch("data_analysis_agent.jobs.worker.configured_agent_identity", return_value=(SETTINGS, identity)), \
             patch("data_analysis_agent.agents.reviews.run_review_agent", return_value=result):
            worker._execute(claim_task(tasks, worker.worker_id))
        task = get_task(tasks, submitted["task"]["task_id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["code"], "timeout")
        self.assertIn("r1", task["result"]["results"])


if __name__ == "__main__":
    unittest.main()
