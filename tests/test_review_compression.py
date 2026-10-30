"""阶段 3：真实隔离 SQL 快照、确定性压缩与公开白名单。"""

from contextlib import closing
from copy import deepcopy
import json
import unittest

from test_review_workbench import WorkbenchFixture, opinion
import test_review_agent as agent_fixture
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_tools import ReviewToolContext, create_review_registry
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.review_results import adapt_result
from data_analysis_agent.agents.review_compression import (
    AnalysisResultCompressor, CompressionOptions, build_analysis_artifacts, compress_trend, read_public_analysis,
)
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.reviews.store import connect_review_database


class CompressionTests(WorkbenchFixture, unittest.TestCase):
    scope = agent_fixture.ReviewAgentTests.scope

    def result(self, question, **scope_changes):
        scope = self.scope(**scope_changes)
        plan = build_query_plan(question, scope)
        self.assertIsNone(plan.unsupported_reason)
        with closing(connect_review_database(self.db, readonly=True)) as connection, connection:
            connection.execute("BEGIN")
            context = ReviewToolContext(connection, scope, self.root, 30, query_plan=plan, sentiment_intent=plan.sentiment)
            registry = create_review_registry(context)
            registry.execute_tool("get_analysis_context", "{}", automatic=True)
            for request in plan.requirements:
                response = registry.execute_tool(request["tool"], json.dumps(request["parameters"])).to_dict()
                self.assertEqual(response["status"], "success", response)
            ids = [key for key in context.results if key != "overview"]
            validation = validate_report(context.results, ids, query_plan=plan)
            self.assertEqual(validation["status"], "passed")
            return {"agent": True, "status": "ok", "question": question, "scope": scope,
                    "result_ids": ids, "results": deepcopy(context.results), "validation": validation,
                    "trace": [{"debug": "private-trace-marker"}], "agent_model_identity": {"model": "private-agent-marker"},
                    "answer": "旧模板回答", "query_plan": plan.to_dict()}, plan

    def compress(self, question, **scope):
        result, plan = self.result(question, **scope)
        return result, build_analysis_artifacts(result, plan)

    def test_positive_rank_counts_denominators_no_negative_or_auto_evidence(self):
        full, artifacts = self.compress("美的空调好评主要集中在哪些方面？")
        compact = artifacts["compact_context"]
        aspects = compact["statistics"][0]["aspects"]
        self.assertEqual([row["aspect"] for row in aspects], ["effectiveness", "logistics", "price"])
        metrics = {row["name"]: row for row in aspects[0]["metrics"]}
        self.assertEqual(metrics["positive_review_count"]["value"], 2)
        self.assertEqual(metrics["positive_rate"]["denominator_name"], "positive_review_count")
        self.assertEqual(metrics["positive_rate"]["denominator"], 3)
        self.assertEqual(metrics["positive_success_rate"]["denominator_name"], "successful_reviews")
        self.assertEqual(compact["sample_summary"][0]["positive_review_count"], 3)
        self.assertEqual(compact["evidence"], [])
        self.assertNotIn("negative_rate", canonical_json(compact))
        self.assertEqual(len(full["results"]["r1"]["data"]["aspects"]), 6)

    def test_negative_preserves_success_denominator_and_baseline(self):
        _, data = self.compress("这个商品主要有哪些问题？")
        compact = data["compact_context"]
        current, baseline = compact["statistics"]
        self.assertNotEqual(current["scope_ref"], baseline["scope_ref"])
        self.assertEqual(current["aspects"][0]["aspect"], "packaging")
        metric = next(m for m in current["aspects"][0]["metrics"] if m["name"] == "negative_rate")
        self.assertEqual(metric["denominator"], 3)
        self.assertNotIn("positive_rate", canonical_json(compact))

    def test_positive_zero_denominator_stays_null(self):
        for rid in (1, 2, 6):
            self.save(rid=rid, expected_revision_id=None, opinions=[])
        _, artifacts = self.compress("好评主要方面")
        compact = artifacts["compact_context"]
        self.assertIn("no_positive_mentions", {row["code"] for row in compact["caveats"]})
        for aspect in compact["statistics"][0]["aspects"]:
            rate = next(m for m in aspect["metrics"] if m["name"] == "positive_rate")
            self.assertIsNone(rate["value"])
            self.assertEqual(rate["denominator"], 0)

    def test_no_annotations_and_low_coverage_are_explicit(self):
        _, artifacts = self.compress("好评主要方面", model="no-such-annotation-version")
        compact = artifacts["compact_context"]
        codes = {row["code"] for row in compact["caveats"]}
        self.assertIn("no_successful_annotations", codes)
        self.assertIn("insufficient_coverage", codes)
        self.assertEqual(compact["sample_summary"][0]["successful_reviews"], 0)
        self.assertIsNone(compact["statistics"][0]["metrics"][1]["value"])

    def test_small_sample_and_multiple_aspects_are_not_summed(self):
        _, artifacts = self.compress("好评主要方面")
        compact = artifacts["compact_context"]
        self.assertIn("small_sample", {row["code"] for row in compact["caveats"]})
        self.assertEqual(compact["sample_summary"][0]["positive_review_count"], 3)
        self.assertGreater(sum(aspect["metrics"][0]["value"] for aspect in compact["statistics"][0]["aspects"]), 3)

    def test_human_snapshot_is_used_without_reannotating(self):
        self.save(rid=2, expected_revision_id=None, opinions=[opinion("service", "positive", "物流很快")])
        _, artifacts = self.compress("好评主要方面")
        ranks = artifacts["compact_context"]["statistics"][0]["aspects"]
        self.assertIn("service", [row["aspect"] for row in ranks])
        self.assertNotIn("logistics", [row["aspect"] for row in ranks])

    def test_trend_mid_peak_missing_bucket_and_fluctuations_retained(self):
        full, plan = self.result("包装按月趋势", date_to="2023-12-31")
        item = deepcopy(full["results"]["r1"])
        for i, row in enumerate(item["data"]["buckets"]):
            success = 0 if i == 6 else 100
            count = 90 if i == 4 else 10
            row.update(review_count=success, eligible_text_reviews=success, successful_reviews=success,
                       failed_reviews=0, unprocessed_reviews=0, labeling_coverage=1.0 if success else None,
                       negative_review_count=count if success else 0, negative_rate=count / success if success else None)
            row["aspects"]["packaging"] = {"negative_review_count": count if success else 0, "negative_rate": count / success if success else None}
        # 这是受控时序统计，非生产数据；明确校验各桶数值后再适配。
        self.assertEqual(validate_report({"r1": item}, ["r1"])["status"], "passed")
        structured = adapt_result(item, full["scope"])
        compact = AnalysisResultCompressor(CompressionOptions(max_trend_points=3)).compress(full["question"], plan, [structured]).model_dump()
        points = compact["statistics"][0]["trends"][0]["points"]
        self.assertIn("2023-05", [point["bucket"] for point in points])
        empty = next(point for point in points if point["bucket"] == "2023-07")
        self.assertIsNone(empty["metric"]["value"])
        finding = compact["key_findings"][0]
        self.assertEqual(finding["direction"], "fluctuating")
        self.assertEqual(finding["peak_bucket"], "2023-05")
        self.assertEqual(finding["largest_adjacent_change"]["change_pp"], 80.0)
        self.assertEqual(len(structured.statistics[0].trends[0].points), 12)

    def test_only_requested_trend_dimensions(self):
        _, artifacts = self.compress("按月查看包装和物流问题变化", date_to="2023-03-31")
        series = [series for row in artifacts["compact_context"]["statistics"] for series in row["trends"]]
        self.assertEqual({series["aspect"] for series in series}, {"packaging", "logistics"})
        self.assertTrue(all(not row["metrics"] for row in artifacts["compact_context"]["statistics"]))
        self.assertEqual(artifacts["compact_context"]["source_result_ids"], ["r1", "r2"])

    def test_trend_plateau_turns_survive_soft_point_budget(self):
        full, _ = self.result("包装按月趋势", date_to="2023-12-31")
        series = adapt_result(full["results"]["r1"], full["scope"]).statistics[0].trends[0]
        for point, rate in zip(series.points, [0.1, 0.8, 0.8, 0.1, 0.1, 0.7, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1]):
            point.sample_size = point.sample_size.model_copy(update={"review_count": 100, "eligible_text_reviews": 100,
                "successful_reviews": 100, "failed_reviews": 0, "unprocessed_reviews": 0, "labeling_coverage": 1.0})
            point.metric = point.metric.model_copy(update={"value": rate, "numerator": round(rate * 100), "denominator": 100})
        compressed, finding = compress_trend(series, CompressionOptions(max_trend_points=3, anomaly_change_pp=100.0))
        buckets = [point["bucket"] for point in compressed["points"]]
        for bucket in ("2023-02", "2023-03", "2023-06", "2023-07"):
            self.assertIn(bucket, buckets)
        self.assertTrue(compressed["target_exceeded"])
        self.assertEqual(finding["direction"], "fluctuating")

    def test_products_and_windows_keep_distinct_scopes(self):
        _, artifacts = self.compress("比较两个商品的主要负面问题", product_ids=["P1", "P2"])
        compact = artifacts["compact_context"]
        product_groups = [row for row in compact["statistics"] if row["analysis_type"] == "comparison"]
        self.assertEqual([row["group_id"] for row in product_groups], ["P1", "P2"])
        scopes = {row["scope_ref"]: row for row in compact["scope_summary"]}
        self.assertEqual(scopes[product_groups[0]["scope_ref"]]["product_ids"], ["P1"])
        self.assertNotEqual(product_groups[0]["sample_ref"], product_groups[1]["sample_ref"])

    def test_evidence_exact_quotes_refs_and_budget(self):
        full, plan = self.result("给我看3条效果好评原文")
        artifacts = build_analysis_artifacts(full, plan, CompressionOptions(quote_chars=1))
        evidence = artifacts["compact_context"]["evidence"]
        self.assertLessEqual(len(evidence), 3)
        for item in evidence:
            original = next(row for row in full["results"]["r1"]["data"]["items"] if row["review_id"] == item["review_id"])
            self.assertEqual(item["evidence_ref"], original["evidence_ref"])
            for quote in item["opinions"]:
                self.assertEqual(original["text"][quote["quote_start"]:quote["quote_end"]], quote["quote"])
                self.assertEqual(quote["sentiment"], "positive")

    def test_context_white_list_and_original_unchanged(self):
        full, plan = self.result("好评主要方面")
        before = canonical_json(full)
        artifacts = build_analysis_artifacts(full, plan)
        self.assertEqual(canonical_json(full), before)
        compact = canonical_json(artifacts["compact_context"])
        public = canonical_json(artifacts["public_analysis"])
        for marker in ("base_url", "prompt_version", "agent_model_identity", "private-trace-marker", "private-agent-marker", self.scope()["model"]):
            self.assertNotIn(marker, compact)
            self.assertNotIn(marker, public)
        self.assertIn("annotation_profile_ref", compact)

    def test_background_samples_deduplicated_without_merging_windows(self):
        _, artifacts = self.compress("数据质量、包装按月趋势")
        self.assertEqual(len(artifacts["compact_context"]["sample_summary"]), 1)
        self.assertEqual(len(artifacts["compact_context"]["statistics"]), 2)
        _, artifacts = self.compress("好评主要方面、数据质量")
        samples = artifacts["compact_context"]["sample_summary"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["positive_review_count"], 3)

    def test_old_task_without_new_fields_converts_and_preserves_export_payload(self):
        full, _ = self.result("好评主要方面")
        full.pop("query_plan")
        full.pop("validation")
        before = canonical_json(full)
        public = read_public_analysis({"status": "succeeded", "result": full})
        self.assertEqual(public["status"], "ok")
        self.assertEqual(public["source_result_ids"], ["r1"])
        self.assertEqual(before, canonical_json(full))

    def test_failed_partial_queued_and_unsupported_do_not_publish_statistics(self):
        full, _ = self.result("好评主要方面")
        for status in ("failed", "partial", "queued", "running"):
            public = read_public_analysis({"status": status, "result": full})
            self.assertNotEqual(public["status"], "ok")
            self.assertEqual(public["statistics"], [])
            self.assertFalse(public["details_available"])
        public = read_public_analysis({"status": "succeeded", "result": full, "error": {"message": "unconfirmed"}})
        self.assertEqual(public["status"], "failed")
        self.assertEqual(public["statistics"], [])
        self.assertEqual(read_public_analysis({"status": "succeeded", "result": [full]})["status"], "failed")
        full["status"] = "unsupported"
        self.assertEqual(read_public_analysis({"status": "succeeded", "result": full})["status"], "unsupported")

    def test_forged_public_fields_and_validation_failure_are_not_trusted(self):
        full, _ = self.result("好评主要方面")
        full["public_analysis"] = {"status": "ok", "trace": "do-not-publish"}
        self.assertNotIn("do-not-publish", canonical_json(read_public_analysis({"status": "succeeded", "result": full})))
        full["sentiment_intent"] = "negative"
        self.assertEqual(read_public_analysis({"status": "succeeded", "result": full})["status"], "failed")
        full.pop("sentiment_intent")
        full["validation"]["status"] = "failed"
        self.assertEqual(read_public_analysis({"status": "succeeded", "result": full})["statistics"], [])


if __name__ == "__main__":
    unittest.main()
