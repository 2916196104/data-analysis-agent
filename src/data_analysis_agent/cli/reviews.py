"""Amazon JSONL import CLI, independent of sales and model configuration."""

import argparse
import json
import zlib
from pathlib import Path

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data_analysis_agent._cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from data_analysis_agent.core.database import DATABASE_ERRORS, database_argument
from data_analysis_agent.reviews.loader import DATASET_VERSION, ReviewDataError
from data_analysis_agent.reviews.store import get_quality_summary, get_review_evidence, import_amazon_jsonl, init_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon 评论与商品元数据导入（历史数据）")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="显式初始化 SQLite / PostgreSQL 评论表")
    load = commands.add_parser("import", help="流式导入评论和/或商品元数据")
    summary = commands.add_parser("summary", help="查询数据库数据质量摘要")
    evidence = commands.add_parser("evidence", help="按评论 ID 查询原文与来源位置")
    for command in (init, load, summary, evidence):
        command.add_argument("--db", required=True, type=database_argument, help="SQLite 文件路径、PostgreSQL URL 或 env:变量名")
    load.add_argument("--reviews", type=Path, help="Amazon 评论 .jsonl 或 .jsonl.gz")
    load.add_argument("--meta", type=Path, help="Amazon 商品元数据 .jsonl 或 .jsonl.gz")
    load.add_argument("--dataset-version", default=DATASET_VERSION)
    load.add_argument("--product-id", action="append", help="只导入指定 parent_asin，可重复传入")
    load.add_argument("--max-reviews", type=int, help="本次最多新增评论数（正整数）；重复和坏记录不占额度，省略则不限")
    evidence.add_argument("--review-id", required=True, type=int)
    args = parser.parse_args()
    if args.command == "import" and args.reviews is None and args.meta is None:
        parser.error("import 至少需要 --reviews 或 --meta。")
    try:
        if args.command == "init":
            result = {"status": "ok", **init_database(args.db)}
        elif args.command == "import":
            result = import_amazon_jsonl(args.db, reviews_path=args.reviews, meta_path=args.meta,
                                         dataset_version=args.dataset_version, product_ids=args.product_id,
                                         max_reviews=args.max_reviews)
        elif args.command == "summary":
            result = {"status": "ok", "quality": get_quality_summary(args.db)}
        else:
            result = {"status": "ok", "evidence": get_review_evidence(args.db, args.review_id)}
    except (ReviewDataError, OSError, *DATABASE_ERRORS, EOFError, zlib.error) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
