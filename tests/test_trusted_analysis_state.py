"""P1-A：仅从任务库恢复可信排名，合成 SQLite，无网络或模型调用。"""
from contextlib import closing
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_review_intent as fixture
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.parameters import AnalysisParameters
from data_analysis_agent.jobs.store import connect_task_database, get_task


class TrustedAnalysisStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ReviewIntentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.queue()
        self.first = self.fixture.run_sync('100013588053的差评主要集中在哪些方面？')
        self.assertEqual(self.first['status'], 'ok', self.first.get('error'))
        self.parent = self.fixture.save(self.first['question'], self.first)
        self.parameters = {**self.fixture.scope(), 'conversation_id': 'conversation-A', 'context_task_id': self.parent}

    def load(self, **changes):
        with patch('socket.socket.connect', side_effect=AssertionError('offline only')), \
             patch('openai.OpenAI', side_effect=AssertionError('offline only')):
            return load_analysis_context(self.fixture.tasks, {**self.parameters, **changes})

    def ranking(self, context):
        return (context or {}).get('analysis_state', {}).get('ranking_basis')

    def change_result(self, task_id, mutate):
        result = deepcopy(get_task(self.fixture.tasks, task_id)['result'])
        mutate(result)
        with closing(connect_task_database(self.fixture.tasks)) as connection, connection:
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?', (canonical_json(result), task_id))

    def test_trusted_ranking_restores_all_six_counts_denominator_and_source(self):
        basis = self.ranking(self.load())
        self.assertIsNotNone(basis)
        self.assertEqual(basis['source']['task_id'], self.parent)
        self.assertEqual(basis['source']['result_id'], 'r1')
        self.assertEqual(basis['scope'], self.first['scope'])
        self.assertEqual(basis['scope_sha256'], content_sha256(self.first['scope']))
        self.assertEqual(basis['sentiment'], 'negative')
        self.assertEqual(basis['denominator']['value'], 3)
        self.assertEqual(len(basis['ranking']), 6)
        original = self.first['results']['r1']['data']['issues']
        self.assertEqual([(r['rank'], r['aspect'], r['negative_review_count']) for r in basis['ranking']],
                         [(r['rank'], r['aspect'], r['negative_review_count']) for r in original])
        self.assertTrue(basis['source']['task_sha256'])
        self.assertEqual(basis['source']['result_sha256'], content_sha256(self.first['results']['r1']))

    def test_target_followup_exposes_verified_basis_without_running_p6(self):
        context = self.load()
        from data_analysis_agent.agents.reviews import run_review_agent
        from test_agent import SETTINGS
        with patch('openai.OpenAI', side_effect=AssertionError('no model')):
            result = run_review_agent(self.fixture.db, '具体体现在哪里？', scope=self.fixture.scope(),
                settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['analysis_state']['ranking_basis']['source']['task_id'], self.parent)

    def test_selection_changes_invalidate_incompatible_rankings(self):
        changes = [dict(product_ids=['XIAOMI-TEST']), dict(date_from='2021-06-02'),
                   dict(baseline_from='2020-09-01'), dict(as_of='2021-06-30T23:59:59Z'),
                   dict(source='other'), dict(dataset_version='other'), dict(model='other'),
                   dict(base_url='https://other.invalid/v1'), dict(prompt_version='other')]
        for change in changes:
            with self.subTest(change=change):
                scope = {**self.fixture.scope(), **change}
                self.assertIsNone(self.ranking(self.load(**change)))
                self.assertIsNone(self.ranking(self.load(**change, selection_scope=scope)))

    def test_explicit_question_scope_change_does_not_publish_previous_basis(self):
        context = self.load()
        selection = self.fixture.scope(product_ids=['100013588053', 'XIAOMI-TEST'])
        from data_analysis_agent.agents.reviews import run_review_agent
        from test_agent import SETTINGS
        with patch('openai.OpenAI', side_effect=AssertionError('no model')):
            result = run_review_agent(self.fixture.db, 'XIAOMI-TEST的差评主要集中在哪些方面？',
                scope=selection, settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok')
        self.assertIsNone(result['analysis_state']['ranking_basis'])

    def test_cross_conversation_missing_and_incomplete_tasks_do_not_supply_rankings(self):
        self.assertIsNone(self.load(conversation_id='another'))
        self.assertIsNone(self.load(context_task_id='missing-task'))
        self.change_result(self.parent, lambda r: r.pop('results'))
        self.assertIsNone(self.ranking(self.load()))

    def test_arithmetic_consistent_parent_tamper_is_detected_by_saved_digest(self):
        def mutate(result):
            # 文字变动不影响数值校验；保存时摘要仍必须发现整个结果已改变。
            result['answer'] = 'tampered'
        self.change_result(self.parent, mutate)
        changed = get_task(self.fixture.tasks, self.parent)['result']
        from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
        checked = validate_report(changed['results'], changed['result_ids'],
                                  expected_scopes=expected_result_scopes(changed['results'], changed['scope']))
        self.assertEqual(checked['status'], 'passed')
        self.assertIsNone(self.ranking(self.load()))

    def test_business_failed_insufficient_and_partial_tasks_never_supply_rankings(self):
        for status, acceptance in [('failed', 'failed'), ('succeeded', 'insufficient_evidence'), ('partial', 'failed')]:
            with self.subTest(status=status, acceptance=acceptance):
                result = deepcopy(self.first)
                result['task_acceptance'] = {'status': acceptance, 'complete': False}
                if status == 'partial':
                    result['status'] = 'partial'
                task = self.fixture.save(self.first['question'], result, status=status)
                self.assertIsNone(self.ranking(self.load(context_task_id=task)))

    def test_legacy_task_preserves_conditions_without_upgrading_unsealed_numbers(self):
        with closing(connect_task_database(self.fixture.tasks)) as connection, connection:
            row = get_task(self.fixture.tasks, self.parent)
            progress = row['progress']
            progress.pop('analysis_integrity', None)
            connection.execute('UPDATE tasks SET progress_json=? WHERE task_id=?', (canonical_json(progress), self.parent))
        context = self.load()
        self.assertEqual(context['validated']['scope'], self.first['scope'])
        self.assertIsNone(self.ranking(context))

    def test_frontend_cannot_submit_ranking_numbers_or_trusted_state(self):
        for field in ('analysis_state', 'ranking_basis'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                AnalysisParameters.model_validate({**self.parameters, 'question': '具体体现在哪里？', field: {'count': 999999}})

    def test_fresh_business_validation_rejects_saved_pass_flags_with_wrong_goal(self):
        claimed = deepcopy(self.first)
        claimed['task_goal']['aspect'] = 'logistics'
        task = self.fixture.save(claimed['question'], claimed)
        self.assertIsNone(self.ranking(self.load(context_task_id=task)))

    def test_incomplete_reordered_and_filtered_rankings_are_not_full_rankings(self):
        for mutate in (lambda r: r['results']['r1']['data']['issues'].reverse(),
                       lambda r: r['results']['r1']['data']['issues'].pop()):
            result = deepcopy(self.first)
            mutate(result)
            task = self.fixture.save(result['question'], result)
            self.assertIsNone(self.ranking(self.load(context_task_id=task)))
        for question in ('物流差评有多少条？', '好评主要集中在哪些方面？'):
            result = self.fixture.run_sync(question)
            task = self.fixture.save(question, result)
            self.assertIsNone(self.ranking(self.load(context_task_id=task)))

    def test_result_reference_is_stable_across_fresh_reads_without_writes(self):
        import hashlib
        before = hashlib.sha256(self.fixture.tasks.read_bytes()).hexdigest()
        first, second = self.ranking(self.load()), self.ranking(self.load())
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(self.fixture.tasks.read_bytes()).hexdigest(), before)

    def test_seal_does_not_weaken_task_execution_ownership(self):
        from data_analysis_agent.jobs.store import finish_task, TaskConflict
        before = get_task(self.fixture.tasks, self.parent)
        with self.assertRaises(TaskConflict):
            finish_task(self.fixture.tasks, self.parent, 'another-worker', status='succeeded', result={'forged': True})
        self.assertEqual(get_task(self.fixture.tasks, self.parent), before)

    def test_pending_theme_goal_is_recovered_without_reparsing_its_old_text(self):
        from data_analysis_agent.agents import review_acceptance, review_intent
        from data_analysis_agent.agents.reviews import run_review_agent
        from test_agent import SETTINGS
        old_question = '具体体现在哪里？'
        with patch('openai.OpenAI', side_effect=AssertionError('no external calls')):
            pending_result = run_review_agent(self.fixture.db, old_question, scope=self.fixture.scope(),
                settings=SETTINGS, conversation_context=self.load())
        self.assertTrue(pending_result['user_intent']['needs_clarification'])
        task = self.fixture.save(old_question, pending_result, parent=self.parent)
        context = self.load(context_task_id=task)
        pending = context['analysis_state']['pending_goal']
        self.assertEqual(pending['source']['task_id'], task)
        self.assertEqual(pending['status'], 'awaiting_aspect')
        self.assertEqual(pending['goal']['levels'], ['themes'])
        self.assertFalse(pending['completed'])
        self.assertIsNotNone(self.ranking(context))
        original_semantic = review_intent.opinion_request
        original_goal = review_acceptance.build_request_goal
        def semantic(question):
            self.assertNotEqual(question, old_question, 'pending request must not be reparsed')
            return original_semantic(question)
        def goal(question, context=None):
            self.assertNotEqual(question, old_question, 'frozen goal must not be rebuilt from old prose')
            return original_goal(question, context)
        products = [{'product_id': '100013588053', 'title': '美的空调', 'brand': '美的'}]
        with patch.object(review_intent, 'opinion_request', side_effect=semantic), \
             patch.object(review_acceptance, 'build_request_goal', side_effect=goal):
            request = review_acceptance.build_request_goal('物流方面呢？', context)
            scope, plan, _ = review_intent.resolve_review_request('物流方面呢？', self.first['scope'], products,
                settings=SETTINGS, context=context)
        self.assertEqual(request.levels, ('themes',))
        self.assertEqual(plan.required_tools, ['get_aspect_drilldown'])
        self.assertEqual(plan.requirements[0]['parameters']['aspect'], 'logistics')
        self.assertEqual(plan.sentiment, 'negative')

    def test_actual_count_tamper_is_rejected_even_when_numeric_fields_are_consistent(self):
        def mutate(result):
            # 同步更改两个统计快照及排序，算术关系仍合法，但保存时摘要已失配。
            for row in result['results'].values():
                summary = row['data'].get('current') or row['data'].get('summary')
                if summary:
                    summary['aspects']['effectiveness'].update(negative_review_count=0, negative_rate=0.0)
            data = result['results']['r1']['data']
            for issue in data['issues']:
                if issue['aspect'] == 'effectiveness':
                    issue.update(negative_review_count=0, negative_rate=0.0)
            data['issues'].sort(key=lambda row: (-row['negative_review_count'], row['aspect']))
            for rank, issue in enumerate(data['issues'], 1):
                issue['rank'] = rank
        self.change_result(self.parent, mutate)
        changed = get_task(self.fixture.tasks, self.parent)['result']
        from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
        checked = validate_report(changed['results'], changed['result_ids'],
                                  expected_scopes=expected_result_scopes(changed['results'], changed['scope']))
        self.assertEqual(checked['status'], 'passed')
        self.assertIsNone(self.ranking(self.load()))

    def test_client_supplied_counts_are_ignored_even_before_parameter_validation(self):
        genuine = self.ranking(self.load())
        context = self.load(analysis_state={'ranking_basis': {'ranking': [{'negative_review_count': 999999}]}})
        self.assertEqual(self.ranking(context), genuine)

    def test_malformed_result_json_is_safe_to_ignore(self):
        with closing(connect_task_database(self.fixture.tasks)) as connection, connection:
            connection.execute('UPDATE tasks SET result_json=? WHERE task_id=?', ('{broken', self.parent))
        self.assertIsNone(self.load())

    def test_pending_goal_is_not_reused_after_scope_change(self):
        from data_analysis_agent.agents.reviews import run_review_agent
        from test_agent import SETTINGS
        with patch('openai.OpenAI', side_effect=AssertionError('no external calls')):
            result = run_review_agent(self.fixture.db, '具体体现在哪里？', scope=self.fixture.scope(),
                settings=SETTINGS, conversation_context=self.load())
        task = self.fixture.save('具体体现在哪里？', result, parent=self.parent)
        for changes in (dict(product_ids=['XIAOMI-TEST']), dict(date_from='2021-06-02')):
            with self.subTest(changes=changes):
                scope = {**self.fixture.scope(), **changes}
                context = self.load(context_task_id=task, selection_scope=scope, **changes)
                state = (context or {}).get('analysis_state', {})
                self.assertIsNone(state.get('pending_goal'))
                self.assertIsNone(state.get('ranking_basis'))


if __name__ == '__main__':
    unittest.main()
