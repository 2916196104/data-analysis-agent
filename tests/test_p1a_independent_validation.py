"""独立验收：自建六维合成语料，经真实 Worker/任务库路径，不调用主题模型。"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from data_analysis_agent.agents.review_prompts import PROMPT_VERSION
from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.parameters import AnalysisParameters
from data_analysis_agent.jobs.store import (init_tasks, worker_started, enqueue, claim_task, get_task,
                                           connect_task_database)
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.store import init_database, import_amazon_jsonl
from data_analysis_agent.reviews.label_store import init_labeling, pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


A, B = '100013588053', '100013588055'
COUNTS = {'service': 5, 'effectiveness': 4, 'logistics': 4, 'quality': 2, 'price': 1, 'packaging': 0}
QUOTES = {'service': '客服回复缓慢', 'effectiveness': '降温速度慢', 'logistics': '配送延误',
          'quality': '外壳开裂', 'price': '价格偏高', 'packaging': '包装破损'}


def independent_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class IndependentStateValidation(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='p1-independent-')))
        for target in ('socket.socket.connect', 'psycopg.connect', 'openai.OpenAI',
                       'data_analysis_agent.agents.review_topics._call'):
            self.enterContext(patch(target, side_effect=AssertionError('independent audit forbids network/classification')))
        self.db, self.tasks = self.root / 'business.sqlite3', self.root / 'tasks.sqlite3'
        init_database(self.db)
        self.identity = {'model': 'independent-labels', 'base_url': 'https://labels.invalid/v1', 'prompt_version': 'independent-v1'}
        self.scope = {**self.identity, 'source': 'amazon_reviews_2023', 'dataset_version': 'independent-state',
                      'product_ids': [A, B], 'date_from': '2023-02-01', 'date_to': '2023-02-28', 'as_of': None,
                      'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31'}
        rows = [{'parent_asin': A, 'user_id': str(i), 'text': '。'.join(QUOTES[a] for a, n in COUNTS.items() if i < n) or '暂无明确反馈',
                 'timestamp': int(datetime(2023, 2, 10, tzinfo=timezone.utc).timestamp() * 1000)} for i in range(6)]
        rows.append({'parent_asin': B, 'user_id': 'B', 'text': '物流准时', 'timestamp': rows[0]['timestamp']})
        source = self.root / 'reviews.jsonl'
        source.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
        import_amazon_jsonl(self.db, reviews_path=source, dataset_version='independent-state')
        init_labeling(self.db)
        for review in pending_reviews(self.db, **self.identity, dataset_version='independent-state', limit=100):
            values = [dict(aspect=a, sentiment='negative', quote=q) for a, q in QUOTES.items() if q in review['text']]
            if review['text'] == '物流准时':
                values = [dict(aspect='logistics', sentiment='positive', quote='物流准时')]
            save_annotation(self.db, review, **self.identity, status='success',
                            opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))
        init_tasks(self.tasks, self.db)
        self.worker = TaskWorker(self.db, self.tasks, import_root=self.root, env_file=self.root / 'unused.env')
        worker_started(self.tasks, self.worker.worker_id)
        self.agent_identity = {'model': SETTINGS.model, 'base_url': SETTINGS.base_url, 'prompt_version': PROMPT_VERSION}
        self.enterContext(patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(SETTINGS, self.agent_identity)))
        self.first = self.work(A + '的差评主要集中在哪些方面？')
        self.assertEqual(self.first['status'], 'succeeded', self.first.get('error'))
        self.assertTrue(self.first['result']['task_acceptance']['complete'])

    def work(self, question, parent=None, **changes):
        scope = {**self.scope, **changes}
        params = {**scope, 'selection_scope': scope, 'rules': {}, 'question': question,
                  'conversation_id': 'independent-session', 'model_identity': self.agent_identity}
        if parent:
            params['context_task_id'] = parent
        task = enqueue(self.tasks, 'analysis', params)['task']
        self.worker._execute(claim_task(self.tasks, self.worker.worker_id))
        return get_task(self.tasks, task['task_id'])

    def load(self, task=None, **changes):
        scope = {**self.scope, **changes}
        return load_analysis_context(self.tasks, {**scope, 'selection_scope': scope,
            'conversation_id': 'independent-session', 'context_task_id': (task or self.first)['task_id']})

    def basis(self, context):
        return (context or {}).get('analysis_state', {}).get('ranking_basis')

    def pending_task(self):
        task = self.work('具体体现在哪里？', self.first['task_id'])
        self.assertEqual(task['status'], 'succeeded')
        self.assertEqual(task['result']['status'], 'unsupported')
        self.assertEqual(task['result']['query_executions'], 0)
        self.assertEqual(task['result']['total_model_calls'], 0)
        return task

    def test_actual_worker_roundtrip_reconstructs_counts_order_scope_and_independent_digests(self):
        basis = self.basis(self.load())
        self.assertIsNotNone(basis)
        expected = sorted(COUNTS, key=lambda a: (-COUNTS[a], a))
        self.assertEqual([row['aspect'] for row in basis['ranking']], expected)
        self.assertEqual([row['rank'] for row in basis['ranking']], list(range(1, 7)))
        self.assertEqual({row['aspect']: row['negative_review_count'] for row in basis['ranking']}, COUNTS)
        self.assertTrue(all(row['denominator'] == 6 for row in basis['ranking']))
        self.assertEqual(basis['denominator'], {'field': 'successful_reviews', 'value': 6})
        self.assertEqual(basis['scope']['product_ids'], [A])
        self.assertEqual(basis['scope_sha256'], independent_digest(basis['scope']))
        source = basis['source']
        self.assertEqual((source['task_id'], source['result_id']), (self.first['task_id'], 'r1'))
        self.assertEqual(source['result_sha256'], independent_digest(self.first['result']['results']['r1']))
        self.assertEqual(source['task_sha256'], independent_digest(['analysis-task-integrity-v1', self.first['task_id'],
                         self.first['status'], self.first['parameters'], self.first['result']]))
        from data_analysis_agent.agents.review_plan import build_query_plan
        from data_analysis_agent.agents.review_acceptance import validate_task_completion
        from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
        result = self.first['result']
        plan = build_query_plan(result['question'], result['scope'])
        numeric = validate_report(result['results'], result['result_ids'], sentiment_intent='negative', query_plan=plan,
                                  expected_scopes=expected_result_scopes(result['results'], result['scope']))
        business = validate_task_completion(result['task_goal'], result['scope'], plan,
                                            [result['results'][key] for key in result['result_ids']])
        self.assertEqual(source['validation_sha256'], independent_digest({'numeric': numeric, 'business': business}))

    def test_pending_goal_restores_and_full_explicit_requests_take_priority(self):
        pending = self.pending_task()
        context = self.load(pending)
        self.assertEqual(context['analysis_state']['pending_goal']['goal']['levels'], ['themes'])
        for question, tool in [('物流差评有多少条？', 'get_top_issues'),
                               ('给我三条物流差评原文', 'get_evidence'),
                               ('好评主要集中在哪些方面？', 'get_positive_aspects')]:
            with self.subTest(question=question):
                task = self.work(question, pending['task_id'])
                self.assertEqual(task['status'], 'succeeded', task.get('error'))
                self.assertTrue(task['result']['task_acceptance']['complete'])
                self.assertEqual(task['result']['query_plan']['required_tools'], [tool])

    def test_explicit_short_positive_switch_after_pending_goal_is_accepted(self):
        pending = self.pending_task()
        task = self.work('那好评呢？', pending['task_id'])
        self.assertEqual(task['status'], 'succeeded', task.get('error'))
        result = task['result']
        self.assertEqual(result['user_intent']['sentiment'], 'positive')
        if result.get('query_plan'):
            self.assertTrue(result['task_acceptance']['plan_check']['passed'])
        if result['status'] == 'ok':
            self.assertTrue(result['task_acceptance']['complete'])
        else:
            # 合法澄清或正向主题的正确空结果也可接受，不能要求凭空产生好评证据。
            self.assertEqual(result['status'], 'unsupported')
            self.assertTrue(result['user_intent'].get('needs_clarification') or
                            result.get('task_acceptance', {}).get('status') == 'insufficient_evidence')

    def test_scope_and_annotation_changes_remove_both_trusted_inputs(self):
        pending = self.pending_task()
        for changes in ({'product_ids': [B]}, {'date_from': '2023-02-02'}, {'dataset_version': 'different'},
                        {'model': 'different'}, {'prompt_version': 'different'}, {'base_url': 'https://different.invalid'}):
            with self.subTest(changes=changes):
                state = (self.load(pending, **changes) or {}).get('analysis_state', {})
                self.assertIsNone(state.get('ranking_basis'))
                self.assertIsNone(state.get('pending_goal'))

    def test_cross_session_and_missing_reference_are_rejected(self):
        parameters = {**self.scope, 'context_task_id': self.first['task_id'], 'conversation_id': 'another-session'}
        self.assertIsNone(load_analysis_context(self.tasks, parameters))
        self.assertIsNone(load_analysis_context(self.tasks, {**parameters, 'context_task_id': 'missing'}))

    def test_tampered_result_and_incomplete_record_cannot_restore_ranking(self):
        with closing(connect_task_database(self.tasks)) as connection, connection:
            value = deepcopy(self.first['result'])
            value['results']['r1']['data']['issues'][0]['negative_review_count'] = 999
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?', (canonical_json(value), self.first['task_id']))
        self.assertIsNone(self.basis(self.load()))
        with closing(connect_task_database(self.tasks)) as connection, connection:
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?', ('{}', self.first['task_id']))
        self.assertIsNone(self.basis(self.load()))

    def test_failed_task_is_not_the_ranking_source(self):
        # 冻结 Agent 身份与 Worker 当前配置不同，真实执行入口应失败。
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity',
                   return_value=(SETTINGS, {**self.agent_identity, 'model': 'changed-model'})):
            task = self.work('查看统计', self.first['task_id'])
        self.assertEqual(task['status'], 'failed')
        context = self.load(task)
        # 失败轮次可贡献用户请求；若回退到旧排名，来源必须仍是之前成功的任务。
        basis = self.basis(context)
        self.assertIsNotNone(basis)
        self.assertEqual(basis['source']['task_id'], self.first['task_id'])
        self.assertNotEqual(basis['source']['task_id'], task['task_id'])

    def test_legacy_unsealed_task_keeps_only_conditions(self):
        with closing(connect_task_database(self.tasks)) as connection, connection:
            connection.execute('UPDATE tasks SET progress_json=? WHERE task_id=?',
                               (canonical_json({'stage': 'completed'}), self.first['task_id']))
        context = self.load()
        self.assertIsNotNone(context['validated'])
        self.assertIsNone(self.basis(context))

    def test_frontend_state_and_count_fields_are_not_an_input_channel(self):
        for field in ('analysis_state', 'ranking_basis'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                AnalysisParameters.model_validate({**self.scope, 'question': '具体体现在哪里？', field: {'count': 999}})

    def test_preflight_selection_rule_is_deterministic_without_classification(self):
        # 仅验证拟定规则，不是生产 P6 Planner 实现。
        ranking = self.basis(self.load())['ranking']
        selected = [row['aspect'] for row in sorted(ranking, key=lambda r: (-r['negative_review_count'], r['aspect']))
                    if row['negative_review_count'] > 0][:3]
        self.assertEqual(selected, ['service', 'effectiveness', 'logistics'])
        self.assertEqual([row['aspect'] for row in ranking if row['negative_review_count'] > 0][:3], selected)

    def test_historical_integrity_does_not_prove_current_business_counts_are_unchanged(self):
        basis = self.basis(self.load())
        row = {'parent_asin': A, 'user_id': 'new', 'text': '包装破损',
               'timestamp': int(datetime(2023, 2, 11, tzinfo=timezone.utc).timestamp() * 1000)}
        path = self.root / 'new.jsonl'
        path.write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='independent-state')
        review = next(r for r in pending_reviews(self.db, **self.identity, dataset_version='independent-state', limit=100)
                      if r['text'] == '包装破损')
        save_annotation(self.db, review, **self.identity, status='success',
                        opinions=validate_opinions(json.dumps({'opinions': [dict(aspect='packaging', sentiment='negative', quote='包装破损')]}), review['text']))
        scope = {k: v for k, v in basis['scope'].items() if not k.startswith('baseline_')}
        current = analytics.get_top_issues(self.db, **scope)
        self.assertEqual(current['current']['successful_reviews'], 7)
        self.assertEqual(basis['denominator']['value'], 6)
        self.assertEqual(self.basis(self.load()), basis)


if __name__ == '__main__':
    unittest.main()
