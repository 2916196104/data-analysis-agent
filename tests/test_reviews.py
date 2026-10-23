"""离线 Amazon 导入测试；每个数据库和输入都是临时夹具。"""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from data_analysis_agent.reviews.loader import (
    ReviewDataError, file_sha256, iter_jsonl, normalize_product, normalize_review,
)
from data_analysis_agent.reviews.store import (
    get_quality_summary, get_review_evidence, import_amazon_jsonl, init_database,
)


def review(**changes):
    return {"parent_asin": "P1", "asin": "V1", "user_id": "U1", "title": "使用反馈",
            "text": "好用，但包装破了。\n客服回复慢。", "rating": 3,
            "timestamp": 1693526400123, "data_source": "SIMULATED", **changes}


def product(**changes):
    return {"parent_asin": "P1", "title": "模拟商品", "main_category": "All Beauty",
            "details": {"Brand": "模拟品牌"}, "data_source": "SIMULATED", **changes}


class ReviewImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = self.root / "reviews.sqlite3"

    def write_jsonl(self, name, rows):
        path = self.root / name
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        if name.endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def import_reviews(self, rows, **options):
        return import_amazon_jsonl(self.db, reviews_path=self.write_jsonl("reviews.jsonl", rows), **options)

    def test_database_requires_explicit_init_and_refuses_other_files(self):
        reviews = self.write_jsonl("reviews.jsonl", [review()])
        with self.assertRaisesRegex(ReviewDataError, "init"):
            import_amazon_jsonl(self.db, reviews_path=reviews)
        self.assertFalse(self.db.exists())
        self.assertTrue(init_database(self.db)["created"])
        self.assertFalse(init_database(self.db)["created"])
        foreign = self.root / "other.sqlite3"
        with closing(sqlite3.connect(foreign)) as connection, connection:
            connection.execute("CREATE TABLE business(value TEXT)")
            connection.execute("INSERT INTO business VALUES ('preserved')")
        before = foreign.read_bytes()
        with self.assertRaises(ReviewDataError):
            init_database(foreign)
        self.assertEqual(foreign.read_bytes(), before)
        with self.assertRaisesRegex(ReviewDataError, "父目录"):
            init_database(self.root / "missing" / "reviews.sqlite3")

    def test_import_both_formats_quality_and_exact_original_evidence(self):
        init_database(self.db)
        reviews = self.root / "reviews.jsonl"
        raw = "  " + json.dumps(review(), ensure_ascii=False) + "  "
        reviews.write_text("\n" + raw + "\n" + json.dumps(
            review(parent_asin="P2", user_id="U2", text="\u3000\t", rating=None)
        ) + "\n", encoding="utf-8-sig")
        metadata = self.write_jsonl("meta.jsonl.gz", [product()])
        result = import_amazon_jsonl(self.db, reviews_path=reviews, meta_path=metadata)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["imports"][1]["blank_lines"], 1)
        quality = result["quality"]
        self.assertEqual(quality["review_count"], 2)
        self.assertEqual(quality["product_count"], 2)
        self.assertEqual(quality["products_without_metadata"], 1)
        self.assertEqual(quality["reviews_without_metadata"], 1)
        self.assertEqual(quality["empty_text_count"], 1)
        self.assertEqual(quality["missing_rating_count"], 1)
        self.assertEqual(quality["published_at_start"], "2023-09-01T00:00:00.123+00:00")
        evidence = get_review_evidence(self.db, 1)
        self.assertEqual(evidence["raw_json"], raw)
        self.assertEqual(evidence["text"], review()["text"])
        self.assertEqual(evidence["line_number"], 2)
        self.assertEqual(evidence["file_sha256"], hashlib.sha256(reviews.read_bytes()).hexdigest())
        self.assertEqual(evidence["evidence_ref"], f"sha256:{evidence['file_sha256']}#line=2")
        reviews.unlink()
        self.assertEqual(get_review_evidence(self.db, 1)["raw_json"], raw)
        self.assertEqual(get_quality_summary(self.db), quality)

    def test_reimport_and_cross_file_duplicates_preserve_first_evidence(self):
        init_database(self.db)
        first = self.import_reviews([review()])
        second = self.import_reviews([review()])
        copied = self.write_jsonl("copy.jsonl.gz", [review(helpful_vote=100, rating=3.0)])
        third = import_amazon_jsonl(self.db, reviews_path=copied)
        self.assertEqual(first["imports"][0]["inserted_records"], 1)
        for result in (second, third):
            self.assertEqual(result["imports"][0]["inserted_records"], 0)
            self.assertEqual(result["imports"][0]["duplicate_records"], 1)
            self.assertEqual(result["quality"]["review_count"], 1)
        self.assertEqual(get_review_evidence(self.db, 1)["file_path"], str(self.root / "reviews.jsonl"))
        changed = self.import_reviews([review(text="编辑后的评论")])
        self.assertEqual(changed["quality"]["review_count"], 2)

    def test_anonymous_reviews_keep_distinct_locations_and_versions(self):
        init_database(self.db)
        result = self.import_reviews([review(user_id=""), review(user_id="")])
        self.assertEqual(result["quality"]["review_count"], 2)
        self.assertEqual(result["quality"]["missing_user_id_count"], 2)
        self.assertEqual(self.import_reviews([review(user_id=""), review(user_id="")])[
            "imports"][0]["duplicate_records"], 2)
        other = self.import_reviews([review(user_id="")], dataset_version="another-snapshot")
        self.assertEqual(other["quality"]["review_count"], 3)
        self.assertEqual(other["quality"]["product_count"], 2)

    def test_metadata_can_fill_placeholders_and_conflicts_do_not_overwrite(self):
        init_database(self.db)
        self.import_reviews([review()])
        metadata = self.write_jsonl("meta.jsonl", [product(), product(), product(title="不同元数据")])
        result = import_amazon_jsonl(self.db, meta_path=metadata)
        imported = result["imports"][0]
        self.assertEqual(imported["inserted_records"], 1)
        self.assertEqual(imported["duplicate_records"], 1)
        self.assertEqual(imported["invalid_records"], 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["quality"]["reviews_without_metadata"], 0)
        self.assertEqual(result["quality"]["product_sample"][0]["title"], "模拟商品")
        with closing(sqlite3.connect(self.db)) as connection:
            stored = connection.execute("SELECT raw_json, line_number FROM products").fetchone()
            self.assertEqual(json.loads(stored[0]), product())
            self.assertEqual(stored[1], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM import_errors").fetchone()[0], 1)

    def test_bad_lines_are_persisted_and_later_good_lines_are_imported(self):
        init_database(self.db)
        path = self.root / "bad.jsonl"
        bad_lines = [b"{broken}\n", b"[]\n", b'\xff\n', b'{"a":1,"a":2}\n',
                     b'{"x":NaN}\n', b'{"x":1e999}\n', b'{"x":"\\ud800"}\n']
        path.write_bytes(b"".join(bad_lines) + json.dumps(review()).encode() + b"\n")
        result = import_amazon_jsonl(self.db, reviews_path=path)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["imports"][0]["invalid_records"], len(bad_lines))
        self.assertEqual(result["quality"]["review_count"], 1)
        self.assertEqual(get_review_evidence(self.db, 1)["line_number"], 8)
        with closing(sqlite3.connect(self.db)) as connection:
            raw_lines = connection.execute("SELECT raw_line FROM import_errors ORDER BY line_number").fetchall()
        self.assertEqual([row[0] for row in raw_lines], bad_lines)

    def test_field_validation_timestamp_alias_and_unknown_fields(self):
        record = review()
        record["sort_timestamp"] = record.pop("timestamp")
        self.assertEqual(normalize_review(record)["published_at"], "2023-09-01T00:00:00.123+00:00")
        self.assertEqual(normalize_review(review(rating=None))["rating"], None)
        self.assertEqual(normalize_product({"parent_asin": "P1"})["title"], "")
        for changes in ({"parent_asin": ""}, {"parent_asin": []}, {"rating": True},
                        {"rating": "1"}, {"rating": float("inf")}, {"rating": 10 ** 1000},
                        {"rating": 0}, {"rating": 6}, {"timestamp": 1693526400},
                        {"timestamp": True}, {"timestamp": 10 ** 1000},
                        {"timestamp": "1693526400123"}, {"sort_timestamp": 1},
                        {"text": None}, {"text": []}, {"user_id": 7}):
            with self.subTest(changes=changes), self.assertRaises(ReviewDataError):
                normalize_review(review(**changes))
        for changes in ({"details": []}, {"details": ""}, {"details": {"Brand": []}}):
            with self.subTest(changes=changes), self.assertRaises(ReviewDataError):
                normalize_product(product(**changes))

    def test_product_filter_and_empty_inputs_are_explicit(self):
        init_database(self.db)
        reviews = self.write_jsonl("reviews.jsonl", [review(parent_asin=" P1 "),
            review(parent_asin="P2", rating="not-validated"), review(parent_asin=[])])
        result = import_amazon_jsonl(self.db, reviews_path=reviews, product_ids=["P1"])
        self.assertEqual(result["imports"][0]["filtered_records"], 1)
        self.assertEqual(result["imports"][0]["invalid_records"], 1)
        self.assertEqual(result["quality"]["review_count"], 1)
        empty = self.write_jsonl("empty.jsonl", [])
        outcome = import_amazon_jsonl(self.db, reviews_path=empty)
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(outcome["imports"][0]["warnings"])

    def test_review_limit_counts_only_new_valid_selected_reviews_and_can_continue(self):
        for name in ("limited.jsonl", "limited.jsonl.gz"):
            with self.subTest(name=name):
                self.db = self.root / (name + ".sqlite3")
                init_database(self.db)
                self.import_reviews([review()])
                path = self.write_jsonl(name, [review(), review(user_id="BAD", rating=99),
                    review(parent_asin="P2", user_id="FILTERED"), review(user_id="U2"),
                    review(user_id="U3"), review(user_id="U4")])
                result = import_amazon_jsonl(self.db, reviews_path=path, product_ids=["P1"], max_reviews=2)
                summary = result["imports"][0]
                self.assertEqual(result["status"], "partial")
                self.assertEqual((summary["inserted_records"], summary["duplicate_records"],
                                  summary["invalid_records"], summary["filtered_records"]), (2, 1, 1, 1))
                self.assertEqual(summary["total_lines"], 5)
                self.assertTrue(summary["limit_reached"])
                self.assertFalse(summary["scan_complete"])
                self.assertEqual(get_review_evidence(self.db, 3)["line_number"], 5)
                continued = import_amazon_jsonl(self.db, reviews_path=path, product_ids=["P1"], max_reviews=2)
                self.assertEqual(continued["imports"][0]["inserted_records"], 1)
                self.assertTrue(continued["imports"][0]["scan_complete"])
                self.assertEqual(continued["quality"]["review_count"], 4)

    def test_review_limit_preserves_anonymous_identity_and_does_not_limit_metadata(self):
        init_database(self.db)
        path = self.write_jsonl("anonymous.jsonl.gz", [review(user_id="")] * 3)
        meta = self.write_jsonl("meta.jsonl", [product(), product(parent_asin="P2")])
        for expected in (1, 2, 3):
            result = import_amazon_jsonl(self.db, reviews_path=path, meta_path=meta, max_reviews=1)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["imports"][0]["valid_records"], 2)
            self.assertTrue(result["imports"][0]["scan_complete"])
            self.assertEqual(result["quality"]["review_count"], expected)
            evidence = get_review_evidence(self.db, expected)
            self.assertEqual(evidence["line_number"], expected)
            self.assertEqual(evidence["file_sha256"], file_sha256(path))

    def test_review_limit_validates_before_writing_and_still_checks_input_fingerprint(self):
        init_database(self.db)
        path = self.write_jsonl("limited.jsonl", [review(), review(user_id="U2")])
        for invalid in (0, -1, True, 1.5, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ReviewDataError):
                import_amazon_jsonl(self.db, reviews_path=path, max_reviews=invalid)
        with self.assertRaises(ReviewDataError):
            import_amazon_jsonl(self.db, meta_path=path, max_reviews=1)
        with patch("data_analysis_agent.reviews.store.file_sha256", side_effect=[file_sha256(path), "changed"]):
            with self.assertRaisesRegex(ReviewDataError, "回滚"):
                import_amazon_jsonl(self.db, reviews_path=path, max_reviews=1)
        self.assertEqual(get_quality_summary(self.db)["import_count"], 0)
        self.assertEqual(get_quality_summary(self.db)["review_count"], 0)

    def test_corrupt_gzip_rolls_back_both_files_and_import_history(self):
        init_database(self.db)
        metadata = self.write_jsonl("meta.jsonl", [product()])
        reviews = self.write_jsonl("reviews.jsonl.gz", [review()])
        reviews.write_bytes(reviews.read_bytes()[:-5])
        with self.assertRaises((EOFError, OSError)):
            import_amazon_jsonl(self.db, reviews_path=reviews, meta_path=metadata)
        quality = get_quality_summary(self.db)
        self.assertEqual((quality["review_count"], quality["product_count"], quality["import_count"]), (0, 0, 0))

    def test_changed_input_rolls_back_all_writes_and_existing_rows_survive(self):
        init_database(self.db)
        self.import_reviews([review(parent_asin="EXISTING")])
        metadata = self.write_jsonl("meta.jsonl", [product()])
        reviews = self.write_jsonl("new.jsonl", [review()])
        hashes = [file_sha256(metadata), file_sha256(metadata), file_sha256(reviews), "changed"]
        with patch("data_analysis_agent.reviews.store.file_sha256", side_effect=hashes):
            with self.assertRaisesRegex(ReviewDataError, "回滚"):
                import_amazon_jsonl(self.db, reviews_path=reviews, meta_path=metadata)
        quality = get_quality_summary(self.db)
        self.assertEqual((quality["review_count"], quality["product_count"], quality["import_count"]), (1, 1, 1))
        self.assertEqual(get_review_evidence(self.db, 1)["product_id"], "EXISTING")

    def test_streaming_plain_and_gzip_do_not_use_whole_file_reads(self):
        init_database(self.db)
        path = self.write_jsonl("many.jsonl.gz", [review(user_id=f"U{i}") for i in range(1000)])
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")), \
                patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")):
            result = import_amazon_jsonl(self.db, reviews_path=path)
        self.assertEqual(result["quality"]["review_count"], 1000)
        self.assertLessEqual(len(result["quality"]["product_sample"]), 20)
        lazy = self.root / "lazy.jsonl"
        lazy.write_bytes(json.dumps(review()).encode() + b"\n\xff\n")
        iterator = iter_jsonl(lazy)
        self.assertIsNotNone(next(iterator).data)
        self.assertIsNotNone(next(iterator).error)
        iterator.close()

    def test_error_samples_are_bounded_and_history_keeps_counts(self):
        init_database(self.db)
        path = self.root / "errors.jsonl"
        path.write_text("invalid\n" * 15, encoding="utf-8")
        result = import_amazon_jsonl(self.db, reviews_path=path)
        self.assertEqual(len(result["imports"][0]["error_samples"]), 10)
        self.assertEqual(result["quality"]["rejected_record_count"], 15)
        with closing(sqlite3.connect(self.db)) as connection:
            history = json.loads(connection.execute("SELECT summary_json FROM import_runs").fetchone()[0])
        self.assertEqual(history, result["imports"][0])

    def test_invalid_requests_and_database_path_collisions(self):
        init_database(self.db)
        with self.assertRaisesRegex(ReviewDataError, "输入路径"):
            import_amazon_jsonl(self.db, reviews_path=self.db)
        with self.assertRaises(ReviewDataError):
            import_amazon_jsonl(self.db)
        for value in ("", " ", None):
            with self.assertRaises(ReviewDataError):
                import_amazon_jsonl(self.db, dataset_version=value)
        for value in ("P1", [""], [1]):
            with self.assertRaises(ReviewDataError):
                import_amazon_jsonl(self.db, product_ids=value)
        for value in (0, -1, True, "1", 1):
            with self.assertRaises(ReviewDataError):
                get_review_evidence(self.db, value)
        with self.assertRaises(FileNotFoundError):
            import_amazon_jsonl(self.db, reviews_path=self.root / "missing.jsonl")
        unsupported = self.root / "reviews.txt"
        unsupported.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ReviewDataError, "jsonl"):
            import_amazon_jsonl(self.db, reviews_path=unsupported)
        self.assertEqual(get_quality_summary(self.db)["import_count"], 0)

    def test_database_uri_handles_unicode_spaces_and_hash(self):
        parent = self.root / "评论 数据 #"
        parent.mkdir()
        self.db = parent / "评论 #.sqlite3"
        init_database(self.db)
        self.import_reviews([review()])
        self.assertEqual(get_quality_summary(self.db)["review_count"], 1)
        self.assertEqual(get_review_evidence(self.db, 1)["product_id"], "P1")

    def run_cli(self, *arguments):
        result = subprocess.run([sys.executable, "-m", "data_analysis_agent.cli.reviews", *arguments],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        return result

    def test_cli_init_import_summary_evidence_and_exit_codes(self):
        missing = self.run_cli("summary", "--db", str(self.db))
        self.assertEqual(missing.returncode, 1)
        self.assertFalse(self.db.exists())
        initialized = self.run_cli("init", "--db", str(self.db))
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertTrue(json.loads(initialized.stdout)["created"])
        self.assertEqual(self.run_cli("import", "--db", str(self.db)).returncode, 2)
        path = self.write_jsonl("reviews.jsonl", [review(), review(rating=99)])
        imported = self.run_cli("import", "--db", str(self.db), "--reviews", str(path))
        self.assertEqual(imported.returncode, 3, imported.stderr)
        self.assertEqual(json.loads(imported.stdout)["quality"]["review_count"], 1)
        summary = self.run_cli("summary", "--db", str(self.db))
        self.assertEqual(summary.returncode, 0, summary.stderr)
        evidence = self.run_cli("evidence", "--db", str(self.db), "--review-id", "1")
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        self.assertEqual(json.loads(evidence.stdout)["evidence"]["text"], review()["text"])
        self.assertEqual(self.run_cli("evidence", "--db", str(self.db), "--review-id", "9").returncode, 1)
        self.assertEqual(self.run_cli("--help").returncode, 0)

    def test_cli_corrupt_deflate_returns_error_and_rolls_back(self):
        init_database(self.db)
        metadata = self.write_jsonl("meta.jsonl", [product()])
        corrupt = self.root / "corrupt.jsonl.gz"
        corrupt.write_bytes(bytes.fromhex("1f8b0800000000000003") + b"\x07" + b"\x00" * 8)
        outcome = self.run_cli("import", "--db", str(self.db), "--meta", str(metadata),
                               "--reviews", str(corrupt))
        self.assertEqual(outcome.returncode, 1, outcome.stderr)
        self.assertEqual(json.loads(outcome.stdout)["status"], "error")
        self.assertNotIn("Traceback", outcome.stderr)
        self.assertEqual(get_quality_summary(self.db)["product_count"], 0)

    def test_cli_review_limit(self):
        init_database(self.db)
        path = self.write_jsonl("reviews.jsonl", [review(user_id=f"U{i}") for i in range(3)])
        for limit, expected in (("1", 1), ("2", 3)):
            result = self.run_cli("import", "--db", str(self.db), "--reviews", str(path), "--max-reviews", limit)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["quality"]["review_count"], expected)
        self.assertEqual(self.run_cli("import", "--db", str(self.db), "--reviews", str(path),
                                     "--max-reviews", "0").returncode, 1)

    def test_task_worker_preserves_review_limit_and_schema_rejects_invalid_values(self):
        from pydantic import ValidationError
        from data_analysis_agent.api.schemas import ImportParameters
        from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, get_task
        from data_analysis_agent.jobs.worker import TaskWorker, prepare_parameters

        init_database(self.db)
        path = self.write_jsonl("reviews.jsonl", [review(user_id=f"U{i}") for i in range(3)])
        for invalid in (0, -1, True, 1.5, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                ImportParameters(reviews_path=path.name, max_reviews=invalid)
        with self.assertRaises(ValidationError):
            ImportParameters(meta_path=path.name, max_reviews=1)
        task_db = self.root / "tasks.sqlite3"
        init_tasks(task_db, self.db)
        values = prepare_parameters("import", {"reviews_path": path.name, "max_reviews": 2},
                                    import_root=self.root, env_file=self.root / "unused.env")
        task = enqueue(task_db, "import", values)["task"]
        worker = TaskWorker(self.db, task_db, import_root=self.root, env_file=self.root / "unused.env")
        worker_started(task_db, worker.worker_id)
        claimed = claim_task(task_db, worker.worker_id)
        worker._execute(claimed)
        completed = get_task(task_db, task["task_id"])
        self.assertEqual(completed["status"], "succeeded", completed["error"])
        self.assertEqual(completed["parameters"]["max_reviews"], 2)
        self.assertEqual(completed["result"]["quality"]["review_count"], 2)
        self.assertTrue(completed["result"]["imports"][0]["limit_reached"])


if __name__ == "__main__":
    unittest.main()
