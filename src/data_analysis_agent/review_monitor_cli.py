"""JSON commands for historical monitoring and local alert decisions."""

import argparse
import json
from pathlib import Path

if __name__ == "__main__" and not __package__:
    from _cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from .review_labeler import ASPECTS
from .database import DATABASE_ERRORS, database_argument
from .review_loader import ReviewDataError
from .review_monitor import DEFAULT_RULES, replay_monthly, run_analysis
from .review_monitor_store import (
    get_alert, get_analysis_run, init_monitoring, list_alerts, list_analysis_runs, set_alert_status,
)


def main():
    parser = argparse.ArgumentParser(description="保存口碑分析、按月回放、检测和处理规则告警")
    commands = parser.add_subparsers(dest="command", required=True)
    parsers = {name: commands.add_parser(name, help=help_text) for name, help_text in (
        ("init", "初始化监控表"), ("run", "分析指定窗口并保存告警"), ("replay", "按 UTC 整月回放"),
        ("runs", "查询分析记录"), ("run-detail", "查看分析快照和完整结果"),
        ("alerts", "查询告警"), ("alert", "查看告警原文证据和处理历史"),
        ("confirm", "确认告警"), ("ignore", "忽略告警"),
    )}
    for command in parsers.values():
        command.add_argument("--db", type=database_argument, required=True)
    for name in ("run", "replay"):
        command = parsers[name]
        for option in ("model", "base-url", "prompt-version"):
            command.add_argument("--" + option, required=True)
        command.add_argument("--product-id", action="append")
        command.add_argument("--source")
        command.add_argument("--dataset-version")
        command.add_argument("--rule-name", default=DEFAULT_RULES["name"])
        for option in ("min_samples", "min_coverage", "min_negative_count", "min_increase_pp", "cooldown_days"):
            command.add_argument("--" + option.replace("_", "-"), default=DEFAULT_RULES[option],
                                 type=float if option in ("min_coverage", "min_increase_pp") else int)
    for option in ("date-from", "date-to", "baseline-from", "baseline-to"):
        parsers["run"].add_argument("--" + option, required=True)
    parsers["run"].add_argument("--as-of", help="默认当前窗口结束日；控制评论发布时间")
    parsers["replay"].add_argument("--month-from", required=True, help="YYYY-MM，包含整月")
    parsers["replay"].add_argument("--month-to", required=True, help="YYYY-MM，包含整月")
    parsers["replay"].add_argument("--baseline-months", type=int, default=1)
    for name in ("runs", "alerts"):
        parsers[name].add_argument("--page", type=int, default=1)
        parsers[name].add_argument("--page-size", type=int, default=20)
    parsers["run-detail"].add_argument("--run-id", required=True)
    parsers["alerts"].add_argument("--status", choices=("pending", "confirmed", "ignored"))
    parsers["alerts"].add_argument("--product-id")
    parsers["alerts"].add_argument("--aspect", choices=tuple(ASPECTS))
    parsers["alerts"].add_argument("--source")
    parsers["alerts"].add_argument("--dataset-version")
    parsers["alerts"].add_argument("--run-id")
    for name in ("alert", "confirm", "ignore"):
        parsers[name].add_argument("--alert-id", type=int, required=True)
    for name in ("confirm", "ignore"):
        parsers[name].add_argument("--actor", required=True, help="处理人，记录到历史")
        parsers[name].add_argument("--note", default="")
    args = parser.parse_args()
    try:
        if args.command in ("run", "replay"):
            common = {name: getattr(args, name) for name in (
                "model", "base_url", "prompt_version", "source", "dataset_version")}
            rules = {key: getattr(args, key) for key in DEFAULT_RULES if key != "name"}
            common.update(product_ids=args.product_id, rules={"name": args.rule_name, **rules})
            if args.command == "run":
                result = run_analysis(args.db, **common, date_from=args.date_from, date_to=args.date_to,
                                      baseline_from=args.baseline_from, baseline_to=args.baseline_to, as_of=args.as_of)
            else:
                result = replay_monthly(args.db, **common, month_from=args.month_from, month_to=args.month_to,
                                        baseline_months=args.baseline_months)
        elif args.command == "init":
            result = init_monitoring(args.db)
        elif args.command == "runs":
            result = list_analysis_runs(args.db, page=args.page, page_size=args.page_size)
        elif args.command == "run-detail":
            result = get_analysis_run(args.db, args.run_id)
        elif args.command == "alerts":
            result = list_alerts(args.db, **{name: getattr(args, name) for name in (
                "status", "product_id", "aspect", "source", "dataset_version", "run_id", "page", "page_size")})
        elif args.command == "alert":
            result = get_alert(args.db, args.alert_id)
        else:
            result = set_alert_status(args.db, args.alert_id,
                                      status="confirmed" if args.command == "confirm" else "ignored",
                                      actor=args.actor, note=args.note)
        partial = result.get("complete") is False
        print(json.dumps({"status": "partial" if partial else "ok", "result": result},
                         ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if partial else 0
    except (ReviewDataError, OSError, *DATABASE_ERRORS) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
