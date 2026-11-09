"""独立请求目标与计划/结果故障注入，全程合成数据和固定模型响应。"""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_review_dimension_content as dimensions
from data_analysis_agent.agents.review_acceptance import build_task_goal, validate_task_completion
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import opinion_request


class IndependentAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = dimensions.DimensionContentTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope()

    def run_local(self, question, **kwargs):
        return self.fixture.run_local(question, **kwargs)

    def rows(self, result):
        return [result['results'][key] for key in result['result_ids']]

    def goal(self, question, plan):
        return build_task_goal(question, self.scope, plan, {})

    def test_shared_router_semantic_failure_cannot_downgrade_theme_goal(self):
        question = '物流差评主要是在抱怨哪些事情？'
        wrong = build_query_plan('物流差评有多少条？', self.scope)
        semantic = {**opinion_request(question), 'theme_summary': False,
                    'content_detail': False, 'breakdown': False}
        with patch('data_analysis_agent.agents.review_acceptance.opinion_request', return_value=semantic, create=True) as router_semantic:
            goal = self.goal(question, wrong)
            router_semantic.assert_not_called()
        self.assertEqual(goal['levels'], ['themes'])
        counts = self.run_local('物流差评有多少条？')
        acceptance = validate_task_completion(goal, self.scope, wrong, self.rows(counts))
        self.assertEqual(acceptance['status'], 'failed')
        self.assertFalse(acceptance['complete'])
        self.assertFalse(acceptance['plan_check']['passed'])

    def test_request_snapshot_exists_before_injected_wrong_router_plan(self):
        question = '物流差评主要是在抱怨哪些事情？'
        wrong = build_query_plan('物流差评有多少条？', self.scope)
        def inject(question, scope, products, **kwargs):
            # 请求目标在调用 Router 前就已冻结，不可读取本次 plan 决定目标。
            self.assertTrue(captured)
            self.assertEqual(captured[0].levels, ('themes',))
            wrong.scope = deepcopy(scope)
            return scope, wrong, True
        captured = []
        from data_analysis_agent.agents import review_acceptance
        original = getattr(review_acceptance, 'build_request_goal', None)
        def capture(*args, **kwargs):
            goal = original(*args, **kwargs)
            captured.append(goal)
            return goal
        with patch.object(review_acceptance, 'build_request_goal', side_effect=capture, create=True), \
             patch('data_analysis_agent.agents.reviews.resolve_review_request', side_effect=inject):
            result = self.run_local(question)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['task_goal']['levels'], ['themes'])
        self.assertEqual(result['task_acceptance']['status'], 'failed')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertFalse(result['task_acceptance']['plan_check']['passed'])
        self.assertNotEqual(result['public_answer']['status'], 'ok')

    def test_evidence_goal_rejects_statistics_plan_and_actual_issues(self):
        wrong = build_query_plan('物流差评有多少条？', self.scope)
        goal = self.goal('给我三条物流差评原文。', wrong)
        counts = self.run_local('物流差评有多少条？')
        acceptance = validate_task_completion(goal, self.scope, wrong, self.rows(counts))
        self.assertEqual(acceptance['status'], 'failed')
        self.assertFalse(acceptance['plan_check']['passed'])
        correct = build_query_plan('给我三条物流差评原文。', self.scope)
        acceptance = validate_task_completion(goal, self.scope, correct, self.rows(counts))
        self.assertTrue(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')

    def test_statistics_goal_requires_actual_statistics_even_if_plan_is_satisfied(self):
        question = '物流差评有多少条？'
        content = self.run_local('物流差评具体内容说了什么？')
        wrong = build_query_plan(content['question'], self.scope)
        acceptance = validate_task_completion(self.goal(question, wrong), self.scope, wrong, self.rows(content))
        self.assertFalse(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')
        correct = build_query_plan(question, self.scope)
        counts = self.run_local(question)
        damaged = deepcopy(self.rows(counts))
        damaged[0]['data'].pop('issues')
        acceptance = validate_task_completion(self.goal(question, correct), self.scope, correct, damaged)
        self.assertTrue(acceptance['plan_check']['passed'])
        self.assertFalse(acceptance['complete'])
        self.assertEqual(acceptance['status'], 'failed')

    def test_correct_themes_and_statistics_pass_both_gates(self):
        for question in ('物流差评主要体现在哪里？', '物流差评有多少条？'):
            with self.subTest(question=question):
                result = self.run_local(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertTrue(result['task_acceptance']['plan_check']['passed'])
                self.assertTrue(result['task_acceptance']['complete'])

    def test_empty_themes_are_insufficient_not_failed_classification(self):
        result = self.run_local('物流差评主要体现在哪里？', scope=self.fixture.scope(date_from='2023-02-20'))
        self.assertEqual(result['results']['r1']['data']['analysis_status'], 'insufficient_data')
        self.assertEqual(result['status'], 'unsupported')
        acceptance = result['task_acceptance']
        self.assertTrue(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'insufficient_evidence')
        self.assertFalse(acceptance['complete'])
        self.assertFalse(result['public_answer']['highlights'])
        self.assertEqual(acceptance['evidence_status'], 'no_matching_opinions')

    def test_partial_classification_cannot_hide_behind_missing_citations(self):
        result = self.run_local('物流差评主要体现在哪里？')
        selected = deepcopy(self.rows(result))
        selected[0]['data'].update(analysis_status='partial', topics=[])
        selected[0]['data']['items'] = []
        plan = build_query_plan(result['question'], self.scope)
        acceptance = validate_task_completion(result['task_goal'], self.scope, plan, selected)
        self.assertTrue(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')
        self.assertFalse(acceptance['complete'])
        self.assertEqual(acceptance['evidence_status'], 'analysis_incomplete')

    def test_completed_classification_with_no_supported_topic_is_insufficient(self):
        from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch
        def unclassified(settings, prompt, payload, schema, timeout):
            if schema is TopicCatalog:
                return TopicCatalog(themes=[])
            return TopicBatch(assignments=[dict(opinion_id=row['opinion_id'], state='unclassified', matches=[])
                                           for row in payload['opinions']])
        with patch('test_review_dimension_content.exact_topic_response', side_effect=unclassified):
            result = self.run_local('物流差评主要体现在哪里？')
        self.assertEqual(result['results']['r1']['data']['analysis_status'], 'complete')
        self.assertGreater(result['results']['r1']['data']['total'], 0)
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['status'], 'unsupported')
        self.assertTrue(result['task_acceptance']['plan_check']['passed'])
        self.assertEqual(result['task_acceptance']['status'], 'insufficient_evidence')
        self.assertEqual(result['task_acceptance']['evidence_status'], 'no_supported_evidence')
        self.assertFalse(result['task_acceptance']['complete'])

    def test_tool_execution_failure_has_no_successful_business_acceptance(self):
        with patch('data_analysis_agent.agents.review_topics.analyze_aspect_topics', side_effect=TimeoutError('synthetic failure')):
            result = self.run_local('物流差评主要体现在哪里？')
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertTrue(result['task_acceptance']['plan_check']['passed'])
        self.assertEqual(result['task_acceptance']['evidence_status'], 'execution_failed')
        self.assertNotEqual(result['public_answer']['status'], 'ok')

    def test_existing_quote_location_validator_still_rejects_corruption(self):
        result = self.run_local('物流差评主要体现在哪里？')
        selected = deepcopy(self.rows(result))
        selected[0]['data']['items'][0]['opinions'][0]['quote_start'] = 99999
        plan = build_query_plan(result['question'], self.scope)
        acceptance = validate_task_completion(result['task_goal'], self.scope, plan, selected)
        self.assertTrue(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')
        self.assertTrue(any(row['name'] == '统计与引用校验' and not row['passed'] for row in acceptance['checks']))

    def test_valid_zero_statistics_can_pass_without_evidence(self):
        result = self.run_local('物流差评有多少条？', scope=self.fixture.scope(date_from='2023-02-20'))
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertTrue(result['task_acceptance']['plan_check']['passed'])
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['task_acceptance']['evidence_status'], 'not_requested')

    def test_plan_sentiment_cannot_override_explicit_user_sentiment(self):
        question = '物流好评占比是多少？'
        wrong = build_query_plan('物流差评占比是多少？', self.scope)
        counts = self.run_local('物流差评有多少条？')
        acceptance = validate_task_completion(self.goal(question, wrong), self.scope, wrong, self.rows(counts))
        self.assertFalse(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')

    def test_statistics_aspect_cannot_be_replaced_with_another_dimension(self):
        wrong = build_query_plan('客服差评有多少条？', self.scope)
        counts = self.run_local('客服差评有多少条？')
        acceptance = validate_task_completion(self.goal('物流差评有多少条？', wrong), self.scope, wrong, self.rows(counts))
        self.assertFalse(acceptance['plan_check']['passed'])
        self.assertEqual(acceptance['status'], 'failed')

    def test_goal_is_frozen_and_unchanged_by_different_compiled_plans(self):
        from dataclasses import FrozenInstanceError
        from data_analysis_agent.agents.review_acceptance import build_request_goal
        question = '物流差评主要是在抱怨哪些事情？'
        request = build_request_goal(question)
        with self.assertRaises(FrozenInstanceError):
            request.levels = ('statistics',)
        goals = [build_task_goal(question, self.scope, build_query_plan(q, self.scope), {}, request_goal=request)
                 for q in (question, '物流差评有多少条？', '给我三条物流差评原文。')]
        self.assertEqual(goals[0], goals[1])
        self.assertEqual(goals[0], goals[2])


if __name__ == '__main__':
    unittest.main()
