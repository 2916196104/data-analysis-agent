"""具体好评总结：真实只读工具、受控语义响应、六维来源和样本分母。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
from topic_fixture import topic_model
from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch, validate_topic_result, analyze_aspect_topics
from data_analysis_agent.agents.review_semantics import product_mentions
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.reviews.analytics import get_aspect_opinions
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import ASPECTS, validate_opinions
from data_analysis_agent.reviews.store import import_amazon_jsonl


THEMES = [
    ('logistics', '发货及时', ['当天发出', '下单即发货']),
    ('logistics', '送达较快', ['第二天送到', '配送很快']),
    ('service', '客服响应及时', ['回复及时', '回应迅速']),
    ('service', '解答耐心', ['解答耐心', '耐心回答']),
    ('effectiveness', '制冷快', ['制冷快', '降温快']),
    ('packaging', '包装严实', ['包装严实', '包装结实']),
    ('quality', '做工牢固', ['做工牢固', '外壳牢固']),
    ('price', '价格合适', ['价格合适', '价格实惠']),
]


def add_summary_reviews(db, root, *, product='100013588053', dataset='positive-summary-fixture', identity=IDENTITY):
    """供单测和真实 API/Worker 浏览器验收共同使用，全部是合成评论。"""
    rows = [{'parent_asin': product, 'user_id': 'summary-' + str(i),
             'text': '。'.join(quotes[i] for _, _, quotes in THEMES), 'timestamp': 1675987200000 + i * 1000}
            for i in range(2)]
    rows += [{'parent_asin': product, 'user_id': 'negative', 'text': '客服回复慢。配送迟到', 'timestamp': 1675987203000},
             {'parent_asin': product, 'user_id': 'uncertain', 'text': '好', 'timestamp': 1675987204000}]
    path = Path(root) / 'summary-reviews.jsonl'
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows), encoding='utf-8')
    import_amazon_jsonl(db, reviews_path=path, dataset_version=dataset)
    for review in pending_reviews(db, **identity, dataset_version=dataset, product_ids=[product], limit=100):
        if review['text'] == '好':
            values = [{'aspect': key, 'sentiment': 'positive', 'quote': '好'} for key in ('logistics', 'service')]
        elif '客服回复慢' in review['text']:
            values = [{'aspect': 'service', 'sentiment': 'negative', 'quote': '客服回复慢'},
                      {'aspect': 'logistics', 'sentiment': 'negative', 'quote': '配送迟到'}]
        else:
            values = [{'aspect': aspect, 'sentiment': 'positive', 'quote': quote}
                      for aspect, _, quotes in THEMES for quote in quotes if quote in review['text']]
        save_annotation(db, review, **identity, status='success', opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))


def summary_model(settings, prompt, payload, schema, timeout):
    if schema is TopicCatalog:
        themes = []
        for index, (aspect, name, quotes) in enumerate(THEMES):
            support = [row['opinion_id'] for row in payload['opinions'] if row['quote'] in quotes and row.get('aspect') == aspect]
            if support:
                themes.append({'key': f't{index + 1}', 'aspect': aspect, 'name': name,
                               'definition': name + '是这些买家认可的具体体验', 'support_ids': support[:6]})
        return TopicCatalog(themes=themes)
    assignments = []
    for row in payload['opinions']:
        matches = [{'key': theme['key'], 'start': 0, 'end': len(row['quote'])}
                   for theme in payload['themes'] for aspect, name, quotes in THEMES
                   if theme['name'] == name and row['quote'] in quotes and row.get('aspect') == aspect]
        assignments.append({'opinion_id': row['opinion_id'], 'state': 'assigned' if matches else 'uncertain', 'matches': matches})
    return TopicBatch(assignments=assignments)


class PositiveSummaryTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        add_summary_reviews(self.db, self.root)

    def scope(self, **changes):
        return {**IDENTITY, 'source': 'amazon_reviews_2023', 'dataset_version': 'positive-summary-fixture',
                'product_ids': ['100013588053'], 'date_from': '2023-02-01', 'date_to': '2023-02-28',
                'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31', **changes}

    def run_summary(self, question, *, context=None, scope=None, response=summary_model):
        with topic_model(response), patch('openai.OpenAI', side_effect=AssertionError('no external model')):
            return run_review_agent(self.db, question, scope=scope or self.scope(),
                                    settings=replace(SETTINGS, semantic_parser_enabled=False), conversation_context=context)

    def test_dimension_synonyms_and_positive_negative_separation(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for aspect, word, expected in [('logistics', '物流', ['发货及时', '送达较快']),
                                       ('service', '客服', ['客服响应及时', '解答耐心'])]:
            result = self.run_summary(f'100013588053{word}的好评体现在哪里？')
            self.assertEqual(result['status'], 'ok', result.get('error'))
            data = result['results']['r1']['data']
            self.assertTrue(validate_topic_result(data))
            self.assertEqual({row['name'] for row in data['topics']}, set(expected))
            self.assertTrue(all(row['review_count'] == 2 and row['review_denominator'] == 3 for row in data['topics']))
            self.assertTrue(all(row['aspect'] == aspect and row['sentiment'] == 'positive' for row in data['opinions']))
            self.assertLessEqual(result['answer_generation']['attempts'], 2)
            for name in expected:
                self.assertIn(name, result['public_answer']['summary'])
            self.assertNotIn('客服回复慢', json.dumps(result['public_answer'], ensure_ascii=False))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_whole_product_covers_six_dimensions_and_uses_per_aspect_denominators(self):
        result = self.run_summary('给出100013588053商品评论中具体的好评')
        self.assertEqual(result['status'], 'ok', result.get('error'))
        data = result['results']['r1']['data']
        self.assertIsNone(data['aspect'])
        self.assertTrue(validate_topic_result(data))
        self.assertEqual({row['aspect'] for row in data['topics']}, set(ASPECTS))
        # 至多二十种表达，发现一次，十条一批复核至多两次。
        self.assertLessEqual(data['model_calls'], 3)
        for topic in data['topics']:
            self.assertEqual(topic['review_denominator'], 3 if topic['aspect'] in ('service', 'logistics') else 2)
            self.assertIn(ASPECTS[topic['aspect']], result['public_answer']['summary'])
        self.assertEqual(data['coverage']['unclassified_opinions'], 2)
        self.assertEqual(len({row['unit_id'] for row in data['opinions'] if row['quote'] == '好'}), 2)
        self.assertIn('各主题占比以该维度实际纳入的评论为分母', ''.join(result['public_answer']['caveats']))
        public = read_public_answer({'status': 'succeeded', 'parameters': self.scope(), 'result': result})
        self.assertEqual(public['summary'], result['public_answer']['summary'])
        self.assertEqual(len(public['highlights']), 8)

    def test_followup_restores_product_and_can_switch_dimension_or_return_to_whole(self):
        first = self.run_summary('100013588053的好评集中在什么地方？')
        context = {'user': {'task_id': 'first', 'question': first['question']}, 'turns_read': 1,
                   'validated': {'task_id': 'first', 'scope': first['scope'], 'sentiment': 'positive',
                                 'queries': [{'kind': 'positive_aspects', 'parameters': {'product_ids': ['100013588053']}}]}}
        products = [{'product_id': '100013588053', 'title': '合成商品', 'brand': None}]
        self.assertEqual(product_mentions('给出评论中具体的好评', products), [])
        whole = self.run_summary('给出评论中具体的好评', context=context, scope=self.scope(product_ids=[]))
        self.assertEqual(whole['status'], 'ok', whole.get('answer'))
        self.assertEqual(whole['scope']['product_ids'], ['100013588053'])
        context['validated']['queries'] = [{'kind': 'aspect_drilldown', 'parameters': {'product_ids': ['100013588053'], 'aspect': 'logistics', 'sentiment': 'positive'}}]
        switched = self.run_summary('客服的好评体现在哪里？', context=context)
        self.assertEqual(switched['results']['r1']['data']['aspect'], 'service')
        overall = self.run_summary('总结这个商品的整体好评', context=context)
        self.assertEqual(overall['status'], 'ok', overall.get('answer'))
        self.assertIsNone(overall['results']['r1']['data']['aspect'])

    def test_whole_summary_sample_limits_and_rejects_cross_aspect_assignments(self):
        scope = {k: v for k, v in self.scope().items() if not k.startswith('baseline_')}
        extracted = get_aspect_opinions(self.db, **scope, aspect=None, sentiment='positive')
        with topic_model(summary_model), patch('data_analysis_agent.agents.review_topics.MAX_POSITIVE_UNIQUE', 6):
            data = analyze_aspect_topics(extracted, settings=SETTINGS)
        self.assertEqual(data['population_mode'], 'sample')
        self.assertTrue(validate_topic_result(data))
        self.assertEqual({row['aspect'] for row in data['opinions']}, set(ASPECTS))
        self.assertEqual(len({row['unit_id'] for row in data['opinions']}), 6)
        corrupt = deepcopy(data)
        corrupt['catalog'][0]['aspect'] = 'price' if corrupt['catalog'][0]['aspect'] != 'price' else 'quality'
        self.assertFalse(validate_topic_result(corrupt))
        corrupt = deepcopy(data)
        corrupt['topics'][0]['review_denominator'] = 999
        self.assertFalse(validate_topic_result(corrupt))

    def test_empty_failure_and_explicit_examples(self):
        empty = self.run_summary('物流的好评体现在哪里？', scope=self.scope(date_from='2023-02-20'))
        self.assertEqual(empty['status'], 'unsupported')
        self.assertEqual(empty['topic_model_calls'], 0)
        self.assertEqual(empty['task_acceptance']['status'], 'insufficient_evidence')
        self.assertFalse(empty['public_answer']['highlights'])
        failed = self.run_summary('总结100013588053的好评', response=lambda *args: (_ for _ in ()).throw(TimeoutError('fixture')))
        self.assertEqual(failed['status'], 'error')
        self.assertNotEqual(failed['public_answer']['status'], 'ok')
        from data_analysis_agent.agents.review_plan import build_query_plan
        self.assertEqual(build_query_plan('给出100013588053的3条好评原文', self.scope()).required_tools, ['get_evidence'])
