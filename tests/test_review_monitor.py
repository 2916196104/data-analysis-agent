"""Monitoring acceptance tests on temporary simulated data, without external model calls."""

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.reviews import monitoring as monitor
from data_analysis_agent.reviews.analytics import get_trend
from data_analysis_agent.reviews.label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.reviews.labeling import validate_opinions
from data_analysis_agent.reviews.loader import ReviewDataError
from data_analysis_agent.cli.monitoring import main
from data_analysis_agent.reviews.monitor_store import (
    get_alert, get_analysis_run, init_monitoring, list_alerts, list_analysis_runs, set_alert_status,
)
from data_analysis_agent.reviews.store import import_amazon_jsonl, init_database
from data_analysis_agent.reviews.human_review import init_workbench, save_human_review

IDENTITY = {"model": "SIMULATED", "base_url": "https://offline.invalid/v1", "prompt_version": "test-v1"}
RULES = {"min_samples": 2, "min_coverage": 0.8, "min_negative_count": 2, "min_increase_pp": 20,
         "cooldown_days": 30}
SOURCE = "amazon_reviews_2023"
VERSION = "amazon-reviews-2023"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "reviews.sqlite3"
        self.serial = 0
        self.ids, self.annotations = {}, {}
        init_database(self.db)
        init_labeling(self.db)
        init_workbench(self.db)
        init_monitoring(self.db)
        rows = []
        for product, month, negatives in (
                ("P1", "2019-12", 0), ("P1", "2020-01", 3), ("P1", "2020-02", 4),
                ("P1", "2020-03", 4), ("P2", "2019-12", 0), ("P2", "2020-01", 0)):
            for index, day in enumerate((1, 10, 20, 28)):
                timestamp = f"{month}-{day:02d}T00:00:00+00:00"
                if month == "2020-01" and index == 2:
                    timestamp = "2020-01-31T23:59:59.999+00:00"
                rows.append((product, timestamp, "negative" if index < negatives else "positive"))
        rows.append(("FUTURE", "2020-04-01T00:00:00+00:00", "negative"))
        self.seed(rows)

    def seed(self, rows, *, dataset_version=VERSION, identity=None):
        identity = identity or IDENTITY
        records, labels = [], {}
        for product, timestamp, label in rows:
            self.serial += 1
            uid = str(self.serial)
            text = "" if label == "empty" else "好用，包装破了，盒子损坏。"
            records.append({"parent_asin": product, "user_id": uid, "text": text, "rating": 3,
                            "timestamp": int(datetime.fromisoformat(timestamp).timestamp() * 1000)})
            labels[uid] = (product, timestamp, label)
        path = self.root / f"simulated-{self.serial}.jsonl"
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records), encoding="utf-8")
        imported = import_amazon_jsonl(self.db, reviews_path=path, dataset_version=dataset_version)
        self.assertEqual(imported["status"], "ok", imported)
        self.assertEqual(imported["imports"][0]["inserted_records"], len(records))
        with closing(sqlite3.connect(self.db)) as con:
            by_id = {rid: labels[uid] for rid, uid in con.execute("SELECT id,user_id FROM reviews") if uid in labels}
        for row in pending_reviews(self.db, **identity, limit=1000):
            if row["id"] not in by_id:
                continue
            product, timestamp, label = by_id[row["id"]]
            self.ids[product, timestamp, dataset_version] = row["id"]
            if label in ("unlabeled", "empty"):
                continue
            if label == "failed":
                save_annotation(self.db, row, **identity, status="failed", error_code="invalid_output")
                continue
            opinions = ([{"aspect": "packaging", "sentiment": "negative", "quote": quote}
                         for quote in ("包装破了", "盒子损坏")] if label == "negative" else
                        [{"aspect": "effectiveness", "sentiment": "positive", "quote": "好用"}])
            self.annotations[row["id"]] = save_annotation(
                self.db, row, **identity, status="success",
                opinions=validate_opinions(json.dumps({"opinions": opinions}), row["text"]))

    def options(self, **changes):
        return {**IDENTITY, "date_from": "2020-01-01", "date_to": "2020-01-31",
                "baseline_from": "2019-12-01", "baseline_to": "2019-12-31",
                "product_ids": ["P1"], "source": SOURCE, "dataset_version": VERSION, "rules": RULES, **changes}

    def run_monitor(self, **changes):
        return monitor.run_analysis(self.db, **self.options(**changes))

    def replay(self, **changes):
        return monitor.replay_monthly(self.db, **{**IDENTITY, "month_from": "2020-01",
                                     "month_to": "2020-03", "product_ids": ["P1"], "rules": RULES, **changes})

    def counts(self):
        with closing(sqlite3.connect(self.db)) as con:
            return tuple(con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
                "review_analysis_runs", "review_alerts", "review_alert_occurrences", "review_alert_actions"))

    def raw_data(self):
        with closing(sqlite3.connect(self.db)) as con:
            return [con.execute("SELECT * FROM " + table + " ORDER BY id").fetchall() for table in (
                "reviews", "review_annotations", "review_opinions", "review_human_reviews", "review_human_opinions")]

    def test_explicit_init_read_only_queries_and_schema_versions(self):
        other = self.root / "new.sqlite3"
        init_database(other)
        with self.assertRaisesRegex(ReviewDataError, "观点表"):
            init_monitoring(other)
        init_labeling(other)
        before = other.read_bytes()
        with self.assertRaisesRegex(ReviewDataError, "监控表"):
            list_alerts(other)
        with self.assertRaisesRegex(ReviewDataError, "监控表"):
            monitor.run_analysis(other, **self.options())
        self.assertEqual(other.read_bytes(), before)
        self.assertTrue(init_monitoring(other)["created"])
        self.assertFalse(init_monitoring(other)["created"])
        with closing(sqlite3.connect(other)) as con, con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT version FROM review_labeling_schema").fetchone()[0], 1)
            con.execute("UPDATE review_monitor_schema SET version=2")
        with self.assertRaisesRegex(ReviewDataError, "版本"):
            init_monitoring(other)
        with closing(sqlite3.connect(other)) as con, con:
            con.execute("DROP TABLE review_alert_actions")
        with self.assertRaisesRegex(ReviewDataError, "不完整"):
            init_monitoring(other)

    def test_run_reuses_four_tools_and_freezes_counts_identity_and_exact_evidence(self):
        before = self.raw_data()
        with patch.object(monitor, "get_trend", wraps=monitor.get_trend) as trend, \
                patch.object(monitor, "get_top_issues", wraps=monitor.get_top_issues) as issues, \
                patch.object(monitor, "compare_products", wraps=monitor.compare_products) as compare, \
                patch.object(monitor, "get_evidence", wraps=monitor.get_evidence) as evidence, \
                patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=AssertionError("no model")), \
                patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("no .env")):
            receipt = self.run_monitor(product_ids=["P1", "P2"])
            self.assertTrue(all(tool.called for tool in (trend, issues, compare, evidence)))
            connections = [call.args[0] for tool in (trend, issues, compare, evidence) for call in tool.call_args_list]
            self.assertTrue(all(connection is connections[0] for connection in connections))
        run = get_analysis_run(self.db, receipt["run_id"])
        self.assertEqual(receipt["new_alerts"], 1)
        self.assertEqual(run["results"]["summary"], {"input_reviews": 16, "products": 2,
                                                    "triggered": 1, "normal": 11, "insufficient_data": 0})
        self.assertEqual(run["request"]["scope"]["model"], IDENTITY["model"])
        self.assertEqual(run["request"]["baseline_scope"]["date_from"], "2019-12-01")
        alert = get_alert(self.db, receipt["alert_occurrences"][0]["alert_id"])
        detail = alert["occurrences"][0]["details"]
        self.assertEqual(detail["issue"]["negative_review_count"], 3)  # two quotes per review
        self.assertEqual(detail["issue"]["negative_rate_change_pp"], 75.0)
        self.assertEqual((detail["evidence"]["total"], detail["evidence"]["total_opinions"]), (3, 6))
        for item in detail["evidence"]["items"]:
            self.assertNotIn("user_id", item)
            self.assertIsNotNone(item["annotation_id"])
            self.assertIn("#line=", item["evidence_ref"])
            for quote in item["opinions"]:
                self.assertEqual(item["text"][quote["quote_start"]:quote["quote_end"]], quote["quote"])
        self.assertEqual(self.raw_data(), before)

    def test_duplicate_and_concurrent_retries_create_one_run_and_one_alert(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: self.run_monitor(), range(2)))
        self.assertEqual(receipts[0]["run_id"], receipts[1]["run_id"])
        self.assertEqual(sorted(row["reused"] for row in receipts), [False, True])
        self.assertEqual(sum(row["new_alerts"] for row in receipts), 1)
        self.assertEqual(self.counts(), (1, 1, 1, 0))
        again = self.run_monitor(product_ids=["P1", "P1"])
        self.assertTrue(again["reused"])
        self.assertEqual(again["new_alerts"], 0)
        before = get_analysis_run(self.db, again["run_id"])
        self.seed([("unrelated", "2021-01-01T00:00:00+00:00", "negative")])
        self.assertEqual(self.run_monitor()["run_id"], again["run_id"])
        self.assertEqual(get_analysis_run(self.db, again["run_id"]), before)

    def test_sample_and_coverage_gates_missing_baseline_and_unlabeled_not_positive(self):
        small = self.run_monitor(rules={**RULES, "min_samples": 5})
        self.assertEqual((small["new_alerts"], small["summary"]["insufficient_data"]), (0, 6))
        self.seed([("P1", "2020-01-02T00:00:00+00:00", label) for label in ("unlabeled", "failed", "empty")])
        result = self.run_monitor()
        run = get_analysis_run(self.db, result["run_id"])
        summary = run["results"]["trend"]["summary"]
        self.assertEqual((summary["successful_reviews"], summary["eligible_text_reviews"], summary["review_count"]),
                         (4, 6, 7))
        self.assertEqual((summary["negative_rate"], summary["labeling_coverage"]), (0.75, 0.666667))
        self.assertEqual(result["summary"]["insufficient_data"], 6)
        self.assertEqual(result["new_alerts"], 0)
        issues = run["results"]["products"][0]["evaluations"]
        self.assertTrue(all(not row["checks"]["current_coverage"] for row in issues))
        baseline = self.run_monitor(baseline_from="2019-11-01", baseline_to="2019-11-30")
        self.assertEqual(baseline["summary"]["insufficient_data"], 6)
        missing = self.run_monitor(product_ids=["missing"])
        self.assertEqual((missing["summary"]["products"], missing["new_alerts"]), (0, 0))
        run = get_analysis_run(self.db, missing["run_id"])
        self.assertEqual(run["results"]["comparison"]["no_reviews_product_ids"], ["missing"])
        self.assertEqual(self.counts()[1], 0)

    def test_exact_rule_boundaries_and_no_negative_count_or_increase_do_not_alert(self):
        at_boundary = self.run_monitor(rules={**RULES, "min_negative_count": 3, "min_increase_pp": 75})
        self.assertEqual(at_boundary["new_alerts"], 1)
        for rules in ({**RULES, "min_negative_count": 4}, {**RULES, "min_increase_pp": 75.000001}):
            result = self.run_monitor(rules=rules)
            self.assertEqual((result["new_alerts"], result["summary"]["normal"]), (0, 6))
        healthy = self.run_monitor(product_ids=["P2"])
        self.assertEqual(healthy["summary"]["normal"], 6)

    def test_human_corrections_create_new_run_but_old_snapshot_and_status_stay(self):
        first = self.run_monitor()
        old = get_analysis_run(self.db, first["run_id"])
        alert_id = first["alert_occurrences"][0]["alert_id"]
        set_alert_status(self.db, alert_id, status="confirmed", actor="tester")
        rid = self.ids["P1", "2020-01-01T00:00:00+00:00", VERSION]
        correction = save_human_review(self.db, rid, annotation_id=self.annotations[rid],
                                       expected_revision_id=None, action="corrected", reviewer="tester", opinions=[])
        second = self.run_monitor()
        self.assertFalse(second["reused"])
        self.assertEqual((second["new_alerts"], second["merged_alerts"]), (0, 1))
        new = get_analysis_run(self.db, second["run_id"])
        frozen = next(row for row in new["inputs"] if row["review_id"] == rid)
        self.assertEqual((frozen["revision_id"], frozen["opinions"]), (correction["revision_id"], []))
        self.assertEqual(new["results"]["trend"]["summary"]["negative_review_count"], 2)
        self.assertEqual(get_analysis_run(self.db, first["run_id"]), old)
        self.assertEqual(get_alert(self.db, alert_id)["status"], "confirmed")
        self.assertEqual(self.counts(), (2, 1, 2, 1))
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE reviews SET content_sha256='changed',text='new text' WHERE id=?", (rid,))
        third = self.run_monitor()
        self.assertEqual(third["summary"]["insufficient_data"], 6)
        self.assertEqual(get_analysis_run(self.db, first["run_id"]), old)

    def test_exact_coverage_and_baseline_coverage_both_gate_alerts(self):
        self.seed([("P1", "2020-01-02T00:00:00+00:00", "unlabeled"),
                   ("P1", "2020-01-03T00:00:00+00:00", "unlabeled")])
        rounded_up = self.run_monitor(rules={**RULES, "min_coverage": 0.666667})
        self.assertEqual(rounded_up["summary"]["insufficient_data"], 6)
        below = self.run_monitor(rules={**RULES, "min_coverage": 0.666666})
        self.assertEqual(below["new_alerts"], 1)
        self.seed([("P1", "2019-12-02T00:00:00+00:00", "failed"),
                   ("P1", "2019-12-03T00:00:00+00:00", "unlabeled"),
                   ("P1", "2019-12-04T00:00:00+00:00", "unlabeled")])
        gated = self.run_monitor(rules={**RULES, "min_coverage": 0.666666})
        self.assertEqual(gated["summary"]["insufficient_data"], 6)
        evaluations = get_analysis_run(self.db, gated["run_id"])["results"]["products"][0]["evaluations"]
        self.assertTrue(all(not row["checks"]["baseline_coverage"] for row in evaluations))

    def test_evidence_sample_is_bounded_but_full_inputs_and_counts_are_preserved(self):
        self.seed([("MANY", "2019-12-01T00:00:00+00:00", "positive") for _ in range(2)]
                  + [("MANY", "2020-01-01T00:00:00+00:00", "negative") for _ in range(25)])
        receipt = self.run_monitor(product_ids=["MANY"])
        alert = get_alert(self.db, receipt["alert_occurrences"][0]["alert_id"])
        evidence = alert["occurrences"][0]["details"]["evidence"]
        self.assertEqual((evidence["total"], len(evidence["items"]), evidence["pages"]), (25, 20, 2))
        run = get_analysis_run(self.db, receipt["run_id"])
        self.assertEqual(len(run["inputs"]), 27)
        self.assertEqual(sum(any(o["sentiment"] == "negative" for o in row["opinions"]) for row in run["inputs"]), 25)

    def test_human_tables_optional_and_zero_cooldown_does_not_duplicate_same_instant(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            for table in ("review_human_opinions", "review_human_reviews", "review_workbench_schema"):
                con.execute("DROP TABLE " + table)
        rules = {**RULES, "cooldown_days": 0}
        first = self.run_monitor(rules=rules)
        self.assertEqual(first["new_alerts"], 1)
        equivalent_time = self.run_monitor(rules=rules, as_of="2020-01-31T23:59:59.999Z")
        self.assertEqual((equivalent_time["new_alerts"], equivalent_time["merged_alerts"]), (0, 1))
        tomorrow = self.run_monitor(rules=rules, as_of="2020-02-01")
        self.assertEqual(tomorrow["new_alerts"], 1)

    def test_cutoff_is_shared_by_inputs_all_tools_and_evidence(self):
        result = self.run_monitor(as_of="2020-02-01T07:59:59.998+08:00")
        run = get_analysis_run(self.db, result["run_id"])
        self.assertEqual(run["as_of"], "2020-01-31T23:59:59.998+00:00")
        self.assertEqual(len(run["inputs"]), 7)
        self.assertEqual(run["results"]["trend"]["summary"]["review_count"], 3)
        for item in run["results"]["products"]:
            self.assertEqual(item["top_issues"]["current"]["review_count"], 3)
            for evaluation in item["evaluations"]:
                for quote in evaluation.get("evidence", {}).get("items", []):
                    self.assertLessEqual(quote["published_at"], run["as_of"])
        exact = self.run_monitor(as_of="2020-02-01T07:59:59.999+08:00")
        exact_run = get_analysis_run(self.db, exact["run_id"])
        self.assertEqual(len(exact_run["inputs"]), 8)
        self.assertTrue(all(row["published_at"] <= exact_run["as_of"] for row in exact_run["inputs"]))
        early = self.run_monitor(as_of="2019-11-30")
        self.assertEqual((early["summary"]["input_reviews"], early["new_alerts"]), (0, 0))

    def test_monthly_replay_leap_year_year_boundary_and_no_future_inputs(self):
        result = self.replay(month_from="2019-12", month_to="2020-03")
        self.assertTrue(result["complete"])
        self.assertEqual([row["month"] for row in result["runs"]], ["2019-12", "2020-01", "2020-02", "2020-03"])
        january, february = result["runs"][1:3]
        self.assertEqual(january["request"]["baseline_scope"]["date_from"], "2019-12-01")
        self.assertEqual(february["request"]["scope"]["date_to"], "2020-02-29")
        self.assertEqual((january["new_alerts"], february["merged_alerts"]), (1, 1))
        self.assertEqual(result["runs"][3]["summary"]["triggered"], 0)
        for receipt in result["runs"]:
            run = get_analysis_run(self.db, receipt["run_id"])
            self.assertTrue(all(row["published_at"] <= run["as_of"] for row in run["inputs"]))
            self.assertTrue(all(row["product_id"] != "FUTURE" for row in run["inputs"]))
        counts = self.counts()
        self.assertTrue(all(row["reused"] for row in self.replay(month_from="2019-12")["runs"]))
        self.assertEqual(self.counts(), counts)
        long_baseline = self.replay(month_from="2020-01", month_to="2020-01", baseline_months=2)
        self.assertEqual(long_baseline["runs"][0]["request"]["baseline_scope"]["date_from"], "2019-11-01")

    def test_cooldown_uses_replay_time_first_hit_and_preserves_ignored_status(self):
        first = self.run_monitor()
        alert_id = first["alert_occurrences"][0]["alert_id"]
        set_alert_status(self.db, alert_id, status="ignored", actor="tester", note="调查记录")
        within = self.run_monitor(as_of="2020-02-29")
        self.assertEqual((within["new_alerts"], within["merged_alerts"]), (0, 1))
        self.assertEqual(within["alert_occurrences"][0]["alert_id"], alert_id)
        self.assertEqual(get_alert(self.db, alert_id)["status"], "ignored")
        boundary = self.run_monitor(as_of="2020-03-01")
        self.assertEqual(boundary["new_alerts"], 1)  # exactly 30 days after first hit
        newer = boundary["alert_occurrences"][0]["alert_id"]
        self.assertNotEqual(newer, alert_id)
        self.assertEqual(get_alert(self.db, newer)["status"], "pending")
        old = self.run_monitor(as_of="2020-01-30")
        self.assertEqual(old["new_alerts"], 1)  # backfill never merges into a future alert
        self.assertLess(old["as_of"], get_alert(self.db, alert_id)["first_seen"])
        self.assertEqual(get_alert(self.db, newer)["first_seen"], boundary["as_of"])

    def test_model_prompt_endpoint_dataset_product_and_rule_isolation(self):
        first = self.run_monitor()
        for name in ("model", "base_url", "prompt_version"):
            receipt = self.run_monitor(**{name: "other"})
            self.assertEqual(receipt["summary"]["insufficient_data"], 6)
            self.assertEqual(receipt["new_alerts"], 0)
        alternate = {**IDENTITY, "prompt_version": "second"}
        for row in pending_reviews(self.db, **alternate, limit=1000):
            with closing(sqlite3.connect(self.db)) as con:
                opinion_rows = con.execute(
                    "SELECT aspect,sentiment,quote,quote_start,quote_end FROM review_opinions WHERE annotation_id=?",
                    (self.annotations[row["id"]],)).fetchall()
            opinions = [dict(zip(("aspect", "sentiment", "quote", "quote_start", "quote_end"), values))
                        for values in opinion_rows]
            save_annotation(self.db, row, **alternate, status="success", opinions=opinions)
        second = self.run_monitor(prompt_version="second")
        self.assertEqual(second["new_alerts"], 1)
        self.assertNotEqual(first["alert_occurrences"][0]["alert_id"], second["alert_occurrences"][0]["alert_id"])
        named = self.run_monitor(rules={**RULES, "name": "other-monitor"})
        self.assertEqual(named["new_alerts"], 1)
        self.seed([("P1", "2019-12-01T00:00:00+00:00", "positive"),
                   ("P1", "2019-12-02T00:00:00+00:00", "positive"),
                   ("P1", "2020-01-01T00:00:00+00:00", "negative"),
                   ("P1", "2020-01-02T00:00:00+00:00", "negative")], dataset_version="v2")
        combined = self.run_monitor(dataset_version=None)
        self.assertEqual(combined["summary"]["products"], 2)
        self.assertEqual((combined["new_alerts"], combined["merged_alerts"]), (1, 1))
        versions = {row["dataset_version"] for row in list_alerts(self.db)["items"]}
        self.assertEqual(versions, {VERSION, "v2"})

    def test_confirm_ignore_history_idempotency_readonly_queries_and_filters(self):
        receipt = self.run_monitor()
        alert_id = receipt["alert_occurrences"][0]["alert_id"]
        self.assertTrue(set_alert_status(self.db, alert_id, status="confirmed", actor="甲")["changed"])
        self.assertFalse(set_alert_status(self.db, alert_id, status="confirmed", actor="甲")["changed"])
        self.assertTrue(set_alert_status(self.db, alert_id, status="ignored", actor="乙", note="复查")["changed"])
        before = self.db.read_bytes()
        alert = get_alert(self.db, alert_id)
        self.assertEqual([(r["from_status"], r["to_status"], r["actor"]) for r in alert["actions"]],
                         [("pending", "confirmed", "甲"), ("confirmed", "ignored", "乙")])
        found = list_alerts(self.db, status="ignored", product_id="P1", aspect="packaging", source=SOURCE,
                            dataset_version=VERSION, run_id=receipt["run_id"], page_size=1)
        self.assertEqual((found["total"], found["items"][0]["occurrence_count"]), (1, 1))
        self.assertEqual(list_alerts(self.db, status="pending")["total"], 0)
        self.assertEqual(list_alerts(self.db, product_id="P1' OR 1=1 --")["total"], 0)
        self.assertEqual(list_alerts(self.db, page=2, page_size=1)["items"], [])
        self.assertEqual(list_analysis_runs(self.db, page_size=1)["total"], 1)
        get_analysis_run(self.db, receipt["run_id"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_tool_or_alert_failure_rolls_back_entire_run_and_retry_succeeds(self):
        before = self.counts()
        with patch.object(monitor, "get_evidence", side_effect=ReviewDataError("simulated failure")):
            with self.assertRaisesRegex(ReviewDataError, "simulated"):
                self.run_monitor()
        self.assertEqual(self.counts(), before)
        original = monitor.attach_alert_occurrence

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise sqlite3.OperationalError("simulated persistence failure")

        with patch.object(monitor, "attach_alert_occurrence", side_effect=fail_after_write):
            with self.assertRaises(sqlite3.Error):
                self.run_monitor()
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.run_monitor()["new_alerts"], 1)

    def test_partial_replay_reports_committed_months_and_can_resume_without_duplicates(self):
        original = monitor.run_analysis

        def fail_february(*args, **kwargs):
            if kwargs["date_from"] == "2020-02-01":
                raise ReviewDataError("simulated February failure")
            return original(*args, **kwargs)

        with patch.object(monitor, "run_analysis", side_effect=fail_february):
            result = self.replay()
        self.assertFalse(result["complete"])
        self.assertEqual((len(result["runs"]), result["failed_month"]), (1, "2020-02"))
        self.assertEqual(self.counts(), (1, 1, 1, 0))
        resumed = self.replay()
        self.assertTrue(resumed["complete"])
        self.assertTrue(resumed["runs"][0]["reused"])
        self.assertEqual(self.counts(), (3, 1, 2, 0))

    def test_invalid_input_rejected_before_writes(self):
        before = self.counts()
        for rules in ([], {"unknown": 1}, {"name": ""}, {"min_samples": True}, {"min_coverage": float("nan")},
                      {"min_coverage": float("inf")}, {"min_coverage": 10**400}, {"min_increase_pp": 0},
                      {"min_negative_count": 0}, {"cooldown_days": -1}):
            with self.subTest(rules=rules), self.assertRaises(ReviewDataError):
                self.run_monitor(rules=rules)
        for changes in ({"baseline_to": "2020-01-01"}, {"date_from": None}, {"model": ""},
                        {"as_of": "2020-01-31T00:00:00"}, {"product_ids": "P1"}):
            with self.subTest(changes=changes), self.assertRaises(ReviewDataError):
                self.run_monitor(**changes)
        for changes in ({"month_from": "2020-1"}, {"month_from": "2020-02-01"}, {"month_from": "2020-13"},
                        {"month_to": "2019-12"}, {"baseline_months": 0}, {"baseline_months": True},
                        {"month_from": "0001-01", "month_to": "0001-02"}, {"month_to": "9999-12"},
                        {"month_from": "9999-12", "month_to": "9999-12"}, {"model": ""}):
            with self.subTest(changes=changes), self.assertRaises(ReviewDataError):
                self.replay(**changes)
        for invoke in (lambda: list_alerts(self.db, page=True), lambda: list_alerts(self.db, status="other"),
                       lambda: list_alerts(self.db, aspect=[]), lambda: list_analysis_runs(self.db, page_size=0),
                       lambda: get_alert(self.db, 999), lambda: get_analysis_run(self.db, "missing"),
                       lambda: set_alert_status(self.db, 1, status="pending", actor="甲"),
                       lambda: set_alert_status(self.db, 1, status="ignored", actor=""),
                       lambda: set_alert_status(self.db, 999, status="ignored", actor="甲")):
            with self.assertRaises(ReviewDataError):
                invoke()
        self.assertEqual(self.counts(), before)

    def test_cli_all_commands_json_errors_and_module_entry(self):
        common = ["--model", IDENTITY["model"], "--base-url", IDENTITY["base_url"],
                  "--prompt-version", IDENTITY["prompt_version"], "--product-id", "P1",
                  "--min-samples", "2", "--min-negative-count", "2", "--min-increase-pp", "20"]
        window = ["--date-from", "2020-01-01", "--date-to", "2020-01-31",
                  "--baseline-from", "2019-12-01", "--baseline-to", "2019-12-31"]

        def cli(command, *args):
            output = io.StringIO()
            with patch.object(sys, "argv", ["review-monitor", command, "--db", str(self.db), *args]), \
                    redirect_stdout(output):
                code = main()
            return code, json.loads(output.getvalue())

        self.assertFalse(cli("init")[1]["result"]["created"])
        code, payload = cli("run", *common, *window)
        self.assertEqual((code, payload["status"]), (0, "ok"))
        receipt = payload["result"]
        run_id, alert_id = receipt["run_id"], str(receipt["alert_occurrences"][0]["alert_id"])
        self.assertEqual(cli("runs")[1]["result"]["total"], 1)
        self.assertEqual(cli("run-detail", "--run-id", run_id)[1]["result"]["run_id"], run_id)
        self.assertEqual(cli("alerts", "--status", "pending")[1]["result"]["total"], 1)
        self.assertEqual(cli("alert", "--alert-id", alert_id)[1]["result"]["status"], "pending")
        self.assertEqual(cli("confirm", "--alert-id", alert_id, "--actor", "甲")[1]["result"]["status"], "confirmed")
        self.assertEqual(cli("ignore", "--alert-id", alert_id, "--actor", "乙", "--note", "复查")[0], 0)
        self.assertTrue(cli("replay", *common, "--month-from", "2020-01", "--month-to", "2020-02")[1]["result"]["complete"])
        for args in (("alert", "--alert-id", "999"), ("run", *common, *window, "--min-coverage", "nan")):
            code, payload = cli(*args)
            self.assertEqual((code, payload["status"]), (1, "error"))
        with patch.object(sys, "argv", ["review-monitor", "confirm"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                main()
            self.assertEqual(exc.exception.code, 2)
        with patch("data_analysis_agent.cli.monitoring.replay_monthly", return_value={"complete": False, "runs": []}):
            self.assertEqual(cli("replay", *common, "--month-from", "2020-01", "--month-to", "2020-02")[0], 1)
        child = subprocess.run([sys.executable, "-m", "data_analysis_agent.cli.monitoring", "alerts",
                                "--db", str(self.db), "--status", "ignored"],
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)["status"], "ok")

    def test_legacy_analytics_still_readonly_and_shared_connection_requires_transaction(self):
        before = self.db.read_bytes()
        get_trend(self.db, **IDENTITY, date_from="2020-01-01", date_to="2020-01-31")
        self.assertEqual(self.db.read_bytes(), before)
        with closing(sqlite3.connect(self.db)) as con:
            with self.assertRaisesRegex(ReviewDataError, "事务"):
                get_trend(con, **IDENTITY, date_from="2020-01-01", date_to="2020-01-31")


if __name__ == "__main__":
    unittest.main()
