"""SQLite/PostgreSQL 存储、显式初始化、原子导入和行级证据。"""

import json
import sqlite3
from time import monotonic
from contextlib import closing
from pathlib import Path

from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.core.database import (PostgresConnection, check_postgres_schema, connect_database, database_identity,
                       database_target, init_postgres, insert_id, is_postgres, table_names)

from data_analysis_agent.reviews.loader import (
    DATASET_VERSION, SOURCE, file_sha256,
    iter_jsonl, normalize_product, normalize_review,
    import_suffix,
)
from data_analysis_agent.reviews.csv_loader import SOURCE as CSV_SOURCE, csv_value, iter_review_csv, normalize_csv_review

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
    """在不修改调用方连接的情况下检查评论数据表身份和版本。"""
    if isinstance(connection, PostgresConnection):
        check_postgres_schema(connection, "business")
        return
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
    if version != SCHEMA_VERSION or app_id != APPLICATION_ID:
        raise ReviewDataError("数据库不是受支持的评论库；请使用 init 初始化专用数据库。")


def connect_review_database(db_path: str | Path, *, readonly: bool = False) -> sqlite3.Connection | PostgresConnection:
    """打开已校验的评论数据库；事务和 close() 由调用方负责。"""
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
    """初始化用户明确选择的新数据库；拒绝无关数据库。"""
    if is_postgres(db_path):
        return {"database": database_identity(db_path), "schema_version": SCHEMA_VERSION,
                "created": init_postgres(db_path, SCHEMA)}
    path = Path(db_path).resolve()
    if not path.parent.is_dir():
        raise ReviewDataError("数据库父目录不存在，请先创建目录。")
    if path.exists():
        with closing(connect_review_database(path, readonly=True)):
            return {"database": str(path), "schema_version": SCHEMA_VERSION, "created": False}
    # 独占创建文件可避免 init 替换已有文件。
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
                  known_products, source=SOURCE):
    # 匿名记录没有安全的跨文件身份；保留不同的位置。
    digest = content_sha256({key: value for key, value in record.items()
                             if key not in ("source_record_id", "product_title", "brand")})
    record_id = record.get("source_record_id") or (digest if record["user_id"] else content_sha256({"file_sha256": fingerprint, "line": line_number}))
    existing = connection.execute(
        "SELECT content_sha256 FROM reviews WHERE source=? AND dataset_version=? AND source_record_id=?",
        (source, version, record_id),
    ).fetchone()
    if existing:
        if record.get("source_record_id") and existing[0] != digest:
            raise ReviewDataError("同版本评论ID 的内容冲突；保留首次记录，请核查或使用新数据集版本。")
        return False
    if record["product_id"] not in known_products:
        connection.execute(
            "INSERT INTO products(source, dataset_version, product_id, title, brand) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (source, version, record["product_id"], record.get("product_title"), record.get("brand")),
        )
        # 即使导入包含数百万商品的类别，也要限制内存使用。
        if len(known_products) < 4096:
            known_products.add(record["product_id"])
    connection.execute(
        """INSERT INTO reviews
        (source, dataset_version, source_record_id, product_id, asin, user_id, title,
         text, has_text, rating, published_at, fetched_at, raw_json, content_sha256, import_run_id, line_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (source, version, record_id, record["product_id"], record["asin"], record["user_id"],
         record["title"], record["text"], int(bool(record["text"].strip())),
         record["rating"], record["published_at"], fetched_at,
         raw_json, digest, run_id, line_number),
    )
    return True


def _import_file(connection, path, kind, version, product_ids, progress=None, max_reviews=None):
    is_csv = import_suffix(path.name, kind) == ".csv"
    source = CSV_SOURCE if is_csv else SOURCE
    counts = {name: 0 for name in
              ("total_lines", "blank_lines", "filtered_records", "inserted_records", "duplicate_records",
               "invalid_records", "empty_text_records", "missing_rating_records", "missing_user_id_records")}
    file_size = path.stat().st_size
    last_report = 0.0

    def report(stage, completed, *, force=False, **extra):
        nonlocal last_report
        if not progress:
            return
        now = monotonic()
        if force or now - last_report >= 0.25:
            progress({"stage": stage, "completed": completed, "total": file_size, "unit": "bytes",
                      "filename": path.name, **counts, **extra})
            last_report = now

    def hash_file(stage):
        completed = 0

        def on_read(value):
            nonlocal completed
            completed = value
            report(stage, value)

        report(stage, 0, force=True)
        digest = file_sha256(path, progress=on_read)
        report(stage, completed, force=True)
        return digest

    fingerprint = hash_file("hashing_" + kind)
    started = utc_now()
    run_id = insert_id(connection,
        """INSERT INTO import_runs(source, dataset_version, kind, file_path, file_sha256, started_at)
        VALUES (?, ?, ?, ?, ?, ?)""", (source, version, kind, str(path), fingerprint, started),
    )
    known_products = set()
    limit_reached = False
    bytes_read = 0
    report("importing_" + kind, 0, force=True)
    with closing(iter_review_csv(path) if is_csv else iter_jsonl(path)) as lines:
        for line in lines:
            report("importing_" + kind, bytes_read)
            bytes_read = line.bytes_read
            counts["total_lines"] += 1
            reason = line.error
            if line.data is None and reason is None:
                counts["blank_lines"] += 1
                continue
            if reason is None:
                try:
                    # 先按商品筛选再校验其他字段，但仍报告格式错误的 JSON。
                    candidate = csv_value(line.data, "product_id") if is_csv else line.data.get("parent_asin")
                    if (product_ids and isinstance(candidate, str) and candidate.strip()
                            and candidate.strip() not in product_ids):
                        counts["filtered_records"] += 1
                        continue
                    if kind == "reviews":
                        record = normalize_csv_review(line.data) if is_csv else normalize_review(line.data)
                        inserted = _store_review(connection, record, line.text, run_id, line.number,
                                                 version, fingerprint, started, known_products, source)
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
    report("importing_" + kind, bytes_read, force=True, limit_reached=limit_reached)
    if hash_file("verifying_" + kind) != fingerprint:
        raise ReviewDataError("读取期间输入文件发生变化，整次导入已回滚。")
    valid = counts["inserted_records"] + counts["duplicate_records"]
    summary = {
        "import_run_id": run_id, "source": source, "dataset_version": version,
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
    """在一个事务中导入两个输入；保留行错误，致命错误则回滚。"""
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
    if reviews_path and str(reviews_path).lower().endswith(".csv") and meta_path:
        raise ReviewDataError("CSV 评论已使用独立来源，不能与 Amazon 商品元数据一起导入。")
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
                progress({"stage": "committing_import", "completed": 0, "total": None, "unit": "files",
                          "total_lines": sum(item["total_lines"] for item in imports),
                          "inserted_records": sum(item["inserted_records"] for item in imports),
                          "invalid_records": sum(item["invalid_records"] for item in imports)})
    if progress:
        progress({"stage": "imported", "completed": len(files), "total": len(files), "unit": "files",
                  "total_lines": sum(item["total_lines"] for item in imports),
                  "inserted_records": sum(item["inserted_records"] for item in imports),
                  "invalid_records": sum(item["invalid_records"] for item in imports)})
    return {"status": "partial" if any(item["status"] == "partial" for item in imports) else "ok",
            "database": database_identity(database), "imports": imports, "quality": quality}


def get_quality_summary(db_path: str | Path) -> dict:
    with closing(connect_review_database(db_path, readonly=True)) as connection:
        with connection:
            connection.execute("BEGIN")
            return _quality_summary(connection)


def delete_import_runs(db_path: str | Path, import_run_ids: list[int]) -> dict:
    """删除指定导入批次及其评论依赖，保留被其他批次复用的商品记录。"""
    if (not isinstance(import_run_ids, list) or not import_run_ids
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in import_run_ids)):
        raise ReviewDataError("import_run_ids 必须是正整数列表。")
    ids = list(dict.fromkeys(import_run_ids))
    database = database_target(db_path)
    placeholders = ",".join("?" for _ in ids)
    with closing(connect_review_database(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        runs = connection.execute(
            f"SELECT id FROM import_runs WHERE id IN ({placeholders})", ids,
        ).fetchall()
        found = {row[0] for row in runs}
        missing = sorted(set(ids) - found)
        if missing:
            raise ReviewDataError(f"导入批次不存在：{', '.join(map(str, missing))}。")
        review_rows = connection.execute(
            f"SELECT id FROM reviews WHERE import_run_id IN ({placeholders})", ids,
        ).fetchall()
        review_ids = [row[0] for row in review_rows]
        if review_ids and "label_tasks" in table_names(connection):
            from data_analysis_agent.jobs.label_queue import delete_review_tasks_on_connection

            delete_review_tasks_on_connection(connection, review_ids)
        annotation_ids: list[int] = []
        human_review_ids: list[int] = []
        if review_ids:
            review_placeholders = ",".join("?" for _ in review_ids)
            annotation_ids = [row[0] for row in connection.execute(
                f"SELECT id FROM review_annotations WHERE review_id IN ({review_placeholders})", review_ids,
            )]
            if annotation_ids:
                annotation_placeholders = ",".join("?" for _ in annotation_ids)
                human_review_ids = [row[0] for row in connection.execute(
                    f"SELECT id FROM review_human_reviews WHERE annotation_id IN ({annotation_placeholders})", annotation_ids,
                )]
                if human_review_ids:
                    human_placeholders = ",".join("?" for _ in human_review_ids)
                    connection.execute(
                        f"DELETE FROM review_human_opinions WHERE human_review_id IN ({human_placeholders})",
                        human_review_ids,
                    )
                    connection.execute(
                        f"DELETE FROM review_human_reviews WHERE id IN ({human_placeholders})",
                        human_review_ids,
                    )
                connection.execute(
                    f"DELETE FROM review_opinions WHERE annotation_id IN ({annotation_placeholders})",
                    annotation_ids,
                )
                connection.execute(
                    f"DELETE FROM review_annotations WHERE id IN ({annotation_placeholders})",
                    annotation_ids,
                )
        if review_ids:
            connection.execute(
                f"DELETE FROM reviews WHERE id IN ({','.join('?' for _ in review_ids)})", review_ids,
            )
        connection.execute(
            f"DELETE FROM import_errors WHERE import_run_id IN ({placeholders})", ids,
        )
        # A product row may have been imported by this batch but later reused or
        # still referenced by another review, so only remove unreferenced rows.
        connection.execute(
            f"DELETE FROM products WHERE import_run_id IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.source=products.source "
            "AND r.dataset_version=products.dataset_version AND r.product_id=products.product_id)", ids,
        )
        connection.execute(f"DELETE FROM import_runs WHERE id IN ({placeholders})", ids)
        return {
            "import_run_ids": ids,
            "deleted_import_runs": len(ids),
            "deleted_reviews": len(review_ids),
            "deleted_annotations": len(annotation_ids),
            "deleted_human_reviews": len(human_review_ids),
        }


def get_review_evidence(db_path: str | Path, review_id: int) -> dict:
    """即使输入文件后来被移动，也返回已保存的原始正文。"""
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
