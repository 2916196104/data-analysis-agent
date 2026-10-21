"""Explicit opinion-table setup, append-only attempts and incremental queries."""

from contextlib import closing
from pathlib import Path

from .review_loader import ReviewDataError
from .review_scope import current_reviews_sql, model_identity, review_scope
from .review_store import _connect, _now

LABEL_SCHEMA_VERSION = 1
LABEL_SCHEMA = """
CREATE TABLE review_labeling_schema (version INTEGER PRIMARY KEY);
INSERT INTO review_labeling_schema VALUES (1);
CREATE TABLE review_annotations (
    id INTEGER PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES reviews(id),
    content_sha256 TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    base_url TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_version TEXT,
    response_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
    error_code TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    elapsed_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX review_annotation_success ON review_annotations
    (review_id, content_sha256, requested_model, base_url, prompt_version) WHERE status='success';
CREATE INDEX review_annotation_lookup ON review_annotations
    (review_id, content_sha256, requested_model, base_url, prompt_version, status);
CREATE TABLE review_opinions (
    id INTEGER PRIMARY KEY,
    annotation_id INTEGER NOT NULL REFERENCES review_annotations(id),
    aspect TEXT NOT NULL CHECK(aspect IN ('quality','effectiveness','price','packaging','logistics','service')),
    sentiment TEXT NOT NULL CHECK(sentiment IN ('positive','negative','neutral','unknown')),
    quote TEXT NOT NULL CHECK(quote<>''),
    quote_start INTEGER NOT NULL CHECK(quote_start>=0),
    quote_end INTEGER NOT NULL CHECK(quote_end>quote_start),
    UNIQUE(annotation_id, aspect, sentiment, quote_start, quote_end)
);
"""


def _check_labels(connection):
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('review_labeling_schema','review_annotations','review_opinions')"
    )}
    if len(tables) != 3:
        raise ReviewDataError("观点表未初始化，请由你执行 review_label_cli init --db 指定路径。")
    rows = connection.execute("SELECT version FROM review_labeling_schema").fetchall()
    if len(rows) != 1 or rows[0][0] != LABEL_SCHEMA_VERSION:
        raise ReviewDataError("观点表版本不支持，请核查数据库。")


def init_labeling(db_path: str | Path) -> dict:
    """User-invoked setup on an existing review DB; never migrate automatically."""
    with closing(_connect(db_path)) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('review_labeling_schema','review_annotations','review_opinions')"
        ).fetchall()
        if tables:
            _check_labels(connection)
            return {"created": False, "label_schema_version": LABEL_SCHEMA_VERSION}
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + LABEL_SCHEMA + "\nCOMMIT;")
        except Exception:
            connection.rollback()
            raise
    return {"created": True, "label_schema_version": LABEL_SCHEMA_VERSION}


def pending_reviews(db_path, *, model, base_url, prompt_version, limit, retry_failed=False, product_ids=(),
                    date_from=None, date_to=None, as_of=None, source=None, dataset_version=None):
    identity = model_identity(model, base_url, prompt_version, required=True)
    where, filters, _ = review_scope(product_ids=product_ids, date_from=date_from, date_to=date_to,
                                    as_of=as_of, source=source, dataset_version=dataset_version)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ReviewDataError("limit 必须是 1—1000 的整数。")
    if not isinstance(retry_failed, bool):
        raise ReviewDataError("retry_failed 必须是布尔值。")
    with closing(_connect(db_path, readonly=True)) as connection:
        _check_labels(connection)
        conditions = [where, "r.has_text=1", "NOT EXISTS (SELECT 1 FROM review_annotations a "
                      "WHERE a.review_id=r.id AND a.content_sha256=r.content_sha256 "
                      "AND a.requested_model=? AND a.base_url=? AND a.prompt_version=?"
                      + (" AND a.status='success')" if retry_failed else ")")]
        return [dict(row) for row in connection.execute(
            "SELECT r.id, r.text, r.content_sha256 FROM reviews r WHERE "
            + " AND ".join(conditions) + " ORDER BY r.id LIMIT ?", (*filters, *identity, limit)
        )]


def save_annotation(db_path, review, *, model, base_url, prompt_version, status,
                    opinions=(), model_version=None, response_id=None, error_code=None,
                    usage=None, elapsed_ms=0):
    """Commit one complete attempt and its verified opinions atomically."""
    usage = usage or {}
    key = (review["id"], review["content_sha256"], model, base_url, prompt_version)
    with closing(_connect(db_path)) as connection:
        _check_labels(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT text, content_sha256 FROM reviews WHERE id=?", (review["id"],)).fetchone()
            if current is None or current[0] != review["text"] or current[1] != review["content_sha256"]:
                raise ReviewDataError("标注期间评论内容变化，结果未保存。")
            if connection.execute(
                "SELECT 1 FROM review_annotations WHERE review_id=? AND content_sha256=? "
                "AND requested_model=? AND base_url=? AND prompt_version=? AND status='success'", key
            ).fetchone():
                return None
            for opinion in opinions:
                if review["text"][opinion["quote_start"]:opinion["quote_end"]] != opinion["quote"]:
                    raise ReviewDataError("观点原文位置与正文不一致，结果未保存。")
            if status == "failed" and opinions:
                raise ReviewDataError("失败的标注不能保存观点。")
            annotation_id = connection.execute(
                """INSERT INTO review_annotations
                (review_id, content_sha256, requested_model, base_url, prompt_version, status,
                 model_version, response_id, error_code, input_tokens, output_tokens, total_tokens,
                 elapsed_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*key, status, model_version, response_id, error_code, usage.get("input_tokens"),
                 usage.get("output_tokens"), usage.get("total_tokens"), elapsed_ms, _now()),
            ).lastrowid
            connection.executemany(
                """INSERT INTO review_opinions
                (annotation_id, aspect, sentiment, quote, quote_start, quote_end) VALUES (?,?,?,?,?,?)""",
                [(annotation_id, item["aspect"], item["sentiment"], item["quote"],
                  item["quote_start"], item["quote_end"]) for item in opinions],
            )
            return annotation_id


def labeling_status(db_path, *, product_ids=None, date_from=None, date_to=None, as_of=None,
                    source=None, dataset_version=None, model=None, base_url=None, prompt_version=None):
    """Read scoped progress without credentials; preserve model counts and add effective counts."""
    where, filters, scope = review_scope(product_ids=product_ids, date_from=date_from, date_to=date_to,
                                        as_of=as_of, source=source, dataset_version=dataset_version)
    identity = model_identity(model, base_url, prompt_version)
    with closing(_connect(db_path, readonly=True)) as connection:
        _check_labels(connection)
        with connection:
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*), COALESCE(SUM(r.has_text=1),0) FROM reviews r WHERE "
                                       + where, filters).fetchone()
            profiles = []
            profile_filter = " AND a.requested_model=? AND a.base_url=? AND a.prompt_version=?" if identity else ""
            rows = connection.execute(
                """SELECT a.requested_model, a.base_url, a.prompt_version,
                SUM(a.status='success') AS successful_attempts, SUM(a.status='failed') AS failed_attempts,
                COUNT(*) AS attempts FROM review_annotations a JOIN reviews r ON r.id=a.review_id WHERE """
                + where + profile_filter + " GROUP BY a.requested_model, a.base_url, a.prompt_version "
                "ORDER BY a.requested_model, a.base_url, a.prompt_version", (*filters, *(identity or ()))
            ).fetchall()
            if identity and not rows:
                rows = [{"requested_model": identity[0], "base_url": identity[1], "prompt_version": identity[2],
                         "successful_attempts": 0, "failed_attempts": 0, "attempts": 0}]
            current_where, current_filters, _ = review_scope(**{k: v for k, v in scope.items() if k != "timezone"}, alias="c")
            for profile in rows:
                item = dict(profile)
                profile_identity = (item["requested_model"], item["base_url"], item["prompt_version"])
                cte, parameters = current_reviews_sql(connection, profile_identity)
                counts = connection.execute(
                    cte + """SELECT COALESCE(SUM(processing_status='success'),0),
                    COALESCE(SUM(processing_status='failed'),0), COALESCE(SUM(processing_status='pending'),0),
                    COALESCE(SUM(revision_id IS NOT NULL),0) FROM current c WHERE """ + current_where,
                    (*parameters, *current_filters)
                ).fetchone()
                item["successful_reviews"], item["failed_reviews"], item["unprocessed_reviews"], item["reviewed_reviews"] = counts
                item["opinion_count"] = connection.execute(
                    cte + "SELECT COUNT(*) FROM review_opinions o JOIN current c ON c.annotation_id=o.annotation_id "
                    "WHERE c.processing_status='success' AND " + current_where, (*parameters, *current_filters)
                ).fetchone()[0]
                item["effective_opinion_count"] = connection.execute(
                    cte + "SELECT COUNT(*) FROM effective_opinions o JOIN current c ON c.id=o.review_id WHERE "
                    + current_where, (*parameters, *current_filters)
                ).fetchone()[0]
                profiles.append(item)
            return {"review_count": total[0], "eligible_text_reviews": total[1],
                    "empty_text_reviews": total[0] - total[1], "profiles": profiles,
                    "scope": {**scope, "model": identity[0] if identity else None,
                              "base_url": identity[1] if identity else None,
                              "prompt_version": identity[2] if identity else None}}


def query_review_opinions(db_path, *, model, base_url, prompt_version, product_ids=None,
                          date_from=None, date_to=None, as_of=None, source=None, dataset_version=None,
                          page=1, page_size=20):
    """Return a read-only, explicitly versioned page of current effective opinions and evidence."""
    identity = model_identity(model, base_url, prompt_version, required=True)
    where, filters, scope = review_scope(product_ids=product_ids, date_from=date_from, date_to=date_to,
                                        as_of=as_of, source=source, dataset_version=dataset_version, alias="c")
    for name, value, maximum in (("page", page, 1000000), ("page_size", page_size, 100)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ReviewDataError(f"{name} 必须是 1—{maximum} 的整数。")
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        _check_labels(connection)
        connection.execute("BEGIN")
        cte, parameters = current_reviews_sql(connection, identity)
        total = connection.execute(cte + "SELECT COUNT(*) FROM current c WHERE " + where,
                                   (*parameters, *filters)).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        items = [dict(row) for row in connection.execute(cte + """
            SELECT c.id, c.source, c.dataset_version, c.product_id, c.text, c.published_at,
                c.processing_status, c.annotation_id, c.model_version, c.content_sha256, c.revision_id, c.human_status,
                CASE WHEN c.revision_id IS NOT NULL THEN 'human'
                     WHEN c.processing_status='success' THEN 'model' END opinion_source,
                c.line_number, i.file_sha256
            FROM current c JOIN import_runs i ON i.id=c.import_run_id WHERE """ + where
            + " ORDER BY c.published_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (*parameters, *filters, page_size, (page - 1) * page_size))]
        if items:
            ids = [item["id"] for item in items]
            grouped = {rid: [] for rid in ids}
            for row in connection.execute(cte + "SELECT * FROM effective_opinions WHERE review_id IN ("
                                          + ",".join("?" for _ in ids) + ") ORDER BY review_id, quote_start, aspect, sentiment",
                                          (*parameters, *ids)):
                grouped[row["review_id"]].append({k: row[k] for k in row.keys() if k != "review_id"})
            for item in items:
                item["opinions"] = grouped[item["id"]]
                item["evidence_ref"] = f"sha256:{item['file_sha256']}#line={item['line_number']}"
        return {"scope": {**scope, "model": identity[0], "base_url": identity[1], "prompt_version": identity[2]},
                "total": total, "page": page, "page_size": page_size, "pages": pages, "items": items}


def opinion_evidence(db_path, review_id):
    if isinstance(review_id, bool) or not isinstance(review_id, int) or review_id < 1:
        raise ReviewDataError("review_id 必须是正整数。")
    with closing(_connect(db_path, readonly=True)) as connection:
        _check_labels(connection)
        review = connection.execute(
            """SELECT r.id, r.product_id, r.text, r.content_sha256, r.line_number,
            i.file_path, i.file_sha256 FROM reviews r JOIN import_runs i ON r.import_run_id=i.id
            WHERE r.id=?""", (review_id,)
        ).fetchone()
        if review is None:
            raise ReviewDataError("评论 ID 不存在。")
        result = dict(review)
        result["evidence_ref"] = f"sha256:{review['file_sha256']}#line={review['line_number']}"
        result["annotations"] = []
        for row in connection.execute("SELECT * FROM review_annotations WHERE review_id=? ORDER BY id", (review_id,)):
            annotation = dict(row)
            annotation["matches_current_content"] = row["content_sha256"] == review["content_sha256"]
            annotation["opinions"] = [dict(item) for item in connection.execute(
                "SELECT aspect, sentiment, quote, quote_start, quote_end FROM review_opinions "
                "WHERE annotation_id=? ORDER BY id", (row["id"],)
            )]
            result["annotations"].append(annotation)
        return result
