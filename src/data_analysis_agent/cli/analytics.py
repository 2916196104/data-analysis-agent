"""JSON command-line entry for read-only review business tools."""

import argparse
import json
from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.reviews.analytics import compare_products, get_evidence, get_top_issues, get_trend
from data_analysis_agent.reviews.opinions import ASPECTS
from data_analysis_agent.core.database import DATABASE_ERRORS, database_argument, is_postgres
from data_analysis_agent.core.errors import ReviewDataError


def main():
    parser = argparse.ArgumentParser(description="商品口碑统计与证据查询，不调用模型")
    commands = parser.add_subparsers(dest="command", required=True)
    trend = commands.add_parser("trend", help="按日或月统计评论量、覆盖率和负面占比")
    issues = commands.add_parser("top-issues", help="主要问题排序与明确基线的变化")
    compare = commands.add_parser("compare-products", help="相同口径下对比商品")
    evidence = commands.add_parser("evidence", help="分页查询观点原文及来源")
    for command in (trend, issues, compare, evidence):
        command.add_argument("--db", required=True, type=database_argument)
        command.add_argument("--model", required=True, help="标注时配置的模型 ID")
        command.add_argument("--base-url", required=True, help="标注时使用的模型服务地址")
        command.add_argument("--prompt-version", required=True, help="完整提示词版本")
        command.add_argument("--product-id", action="append", help="可重复；省略时选择全部商品")
        command.add_argument("--date-from", required=True, help="UTC 开始日期，含当天")
        command.add_argument("--date-to", required=True, help="UTC 结束日期，含当天")
        command.add_argument("--as-of", help="回放截止 UTC 日期或带时区的时间")
        command.add_argument("--source", help="指定数据来源")
        command.add_argument("--dataset-version", help="指定数据集版本")
        command.add_argument("--output", type=Path, help="另存 JSON 到新文件，不覆盖已有文件")
    trend.add_argument("--period", choices=("day", "month"), default="month")
    issues.add_argument("--baseline-from", required=True, help="基线 UTC 开始日期")
    issues.add_argument("--baseline-to", required=True, help="基线 UTC 结束日期，须早于当前窗口")
    issues.add_argument("--top-n", type=int, default=6)
    evidence.add_argument("--aspect", choices=tuple(ASPECTS))
    evidence.add_argument("--sentiment", choices=("positive", "negative", "neutral", "unknown", "all"), default="negative")
    evidence.add_argument("--review-id", type=int)
    evidence.add_argument("--page", type=int, default=1)
    evidence.add_argument("--page-size", type=int, default=20)
    args = parser.parse_args()
    try:
        if args.output and not is_postgres(args.db):
            database = Path(args.db).resolve()
            protected = {database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
            if args.output.resolve() in protected:
                raise ReviewDataError("输出路径不能是数据库或数据库辅助文件。")
        options = {name: getattr(args, name) for name in (
            "model", "base_url", "prompt_version", "date_from", "date_to", "as_of", "source", "dataset_version")}
        options["product_ids"] = args.product_id
        if args.command == "trend":
            result = get_trend(args.db, **options, period=args.period)
        elif args.command == "top-issues":
            result = get_top_issues(args.db, **options, baseline_from=args.baseline_from,
                                    baseline_to=args.baseline_to, top_n=args.top_n)
        elif args.command == "compare-products":
            result = compare_products(args.db, **options)
        else:
            result = get_evidence(args.db, **options, aspect=args.aspect,
                                  sentiment=None if args.sentiment == "all" else args.sentiment,
                                  review_id=args.review_id, page=args.page, page_size=args.page_size)
        payload = json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(payload + "\n")
    except (ReviewDataError, OSError, *DATABASE_ERRORS) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
