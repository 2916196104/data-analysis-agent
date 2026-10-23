"""创建隔离的模拟评论数据库，供前端验收使用。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_analysis_agent.reviews.label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.reviews.labeling import validate_opinions
from data_analysis_agent.reviews.monitor_store import init_monitoring
from data_analysis_agent.reviews.store import import_amazon_jsonl, init_database
from data_analysis_agent.jobs.store import init_tasks
from data_analysis_agent.reviews.human_review import init_workbench


def create_fixture(root: Path, database: str | None = None) -> dict[str, Any]:
    """初始化测试表，并写入两款商品、两个月份的固定评论和观点。"""
    root.mkdir(parents=True, exist_ok=True)
    db, tasks = root / 'reviews.sqlite3', root / 'reviews.tasks.sqlite3'
    if db.exists() or tasks.exists():
        raise ValueError('验收目录已存在数据库，请使用一个新的空目录。')
    if database:
        from urllib.parse import urlsplit

        if not urlsplit(database).path.startswith('/agent_test_'):
            raise ValueError('外部验收数据库必须使用 agent_test_ 前缀。')
        db = tasks = database
    init_database(db)
    init_labeling(db)
    init_workbench(db)
    init_monitoring(db)
    init_tasks(tasks, db)
    rows = []
    for product in ('DEMO-A', 'DEMO-B'):
        for month in (1, 2):
            if month == 1:
                negative_days = 2
            elif product == 'DEMO-A':
                negative_days = 12
            else:
                negative_days = 4
            for day in range(1, 25):
                rows.append({
                    'parent_asin': product, 'user_id': f'{product}-{month}-{day}',
                    'title': '模拟评论，仅用于前端验收',
                    'rating': 2 if day <= negative_days else 5,
                    'text': '包装破损，效果一般。' if day <= negative_days else '使用顺手，效果不错。',
                    'timestamp': int(datetime(2023, month, day, tzinfo=timezone.utc).timestamp() * 1000),
                })
    path = root / 'simulated.jsonl'
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
    imported = import_amazon_jsonl(db, reviews_path=path, dataset_version='SIMULATED-frontend')
    assert imported['status'] == 'ok', imported
    assert imported['imports'][0]['inserted_records'] == len(rows), imported
    identity = {'model': 'SIMULATED-ANNOTATIONS', 'base_url': 'https://offline.invalid/v1', 'prompt_version': 'fixture-v1'}
    for review in pending_reviews(db, **identity, limit=1000):
        is_negative = '破损' in review['text']
        opinions = [{
            'aspect': 'packaging' if is_negative else 'effectiveness',
            'sentiment': 'negative' if is_negative else 'positive',
            'quote': '包装破损' if is_negative else '效果不错',
        }]
        save_annotation(
            db,
            review,
            **identity,
            status='success',
            opinions=validate_opinions(
                json.dumps({'opinions': opinions}, ensure_ascii=False),
                review['text'],
            ),
        )
    (root / 'extra.jsonl').write_text(json.dumps({**rows[0], 'user_id': 'extra-demo', 'parent_asin': 'DEMO-C'}), encoding='utf-8')
    (root / 'broken.jsonl.gz').write_bytes(b'Intentionally invalid gzip for failure acceptance test.')
    return {'db': str(db), 'task_db': str(tasks), 'import_root': str(root), 'reviews': len(rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--database-env', help='可选的隔离 PostgreSQL 测试库环境变量名')
    args = parser.parse_args()
    import os

    result = create_fixture(args.output.resolve(), os.environ[args.database_env] if args.database_env else None)
    from data_analysis_agent.core.database import database_identity

    result['db'] = database_identity(result['db'])
    result['task_db'] = database_identity(result['task_db'])
    print(json.dumps(result))
