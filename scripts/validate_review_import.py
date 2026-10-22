"""Verify real-data import/dedup/evidence in a disposable database only."""

import argparse
import gzip
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from data_analysis_agent.reviews.loader import file_sha256
from data_analysis_agent.reviews.store import get_review_evidence


def run_cli(*arguments):
    result = subprocess.run(
        [sys.executable, "-m", "data_analysis_agent.cli.reviews", *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    if result.returncode not in (0, 3):
        raise RuntimeError(result.stdout or result.stderr)
    return json.loads(result.stdout)


def check_original_lines(connection, source_path, kind):
    """Compare every stored raw JSON line directly against the original file."""
    table = "reviews" if kind == "reviews" else "products"
    cursor = connection.execute(f"SELECT line_number, raw_json FROM {table} ORDER BY line_number")
    expected = next(cursor, None)
    checked = 0
    opener = gzip.open if source_path.name.lower().endswith(".gz") else open
    with opener(source_path, "rb") as handle:
        for number, raw in enumerate(handle, start=1):
            if expected is None:
                break
            if number == expected[0]:
                text = raw.decode("utf-8-sig" if number == 1 else "utf-8").rstrip("\r\n")
                assert text == expected[1], (kind, number, "original line mismatch")
                checked += 1
                expected = next(cursor, None)
    assert expected is None, (kind, "source ended before a referenced line")
    return checked


def validate_import(reviews_path, meta_path, product_ids):
    reviews_path, meta_path = Path(reviews_path).resolve(), Path(meta_path).resolve()
    fingerprints = {"reviews": file_sha256(reviews_path), "meta": file_sha256(meta_path)}
    product_ids = sorted(set(product_ids))
    assert product_ids, "Choose at least one product."
    with tempfile.TemporaryDirectory(prefix="review-real-data-validation-") as directory:
        database = Path(directory) / "temporary.sqlite3"
        run_cli("init", "--db", database)
        arguments = ["import", "--db", database, "--reviews", reviews_path, "--meta", meta_path]
        for product_id in product_ids:
            arguments.extend(("--product-id", product_id))
        print("Importing selected products into a temporary database...", flush=True)
        first = run_cli(*arguments)
        print("Repeating the same import to check deduplication...", flush=True)
        second = run_cli(*arguments)
        assert first["quality"]["review_count"] > 0
        assert first["quality"]["product_count"] == len(product_ids)
        assert first["quality"]["reviews_without_metadata"] == 0
        assert first["quality"]["products_without_metadata"] == 0
        assert all(item["inserted_records"] == 0 for item in second["imports"])
        assert first["quality"]["review_count"] == second["quality"]["review_count"]
        assert first["quality"]["product_count"] == second["quality"]["product_count"]
        for item in first["imports"] + second["imports"]:
            assert item["total_lines"] == sum(item[key] for key in (
                "blank_lines", "filtered_records", "inserted_records", "duplicate_records", "invalid_records"))
            assert item["file_sha256"] == fingerprints[item["kind"]]
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            products = [dict(row) for row in connection.execute(
                """SELECT p.product_id, p.title, p.brand, p.category,
                COUNT(r.id) AS review_count, SUM(r.has_text) AS nonempty_text_count,
                SUM(r.rating<=2) AS low_rating_count,
                SUM(r.published_at>='2023-01-01') AS reviews_2023,
                MIN(r.published_at) AS published_at_start, MAX(r.published_at) AS published_at_end
                FROM products p JOIN reviews r ON
                (p.source=r.source AND p.dataset_version=r.dataset_version AND p.product_id=r.product_id)
                GROUP BY p.source, p.dataset_version, p.product_id ORDER BY p.product_id"""
            )]
            assert {item["product_id"] for item in products} == set(product_ids)
            matched_reviews = check_original_lines(connection, reviews_path, "reviews")
            matched_metadata = check_original_lines(connection, meta_path, "meta")
            assert matched_reviews == first["quality"]["review_count"]
            assert matched_metadata == len(product_ids)
            evidence_samples = []
            for product_id in product_ids:
                review_id = connection.execute(
                    """SELECT id FROM reviews WHERE product_id=?
                    ORDER BY CASE WHEN rating<=2 THEN 0 ELSE 1 END, published_at DESC, id LIMIT 1""",
                    (product_id,),
                ).fetchone()[0]
                evidence = get_review_evidence(database, review_id)
                assert evidence["file_path"] == str(reviews_path)
                assert evidence["file_sha256"] == fingerprints["reviews"]
                assert json.loads(evidence["raw_json"]).get("text", "") == evidence["text"]
                assert evidence["evidence_ref"] == (
                    f"sha256:{fingerprints['reviews']}#line={evidence['line_number']}"
                )
                evidence_samples.append(evidence)
        assert file_sha256(reviews_path) == fingerprints["reviews"]
        assert file_sha256(meta_path) == fingerprints["meta"]
        for result in (first, second):
            result["database"] = "disposable test database; removed after verification"
        report = {
            "verified_at": datetime.now(timezone.utc).isoformat(), "status": "passed",
            "product_ids": product_ids, "inputs": {
                "reviews": {"path": str(reviews_path), "sha256": fingerprints["reviews"]},
                "meta": {"path": str(meta_path), "sha256": fingerprints["meta"]},
            },
            "first_import": first, "repeat_import": second, "products": products,
            "checks": {"original_review_lines_matched": matched_reviews,
                       "original_metadata_lines_matched": matched_metadata,
                       "repeat_inserted_records": 0, "foreign_key_errors": 0,
                       "input_files_unchanged": True, "business_database_modified": False},
            "evidence_samples": evidence_samples,
        }
    assert not database.exists(), "Temporary database cleanup failed."
    report["checks"]["temporary_database_removed"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description="仅使用临时数据库验证真实评论导入，不配置业务数据库")
    parser.add_argument("--reviews", required=True, type=Path)
    parser.add_argument("--meta", required=True, type=Path)
    parser.add_argument("--product-id", required=True, action="append")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve() in {args.reviews.resolve(), args.meta.resolve()}:
        parser.error("验证报告不能覆盖输入文件。")
    report = validate_import(args.reviews, args.meta, args.product_id)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": report["checks"],
                      "products": report["products"], "report": str(args.report.resolve())},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
