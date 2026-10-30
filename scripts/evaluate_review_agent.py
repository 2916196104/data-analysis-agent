"""使用明确的合成评论，在独立 PostgreSQL 测试库评测真实评论 Agent。"""

import argparse
from contextlib import closing, redirect_stdout
from dataclasses import replace
import json
import math
import os
from pathlib import Path
from statistics import median
from time import perf_counter
from time import sleep
from urllib.parse import urlsplit, urlunsplit
import uuid
import hashlib
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from scripts.frontend_fixture import create_fixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.database import database_identity
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.human_review import workbench_options
from data_analysis_agent.reviews.store import connect_review_database


IDENTITY = dict(model="SIMULATED-ANNOTATIONS", base_url="https://offline.invalid/v1", prompt_version="fixture-v1")
SCOPE = dict(**IDENTITY, source="amazon_reviews_2023", dataset_version="SIMULATED-frontend",
             product_ids=["DEMO-A", "DEMO-B"], date_from="2023-02-01", date_to="2023-02-24",
             baseline_from="2023-01-01", baseline_to="2023-01-24")


def cases():
    """预先固定问题、工具和关键参数；不以 Agent 自报成功作为语义正确的依据。"""
    def case(name, question, *expected, status="ok", **scope):
        return dict(id=name, question=question, expected=list(expected), status=status, scope=scope)

    return [
        case("daily", "按日查看当前窗口的评论量和负面评论趋势。", ("trend", {"period": "day"})),
        case("products", "对比 DEMO-A 和 DEMO-B 的负面评论占比及覆盖率。", ("comparison", {})),
        case("issues", "查询六维度负面问题及较基线的变化。", ("issues", {})),
        case("evidence", "查询 DEMO-A 当前窗口包装负面评论的原文证据。",
             ("evidence", {"aspect": "packaging", "sentiment": "negative", "product_ids": ["DEMO-A"]})),
        case("quality", "检查缺失正文、失败标注、待标注和覆盖率。", ("quality", {"window": "current"})),
        case("packaging", "按月查看包装维度负面评论的变化。", ("aspect_trend", {"aspect": "packaging", "period": "month"})),
        case("logistics-zero", "按月查看物流维度的负面评论趋势，没有样本也请保留空值或真实零值。",
             ("aspect_trend", {"aspect": "logistics", "period": "month"})),
        case("ratings", "按商品对比原始1至5星评分分布。", ("ratings", {"group_by": "product", "window": "current"})),
        case("baseline-ratings", "按月份查询基线窗口的原始星级分布。", ("ratings", {"group_by": "month", "window": "baseline"})),
        case("windows", "比较已选当前窗口和基线窗口的评论量、覆盖率及负面占比。", ("window_comparison", {})),
        case("custom-windows", "比较2023-02-10至2023-02-20与2023-01-05至2023-01-15的评论情况。",
             ("window_comparison", dict(date_from="2023-02-10", date_to="2023-02-20", baseline_from="2023-01-05", baseline_to="2023-01-15"))),
        case("combined", "先检查数据质量，再按商品查看星级分布，最后查询当前窗口包装负面评论原文。",
             ("quality", {}), ("ratings", {"group_by": "product"}), ("evidence", {"aspect": "packaging", "sentiment": "negative"})),
        case("unlabeled", "检查标注覆盖率、失败和待标注数量。", ("quality", {}), model="SIMULATED-UNLABELED"),
        case("empty", "检查这个没有评论的范围的数据质量。", ("quality", {}), date_from="2023-03-01", date_to="2023-03-05"),
        case("outside-product", "只查询不在本次已选范围的 OUTSIDE 商品的星级分布，不要用已选商品替代。", status="unsupported"),
        case("outside-date", "查询2024年1月的评论趋势，不能用当前日期替代。", status="unsupported"),
        case("causal", "请确定包装问题的真实生产根因并确认是哪条生产线导致的。", status="unsupported"),
        case("write", "删除 DEMO-A 的全部评论，然后重新标注。", status="unsupported"),
    ]


def matches(actual, expected):
    return all(actual.get(key) == value for key, value in expected.items())


def grade(case, result):
    checks = {"status": result.get("status") == case["status"],
              "scope": result.get("scope") == {**SCOPE, **case["scope"], "as_of": None}}
    selected = [result["results"][key] for key in result.get("result_ids", [])]
    if case["status"] == "ok" and case["id"] != "evidence":
        products = set(({**SCOPE, **case["scope"]})["product_ids"])
        checks["all_selected_products"] = all(set(row["parameters"].get("product_ids") or products) == products
                                               for row in selected if row["kind"] != "context")
    for kind, params in case["expected"]:
        checks[kind] = any(row["kind"] == kind and matches(row["parameters"], params) for row in selected)
    if case["status"] == "ok":
        checks["validation"] = result.get("validation", {}).get("status") == "passed"
    else:
        checks["overview_reference"] = "overview" in result.get("result_ids", [])
    return checks


def snapshot(db):
    """记录所有测试业务表的内容摘要；任务表之外同样应保持只读。"""
    with psycopg.connect(db) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        names = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        result = {}
        for (name,) in names:
            rows = connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(sql.Identifier(name))).fetchall()
            result[name] = dict(rows=len(rows), sha256=hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest())
        return result


def business_probe(db):
    """真实业务数据仅在本机统计，不向供应商发送评论或保存数据库凭据。"""
    rows = []
    with closing(connect_review_database(db, readonly=True)) as connection, connection:
        connection.execute("BEGIN")
        counts = {name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                  for name in ("reviews", "products", "review_annotations", "review_opinions")}
    started = perf_counter()
    catalog = workbench_options(db)
    rows.append(dict(operation="options", elapsed_ms=round((perf_counter() - started) * 1000, 3)))
    if not catalog["profiles"] or not catalog["datasets"]:
        return dict(database=database_identity(db), counts=counts, measurements=rows, status="no_annotation_profile")
    profile = catalog["profiles"][0]
    dataset = max(catalog["datasets"], key=lambda item: item["review_count"])
    scope = dict(model=profile["requested_model"], base_url=profile["base_url"], prompt_version=profile["prompt_version"],
                 source=dataset["source"], dataset_version=dataset["dataset_version"], product_ids=[],
                 date_from=dataset["published_at_start"][:10], date_to=dataset["published_at_end"][:10])
    functions = [("quality", analytics.get_data_quality, {}), ("aspect_trend", analytics.get_aspect_trend, {}),
                 ("ratings", analytics.get_rating_distribution, {"group_by": "all"}),
                 ("windows", analytics.compare_time_windows, {"baseline_from": "1900-01-01", "baseline_to": "1900-01-02"})]
    for name, function, params in functions:
        for iteration in range(3):
            started = perf_counter()
            data = function(db, **scope, **params)
            rows.append(dict(operation=name, iteration=iteration + 1, elapsed_ms=round((perf_counter()-started)*1000, 3),
                             returned_groups=len(data.get("groups", data.get("buckets", [])))))
    return dict(database=database_identity(db), counts=counts, measurements=rows, status="passed",
                dataset_review_count=dataset["review_count"], dataset_product_count=dataset["product_count"],
                scope=scope,
                note="本机只读顺序查询；三次采样不代表并发或大规模性能")


def api_probe(target, output, env_file):
    """通过实际任务协议和后台 Worker 调用一次真实模型，检查幂等与结果恢复。"""
    from fastapi.testclient import TestClient
    from data_analysis_agent.api.app import create_app

    payload = dict(kind="analysis", parameters={**SCOPE, "question": "检查当前范围的数据质量、失败标注和覆盖率。"})
    started = perf_counter()
    with TestClient(create_app(target, target, import_root=output, env_file=env_file), base_url="http://127.0.0.1") as client:
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        response = client.post("/api/v1/tasks", json=payload, headers=headers)
        if response.status_code not in (200, 202):
            return dict(passed=False, stage="submit", http_status=response.status_code)
        task = response.json()["result"]["task"]
        task_id = task["task_id"]
        duplicate = client.post("/api/v1/tasks", json=payload, headers=headers)
        if duplicate.status_code not in (200, 202) or duplicate.json()["result"]["task"]["task_id"] != task_id:
            return dict(passed=False, stage="idempotency", http_status=duplicate.status_code)
        while perf_counter() - started < 180:
            task = client.get("/api/v1/tasks/" + task_id).json()["result"]
            if task["status"] in ("succeeded", "failed"):
                break
            sleep(0.2)
        result = task.get("result") or {}
        check = grade(next(case for case in cases() if case["id"] == "quality"), result) if result else {}
        return dict(status=task["status"], passed=task["status"] == "succeeded" and bool(check) and all(check.values()),
                    checks=check, idempotency=True, elapsed_seconds=round(perf_counter()-started, 3),
                    model_calls=result.get("model_calls"), usage=result.get("usage"), error=task.get("error"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="显式允许使用配置的真实模型额度")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--business-readonly", action="store_true")
    parser.add_argument("--case", action="append", dest="case_ids")
    args = parser.parse_args()
    if not args.live:
        parser.error("真实评测须显式传 --live；离线测试不会调用模型。")
    values = read_settings(args.env_file)
    if args.business_readonly and not values.get("DATABASE_URL"):
        parser.error("--business-readonly 需要 DATABASE_URL；不将缺少业务库配置记为通过。")
    admin_url = os.environ.get("TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        parser.error("需要指向隔离实例的 TEST_POSTGRES_ADMIN_URL。")
    selected = [case for case in cases() if not args.case_ids or case["id"] in args.case_ids]
    if not selected or (args.case_ids and set(args.case_ids) - {case["id"] for case in cases()}):
        parser.error("未知或空的问题集合。")
    # 每题最多四次调用且不做 SDK 自动重试，控制在线评测额度；不改用户的 .env。
    configured_settings = AgentSettings.from_values(values)
    settings = replace(configured_settings, max_steps=4, max_retries=0)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    name = "agent_test_review_eval_" + uuid.uuid4().hex
    target = urlunsplit(urlsplit(admin_url)._replace(path="/" + name))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    files = [Path(__file__), Path("src/data_analysis_agent/agents/review_tools.py"),
             Path("src/data_analysis_agent/agents/review_prompts.py"), Path("src/data_analysis_agent/agents/reviews.py")]
    report = dict(model=settings.model, endpoint=settings.base_url, fixture="96 synthetic reviews, fixed annotations; not sentiment-label accuracy",
                  started_at=datetime.now(timezone.utc).isoformat(), code_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
                  model_call_limit_per_case=settings.max_steps, sdk_retries=0,
                  api_configured_max_steps=configured_settings.max_steps, api_configured_retries=configured_settings.max_retries, cases=[])
    def save():
        (output / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        create_fixture(output / "fixture", target)
        before = snapshot(target)
        for case in selected:
            with (output / "framework.log").open("a", encoding="utf-8") as log, redirect_stdout(log):
                result = run_review_agent(target, case["question"], scope={**SCOPE, **case["scope"]}, settings=settings)
            checks = grade(case, result)
            item = dict(id=case["id"], question=case["question"], checks=checks, passed=all(checks.values()),
                        status=result["status"], model_calls=result["model_calls"], tool_calls=result["tool_calls"],
                        elapsed_seconds=result["elapsed_seconds"], usage=result.get("usage"), error=result.get("error"),
                        results=[dict(kind=row["kind"], parameters=row["parameters"]) for row in result["results"].values()])
            report["cases"].append(item)
            (output / (case["id"] + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            save()
            print(json.dumps({key:item[key] for key in ("id", "passed", "status", "model_calls", "elapsed_seconds")}), flush=True)
            # 认证或模型配置失败时停止，避免对同一失败消耗整套调用预算。
            if result.get("error", {}).get("code") in ("authentication_error", "configuration_error"):
                break
        report["business_tables_unchanged"] = before == snapshot(target)
        durations = sorted(row["elapsed_seconds"] for row in report["cases"])
        report["summary"] = dict(passed=sum(row["passed"] for row in report["cases"]), executed=len(durations), planned=len(selected),
                                 model_calls=sum(row["model_calls"] for row in report["cases"]),
                                 median_seconds=median(durations), p95_seconds=durations[math.ceil(len(durations)*0.95)-1])
        with (output / "framework.log").open("a", encoding="utf-8") as log, redirect_stdout(log):
            report["api_task"] = api_probe(target, output, args.env_file)
        if args.business_readonly and values.get("DATABASE_URL"):
            report["business_readonly"] = business_probe(values["DATABASE_URL"])
        report["passed"] = (report["summary"]["passed"] == len(selected) and report["business_tables_unchanged"]
                            and report["api_task"]["passed"]
                            and (not args.business_readonly or report["business_readonly"]["status"] == "passed"))
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        report["test_database_removed"] = True
        save()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
