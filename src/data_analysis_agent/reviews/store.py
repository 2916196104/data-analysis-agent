"""SQLite/PostgreSQL storage, explicit setup, atomic imports and row-level evidence."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import (PostgresConnection, check_postgres_schema, connect_database, database_identity,
                       database_target, init_postgres, insert_id, is_postgres)

from data_analysis_agent.reviews.loader import (
    DATASET_VERSION, SOURCE, ReviewDataError, content_sha256, file_sha256,
    iter_jsonl, normalize_product, normalize_review,
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x52455631
SCHEMA = """
CREATE TABLE import_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('reviews', 'meta')),
    file_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    summary_json TEXT
);
CREATE TABLE products (
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    product_id TEXT NOT NULL,
    title TEXT,
    brand TEXT,
    category TEXT,
    raw_json TEXT,
    content_sha256 TEXT,
    import_run_id INTEGER REFERENCES import_runs(id),
    line_number INTEGER,
    PRIMARY KEY (source, dataset_version, product_id)
);
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    asin TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    has_text INTEGER NOT NULL CHECK(has_text IN (0, 1)),
    rating REAL CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    line_number INTEGER NOT NULL,
    UNIQUE (source, dataset_version, source_record_id),
    FOREIGN KEY (source, dataset_version, product_id)
        REFERENCES products(source, dataset_version, product_id)
);
CREATE INDEX reviews_product_date ON reviews(source, dataset_version, product_id, published_at);
CREATE TABLE import_errors (
    id INTEGER PRIMARY KEY,
    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
    line_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    raw_line BLOB NOT NULL
);
"""


def check_review_schema(connection: sqlite3.Connection | PostgresConnection) -> None:
    """Check review schema identity/version without modifying the caller's connection."""
    if isinstance(connection, PostgresConnection):
        check_postgres_schema(connection, "business")
        return
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
    if version != SCHEMA_VERSION or app_id != APPLICATION_ID:
        raise ReviewDataError("数据库不是受支持的评论库；请使用 init 初始化专用数据库。")


def connect_review_database(db_path: str | Path, *, readonly: bool = False) -> sqlite3.Connection | PostgresConnection:
    """Open a validated review database; the caller owns transactions and close()."""
    if not is_postgres(db_path) and not Path(db_path).is_file():
        raise ReviewDataError("评论数据库不存在，请先执行 init --db 指定路径。")
    connection = connect_database(db_path, readonly=readonly)
    try:
        check_review_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


def init_database(db_path: str | Path) -> dict:
    """Initialize a new, explicitly chosen database; refuse unrelated databases."""
    if is_postgres(db_path):
        return {"database": database_identity(db_path), "schema_version": SCHEMA_VERSION,
                "created": init_postgres(db_path, SCHEMA)}
    path = Path(db_path).resolve()
    if not path.parent.is_dir():
        raise ReviewDataError("数据库父目录不存在，请先创建目录。")
    if path.exists():
        with closing(connect_review_database(path, readonly=True)):
            return {"database": str(path), "schema_version": SCHEMA_VERSION, "created": False}
    # Exclusive file creation prevents init from replacing an existing file.
    with path.open("xb"):
        pass
    with closing(connect_database(path)) as connection:
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + SCHEMA
                + f"PRAGMA application_id = {APPLICATION_ID};\n"
                + f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
            )
        except Exception:
            connection.rollback()
            raise
    return {"database": str(path), "schema_version": SCHEMA_VERSION, "created": True}


def _store_product(connection, record, raw_json, raw_data, run_id, line_number, version):
    key = (SOURCE, version, record["product_id"])
    existing = connection.execute(
        "SELECT content_sha256 FROM products WHERE source=? AND dataset_version=? AND product_id=?", key
    ).fetchone()
    digest = content_sha256(raw_data)
    if existing is not None and existing[0] is not None:
        if existing[0] == digest:
            return False
        raise ReviewDataError("同版本商品元数据冲突；保留首次记录，请核查或使用新数据集版本。")
    connection.execute(
        """INSERT INTO products
        (source, dataset_version, product_id, title, brand, category, raw_json,
         content_sha256, import_run_id, line_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, dataset_version, product_id) DO UPDATE SET
        title=excluded.title, brand=excluded.brand, category=excluded.category,
        raw_json=excluded.raw_json, content_sha256=excluded.content_sha256,
        import_run_id=excluded.import_run_id, line_number=excluded.line_number""",
        (*key, record["title"], record["brand"], record["category"], raw_json, digest, run_id, line_number),
    )
    return True


def _store_review(connection, record, raw_json, run_id, line_number, version, fingerprint, fetched_at,
                  known_products):
    # Anonymous records have no safe cross-file identity; keep distinct locations.
    digest = content_sha256(record)
    record_id = digest if record["user_id"] else content_sha256({"file_sha256": fingerprint, "line": line_number})
    if connection.execute(
        "SELECT 1 FROM reviews WHERE source=? AND dataset_version=? AND source_record_id=?",
        (SOURCE, version, record_id),
    ).fetchone():
        return False
    if record["product_id"] not in known_products:
        connection.execute(
            "INSERT INTO products(source, dataset_version, product_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (SOURCE, version, record["product_id"]),
        )
        # Bound memory even when importing a category with millions of products.
        if len(known_products) < 4096:
            known_products.add(record["product_id"])
    connection.execute(
        """INSERT INTO reviews
        (source, dataset_version, source_record_id, product_id, asin, user_id, title,
         text, has_text, rating, published_at, fetched_at, raw_json, content_sha256, import_run_id, line_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (SOURCE, version, record_id, record["product_id"], record["asin"], record["user_id"],
         record["title"], record["text"], int(bool(record["text"].strip())),
         record["rating"], record["published_at"], fetched_at,
         raw_json, digest, run_id, line_number),
    )
    return True


def _import_file(connection, path, kind, version, product_ids, progress=None, max_reviews=None):
    if progress:
        progress({"stage": "hashing_" + kind, "completed": 0, "total": None, "unit": "lines"})
    fingerprint = file_sha256(path)
    started = utc_now()
    run_id = insert_id(connection,
        """INSERT INTO import_runs(source, dataset_version, kind, file_path, file_sha256, started_at)
        VALUES (?, ?, ?, ?, ?, ?)""", (SOURCE, version, kind, str(path), fingerprint, started),
    )
    counts = {name: 0 for name in
              ("total_lines", "blank_lines", "filtered_records", "inserted_records", "duplicate_records",
               "invalid_records", "empty_text_records", "missing_rating_records", "missing_user_id_records")}
    known_products = set()
    limit_reached = False
    with closing(iter_jsonl(path)) as lines:
        for line in lines:
            counts["total_lines"] += 1
            if progress and counts["total_lines"] % 1000 == 0:
                progress({"stage": "importing_" + kind, "completed": counts["total_lines"] - 1,
                          "total": None, "unit": "lines", "inserted_records": counts["inserted_records"],
                          "invalid_records": counts["invalid_records"]})
            reason = line.error
            if line.data is None and reason is None:
                counts["blank_lines"] += 1
                continue
            if reason is None:
                try:
                    # Filter by product before validating other fields, but report malformed JSON.
                    candidate = line.data.get("parent_asin")
                    if (product_ids and isinstance(candidate, str) and candidate.strip()
                            and candidate.strip() not in product_ids):
                        counts["filtered_records"] += 1
                        continue
                    if kind == "reviews":
                        record = normalize_review(line.data)
                        inserted = _store_review(connection, record, line.text, run_id, line.number,
                                                 version, fingerprint, started, known_products)
                        counts["empty_text_records"] += not record["text"].strip()
                        counts["missing_rating_records"] += record["rating"] is None
                        counts["missing_user_id_records"] += not record["user_id"]
                    else:
                        record = normalize_product(line.data)
                        inserted = _store_product(connection, record, line.text, line.data,
                                                  run_id, line.number, version)
                    counts["inserted_records" if inserted else "duplicate_records"] += 1
                    if kind == "reviews" and max_reviews is not None and counts["inserted_records"] >= max_reviews:
                        limit_reached = True
                        break
                except ReviewDataError as exc:
                    reason = str(exc)
            if reason is not None:
                counts["invalid_records"] += 1
                connection.execute(
                    "INSERT INTO import_errors(import_run_id, line_number, reason, raw_line) VALUES (?, ?, ?, ?)",
                    (run_id, line.number, reason, line.raw),
                )
    if progress:
        progress({"stage": "verifying_" + kind, "completed": counts["total_lines"], "total": None, "unit": "lines"})
    if file_sha256(path) != fingerprint:
        raise ReviewDataError("读取期间输入文件发生变化，整次导入已回滚。")
    valid = counts["inserted_records"] + counts["duplicate_records"]
    summary = {
        "import_run_id": run_id, "source": SOURCE, "dataset_version": version,
        "kind": kind, "file_path": str(path), "file_sha256": fingerprint,
        "product_ids": sorted(product_ids),
        "max_reviews": max_reviews if kind == "reviews" else None,
        "limit_reached": limit_reached, "scan_complete": not limit_reached,
        **counts, "valid_records": valid,
        "status": "partial" if counts["invalid_records"] or not valid else "ok",
        "warnings": ["未找到符合条件的有效记录。"] if not valid else [],
        "error_samples": [dict(row) for row in connection.execute(
            "SELECT line_number, reason FROM import_errors WHERE import_run_id=? ORDER BY id LIMIT 10", (run_id,)
        )],
    }
    connection.execute(
        "UPDATE import_runs SET completed_at=?, summary_json=? WHERE id=?",
        (utc_now(), json.dumps(summary, ensure_ascii=False), run_id),
    )
    return summary


def _quality_summary(connection):
    reviews = dict(connection.execute(
        """SELECT COUNT(*) AS review_count,
        COALESCE(SUM(CASE WHEN has_text=0 THEN 1 ELSE 0 END), 0) AS empty_text_count,
        COALESCE(SUM(CASE WHEN rating IS NULL THEN 1 ELSE 0 END), 0) AS missing_rating_count,
        COALESCE(SUM(CASE WHEN user_id='' THEN 1 ELSE 0 END), 0) AS missing_user_id_count,
        MIN(published_at) AS published_at_start, MAX(published_at) AS published_at_end FROM reviews"""
    ).fetchone())
    products = dict(connection.execute(
        """SELECT COUNT(*) AS product_count,
        COALESCE(SUM(CASE WHEN raw_json IS NULL THEN 1 ELSE 0 END), 0) AS products_without_metadata,
        COALESCE(SUM(CASE WHEN raw_json IS NOT NULL AND length(trim(title))=0 THEN 1 ELSE 0 END), 0) AS metadata_without_title
        FROM products"""
    ).fetchone())
    missing_metadata = connection.execute(
        """SELECT COUNT(*) FROM reviews r JOIN products p
        ON (r.source=p.source AND r.dataset_version=p.dataset_version AND r.product_id=p.product_id)
        WHERE p.raw_json IS NULL"""
    ).fetchone()[0]
    return {
        **reviews, **products, "reviews_without_metadata": missing_metadata,
        "import_count": connection.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0],
        "rejected_record_count": connection.execute("SELECT COUNT(*) FROM import_errors").fetchone()[0],
        "product_sample": [dict(row) for row in connection.execute(
            """SELECT p.source, p.dataset_version, p.product_id, p.title,
            CASE WHEN p.raw_json IS NOT NULL THEN 1 ELSE 0 END AS has_metadata, COUNT(r.id) AS review_count,
            MIN(r.published_at) AS published_at_start, MAX(r.published_at) AS published_at_end
            FROM products p LEFT JOIN reviews r ON
            (r.source=p.source AND r.dataset_version=p.dataset_version AND r.product_id=p.product_id)
            GROUP BY p.source, p.dataset_version, p.product_id ORDER BY review_count DESC,
            p.source, p.dataset_version, p.product_id LIMIT 20"""
        )],
    }


def import_amazon_jsonl(db_path: str | Path, *, reviews_path: str | Path | None = None,
                        meta_path: str | Path | None = None, dataset_version: str = DATASET_VERSION,
                        product_ids: list[str] | None = None, progress=None, max_reviews: int | None = None) -> dict:
    """Import both inputs in one transaction; row errors are retained, fatal errors roll back."""
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise ReviewDataError("dataset_version 不能为空。")
    if max_reviews is not None:
        if isinstance(max_reviews, bool) or not isinstance(max_reviews, int) or max_reviews < 1:
            raise ReviewDataError("max_reviews 必须是正整数，省略时不限数量。")
        if reviews_path is None:
            raise ReviewDataError("max_reviews 需要同时指定评论文件。")
    if product_ids is not None and (not isinstance(product_ids, list)
                                  or any(not isinstance(item, str) or not item.strip() for item in product_ids)):
        raise ReviewDataError("product_ids 必须是非空商品 ID 的列表。")
    selected = {item.strip() for item in product_ids or []}
    files = [(kind, Path(path).resolve()) for kind, path in (("meta", meta_path), ("reviews", reviews_path))
             if path is not None]
    if not files:
        raise ReviewDataError("至少指定一个评论文件或商品元数据文件。")
    database = database_target(db_path)
    for _, path in files:
        if not is_postgres(database) and path in {database, Path(str(database) + "-journal"), Path(str(database) + "-wal"),
                    Path(str(database) + "-shm")}:
            raise ReviewDataError("输入路径不能与数据库及其辅助文件相同。")
        if not path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{path}")
    with closing(connect_review_database(database)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            imports = [_import_file(connection, path, kind, dataset_version.strip(), selected, progress, max_reviews)
                       for kind, path in files]
            quality = _quality_summary(connection)
    if progress:
        progress({"stage": "imported", "completed": len(files), "total": len(files), "unit": "files"})
    return {"status": "partial" if any(item["status"] == "partial" for item in imports) else "ok",
            "database": database_identity(database), "imports": imports, "quality": quality}


def get_quality_summary(db_path: str | Path) -> dict:
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        with connection:
            connection.execute("BEGIN")
            return _quality_summary(connection)


def get_review_evidence(db_path: str | Path, review_id: int) -> dict:
    """Return stored original text even if the input file has since moved."""
    if isinstance(review_id, bool) or not isinstance(review_id, int) or review_id < 1:
        raise ReviewDataError("review_id 必须是正整数。")
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        row = connection.execute(
            """SELECT r.*, i.file_path, i.file_sha256 FROM reviews r
            JOIN import_runs i ON r.import_run_id=i.id WHERE r.id=?""", (review_id,)
        ).fetchone()
        if row is None:
            raise ReviewDataError("评论 ID 不存在。")
        evidence = dict(row)
        evidence["evidence_ref"] = f"sha256:{row['file_sha256']}#line={row['line_number']}"
        return evidence
