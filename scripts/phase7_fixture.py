"""仅给 agent_test_ 隔离库追加分页验收样本。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from scripts.evaluate_review_agent import IDENTITY
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


def add_logistics(db, root):
    assert urlsplit(db).path.startswith('/agent_test_'), '只允许隔离测试库'
    rows = [{'parent_asin': 'DEMO-A', 'user_id': 'phase7-logistics-' + str(i), 'text': '物流太慢，包装完好。',
             'timestamp': int(datetime(2023, 2, 10 + i, tzinfo=timezone.utc).timestamp() * 1000), 'rating': 3} for i in range(8)]
    path = root / 'phase7-logistics.jsonl'
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
    imported = import_amazon_jsonl(db, reviews_path=path, dataset_version='SIMULATED-frontend')
    assert imported['status'] == 'ok'
    for review in pending_reviews(db, **IDENTITY, limit=1000):
        save_annotation(db, review, **IDENTITY, status='success', opinions=validate_opinions(
            json.dumps({'opinions': [{'aspect': 'logistics', 'sentiment': 'negative', 'quote': '物流太慢'}]}), review['text']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    add_logistics(os.environ['E2E_DATABASE_URL'], args.output)
