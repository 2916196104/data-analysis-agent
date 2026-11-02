"""显式运维入口；没有自动初始化、自动范围推断或 Agent 接入。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from data_analysis_agent.core.database import database_argument
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import migrate_embedding_store, sync_effective_opinions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='env:DATABASE_URL')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('migrate', 'rollback'):
        child = sub.add_parser(name)
        child.add_argument('--ddl-approved', action='store_true', required=True,
                           help='仅在操作者已经取得目标数据库 DDL 许可后使用')
    sync = sub.add_parser('sync')
    for name in ('source', 'dataset-version', 'product-id', 'date-from', 'date-to', 'model', 'prompt-version'):
        sync.add_argument('--' + name, required=True)
    sync.add_argument('--base-url-env', required=True, help='包含标注 base_url 的配置名，避免输出私有地址')
    sync.add_argument('--limit', type=int, default=200)
    sync.add_argument('--embedding-python', default='.cache/embedding-eval/venv/Scripts/python.exe')
    sync.add_argument('--cache-dir', default='.cache/embedding-eval/hf/hub')
    args = parser.parse_args()
    try:
        db = database_argument(args.db)
        if args.command in ('migrate', 'rollback'):
            migrate_embedding_store(db, rollback=args.command == 'rollback')
            print(json.dumps(dict(status='completed', command=args.command)))
        else:
            options = dict(source=args.source, dataset_version=args.dataset_version, product_ids=[args.product_id],
                date_from=args.date_from, date_to=args.date_to, model=args.model, prompt_version=args.prompt_version,
                base_url=read_settings().get(args.base_url_env, ''))
            encoder = LocalEmbeddingProcess(args.embedding_python, args.cache_dir)
            print(json.dumps(sync_effective_opinions(db, options=options, encoder=encoder, limit=args.limit)))
        return 0
    except Exception:
        print('{"error":"embedding_operation_failed; inspect rag_embedding_sync_runs"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
