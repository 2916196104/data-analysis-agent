"""Offline reviews_csv fixture preserving the independent P6-A ranking."""
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import test_p1a_independent_validation as old
from test_agent import SETTINGS
from data_analysis_agent.agents.review_prompts import PROMPT_VERSION
from data_analysis_agent.jobs.store import init_tasks, worker_started
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews.store import init_database, import_amazon_jsonl
from data_analysis_agent.reviews.label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


def csv_fixture_setup(self):
    """Recreate the old fixture's ranking with the supported source and real Worker."""
    self.root = Path(self.enterContext(TemporaryDirectory(prefix='p8c0-csv-')))
    for target in ('socket.socket.connect', 'psycopg.connect', 'openai.OpenAI', 'openai.AsyncOpenAI',
                   'data_analysis_agent.agents.review_topics._call',
                   'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
        self.enterContext(patch(target, side_effect=AssertionError('offline migration sample')))
    self.db, self.tasks = self.root / 'business.sqlite3', self.root / 'tasks.sqlite3'
    init_database(self.db)
    self.identity = {'model': 'independent-labels', 'base_url': 'https://labels.invalid/v1',
                     'prompt_version': 'independent-v1'}
    self.scope = {**self.identity, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
                  'product_ids': [old.A, old.B], 'date_from': '2023-02-01', 'date_to': '2023-02-28',
                  'as_of': None, 'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31'}
    rows = [dict(product_id=old.A, product_name='合成商品A', user_id=str(i),
                 text='。'.join(old.QUOTES[a] for a, n in old.COUNTS.items() if i < n) or '暂无明确反馈',
                 published_at='2023-02-10') for i in range(6)]
    rows.append(dict(product_id=old.B, product_name='合成商品B', user_id='B',
                     text='物流准时', published_at='2023-02-10'))
    path = self.root / 'reviews.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
    init_labeling(self.db)
    for review in pending_reviews(self.db, **self.identity, source='reviews_csv', dataset_version='reviews-csv', limit=100):
        values = [dict(aspect=a, sentiment='negative', quote=q) for a, q in old.QUOTES.items() if q in review['text']]
        if review['text'] == '物流准时':
            values = [dict(aspect='logistics', sentiment='positive', quote='物流准时')]
        save_annotation(self.db, review, **self.identity, status='success',
                        opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))
    init_tasks(self.tasks, self.db)
    self.worker = TaskWorker(self.db, self.tasks, import_root=self.root, env_file=self.root / 'unused.env')
    worker_started(self.tasks, self.worker.worker_id)
    self.agent_identity = {'model': SETTINGS.model, 'base_url': SETTINGS.base_url, 'prompt_version': PROMPT_VERSION}
    self.enterContext(patch('data_analysis_agent.jobs.worker.configured_agent_identity',
                            return_value=(SETTINGS, self.agent_identity)))
    self.first = self.work(old.A + '的差评主要集中在哪些方面？')
    self.assertEqual(self.first['status'], 'succeeded', self.first.get('error'))
    result = self.first['result']
    self.assertEqual(result['scope']['source'], 'reviews_csv')
    self.assertEqual(result['scope']['dataset_version'], 'reviews-csv')
    self.assertEqual(result['scope']['product_ids'], [old.A])
    self.assertEqual(result['query_plan']['required_tools'], ['get_top_issues'])
    self.assertGreater(result['query_executions'], 0)
    self.assertEqual(result['validation']['status'], 'passed')
    self.assertEqual(result['total_model_calls'], 0)
    self.assertTrue(result['task_acceptance']['complete'])

