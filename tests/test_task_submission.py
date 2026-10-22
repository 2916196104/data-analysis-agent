"""Submission keeps persisted identities, request hashes and file boundaries stable."""

import hashlib
import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.store import (
    TaskConflict, claim_task, connect_task_database, enqueue, finish_task, init_tasks, worker_started,
)
from data_analysis_agent.jobs.submission import prepare_parameters, retry_task
from data_analysis_agent.reviews.catalog import list_products
from data_analysis_agent.reviews.prompts import PROMPT_VERSION
from data_analysis_agent.reviews.store import init_database


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.import_root = self.root / 'imports'
        self.import_root.mkdir()
        self.options = {'import_root': self.import_root, 'env_file': self.root / 'unused.env'}
        business, self.tasks = self.root / 'reviews.db', self.root / 'tasks.db'
        init_database(business)
        init_tasks(self.tasks, business)

    def fail_task(self, task_id):
        worker_started(self.tasks, 'simulated-worker')
        self.assertEqual(claim_task(self.tasks, 'simulated-worker')['task_id'], task_id)
        finish_task(self.tasks, task_id, 'simulated-worker', status='failed', error={'code': 'simulated'})

    def test_import_paths_and_retries_recheck_current_directory_boundaries(self):
        source = self.import_root / 'reviews.jsonl'
        source.write_text('{}\n', encoding='utf-8')
        values = prepare_parameters('import', {'reviews_path': source.name}, **self.options)
        self.assertEqual(values['reviews_path'], str(source.resolve()))
        task = enqueue(self.tasks, 'import', values)['task']
        self.fail_task(task['task_id'])
        outside = self.root / source.name
        source.rename(outside)
        for path in (source.name, str(outside), '../reviews.jsonl'):
            with self.subTest(path=path), self.assertRaises(ReviewDataError):
                prepare_parameters('import', {'reviews_path': path}, **self.options)
        with self.assertRaises(ReviewDataError):
            retry_task(self.tasks, task['task_id'], **self.options)

    def test_label_retry_preserves_frozen_model_and_rejects_configuration_changes(self):
        settings = {'LLM_MODEL_ID': 'simulated-model', 'LLM_API_KEY': 'simulated-secret',
                    'LLM_BASE_URL': 'https://simulated.invalid/v1'}
        with patch('data_analysis_agent.jobs.submission.read_settings', return_value=settings):
            values = prepare_parameters('label', {}, **self.options)
            self.assertEqual(values['model_identity'], {'model': settings['LLM_MODEL_ID'],
                              'base_url': settings['LLM_BASE_URL'], 'prompt_version': PROMPT_VERSION})
            task = enqueue(self.tasks, 'label', values)['task']
            self.fail_task(task['task_id'])
            retry = retry_task(self.tasks, task['task_id'], **self.options)['task']
            self.assertEqual(retry['parameters'], values)
            self.assertEqual(retry['parent_task_id'], task['task_id'])
        with patch('data_analysis_agent.jobs.submission.read_settings', return_value={**settings, 'LLM_MODEL_ID': 'changed'}):
            with self.assertRaises(TaskConflict):
                retry_task(self.tasks, task['task_id'], **self.options)

    def test_shared_serialization_retains_existing_request_hash_and_idempotency(self):
        values = {'reviews_path': '中文.jsonl', 'max_reviews': 20}
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':'), allow_nan=False)
        expected = hashlib.sha256(encode(['import', values, None]).encode('utf-8')).hexdigest()
        first = enqueue(self.tasks, 'import', values, idempotency_key='stable-key')['task']
        with closing(connect_task_database(self.tasks, readonly=True)) as connection:
            row = connection.execute('SELECT parameters_json, request_hash FROM tasks WHERE task_id=?',
                                     (first['task_id'],)).fetchone()
            self.assertEqual(row['parameters_json'], encode(values))
            self.assertEqual(row['request_hash'], expected)
        repeated = enqueue(self.tasks, 'import', dict(reversed(list(values.items()))), idempotency_key='stable-key')
        self.assertTrue(repeated['reused'])
        self.assertEqual(repeated['task']['task_id'], first['task_id'])

    def test_catalog_rejects_invalid_pagination_before_opening_database(self):
        for name, values in (('page', (False, 0, -1, 1.5, 1000001)), ('page_size', (True, 0, 101))):
            for value in values:
                with self.subTest(name=name, value=value), patch(
                        'data_analysis_agent.reviews.catalog.connect_review_database', side_effect=AssertionError('opened')):
                    with self.assertRaises(ReviewDataError):
                        list_products('unused.db', **{name: value})


if __name__ == '__main__':
    unittest.main()
