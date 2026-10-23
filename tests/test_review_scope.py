"""共享标注/查询范围及有效观点的离线检查。"""

import io
import json
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

import test_review_labels as label_tests
from test_review_workbench import IDENTITY, WorkbenchFixture, opinion

from data_analysis_agent.cli.labeling import main
from data_analysis_agent.reviews.label_store import labeling_status, pending_reviews, query_review_opinions
from data_analysis_agent.reviews.labeling import PROMPT_VERSION, run_labeling
from data_analysis_agent.reviews.loader import ReviewDataError
from data_analysis_agent.reviews.store import import_amazon_jsonl


class ReviewScopeTests(WorkbenchFixture, unittest.TestCase):
    def query(self, **kwargs):
        identity = kwargs.pop("identity", IDENTITY)
        return query_review_opinions(self.db, **identity, **kwargs)

    def progress(self, **kwargs):
        identity = kwargs.pop("identity", IDENTITY)
        return labeling_status(self.db, **identity, **kwargs)

    def test_inclusive_utc_dates_are_shared_by_pending_status_and_query(self):
        identity = {**IDENTITY, "model": "new-profile"}
        filters = {"product_ids": [" P2 ", "P3", "P2"],
                   "date_from": "2023-01-02", "date_to": "2023-01-02"}
        pending = pending_reviews(self.db, **identity, limit=20, **filters)
        result = self.query(identity=identity, **filters)
        status = self.progress(identity=identity, **filters)
        self.assertEqual([r["id"] for r in pending], [2, 6])
        self.assertCountEqual([r["id"] for r in result["items"]], [2, 6])
        self.assertEqual(result["scope"], status["scope"])
        self.assertEqual(status["scope"]["product_ids"], ["P2", "P3"])
        self.assertEqual(status["eligible_text_reviews"], 2)
        self.assertEqual(status["profiles"][0]["unprocessed_reviews"], 2)
        self.assertEqual(status["profiles"][0]["attempts"], 0)

    def test_as_of_exact_time_offsets_dates_and_empty_replay(self):
        for cutoff in ("2023-01-02T12:00:00Z", "2023-01-02T20:00:00+08:00",
                       "2023-01-02T12:00:00.000500+00:00"):
            with self.subTest(cutoff=cutoff):
                result = self.query(as_of=cutoff)
                self.assertEqual([r["id"] for r in result["items"]], [6, 1])
                self.assertEqual(self.progress(as_of=cutoff)["review_count"], 2)
                # 标注是在之后生成的；as_of 表示发布可见性，而不是处理时间。
                self.assertTrue(all(r["processing_status"] == "success" for r in result["items"]))
        self.assertEqual([r["id"] for r in self.query(as_of="2023-01-02")["items"]], [2, 6, 1])
        self.assertEqual([r["id"] for r in self.query(as_of="2023-01-01T00:00:00Z")["items"]], [1])
        self.assertEqual(self.query(date_from="2023-01-02", as_of="2023-01-01")["total"], 0)
        self.assertEqual(self.progress(as_of="2022-12-31")["profiles"][0]["unprocessed_reviews"], 0)

    def test_run_date_filters_incremental_skip_and_scoped_progress(self):
        requests = []
        filters = {"date_from": "2023-01-02", "date_to": "2023-01-02", "as_of": "2023-01-02"}
        with label_tests.ReviewLabelTests.mock_model(self, [label_tests.response([])] * 2, requests):
            result = run_labeling(self.db, settings=label_tests.SETTINGS, **filters)
        self.assertEqual([r["review_id"] for r in result["results"]], [2, 6])
        self.assertEqual((result["model_calls"], len(requests)), (2, 2))
        identity = {"model": label_tests.SETTINGS.model, "base_url": label_tests.SETTINGS.base_url,
                    "prompt_version": PROMPT_VERSION}
        status = self.progress(identity=identity, **filters)
        self.assertEqual(status["profiles"][0]["successful_reviews"], 2)
        self.assertEqual(status["profiles"][0]["unprocessed_reviews"], 0)
        self.assertEqual(result["scope"], {k: v for k, v in status["scope"].items() if k not in identity})
        with patch("data_analysis_agent.reviews.labeling.OpenAI") as client:
            self.assertEqual(run_labeling(self.db, settings=label_tests.SETTINGS, **filters)["model_calls"], 0)
            client.assert_not_called()

    def test_failed_retry_remains_in_scope_and_version_isolation_is_preserved(self):
        # 评论 3 在该 IDENTITY 下失败；评论 5 在该身份下处于待处理状态。
        filters = {"product_ids": ["P1"], "date_from": "2023-01-03", "date_to": "2023-01-05"}
        self.assertEqual(pending_reviews(self.db, **IDENTITY, limit=20, **filters), [])
        retried = pending_reviews(self.db, **IDENTITY, limit=20, retry_failed=True, **filters)
        self.assertEqual([r["id"] for r in retried], [3])
        self.assertEqual(self.progress(**filters)["profiles"][0]["failed_reviews"], 1)
        for field, value in (("model", "other"), ("base_url", "https://other.invalid/v1"),
                             ("prompt_version", "other-prompt")):
            identity = {**IDENTITY, field: value}
            self.assertEqual([r["id"] for r in pending_reviews(self.db, **identity, limit=20, **filters)], [3])
            self.assertEqual(self.progress(identity=identity, **filters)["profiles"][0]["failed_reviews"], 0)

    def test_run_retry_and_cutoff_forwarding_with_real_mocked_sdk(self):
        requests = []
        filters = {"product_ids": ["P2"], "date_from": "2023-01-05", "as_of": "2023-01-05"}
        with label_tests.ReviewLabelTests.mock_model(self, [label_tests.response(content="invalid")], requests):
            first = run_labeling(self.db, settings=label_tests.SETTINGS, **filters)
        self.assertEqual(first["failed_reviews"], 1)
        with patch("data_analysis_agent.reviews.labeling.OpenAI") as client:
            self.assertEqual(run_labeling(self.db, settings=label_tests.SETTINGS, **filters)["selected_reviews"], 0)
            client.assert_not_called()
        with label_tests.ReviewLabelTests.mock_model(self, [label_tests.response([])], requests):
            retry = run_labeling(self.db, settings=label_tests.SETTINGS, retry_failed=True, **filters)
        self.assertEqual([r["review_id"] for r in retry["results"]], [5])
        self.assertEqual(retry["successful_reviews"], 1)

    def test_human_latest_snapshot_empty_and_model_fallback_match_workbench(self):
        original = self.model_snapshot()
        revision = self.save(expected_revision_id=None, opinions=[opinion("quality", "negative", "包装破了")])
        filters = {"product_ids": ["P1"], "date_from": "2023-01-01", "date_to": "2023-01-01"}
        item = self.query(**filters)["items"][0]
        self.assertEqual((item["opinion_source"], item["revision_id"]), ("human", revision["revision_id"]))
        self.assertEqual(item["opinions"][0]["aspect"], "quality")
        self.assertEqual(item["opinions"], self.browse(date_to="2023-01-01")["items"][0]["opinions"])
        status = self.progress(**filters)["profiles"][0]
        self.assertEqual((status["opinion_count"], status["effective_opinion_count"], status["reviewed_reviews"]), (3, 1, 1))
        self.save(expected_revision_id=revision["revision_id"], opinions=[])
        item = self.query(**filters)["items"][0]
        self.assertEqual((item["opinion_source"], item["opinions"]), ("human", []))
        self.assertEqual(self.progress(**filters)["profiles"][0]["effective_opinion_count"], 0)
        fallback = self.query(product_ids=["P2"], date_to="2023-01-02")["items"][0]
        self.assertEqual((fallback["opinion_source"], len(fallback["opinions"])), ("model", 3))
        self.assertEqual(self.model_snapshot(), original)

    def test_other_profiles_and_changed_content_do_not_reuse_human_results(self):
        self.save(expected_revision_id=None, opinions=[])
        for field, value in (("model", "B"), ("base_url", "https://other.invalid/v1"),
                             ("prompt_version", "B")):
            item = self.query(identity={**IDENTITY, field: value}, date_to="2023-01-01")["items"][0]
            self.assertEqual((item["processing_status"], item["opinion_source"], item["opinions"]), ("pending", None, []))
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("UPDATE reviews SET content_sha256='changed' WHERE id=1")
        item = self.query(date_to="2023-01-01")["items"][0]
        self.assertEqual((item["processing_status"], item["revision_id"], item["opinions"]), ("pending", None, []))
        profile = self.progress(date_to="2023-01-01")["profiles"][0]
        self.assertEqual((profile["successful_attempts"], profile["successful_reviews"], profile["reviewed_reviews"]), (1, 0, 0))

    def test_optional_human_tables_fall_back_without_schema_writes(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("DROP TABLE review_human_opinions")
            con.execute("DROP TABLE review_human_reviews")
            con.execute("DROP TABLE review_workbench_schema")
        before = self.db.read_bytes()
        result = self.query(date_to="2023-01-01")
        self.assertEqual(result["items"][0]["opinion_source"], "model")
        profile = self.progress(date_to="2023-01-01")["profiles"][0]
        self.assertEqual((profile["opinion_count"], profile["effective_opinion_count"]), (3, 3))
        self.assertEqual(self.db.read_bytes(), before)
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("CREATE TABLE review_workbench_schema(version INTEGER)")
        with self.assertRaisesRegex(ReviewDataError, "不完整"):
            self.query()

    def test_source_dataset_scope_pagination_and_evidence_are_explicit(self):
        source = self.root / "other.jsonl"
        source.write_text(json.dumps(self.records[0]), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source, dataset_version="other-version")
        filters = {"product_ids": ["P1"], "source": "amazon_reviews_2023", "dataset_version": "other-version"}
        result = self.query(**filters)
        self.assertEqual((result["total"], result["items"][0]["id"]), (1, 7))
        self.assertEqual(self.progress(**filters)["review_count"], 1)
        self.assertEqual(pending_reviews(self.db, **IDENTITY, limit=20, **filters)[0]["id"], 7)
        result = self.query(dataset_version="amazon-reviews-2023", page_size=2, page=999)
        self.assertEqual((result["total"], result["pages"], result["page"]), (6, 3, 3))
        for item in result["items"]:
            self.assertTrue(item["evidence_ref"].endswith("#line=" + str(item["line_number"])))
            self.assertNotIn("user_id", item)
            for value in item["opinions"]:
                self.assertEqual(item["text"][value["quote_start"]:value["quote_end"]], value["quote"])

    def test_invalid_scopes_fail_before_configuration_or_model_access(self):
        bad = [{"date_from": "2023-02-30"}, {"date_from": "20230101"}, {"date_to": ""},
               {"date_from": True}, {"date_to": "9999-12-31"},
               {"date_from": "2023-01-03", "date_to": "2023-01-02"},
               {"as_of": "2023-01-02T12:00:00"}, {"as_of": "not-a-date"},
               {"as_of": "2023-01-02T12:00:00+00:60"}, {"as_of": "2023-01-02T12:00:00+24:00"},
               {"as_of": "9999-12-31"}, {"as_of": True}, {"product_ids": "P1"},
               {"product_ids": [""]}, {"source": ""}, {"dataset_version": 1}]
        with patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("must not read .env")), \
                patch("data_analysis_agent.reviews.labeling.OpenAI", side_effect=AssertionError("must not call model")):
            for filters in bad:
                with self.subTest(filters=filters):
                    for call in (self.query, self.progress, lambda **kw: run_labeling(self.db, **kw)):
                        with self.assertRaises(ReviewDataError):
                            call(**filters)
        with self.assertRaises(ReviewDataError):
            labeling_status(self.db, model=IDENTITY["model"])
        for filters in ({"page": True}, {"page_size": 101}):
            with self.assertRaises(ReviewDataError):
                self.query(**filters)
        self.assertEqual(self.query(product_ids=["P1' OR 1=1 --"])["total"], 0)

    def test_cli_status_query_are_read_only_without_env_and_run_forwards_filters(self):
        def cli(*args):
            output = io.StringIO()
            with patch.object(sys, "argv", ["review-label", *args]), redirect_stdout(output):
                code = main()
            return code, json.loads(output.getvalue())

        args = ["--db", str(self.db), "--product-id", "P1", "--date-from", "2023-01-01",
                "--date-to", "2023-01-03", "--as-of", "2023-01-02T00:00:00Z"]
        model = ["--model", IDENTITY["model"], "--base-url", IDENTITY["base_url"],
                 "--prompt-version", IDENTITY["prompt_version"]]
        before = self.db.read_bytes()
        with patch("data_analysis_agent.reviews.labeling.read_settings", side_effect=AssertionError("must not read .env")):
            code, status = cli("status", *args, *model)
            self.assertEqual((code, status["review_count"]), (0, 1))
            code, query = cli("query", *args, *model)
            self.assertEqual((code, query["total"]), (0, 1))
            self.assertEqual(status["scope"], query["scope"])
            self.assertEqual(cli("status", *args, "--model", "incomplete")[0], 1)
            self.assertEqual(cli("run", *args, "--date-to", "invalid")[0], 1)
        self.assertEqual(self.db.read_bytes(), before)
        requests = []
        with patch("data_analysis_agent.reviews.labeling.read_settings", return_value={
            "LLM_MODEL_ID": label_tests.SETTINGS.model, "LLM_API_KEY": label_tests.FAKE_KEY,
            "LLM_BASE_URL": label_tests.SETTINGS.base_url,
        }), label_tests.ReviewLabelTests.mock_model(self, [label_tests.response([])], requests):
            code, run = cli("run", *args)
        self.assertEqual((code, run["selected_reviews"], len(requests)), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
