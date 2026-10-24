"""复核已执行查询的数字和证据；不使用模型判断计算是否正确。"""

import math


def result_summaries(results):
    for item in results:
        data = item["data"]
        if item["kind"] in ("context", "trend"):
            yield data["summary"]
            yield from data.get("buckets", [])
        elif item["kind"] == "comparison":
            yield from data["products"]
        elif item["kind"] == "issues":
            yield data["current"]
            yield data["baseline"]


def _matches_rate(value, count, denominator, scale=1):
    if denominator == 0:
        return value is None
    return (type(value) in (int, float) and math.isfinite(value)
            and abs(value - count / denominator * scale) <= 0.00000051)


def validate_report(results, result_ids):
    """校验快照中所有统计行，零分母必须保留 null；失败时禁止发布成功回答。"""
    rows = list(result_summaries(results.values()))
    counts_ok, rates_ok, coverage_ok = True, True, True
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
        for aspect in row.get("aspects", {}).values():
            count = aspect["negative_review_count"]
            counts_ok &= type(count) is int and 0 <= count <= success
            rates_ok &= _matches_rate(aspect["negative_rate"], count, success)

    quotes_ok, quote_count = True, 0
    for item in results.values():
        data = item["data"]
        if item["kind"] == "evidence":
            for evidence in data["items"]:
                quotes_ok &= evidence["evidence_ref"] == f"sha256:{evidence['file_sha256']}#line={evidence['line_number']}"
                for opinion in evidence["opinions"]:
                    quote_count += 1
                    start, end = opinion["quote_start"], opinion["quote_end"]
                    quotes_ok &= (type(start) is int and type(end) is int and 0 <= start < end <= len(evidence["text"])
                                  and evidence["text"][start:end] == opinion["quote"])
        elif item["kind"] == "issues":
            current, baseline = data["current"], data["baseline"]
            for issue in data["issues"]:
                now = current["aspects"][issue["aspect"]]
                before = baseline["aspects"][issue["aspect"]]
                rates_ok &= (issue["negative_review_count"] == now["negative_review_count"]
                             and issue["negative_rate"] == now["negative_rate"]
                             and issue["baseline_negative_review_count"] == before["negative_review_count"]
                             and issue["baseline_negative_rate"] == before["negative_rate"])
                a, b = current["successful_reviews"], baseline["successful_reviews"]
                rates_ok &= _matches_rate(issue["negative_rate_change_pp"],
                                          now["negative_review_count"] * b - before["negative_review_count"] * a,
                                          a * b, 100)

    refs_ok = (len(set(result_ids)) == len(result_ids) and bool(set(result_ids) - {"overview"})
               and set(result_ids) <= set(results) and set(results) - {"overview"} <= set(result_ids))
    missing = sum(row["successful_reviews"] == 0 for row in rows)
    checks = [
        {"name": "数字与分母", "passed": bool(counts_ok and rates_ok),
         "detail": f"复核 {len(rows)} 行统计的计数及占比；{missing} 行无成功标注，保留空占比"},
        {"name": "覆盖率", "passed": bool(coverage_ok), "detail": "按成功标注数 / 有效正文数复核，不以评论总数代替分母"},
        {"name": "原文引用", "passed": bool(quotes_ok), "detail": f"核对 {quote_count} 处引文的原文位置及文件引用"},
        {"name": "查询结果完整性", "passed": refs_ok, "detail": "最终引用覆盖所有已完成查询；不代表对用户问题语义的完整性保证"},
    ]
    return {"status": "passed" if all(check["passed"] for check in checks) else "failed", "checks": checks}
