"""创建隔离的模拟评论数据库，供前端验收使用。"""

import argparse
import csv
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


def initialize_fixture(db, tasks):
    init_database(db)
    init_labeling(db)
    init_workbench(db)
    init_monitoring(db)
    init_tasks(tasks, db)


def seed_positive_trend(db, root: Path, identity: dict) -> None:
    specs = [('2023-01', '物流很快，送货很快，制冷很差'), ('2023-01', '客服很好'),
        ('2023-01', '物流很慢'), ('2023-01', '待标注'), ('2023-01', '失败标注'), ('2023-01', ''),
        ('2023-02', '物流很快'), ('2023-02', '送货很快'), ('2023-02', '客服很好'),
        ('2023-02', '物流很慢'), ('2023-03', '物流很慢')]
    rows = [dict(product_id='100013588055', product_name='正向趋势合成商品', user_id=str(index),
                 published_at=month + '-15', text=text) for index, (month, text) in enumerate(specs)]
    rows += [dict(product_id=product_id, product_name='范围对照', user_id='excluded-' + str(index),
                  published_at=day, text='物流很快') for index, (product_id, day) in enumerate([
                      ('OTHER', '2023-01-15'), ('100013588055', '2022-12-15'), ('100013588055', '2023-05-15')])]
    path = root / 'positive-trend.csv'
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    imported = import_amazon_jsonl(db, reviews_path=path, dataset_version='reviews-csv')
    if imported['imports'][0]['inserted_records'] != len(rows):
        raise ValueError('演示评论样例导入不完整。')
    for row in pending_reviews(db, **identity, source='reviews_csv', limit=100):
        if row['text'] == '待标注':
            continue
        if row['text'] == '失败标注':
            save_annotation(db, row, **identity, status='failed', error_code='invalid_output')
            continue
        values = [dict(aspect=aspect, sentiment=sentiment, quote=quote) for aspect, sentiment, quote in [
            ('logistics', 'positive', '物流很快'), ('logistics', 'positive', '送货很快'),
            ('logistics', 'negative', '物流很慢'), ('service', 'positive', '客服很好'),
            ('effectiveness', 'negative', '制冷很差')] if quote in row['text']]
        save_annotation(db, row, **identity, status='success',
                        opinions=validate_opinions(json.dumps({'opinions': values}), row['text']))


def create_demo_fixture(root: Path) -> Path:
    db = root / 'reviews.sqlite3'
    initialize_fixture(db, root / 'reviews.tasks.sqlite3')
    seed_positive_trend(db, root, {'model': 'SIMULATED-ANNOTATIONS', 'base_url': 'https://offline.invalid/v1',
                                  'prompt_version': 'fixture-v1'})
    return db


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
    initialize_fixture(db, tasks)
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
