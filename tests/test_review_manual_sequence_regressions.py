"""Reported manual questions, including native analysis after history; synthetic CSV only."""
import csv
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
import test_assistant_semantics as fixture
from topic_fixture import topic_model
from data_analysis_agent.agents.review_acceptance import build_request_goal
from data_analysis_agent.agents.review_semantics import (
    QuestionScopeError, match_product_mentions, product_mentions,
)
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


CATALOG = [dict(product_id='100013588053', title='美的 1.5 匹风酷空调', brand='美的')]


class ProductRequestPrefixTests(unittest.TestCase):
    def test_request_and_evidence_count_are_not_part_of_product_name(self):
        for prefix in ('给我3条', '请给我 3 条', '提供2条', '请列出3条', '给出 3 条'):
            with self.subTest(prefix=prefix):
                question = prefix + '美的空调的物流差评原文。'
                mentions = product_mentions(question, CATALOG)
                self.assertEqual(mentions, ['美的空调'])
                self.assertEqual(match_product_mentions(mentions, CATALOG), ['100013588053'])

    def test_unknown_product_still_rejects_after_request_prefix_is_removed(self):
        mentions = product_mentions('给我3条不存在牌空调的物流差评原文。', CATALOG)
        self.assertIn('不存在牌空调', mentions)
        with self.assertRaises(QuestionScopeError):
            match_product_mentions(mentions, CATALOG, selected_ids=['100013588053'])


class ManualSequenceTests(fixture.AssistantSemanticsFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        rows = [dict(product_id='100013588053', product_name=CATALOG[0]['title'], brand='美的',
                     user_id=f'manual-sequence-{index}', published_at='2021-06-15',
                     text=f'合成物流{direction}观点{index}')
                for index, direction in enumerate(['差评'] * 3 + ['好评'] * 3)]
        path = self.root / 'manual-sequence.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **self.identity, source='reviews_csv', limit=100):
            if not review['text'].startswith('合成物流'):
                continue
            value = dict(aspect='logistics', sentiment='negative' if '差评' in review['text'] else 'positive',
                         quote=review['text'])
            save_annotation(self.db, review, **self.identity, status='success',
                opinions=validate_opinions(json.dumps({'opinions': [value]}), review['text']))

    def run_local(self, question, *, context=None):
        with topic_model(), patch('openai.OpenAI', side_effect=AssertionError('no external model')), \
                patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planning model')):
            return run_review_agent(self.db, question, scope=self.scope(),
                settings=replace(SETTINGS, semantic_parser_enabled=False), conversation_context=context)

    def history(self):
        question = '美的空调的物流差评具体内容说了什么？'
        first = self.run_local(question)
        self.assertEqual(first['status'], 'ok', first.get('error'))
        return {'user': {'task_id': 'previous-logistics', 'question': question},
            'users': [{'task_id': 'previous-logistics', 'question': question}], 'turns_read': 1,
            'validated': {'task_id': 'previous-logistics', 'scope': first['scope'], 'sentiment': 'negative',
                'queries': [dict(kind=first['results'][key]['kind'],
                    parameters=deepcopy(first['results'][key]['parameters'])) for key in first['result_ids']]}}

    def test_named_whole_product_request_does_not_inherit_previous_logistics_dimension(self):
        context = self.history()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for question in ('美的空调的好评体现在哪里？', '100013588053的好评体现在哪里？'):
            with self.subTest(question=question):
                result = self.run_local(question, context=context)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertIsNone(result['request_goal']['aspect'])
                self.assertTrue(result['task_acceptance']['complete'])
                self.assertEqual(result['query_plan']['required_tools'], ['get_aspect_drilldown'])
                data = result['results'][result['result_ids'][0]]['data']
                self.assertIsNone(data['aspect'])
                self.assertEqual({row['aspect'] for row in data['topics']}, {'effectiveness', 'logistics'})
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_short_followup_still_inherits_dimension_and_explicit_dimension_still_wins(self):
        context = self.history()
        self.assertEqual(build_request_goal('好评体现在哪里？', context).aspect, 'logistics')
        self.assertEqual(build_request_goal('具体体现在哪里？', context).aspect, 'logistics')
        self.assertEqual(build_request_goal('美的空调的使用效果好评体现在哪里？', context).aspect, 'effectiveness')
        result = self.run_local('好评体现在哪里？', context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['results'][result['result_ids'][0]]['data']['aspect'], 'logistics')

    def test_original_evidence_question_reaches_tool_and_preserves_requested_count(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for prefix, count in (('给我3条', 3), ('请提供2条', 2)):
            with self.subTest(prefix=prefix):
                result = self.run_local(prefix + '美的空调的物流差评原文。')
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertFalse(result['user_intent']['needs_clarification'])
                self.assertEqual(result['query_plan']['required_tools'], ['get_evidence'])
                self.assertTrue(result['task_acceptance']['complete'])
                evidence = result['results'][result['result_ids'][0]]
                self.assertEqual(evidence['parameters']['page_size'], count)
                self.assertEqual(len(evidence['data']['items']), count)
                for row in evidence['data']['items']:
                    self.assertEqual(row['product_id'], '100013588053')
                    self.assertTrue(all(op['aspect'] == 'logistics' and op['sentiment'] == 'negative'
                                        for op in row['opinions']))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
