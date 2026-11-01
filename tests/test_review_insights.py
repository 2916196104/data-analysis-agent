"""有来源的解释、重点选择和模型失败；不调用真实模型或生产库。"""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
import test_review_answers as answer_fixture
import test_review_topics as topic_fixture
from topic_fixture import topic_model
from data_analysis_agent.agents.review_answers import AnswerAssembler, NaturalLanguageAnswerGenerator, read_public_answer
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_insights import InsightDraft, fact_catalog, default_focus
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.core.serialization import canonical_json


class InsightTests(answer_fixture.WorkbenchFixture, unittest.TestCase):
    scope = answer_fixture.AnswerTests.scope
    result = answer_fixture.AnswerTests.result
    context = answer_fixture.AnswerTests.context
    strong_trend_context = answer_fixture.AnswerTests.strong_trend_context

    def topic_context(self):
        fixture = topic_fixture.ReviewTopicTests(); fixture.setUp()
        try:
            with topic_model(topic_fixture.semantic_response), patch('openai.OpenAI', side_effect=AssertionError('no external model')):
                full = run_review_agent(fixture.db, '使用效果具体好在哪里？', scope=fixture.scope(), settings=SETTINGS)
            return full, full['compact_context']
        finally:
            fixture.tearDown()

    def model(self, *, transform=None, reject=False, error_stage=None, fail_first=False):
        self.requests = []
        def handle(request):
            body = json.loads(request.content); self.requests.append(body)
            payload = json.loads(body['messages'][-1]['content'])
            if error_stage == payload['stage'] or (fail_first and len(self.requests) == 1):
                raise httpx.ReadTimeout('synthetic model timeout')
            if payload['stage'] == 'answer_draft':
                key = payload['required_focus'][0]
                draft = {'schema_version': '2.0', 'focus': payload['required_focus'], 'insights': [{
                    'kind': 'interpretation', 'fact_ids': [key],
                    'text': f'[[{key}.label]]提示了当前评价的一项关注点，仍需结合其他反馈理解。'}]}
                if transform:
                    draft = transform(draft)
            else:
                draft = {'decisions': [{'index': index, 'supported': not reject,
                    'reason': 'unsupported_interpretation' if reject else 'supported'} for index in range(len(payload['insights']))]}
            return completion({'role': 'assistant', 'content': json.dumps(draft)})
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            self.assertLessEqual(kwargs['timeout'], SETTINGS.answer_timeout_seconds)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        return patch('openai.OpenAI', side_effect=client)

    def generate(self, context):
        return NaturalLanguageAnswerGenerator().generate(context['question'], context, settings=SETTINGS, validated=True)

    def test_topic_fact_catalog_and_supported_interpretation_without_regrouping(self):
        full, context = self.topic_context()
        before = canonical_json(full['results'])
        catalog, _ = fact_catalog(CompactAnalysisContext.model_validate(context))
        self.assertEqual([row['label'] for row in catalog], ['制冷速度', '运行噪声'])
        self.assertTrue(all(row['evidence_ids'] and row['topic_id'] for row in catalog))
        tampered = deepcopy(catalog)
        tampered[-1]['fact'].source_result_ids = ['unverified-result']
        with self.assertRaisesRegex(ValueError, '来源'):
            AnswerAssembler().assemble_insights(CompactAnalysisContext.model_validate(context),
                InsightDraft(focus=default_focus(tampered)), tampered, generation_status='llm')
        with self.model(), patch('data_analysis_agent.agents.review_topics._call', side_effect=AssertionError('no regrouping')):
            answer, diag = self.generate(context)
        self.assertEqual(answer['generation_status'], 'llm')
        self.assertEqual(diag['attempts'], 2)
        self.assertEqual(diag['reviewed_insights'], 1)
        self.assertEqual((diag['model_input_tokens'], diag['model_output_tokens']), (20, 10))
        self.assertEqual(answer['insights'][0]['kind'], 'interpretation')
        self.assertIn('制冷速度', answer['insights'][0]['text'])
        self.assertIn('2 条去重评论、3 条观点', answer['highlights'][0]['text'])
        self.assertEqual(before, canonical_json(full['results']))
        sent = canonical_json(self.requests)
        for quote in ('降温很快', '制冷速度快', '运行声音小'):
            self.assertNotIn(quote, sent)

    def test_sample_candidate_limits_and_unclassified_never_lost(self):
        _, context = self.topic_context()
        context['statistics'][0]['filters']['population_mode'] = 'sample'
        with self.model():
            answer, _ = self.generate(context)
        self.assertIn('非随机样本', answer['summary'])
        self.assertIn('不能视为总体', ''.join(answer['caveats']))
        self.assertIn('未经人工确认', ''.join(answer['caveats']))
        self.assertIn('未归类 2 条', ''.join(answer['caveats']))

    def test_known_parent_dimension_is_rebound_but_free_quotes_remain_rejected(self):
        _, context = self.topic_context()
        def transform(draft):
            draft['insights'][0]['text'] = '用户提到的“使用效果”包括' + draft['insights'][0]['text']
            return draft
        with self.model(transform=transform):
            answer, diag = self.generate(context)
        self.assertEqual(answer['generation_status'], 'llm')
        self.assertIn('使用效果', answer['insights'][0]['text'])
        self.assertNotIn('“', answer['insights'][0]['text'])
        self.assertIn('.aspect]]', diag['validated_draft']['insights'][0]['text'])

    def test_unsupported_free_text_and_independent_rejection_fall_back(self):
        _, context = self.topic_context()
        for text in ('已有999条评论证实节能。', '已确认制冷更好。', '物流延迟直接导致退货。', '这属于人工标签。', '“编造原文”。'):
            with self.subTest(text=text), self.model(transform=lambda draft: {**draft, 'insights': [{**draft['insights'][0], 'text': text}]}):
                answer, diag = self.generate(context)
            self.assertEqual(answer['generation_status'], 'fallback')
            self.assertEqual(answer['insights'], [])
            self.assertEqual(diag['failed_stage'], 'draft')
        with self.model(reject=True):
            answer, diag = self.generate(context)
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual(diag['failed_stage'], 'support_review')
        self.assertEqual(answer['insights'], [])

    def test_partial_failed_and_insufficient_data_have_no_external_generation(self):
        _, context = self.topic_context()
        for state in ('partial', 'failed'):
            data = deepcopy(context); data['statistics'][0]['filters']['analysis_status'] = state
            with patch('openai.OpenAI') as client:
                answer, diag = self.generate(data)
            client.assert_not_called()
            self.assertEqual(answer['status'], state)
            self.assertEqual(diag['attempts'], 0)
        context['statistics'][0]['filters'].update(analysis_status='insufficient_data', topics=[])
        with patch('openai.OpenAI') as client:
            answer, diag = self.generate(context)
        client.assert_not_called()
        self.assertEqual(diag['attempts'], 0)
        self.assertFalse(answer['insights'])
        self.assertIn('不据此编造主题排名', answer['highlights'][0]['text'])

    def test_public_history_does_not_trust_persisted_free_interpretations(self):
        full, context = self.topic_context()
        with self.model():
            _, diag = self.generate(context)
        full['answer_generation'] = diag
        with patch('openai.OpenAI') as client:
            answer = read_public_answer({'status': 'succeeded', 'result': full})
        client.assert_not_called()
        self.assertEqual(answer['status'], 'ok')
        self.assertEqual(answer['insights'], [])

    def test_review_timeout_preserves_verified_facts(self):
        _, context = self.topic_context()
        with self.model(error_stage='answer_support_review'):
            answer, diag = self.generate(context)
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual(diag['attempts'], 2)
        self.assertEqual(diag['error_code'], 'generation_timeout')
        self.assertEqual(answer['insights'], [])
        self.assertTrue(answer['highlights'])

    def test_transport_retry_shares_the_two_request_budget_with_support_review(self):
        _, context = self.topic_context()
        with self.model(fail_first=True):
            answer, diag = NaturalLanguageAnswerGenerator().generate(context['question'], context,
                settings=replace(SETTINGS, answer_max_retries=1), validated=True)
        self.assertEqual(diag['attempts'], 2)
        self.assertEqual(diag['request_stages'], ['draft', 'draft'])
        self.assertEqual(diag['error_code'], 'answer_call_budget_exceeded')
        self.assertEqual(diag['failed_stage'], 'support_review')
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual(answer['insights'], [])

    def test_comparison_relation_is_computed_and_incomparable_windows_do_not_mix(self):
        _, context = self.context('比较两个商品的主要负面问题', product_ids=['P1', 'P2'])
        catalog, _ = fact_catalog(CompactAnalysisContext.model_validate(context))
        self.assertEqual(catalog[0]['kind'], 'contrast')
        with self.model(transform=lambda draft: {**draft, 'insights': []}):
            answer, diag = self.generate(context)
        self.assertEqual(diag['attempts'], 1)
        self.assertEqual(len(answer['highlights']), len(catalog))
        self.assertTrue(any('暂无匹配' in item['text'] for item in answer['highlights']))
        self.assertTrue(any('包装' in item['text'] for item in answer['highlights']))
        self.assertIn('真实产品性能', answer['summary'])
        self.assertIn('个百分点', answer['highlights'][0]['text'])
        changed = deepcopy(context)
        changed['scope_summary'][-1]['date_to'] = '2023-05-31'
        catalog, _ = fact_catalog(CompactAnalysisContext.model_validate(changed))
        self.assertFalse(any(row['kind'] == 'contrast' for row in catalog))

    def test_trends_cover_requested_dimensions_and_reviewer_rejects_stable_improvement(self):
        _, context = self.context('按月查看包装和物流问题变化', date_to='2023-03-31')
        with self.model(transform=lambda draft: {**draft, 'insights': []}):
            answer, _ = self.generate(context)
        self.assertEqual({row['aspect_key'] for row in answer['highlights']}, {'packaging', 'logistics'})
        self.assertIn('暂不能判断稳定趋势', answer['summary'])
        with self.model(transform=lambda draft: {**draft, 'focus': draft['focus'][:1]}):
            answer, _ = self.generate(context)
        self.assertEqual(answer['generation_status'], 'fallback')
        self.assertEqual({row['aspect_key'] for row in answer['highlights']}, {'packaging', 'logistics'})

    def test_bad_focus_slots_sample_extrapolation_and_chinese_numbers_are_rejected(self):
        _, context = self.topic_context()
        context['statistics'][0]['filters']['population_mode'] = 'sample'
        transforms = [lambda d: {**d, 'focus': ['finding:' + '0'*20]},
            lambda d: {**d, 'insights': [{**d['insights'][0], 'fact_ids': ['finding:'+'0'*20]}]},
            lambda d: {**d, 'insights': [{**d['insights'][0], 'text': d['insights'][0]['text']+'覆盖全部评论。'}]},
            lambda d: {**d, 'insights': [{**d['insights'][0], 'text': d['insights'][0]['text']+'占百分之五十。'}]},
            lambda d: {**d, 'insights': [{**d['insights'][0], 'text': d['insights'][0]['text']+'已有９９条。'}]},
            lambda d: {**d, 'statistic': 99}]
        for transform in transforms:
            with self.subTest(transform=transform), self.model(transform=transform):
                answer, diag = self.generate(context)
            self.assertEqual(answer['generation_status'], 'fallback')
            self.assertEqual(answer['insights'], [])
            self.assertLessEqual(diag['attempts'], 2)

    def test_database_connection_is_closed_before_answer_generation(self):
        import data_analysis_agent.agents.reviews as runtime
        original_connect = runtime.connect_review_database
        original_generate = runtime.NaturalLanguageAnswerGenerator.generate
        connections = []
        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs); connections.append(connection); return connection
        def generate(*args, **kwargs):
            self.assertTrue(connections)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
            return original_generate(*args, **kwargs)
        original_question = '这款商品主要有哪些差评？'
        question = '执行本轮已确认的只读查询\n' + json.dumps({'tool':'get_top_issues','parameters':{}, 'question': original_question})
        with patch.object(runtime, 'connect_review_database', side_effect=connect), patch.object(runtime.NaturalLanguageAnswerGenerator, 'generate', new=generate):
            result = runtime.run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['analysis_question'], original_question)
