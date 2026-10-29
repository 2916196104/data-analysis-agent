"""数据集摘要和分析统计共用口径；仅使用临时 SQLite。"""

import hashlib
import json
import unittest
from contextlib import closing

from data_analysis_agent.reviews.analytics import get_trend
from data_analysis_agent.reviews.human_review import workbench_options
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.store import connect_review_database, import_amazon_jsonl
from test_review_workbench import IDENTITY, WorkbenchFixture


class DatasetSummaryTests(WorkbenchFixture, unittest.TestCase):
    def summaries(self):
        options = workbench_options(self.db)
        return options, {(item["source"], item["dataset_version"]): item for item in options["datasets"]}

    def test_dataset_identity_dates_counts_and_unlabeled_versions(self):
        csv = self.root / "same-version.csv"
        csv.write_text("product_id,text,published_at\nP1,CSV only,2019-01-01T00:30:00+08:00\n", encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=csv, dataset_version="amazon-reviews-2023")
        source = self.root / "old.jsonl"
        source.write_text(json.dumps({**self.records[0], "timestamp": 1514764800000}), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=source, dataset_version="older")
        options, summaries = self.summaries()
        original = summaries[("amazon_reviews_2023", "amazon-reviews-2023")]
        self.assertEqual((original["review_count"], original["product_count"], original["eligible_text_reviews"]), (6, 3, 5))
        self.assertEqual(original["published_at_start"][:10], "2023-01-01")
        self.assertEqual(original["published_at_end"][:10], "2023-01-05")
        csv_summary = summaries[("reviews_csv", "amazon-reviews-2023")]
        self.assertEqual((csv_summary["review_count"], csv_summary["product_count"]), (1, 1))
        self.assertEqual(csv_summary["published_at_start"][:10], "2018-12-31")
        for key in (("reviews_csv", "amazon-reviews-2023"), ("amazon_reviews_2023", "older")):
            self.assertEqual(summaries[key]["profiles"], [{"profile_id": self.profile, "successful_reviews": 0, "labeling_coverage": 0.0}])

    def test_profiles_are_isolated_by_full_identity_and_match_analysis(self):
        for change in ({"model": "another-model"}, {"base_url": "https://other.invalid/v1"}, {"prompt_version": "another-prompt"}):
            identity = {**IDENTITY, **change}
            reviews = pending_reviews(self.db, **identity, limit=10)
            save_annotation(self.db, reviews[0], **identity, status="failed", error_code="test")
            save_annotation(self.db, reviews[0], **identity, status="success", opinions=[])
        options, summaries = self.summaries()
        dataset = summaries[("amazon_reviews_2023", "amazon-reviews-2023")]
        self.assertEqual(len(dataset["profiles"]), 4)
        for profile in options["profiles"]:
            stored = next(item for item in dataset["profiles"] if item["profile_id"] == profile["profile_id"])
            actual = get_trend(self.db, model=profile["requested_model"], base_url=profile["base_url"],
                               prompt_version=profile["prompt_version"], source=dataset["source"],
                               dataset_version=dataset["dataset_version"], date_from="2023-01-01", date_to="2023-01-05")["summary"]
            self.assertEqual(stored["successful_reviews"], actual["successful_reviews"])
            self.assertEqual(stored["labeling_coverage"], actual["labeling_coverage"])
        original = next(item for item in dataset["profiles"] if item["profile_id"] == self.profile)
        self.assertEqual(original["successful_reviews"], 3)
        self.assertEqual(original["labeling_coverage"], 0.6)

    def test_empty_text_and_metadata_only_preserve_null_coverage(self):
        empty = self.root / "empty.jsonl"
        empty.write_text(json.dumps({**self.records[0], "text": ""}), encoding="utf-8")
        import_amazon_jsonl(self.db, reviews_path=empty, dataset_version="empty-text")
        meta = self.root / "only-meta.jsonl"
        meta.write_text(json.dumps({"parent_asin": "P0", "title": "Metadata only"}), encoding="utf-8")
        import_amazon_jsonl(self.db, meta_path=meta, dataset_version="only-meta")
        _, summaries = self.summaries()
        empty = summaries[("amazon_reviews_2023", "empty-text")]
        self.assertEqual((empty["review_count"], empty["eligible_text_reviews"]), (1, 0))
        meta = summaries[("amazon_reviews_2023", "only-meta")]
        self.assertEqual((meta["review_count"], meta["product_count"]), (0, 1))
        self.assertIsNone(meta["published_at_start"])
        self.assertIsNone(meta["published_at_end"])
        for dataset in (empty, meta):
            self.assertEqual(dataset["profiles"][0]["successful_reviews"], 0)
            self.assertIsNone(dataset["profiles"][0]["labeling_coverage"])

    def test_human_empty_snapshot_does_not_erase_success_and_stale_content_does(self):
        self.save(expected_revision_id=None, opinions=[])
        _, summaries = self.summaries()
        dataset = summaries[("amazon_reviews_2023", "amazon-reviews-2023")]
        self.assertEqual(dataset["profiles"][0]["successful_reviews"], 3)
        with closing(connect_review_database(self.db)) as connection, connection:
            connection.execute("UPDATE reviews SET text=?,content_sha256=? WHERE id=?", ("New content", "new-hash", 1))
        _, summaries = self.summaries()
        self.assertEqual(summaries[("amazon_reviews_2023", "amazon-reviews-2023")]["profiles"][0]["successful_reviews"], 2)

    def test_summary_is_readonly_and_independent_of_selected_profile(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        a = workbench_options(self.db)
        b = workbench_options(self.db, self.profile)
        self.assertEqual(a["datasets"], b["datasets"])
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()

