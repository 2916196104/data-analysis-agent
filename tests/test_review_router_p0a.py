"""P0-A 路由回归：固定范围，禁止网络/模型，无数据库。"""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import IDENTITY
import test_review_dimension_content as dimension_fixture
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import opinion_request


SCOPE = {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'router-p0a',
         'product_ids': ['100013588055'], 'date_from': '2021-06-01', 'date_to': '2021-06-30',
         'baseline_from': '2021-05-01', 'baseline_to': '2021-05-31', 'as_of': None}
PRODUCTS = [{'product_id': '100013588055', 'title': '美的空调', 'brand': '美的'}]
# id, question, aspect, tool, themes, content, examples, breakdown
CASES = [
    ('A', '物流差评主要体现在哪里？', 'logistics', 'get_aspect_drilldown', True, True, False, True),
    ('B', '物流差评主要是在抱怨哪些事情？', 'logistics', 'get_aspect_drilldown', True, True, False, True),
    ('C', '物流差评都在吐槽些什么？', 'logistics', 'get_aspect_drilldown', True, True, False, True),
    ('D', '大家对物流有哪些不满意的地方？', 'logistics', 'get_aspect_drilldown', True, True, False, True),
    ('E', '客服差评具体涉及哪些问题？', 'service', 'get_aspect_drilldown', True, True, False, True),
    ('F', '物流差评有多少条？', 'logistics', 'get_top_issues', False, False, False, False),
    ('G', '客服差评占比是多少？', 'service', 'get_top_issues', False, False, False, False),
    ('H', '给我三条物流差评原文。', 'logistics', 'get_evidence', False, False, True, False),
    ('I', '差评主要集中在哪些维度？', None, 'get_top_issues', False, False, False, False),
    ('J', '物流差评主要有哪些问题，各自涉及多少评论？', 'logistics', 'get_aspect_drilldown', True, True, False, True),
]
KINDS = {'get_aspect_drilldown': ('aspect_drilldown', 'aspect_drilldown'),
         'get_top_issues': ('issues', 'aspect_summary'), 'get_evidence': ('evidence', 'evidence')}


class RouterP0ATests(unittest.TestCase):
    def resolve(self, question, **kwargs):
        diagnostics, intent = {}, {}
        with patch('openai.OpenAI', side_effect=AssertionError('offline only')), \
             patch('data_analysis_agent.agents.review_intent._llm_intent', side_effect=AssertionError('no intent model')):
            bound, plan, _ = resolve_review_request(question, deepcopy(SCOPE), PRODUCTS,
                settings=replace(SETTINGS, semantic_parser_enabled=True),
                diagnostics=diagnostics, intent_state=intent, **kwargs)
        self.assertEqual(diagnostics['model_calls'], 0)
        self.assertEqual(bound, SCOPE)
        return plan, intent

    def test_a_to_j_semantics(self):
        for key, question, aspect, tool, themes, content, examples, breakdown in CASES:
            with self.subTest(case=key, question=question):
                semantic = opinion_request(question)
                self.assertEqual(semantic['aspect'], aspect)
                self.assertEqual((semantic['theme_summary'], semantic['content_detail'],
                                  semantic['examples'], semantic['breakdown']),
                                 (themes, content, examples, breakdown))

    def test_a_to_j_planner(self):
        for key, question, aspect, tool, *_ in CASES:
            with self.subTest(case=key, question=question):
                plan = build_query_plan(question, deepcopy(SCOPE))
                self.assertIsNone(plan.unsupported_reason)
                self.assertEqual(plan.required_tools, [tool])
                self.assertEqual(plan.analysis_type, KINDS[tool][0])
                self.assertEqual(plan.sentiment, 'negative')
                self.assertEqual(plan.requirements[0]['parameters'].get('aspect'), aspect)
                self.assertEqual('get_top_issues' in plan.required_tools, tool == 'get_top_issues')
                self.assertEqual(plan.scope, SCOPE)

    def test_a_to_j_router(self):
        for key, question, aspect, tool, *_ in CASES:
            with self.subTest(case=key, question=question):
                plan, intent = self.resolve(question)
                self.assertIsNone(plan.unsupported_reason)
                self.assertEqual(plan.required_tools, [tool])
                self.assertEqual(plan.analysis_type, KINDS[tool][0])
                self.assertEqual(intent['query_type'], KINDS[tool][1])
                self.assertEqual(intent['aspect'], aspect)
                self.assertEqual(intent['sentiment'], 'negative')

    def test_equivalent_theme_phrasings_have_identical_requirements(self):
        questions = [row[1] for row in CASES if row[2] == 'logistics' and row[3] == 'get_aspect_drilldown']
        questions += ['物流差评具体表现是什么？', '物流差评主要抱怨哪些事情？',
                      '物流差评具体有哪些问题？', '物流差评主要问题集中在哪些环节？']
        expected = build_query_plan(questions[0], SCOPE).requirements
        for question in questions:
            with self.subTest(question=question):
                plan, _ = self.resolve(question)
                self.assertEqual(plan.requirements, expected)

    def test_raw_opinion_grouping_and_metric_manifestation_keep_existing_tools(self):
        for question, expected in [
            ('物流差评的原始观点内容按文本分组', 'get_opinion_breakdown'),
            ('物流差评具体内容说了什么？', 'get_opinion_breakdown'),
            ('物流差评占比表现如何？', 'get_top_issues'),
            ('物流好评数量表现如何？', 'get_positive_aspects'),
            ('差评主要有哪些维度存在问题？', 'get_top_issues'),
            ('这个商品主要有哪些问题？', 'get_top_issues'),
        ]:
            with self.subTest(question=question):
                plan, _ = self.resolve(question)
                self.assertEqual(plan.required_tools, [expected])

    def test_followup_themes_inherit_scope_aspect_and_sentiment(self):
        context = {'validated': {'scope': deepcopy(SCOPE), 'sentiment': 'negative',
            'analysis_type': 'issues', 'queries': [{'kind': 'issues',
                'parameters': {'product_ids': SCOPE['product_ids'], 'aspect': 'logistics'}}]}}
        for question in ('主要抱怨哪些事情？', '都在吐槽些什么？', '物流差评主要有哪些问题，各自涉及多少评论？'):
            with self.subTest(question=question):
                plan, intent = self.resolve(question, context=context)
                self.assertEqual(plan.required_tools, ['get_aspect_drilldown'])
                self.assertEqual((intent['aspect'], intent['sentiment']), ('logistics', 'negative'))
                self.assertEqual(plan.requirements[0]['parameters']['product_ids'], SCOPE['product_ids'])

    def test_content_precheck_repairs_legal_but_wrong_statistics_plan(self):
        wrong = build_query_plan('物流差评有多少条？', SCOPE)
        def stale_rules(question, scope, **kwargs):
            if question.startswith('执行本轮已确认的只读查询'):
                return build_query_plan(question, scope, **kwargs)
            return deepcopy(wrong)
        for question, tool in [('物流差评主要是在抱怨哪些事情？', 'get_aspect_drilldown'),
                               ('物流差评具体内容说了什么？', 'get_opinion_breakdown')]:
            with self.subTest(question=question), patch(
                    'data_analysis_agent.agents.review_intent.build_query_plan', side_effect=stale_rules):
                plan, intent = self.resolve(question)
                self.assertEqual(plan.required_tools, [tool])
                self.assertEqual(intent['query_type'], tool.removeprefix('get_'))

    def test_out_of_scope_mixed_theme_requests_are_explicitly_unsupported(self):
        context = {'validated': {'scope': deepcopy(SCOPE), 'sentiment': 'negative',
            'queries': [{'kind': 'issues', 'parameters': {'aspect': 'logistics'}}]}}
        for question in ('物流和客服差评主要抱怨哪些事情？',
                         '物流差评主要抱怨哪些事情，并按月看趋势？',
                         '物流差评主要有哪些问题，并查看标注覆盖率？'):
            for history in (None, context):
                with self.subTest(question=question, history=bool(history)):
                    plan, _ = self.resolve(question, context=history)
                    self.assertTrue(plan.unsupported_reason)
                    self.assertEqual(plan.required_tools, [])

    def test_missing_theme_sentiment_asks_for_direction(self):
        plan, _ = self.resolve('物流具体表现是什么？')
        self.assertIn('好评还是差评', plan.unsupported_reason)
        self.assertEqual(plan.required_tools, [])


class RouterP0AIntegrationTests(unittest.TestCase):
    # 复用隔离评论夹具，主题分类用固定响应，不调用模型或生产库。
    def setUp(self):
        fixture = dimension_fixture.DimensionContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.db, self.scope, self.run_local = fixture.db, fixture.scope, fixture.run_local

    def test_agent_executes_a_to_j_without_intent_or_planning_model(self):
        import hashlib
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for key, question, aspect, tool, *_ in CASES:
            with self.subTest(case=key, question=question):
                result = self.run_local(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(result['query_plan']['required_tools'], [tool])
                self.assertEqual(result['analysis_intent'], KINDS[tool][0])
                self.assertEqual(result['user_intent']['query_type'], KINDS[tool][1])
                self.assertEqual(result['sentiment_intent'], 'negative')
                self.assertEqual(result['intent_resolution']['model_calls'], 0)
                self.assertEqual(result['model_calls'], 0)
                self.assertEqual(result['answer_model_calls'], 0)
                self.assertEqual(result['topic_model_calls'], 2 if tool == 'get_aspect_drilldown' else 0)
                self.assertTrue(result['task_acceptance']['complete'])
                self.assertEqual(result['query_plan']['scope'], result['scope'])
                for field, value in self.scope().items():
                    self.assertEqual(result['scope'][field], value, field)
                for row in result['results'].values():
                    for field in ('source', 'dataset_version', 'model', 'base_url', 'prompt_version',
                                  'product_ids', 'date_from', 'date_to', 'as_of'):
                        self.assertEqual(row['data']['scope'][field], result['scope'][field], field)
                data = result['results']['r1']['data']
                if tool == 'get_aspect_drilldown':
                    self.assertEqual((data['aspect'], data['sentiment']), (aspect, 'negative'))
                    self.assertEqual(data['topics'][0]['review_count'], 2)
                    self.assertTrue(data['topics'][0]['review_ids'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
