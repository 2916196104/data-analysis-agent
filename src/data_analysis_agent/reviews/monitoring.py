"""Snapshot-based analysis, monthly historical replay and deterministic rule alerts."""
import uuid
from contextlib import closing
from datetime import timedelta
from decimal import Decimal

from data_analysis_agent.core.clock import utc_now
from data_analysis_agent.core.database import DATABASE_ERRORS
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.core.validation import validate_integer
from data_analysis_agent.reviews.analytics import compare_products, get_evidence, get_top_issues, get_trend
from data_analysis_agent.reviews.monitor_store import (
    attach_alert_occurrence, check_monitoring_schema, read_analysis_run,
)
from data_analysis_agent.reviews.parameters import DEFAULT_RULES, parse_month, shift_month, validate_rules
from data_analysis_agent.reviews.scope import analysis_scope, current_reviews_sql
from data_analysis_agent.reviews.store import connect_review_database

ENGINE_VERSION = "review-monitor-v1"


def _inputs(connection, identity, windows):
    """Freeze all contributing inputs, including failed/unlabeled reviews and empty human snapshots."""
    cte, parameters = current_reviews_sql(connection, identity)
    where = " OR ".join("(" + window[0] + ")" for window in windows)
    filters = [value for window in windows for value in window[1]]
    rows = [dict(r) for r in connection.execute(cte + """
        SELECT c.id review_id,c.source,c.dataset_version,c.product_id,c.published_at,
            c.text,c.has_text,c.content_sha256,c.annotation_id,c.model_version,c.revision_id,
            c.processing_status,c.human_status,c.error_code,i.file_path,i.file_sha256,c.line_number
        FROM current c JOIN import_runs i ON i.id=c.import_run_id WHERE """ + where + " ORDER BY c.id",
        (*parameters, *filters))]
    by_id = {row["review_id"]: row for row in rows}
    for row in rows:
        row["opinions"] = []
        row["evidence_ref"] = f"sha256:{row['file_sha256']}#line={row['line_number']}"
    for row in connection.execute(
            cte + "SELECT o.* FROM current c JOIN effective_opinions o ON o.review_id=c.id WHERE "
            + where + " ORDER BY o.review_id,o.quote_start,o.quote_end,o.aspect,o.sentiment",
            (*parameters, *filters)):
        by_id[row["review_id"]]["opinions"].append({k: row[k] for k in row.keys() if k != "review_id"})
    return rows


def _evaluate(top, issue, rules):
    current, baseline = top["current"], top["baseline"]
    coverage = Decimal(str(rules["min_coverage"]))
    checks = {}
    for name, summary in (("current", current), ("baseline", baseline)):
        checks[name + "_samples"] = summary["successful_reviews"] >= rules["min_samples"]
        checks[name + "_coverage"] = (summary["eligible_text_reviews"] > 0
                                     and summary["successful_reviews"] >= coverage * summary["eligible_text_reviews"])
    sufficient = all(checks.values())
    checks["negative_count"] = issue["negative_review_count"] >= rules["min_negative_count"]
    checks["rate_increase"] = False
    if current["successful_reviews"] and baseline["successful_reviews"]:
        # Compare exact counts, not the tools' six-decimal display rates.
        increase = (Decimal(issue["negative_review_count"]) / current["successful_reviews"]
                    - Decimal(issue["baseline_negative_review_count"]) / baseline["successful_reviews"]) * 100
        checks["rate_increase"] = increase >= Decimal(str(rules["min_increase_pp"]))
    status = "insufficient_data" if not sufficient else ("triggered" if all(checks.values()) else "normal")
    return {"aspect": issue["aspect"], "status": status, "checks": checks,
            "reasons": [name for name, passed in checks.items() if not passed],
            "issue": issue, "current": current, "baseline": baseline}


def _receipt(run, reused):
    hits = run["alert_occurrences"]
    return {"run_id": run["run_id"], "reused": reused, "as_of": run["as_of"],
            "request": run["request"], "summary": run["results"]["summary"],
            "new_alerts": 0 if reused else sum(hit["is_new_alert"] for hit in hits),
            "merged_alerts": 0 if reused else sum(not hit["is_new_alert"] for hit in hits), "alert_occurrences": hits}


def run_analysis(db_path, *, model, base_url, prompt_version, date_from, date_to,
                 baseline_from, baseline_to, product_ids=None, source=None, dataset_version=None,
                 as_of=None, rules=None):
    """Atomically save one analysis and its alerts; identical scoped inputs reuse the saved run."""
    rules = validate_rules(rules)
    common = dict(model=model, base_url=base_url, prompt_version=prompt_version, product_ids=product_ids,
                  source=source, dataset_version=dataset_version, as_of=as_of if as_of is not None else date_to)
    identity, where, filters, scope = analysis_scope(date_from=date_from, date_to=date_to, **common)
    _, before_where, before_filters, baseline_scope = analysis_scope(
        date_from=baseline_from, date_to=baseline_to, **common)
    if baseline_to >= date_from:
        raise ReviewDataError("基线结束日期必须早于当前窗口开始日期。")
    cutoff = scope["as_of"]
    event_time = cutoff if "T" in cutoff else cutoff + "T23:59:59.999+00:00"
    common = {key: scope[key] for key in (
        "model", "base_url", "prompt_version", "product_ids", "source", "dataset_version", "as_of")}
    request = {"engine_version": ENGINE_VERSION, "scope": scope, "baseline_scope": baseline_scope, "rules": rules,
               "snapshot_policy": "current_labels_frozen_at_run; publication_cutoff"}
    with closing(connect_review_database(db_path)) as connection, connection:
        # One short write transaction serializes retries and keeps every tool on the same snapshot.
        connection.execute("BEGIN IMMEDIATE")
        check_monitoring_schema(connection)
        inputs = _inputs(connection, identity, ((where, filters), (before_where, before_filters)))
        key = content_sha256({"request": request, "inputs": inputs})
        cached = connection.execute("SELECT run_id FROM review_analysis_runs WHERE run_key=?", (key,)).fetchone()
        if cached:
            return _receipt(read_analysis_run(connection, cached["run_id"]), reused=True)
        current_args = {**common, "date_from": date_from, "date_to": date_to}
        baseline_args = {**common, "date_from": baseline_from, "date_to": baseline_to}
        trend = get_trend(connection, **current_args, period="month")
        comparison = compare_products(connection, **current_args)
        baseline_comparison = compare_products(connection, **baseline_args)
        products = {tuple(row[k] for k in ("source", "dataset_version", "product_id"))
                    for row in comparison["products"] + baseline_comparison["products"]}
        results = {"trend": trend, "comparison": comparison, "baseline_comparison": baseline_comparison,
                   "products": [], "summary": {"input_reviews": len(inputs), "products": len(products),
                                              "triggered": 0, "normal": 0, "insufficient_data": 0}}
        for src, version, pid in sorted(products):
            product = {"source": src, "dataset_version": version, "product_id": pid}
            arguments = {**current_args, "source": src, "dataset_version": version, "product_ids": [pid]}
            top = get_top_issues(connection, **arguments, baseline_from=baseline_from, baseline_to=baseline_to)
            evaluations = []
            for issue in top["issues"]:
                evaluation = _evaluate(top, issue, rules)
                if evaluation["status"] == "triggered":
                    evidence = get_evidence(connection, **arguments, aspect=issue["aspect"], page_size=20)
                    if evidence["total"] != issue["negative_review_count"]:
                        raise ReviewDataError("告警统计与证据数量不一致，分析未保存。")
                    evaluation["evidence"] = evidence
                results["summary"][evaluation["status"]] += 1
                evaluations.append(evaluation)
            results["products"].append({"product": product, "top_issues": top, "evaluations": evaluations})
        run_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO review_analysis_runs(run_id,run_key,created_at,as_of,request_json,inputs_json,results_json) "
            "VALUES (?,?,?,?,?,?,?)", (run_id, key, utc_now(), event_time, canonical_json(request), canonical_json(inputs), canonical_json(results)))
        for item in results["products"]:
            for evaluation in item["evaluations"]:
                if evaluation["status"] == "triggered":
                    attach_alert_occurrence(connection, run_id=run_id, as_of=event_time, request=request,
                                  product=item["product"], evaluation=evaluation)
        return _receipt(read_analysis_run(connection, run_id), reused=False)


def replay_monthly(db_path, *, month_from, month_to, baseline_months=1, model, base_url, prompt_version,
                   product_ids=None, source=None, dataset_version=None, rules=None, progress=None):
    """Advance through whole UTC months; each month commits separately and can be resumed."""
    start, end = parse_month(month_from, "month_from"), parse_month(month_to, "month_to")
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    if not 1 <= count <= 1200:
        raise ReviewDataError("回放起止月份须有序，且最多包含 1200 个月。")
    validate_integer(baseline_months, "baseline_months", maximum=120)
    rules = validate_rules(rules)
    shift_month(start, -baseline_months)
    shift_month(end, 1)
    # Validate the shared configuration before any month is committed.
    analysis_scope(model=model, base_url=base_url, prompt_version=prompt_version,
           date_from=start.isoformat(), date_to=end.isoformat(), product_ids=product_ids,
           source=source, dataset_version=dataset_version)
    runs = []
    for index in range(count):
        current = shift_month(start, index)
        last = shift_month(current, 1) - timedelta(days=1)
        if progress:
            progress({"stage": "replaying", "completed": index, "total": count, "unit": "months",
                      "month": current.isoformat()[:7]})
        try:
            result = run_analysis(
                db_path, model=model, base_url=base_url, prompt_version=prompt_version,
                date_from=current.isoformat(), date_to=last.isoformat(), as_of=last.isoformat(),
                baseline_from=shift_month(current, -baseline_months).isoformat(),
                baseline_to=(current - timedelta(days=1)).isoformat(), product_ids=product_ids,
                source=source, dataset_version=dataset_version, rules=rules)
        except (ReviewDataError, *DATABASE_ERRORS, OSError) as exc:
            return {"complete": False, "month_from": month_from, "month_to": month_to,
                    "baseline_months": baseline_months, "runs": runs, "failed_month": current.isoformat()[:7],
                    "error": str(exc)}
        runs.append({"month": current.isoformat()[:7], **result})
        if progress:
            progress({"stage": "replaying", "completed": index + 1, "total": count, "unit": "months",
                      "month": current.isoformat()[:7], "run_id": result["run_id"]})
    return {"complete": True, "month_from": month_from, "month_to": month_to,
            "baseline_months": baseline_months, "runs": runs}
