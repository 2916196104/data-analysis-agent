"""阶段 6：隔离 SQLite、真实 SQL、内存 Mock 模型；无外部服务。"""
from contextlib import closing
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
import test_review_agent as agent_fixture
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews import analytics


def confirmed(tool, **parameters):
    return "执行本轮已确认的只读查询\n" + json.dumps({"tool": tool, "parameters": parameters})


class ContextPerformanceTests(WorkbenchFixture, unittest.TestCase):
    scope = agent_fixture.ReviewAgentTests.scope

    def run_confirmed(self, tool, **parameters):
        with patch("data_analysis_agent.agents.reviews.create_llm", side_effect=AssertionError("no planner for confirmed query")):
            return run_review_agent(self.db, confirmed(tool, **parameters), scope=self.scope(), settings=SETTINGS)

    def test_sentiment_switch_and_single_aspect_public_answer(self):
        positive = self.run_confirmed("get_positive_aspects")
        negative = self.run_confirmed("get_top_issues")
        logistics = self.run_confirmed("get_top_issues", aspect="logistics")
        for result in (positive, negative, logistics):
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["validation"]["status"], "passed")
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["answer_model_calls"], 0)
        self.assertEqual(positive["sentiment_intent"], "positive")
        self.assertEqual(negative["sentiment_intent"], "negative")
        self.assertNotIn("包装", json.dumps(logistics["public_answer"], ensure_ascii=False))
        self.assertNotIn("客服", json.dumps(logistics["public_answer"], ensure_ascii=False))
        self.assertEqual(logistics["results"]["r1"]["parameters"]["aspect"], "logistics")

    def test_overview_and_identical_trend_reuse_one_aggregate(self):
        scope = self.scope()
        plan = build_query_plan("按月查看评论趋势", scope)
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            context = ReviewToolContext(connection, scope, self.root, 12, query_plan=plan)
            registry = create_review_registry(context)
            with patch("data_analysis_agent.agents.review_tools.analytics.get_trend", wraps=analytics.get_trend) as sql:
                overview = registry.execute_tool("get_analysis_context", "{}", automatic=True).to_dict()
                trend = registry.execute_tool("get_trend", '{"period":"month"}').to_dict()
                self.assertEqual(trend["status"], "success", trend)
                self.assertEqual(sql.call_count, 1)
                self.assertEqual(context.query_executions, 1)
                self.assertEqual(context.cache_hits, 1)
                self.assertEqual(overview["data"]["data"]["summary"], trend["data"]["data"]["summary"])

    def test_product_dataset_annotation_and_new_context_do_not_share_cache(self):
        def count(**changes):
            scope = self.scope(**changes)
            with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
                connection.execute("BEGIN")
                context = ReviewToolContext(connection, scope, self.root, 12)
                context.execute("get_analysis_context", {})
                return context.query_executions, context.cache_hits, context.results["overview"]["data"]["summary"]
        a = count(product_ids=["P1"])
        b = count(product_ids=["P2"])
        annotation = count(model="different-annotation")
        source = self.root / "another.jsonl"
        source.write_text(json.dumps(self.records[0]), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source, dataset_version="different-dataset")
        dataset = count(dataset_version="different-dataset")
        other_session = count(product_ids=["P1"])
        for executed, hits, _ in (a, b, annotation, dataset, other_session):
            self.assertEqual((executed, hits), (1, 0))
        self.assertEqual(a, other_session)
        self.assertEqual(annotation[2]["successful_reviews"], 0)
        self.assertNotEqual(a[2]["review_count"], dataset[2]["review_count"])

    def test_human_correction_import_and_annotation_are_visible_in_new_task(self):
        before = self.run_confirmed("get_positive_aspects")["results"]["r1"]["data"]["summary"]
        self.save(rid=1, expected_revision_id=None, opinions=[])
        after = self.run_confirmed("get_positive_aspects")["results"]["r1"]["data"]["summary"]
        self.assertEqual(after["positive_review_count"], before["positive_review_count"] - 1)
        source = self.root / "new.jsonl"
        source.write_text(json.dumps({**self.records[0], "user_id": "new-review"}), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source)
        unlabelled = self.run_confirmed("get_positive_aspects")["results"]["r1"]["data"]["summary"]
        self.assertEqual(unlabelled["review_count"], after["review_count"] + 1)
        row = next(row for row in pending_reviews(self.db, **IDENTITY, limit=100) if row["id"] > 6)
        save_annotation(self.db, row, **IDENTITY, status="success", opinions=validate_opinions(
            json.dumps({"opinions": [opinion()]}), row["text"]))
        labelled = self.run_confirmed("get_positive_aspects")["results"]["r1"]["data"]["summary"]
        self.assertEqual(labelled["positive_review_count"], unlabelled["positive_review_count"] + 1)

    def test_direct_invalid_parameters_and_budget_never_publish(self):
        for params in ({"aspect": "installation"}, {"product_ids": ["not-selected"]}):
            result = self.run_confirmed("get_top_issues", **params)
            self.assertEqual(result["status"], "error")
            self.assertNotEqual(result["public_answer"]["status"], "ok")

    def test_confirmed_path_keeps_tool_budget_and_answer_publication_guard(self):
        result = run_review_agent(self.db, confirmed("get_top_issues"), scope=self.scope(), settings=replace(SETTINGS, max_tool_calls=1))
        self.assertEqual(result["status"], "error")
        self.assertNotEqual(result["public_answer"]["status"], "ok")
        self.assertEqual(result["query_executions"], 1)

    def test_relative_dates_not_silently_replaced_by_bound_historical_window(self):
        for text in ("最近半年物流问题怎么样", "那前三个月呢"):
            self.assertIsNotNone(build_query_plan(text, self.scope()).unsupported_reason)
