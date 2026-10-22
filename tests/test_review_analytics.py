"""Business assertions against temporary review databases; no online model calls."""

import io
import json
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime
from unittest.mock import patch

from test_review_workbench import IDENTITY, WorkbenchFixture, opinion

from data_analysis_agent.cli.analytics import main
from data_analysis_agent.reviews.analytics import compare_products, get_evidence, get_top_issues, get_trend
from data_analysis_agent.reviews.labeling import validate_opinions
from data_analysis_agent.reviews.loader import ReviewDataError
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.human_review import save_human_review


class ReviewAnalyticsTests(WorkbenchFixture, unittest.TestCase):
    def options(self, **kwargs):
        return {**IDENTITY, "date_from": "2023-01-01", "date_to": "2023-01-06", **kwargs}

    def test_daily_counts_coverage_and_null_rates_for_unlabeled_or_empty_days(self):
        result = get_trend(self.db, **self.options(), period="day")
        total = result["summary"]
        self.assertEqual((total["review_count"], total["eligible_text_reviews"], total["successful_reviews"],
                          total["failed_reviews"], total["unprocessed_reviews"], total["empty_text_reviews"]),
                         (6, 5, 3, 1, 1, 1))
        self.assertEqual((total["labeling_coverage"], total["negative_review_count"], total["negative_rate"]),
                         (0.6, 1, 0.333333))
        self.assertEqual(total["aspects"]["packaging"]["negative_review_count"], 1)
        self.assertEqual(total["aspects"]["service"]["negative_review_count"], 1)
        days = {row["bucket"]: row for row in result["buckets"]}
        self.assertEqual(len(days), 6)
        self.assertEqual((days["2023-01-02"]["review_count"], days["2023-01-02"]["labeling_coverage"]), (2, 1.0))
        self.assertEqual(days["2023-01-02"]["negative_rate"], 0.0)
        self.assertEqual(days["2023-01-03"]["labeling_coverage"], 0.0)
        self.assertIsNone(days["2023-01-03"]["negative_rate"])
        self.assertIsNone(days["2023-01-04"]["labeling_coverage"])
        self.assertEqual(days["2023-01-06"]["review_count"], 0)
        self.assertIsNone(days["2023-01-06"]["negative_rate"])

    def test_multiple_quotes_count_one_review_and_evidence_keeps_each_quote(self):
        quote = validate_opinions(json.dumps({"opinions": [opinion("packaging", "negative", "破了")]}),
                                  self.records[0]["text"])[0]
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO review_opinions(annotation_id,aspect,sentiment,quote,quote_start,quote_end) "
                        "VALUES (?,?,?,?,?,?)", (self.annotations[1], *(quote[k] for k in (
                            "aspect", "sentiment", "quote", "quote_start", "quote_end"))))
        summary = get_trend(self.db, **self.options())["summary"]
        self.assertEqual(summary["review_count"], 6)
        self.assertEqual(summary["aspects"]["packaging"]["negative_review_count"], 1)
        self.assertEqual(summary["negative_review_count"], 1)
        evidence = get_evidence(self.db, **self.options(), aspect="packaging")
        self.assertEqual((evidence["total"], evidence["total_opinions"]), (1, 2))
        self.assertEqual(len(evidence["items"][0]["opinions"]), 2)
        self.assertEqual(get_evidence(self.db, **self.options(), aspect="effectiveness")["total"], 0)

    def test_month_boundaries_and_empty_months_are_filled_without_future_buckets(self):
        path = self.root / "boundary.jsonl"
        values = [{"parent_asin": "P4", "user_id": str(i), "text": "SIMULATED boundary",
                   "timestamp": int(datetime.fromisoformat(day).timestamp() * 1000)} for i, day in enumerate(
                       ("2023-01-31T23:59:59.999+00:00", "2023-02-01T00:00:00+00:00"))]
        path.write_text("\n".join(json.dumps(v) for v in values), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=path)
        options = self.options(product_ids=["P4"], date_from="2023-01-31", date_to="2023-03-01")
        months = get_trend(self.db, **options)["buckets"]
        self.assertEqual([(r["bucket"], r["review_count"]) for r in months],
                         [("2023-01", 1), ("2023-02", 1), ("2023-03", 0)])
        limited = get_trend(self.db, **{**options, "as_of": "2023-01-31T23:59:59.999Z"})
        self.assertEqual([(r["bucket"], r["review_count"]) for r in limited["buckets"]], [("2023-01", 1)])

    def test_cutoff_applies_to_every_tool_and_includes_exact_utc_time(self):
        options = self.options(as_of="2023-01-02T20:00:00+08:00")
        trend = get_trend(self.db, **options, period="day")
        self.assertEqual((trend["summary"]["review_count"], trend["summary"]["successful_reviews"]), (2, 2))
        self.assertEqual([r["bucket"] for r in trend["buckets"]], ["2023-01-01", "2023-01-02"])
        self.assertEqual([r["product_id"] for r in compare_products(self.db, **options)["products"]], ["P1", "P3"])
        evidence = get_evidence(self.db, **options, sentiment=None)
        self.assertEqual([r["review_id"] for r in evidence["items"]], [6, 1])
        issues = get_top_issues(self.db, **options, baseline_from="2022-12-01", baseline_to="2022-12-31")
        self.assertEqual(issues["current"]["review_count"], 2)
        self.assertEqual(issues["scope"], trend["scope"])
        empty_options = {**options, "as_of": "2022-12-31"}
        self.assertEqual(get_trend(self.db, **empty_options)["buckets"], [])
        self.assertEqual(compare_products(self.db, **empty_options)["products"], [])
        self.assertEqual(get_evidence(self.db, **empty_options, sentiment=None)["total"], 0)

    def test_human_latest_empty_snapshot_and_confirmation_drive_all_statistics(self):
        before = self.model_snapshot()
        first = self.save(expected_revision_id=None, opinions=[opinion("quality", "negative", "包装破了")])
        summary = get_trend(self.db, **self.options())["summary"]
        self.assertEqual(summary["reviewed_reviews"], 1)
        self.assertEqual(summary["aspects"]["quality"]["negative_review_count"], 1)
        self.assertEqual(summary["aspects"]["packaging"]["negative_review_count"], 0)
        product = compare_products(self.db, **self.options())["products"][0]
        self.assertEqual(product["aspects"]["quality"], {"negative_review_count": 1, "negative_rate": 1.0})
        self.assertEqual(summary["aspects"]["quality"]["negative_rate"], 0.333333)
        evidence = get_evidence(self.db, **self.options(), aspect="quality")["items"][0]
        self.assertEqual((evidence["opinion_source"], evidence["revision_id"]), ("human", first["revision_id"]))
        second = self.save(expected_revision_id=first["revision_id"], opinions=[])
        summary = get_trend(self.db, **self.options())["summary"]
        self.assertEqual((summary["negative_review_count"], summary["successful_reviews"]), (0, 3))
        self.assertEqual(get_evidence(self.db, **self.options())["total"], 0)
        save_human_review(self.db, 1, annotation_id=self.annotations[1], expected_revision_id=second["revision_id"],
                          action="confirmed", reviewer="复验")
        self.assertEqual(get_trend(self.db, **self.options())["summary"]["negative_review_count"], 1)
        self.assertEqual(self.model_snapshot(), before)

    def test_top_issues_orders_current_counts_and_computes_percentage_point_changes(self):
        self.save(rid=2, expected_revision_id=None, opinions=[opinion("quality", "negative", "质量很好")])
        result = get_top_issues(self.db, **self.options(date_from="2023-01-02"),
                                baseline_from="2023-01-01", baseline_to="2023-01-01")
        first = result["issues"][0]
        self.assertEqual((first["rank"], first["aspect"], first["negative_review_count"]), (1, "quality", 1))
        self.assertEqual((first["negative_rate"], first["baseline_negative_rate"], first["negative_rate_change_pp"]),
                         (0.5, 0.0, 50.0))
        self.assertEqual((result["current"]["review_count"], result["baseline"]["review_count"]), (5, 1))
        packaging = next(r for r in result["issues"] if r["aspect"] == "packaging")
        self.assertEqual((packaging["negative_review_count_change"], packaging["negative_rate_change_pp"]), (-1, -100.0))
        self.assertEqual(result["baseline_scope"]["model"], result["scope"]["model"])

    def test_baseline_without_labels_returns_unknown_changes_instead_of_zero(self):
        result = get_top_issues(self.db, **self.options(), baseline_from="2022-12-01", baseline_to="2022-12-31")
        self.assertIsNone(result["baseline"]["labeling_coverage"])
        self.assertTrue(all(item["negative_rate_change_pp"] is None for item in result["issues"]))
        result = get_top_issues(self.db, **self.options(date_from="2023-01-03", date_to="2023-01-05"),
                                baseline_from="2023-01-01", baseline_to="2023-01-02")
        self.assertEqual(result["current"]["labeling_coverage"], 0.0)
        self.assertIsNone(result["current"]["negative_rate"])
        self.assertTrue(all(item["negative_rate_change_pp"] is None for item in result["issues"]))

    def test_profiles_and_current_content_are_isolated_across_tools(self):
        self.save(expected_revision_id=None, opinions=[])
        for field, value in (("model", "other"), ("base_url", "https://other.invalid/v1"), ("prompt_version", "other")):
            options = self.options(**{field: value})
            summary = get_trend(self.db, **options)["summary"]
            self.assertEqual((summary["successful_reviews"], summary["unprocessed_reviews"], summary["labeling_coverage"]), (0, 5, 0.0))
            self.assertIsNone(summary["negative_rate"])
            self.assertEqual(get_evidence(self.db, **options, sentiment=None)["total"], 0)
            self.assertTrue(all(r["reviewed_reviews"] == 0 for r in compare_products(self.db, **options)["products"]))
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE reviews SET content_sha256='changed' WHERE id=1")
        self.assertEqual(get_trend(self.db, **self.options())["summary"]["successful_reviews"], 2)
        self.assertEqual(get_evidence(self.db, **self.options(), review_id=1, sentiment=None)["total"], 0)

    def test_product_comparison_preserves_dataset_identity_and_reports_absent_products(self):
        result = compare_products(self.db, **self.options(product_ids=["P1", "P2", "P3", "missing"]))
        products = {r["product_id"]: r for r in result["products"]}
        self.assertEqual((products["P1"]["review_count"], products["P1"]["labeling_coverage"], products["P1"]["negative_rate"]), (3, 0.5, 1.0))
        self.assertEqual((products["P2"]["review_count"], products["P2"]["labeling_coverage"], products["P2"]["negative_rate"]), (2, 0.5, 0.0))
        self.assertEqual(result["no_reviews_product_ids"], ["missing"])
        path = self.root / "version.jsonl"
        path.write_text(json.dumps(self.records[0]), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version="other-version")
        products = compare_products(self.db, **self.options(product_ids=["P1"]))["products"]
        self.assertEqual(len(products), 2)
        self.assertEqual(sorted(r["review_count"] for r in products), [1, 3])
        filtered = compare_products(self.db, **self.options(product_ids=["P1"], dataset_version="other-version"))
        self.assertEqual(filtered["products"][0]["unprocessed_reviews"], 1)
        self.assertIsNone(filtered["products"][0]["negative_rate"])

    def test_evidence_unicode_locations_filters_and_stable_review_pagination(self):
        self.save(rid=6, expected_revision_id=None, opinions=[opinion("packaging", "negative", "🎉好用\n包装破了")])
        result = get_evidence(self.db, **self.options(), aspect="packaging", page_size=1)
        self.assertEqual((result["total"], result["total_opinions"], result["pages"]), (2, 2, 2))
        self.assertEqual(result["items"][0]["review_id"], 6)
        last = get_evidence(self.db, **self.options(), aspect="packaging", page_size=1, page=999)
        self.assertEqual((last["page"], last["items"][0]["review_id"]), (2, 1))
        for item in result["items"]:
            self.assertNotIn("user_id", item)
            self.assertTrue(item["file_path"].endswith("reviews.jsonl"))
            self.assertTrue(item["evidence_ref"].endswith("#line=" + str(item["line_number"])))
            for value in item["opinions"]:
                self.assertEqual(item["text"][value["quote_start"]:value["quote_end"]], value["quote"])
        self.assertEqual(get_evidence(self.db, **self.options(), review_id=6)["total"], 1)
        self.assertEqual(get_evidence(self.db, **self.options(), review_id=999)["total"], 0)

    def test_invalid_ranges_periods_baselines_and_parameters_fail_cleanly(self):
        for changes in ({"date_from": None}, {"date_from": "20230101"}, {"date_to": "2022-12-31"},
                        {"as_of": "2023-01-01T00:00:00"}, {"product_ids": "P1"}, {"model": ""}):
            with self.subTest(changes=changes), self.assertRaises(ReviewDataError):
                get_trend(self.db, **self.options(**changes))
        for period in ("week", None, []):
            with self.assertRaises(ReviewDataError):
                get_trend(self.db, **self.options(), period=period)
        for extra in ({"baseline_from": "2023-01-01", "baseline_to": "2023-01-02"},
                      {"baseline_from": "2022-12-31", "baseline_to": "2022-12-01"},
                      {"baseline_from": "2022-12-01", "baseline_to": "2022-12-31", "top_n": True}):
            with self.assertRaises(ReviewDataError):
                get_top_issues(self.db, **self.options(), **extra)
        for extra in ({"aspect": "other"}, {"aspect": []}, {"sentiment": "mixed"}, {"page": True},
                      {"page_size": 101}, {"review_id": 0}):
            with self.assertRaises(ReviewDataError):
                get_evidence(self.db, **self.options(), **extra)
        with self.assertRaisesRegex(ReviewDataError, "10000"):
            get_trend(self.db, **self.options(date_from="0001-01-01", date_to="9999-12-30"), period="day")
        self.assertEqual(get_trend(self.db, **self.options(product_ids=["P1' OR 1=1 --"]))["summary"]["review_count"], 0)

    def test_tools_are_read_only_and_do_not_load_configuration_or_invoke_models(self):
        before = self.db.read_bytes()
        with patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("must not read .env")), \
                patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=AssertionError("must not call model")):
            get_trend(self.db, **self.options())
            get_top_issues(self.db, **self.options(), baseline_from="2022-12-01", baseline_to="2022-12-31")
            compare_products(self.db, **self.options())
            get_evidence(self.db, **self.options())
        self.assertEqual(self.db.read_bytes(), before)

    def test_cli_json_commands_export_and_existing_file_protection(self):
        args = ["--db", str(self.db), "--model", IDENTITY["model"], "--base-url", IDENTITY["base_url"],
                "--prompt-version", IDENTITY["prompt_version"], "--date-from", "2023-01-01", "--date-to", "2023-01-06"]

        def cli(command, *extra):
            output = io.StringIO()
            with patch.object(sys, "argv", ["review-analysis", command, *args, *extra]), redirect_stdout(output):
                code = main()
            return code, json.loads(output.getvalue())

        before = self.db.read_bytes()
        with patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("must not read .env")):
            target = self.root / "outputs" / "trend.json"
            code, result = cli("trend", "--period", "day", "--output", str(target))
            self.assertEqual((code, result["status"], result["summary"]["review_count"]), (0, "ok", 6))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), result)
            saved = target.read_bytes()
            self.assertEqual(cli("trend", "--output", str(target))[0], 1)
            self.assertEqual(target.read_bytes(), saved)
            self.assertEqual(cli("trend", "--output", str(self.db))[0], 1)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = str(self.db) + suffix
                self.assertEqual(cli("trend", "--output", sidecar)[0], 1)
            self.assertEqual(cli("compare-products")[1]["products"][0]["product_id"], "P1")
            self.assertEqual(cli("evidence", "--sentiment", "all")[1]["total"], 3)
            self.assertEqual(cli("top-issues", "--baseline-from", "2022-12-01", "--baseline-to", "2022-12-31")[0], 0)
            self.assertEqual(cli("trend", "--date-from", "invalid")[0], 1)
            with patch.object(sys, "argv", ["review-analysis", "trend"]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
            self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.db.read_bytes(), before)
        child = subprocess.run([sys.executable, "-m", "data_analysis_agent.cli.analytics", "trend", *args,
                                "--period", "month"], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)["buckets"][0]["bucket"], "2023-01")


if __name__ == "__main__":
    unittest.main()
