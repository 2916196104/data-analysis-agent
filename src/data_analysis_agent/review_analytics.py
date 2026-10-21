"""Read-only review business tools shared by CLI and future API/Agent callers."""

from contextlib import closing, contextmanager
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import sqlite3

from .review_label_store import _check_labels
from .review_labeler import ASPECTS
from .review_loader import ReviewDataError
from .review_scope import _day, current_reviews_sql, model_identity, review_scope
from .review_store import _connect

DEFINITIONS = {
    "count_unit": "distinct_reviews",
    "labeling_coverage_denominator": "eligible_text_reviews",
    "negative_rate_denominator": "successful_reviews",
    "rate_unit": "fraction_0_to_1",
    "human_policy": "latest_snapshot",
    "as_of_field": "published_at",
}


@contextmanager
def _snapshot(db_path):
    # Monitoring supplies its transaction so all four tools see the same snapshot.
    if isinstance(db_path, sqlite3.Connection):
        _check_labels(db_path)
        if not db_path.in_transaction:
            raise ReviewDataError("共享统计连接必须已有事务。")
        yield db_path
        return
    with closing(_connect(db_path, readonly=True)) as connection, connection:
        _check_labels(connection)
        connection.execute("BEGIN")
        yield connection


def _scope(*, model, base_url, prompt_version, date_from, date_to, **filters):
    if date_from is None or date_to is None:
        raise ReviewDataError("统计查询必须指定 date_from 和 date_to。")
    identity = model_identity(model, base_url, prompt_version, required=True)
    where, values, scope = review_scope(date_from=date_from, date_to=date_to, **filters, alias="c")
    return identity, where, values, {**scope, "model": identity[0], "base_url": identity[1], "prompt_version": identity[2]}


def _rounded(value):
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _rate(count, denominator):
    return _rounded(Decimal(count) / denominator) if denominator else None


def _summary(row=None):
    raw = dict(row) if row is not None else {}
    result = {name: raw.get(name, 0) for name in (
        "review_count", "eligible_text_reviews", "successful_reviews", "failed_reviews",
        "unprocessed_reviews", "empty_text_reviews", "reviewed_reviews", "negative_review_count")}
    result["labeling_coverage"] = _rate(result["successful_reviews"], result["eligible_text_reviews"])
    result["negative_rate"] = _rate(result["negative_review_count"], result["successful_reviews"])
    result["aspects"] = {aspect: {
        "negative_review_count": raw.get("negative_" + aspect, 0),
        "negative_rate": _rate(raw.get("negative_" + aspect, 0), result["successful_reviews"]),
    } for aspect in ASPECTS}
    return result


def _summaries(connection, identity, where, filters, group=None):
    groups = {
        None: ("", ""),
        "day": ("substr(c.published_at,1,10) bucket, ", " GROUP BY bucket ORDER BY bucket"),
        "month": ("substr(c.published_at,1,7) bucket, ", " GROUP BY bucket ORDER BY bucket"),
        "product": ("c.source, c.dataset_version, c.product_id, ",
                    " GROUP BY c.source,c.dataset_version,c.product_id ORDER BY c.source,c.dataset_version,c.product_id"),
    }
    select, grouping = groups[group]
    columns = []
    for name, condition in (
        ("review_count", "1"), ("eligible_text_reviews", "c.has_text=1"),
        ("successful_reviews", "c.processing_status='success'"),
        ("failed_reviews", "c.processing_status='failed'"),
        ("unprocessed_reviews", "c.processing_status='pending'"),
        ("empty_text_reviews", "c.processing_status='empty'"),
        ("reviewed_reviews", "c.revision_id IS NOT NULL"),
        ("negative_review_count", "o.sentiment='negative'"),
        *(("negative_" + aspect, f"o.sentiment='negative' AND o.aspect='{aspect}'") for aspect in ASPECTS),
    ):
        columns.append(f"COUNT(DISTINCT CASE WHEN {condition} THEN c.id END) {name}")
    cte, parameters = current_reviews_sql(connection, identity)
    return connection.execute(cte + "SELECT " + select + ", ".join(columns)
                              + " FROM current c LEFT JOIN effective_opinions o ON o.review_id=c.id WHERE "
                              + where + grouping, (*parameters, *filters)).fetchall()


def _buckets(scope, period):
    start, end = _day(scope["date_from"], "date_from"), _day(scope["date_to"], "date_to")
    if scope["as_of"]:
        end = min(end, _day(scope["as_of"][:10], "as_of"))
    if end < start:
        return []
    count = (end - start).days + 1 if period == "day" else (end.year - start.year) * 12 + end.month - start.month + 1
    if count > 10000:
        raise ReviewDataError("统计区间超过 10000 个时间桶，请缩小区间或改用月度统计。")
    if period == "day":
        return [(start + timedelta(days=index)).isoformat() for index in range(count)]
    first = start.year * 12 + start.month - 1
    return [f"{(first + index) // 12:04d}-{(first + index) % 12 + 1:02d}" for index in range(count)]


def get_trend(db_path, *, model, base_url, prompt_version, date_from, date_to,
              product_ids=None, as_of=None, source=None, dataset_version=None, period="month"):
    """Return UTC buckets, coverage and distinct negative-review counts; fill empty visible buckets."""
    if period not in ("day", "month"):
        raise ReviewDataError("period 仅支持 day 或 month。")
    identity, where, filters, scope = _scope(
        model=model, base_url=base_url, prompt_version=prompt_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    keys = _buckets(scope, period)
    with _snapshot(db_path) as connection:
        summary = _summary(_summaries(connection, identity, where, filters)[0])
        grouped = {row["bucket"]: _summary(row) for row in _summaries(connection, identity, where, filters, period)}
    return {"scope": scope, "definitions": dict(DEFINITIONS), "period": period, "summary": summary,
            "buckets": [{"bucket": key, **(grouped[key] if key in grouped else _summary())} for key in keys]}


def get_top_issues(db_path, *, model, base_url, prompt_version, date_from, date_to,
                   baseline_from, baseline_to, product_ids=None, as_of=None,
                   source=None, dataset_version=None, top_n=6):
    """Rank current negative aspects and compare a caller-selected, strictly earlier baseline."""
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= len(ASPECTS):
        raise ReviewDataError("top_n 必须是 1—6 的整数。")
    common = dict(model=model, base_url=base_url, prompt_version=prompt_version,
                  product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    identity, where, filters, scope = _scope(date_from=date_from, date_to=date_to, **common)
    _, baseline_where, baseline_filters, baseline_scope = _scope(
        date_from=baseline_from, date_to=baseline_to, **common)
    if baseline_to >= date_from:
        raise ReviewDataError("基线结束日期必须早于当前窗口开始日期。")
    with _snapshot(db_path) as connection:
        current = _summary(_summaries(connection, identity, where, filters)[0])
        baseline = _summary(_summaries(connection, identity, baseline_where, baseline_filters)[0])
    ranked = sorted(ASPECTS, key=lambda aspect: (-current["aspects"][aspect]["negative_review_count"], aspect))
    issues = []
    for rank, aspect in enumerate(ranked[:top_n], 1):
        now, before = current["aspects"][aspect], baseline["aspects"][aspect]
        delta = None
        if current["successful_reviews"] and baseline["successful_reviews"]:
            delta = _rounded((Decimal(now["negative_review_count"]) / current["successful_reviews"]
                              - Decimal(before["negative_review_count"]) / baseline["successful_reviews"]) * 100)
        issues.append({"rank": rank, "aspect": aspect, "aspect_label": ASPECTS[aspect], **now,
                       "baseline_negative_review_count": before["negative_review_count"],
                       "baseline_negative_rate": before["negative_rate"],
                       "negative_review_count_change": now["negative_review_count"] - before["negative_review_count"],
                       "negative_rate_change_pp": delta})
    return {"scope": scope, "baseline_scope": baseline_scope, "definitions": dict(DEFINITIONS),
            "current": current, "baseline": baseline, "issues": issues}


def compare_products(db_path, *, model, base_url, prompt_version, date_from, date_to,
                     product_ids=None, as_of=None, source=None, dataset_version=None):
    """Compare visible products without merging different sources or dataset versions."""
    identity, where, filters, scope = _scope(
        model=model, base_url=base_url, prompt_version=prompt_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    with _snapshot(db_path) as connection:
        rows = _summaries(connection, identity, where, filters, "product")
    products = [{"source": row["source"], "dataset_version": row["dataset_version"],
                 "product_id": row["product_id"], **_summary(row)} for row in rows]
    seen = {item["product_id"] for item in products}
    return {"scope": scope, "definitions": dict(DEFINITIONS), "products": products,
            "no_reviews_product_ids": [pid for pid in scope["product_ids"] if pid not in seen]}


def get_evidence(db_path, *, model, base_url, prompt_version, date_from, date_to,
                 product_ids=None, as_of=None, source=None, dataset_version=None, aspect=None,
                 sentiment="negative", review_id=None, page=1, page_size=20):
    """Page matching reviews with only matching opinion quotes and their original file locations."""
    identity, where, filters, scope = _scope(
        model=model, base_url=base_url, prompt_version=prompt_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    if aspect is not None and (not isinstance(aspect, str) or aspect not in ASPECTS):
        raise ReviewDataError("aspect 必须是六个有效维度之一。")
    if sentiment is not None and sentiment not in ("positive", "negative", "neutral", "unknown"):
        raise ReviewDataError("sentiment 必须是有效情感或 None。")
    for name, value, maximum in (("page", page, 1000000), ("page_size", page_size, 100),
                                  ("review_id", review_id, 2147483647)):
        if name == "review_id" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ReviewDataError(f"{name} 必须是 1—{maximum} 的整数。")
    opinion_where, opinion_filters = ["1"], []
    for field, value in (("aspect", aspect), ("sentiment", sentiment)):
        if value is not None:
            opinion_where.append(f"o.{field}=?")
            opinion_filters.append(value)
    opinion_where = " AND ".join(opinion_where)
    if review_id is not None:
        where += " AND c.id=?"
        filters.append(review_id)
    matching = where + " AND EXISTS (SELECT 1 FROM effective_opinions o WHERE o.review_id=c.id AND " + opinion_where + ")"
    with _snapshot(db_path) as connection:
        cte, parameters = current_reviews_sql(connection, identity)
        total = connection.execute(cte + "SELECT COUNT(*) FROM current c WHERE " + matching,
                                   (*parameters, *filters, *opinion_filters)).fetchone()[0]
        total_opinions = connection.execute(
            cte + "SELECT COUNT(*) FROM current c JOIN effective_opinions o ON o.review_id=c.id WHERE "
            + where + " AND " + opinion_where, (*parameters, *filters, *opinion_filters)).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        items = [dict(row) for row in connection.execute(cte + """
            SELECT c.id review_id, c.source, c.dataset_version, c.product_id, c.text, c.published_at,
                c.content_sha256, c.annotation_id, c.model_version, c.revision_id,
                CASE WHEN c.revision_id IS NOT NULL THEN 'human' ELSE 'model' END opinion_source,
                i.file_path, i.file_sha256, c.line_number
            FROM current c JOIN import_runs i ON i.id=c.import_run_id WHERE """ + matching
            + " ORDER BY c.published_at DESC,c.id DESC LIMIT ? OFFSET ?",
            (*parameters, *filters, *opinion_filters, page_size, (page - 1) * page_size))]
        if items:
            ids = [item["review_id"] for item in items]
            grouped = {rid: [] for rid in ids}
            for row in connection.execute(
                cte + "SELECT * FROM effective_opinions o WHERE o.review_id IN ("
                + ",".join("?" for _ in ids) + ") AND " + opinion_where
                + " ORDER BY o.review_id,o.quote_start,o.quote_end,o.aspect,o.sentiment",
                (*parameters, *ids, *opinion_filters)):
                grouped[row["review_id"]].append({k: row[k] for k in row.keys() if k != "review_id"})
            for item in items:
                item["opinions"] = grouped[item["review_id"]]
                item["evidence_ref"] = f"sha256:{item['file_sha256']}#line={item['line_number']}"
    return {"scope": scope, "aspect": aspect, "sentiment": sentiment, "review_id": review_id,
            "total": total, "total_opinions": total_opinions, "page": page, "page_size": page_size,
            "pages": pages, "items": items}
