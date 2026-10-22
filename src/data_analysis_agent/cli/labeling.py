"""Explicit setup, incremental opinion labeling, status and evidence CLI."""

import argparse
import json
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.config import ConfigurationError
from data_analysis_agent.reviews.label_store import init_labeling, labeling_status, opinion_evidence, query_review_opinions
from data_analysis_agent.reviews.labeling import PROMPT_VERSION, run_labeling
from data_analysis_agent.core.database import DATABASE_ERRORS, database_argument
from data_analysis_agent.core.errors import ReviewDataError


def main():
    parser = argparse.ArgumentParser(description="商品评论六维度观点标注")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("init", help="由你显式创建观点表，保留原始评论")
    run = commands.add_parser("run", help="按模型和提示词版本增量标注")
    status = commands.add_parser("status", help="只读查看各模型版本进度，无需模型配置")
    evidence = commands.add_parser("evidence", help="查看评论原文、观点和模型版本")
    query = commands.add_parser("query", help="按明确版本和时间范围查询当前有效观点，不调用模型")
    for command in (setup, run, status, evidence, query):
        command.add_argument("--db", required=True, type=database_argument)
    for command in (run, status, query):
        command.add_argument("--product-id", action="append", help="指定商品 ID，可重复")
        command.add_argument("--date-from", help="评论 UTC 开始日期，YYYY-MM-DD，含当天")
        command.add_argument("--date-to", help="评论 UTC 结束日期，YYYY-MM-DD，含当天")
        command.add_argument("--as-of", help="回放截止 UTC 日期（含当天）或带时区的 ISO 8601 时间")
        command.add_argument("--source", help="只选择指定数据来源")
        command.add_argument("--dataset-version", help="只选择指定数据集版本")
    for command in (status, query):
        command.add_argument("--model", required=command is query, help="配置的模型 ID，与服务地址和提示词版本一起指定")
        command.add_argument("--base-url", required=command is query, help="标注时使用的服务地址")
        command.add_argument("--prompt-version", required=command is query, help="标注时使用的完整提示词版本")
    run.add_argument("--limit", type=int, default=20, help="每批最多处理条数，1—1000，默认20")
    run.add_argument("--workers", type=int, default=1, help="同时调用模型的请求数，1—4，默认1")
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--retry-failed", action="store_true", help="同时选择失败评论重新尝试")
    evidence.add_argument("--review-id", required=True, type=int)
    query.add_argument("--page", type=int, default=1)
    query.add_argument("--page-size", type=int, default=20)
    args = parser.parse_args()
    try:
        scope, identity = {}, {}
        if args.command in ("run", "status", "query"):
            scope = {"product_ids": args.product_id, **{name: getattr(args, name) for name in (
                "date_from", "date_to", "as_of", "source", "dataset_version")}}
        if args.command in ("status", "query"):
            identity = {name: getattr(args, name) for name in ("model", "base_url", "prompt_version")}
        if args.command == "init":
            result = {"status": "ok", **init_labeling(args.db)}
        elif args.command == "run":
            def show_progress(value):
                print(f"标注进度 {value['completed']}/{value['total']}，成功 {value['successful_reviews']}，"
                      f"失败 {value['failed_reviews']}，模型调用 {value['model_calls']}",
                      file=sys.stderr, flush=True)

            result = run_labeling(args.db, limit=args.limit, retry_failed=args.retry_failed,
                                   env_file=args.env_file, progress=show_progress, workers=args.workers, **scope)
        elif args.command == "status":
            result = {"status": "ok", "current_prompt_version": PROMPT_VERSION,
                      **labeling_status(args.db, **scope, **identity)}
        elif args.command == "query":
            result = {"status": "ok", **query_review_opinions(
                args.db, **scope, **identity, page=args.page, page_size=args.page_size)}
        else:
            result = {"status": "ok", "evidence": opinion_evidence(args.db, args.review_id)}
    except (ReviewDataError, ConfigurationError, OSError, *DATABASE_ERRORS) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
