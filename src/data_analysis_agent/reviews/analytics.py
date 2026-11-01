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
    "positive_aspect_rate_denominator": "positive_review_count",
    "positive_success_rate_denominator": "successful_reviews",
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


def count_rate(count: int, denominator: int) -> float | None:
    """统计和数据集摘要共用六位小数占比；空分母保留空值。"""
    if not denominator:
        return None
    return _rounded(Decimal(count) / denominator)


def _summary(row: Any = None, *, include_positive: bool = False) -> dict[str, Any]:
    raw = dict(row) if row is not None else {}
    count_fields = (
        "review_count", "eligible_text_reviews", "successful_reviews", "failed_reviews",
        "unprocessed_reviews", "empty_text_reviews", "reviewed_reviews",
        "negative_review_count",
    )
    result = {name: raw.get(name, 0) for name in count_fields}
    result["labeling_coverage"] = count_rate(result["successful_reviews"], result["eligible_text_reviews"])
    result["negative_rate"] = count_rate(result["negative_review_count"], result["successful_reviews"])
    if include_positive:
        result["positive_review_count"] = raw.get("positive_review_count", 0)
        result["positive_rate"] = count_rate(result["positive_review_count"], result["successful_reviews"])
    result["aspects"] = {}
    for aspect in ASPECTS:
        negative_count = raw.get("negative_" + aspect, 0)
        result["aspects"][aspect] = {
            "negative_review_count": negative_count,
            "negative_rate": count_rate(negative_count, result["successful_reviews"]),
        }
        if include_positive:
            positive_count = raw.get("positive_" + aspect, 0)
            result["aspects"][aspect].update(
                positive_review_count=positive_count,
                positive_rate=count_rate(positive_count, result["positive_review_count"]),
                positive_success_rate=count_rate(positive_count, result["successful_reviews"]),
            )
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
        ("positive_review_count", "o.sentiment='positive'"),
        *(("negative_" + aspect, f"o.sentiment='negative' AND o.aspect='{aspect}'") for aspect in ASPECTS),
        *(("positive_" + aspect, f"o.sentiment='positive' AND o.aspect='{aspect}'") for aspect in ASPECTS),
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


def get_positive_aspects(
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
    """按正向观点统计六个业务维度；所有计数均按评论 ID 去重。"""
    identity, where, filters, scope = analysis_scope(
        model=model, base_url=base_url, prompt_version=prompt_version,
        date_from=date_from, date_to=date_to, product_ids=product_ids,
        as_of=as_of, source=source, dataset_version=dataset_version,
    )
    with _snapshot(db_path) as connection:
        summary = _summary(_summaries(connection, identity, where, filters)[0], include_positive=True)

    positive_review_count = summary["positive_review_count"]
    successful_reviews = summary["successful_reviews"]
    ranked = sorted(
        ASPECTS,
        key=lambda aspect: (-summary["aspects"][aspect]["positive_review_count"], aspect),
    )
    aspects = []
    for rank, aspect in enumerate(ranked, 1):
        positive_count = summary["aspects"][aspect]["positive_review_count"]
        aspects.append({
            "rank": rank,
            "aspect": aspect,
            "aspect_label": ASPECTS[aspect],
            "positive_review_count": positive_count,
            "positive_rate": count_rate(positive_count, positive_review_count),
            "positive_success_rate": count_rate(positive_count, successful_reviews),
            "positive_rate_denominator": positive_review_count,
            "positive_success_rate_denominator": successful_reviews,
        })

    if successful_reviews == 0:
        data_status = "insufficient_data"
    elif positive_review_count == 0:
        data_status = "no_positive_mentions"
    else:
        data_status = "ok"
    return {
        "analysis_type": "positive_aspects",
        "sentiment": "positive",
        "data_status": data_status,
        "scope": scope,
        "definitions": {
            **DEFINITIONS,
            "positive_count_unit": "distinct_reviews_with_positive_opinion",
            "positive_aspect_rate_denominator": "positive_review_count",
            "positive_success_rate_denominator": "successful_reviews",
        },
        "summary": summary,
        "sample_size": {
            "review_count": summary["review_count"],
            "eligible_text_reviews": summary["eligible_text_reviews"],
            "successful_reviews": successful_reviews,
            "positive_review_count": positive_review_count,
        },
        "aspects": aspects,
    }


def get_top_issues(
    db_path: str,
    *,
    model: str,
    base_url: str,
    prompt_version: str,
    date_from: str,
    date_to: str,
    baseline_from: str | None = None,
    baseline_to: str | None = None,
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
    if baseline_from is None and baseline_to is None:
        with _snapshot(db_path) as connection:
            current = _summary(_summaries(connection, identity, where, filters)[0])
        ranked = sorted(ASPECTS, key=lambda aspect: (-current['aspects'][aspect]['negative_review_count'], aspect))
        return {"scope": scope, "definitions": dict(DEFINITIONS), "current": current,
                "issues": [{"rank": rank, "aspect": aspect, "aspect_label": ASPECTS[aspect],
                            **current['aspects'][aspect], "baseline_negative_review_count": None,
                            "baseline_negative_rate": None, "negative_review_count_change": None,
                            "negative_rate_change_pp": None} for rank, aspect in enumerate(ranked[:top_n], 1)]}
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


def get_data_quality(db_path, **scope):
    """按当前正文和标注身份统计缺失、失败、待处理及覆盖率。"""
    identity, where, filters, normalized = analysis_scope(**scope)
    with _snapshot(db_path) as connection:
        summary = _summary(_summaries(connection, identity, where, filters)[0])
        cte, parameters = current_reviews_sql(connection, identity)
        failures = [dict(row) for row in connection.execute(
            cte + "SELECT c.error_code, COUNT(*) review_count FROM current c WHERE " + where
            + " AND c.processing_status='failed' GROUP BY c.error_code ORDER BY review_count DESC,c.error_code",
            (*parameters, *filters))]
        sources = [dict(row) for row in connection.execute(
            cte + "SELECT i.file_sha256, COUNT(*) review_count FROM current c "
            "JOIN import_runs i ON i.id=c.import_run_id WHERE " + where
            + " GROUP BY i.file_sha256 ORDER BY i.file_sha256", (*parameters, *filters))]
    return {"scope": normalized, "definitions": {**DEFINITIONS, "missing_text_rate_denominator": "review_count",
                                                  "failed_rate_denominator": "eligible_text_reviews"}, "summary": summary,
            "missing_text_rate": count_rate(summary["empty_text_reviews"], summary["review_count"]),
            "failed_rate": count_rate(summary["failed_reviews"], summary["eligible_text_reviews"]),
            "failures": failures, "sources": sources}


def get_aspect_trend(db_path, *, aspect=None, period="month", **scope):
    """复用去重评论口径；同一评论的多条观点不会重复计数。"""
    if aspect is not None and aspect not in ASPECTS:
        raise ReviewDataError("aspect 必须是六个有效维度之一。")
    result = get_trend(db_path, period=period, **scope)
    selected = {key: label for key, label in ASPECTS.items() if aspect is None or key == aspect}
    for row in [result["summary"], *result["buckets"]]:
        row["aspects"] = {key: row["aspects"][key] for key in selected}
    return {**result, "aspects": selected}


def get_rating_distribution(db_path, *, group_by="product", **scope):
    """星级来自原始评分，不依赖标注；非整数星级单列，不作四舍五入。"""
    if group_by not in ("product", "month", "all"):
        raise ReviewDataError("group_by 必须是 product、month 或 all。")
    identity, where, filters, normalized = analysis_scope(**scope)
    grouping = {"product": "c.product_id", "month": "substr(c.published_at,1,7)", "all": "'all'"}[group_by]
    with _snapshot(db_path) as connection:
        cte, parameters = current_reviews_sql(connection, identity)
        rows = connection.execute(
            cte + "SELECT c.source,c.dataset_version," + grouping + " group_key,c.rating,COUNT(*) review_count "
            "FROM current c WHERE " + where + " GROUP BY c.source,c.dataset_version,group_key,c.rating "
            "ORDER BY c.source,c.dataset_version,group_key,c.rating", (*parameters, *filters)).fetchall()
    grouped = {}
    for row in rows:
        key = (row["source"], row["dataset_version"], row["group_key"])
        item = grouped.setdefault(key, {"source": key[0], "dataset_version": key[1], "group": key[2],
            "review_count": 0, "rated_reviews": 0, "missing_rating_reviews": 0, "other_rating_reviews": 0,
            "stars": [{"star": star, "review_count": 0} for star in range(1, 6)]})
        rating, count = row["rating"], row["review_count"]
        item["review_count"] += count
        if rating is None:
            item["missing_rating_reviews"] += count
        elif rating in (1, 2, 3, 4, 5):
            item["rated_reviews"] += count
            item["stars"][int(rating) - 1]["review_count"] += count
        else:
            item["other_rating_reviews"] += count
    if not grouped and group_by == "all":
        grouped["empty"] = {"source": normalized["source"], "dataset_version": normalized["dataset_version"],
            "group": "all", "review_count": 0, "rated_reviews": 0, "missing_rating_reviews": 0,
            "other_rating_reviews": 0, "stars": [{"star": star, "review_count": 0} for star in range(1, 6)]}
    for item in grouped.values():
        for star in item["stars"]:
            star["rate"] = count_rate(star["review_count"], item["rated_reviews"])
    return {"scope": normalized, "group_by": group_by, "groups": list(grouped.values()),
            "definitions": {"count_unit": "distinct_reviews", "rating_source": "raw_review_rating",
                            "rating_rate_denominator": "rated_reviews", "valid_stars": [1, 2, 3, 4, 5],
                            "other_rating_policy": "excluded_without_rounding", "rate_unit": "fraction_0_to_1"}}


def compare_time_windows(db_path, *, baseline_from, baseline_to, **scope):
    """比较任意两个不重叠窗口；百分点由未舍入的计数计算。"""
    result = get_top_issues(db_path, baseline_from=baseline_from, baseline_to=baseline_to, **scope)
    current, baseline = result["current"], result["baseline"]
    changes = {key: current[key] - baseline[key] for key in (
        "review_count", "eligible_text_reviews", "successful_reviews", "failed_reviews",
        "unprocessed_reviews", "empty_text_reviews", "negative_review_count")}
    for metric, numerator, denominator in (("negative_rate", "negative_review_count", "successful_reviews"),
                                           ("labeling_coverage", "successful_reviews", "eligible_text_reviews")):
        a, b = current[denominator], baseline[denominator]
        changes[metric + "_pp"] = (_rounded((Decimal(current[numerator]) / a
                                              - Decimal(baseline[numerator]) / b) * 100) if a and b else None)
    return {**result, "changes": changes,
            "current_days": (parse_day(scope["date_to"], "date_to") - parse_day(scope["date_from"], "date_from")).days + 1,
            "baseline_days": (parse_day(baseline_to, "baseline_to") - parse_day(baseline_from, "baseline_from")).days + 1}


def get_opinion_breakdown(db_path, *, aspect=None, sentiment="all", topic=None, **scope):
    """全文范围按已有观点原文和情感归组；不是细分标签或抽样推算。"""
    if aspect is not None and aspect not in ASPECTS:
        raise ReviewDataError("aspect 必须是六个有效维度之一。")
    if sentiment not in (*SENTIMENTS, "all"):
        raise ReviewDataError("sentiment 必须是有效情感或 all。")
    if topic is not None and (not isinstance(topic, str) or not 1 <= len(topic) <= 100):
        raise ReviewDataError("topic 必须是 1 至 100 字的检索词。")
    identity, where, filters, normalized_scope = analysis_scope(**scope)
    conditions, values = [where], list(filters)
    for field, value in (("aspect", aspect), ("sentiment", None if sentiment == "all" else sentiment)):
        if value is not None:
            conditions.append(f"o.{field}=?")
            values.append(value)
    keywords = {"制冷": ["制冷", "冷气", "凉快", "凉爽"], "静音": ["静音", "噪音", "声音", "噪声"],
                "安装": ["安装"]}.get(topic, [topic] if topic else [])
    if keywords:
        conditions.append("(" + " OR ".join("o.quote LIKE ? ESCAPE '!'" for _ in keywords) + ")")
        values.extend("%" + word.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%" for word in keywords)
    with _snapshot(db_path) as connection:
        cte, parameters = current_reviews_sql(connection, identity)
        matching = " FROM current c JOIN effective_opinions o ON o.review_id=c.id WHERE " + " AND ".join(conditions)
        args = (*parameters, *values)
        totals = connection.execute(cte + "SELECT COUNT(DISTINCT c.id) reviews,COUNT(*) opinions" + matching, args).fetchone()
        groups = [dict(row) for row in connection.execute(cte + """SELECT o.quote,o.sentiment,
            COUNT(DISTINCT c.id) review_count,COUNT(*) opinion_count,MIN(c.id) review_id"""
            + matching + " GROUP BY o.quote,o.sentiment ORDER BY review_count DESC,opinion_count DESC,o.quote,o.sentiment LIMIT 10", args)]
        items = []
        for row in groups[:3]:
            evidence = get_evidence(connection, **scope, aspect=aspect, sentiment=row['sentiment'], review_id=row['review_id'])
            item = evidence['items'][0]
            item['opinions'] = [opinion for opinion in item['opinions'] if opinion['quote'] == row['quote']]
            row['evidence_ref'] = item['evidence_ref']
            item.pop('file_path', None)
            if not any(saved['review_id'] == item['review_id'] for saved in items):
                items.append(item)
            else:
                saved = next(saved for saved in items if saved['review_id'] == item['review_id'])
                saved['opinions'].extend(opinion for opinion in item['opinions'] if opinion not in saved['opinions'])
        summary = _summary(_summaries(connection, identity, where, filters)[0])
    for row in groups:
        row['review_rate'] = count_rate(row['review_count'], totals['reviews'])
    return {"scope": normalized_scope, "summary": summary, "aspect": aspect,
            "sentiment": None if sentiment == 'all' else sentiment, "topic": topic, "keywords": keywords,
            "grouping": "exact_opinion_text_and_sentiment", "total": totals['reviews'],
            "total_opinions": totals['opinions'], "groups": groups, "items": items}


def get_aspect_opinions(db_path, *, aspect, sentiment, **scope):
    """提取二级分析的有限真实样本；完整总体计数仍由同一只读快照计算。"""
    if aspect not in ASPECTS or sentiment not in ('positive', 'negative'):
        raise ReviewDataError('二级分析必须指定一个一级维度及正向或负向情感。')
    if len(scope.get('product_ids') or []) != 1:
        raise ReviewDataError('二级分析首版仅支持一个明确的商品 ID。')
    identity, where, filters, normalized = analysis_scope(**scope)
    with _snapshot(db_path) as connection:
        evidence = get_evidence(connection, aspect=aspect, sentiment=sentiment, page_size=100, **scope)
        summary = _summary(_summaries(connection, identity, where, filters)[0])
    for item in evidence['items']:
        item.pop('file_path', None)
    return {**evidence, 'scope': normalized, 'summary': summary}


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
