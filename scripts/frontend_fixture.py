"""Create an isolated, explicitly simulated database for frontend acceptance tests."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from data_analysis_agent.review_label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.review_labeler import validate_opinions
from data_analysis_agent.review_monitor_store import init_monitoring
from data_analysis_agent.review_store import import_amazon_jsonl, init_database
from data_analysis_agent.review_task_store import init_tasks
from data_analysis_agent.review_workbench_store import init_workbench


def create_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    db, tasks = root / 'reviews.sqlite3', root / 'reviews.tasks.sqlite3'
    if db.exists() or tasks.exists():
        raise ValueError('验收目录已存在数据库，请使用一个新的空目录。')
    init_database(db)
    init_labeling(db)
    init_workbench(db)
    init_monitoring(db)
    init_tasks(tasks, db)
    rows = []
    for product in ('DEMO-A', 'DEMO-B'):
        for month in (1, 2):
            negatives = 2 if month == 1 else (12 if product == 'DEMO-A' else 4)
            for day in range(1, 25):
                rows.append({
                    'parent_asin': product, 'user_id': f'{product}-{month}-{day}',
                    'title': '模拟评论，仅用于前端验收', 'rating': 2 if day <= negatives else 5,
                    'text': '包装破损，效果一般。' if day <= negatives else '使用顺手，效果不错。',
                    'timestamp': int(datetime(2023, month, day, tzinfo=timezone.utc).timestamp() * 1000),
                })
    path = root / 'simulated.jsonl'
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
    imported = import_amazon_jsonl(db, reviews_path=path, dataset_version='SIMULATED-frontend')
    assert imported['status'] == 'ok', imported
    assert imported['imports'][0]['inserted_records'] == len(rows), imported
    identity = {'model': 'SIMULATED-ANNOTATIONS', 'base_url': 'https://offline.invalid/v1', 'prompt_version': 'fixture-v1'}
    for review in pending_reviews(db, **identity, limit=1000):
        negative = '破损' in review['text']
        opinions = [{'aspect': 'packaging' if negative else 'effectiveness',
                     'sentiment': 'negative' if negative else 'positive',
                     'quote': '包装破损' if negative else '效果不错'}]
        save_annotation(db, review, **identity, status='success',
                        opinions=validate_opinions(json.dumps({'opinions': opinions}, ensure_ascii=False), review['text']))
    (root / 'extra.jsonl').write_text(json.dumps({**rows[0], 'user_id': 'extra-demo', 'parent_asin': 'DEMO-C'}), encoding='utf-8')
    (root / 'broken.jsonl.gz').write_bytes(b'Intentionally invalid gzip for failure acceptance test.')
    return {'db': str(db), 'task_db': str(tasks), 'import_root': str(root), 'reviews': len(rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_fixture(args.output.resolve())))
