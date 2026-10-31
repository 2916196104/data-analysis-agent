"""确定性压缩与公开白名单；完整快照仍由原任务结果保存。"""

from copy import deepcopy
from decimal import Decimal
from typing import Literal

from pydantic import Field

from data_analysis_agent.agents.review_plan import ASPECT_WORDS, TOOL_KINDS, ReviewQueryPlan
from data_analysis_agent.agents.review_results import (
    AnalysisModel, EvidenceReference, StructuredAnalysisResult,
    TrendPoint, TrendSeries, adapt_result,
)
from data_analysis_agent.core.serialization import canonical_json, content_sha256


class CompressionOptions(AnalysisModel):
    top_aspects: int = Field(default=3, ge=1, le=6)
    max_trend_points: int = Field(default=24, ge=3, le=366)
    max_evidence_items: int = Field(default=10, ge=0, le=10)
    quote_chars: int = Field(default=240, ge=1, le=2000)
    quotes_per_review: int = Field(default=2, ge=1, le=30)
    anomaly_change_pp: float = Field(default=20.0, gt=0, le=100)


class CompactAnalysisContext(AnalysisModel):
    schema_version: str = "1.0"
    question: str
    analysis_type: str
    sentiment: str | None
    scope_summary: list[dict]
    sample_summary: list[dict]
    key_findings: list[dict]
    statistics: list[dict]
    evidence: list[EvidenceReference]
    caveats: list[dict]
    source_result_ids: list[str]


class PublicAnalysis(AnalysisModel):
    schema_version: str = "1.0"
    status: Literal["ok", "unsupported", "failed", "partial", "queued", "running", "error"]
    scope_summary: list[dict] = Field(default_factory=list)
    summary_metrics: list[dict] = Field(default_factory=list)
    highlights: list[dict] = Field(default_factory=list)
    statistics: list[dict] = Field(default_factory=list)
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    caveats: list[dict] = Field(default_factory=list)
    source_result_ids: list[str] = Field(default_factory=list)
    details_available: bool = False


def public_scope(scope):
    # 标注身份只给稳定指纹引用；原始模型/端点/提示词版本留在审计快照。
    output = {key: deepcopy(scope[key]) for key in (
        "source", "dataset_version", "product_ids", "date_from", "date_to", "baseline_from", "baseline_to", "as_of", "timezone"
    ) if key in scope}
    identity = [scope.get(key) for key in ("model", "base_url", "prompt_version")]
    if any(value is not None for value in identity):
        output["annotation_profile_ref"] = "profile:" + content_sha256(identity)[:16]
    return output


def _delta(a: TrendPoint, b: TrendPoint):
    if not a.metric.denominator or not b.metric.denominator:
        return None
    return float(round((Decimal(b.metric.numerator) / b.metric.denominator
                        - Decimal(a.metric.numerator) / a.metric.denominator) * 100, 6))


def _metric_payload(metric):
    # 空 value 是业务含义；不适用的元数据才可以省略。
    return {key: value for key, value in metric.model_dump().items() if value is not None or key == "value"}


def _sample_payload(sample):
    return {key: value for key, value in sample.model_dump().items() if value is not None or key == "labeling_coverage"}


def compress_trend(series: TrendSeries, options: CompressionOptions):
    points = series.points
    valid = [i for i, point in enumerate(points) if point.metric.value is not None]
    mandatory = {0, len(points) - 1} if points else set()
    changes = [(i, _delta(points[i - 1], points[i])) for i in range(1, len(points))]
    changes = [(i, value) for i, value in changes if value is not None]
    findings = {"aspect": series.aspect, "period": series.period, "bucket_count": len(points),
                "empty_buckets": [point.bucket for point in points if point.metric.value is None]}
    findings["valid_bucket_count"] = len(valid)
    findings["sufficient_bucket_count"] = sum(points[i].sample_size.successful_reviews >= 20
        and (points[i].sample_size.labeling_coverage or 0) >= 0.8 for i in valid)
    findings["adjacent_valid_pair_count"] = len(changes)
    if changes:
        recent_i, recent = changes[-1]
        mandatory.update((recent_i - 1, recent_i))
        findings["recent_adjacent_change"] = {"from": points[recent_i - 1].bucket,
                                              "to": points[recent_i].bucket, "change_pp": recent}
    if valid:
        peak = max(valid, key=lambda i: points[i].metric.value)
        trough = min(valid, key=lambda i: points[i].metric.value)
        count_peak = max(valid, key=lambda i: points[i].metric.numerator)
        mandatory.update((valid[0], valid[-1], peak, trough, count_peak))
        findings.update(peak_bucket=points[peak].bucket, trough_bucket=points[trough].bucket,
                        count_peak_bucket=points[count_peak].bucket,
                        first_valid_bucket=points[valid[0]].bucket, last_valid_bucket=points[valid[-1]].bucket,
                        first_last_change_pp=_delta(points[valid[0]], points[valid[-1]]))
    else:
        findings["first_last_change_pp"] = None
    signs = {1 if value > 0 else -1 if value < 0 else 0 for _, value in changes} - {0}
    findings["direction"] = "insufficient_data" if not changes else (
        "fluctuating" if len(signs) > 1 else "up" if signs == {1} else "down" if signs == {-1} else "flat")
    findings["large_adjacent_changes"] = []
    if changes:
        largest_i, largest = max(changes, key=lambda pair: abs(pair[1]))
        mandatory.update((largest_i - 1, largest_i))
        findings["largest_adjacent_change"] = {"from": points[largest_i - 1].bucket, "to": points[largest_i].bucket, "change_pp": largest}
    for i, value in changes:
        if abs(value) >= options.anomaly_change_pp:
            mandatory.update((i - 1, i))
            findings["large_adjacent_changes"].append({"from": points[i - 1].bucket, "to": points[i].bucket, "change_pp": value})
    # 保留缺失边界与所有局部转折，宁可超出目标点数，也不把中间波动压成首尾直线。
    for i in range(1, len(points)):
        if (points[i - 1].metric.value is None) != (points[i].metric.value is None):
            mandatory.update((i - 1, i))
    previous_change = None
    for i in range(1, len(points)):
        change = _delta(points[i - 1], points[i])
        if change is None:
            previous_change = None
        elif change != 0:
            if previous_change is not None and previous_change[1] * change < 0:
                # 平台峰/谷的首尾也需保留，连续相等的桶不能掩盖转折。
                mandatory.update((previous_change[0], i - 1))
            previous_change = (i, change)
    if len(points) <= options.max_trend_points:
        selected = set(range(len(points)))
    else:
        selected = set(mandatory)
        for i in range(options.max_trend_points):
            if len(selected) >= options.max_trend_points:
                break
            selected.add(round(i * (len(points) - 1) / (options.max_trend_points - 1)))
    compressed = {"aspect": series.aspect, "period": series.period, "original_point_count": len(points),
                  "omitted_point_count": len(points) - len(selected), "target_exceeded": len(selected) > options.max_trend_points,
                  "points": [{"bucket": points[i].bucket, "sample_size": _sample_payload(points[i].sample_size),
                              "metric": _metric_payload(points[i].metric)} for i in sorted(selected)]}
    return compressed, findings


class AnalysisResultCompressor:
    def __init__(self, options: CompressionOptions | None = None):
        self.options = options or CompressionOptions()

    def compress(self, question, plan, results: list[StructuredAnalysisResult]):
        scopes, samples, statistics, findings, caveats, evidence, source_ids = {}, {}, [], [], [], [], []
        evidence_budget = min(self.options.max_evidence_items, plan.max_evidence_items) if plan.requires_evidence else 0
        for result in results:
            source_ids.append(result.result_id)
            scope = public_scope(result.scope)
            scope_key = canonical_json(scope)
            scope_ref = scopes.setdefault(scope_key, {"scope_ref": "scope:" + content_sha256(scope)[:16], **scope})["scope_ref"]
            for group in result.statistics:
                actual_scope = public_scope(group.scope)
                key = canonical_json(actual_scope)
                group_scope_ref = scopes.setdefault(key, {"scope_ref": "scope:" + content_sha256(actual_scope)[:16], **actual_scope})["scope_ref"]
                sample_ref = None
                if group.sample_size:
                    sample = _sample_payload(group.sample_size)
                    # 正向总数是同一背景的附加指标；缺少该字段不应重复整份样本。
                    sample_identity = {key: value for key, value in sample.items() if key != "positive_review_count"}
                    sample_key = canonical_json([group_scope_ref, sample_identity])
                    existing = samples.get(sample_key, {})
                    if "positive_review_count" in sample and existing.get("positive_review_count", sample["positive_review_count"]) != sample["positive_review_count"]:
                        sample_identity = sample
                        sample_key = canonical_json([group_scope_ref, sample_identity])
                    entry = samples.setdefault(sample_key, {"sample_ref": "sample:" + content_sha256([group_scope_ref, sample_identity])[:16],
                                                           "scope_ref": group_scope_ref, **sample})
                    if "positive_review_count" in sample:
                        entry["positive_review_count"] = sample["positive_review_count"]
                    sample_ref = entry["sample_ref"]
                    codes = []
                    if group.sample_size.successful_reviews == 0:
                        codes.append("no_successful_annotations")
                    elif group.sample_size.successful_reviews < 20:
                        codes.append("small_sample")
                    if group.sample_size.labeling_coverage is None or group.sample_size.labeling_coverage < 0.8:
                        codes.append("insufficient_coverage")
                    if result.kind == "positive_aspects" and not group.sample_size.positive_review_count:
                        codes.append("no_positive_mentions")
                    caveats.extend({"code": code, "scope_ref": group_scope_ref, "sample_ref": sample_ref} for code in codes)
                requested = {value for word, value in ASPECT_WORDS.items() if word in question.replace("数据质量", "")}
                candidates = [row for row in group.aspects if not requested or row.aspect in requested]
                mentioned = [row for row in candidates if row.metrics[0].value > 0]
                rows = [{**row.model_dump(), "metrics": [_metric_payload(metric) for metric in row.metrics]}
                        for row in (mentioned or candidates)[:self.options.top_aspects]]
                trends = []
                for series in group.trends:
                    compressed, trend_finding = compress_trend(series, self.options)
                    trends.append(compressed)
                    findings.append({"source_result_id": result.result_id, "scope_ref": group_scope_ref, "type": "trend", **trend_finding})
                    if trend_finding["empty_buckets"]:
                        caveats.append({"code": "missing_time_buckets", "scope_ref": group_scope_ref})
                    if any(point.sample_size.successful_reviews < 20 or (point.sample_size.labeling_coverage or 0) < 0.8 for point in series.points):
                        caveats.append({"code": "limited_bucket_samples", "scope_ref": group_scope_ref})
                    caveats.append({"code": "boundary_buckets_may_be_partial", "scope_ref": group_scope_ref})
                    if compressed["target_exceeded"]:
                        caveats.append({"code": "trend_target_exceeded_to_preserve_changes", "scope_ref": group_scope_ref})
                statistics.append({"source_result_id": result.result_id, "analysis_type": result.analysis_type,
                                   "sentiment": result.sentiment, "group_id": group.group_id,
                                   "scope_ref": group_scope_ref, "sample_ref": sample_ref,
                                   "metrics": [_metric_payload(metric) for metric in group.metrics], "aspects": rows, "trends": trends})
                if mentioned:
                    findings.append({"source_result_id": result.result_id, "scope_ref": group_scope_ref, "type": "aspect_ranking",
                                     "aspects": [row["aspect"] for row in rows], "sentiment": result.sentiment})
                if group.sample_size is None and not group.metrics:
                    caveats.append({"code": "no_reviews", "scope_ref": group_scope_ref})
            for ref in result.evidence_refs:
                if len(evidence) >= evidence_budget:
                    break
                compact_ref = ref.model_copy(deep=True)
                compact_ref.opinions = compact_ref.opinions[:self.options.quotes_per_review]
                if len(compact_ref.opinions) < len(ref.opinions):
                    caveats.append({"code": "evidence_quotes_omitted", "scope_ref": scope_ref})
                for quote in compact_ref.opinions:
                    if len(quote.quote) > self.options.quote_chars:
                        quote.quote = quote.quote[:self.options.quote_chars]
                        quote.quote_end = quote.quote_start + len(quote.quote)
                        quote.truncated = True
                        caveats.append({"code": "evidence_quote_truncated", "scope_ref": scope_ref})
                evidence.append(compact_ref)
            if result.kind in ("evidence", "opinion_breakdown"):
                if result.kind == 'opinion_breakdown':
                    statistics = [row for row in statistics if row['source_result_id'] != result.result_id]
                statistics.append({"source_result_id": result.result_id, "analysis_type": result.kind, "scope_ref": scope_ref,
                                   "filters": deepcopy(result.filters)})
                caveats.append({"code": "evidence_is_matching_sample_not_population", "scope_ref": scope_ref})
        # 背景样本只存一次；结果/窗口/商品仍保留独立的 scope_ref 与来源，不相加。
        unique_caveats = {canonical_json(item): item for item in caveats}
        for finding in findings:
            finding["finding_ref"] = "finding:" + content_sha256(finding)[:20]
        for stat in statistics:
            stat["finding_ref"] = "finding:" + content_sha256([stat["source_result_id"], stat["scope_ref"],
                stat.get("group_id"), stat["analysis_type"]])[:20]
            for aspect in stat.get("aspects", []):
                aspect["finding_ref"] = "finding:" + content_sha256([stat["finding_ref"], aspect["aspect"]])[:20]
        return CompactAnalysisContext(question=question, analysis_type=plan.analysis_type, sentiment=plan.sentiment,
                                      scope_summary=list(scopes.values()), sample_summary=list(samples.values()),
                                      key_findings=findings, statistics=statistics, evidence=evidence,
                                      caveats=list(unique_caveats.values()), source_result_ids=list(dict.fromkeys(source_ids)))


def public_from_context(context):
    return PublicAnalysis(status="ok", scope_summary=context.scope_summary, summary_metrics=context.sample_summary,
                          highlights=context.key_findings, statistics=context.statistics, evidence_refs=context.evidence,
                          caveats=context.caveats, source_result_ids=context.source_result_ids, details_available=True)


def empty_public(status):
    return PublicAnalysis(status=status, caveats=[{"code": "analysis_not_supported" if status == "unsupported" else "analysis_not_complete"}])


def build_analysis_artifacts(result, plan, options=None):
    if result.get("status") != "ok" or result.get("validation", {}).get("status") != "passed":
        raise ValueError("只有已校验成功的完整结果可以压缩。")
    selected = [result["results"][key] for key in result["result_ids"] if key != "overview"]
    relevant = [item for item in selected if any(
        item["kind"] == TOOL_KINDS[request["tool"]] and all(item["parameters"].get(key) == value
        for key, value in request["parameters"].items()) for request in plan.requirements
    ) or item["kind"] in {TOOL_KINDS[name] for name in plan.optional_tools}]
    if not plan.satisfied(relevant):
        raise ValueError("完整结果未满足查询计划。")
    structured = [adapt_result(item, result["scope"]) for item in relevant]
    context = AnalysisResultCompressor(options).compress(result.get("analysis_question", result["question"]), plan, structured)
    return {"structured_analysis": [item.model_dump() for item in structured],
            "compact_context": context.model_dump(), "public_analysis": public_from_context(context).model_dump()}


def read_public_analysis(task, options=None):
    """普通读取入口不信任持久化 public 字段，重新从数值快照白名单适配。"""
    result = task.get("result") or {}
    if task.get("status") != "succeeded" or task.get("error"):
        status = task.get("status", "failed")
        return empty_public("failed" if status == "succeeded" else status).model_dump()
    if not isinstance(result, dict):
        return empty_public("failed").model_dump()
    if result.get("status") == "unsupported":
        return empty_public("unsupported").model_dump()
    if result.get("status") != "ok" or result.get("validation", {}).get("status") == "failed":
        return empty_public("failed").model_dump()
    from data_analysis_agent.agents.review_validation import validate_report

    try:
        # 旧任务没有新模型时，依据实际已保存工具引用转换，不重新查询/不重新解释问题。
        inverse = {kind: tool for tool, kind in TOOL_KINDS.items()}
        requests = [{"tool": inverse[result["results"][key]["kind"]], "parameters": result["results"][key]["parameters"]}
                    for key in result["result_ids"] if key != "overview"]
        plan = ReviewQueryPlan(analysis_type=result.get("analysis_intent") or "composite",
                               sentiment=result.get("sentiment_intent"), scope=result["scope"], requirements=requests,
                               requires_evidence=any(item["tool"] in {"get_evidence", "get_opinion_breakdown"} for item in requests), max_evidence_items=10)
        validation = validate_report(result["results"], result["result_ids"],
                                     sentiment_intent=result.get("sentiment_intent"), query_plan=plan)
        if validation["status"] != "passed":
            return empty_public("failed").model_dump()
        checked = {**result, "validation": validation}
        return build_analysis_artifacts(checked, plan, options)["public_analysis"]
    except (ValueError, KeyError, TypeError, IndexError, ArithmeticError):
        # 老结构无法可靠转换时只关闭新公开视图，不损坏历史原始结果和导出。
        return empty_public("failed").model_dump()
