"""评论统计与证据查询；CLI、HTTP API 和后续 Agent 共用同一业务口径。"""

from contextlib import closing, contextmanager
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import sqlite3
from typing import Any, Sequence

from data_analysis_agent.core.database import PostgresConnection

from data_analysis_agent.reviews.label_store import check_labeling_schema
from data_analysis_agent.reviews.opinions import ASPECTS, SENTIMENTS
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.reviews.scope import analysis_scope, current_reviews_sql, parse_day
from data_analysis_agent.reviews.store import connect_review_database

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
    """复用监控事务，或为单次只读查询创建一致性快照。"""
    # 监控流程传入已有事务，确保四个统计工具读取同一时刻的数据。
    if isinstance(db_path, (sqlite3.Connection, PostgresConnection)):
        check_labeling_schema(db_path)
        if not db_path.in_transaction:
            raise ReviewDataError("共享统计连接必须已有事务。")
        yield db_path
        return
    with closing(connect_review_database(db_path, readonly=True)) as connection, connection:
        check_labeling_schema(connection)
        connection.execute("BEGIN")
        yield connection


def _rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _rate(count: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return _rounded(Decimal(count) / denominator)


def _summary(row: Any = None) -> dict[str, Any]:
    raw = dict(row) if row is not None else {}
    count_fields = (
        "review_count", "eligible_text_reviews", "successful_reviews", "failed_reviews",
        "unprocessed_reviews", "empty_text_reviews", "reviewed_reviews",
        "negative_review_count",
    )
    result = {name: raw.get(name, 0) for name in count_fields}
    result["labeling_coverage"] = _rate(result["successful_reviews"], result["eligible_text_reviews"])
    result["negative_rate"] = _rate(result["negative_review_count"], result["successful_reviews"])
    result["aspects"] = {}
    for aspect in ASPECTS:
        negative_count = raw.get("negative_" + aspect, 0)
        result["aspects"][aspect] = {
            "negative_review_count": negative_count,
            "negative_rate": _rate(negative_count, result["successful_reviews"]),
        }
    return result


def _summaries(
    connection: Any,
    identity: tuple[str, str, str],
    where: str,
    filters: Sequence[Any],
    group: str | None = None,
) -> list[Any]:
    """按指定维度聚合评论，并保留所有指标的统一分母。"""
    groups = {
        None: ("", ""),
        "day": ("substr(c.published_at,1,10) bucket, ", " GROUP BY bucket ORDER BY bucket"),
        "month": ("substr(c.published_at,1,7) bucket, ", " GROUP BY bucket ORDER BY bucket"),
        "product": (
            "c.source, c.dataset_version, c.product_id, ",
            " GROUP BY c.source,c.dataset_version,c.product_id "
            "ORDER BY c.source,c.dataset_version,c.product_id",
        ),
    }
    select, grouping = groups[group]
    columns = []
    for name, condition in (
        ("review_count", "TRUE"), ("eligible_text_reviews", "c.has_text=1"),
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
    query = (
        cte
        + "SELECT "
        + select
        + ", ".join(columns)
        + " FROM current c LEFT JOIN effective_opinions o ON o.review_id=c.id WHERE "
        + where
        + grouping
    )
    return connection.execute(query, (*parameters, *filters)).fetchall()


def _buckets(scope: dict[str, Any], period: str) -> list[str]:
    start, end = parse_day(scope["date_from"], "date_from"), parse_day(scope["date_to"], "date_to")
    if scope["as_of"]:
        end = min(end, parse_day(scope["as_of"][:10], "as_of"))
    if end < start:
        return []
    if period == "day":
        count = (end - start).days + 1
    else:
        count = (end.year - start.year) * 12 + end.month - start.month + 1
    if count > 10000:
        raise ReviewDataError("统计区间超过 10000 个时间桶，请缩小区间或改用月度统计。")
    if period == "day":
        return [(start + timedelta(days=index)).isoformat() for index in range(count)]
    first = start.year * 12 + start.month - 1
    return [f"{(first + index) // 12:04d}-{(first + index) % 12 + 1:02d}" for index in range(count)]


def get_trend(
    db_path: str,
    *,
    model: str,
    base_url: str,
    prompt_version: str,
    date_from: str,
    date_to: str,
    product_ids: list[str] | None = None,
    as_of: str | None = None,
    source: str | None = None,
    dataset_version: str | None = None,
    period: str = "month",
) -> dict[str, Any]:
    """返回 UTC 时间桶、覆盖率和去重后的负面评论数，并补齐可见空桶。"""
    if period not in ("day", "month"):
        raise ReviewDataError("period 仅支持 day 或 month。")
    identity, where, filters, scope = analysis_scope(
        model=model,
        base_url=base_url,
        prompt_version=prompt_version,
        date_from=date_from,
        date_to=date_to,
        product_ids=product_ids,
        as_of=as_of,
        source=source,
        dataset_version=dataset_version,
    )
    keys = _buckets(scope, period)
    with _snapshot(db_path) as connection:
        summary = _summary(_summaries(connection, identity, where, filters)[0])
        grouped = {row["bucket"]: _summary(row) for row in _summaries(connection, identity, where, filters, period)}
    return {
        "scope": scope,
        "definitions": dict(DEFINITIONS),
        "period": period,
        "summary": summary,
        "buckets": [
            {"bucket": key, **(grouped[key] if key in grouped else _summary())}
            for key in keys
        ],
    }


def get_top_issues(
    db_path: str,
    *,
    model: str,
    base_url: str,
    prompt_version: str,
    date_from: str,
    date_to: str,
    baseline_from: str,
    baseline_to: str,
    product_ids: list[str] | None = None,
    as_of: str | None = None,
    source: str | None = None,
    dataset_version: str | None = None,
    top_n: int = 6,
) -> dict[str, Any]:
    """按当前窗口的负面评论数排序，并与调用方指定的更早基线比较。"""
    validate_integer(top_n, "top_n", maximum=len(ASPECTS))
    common = {
        "model": model,
        "base_url": base_url,
        "prompt_version": prompt_version,
        "product_ids": product_ids,
        "as_of": as_of,
        "source": source,
        "dataset_version": dataset_version,
    }
    identity, where, filters, scope = analysis_scope(date_from=date_from, date_to=date_to, **common)
    _, baseline_where, baseline_filters, baseline_scope = analysis_scope(
        date_from=baseline_from, date_to=baseline_to, **common)
    if baseline_to >= date_from:
        raise ReviewDataError("基线结束日期必须早于当前窗口开始日期。")
    with _snapshot(db_path) as connection:
        current = _summary(_summaries(connection, identity, where, filters)[0])
        baseline = _summary(_summaries(connection, identity, baseline_where, baseline_filters)[0])
    ranked = sorted(
        ASPECTS,
        key=lambda aspect: (
            -current["aspects"][aspect]["negative_review_count"],
            aspect,
        ),
    )
    issues = []
    for rank, aspect in enumerate(ranked[:top_n], 1):
        now, before = current["aspects"][aspect], baseline["aspects"][aspect]
        delta = None
        if current["successful_reviews"] and baseline["successful_reviews"]:
            delta = _rounded(
                (
                    Decimal(now["negative_review_count"])
                    / current["successful_reviews"]
                    - Decimal(before["negative_review_count"])
                    / baseline["successful_reviews"]
                )
                * 100
            )
        issues.append(
            {
                "rank": rank,
                "aspect": aspect,
                "aspect_label": ASPECTS[aspect],
                **now,
                "baseline_negative_review_count": before["negative_review_count"],
                "baseline_negative_rate": before["negative_rate"],
                "negative_review_count_change": (
                    now["negative_review_count"] - before["negative_review_count"]
                ),
                "negative_rate_change_pp": delta,
            }
        )
    return {"scope": scope, "baseline_scope": baseline_scope, "definitions": dict(DEFINITIONS),
            "current": current, "baseline": baseline, "issues": issues}


def compare_products(
    db_path: str,
    *,
    model: str,
    base_url: str,
    prompt_version: str,
    date_from: str,
    date_to: str,
    product_ids: list[str] | None = None,
    as_of: str | None = None,
    source: str | None = None,
    dataset_version: str | None = None,
) -> dict[str, Any]:
    """比较可见商品；不同来源和数据集版本始终分别统计。"""
    identity, where, filters, scope = analysis_scope(
        model=model, base_url=base_url, prompt_version=prompt_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    with _snapshot(db_path) as connection:
        rows = _summaries(connection, identity, where, filters, "product")
    products = [{"source": row["source"], "dataset_version": row["dataset_version"],
                 "product_id": row["product_id"], **_summary(row)} for row in rows]
    seen = {item["product_id"] for item in products}
    return {"scope": scope, "definitions": dict(DEFINITIONS), "products": products,
            "no_reviews_product_ids": [pid for pid in scope["product_ids"] if pid not in seen]}


def get_evidence(
    db_path: str,
    *,
    model: str,
    base_url: str,
    prompt_version: str,
    date_from: str,
    date_to: str,
    product_ids: list[str] | None = None,
    as_of: str | None = None,
    source: str | None = None,
    dataset_version: str | None = None,
    aspect: str | None = None,
    sentiment: str | None = "negative",
    review_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页返回匹配评论、观点引用和原始文件位置，证据只来自当前快照。"""
    identity, where, filters, scope = analysis_scope(
        model=model, base_url=base_url, prompt_version=prompt_version, date_from=date_from, date_to=date_to,
        product_ids=product_ids, as_of=as_of, source=source, dataset_version=dataset_version)
    if aspect is not None and (not isinstance(aspect, str) or aspect not in ASPECTS):
        raise ReviewDataError("aspect 必须是六个有效维度之一。")
    if sentiment is not None and sentiment not in SENTIMENTS:
        raise ReviewDataError("sentiment 必须是有效情感或 None。")
    for name, value, maximum in (("page", page, 1000000), ("page_size", page_size, 100),
                                  ("review_id", review_id, 2147483647)):
        if name == "review_id" and value is None:
            continue
        validate_integer(value, name, maximum=maximum)
    opinion_where, opinion_filters = ["TRUE"], []
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
