"""P1 real API/Worker boundaries with an in-memory catalog; no database or external calls."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_analysis_agent.agents.review_mvp_scope import validate_agent_scope
from data_analysis_agent.api.responses import error_response
from data_analysis_agent.api.routes.tasks import create_router
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.parameters import AnalysisParameters, LabelParameters
from data_analysis_agent.jobs.submission import prepare_parameters, retry_task
from data_analysis_agent.jobs.worker import TaskWorker
from test_agent import SETTINGS
from test_review_unknown_product_id import PRODUCTS, SCOPE, agent_at_boundary
import test_review_unknown_product_id as p0

SCOPE = {**SCOPE, 'dataset_version': 'reviews-csv'}
IDENTITY = {'model': SETTINGS.model, 'base_url': SETTINGS.base_url, 'prompt_version': 'offline'}


def task(parameters=None, status='queued'):
    return dict(task_id='offline-task', kind='analysis', status=status,
        parameters={**SCOPE, 'question': '商品 100013588055 有哪些差评？', 'rules': {},
                    'model_identity': IDENTITY, **(parameters or {})},
        progress={}, parent_task_id=None, started_at=None, created_at=None, result=None, error=None)


class MvpScopeTests(unittest.TestCase):
    def setUp(self):
        for target in ('sqlite3.connect', 'psycopg.connect', 'openai.OpenAI',
                       'data_analysis_agent.agents.reviews.create_llm',
                       'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            self.enterContext(patch(target, side_effect=AssertionError(f'Offline only: {target}')))
        self.connection = MagicMock()

        def execute(sql, parameters):
            self.assertEqual(sql, 'SELECT product_id FROM products WHERE source=? AND dataset_version=?')
            self.assertEqual(parameters, ('reviews_csv', 'reviews-csv'))
            return MagicMock(fetchall=lambda: deepcopy(self.catalog))

        self.catalog = PRODUCTS
        self.connection.execute.side_effect = execute
        self.connect = self.enterContext(patch('data_analysis_agent.agents.review_mvp_scope.connect_review_database',
                                               return_value=self.connection))
        self.configured = self.enterContext(patch('data_analysis_agent.jobs.submission.configured_agent_identity',
                                                  return_value=(SETTINGS, IDENTITY)))
        self.enqueue = self.enterContext(patch('data_analysis_agent.api.routes.tasks.enqueue',
            return_value={'task': task(), 'reused': False}))
        app = FastAPI()
        app.include_router(create_router('unused-tasks', 'unused-business', import_root=Path.cwd(), env_file='unused'),
                           prefix='/api/v1')

        @app.exception_handler(ReviewDataError)
        async def controlled_error(request, exc):
            return error_response(400, 'business_error', str(exc))

        self.client = self.enterContext(TestClient(app))

    def post(self, **changes):
        return self.client.post('/api/v1/tasks', json={'kind': 'analysis',
            'parameters': {**SCOPE, 'question': '商品 100013588055 有哪些差评？', **changes}})

    def worker(self, queued):
        worker = TaskWorker('unused-business', 'unused-tasks', import_root=Path.cwd(), env_file='unused')
        with patch('data_analysis_agent.jobs.worker.update_progress'), \
             patch('data_analysis_agent.jobs.worker.finish_task') as finish, \
             patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(SETTINGS, IDENTITY)) as identity, \
             patch('data_analysis_agent.jobs.analysis_context.load_analysis_context', return_value=None) as context, \
             patch('data_analysis_agent.agents.reviews.run_review_agent',
                   return_value={'status': 'unsupported', 'tool_calls': 0}) as agent:
            worker._execute(queued)
        return finish, identity, context, agent

    def test_a_api_keeps_exact_csv_identity_products_and_selection(self):
        original = deepcopy(SCOPE)
        response = self.post(selection_scope=SCOPE)
        self.assertEqual(response.status_code, 202, response.text)
        submitted = self.enqueue.call_args.args[2]
        self.assertEqual(submitted['source'], 'reviews_csv')
        self.assertEqual(submitted['dataset_version'], 'reviews-csv')
        self.assertEqual(submitted['product_ids'], ['100013588055'])
        self.assertEqual(submitted['selection_scope'], original)
        self.connect.assert_called_once_with('unused-business', readonly=True)
        self.assertEqual(SCOPE, original)

    def test_api_rejects_wrong_source_and_version_before_enqueue_or_configuration(self):
        for changes in ({'source': 'amazon_reviews_2023'}, {'dataset_version': 'amazon-reviews-2023'},
                        {'source': 'reviews-csv'}, {'source': None}, {'dataset_version': None}):
            with self.subTest(changes=changes):
                self.assertIn(self.post(**changes).status_code, (400, 422))
        self.enqueue.assert_not_called()
        self.configured.assert_not_called()
        self.connect.assert_not_called()

    def test_api_rejects_unknown_or_other_catalog_product(self):
        for ids in (['B0ZZZZZZZZ'], ['XYZ-ABC123'], ['100013588055', 'FOREIGN1']):
            with self.subTest(ids=ids):
                self.assertEqual(self.post(product_ids=ids).status_code, 400)
        self.enqueue.assert_not_called()
        self.configured.assert_not_called()

    def test_api_checks_selection_products_and_identity_independently(self):
        for selection in ({**SCOPE, 'product_ids': ['B0ZZZZZZZZ']},
                          {**SCOPE, 'source': 'amazon_reviews_2023'}):
            with self.subTest(selection=selection):
                self.assertIn(self.post(selection_scope=selection).status_code, (400, 422))
        self.enqueue.assert_not_called()

    def test_e_missing_csv_catalog_never_falls_back(self):
        self.catalog = []
        response = self.post()
        self.assertEqual(response.status_code, 400)
        self.assertIn('不可用', response.text)
        self.enqueue.assert_not_called()
        self.configured.assert_not_called()

    def test_worker_rejects_bypassed_source_before_context_or_agent(self):
        queued = task({'source': 'amazon_reviews_2023', 'dataset_version': 'amazon-reviews-2023'})
        original = deepcopy(queued)
        finish, identity, context, agent = self.worker(queued)
        self.assertEqual(finish.call_args.kwargs['status'], 'failed')
        self.assertIn('仅支持', finish.call_args.kwargs['error']['message'])
        identity.assert_not_called(); context.assert_not_called(); agent.assert_not_called()
        self.connect.assert_not_called()
        self.assertEqual(queued, original)

    def test_worker_rechecks_products_and_unavailable_catalog(self):
        for catalog, ids in ((PRODUCTS, ['FOREIGN1']), ([], ['100013588055'])):
            self.catalog = catalog
            with self.subTest(ids=ids, available=bool(catalog)):
                finish, identity, context, agent = self.worker(task({'product_ids': ids}))
                self.assertEqual(finish.call_args.kwargs['status'], 'failed')
                identity.assert_not_called(); context.assert_not_called(); agent.assert_not_called()

    def test_worker_also_rejects_foreign_products_in_selection_scope(self):
        finish, identity, context, agent = self.worker(task({'selection_scope': {**SCOPE, 'product_ids': ['FOREIGN1']}}))
        self.assertEqual(finish.call_args.kwargs['status'], 'failed')
        identity.assert_not_called(); context.assert_not_called(); agent.assert_not_called()

    def test_a_b_valid_worker_preserves_question_range_and_context_reference(self):
        for question in ('商品 100013588055 有哪些差评？', '那好评呢？'):
            queued = task({'question': question, 'conversation_id': 'conversation', 'context_task_id': 'parent',
                           'selection_scope': deepcopy(SCOPE)})
            with self.subTest(question=question):
                finish, _, context, agent = self.worker(queued)
                self.assertEqual(finish.call_args.kwargs['status'], 'succeeded')
                self.assertEqual(agent.call_args.args[1], question)
                self.assertEqual(agent.call_args.kwargs['scope'], SCOPE)
                self.assertEqual(context.call_args.args[1]['context_task_id'], 'parent')
                self.assertEqual(context.call_args.args[1]['selection_scope'], SCOPE)

    def test_retry_of_historical_amazon_job_is_rejected_without_rewriting_it(self):
        saved = task({'source': 'amazon_reviews_2023', 'dataset_version': 'amazon-reviews-2023'}, status='failed')
        original = deepcopy(saved)
        with patch('data_analysis_agent.jobs.submission.get_task', return_value=saved), \
             patch('data_analysis_agent.jobs.submission.enqueue') as enqueue:
            with self.assertRaisesRegex(ReviewDataError, '仅支持'):
                retry_task('unused', 'old', import_root=Path.cwd(), env_file='unused', business_db='unused-business')
        enqueue.assert_not_called()
        self.assertEqual(saved, original)

    def test_g_history_get_returns_original_amazon_identity(self):
        saved = task({'source': 'amazon_reviews_2023', 'dataset_version': 'amazon-reviews-2023'}, status='succeeded')
        with patch('data_analysis_agent.api.routes.tasks.get_task', return_value=saved):
            response = self.client.get('/api/v1/tasks/offline-task')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result'], saved)
        self.connect.assert_not_called()

    def test_f_cross_dataset_history_does_not_restore_users_or_pending_goals(self):
        saved = task({'source': 'amazon_reviews_2023', 'dataset_version': 'amazon-reviews-2023',
                      'conversation_id': 'conversation'}, status='failed')
        params = {**SCOPE, 'conversation_id': 'conversation', 'context_task_id': 'old', 'selection_scope': SCOPE}
        with patch('data_analysis_agent.jobs.analysis_context.get_task', return_value=saved):
            self.assertIsNone(load_analysis_context('unused', params))

    def test_untrusted_result_source_cannot_restore_pending_or_validated_state(self):
        saved = task({'conversation_id': 'conversation'}, status='succeeded')
        saved['result'] = {'status': 'ok', 'scope': {**SCOPE, 'source': 'amazon_reviews_2023'},
                           'validation': {'status': 'passed'}}
        with patch('data_analysis_agent.jobs.analysis_context.get_task', return_value=saved), \
             patch('data_analysis_agent.jobs.analysis_context.restore_pending_goal') as pending:
            self.assertIsNone(load_analysis_context('unused', {**SCOPE, 'conversation_id': 'conversation',
                                                               'context_task_id': 'old'}))
        pending.assert_not_called()

    def test_non_agent_amazon_analysis_and_label_contracts_remain_available(self):
        scope = {**SCOPE, 'source': 'amazon_reviews_2023', 'dataset_version': 'amazon-reviews-2023'}
        self.assertEqual(AnalysisParameters.model_validate(scope).source, 'amazon_reviews_2023')
        self.assertEqual(LabelParameters.model_validate({key: value for key, value in scope.items()
            if key in LabelParameters.model_fields}).source, 'amazon_reviews_2023')
        values = prepare_parameters('analysis', scope, import_root=Path.cwd(), env_file='unused', business_db='unused')
        self.assertEqual(values['source'], 'amazon_reviews_2023')
        self.connect.assert_not_called()

    def test_explicit_amazon_source_in_question_is_not_rebound_to_csv(self):
        for question in ('Amazon 商品 100013588055 有哪些差评？', '亚马逊的商品 100013588055 有哪些差评？',
                         'amazon_reviews_2023 商品 100013588055 有哪些差评？'):
            with self.subTest(question=question):
                self.assertEqual(self.post(question=question).status_code, 400)
                finish, identity, context, agent = self.worker(task({'question': question}))
                self.assertEqual(finish.call_args.kwargs['status'], 'failed')
                identity.assert_not_called(); context.assert_not_called(); agent.assert_not_called()
        self.enqueue.assert_not_called()
        self.connect.assert_not_called()

    def test_c_d_agent_catalog_gate_still_blocks_unknown_and_asin_without_tools(self):
        with patch.object(p0, 'SCOPE', SCOPE):
            for question in ('商品 XYZ-ABC123 有哪些差评？', '商品 B0ZZZZZZZZ 有哪些差评？'):
                with self.subTest(question=question):
                    result, reached = agent_at_boundary(question)
                    self.assertEqual(result['status'], 'unsupported')
                    self.assertEqual(result['scope']['source'], 'reviews_csv')
                    self.assertEqual(result['scope']['dataset_version'], 'reviews-csv')
                    self.assertEqual(result['query_executions'], 0)
                    self.assertFalse(reached)


if __name__ == '__main__':
    unittest.main()
