"""页面商品优先绑定、明确越界澄清及查询执行范围回归。"""

import hashlib
import unittest
from copy import deepcopy
from unittest.mock import patch

from test_agent import SETTINGS
import test_assistant_semantics as fixture
import test_review_intent as intent_fixture
from data_analysis_agent.agents.review_intent import ReviewIntent, resolve_review_request
from data_analysis_agent.agents.review_semantics import QuestionScopeError, product_mentions
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.store import get_task


class SelectedProductTests(fixture.AssistantSemanticsFixture, unittest.TestCase):
    queue = intent_fixture.ReviewIntentTests.queue
    enqueue = intent_fixture.ReviewIntentTests.enqueue
    save = intent_fixture.ReviewIntentTests.save
    work = intent_fixture.ReviewIntentTests.work
    semantic_model = intent_fixture.ReviewIntentTests.semantic_model

    def test_selected_product_without_name_executes_real_id(self):
        result = self.run_sync('好评主要集中在哪些方面？')
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['user_intent']['resolved_product_ids'], ['100013588053'])
        self.assertEqual(result['results']['r1']['data']['scope']['product_ids'], ['100013588053'])
        self.assertEqual(result['sentiment_intent'], 'positive')

    def test_alias_and_natural_language_prefix_preserve_cooling_positive_topic(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for question in ('美的风酷空调的制冷好评主要集中在哪些方面？',
                         '用户对于美的空调风酷的制冷好评主要集中在哪些方面？',
                         '消费者对于美的风酷空调的制冷好评主要集中在哪些方面？'):
            with self.subTest(question=question), patch('openai.OpenAI') as model:
                result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
            self.assertEqual(result['status'], 'ok', result.get('error'))
            self.assertEqual(result['query_plan']['required_tools'], ['get_opinion_breakdown'])
            self.assertEqual(result['results']['r1']['parameters'], {
                'product_ids': [], 'aspect': 'effectiveness', 'topic': '制冷', 'sentiment': 'positive'})
            self.assertEqual(result['results']['r1']['data']['scope']['product_ids'], ['100013588053'])
            self.assertEqual(result['results']['r1']['data']['total'], 2)
            self.assertEqual(result['validation']['status'], 'passed')
            model.assert_not_called()
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_other_product_requires_confirmation_before_any_query(self):
        for question in ('小米空调的好评主要是什么？', 'XIAOMI-TEST 的制冷好评集中在哪些方面？'):
            with self.subTest(question=question), patch('openai.OpenAI') as model:
                result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
            self.assertEqual(result['status'], 'unsupported')
            self.assertIn('确认切换', result['answer'])
            self.assertIn('XIAOMI-TEST', result['answer'])
            self.assertEqual(result['scope']['product_ids'], ['100013588053'])
            self.assertEqual(result['query_executions'], 0)
            model.assert_not_called()

    def test_unknown_product_or_series_never_falls_back(self):
        for name in ('外星牌空调风酷', '美的不存在系列空调', '外星牌冰箱'):
            result = run_review_agent(self.db, f'用户对于{name}的制冷好评主要集中在哪些方面？', scope=self.scope(), settings=SETTINGS)
            self.assertEqual(result['status'], 'unsupported')
            self.assertIn(name, result['answer'])
            self.assertIn('未找到', result['answer'])
            self.assertEqual(result['query_executions'], 0)

    def test_catalog_aliases_are_generic_and_multiple_selected_models_clarify(self):
        products = [dict(product_id='CAT-1', title='星河（Galaxy）1.5匹清风系列 新一级能效 变频冷暖 壁挂式空调挂机', brand='星河'),
                    dict(product_id='CAT-2', title='星河（Galaxy）3匹清风系列空调', brand='星河'),
                    dict(product_id='CAT-3', title='星河3匹晨风空调', brand='星河')]
        question = '用户对于星河空调清风的制冷好评主要集中在哪些方面？'
        self.assertEqual(product_mentions(question, products), ['星河空调清风'])
        scope, plan, _ = resolve_review_request(question, self.scope(product_ids=['CAT-1']), products, settings=SETTINGS)
        self.assertEqual(scope['product_ids'], ['CAT-1'])
        self.assertEqual(plan.requirements[0]['parameters']['topic'], '制冷')
        with self.assertRaisesRegex(QuestionScopeError, 'CAT-1.*CAT-2'):
            resolve_review_request(question, self.scope(product_ids=['CAT-1', 'CAT-2']), products, settings=SETTINGS)
        with self.assertRaisesRegex(QuestionScopeError, '未找到'):
            resolve_review_request('星河不存在系列空调的制冷好评有哪些？', self.scope(product_ids=['CAT-1']), products, settings=SETTINGS)

    def test_multiple_selection_keeps_all_or_narrows_explicit_target(self):
        products = [dict(product_id='A', title='星河清风空调', brand='星河'), dict(product_id='B', title='晨光明月空调', brand='晨光')]
        selection = self.scope(product_ids=['A', 'B'])
        scope, _, _ = resolve_review_request('好评主要集中在哪些方面？', selection, products, settings=SETTINGS)
        self.assertEqual(scope['product_ids'], ['A', 'B'])
        scope, _, _ = resolve_review_request('星河清风空调的好评主要集中在哪些方面？', selection, products, settings=SETTINGS)
        self.assertEqual(scope['product_ids'], ['A'])
        self.assertEqual(selection['product_ids'], ['A', 'B'])

    def test_explicit_comparison_enters_comparison_instead_of_current_product(self):
        result = run_review_agent(self.db, '对比美的空调与小米空调的差评', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['100013588053', 'XIAOMI-TEST'])
        self.assertEqual(result['query_plan']['required_tools'], ['compare_products'])
        self.assertEqual(result['validation']['status'], 'passed')

    def test_comparison_never_discards_explicit_sentiment_or_topic(self):
        for question in ('对比这两款商品的好评差别', '对比美的空调与小米空调的制冷好评'):
            result = run_review_agent(self.db, question, scope=self.scope(product_ids=['100013588053', 'XIAOMI-TEST']), settings=SETTINGS)
            self.assertEqual(result['status'], 'unsupported')
            self.assertEqual(result['query_executions'], 0)

    def test_three_turns_inherit_product_sentiment_and_cooling(self):
        self.queue()
        first = self.run_sync('好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        second = self.work('制冷方面呢？', parent=parent)
        third = self.work('这些好评具体反映什么？', parent=second['task_id'])
        for task in (second, third):
            self.assertEqual(task['result']['status'], 'ok', task.get('error'))
            self.assertEqual(task['result']['results']['r1']['parameters'], {
                'product_ids': ['100013588053'], 'aspect': 'effectiveness', 'topic': '制冷', 'sentiment': 'positive'})

    def test_manual_switch_rejects_old_context_and_preserves_historical_scope(self):
        self.queue()
        first = self.run_sync('好评主要集中在哪些方面？')
        parent = self.save(first['question'], first)
        historical = deepcopy(get_task(self.tasks, parent))
        changed = self.scope(product_ids=['XIAOMI-TEST'])
        context = load_analysis_context(self.tasks, {**changed, 'context_task_id': parent, 'conversation_id': 'conversation-A'})
        self.assertIsNone(context)
        result = run_review_agent(self.db, '制冷好评主要集中在哪些方面？', scope=changed, settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['results']['r1']['data']['scope']['product_ids'], ['XIAOMI-TEST'])
        self.assertEqual(get_task(self.tasks, parent), historical)
        for changes in ({'dataset_version': 'other'}, {'model': 'other'}, {'prompt_version': 'other'}):
            self.assertIsNone(load_analysis_context(self.tasks, {**self.scope(), **changes, 'context_task_id': parent, 'conversation_id': 'conversation-A'}))

    def test_failed_unconfirmed_switch_cannot_change_followup_product(self):
        self.queue()
        first = self.run_sync('好评主要集中在哪些方面？')
        old = self.save(first['question'], first)
        failed = self.save('小米空调的差评主要是什么？', {'agent': True, 'status': 'error'}, status='failed', parent=old)
        task = self.work('制冷方面呢？', parent=failed)
        self.assertEqual(task['result']['status'], 'unsupported')
        self.assertIn('确认切换', task['result']['answer'])
        self.assertEqual(task['result']['query_executions'], 0)

    def test_invalid_selected_id_is_not_repaired_by_question_or_expanded_to_all(self):
        for ids in (['missing'], ['']):
            result = run_review_agent(self.db, '美的空调的制冷好评有哪些？', scope=self.scope(product_ids=ids), settings=SETTINGS)
            self.assertIn(result['status'], ('error', 'unsupported'))
            self.assertEqual(result['query_executions'], 0)

    def test_model_cannot_narrow_verified_ids_using_a_historical_mention(self):
        scope = self.scope(product_ids=['100013588053', 'XIAOMI-TEST'])
        context = {'users': [{'question': '美的空调的好评主要是什么？'}]}
        intent = ReviewIntent(query_type='aspect_summary', sentiment='positive', product_mentions=['美的空调'])
        with self.semantic_model(intent.model_dump()):
            result = run_review_agent(self.db, '哪块让买家买账呀', scope=scope, settings=SETTINGS, conversation_context=context)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['query_executions'], 0)
