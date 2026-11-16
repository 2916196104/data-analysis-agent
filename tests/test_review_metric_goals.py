"""Regression for requested denominators, plural dimensions and time-window entities."""
from copy import deepcopy
import json
import unittest

from test_review_positive_trend import PositiveTrendTests
from test_review_unknown_product_id import PRODUCTS, SCOPE
from test_agent import SETTINGS
from data_analysis_agent.agents.review_acceptance import build_request_goal, build_task_goal, validate_goal_plan
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.agents.review_semantics import product_mentions, QuestionScopeError
from data_analysis_agent.agents.reviews import run_review_agent


class MetricGoalTests(unittest.TestCase):
    def setUp(self):
        self.f = PositiveTrendTests('test_monthly_counts_denominators_nulls_scope_and_public_history')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def test_success_denominator_is_selected_and_retained_in_answer_and_history(self):
        for question in ('按月分析物流好评占成功标注评论的比例趋势。',
                         '以成功标注评论数为分母，按月分析物流好评占比趋势。'):
            with self.subTest(question=question):
                result = self.f.run_positive(question)
                point = result['compact_context']['statistics'][0]['trends'][0]['points'][0]['metric']
                self.assertEqual((point['numerator'], point['denominator'], point['denominator_name']), (1, 3, 'successful_reviews'))
                self.assertAlmostEqual(point['value'], 1/3, places=6)
                answer = result['public_answer']
                text = answer['summary'] + ''.join(row['text'] for row in answer['highlights'])
                self.assertIn('分母 3 条成功标注评论，占比 33.3%', text)
                self.assertEqual(read_public_answer({'status': 'succeeded', 'result': result}), answer)

    def test_compiled_default_cannot_satisfy_explicit_success_denominator(self):
        question = '按月分析物流好评占成功标注评论的比例趋势。'
        goal = build_task_goal(question, self.f.scope, None, {}, request_goal=build_request_goal(question))
        wrong = build_query_plan('按月分析物流好评占比趋势。', self.f.scope)
        self.assertFalse(validate_goal_plan(goal, wrong)['passed'])
        wrapper = '执行本轮已确认的只读查询\n' + json.dumps({'tool':'get_aspect_trend',
            'parameters': {'aspect':'logistics', 'period':'month', 'sentiment':'positive'}, 'question':question})
        result = run_review_agent(self.f.db, wrapper, scope=self.f.scope, settings=SETTINGS)
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(result['query_executions'], 0)

    def test_plural_trends_publish_every_dimension_with_separate_sources(self):
        for question, aspects in [('按月查看包装和物流问题变化', {'packaging','logistics'}),
            ('按月分析物流和客服好评占比趋势。', {'logistics','service'}),
            ('按月分析包装和物流差评占比趋势。', {'packaging','logistics'})]:
            with self.subTest(question=question):
                result = self.f.run_positive(question)
                self.assertEqual({s['trends'][0]['aspect'] for s in result['compact_context']['statistics']}, aspects)
                self.assertEqual(len(result['public_answer']['highlights']), 2)
                self.assertEqual(len(result['public_answer']['source_result_ids']), 2)
                self.assertEqual(read_public_answer({'status':'succeeded','result':result}), result['public_answer'])
                plan = build_query_plan(question, self.f.scope)
                plan.requirements.pop()
                self.assertFalse(validate_goal_plan(result['task_goal'], plan)['passed'])

    def test_fine_topics_and_unclassified_all_reviews_ratio_remain_blocked(self):
        for question in ('客服不耐烦的反馈占全部评论多少？', '物流配送迟缓的负面评论比例？',
                         '按月分析物流好评占全部评论比例趋势。'):
            result = run_review_agent(self.f.db, question, scope=self.f.scope, settings=SETTINGS)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['query_executions'], 0)
            self.assertEqual(result['total_model_calls'], 0)

    def test_window_words_are_not_products_and_unknown_ids_still_block(self):
        for question in ('对比当前窗口与基线的物流好评占比。',
                         '比较当前窗口与基线窗口的物流好评占比。'):
            self.assertEqual(product_mentions(question, PRODUCTS), [])
            _, plan, _ = resolve_review_request(question, SCOPE, PRODUCTS, settings=SETTINGS)
            self.assertIsNone(plan.unsupported_reason)
            self.assertEqual(plan.required_tools, ['compare_time_windows'])
            self.assertEqual(plan.requirements[0]['parameters'], {'sentiment': 'positive', 'aspect': 'logistics'})
        for question in ('对比商品 XYZ-ABC123 与基线的物流差评', '比较基线空调的差评'):
            with self.assertRaises(QuestionScopeError):
                resolve_review_request(question, SCOPE, PRODUCTS, settings=SETTINGS)
        catalog = [dict(product_id='100013588055', title='基线', brand='测试')]
        self.assertEqual(product_mentions('基线的物流好评', catalog), ['基线'])

    def test_multidimension_with_success_denominator_ignores_previous_single_dimension(self):
        previous = self.f.run_positive()
        context = {'user': {'task_id':'previous','question':previous['question']},
            'users':[{'task_id':'previous','question':previous['question']}],
            'validated':{'task_id':'previous','scope':previous['scope'],'sentiment':'positive',
                'analysis_type':'aspect_trend','queries':[{'kind':'aspect_trend','parameters':previous['results']['r1']['parameters']}]}}
        result = self.f.run_positive('按月分析物流和客服好评占成功标注评论的比例趋势。', context=context)
        self.assertEqual(len(result['result_ids']), 2)
        self.assertTrue(all(s['trends'][0]['points'][0]['metric']['denominator_name']=='successful_reviews'
                            for s in result['compact_context']['statistics']))

    def test_explicit_denominator_survives_day_followup_and_tampering_is_rejected(self):
        first = self.f.run_positive('按月分析物流好评占成功标注评论的比例趋势。')
        context = {'user': {'task_id':'p','question':first['question']}, 'users':[],
            'validated':{'task_id':'p','scope':first['scope'],'analysis_type':'aspect_trend','sentiment':'positive',
                'queries':[{'kind':'aspect_trend','parameters':first['results']['r1']['parameters']}]}}
        daily = self.f.run_positive('改为按日', context=context)
        self.assertEqual(daily['results']['r1']['data']['rate_denominator'], 'successful_reviews')
        forged = deepcopy(first['results']); forged['r1']['data']['rate_denominator'] = 'positive_review_count'
        self.assertEqual(validate_report(forged, first['result_ids'], sentiment_intent='positive',
            query_plan=build_query_plan(first['question'], self.f.scope),
            expected_scopes=expected_result_scopes(forged, self.f.scope))['status'], 'failed')

    def test_singular_goal_rejects_extra_or_replaced_dimension(self):
        question = '按月分析物流好评占比趋势。'
        goal = build_task_goal(question, self.f.scope, None, {}, request_goal=build_request_goal(question))
        for changed in ('按月分析客服好评占比趋势。', '按月分析物流和客服好评占比趋势。'):
            self.assertFalse(validate_goal_plan(goal, build_query_plan(changed, self.f.scope))['passed'])

    def test_conflicting_denominators_are_explained_without_queries(self):
        result = run_review_agent(self.f.db, '按月分析物流好评占成功标注评论的比例，以正向观点评论数为分母。',
            scope=self.f.scope, settings=SETTINGS)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['total_model_calls'], 0)
        self.assertIn('多个分母', result['public_answer']['summary'])
