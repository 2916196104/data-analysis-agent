"""真实离线 P6-B/C 与 Worker 产物；只模拟供应商，不手写公开卡片数据。"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_p6c_budget_execution as cfixtures
from data_analysis_agent.agents.review_execution import P6ExecutedAnalysis
from data_analysis_agent.agents.review_publication import publish_p6_execution, read_p6_publication
from data_analysis_agent.agents.review_budget import P6InputIntegrityError
from data_analysis_agent.core.serialization import canonical_json, content_sha256


class PublicationFixture:
    def __init__(self):
        self.case = cfixtures.P6CBudgetExecutionTests()
        self.case.setUp()
        self.settings = replace(self.case.settings, p6_preparation_enabled=True, p6_publication_enabled=True)

    def close(self): self.case.doCleanups()

    def respond(self, mode):
        def response(request):
            if mode == 'fail_' + request['aspect']:
                self.case.calls.append(request)
                raise TimeoutError('offline injection')
            empty = mode == 'empty' or mode == 'empty_' + request['aspect']
            result = self.case.response(request, empty=empty)
            if mode == 'many':
                body = json.loads(request['messages'][1]['content'])
                if request['stage'] == 'topic_discovery':
                    content = {'themes': [{'n': body['opinions'][0]['quote'] + str(i),
                        'd': body['opinions'][0]['quote'], 'q': [body['opinions'][0]['opinion_id']]} for i in range(1, 5)]}
                else:
                    content = {'rows': [{'i': row['opinion_id'], 's': 'a',
                        'm': [[f't{i}', {'quote_id': row['opinion_id']}] for i in range(1, 5)]} for row in body['opinions']]}
                result.choices[0].message.content = json.dumps(content, ensure_ascii=False)
            return result
        return response

    def budget(self, mode):
        return self.case.budget(self.respond(mode), self.case.policy(amount_limit=Decimal('0' if mode == 'budget' else '100')))

    def publish(self, mode='complete'):
        executed = self.case.execute(self.budget(mode))
        return publish_p6_execution(executed, self.case.prepared, settings=self.settings, **self.case.auth)

    def worker(self, mode='complete', **flags):
        settings = replace(self.settings, **flags)
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(settings, self.case.fixture.agent_identity)), \
             patch('data_analysis_agent.agents.review_budget.make_execution_budget', side_effect=lambda _: self.budget(mode)):
            return self.case.fixture.work('具体体现在哪里？', self.case.fixture.first['task_id'])


class P6DPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = PublicationFixture()
        self.addCleanup(self.fixture.close)
        self.c = self.fixture.case

    def test_complete_keeps_three_dimensions_all_denominators_and_true_evidence(self):
        result = self.fixture.publish()
        p = result['p6_publication']
        self.assertEqual((p['status'], p['parent_status'], p['complete']), ('ok', 'complete', True))
        self.assertEqual(p['required_aspects'], ['service', 'effectiveness', 'logistics'])
        self.assertEqual(p['D'], 6)
        for d in p['dimensions']:
            t = d['topics'][0]; ref = t['evidence'][0]
            self.assertEqual(t['sample_denominator'], d['S_a'])
            self.assertEqual(t['N_a_t'], d['S_a'])
            self.assertEqual(ref['text'][ref['quote_start']:ref['quote_end']], ref['quote'])
            self.assertEqual(ref['aspect'], d['aspect'])
        self.assertEqual(result['answer_generation']['attempts'], 0)

    def test_first_middle_last_failure_keeps_full_target_and_only_valid_success(self):
        for aspect in self.c.prepared.goal.required_aspects:
            with self.subTest(aspect=aspect):
                result = self.fixture.publish('fail_' + aspect)
                p = result['p6_publication']
                self.assertEqual(p['status'], 'partial')
                self.assertFalse(result['task_acceptance']['complete'])
                self.assertEqual(len(p['required_aspects']), 3)
                self.assertEqual(len(p['publishable_aspects']), 2)
                bad = next(d for d in p['dimensions'] if d['aspect'] == aspect)
                self.assertEqual(bad['status'], 'failed'); self.assertEqual(bad['topics'], [])
                self.assertTrue(bad['reason'])

    def test_two_complete_one_insufficient(self):
        p = self.fixture.publish('empty_effectiveness')['p6_publication']
        self.assertEqual(p['parent_status'], 'partial')
        self.assertEqual(p['dimensions'][1]['status'], 'insufficient_evidence')
        self.assertEqual(p['dimensions'][1]['topics'], [])

    def test_all_insufficient_does_not_invent_conclusions(self):
        result = self.fixture.publish('empty'); p = result['p6_publication']
        self.assertEqual((p['status'], p['parent_status']), ('unsupported', 'insufficient_evidence'))
        self.assertFalse(p['complete']); self.assertEqual(p['publishable_aspects'], [])
        self.assertTrue(all(not d['topics'] for d in p['dimensions']))
        self.assertNotIn('### ', result['answer'])

    def test_all_budget_skipped_is_error_and_keeps_all_reasons(self):
        p = self.fixture.publish('budget')['p6_publication']
        self.assertEqual((p['status'], p['parent_status']), ('error', 'skipped_budget'))
        self.assertTrue(all(d['status'] == 'skipped_budget' and not d['topics'] for d in p['dimensions']))
        self.assertEqual(self.c.calls, [])

    def test_each_dimension_has_three_topics_even_when_global_total_is_nine(self):
        p = self.fixture.publish('many')['p6_publication']
        self.assertEqual([len(d['topics']) for d in p['dimensions']], [3, 3, 3])
        self.assertEqual([d['omitted_topic_count'] for d in p['dimensions']], [1, 1, 1])
        self.assertTrue(all(d['truncation_reason'] == 'per_aspect_top3' for d in p['dimensions']))
        self.assertEqual(sum(len(t['evidence']) for d in p['dimensions'] for t in d['topics']), 9)
        self.assertIn('另有 1 个', p['answer_text'])

    def test_same_review_keeps_separate_aspect_opinion_and_topic_lineage(self):
        p = self.fixture.publish()['p6_publication']
        refs = [d['topics'][0]['evidence'][0] for d in p['dimensions'][1:]]
        self.assertEqual(refs[0]['review_id'], refs[1]['review_id'])
        self.assertNotEqual(refs[0]['opinion_id'], refs[1]['opinion_id'])
        self.assertNotEqual(refs[0]['topic_id'], refs[1]['topic_id'])

    def test_sample_S_differs_from_M_and_is_not_extrapolated(self):
        self.c.add_rows([{'parent_asin': cfixtures.independent.A, 'user_id': f'd-sample{i}',
            'text': f'服务问题{i}', 'timestamp': 1676160000000} for i in range(25)])
        p = self.fixture.publish()['p6_publication']; d = p['dimensions'][0]
        self.assertEqual((p['D'], d['M_a'], d['S_a']), (31, 30, 20))
        self.assertLess(d['review_coverage'], 1)
        self.assertEqual(d['topics'][0]['sample_denominator'], 20)
        self.assertIn('不是全量投诉率', p['answer_text'])

    def forged(self, mutate):
        value = json.loads(self.c.execute().payload_json)
        mutate(value)
        return P6ExecutedAnalysis(canonical_json(value), content_sha256(value))

    def test_scope_source_count_reference_and_frozen_inputs_cannot_be_resealed_to_publish(self):
        mutations = [
            lambda v: v['outcomes'][1]['results'][0]['data']['scope'].update(source='forged'),
            lambda v: v['outcomes'][1]['results'][0]['data']['items'][0].update(file_sha256='f'*64),
            lambda v: v['outcomes'][1]['results'][0]['data']['opinions'][0].update(quote_start=1),
            lambda v: v['outcomes'][1]['results'][0]['data']['topics'][0].update(review_count=999),
            lambda v: v['outcomes'][1]['results'][0]['data']['items'][0].update(text='forged'),
            lambda v: v['outcomes'][1].update(status='failed'),
            lambda v: v['outcomes'][1].update(model_attempts=0),
            lambda v: v['outcomes'].pop(),
            lambda v: v.update(goal={}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(P6InputIntegrityError):
                publish_p6_execution(self.forged(mutate), self.c.prepared, settings=self.fixture.settings, **self.c.auth)

    def test_cross_session_and_source_tampering_hard_fail_before_any_partial_publication(self):
        executed = self.c.execute()
        for auth in ({**self.c.auth, 'conversation_id': 'different'},
                     {**self.c.auth, 'agent_identity': {'model': 'other'}}):
            with self.assertRaises(P6InputIntegrityError):
                publish_p6_execution(executed, self.c.prepared, settings=self.fixture.settings, **auth)

    def test_client_complete_json_and_diagnostics_are_not_executable_results(self):
        for value in ({'complete': True}, self.c.execute().diagnostics(), json.loads(self.c.execute().payload_json)):
            with self.assertRaises(P6InputIntegrityError):
                publish_p6_execution(value, self.c.prepared, settings=self.fixture.settings, **self.c.auth)

    def test_publication_is_offline_and_never_reenters_compressor_or_classifier(self):
        executed = self.c.execute()
        with patch('data_analysis_agent.agents.review_topics.analyze_aspect_topics', side_effect=AssertionError('no classify')), \
             patch('data_analysis_agent.agents.review_compression.build_analysis_artifacts', side_effect=AssertionError('no old compressor')), \
             patch('data_analysis_agent.reviews.store.connect_review_database', side_effect=AssertionError('no SQL')):
            result = publish_p6_execution(executed, self.c.prepared, settings=self.fixture.settings, **self.c.auth)
        self.assertTrue(result['task_acceptance']['complete'])

    def test_three_server_gates_are_independent_and_default_publication_is_closed(self):
        for name in ('p6_preparation_enabled', 'p6_execution_enabled', 'p6_publication_enabled'):
            settings = replace(self.fixture.settings, **{name: False})
            with self.assertRaises(P6InputIntegrityError):
                publish_p6_execution(self.c.execute(), self.c.prepared, settings=settings, **self.c.auth)
        self.assertFalse(cfixtures.independent.SETTINGS.p6_publication_enabled)

    def test_real_worker_maps_complete_partial_unsupported_and_budget_fail_correctly(self):
        for mode, status, public_status in [('complete','succeeded','ok'), ('fail_service','partial','partial'),
                ('fail_effectiveness','partial','partial'), ('fail_logistics','partial','partial'),
                ('empty_effectiveness','partial','partial'), ('empty','succeeded','unsupported'), ('budget','failed','error')]:
            with self.subTest(mode=mode):
                task = self.fixture.worker(mode); result = task['result']
                self.assertEqual(task['status'], status, result.get('error'))
                self.assertEqual(result['status'], public_status)
                self.assertEqual(result['task_acceptance']['complete'], mode == 'complete')
                self.assertNotIn('complete', result)  # unsupported 不被 Worker 的顶层 complete=false 误判。
                self.assertEqual(result['answer_model_calls'], 0)
                self.assertEqual(result['intent_model_calls'], 0)
                self.assertIsNotNone(read_p6_publication(task))

    def test_worker_default_publication_barrier_is_unchanged(self):
        task = self.fixture.worker(p6_publication_enabled=False)
        self.assertEqual(task['result']['status'], 'unsupported')
        self.assertNotIn('p6_publication', task['result'])
        self.assertFalse(task['result']['p6_execution']['public_release_enabled'])

    def test_readback_requires_server_task_seal_and_never_trusts_saved_text_alone(self):
        task = self.fixture.worker(); self.assertIsNotNone(read_p6_publication(task))
        task['result']['p6_publication']['dimensions'][0]['topics'][0]['name'] = 'forged'
        self.assertIsNone(read_p6_publication(task))

    def test_global_publication_failure_worker_does_not_publish_any_successful_subset(self):
        original = P6ExecutedAnalysis.revalidate_for_publication
        def damaged(executed, prepared, **auth):
            value = json.loads(executed.payload_json)
            value['outcomes'][1]['results'][0]['data']['items'][0]['text'] = 'forged'
            forged = P6ExecutedAnalysis(canonical_json(value), content_sha256(value))
            return original(forged, prepared, **auth)
        with patch.object(P6ExecutedAnalysis, 'revalidate_for_publication', damaged):
            task = self.fixture.worker()
        self.assertEqual(task['status'], 'failed')
        self.assertNotIn('p6_publication', task['result'])
        self.assertNotIn('answer', task['result'])

    def test_public_read_route_revalidates_complete_partial_and_unsupported_without_IO(self):
        from data_analysis_agent.api.routes.public_analysis import create_router
        # 直接执行真实路由处理器；此套件禁止 socket，真实 HTTP 另由浏览器 E2E 验证。
        endpoint = create_router(self.c.fixture.tasks).routes[0].endpoint
        for mode in ('complete', 'fail_effectiveness', 'empty', 'budget'):
            task = self.fixture.worker(mode)
            view = endpoint(task['task_id'])['result']
            self.assertEqual(view['p6_publication'], task['result']['p6_publication'])
            self.assertEqual(view['public_answer']['status'], task['result']['status'])

    def test_publication_flag_alone_does_not_authorize_real_model_costs(self):
        from data_analysis_agent.agents import review_budget
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity', return_value=(self.fixture.settings, self.c.fixture.agent_identity)):
            task = self.c.fixture.work('具体体现在哪里？', self.c.fixture.first['task_id'])
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['result']['p6_execution']['budget']['attempts'], 0)
        self.assertEqual(task['result']['p6_publication']['parent_status'], 'skipped_budget')
        self.assertIsNone(review_budget.make_execution_budget(self.fixture.settings).authorization)


def generate_frontend_fixtures(output):
    fixture = PublicationFixture()
    try:
        tasks = {mode: fixture.worker(mode) for mode in ('complete', 'many', 'fail_service', 'fail_effectiveness',
            'fail_logistics', 'empty_effectiveness', 'empty', 'budget')}
        fixture.case.add_rows([{'parent_asin': cfixtures.independent.A, 'user_id': f'd-sample{i}',
            'text': f'服务问题{i}', 'timestamp': 1676160000000} for i in range(25)])
        tasks['sample'] = fixture.worker()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding='utf-8')
    finally: fixture.close()


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--fixtures': generate_frontend_fixtures(Path(sys.argv[2]))
    else: unittest.main()
