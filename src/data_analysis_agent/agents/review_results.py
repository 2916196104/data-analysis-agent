"""将已校验工具快照适配为统一分析模型；不查询数据库，不修改原始结果。"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data_analysis_agent.reviews.opinions import ASPECTS


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Metric(AnalysisModel):
    name: str
    value: int | float | None
    unit: str = "reviews"
    numerator: int | None = None
    denominator: int | None = None
    denominator_name: str | None = None


class SampleSize(AnalysisModel):
    review_count: int
    eligible_text_reviews: int
    successful_reviews: int
    failed_reviews: int
    unprocessed_reviews: int
    labeling_coverage: float | int | None
    positive_review_count: int | None = None
    empty_text_reviews: int | None = None
    reviewed_reviews: int | None = None


class AspectStatistic(AnalysisModel):
    aspect: str
    aspect_label: str
    rank: int
    metrics: list[Metric]


class TrendPoint(AnalysisModel):
    bucket: str
    sample_size: SampleSize
    metric: Metric


class TrendSeries(AnalysisModel):
    aspect: str | None = None
    period: Literal["day", "month"]
    points: list[TrendPoint]


class EvidenceQuote(AnalysisModel):
    aspect: str
    sentiment: str
    quote: str
    quote_start: int
    quote_end: int
    truncated: bool = False


class EvidenceReference(AnalysisModel):
    source_result_id: str
    review_id: int
    product_id: str
    published_at: str
    evidence_ref: str
    opinion_source: str
    opinions: list[EvidenceQuote]


class StatisticGroup(AnalysisModel):
    group_id: str
    scope: dict
    sample_size: SampleSize | None = None
    metrics: list[Metric] = Field(default_factory=list)
    aspects: list[AspectStatistic] = Field(default_factory=list)
    trends: list[TrendSeries] = Field(default_factory=list)


class StructuredAnalysisResult(AnalysisModel):
    result_id: str
    kind: str
    analysis_type: str
    sentiment: str | None
    validation_status: Literal["passed"] = "passed"
    scope: dict
    statistics: list[StatisticGroup] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def sample_size(row):
    return SampleSize(**{key: row[key] for key in SampleSize.model_fields if key in row})


def rate_metric(name, count, denominator, value, denominator_name):
    return Metric(name=name, value=value, unit="fraction_0_to_1", numerator=count,
                  denominator=denominator, denominator_name=denominator_name)


def aspect_statistics(row, sentiment):
    counts = row.get("aspects", {})
    count_key = sentiment + "_review_count"
    ranked = sorted(counts, key=lambda key: (-counts[key][count_key], key))
    output = []
    for rank, key in enumerate(ranked, 1):
        item = counts[key]
        metrics = [Metric(name=count_key, value=item[count_key])]
        if sentiment == "positive":
            metrics.extend([
                rate_metric("positive_rate", item[count_key], row["positive_review_count"], item["positive_rate"], "positive_review_count"),
                rate_metric("positive_success_rate", item[count_key], row["successful_reviews"], item["positive_success_rate"], "successful_reviews"),
            ])
        else:
            metrics.append(rate_metric("negative_rate", item[count_key], row["successful_reviews"], item["negative_rate"], "successful_reviews"))
        output.append(AspectStatistic(aspect=key, aspect_label=ASPECTS[key], rank=rank, metrics=metrics))
    return output


def summary_metrics(row, sentiment):
    if sentiment == "positive":
        return [Metric(name="positive_review_count", value=row["positive_review_count"]),
                rate_metric("positive_success_rate", row["positive_review_count"], row["successful_reviews"], row["positive_rate"], "successful_reviews")]
    return [Metric(name="negative_review_count", value=row["negative_review_count"]),
            rate_metric("negative_rate", row["negative_review_count"], row["successful_reviews"], row["negative_rate"], "successful_reviews")]


def adapt_result(item, bound_scope):
    """完整模型保留所有时间桶/商品/窗口，公开边界在压缩层单独构造。"""
    data, kind = item["data"], item["kind"]
    scope = deepcopy(data.get("scope", bound_scope))
    sentiment = "positive" if kind == "positive_aspects" else (
        data.get("sentiment") if kind in ("evidence", "opinion_breakdown") else "negative" if kind not in ("ratings", "quality", "context") else None)
    result = StructuredAnalysisResult(result_id=item["result_id"], kind=kind, analysis_type=kind,
                                      sentiment=sentiment, scope=scope)
    def group(name, row, actual_scope=None, aspects=False):
        return StatisticGroup(group_id=name, scope=deepcopy(actual_scope or scope), sample_size=sample_size(row),
                              metrics=summary_metrics(row, sentiment or "negative") if kind not in ("quality", "opinion_breakdown") else [],
                              aspects=aspect_statistics(row, sentiment or "negative") if aspects else [])

    if kind in ("positive_aspects", "trend", "aspect_trend", "quality", "context"):
        row = data["summary"]
        result.statistics = [group("current", row, aspects=kind == "positive_aspects")]
        if kind == "positive_aspects" and data["data_status"] != "ok":
            result.warnings.append(data["data_status"])
        if kind == "aspect_trend":
            # 样本背景保留；整体负面占比不能代替用户指定维度的趋势。
            result.statistics[0].metrics = []
        if kind == "quality":
            result.statistics[0].metrics = [
                rate_metric("missing_text_rate", row["empty_text_reviews"], row["review_count"], data["missing_text_rate"], "review_count"),
                rate_metric("failed_rate", row["failed_reviews"], row["eligible_text_reviews"], data["failed_rate"], "eligible_text_reviews")]
        if kind in ("trend", "aspect_trend"):
            for aspect in (list(data["aspects"]) if kind == "aspect_trend" else [None]):
                points = []
                for bucket in data["buckets"]:
                    counts = bucket["aspects"][aspect] if aspect else bucket
                    points.append(TrendPoint(bucket=bucket["bucket"], sample_size=sample_size(bucket), metric=rate_metric(
                        "negative_rate", counts["negative_review_count"], bucket["successful_reviews"], counts["negative_rate"], "successful_reviews")))
                result.statistics[0].trends.append(TrendSeries(aspect=aspect, period=data["period"], points=points))
    elif kind == "comparison":
        for row in data["products"]:
            result.statistics.append(group(row["product_id"], row, {**scope, "product_ids": [row["product_id"]]}, aspects=True))
        for product_id in data.get("no_reviews_product_ids", []):
            result.statistics.append(StatisticGroup(group_id=product_id, scope={**scope, "product_ids": [product_id]}))
            result.warnings.append("no_reviews:" + product_id)
    elif kind in ("issues", "window_comparison"):
        result.statistics = [group("current", data["current"], aspects=True)]
        if data.get('baseline'):
            result.statistics.append(group("baseline", data["baseline"], data["baseline_scope"], aspects=True))
        changes = {row["aspect"]: row for row in data["issues"]}
        for aspect in result.statistics[0].aspects:
            change = changes.get(aspect.aspect)
            if change:
                aspect.metrics.append(Metric(name="negative_rate_change_pp", value=change["negative_rate_change_pp"], unit="percentage_points"))
        if kind == "window_comparison":
            result.statistics[0].metrics.extend(Metric(name=key, value=value, unit="percentage_points" if key.endswith("_pp") else "reviews")
                                                 for key, value in data["changes"].items())
    elif kind == "ratings":
        for row in data["groups"]:
            group_scope = {**scope, "product_ids": [row["group"]]} if data["group_by"] == "product" else scope
            metrics = [Metric(name=name, value=row[name]) for name in ("review_count", "rated_reviews", "missing_rating_reviews", "other_rating_reviews")]
            metrics.extend(rate_metric(f"star_{star['star']}", star["review_count"], row["rated_reviews"], star["rate"], "rated_reviews") for star in row["stars"])
            result.statistics.append(StatisticGroup(group_id=row["group"], scope=deepcopy(group_scope), metrics=metrics))
    elif kind in ("evidence", "opinion_breakdown"):
        result.filters = {key: data.get(key) for key in ("aspect", "sentiment", "page", "page_size", "pages", "total")}
        if kind == 'opinion_breakdown':
            result.filters.update({key: deepcopy(data[key]) for key in ('groups', 'grouping', 'total_opinions', 'topic')})
            result.statistics = [group('current', data['summary'])]
            result.statistics[0].metrics = []
        result.filters["window"] = item["parameters"].get("window", "current")
        for row in data["items"]:
            result.evidence_refs.append(EvidenceReference(
                source_result_id=item["result_id"], **{key: row[key] for key in (
                    "review_id", "product_id", "published_at", "evidence_ref", "opinion_source")},
                opinions=[EvidenceQuote(**{key: opinion[key] for key in ("aspect", "sentiment", "quote", "quote_start", "quote_end")})
                          for opinion in row["opinions"]]))
    else:
        raise ValueError("无法适配的分析结果类型。")
    return result
