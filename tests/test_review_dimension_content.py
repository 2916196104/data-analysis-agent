"""六维度内容下钻、正负隔离、一次补查和失败发布保护；全程合成数据。"""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
import test_review_intent as intent_fixture
from scripts.insight_evidence_cases import EvidenceFixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_evidence import assess_evidence
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.store import claim_task, finish_task
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.labeling import validate_opinions
from data_analysis_agent.reviews import analytics
from topic_fixture import exact_topic_response


CASES = [('客服方面有什么问题？', 'service', 'negative'),
         ('物流主要被投诉什么？', 'logistics', 'negative'),
         ('使用效果具体差在哪里？', 'effectiveness', 'negative'),
         ('包装差评主要反映哪些问题？', 'packaging', 'negative'),
         ('商品质量方面的负面评价有哪些？', 'quality', 'negative'),
         ('价格方面用户主要不满意什么？', 'price', 'negative'),
         ('客服好评具体体现在哪些方面？', 'service', 'positive'),
         ('用户主要称赞使用效果的哪些体验？', 'effectiveness', 'positive'),
         ('客服主要被投诉什么', 'service', 'negative'),
         ('客服主要在抱怨什么', 'service', 'negative'),
         ('物流差评主要体现在哪里？', 'logistics', 'negative'),
         ('给出负面的物流观点的体现在哪里', 'logistics', 'negative'),
         ('物流负面评价主要表现在哪些方面？', 'logistics', 'negative'),
         ('包装差评体现在哪些地方？', 'packaging', 'negative'),
         ('商品质量差评主要表现在哪些方面？', 'quality', 'negative')]


class DimensionContentTests(WorkbenchFixture, unittest.TestCase):
    queue = intent_fixture.ReviewIntentTests.queue
    enqueue = intent_fixture.ReviewIntentTests.enqueue
    save_task = intent_fixture.ReviewIntentTests.save
    work = intent_fixture.ReviewIntentTests.work

    def scope(self, **changes):
        return {**IDENTITY, 'source': 'amazon_reviews_2023', 'dataset_version': 'dimension-fixture',
                'product_ids': ['DIM-A'], 'date_from': '2023-02-01', 'date_to': '2023-02-28',
                'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31', **changes}

    def setUp(self):
        super().setUp()
        self.quotes = {'service': ('客服回复慢', '客服解答清楚'), 'quality': ('外壳易裂', '做工牢固'),
                       'effectiveness': ('制冷慢', '制冷快'), 'packaging': ('包装破损', '包装严实'),
                       'logistics': ('送货迟到', '送货准时'), 'price': ('价格偏高', '价格合适')}
        rows = []
        for product in ('DIM-A', 'DIM-B'):
            for index in range(4):
                text = '。'.join(pair[index % 2] for pair in self.quotes.values())
                if product == 'DIM-B':
                    text = '另一商品' + text
                rows.append({'parent_asin': product, 'user_id': str(index), 'text': text, 'rating': 3,
                             'timestamp': int(datetime(2023, 2, 10, tzinfo=timezone.utc).timestamp() * 1000)})
        source = self.root / 'dimensions.jsonl'
        source.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
        import_amazon_jsonl(self.db, reviews_path=source, dataset_version='dimension-fixture')
        for review in pending_reviews(self.db, **IDENTITY, dataset_version='dimension-fixture', limit=100):
            opinions = [{'aspect': aspect, 'sentiment': 'positive' if pair[1] in review['text'] else 'negative',
                         'quote': pair[1] if pair[1] in review['text'] else pair[0]} for aspect, pair in self.quotes.items()]
            save_annotation(self.db, review, **IDENTITY, status='success', opinions=validate_opinions(json.dumps({'opinions': opinions}), review['text']))

    def run_local(self, question, *, scope=None, context=None, settings=SETTINGS):
        with patch('openai.OpenAI', side_effect=AssertionError('no external calls')), \
             patch('data_analysis_agent.agents.review_topics._call', side_effect=exact_topic_response), \
             patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planning model')):
            return run_review_agent(self.db, question, scope=scope or self.scope(), settings=replace(settings, semantic_parser_enabled=False), conversation_context=context)

    def explicit(self, question):
        return '执行本轮已确认的只读查询\n' + json.dumps({'tool': 'get_top_issues', 'parameters': {'aspect': 'service'}, 'question': question})

    def test_all_dimensions_and_sentiments_without_history_use_local_content(self):
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for question, aspect, sentiment in CASES:
            with self.subTest(question=question):
                result = self.run_local(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                themes = {'物流差评主要体现在哪里？', '给出负面的物流观点的体现在哪里',
                          '物流负面评价主要表现在哪些方面？', '包装差评体现在哪些地方？',
                          '商品质量差评主要表现在哪些方面？'}
                expected = 'aspect_drilldown' if question in themes or (sentiment == 'positive' and '制冷' not in question) else 'opinion_breakdown'
                self.assertEqual(result['query_plan']['required_tools'], ['get_' + expected])
                self.assertEqual(result['user_intent']['query_type'], expected)
                data = result['results']['r1']['data']
                self.assertEqual((data['aspect'], data['sentiment'], data['total']), (aspect, sentiment, 2))
                self.assertEqual(data['topics' if expected == 'aspect_drilldown' else 'groups'][0]['review_count'], 2)
                self.assertTrue(all(opinion['sentiment'] == sentiment and opinion['aspect'] == aspect
                                    for item in data['items'] for opinion in item['opinions']))
                answer = result['public_answer']
                self.assertIn(self.quotes[aspect][sentiment == 'positive'], answer['summary'])
                self.assertIn('分母 2 条', answer['highlights'][0]['text'])
                self.assertEqual(result['answer_generation']['attempts'], 0)
                self.assertEqual(result['topic_model_calls'], 2 if expected == 'aspect_drilldown' else 0)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), digest)

    def test_a_queue_followup_uses_parent_and_content_query(self):
        self.queue()
        first = self.run_local('执行本轮已确认的只读查询\n' + json.dumps({'tool': 'get_top_issues', 'parameters': {}}))
        parent = self.save_task('差评主要集中在什么地方？', first)
        second = self.work('客服方面有什么问题？', parent=parent)
        self.assertEqual(second['status'], 'succeeded', second.get('error'))
        result = second['result']
        self.assertEqual(result['user_intent']['sub_intent'], 'drill_down')
        self.assertEqual(result['query_plan']['required_tools'], ['get_opinion_breakdown'])
        self.assertEqual(result['scope']['product_ids'], ['DIM-A'])
        self.assertIn('客服回复慢', result['public_answer']['summary'])
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=exact_topic_response):
            positive = self.work('客服好评具体体现在哪些方面？', parent=second['task_id'])
        self.assertEqual(positive['status'], 'succeeded', positive.get('error'))
        self.assertEqual(positive['result']['results']['r1']['data']['sentiment'], 'positive')
        self.assertIn('客服解答清楚', positive['result']['public_answer']['summary'])
        self.assertNotIn('客服回复慢', json.dumps(positive['result']['public_answer'], ensure_ascii=False))

    def test_content_followup_keeps_resolved_product_and_explicit_all_is_respected(self):
        self.queue()
        selection = self.scope(product_ids=['DIM-A', 'DIM-B'])
        question = 'DIM-A物流差评有多少条？'
        first = self.run_local(question, scope=selection)
        self.assertEqual(first['scope']['product_ids'], ['DIM-A'])
        parent = self.enqueue(question, product_ids=selection['product_ids'])
        claim_task(self.tasks, self.worker.worker_id)
        finish_task(self.tasks, parent, self.worker.worker_id, status='succeeded', result=first)
        context = load_analysis_context(self.tasks, {**selection, 'context_task_id': parent,
                                                    'conversation_id': 'conversation-A'})
        self.assertEqual(context['validated']['scope']['product_ids'], ['DIM-A'])
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for question in ('给出负面的物流观点的体现在哪里', '具体体现在哪里？', '主要表现在哪些方面？'):
            with self.subTest(question=question):
                result = self.run_local(question, scope=selection, context=context)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(result['scope']['product_ids'], ['DIM-A'])
                query = result['results']['r1']
                self.assertEqual((query['kind'], query['data']['aspect'], query['data']['sentiment']),
                                 ('aspect_drilldown', 'logistics', 'negative'))
                self.assertTrue(all(row['product_id'] == 'DIM-A' for row in query['data']['items']))
                self.assertIn('送货迟到', result['public_answer']['summary'])
                self.assertNotIn('送货准时', result['public_answer']['summary'])
                self.assertEqual(result['topic_model_calls'], 2)
                self.assertTrue(result['task_acceptance']['complete'])
        overall = self.run_local('所有商品的物流差评都在说什么？', scope=selection, context=context)
        self.assertEqual(overall['status'], 'ok', overall.get('error'))
        self.assertEqual(set(overall['scope']['product_ids']), {'DIM-A', 'DIM-B'})
        self.assertEqual(overall['results']['r1']['data']['total'], 4)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_content_goal_cannot_finish_with_only_counts(self):
        question = '执行本轮已确认的只读查询\n' + json.dumps({'tool': 'get_top_issues',
            'parameters': {'aspect': 'logistics'}, 'question': '物流差评主要体现在哪里？'})
        result = self.run_local(question)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['evidence_completion']['status'], 'not_started')
        self.assertFalse(result['task_plan_check']['passed'])
        self.assertEqual(result['query_executions'], 0)
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertNotEqual(result['public_answer']['status'], 'ok')
        with patch.object(analytics, 'get_opinion_breakdown', side_effect=TimeoutError('offline failure')):
            failed = self.run_local(question)
        self.assertEqual(failed['status'], 'error')
        self.assertNotEqual(failed['public_answer']['status'], 'ok')

    def test_d_changed_product_or_conversation_cannot_inherit_parent(self):
        self.queue()
        first = self.run_local('客服好评具体体现在哪些方面？')
        parent = self.save_task('客服好评具体体现在哪些方面？', first)
        for changes in ({'product_ids': ['DIM-B']}, {'conversation_id': 'other'}):
            params = {**self.scope(), 'conversation_id': 'conversation-A', 'context_task_id': parent, **changes}
            self.assertIsNone(load_analysis_context(self.tasks, params))
        second = self.run_local('客服方面有什么问题？', scope=self.scope(product_ids=['DIM-B']))
        self.assertEqual(second['results']['r1']['data']['sentiment'], 'negative')
        self.assertTrue(all(row['product_id'] == 'DIM-B' for row in second['results']['r1']['data']['items']))

    def test_e_statistics_only_is_insufficient_and_does_not_invent_content(self):
        with patch.object(self, 'scope', return_value=self.scope()):
            snap = EvidenceFixture.snapshot(self, '客服方面有什么问题？', ['DIM-A'], [{'tool': 'get_top_issues', 'parameters': {'aspect': 'service'}}])
        context = CompactAnalysisContext.model_validate(snap['compact_context'])
        assessment = assess_evidence(context)
        self.assertEqual(assessment['requested_level'], 'content')
        self.assertIn('opinion_content_missing', assessment['gaps'])
        answer, _ = NaturalLanguageAnswerGenerator().generate(context.question, snap['compact_context'], settings=SETTINGS, validated=True)
        self.assertIn('尚未取得', answer['summary'])
        for phrase in ('客服回复慢', '客服态度差', '售后推诿'):
            self.assertNotIn(phrase, json.dumps(answer, ensure_ascii=False))

    def test_one_controlled_supplement_and_failure_protection(self):
        # 合法补证从已满足统计目标的主计划出发；冲突目标由上例验证零查询。
        question = self.explicit('客服差评有多少条，具体如何解读？')
        result = self.run_local(question)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertTrue(result['task_plan_check']['passed'])
        record = result['evidence_completion']
        self.assertEqual((record['cycles'], record['additional_tool_calls'], record['status']), (1, 1, 'completed'))
        self.assertEqual(record['requests'][0]['tool'], 'get_opinion_breakdown')
        self.assertIn('客服回复慢', json.dumps(result['public_answer'], ensure_ascii=False))
        for query in (question, '客服方面有什么问题？'):
            with self.subTest(query=query), patch.object(analytics, 'get_opinion_breakdown', side_effect=TimeoutError('test timeout')):
                failed = self.run_local(query)
            self.assertEqual(failed['status'], 'error')
            self.assertNotEqual(failed['public_answer']['status'], 'ok')
            self.assertEqual(failed['public_answer']['highlights'], [])
        limited = self.run_local(question, settings=replace(SETTINGS, max_tool_calls=2))
        self.assertEqual(limited['evidence_completion']['status'], 'failed')
        self.assertNotEqual(limited['public_answer']['status'], 'ok')

    def test_counts_stay_summary_and_existing_topics_still_work(self):
        for question in ('客服差评有多少条？', '客服负面占比是多少？', '物流差评占比表现如何？'):
            self.assertEqual(build_query_plan(question, self.scope()).required_tools, ['get_top_issues'])
        self.assertEqual(build_query_plan('物流好评数量表现如何？', self.scope()).required_tools, ['get_positive_aspects'])
        # 明确的二级主题能力仍保留，不能因新内容规则退化成一级统计。
        self.assertEqual(build_query_plan('使用效果具体好在哪里？', self.scope()).required_tools, ['get_aspect_drilldown'])

    def test_selected_multiple_products_get_logistics_content_summary(self):
        for product_ids in (['DIM-A', 'DIM-B'], []):
            with self.subTest(product_ids=product_ids):
                result = self.run_local('所有商品的物流好在哪里？', scope=self.scope(product_ids=product_ids))
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(set(result['scope']['product_ids']), {'DIM-A', 'DIM-B'})
                self.assertEqual(result['query_plan']['required_tools'], ['get_opinion_breakdown'])
                data = result['results']['r1']['data']
                self.assertEqual((data['aspect'], data['sentiment'], data['total']), ('logistics', 'positive', 4))
                self.assertEqual(result['validation']['status'], 'passed')
                self.assertIn('当前所选 2 款商品', result['public_answer']['summary'])
                self.assertIn('买家对物流的好评提到', result['public_answer']['summary'])
                self.assertIn('送货准时', result['public_answer']['summary'])
                self.assertNotIn('送货迟到', json.dumps(result['public_answer'], ensure_ascii=False))
                self.assertNotIn('sha256:', ''.join(row['text'] for row in result['public_answer']['highlights']))
                self.assertEqual(result['topic_model_calls'], 0)

    def test_scope_binding_and_simple_ranking_are_deterministic(self):
        single = self.run_local('差评主要集中在哪里？')
        self.assertEqual(single['status'], 'ok', single.get('error'))
        self.assertEqual(single['query_plan']['required_tools'], ['get_top_issues'])
        self.assertEqual(single['model_calls'], 0)
        self.assertEqual(single['answer_model_calls'], 0)
        self.assertIn('成功标注评论中有', single['public_answer']['summary'])
        self.assertIn('4 条成功标注评论中有 2 条', single['public_answer']['summary'])
        self.assertIn('具体内容仍需查看对应观点', single['public_answer']['summary'])
        self.assertGreater(single['performance']['scope_resolution_duration_ms'], 0)
        self.assertGreater(single['performance']['intent_resolution_duration_ms'], 0)
        self.assertEqual(single['performance']['planning_model_duration_ms'], 0)

        all_scope = self.scope(product_ids=[])
        ambiguous = self.run_local('当前空调物流好在哪里？', scope=all_scope)
        self.assertEqual(ambiguous['status'], 'unsupported')
        self.assertIn('请指定一款商品 ID', ambiguous['public_answer']['summary'])
        self.assertEqual(ambiguous['query_executions'], 0)
        self.assertEqual(set(ambiguous['scope']['product_ids']), {'DIM-A', 'DIM-B'})

        overall = self.run_local('所有商品的物流好评整体怎么样？', scope=all_scope)
        self.assertEqual(overall['status'], 'ok', overall.get('error'))
        self.assertEqual(overall['query_plan']['required_tools'], ['get_positive_aspects'])
        self.assertEqual(set(overall['scope']['product_ids']), {'DIM-A', 'DIM-B'})
        self.assertEqual(overall['model_calls'], 0)
        self.assertIn('物流', overall['public_answer']['summary'])

        named = self.run_local('商品 DIM-A 的物流好评整体怎么样？', scope=all_scope)
        self.assertEqual(named['status'], 'ok', named.get('error'))
        self.assertEqual(named['scope']['product_ids'], ['DIM-A'])
        outside = self.run_local('商品 DIM-B 的物流好评整体怎么样？')
        self.assertEqual(outside['status'], 'unsupported')
        self.assertIn('当前选择范围之外', outside['public_answer']['summary'])

    def test_single_product_logistics_positive_content_uses_bounded_topic_tool(self):
        with patch('data_analysis_agent.agents.review_topics._call', side_effect=exact_topic_response):
            result = self.run_local('物流好在哪里？')
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['DIM-A'])
        self.assertEqual(result['query_plan']['required_tools'][0], 'get_aspect_drilldown')
        self.assertEqual(result['results']['r1']['parameters']['sentiment'], 'positive')
        self.assertEqual(result['validation']['status'], 'passed')
