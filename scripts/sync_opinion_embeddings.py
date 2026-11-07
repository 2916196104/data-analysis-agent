"""显式运维入口；没有自动初始化、自动范围推断或 Agent 接入。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from data_analysis_agent.core.database import database_argument
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import migrate_embedding_store, sync_effective_opinions
from data_analysis_agent.reviews.embedding_budget import Frozen053Budget, ROOT, require

EXECUTION = Path('outputs/rag-semantic-retrieval/mvp-053-sync-execution-v1')


def main(argv=None):
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
    sync.add_argument('--execute', action='store_true', help='仅在另行取得数据库写入授权后使用；默认dry-run')
    sync.add_argument('--authorize-local-encoding', action='store_true', help='仅在另行取得本地观点编码授权后使用')
    args = parser.parse_args(argv)
    receipt = None
    try:
        db = database_argument(args.db)
        if args.command in ('migrate', 'rollback'):
            migrate_embedding_store(db, rollback=args.command == 'rollback')
            print(json.dumps(dict(status='completed', command=args.command)))
        else:
            options = dict(source=args.source, dataset_version=args.dataset_version, product_ids=[args.product_id],
                date_from=args.date_from, date_to=args.date_to, model=args.model, prompt_version=args.prompt_version,
                base_url=read_settings().get(args.base_url_env, ''))
            budget = Frozen053Budget.load() if args.product_id == '100013588053' else None
            if budget is not None:
                budget.check_target(db)
                budget.check_options(options, args.limit)
            if args.execute:
                require(args.authorize_local_encoding, 'explicit_local_encoding_authorization_required')
                if budget is not None:
                    require(Path(sys.executable).resolve() == (ROOT/'.venv/Scripts/python.exe').resolve(), 'project_python_required')
                    require(Path(args.embedding_python).resolve() == (ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve()
                            and Path(args.cache_dir).resolve() == (ROOT/'.cache/embedding-eval/hf/hub').resolve(), 'frozen_worker_paths_required')
                    output = ROOT/EXECUTION
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.mkdir(exist_ok=False)  # A consumed attempt is never removed or automatically retried.
                    receipt = output/'result.json'
                    receipt.write_text(json.dumps(dict(status='claimed', private_plan_sha256=sha256(budget.raw_plan).hexdigest())), encoding='utf-8')
            encoder = LocalEmbeddingProcess(args.embedding_python, args.cache_dir) if args.execute else None
            result = sync_effective_opinions(db, options=options, encoder=encoder, limit=args.limit,
                budget=budget, execute=args.execute, encoding_authorized=args.authorize_local_encoding)
            if receipt is not None:
                receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            public = dict(result)
            if 'metrics' in public:
                public['metrics'] = {k: v for k, v in public['metrics'].items() if k != 'published_rows'}
            print(json.dumps(public))
        return 0
    except Exception:
        if receipt is not None:
            try:
                receipt.write_text(json.dumps(dict(status='failed_or_commit_unconfirmed', automatic_retries=0,
                    action='stop_and_reconcile_before_any_new_attempt')), encoding='utf-8')
            except OSError:
                pass  # Preserve the occupied attempt directory even when its receipt cannot be written.
        print('{"error":"embedding_operation_failed; inspect rag_embedding_sync_runs"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
