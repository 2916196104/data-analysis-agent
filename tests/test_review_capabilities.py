"""第三阶段统计口径、范围约束和 Agent 闭环；仅使用隔离数据与模拟模型。"""
import hashlib
import json
import sqlite3
import unittest
from contextlib import closing
from copy import deepcopy

from test_agent import tools, final
import test_review_agent as agent_fixture
from test_review_workbench import WorkbenchFixture, IDENTITY
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.reviews.analytics import (
    get_aspect_trend, get_data_quality, get_rating_distribution, compare_time_windows,
)
from data_analysis_agent.reviews.loader import SOURCE, DATASET_VERSION
from data_analysis_agent.reviews.store import connect_review_database


class CapabilityTests(WorkbenchFixture, unittest.TestCase):
    def scope(self, **changes):
        return {**IDENTITY, "source": SOURCE, "dataset_version": DATASET_VERSION, "product_ids": [],
                "date_from": "2023-01-01", "date_to": "2023-03-01", **changes}

    def test_quality_preserves_denominators_failures_sources_and_annotation_identity(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        data = get_data_quality(self.db, **self.scope())
        self.assertEqual(data["summary"]["empty_text_reviews"], 1)
        self.assertEqual(data["summary"]["labeling_coverage"], 0.6)
        self.assertEqual(data["missing_text_rate"], 0.166667)
        self.assertEqual(data["failed_rate"], 0.2)
        self.assertEqual(data["failures"], [{"error_code": "invalid_output", "review_count": 1}])
        self.assertEqual(sum(row["review_count"] for row in data["sources"]), 6)
        self.assertEqual(len(data["sources"][0]["file_sha256"]), 64)
        other = get_data_quality(self.db, **self.scope(model="unlabeled"))
        self.assertEqual(other["summary"]["unprocessed_reviews"], 5)
        self.assertEqual(other["summary"]["labeling_coverage"], 0)
        self.assertEqual(other["failures"], [])
        empty = get_data_quality(self.db, **self.scope(dataset_version="not-selected"))
        self.assertIsNone(empty["summary"]["labeling_coverage"])
        self.assertIsNone(empty["missing_text_rate"])
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_aspect_months_fill_empty_buckets_and_use_effective_human_opinions(self):
        data = get_aspect_trend(self.db, aspect="packaging", **self.scope())
        self.assertEqual([row["bucket"] for row in data["buckets"]], ["2023-01", "2023-02", "2023-03"])
        self.assertEqual(data["buckets"][0]["aspects"]["packaging"], {"negative_review_count": 1, "negative_rate": 0.333333})
        self.assertIsNone(data["buckets"][1]["aspects"]["packaging"]["negative_rate"])
        self.save(opinions=[], expected_revision_id=None)
        self.assertEqual(get_aspect_trend(self.db, **self.scope())["summary"]["aspects"]["packaging"]["negative_review_count"], 0)

    def test_rating_buckets_ignore_labels_and_keep_missing_and_fractional_scores_separate(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for rid, rating in enumerate([None, 4.5, 1, 2, 5, 5], 1):
                connection.execute("UPDATE reviews SET rating=? WHERE id=?", (rating, rid))
        data = get_rating_distribution(self.db, group_by="all", **self.scope(model="unlabeled"))
        row = data["groups"][0]
        self.assertEqual([row[key] for key in ("review_count", "rated_reviews", "missing_rating_reviews", "other_rating_reviews")], [6, 4, 1, 1])
        self.assertEqual([star["review_count"] for star in row["stars"]], [1, 1, 0, 0, 2])
        self.assertEqual([star["rate"] for star in row["stars"]], [0.25, 0.25, 0, 0, 0.5])
        product = get_rating_distribution(self.db, **self.scope(product_ids=["P1"]))["groups"]
        self.assertEqual([(row["group"], row["review_count"]) for row in product], [("P1", 3)])
        self.assertEqual(get_rating_distribution(self.db, group_by="month", **self.scope())["groups"][0]["group"], "2023-01")
        empty = get_rating_distribution(self.db, group_by="all", **self.scope(as_of="2022-12-31"))["groups"][0]
        self.assertTrue(all(star["rate"] is None for star in empty["stars"]))

    def test_custom_windows_compare_exact_counts_and_null_denominators(self):
        data = compare_time_windows(self.db, **self.scope(date_from="2023-01-02", date_to="2023-01-05"),
                                    baseline_from="2023-01-01", baseline_to="2023-01-01")
        self.assertEqual((data["current_days"], data["baseline_days"]), (4, 1))
        self.assertEqual(data["changes"]["negative_rate_pp"], -100)
        self.assertEqual(data["changes"]["labeling_coverage_pp"], -50)
        self.assertEqual(data["changes"]["review_count"], 4)
        empty = compare_time_windows(self.db, **self.scope(), baseline_from="2022-01-01", baseline_to="2022-01-02")
        self.assertIsNone(empty["changes"]["negative_rate_pp"])

    def test_tool_custom_windows_cannot_expand_scope_or_bridge_unselected_gap(self):
        scope = {**self.scope(), "baseline_from": "2022-11-01", "baseline_to": "2022-11-30"}
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            for values in [
                {"date_from": "2023-01-02"},
                {"date_from": "2023-01-02", "date_to": "2023-03-02", "baseline_from": "2022-11-01", "baseline_to": "2022-11-30"},
                {"date_from": "2022-11-02", "date_to": "2023-01-01", "baseline_from": "2022-11-01", "baseline_to": "2022-11-01"},
                {"date_from": "2023-01-01", "date_to": "2023-01-02", "baseline_from": "2023-01-02", "baseline_to": "2023-01-03"},
                {"product_ids": ["OUTSIDE"]}, {"model": "other"},
            ]:
                with self.subTest(values=values):
                    registry = create_review_registry(ReviewToolContext(connection, scope, self.root, 30))
                    response = registry.execute_tool("compare_time_windows", json.dumps(values)).to_dict()
                    self.assertEqual(response["status"], "error", response)
                    self.assertEqual(response["error"]["code"], "INVALID_PARAM", response)
            values = {"date_from": "2023-01-02", "date_to": "2023-01-05", "baseline_from": "2023-01-01", "baseline_to": "2023-01-01"}
            registry = create_review_registry(ReviewToolContext(connection, scope, self.root, 30))
            self.assertEqual(registry.execute_tool("compare_time_windows", json.dumps(values)).to_dict()["status"], "success")


class AgentCapabilityTests(WorkbenchFixture, unittest.TestCase):
    # 共享真实 SDK + 模拟模型的执行助手，避免重复整个历史回归测试类。
    scope = agent_fixture.ReviewAgentTests.scope
    mock_model = agent_fixture.ReviewAgentTests.mock_model
    run_script = agent_fixture.ReviewAgentTests.run_script

    def test_all_new_tools_are_validated_grounded_and_readonly(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result, _ = self.run_script([tools(("get_data_quality", {}), ("get_aspect_trend", {"aspect": "packaging"}),
                                          ("get_rating_distribution", {"group_by": "product"}), ("compare_time_windows", {})),
                                     final("ok", "r1", "r2", "r3", "r4")])
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["validation"]["status"], "passed")
        self.assertIn("缺失正文 1 条", result["answer"])
        self.assertIn("原始星级分布", result["answer"])
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        for path, value in [(("r1", "data", "failed_rate"), 0.99),
                            (("r2", "data", "buckets", 0, "aspects", "packaging", "negative_rate"), 0.9),
                            (("r3", "data", "groups", 0, "stars", 0, "rate"), 0.9),
                            (("r4", "data", "changes", "negative_rate_pp"), 0)]:
            changed = deepcopy(result["results"])
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            self.assertEqual(validate_report(changed, result["result_ids"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
