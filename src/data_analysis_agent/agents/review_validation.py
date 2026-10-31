"""复核已执行查询的数字和证据；不使用模型判断计算是否正确。"""

import math


def result_summaries(results):
    for item in results:
        data = item["data"]
        if item["kind"] in ("context", "trend", "quality", "aspect_trend", "positive_aspects", "opinion_breakdown"):
            yield data["summary"]
            yield from data.get("buckets", [])
        elif item["kind"] == "comparison":
            yield from data["products"]
        elif item["kind"] in ("issues", "window_comparison"):
            yield data["current"]
            if data.get("baseline"):
                yield data["baseline"]


def _matches_rate(value, count, denominator, scale=1):
    if denominator == 0:
        return value is None
    return (type(value) in (int, float) and math.isfinite(value)
            and abs(value - count / denominator * scale) <= 0.00000051)


def _scope_matches(actual, expected):
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    for key in ("model", "base_url", "prompt_version", "source", "dataset_version",
                "date_from", "date_to", "as_of"):
        if key in expected and actual.get(key) != expected.get(key):
            return False
    if "product_ids" in expected:
        return sorted(actual.get("product_ids", [])) == sorted(expected.get("product_ids", []))
    return True


def _validate_positive_aspects(data):
    summary = data.get("summary", {})
    rows = data.get("aspects", [])
    if not isinstance(rows, list) or not isinstance(summary.get("aspects"), dict):
        return False
    positive_total = summary.get("positive_review_count")
    successful = summary.get("successful_reviews")
    if type(positive_total) is not int or positive_total < 0 or type(successful) is not int or successful < 0:
        return False
    expected_order = sorted(
        summary["aspects"],
        key=lambda aspect: (-summary["aspects"][aspect].get("positive_review_count", -1), aspect),
    )
    if [row.get("aspect") for row in rows] != expected_order:
        return False
    expected_status = "insufficient_data" if successful == 0 else (
        "no_positive_mentions" if positive_total == 0 else "ok")
    if data.get("data_status") != expected_status or data.get("analysis_type") != "positive_aspects":
        return False
    if data.get("sentiment") != "positive":
        return False
    for rank, row in enumerate(rows, 1):
        aspect = row.get("aspect")
        source = summary["aspects"].get(aspect)
        if not isinstance(source, dict) or row.get("rank") != rank:
            return False
        count = source.get("positive_review_count")
        if row.get("positive_review_count") != count or type(count) is not int or count < 0 or count > successful:
            return False
        if row.get("positive_rate_denominator") != positive_total:
            return False
        if row.get("positive_success_rate_denominator") != successful:
            return False
        if not _matches_rate(row.get("positive_rate"), count, positive_total):
            return False
        if not _matches_rate(row.get("positive_success_rate"), count, successful):
            return False
    return True


def validate_report(results, result_ids, *, sentiment_intent=None, expected_scope=None,
                    query_plan=None, expected_scopes=None):
    """校验快照中所有统计行，零分母必须保留 null；失败时禁止发布成功回答。"""
    sentiment_intent = {"positive_aspects": "positive", "negative_aspects": "negative"}.get(
        sentiment_intent, sentiment_intent,
    )
    rows = list(result_summaries(results.values()))
    counts_ok, rates_ok, coverage_ok, scope_ok, positive_ok = True, True, True, True, True
    for row in rows:
        counts = [row[key] for key in ("review_count", "eligible_text_reviews", "successful_reviews",
                                      "failed_reviews", "unprocessed_reviews", "negative_review_count")]
        valid = all(type(value) is int and value >= 0 for value in counts)
        counts_ok &= valid
        if not valid:
            rates_ok = coverage_ok = False
            continue
        total, eligible, success, failed, pending, negative = counts
        counts_ok &= negative <= success <= eligible <= total and success + failed + pending == eligible
        rates_ok &= _matches_rate(row["negative_rate"], negative, success)
        coverage_ok &= _matches_rate(row["labeling_coverage"], success, eligible)
        if "positive_review_count" in row:
            positive = row["positive_review_count"]
            counts_ok &= type(positive) is int and 0 <= positive <= success
            rates_ok &= _matches_rate(row.get("positive_rate"), positive, success)
        for aspect in row.get("aspects", {}).values():
            count = aspect["negative_review_count"]
            counts_ok &= type(count) is int and 0 <= count <= success
            rates_ok &= _matches_rate(aspect["negative_rate"], count, success)
            if "positive_review_count" in aspect:
                positive_count = aspect["positive_review_count"]
                positive_total = row.get("positive_review_count", 0)
                counts_ok &= type(positive_count) is int and 0 <= positive_count <= success
                rates_ok &= _matches_rate(aspect.get("positive_rate"), positive_count, positive_total)
                rates_ok &= _matches_rate(aspect.get("positive_success_rate"), positive_count, success)

    quotes_ok, quote_count, additional_ok = True, 0, True
    for result_id, item in results.items():
        data = item["data"]
        expected = expected_scopes.get(result_id) if expected_scopes is not None else expected_scope
        if expected is not None:
            scope_ok &= _scope_matches(data.get("scope"), expected)
        if item["kind"] == "positive_aspects":
            positive_ok &= _validate_positive_aspects(data)
        if item["kind"] in ("evidence", "opinion_breakdown"):
            sentiment = item["parameters"].get("sentiment", "all" if item['kind'] == 'opinion_breakdown' else "negative")
            aspect = item["parameters"].get("aspect")
            quotes_ok &= data.get("sentiment") == (None if sentiment == "all" else sentiment)
            for evidence in data["items"]:
                quotes_ok &= evidence["evidence_ref"] == f"sha256:{evidence['file_sha256']}#line={evidence['line_number']}"
                for opinion in evidence["opinions"]:
                    quotes_ok &= sentiment == "all" or opinion["sentiment"] == sentiment
                    quotes_ok &= aspect is None or opinion["aspect"] == aspect
                    quote_count += 1
                    start, end = opinion["quote_start"], opinion["quote_end"]
                    quotes_ok &= (type(start) is int and type(end) is int and 0 <= start < end <= len(evidence["text"])
                                  and evidence["text"][start:end] == opinion["quote"])
        elif item["kind"] in ("issues", "window_comparison"):
            current, baseline = data["current"], data.get("baseline")
            for issue in data["issues"]:
                now = current["aspects"][issue["aspect"]]
                if baseline is None:
                    rates_ok &= issue['negative_review_count'] == now['negative_review_count'] and issue['negative_rate'] == now['negative_rate']
                    rates_ok &= all(issue.get(key) is None for key in ('baseline_negative_review_count', 'baseline_negative_rate', 'negative_rate_change_pp'))
                    continue
                before = baseline["aspects"][issue["aspect"]]
                rates_ok &= (issue["negative_review_count"] == now["negative_review_count"]
                             and issue["negative_rate"] == now["negative_rate"]
                             and issue["baseline_negative_review_count"] == before["negative_review_count"]
                             and issue["baseline_negative_rate"] == before["negative_rate"])
                a, b = current["successful_reviews"], baseline["successful_reviews"]
                rates_ok &= _matches_rate(issue["negative_rate_change_pp"],
                                          now["negative_review_count"] * b - before["negative_review_count"] * a,
                                          a * b, 100)
            if item["kind"] == "window_comparison":
                for key, change in data["changes"].items():
                    if not key.endswith("_pp"):
                        additional_ok &= change == current[key] - baseline[key]
                for metric, count, denominator in (("negative_rate", "negative_review_count", "successful_reviews"),
                                                   ("labeling_coverage", "successful_reviews", "eligible_text_reviews")):
                    a, b = current[denominator], baseline[denominator]
                    additional_ok &= _matches_rate(data["changes"][metric + "_pp"],
                        current[count] * b - baseline[count] * a, a * b, 100)
        elif item["kind"] == "quality":
            row = data["summary"]
            additional_ok &= row["empty_text_reviews"] == row["review_count"] - row["eligible_text_reviews"]
            additional_ok &= _matches_rate(data["missing_text_rate"], row["empty_text_reviews"], row["review_count"])
            additional_ok &= _matches_rate(data["failed_rate"], row["failed_reviews"], row["eligible_text_reviews"])
            additional_ok &= sum(row["review_count"] for row in data["failures"]) == data["summary"]["failed_reviews"]
            additional_ok &= sum(row["review_count"] for row in data["sources"]) == data["summary"]["review_count"]
        elif item["kind"] == "ratings":
            for row in data["groups"]:
                counts = [row[key] for key in ("review_count", "rated_reviews", "missing_rating_reviews", "other_rating_reviews")]
                additional_ok &= all(type(value) is int and value >= 0 for value in counts)
                additional_ok &= row["review_count"] == sum(counts[1:])
                additional_ok &= [star["star"] for star in row["stars"]] == [1, 2, 3, 4, 5]
                additional_ok &= sum(star["review_count"] for star in row["stars"]) == row["rated_reviews"]
                for star in row["stars"]:
                    additional_ok &= type(star["review_count"]) is int and star["review_count"] >= 0
                    additional_ok &= _matches_rate(star["rate"], star["review_count"], row["rated_reviews"])

        if item['kind'] == 'opinion_breakdown':
            total, opinions = data['total'], data['total_opinions']
            additional_ok &= type(total) is int and type(opinions) is int and 0 <= total <= opinions
            additional_ok &= total <= data['summary']['successful_reviews']
            additional_ok &= data['grouping'] == 'exact_opinion_text_and_sentiment'
            groups = data['groups']
            additional_ok &= groups == sorted(groups, key=lambda row: (-row['review_count'], -row['opinion_count']))
            additional_ok &= len({(row['quote'], row['sentiment']) for row in groups}) == len(groups)
            for row in groups:
                additional_ok &= 0 < row['review_count'] <= total and row['review_count'] <= row['opinion_count'] <= opinions
                additional_ok &= _matches_rate(row['review_rate'], row['review_count'], total)
            for row in groups[:3]:
                quotes_ok &= any(evidence['review_id'] == row['review_id'] and evidence['evidence_ref'] == row.get('evidence_ref')
                    and any(opinion['quote'] == row['quote'] and opinion['sentiment'] == row['sentiment'] for opinion in evidence['opinions'])
                    for evidence in data['items'])

    refs_ok = (len(set(result_ids)) == len(result_ids) and bool(set(result_ids) - {"overview"})
               and set(result_ids) <= set(results) and set(results) - {"overview"} <= set(result_ids))
    selected = [results[result_id] for result_id in result_ids if result_id in results]
    semantic_ok = True
    if sentiment_intent == "positive":
        semantic_ok = any(item["kind"] == "positive_aspects" or (
            item["kind"] in {"evidence", "opinion_breakdown"} and item["data"].get("sentiment") == "positive"
        ) for item in selected)
    elif sentiment_intent == "negative":
        semantic_ok = any(item["kind"] in {"trend", "aspect_trend", "comparison", "issues", "window_comparison"}
                          or (item["kind"] in {"evidence", "opinion_breakdown"} and item["data"].get("sentiment") == "negative")
                          for item in selected)
    if query_plan is not None:
        semantic_ok &= query_plan.satisfied(selected)
    missing = sum(row["successful_reviews"] == 0 for row in rows)
    checks = [
        {"name": "数字与分母", "passed": bool(counts_ok and rates_ok),
         "detail": f"复核 {len(rows)} 行统计的计数及占比；{missing} 行无成功标注，保留空占比"},
        {"name": "覆盖率", "passed": bool(coverage_ok), "detail": "按成功标注数 / 有效正文数复核，不以评论总数代替分母"},
        {"name": "原文引用", "passed": bool(quotes_ok), "detail": f"核对 {quote_count} 处引文的原文位置及文件引用"},
        {"name": "扩展统计", "passed": bool(additional_ok), "detail": "复核缺失与失败数、星级分布分母及窗口变化；空分母保留空值"},
        {"name": "正向统计", "passed": bool(positive_ok), "detail": "复核正向维度排序、去重计数、两个占比分母和零分母状态"},
        {"name": "范围身份", "passed": bool(scope_ok), "detail": "复核来源、数据集、日期、商品和标注版本与绑定范围一致"},
        {"name": "情感语义", "passed": bool(semantic_ok), "detail": "正向问题必须引用 positive_aspects；负向问题必须引用负面统计结果"},
        {"name": "查询结果完整性", "passed": refs_ok, "detail": "最终引用覆盖所有已完成查询；不代表对用户问题语义的完整性保证"},
    ]
    return {"status": "passed" if all(check["passed"] for check in checks) else "failed", "checks": checks}
