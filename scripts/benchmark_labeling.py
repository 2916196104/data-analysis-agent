"""Run one PostgreSQL labeling benchmark and persist comparable metrics."""

import argparse
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import database_target
from data_analysis_agent.reviews.store import connect_review_database


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percent) - 1)
    return round(ordered[index], 3)


def resolve_database(value, env_file):
    if value.startswith("env:"):
        name = value[4:]
        value = read_settings(env_file).get(name, "")
        if not value:
            raise ValueError(f"请在 {env_file} 或进程环境变量中配置 {name}。")
    return database_target(value)


def collect_annotation_metrics(target, result):
    annotation_ids = [
        item["annotation_id"]
        for item in result.get("results", [])
        if item.get("annotation_id") is not None
    ]
    if not annotation_ids:
        return {"annotation_rows": 0, "errors": {}, "model_latency_ms": {}, "tokens": {}}

    marks = ",".join("?" for _ in annotation_ids)
    with closing(connect_review_database(target, readonly=True)) as connection:
        rows = connection.execute(
            f"""SELECT id, status, error_code, elapsed_ms,
                       input_tokens, output_tokens, total_tokens
                FROM review_annotations
                WHERE id IN ({marks})
                ORDER BY id""",
            annotation_ids,
        ).fetchall()

    latencies = [float(row[3]) for row in rows if row[3] is not None]
    errors = Counter(
        row[2] or ("success" if row[1] == "success" else "unknown")
        for row in rows
    )
    return {
        "annotation_rows": len(rows),
        "annotation_success": sum(row[1] == "success" for row in rows),
        "annotation_failed": sum(row[1] == "failed" for row in rows),
        "errors": dict(errors),
        "model_latency_ms": {
            "count": len(latencies),
            "average": round(statistics.mean(latencies), 3) if latencies else None,
            "p95": percentile(latencies, 0.95),
        },
        "tokens": {
            "input": sum(row[4] or 0 for row in rows),
            "output": sum(row[5] or 0 for row in rows),
            "total": sum(row[6] or 0 for row in rows),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="记录一次 PostgreSQL 模型标注基准")
    parser.add_argument("--db", required=True, help="数据库路径、DSN 或 env:NAME")
    parser.add_argument("--workers", required=True, type=int, choices=range(1, 5))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 1000:
        parser.error("--limit 必须在 1—1000 之间")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress_path = args.output.with_suffix(".progress.log")
    started_at = datetime.now(timezone.utc)
    command = [
        sys.executable,
        "-m",
        "data_analysis_agent.cli.labeling",
        "run",
        "--db",
        args.db,
        "--limit",
        str(args.limit),
        "--workers",
        str(args.workers),
        "--env-file",
        str(args.env_file),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=Path.cwd(),
    )
    finished_at = datetime.now(timezone.utc)
    progress_path.write_text(completed.stderr, encoding="utf-8")

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        args.output.write_text(completed.stdout, encoding="utf-8")
        print(f"标注命令没有返回 JSON，原始输出已保存到 {args.output}。", file=sys.stderr)
        return 1

    metrics = {}
    if isinstance(result, dict) and result.get("status") != "error":
        try:
            target = resolve_database(args.db, args.env_file)
            metrics = collect_annotation_metrics(target, result)
        except Exception as exc:
            metrics = {"database_metrics_error": type(exc).__name__}

    report = {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "wall_ms": round((finished_at - started_at).total_seconds() * 1000, 3),
        "exit_code": completed.returncode,
        "workers": args.workers,
        "limit": args.limit,
        "database": args.db,
        "progress_log": str(progress_path),
        "batch": result,
        "metrics": metrics,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
