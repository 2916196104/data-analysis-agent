"""任务级离线验收。分类名称仅来自本文件的合成分类器，不是生产金标准。"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import csv
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
import test_review_intent as intent_fixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_topics import TopicCatalog, TopicBatch
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_acceptance import validate_task_completion
from data_analysis_agent.jobs.analysis_context import load_analysis_context
from data_analysis_agent.jobs.store import claim_task, finish_task
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.labeling import validate_opinions

A, B = '100010219881', '100018015886'
PRODUCTS = [A, B, '100018015890', '100018022105', '100021755350']


def synthetic_classifier(settings, prompt, payload, schema, timeout):
    taxonomy = {'t1': ('模拟配送迟延', {'配送很慢', '送货太晚'}),
                't2': ('模拟运输追踪', {'运输更新停滞'}),
                't3': ('模拟预约履约', {'预约时间与送达不一致'})}
    if schema is TopicCatalog:
        return TopicCatalog(themes=[dict(key=key, name=name, definition=name,
            support_ids=[row['opinion_id'] for row in payload['opinions'] if row['quote'] in quotes])
            for key, (name, quotes) in taxonomy.items() if any(row['quote'] in quotes for row in payload['opinions'])])
    rows = []
    for row in payload['opinions']:
        keys = [key for key, (_, quotes) in taxonomy.items() if row['quote'] in quotes]
        rows.append(dict(opinion_id=row['opinion_id'], state='assigned' if keys else 'uncertain',
            matches=[dict(key=key, start=0, end=len(row['quote'])) for key in keys]))
    return TopicBatch(assignments=rows)


class ReviewTaskCompletionTests(WorkbenchFixture, unittest.TestCase):
    queue = intent_fixture.ReviewIntentTests.queue
    enqueue = intent_fixture.ReviewIntentTests.enqueue

    def scope(self, **changes):
        return {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'completion-fixture',
                'product_ids': [A], 'date_from': '2023-02-01', 'date_to': '2023-02-28',
                'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31', **changes}

    def setUp(self):
        super().setUp()
        rows = [(A, '配送很慢，送货太晚，运输更新停滞'), (A, '送货太晚'),
                (A, '预约时间与送达不一致'), (A, '13号到货'), (A, '送货准时'),
                (B, '运输更新停滞'), *[(product, '暂无明确观点') for product in PRODUCTS[2:]]]
        path = self.root / 'completion.csv'
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['product_id', 'product_name', 'brand', 'text', 'user_id', 'published_at'])
            writer.writeheader()
            writer.writerows(dict(product_id=product, product_name='小米 测试空调 ' + product, brand='小米',
                text=text, user_id=str(index), published_at='2023-02-10') for index, (product, text) in enumerate(rows))
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='completion-fixture')
        for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', dataset_version='completion-fixture', limit=100):
            opinions = [] if review['text'] == '暂无明确观点' else [dict(aspect='logistics',
                sentiment='positive' if quote == '送货准时' else 'negative', quote=quote)
                for quote in review['text'].split('，')]
            save_annotation(self.db, review, **IDENTITY, status='success',
                opinions=validate_opinions(json.dumps({'opinions': opinions}), review['text']))

    def run_agent(self, question, *, scope=None, context=None, classifier=synthetic_classifier):
        with patch('socket.socket.connect', side_effect=AssertionError('offline only')), \
             patch('openai.OpenAI', side_effect=AssertionError('no external model')), \
             patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planner')), \
             patch('data_analysis_agent.agents.review_topics._call', side_effect=classifier):
            return run_review_agent(self.db, question, scope=scope or self.scope(),
                settings=replace(SETTINGS, semantic_parser_enabled=False), conversation_context=context)

    def saved_context(self, first_question=None, **selection_changes):
        self.queue()
        selection = self.scope(product_ids=PRODUCTS)
        question = first_question or A + '物流差评有多少条？'
        first = self.run_agent(question, scope=selection)
        self.assertEqual(first['status'], 'ok', first.get('error'))
        parent = self.enqueue(question, product_ids=PRODUCTS, selection_scope=selection)
        claim_task(self.tasks, self.worker.worker_id)
        finish_task(self.tasks, parent, self.worker.worker_id, status='succeeded', result=first)
        current = {**selection, **selection_changes}
        parameters = {**current, 'selection_scope': current, 'context_task_id': parent, 'conversation_id': 'conversation-A'}
        return current, load_analysis_context(self.tasks, parameters), parameters

    def test_single_product_themes_are_deduplicated_and_cited(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = self.run_agent(A + '物流差评主要体现在哪里？')
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertTrue(result['task_acceptance']['complete'])
        data = result['results']['r1']['data']
        themes = {row['name']: row for row in data['topics']}
        self.assertEqual(themes['模拟配送迟延']['review_count'], 2)
        self.assertEqual(themes['模拟配送迟延']['opinion_count'], 3)
        self.assertEqual(data['coverage']['unclassified_opinions'], 1)
        for item in data['items']:
            self.assertEqual(item['product_id'], A)
            self.assertTrue(item['evidence_ref'])
            for opinion in item['opinions']:
                self.assertEqual(item['text'][opinion['quote_start']:opinion['quote_end']], opinion['quote'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_multiturn_statistics_themes_and_examples_keep_one_product(self):
        selection, context, _ = self.saved_context()
        for question, kind in [('物流差评有多少条？', 'issues'),
                               ('给出负面的物流观点的体现在哪里', 'aspect_drilldown'),
                               ('这款商品的物流差评体现在哪里？', 'aspect_drilldown'),
                               ('物流差评都在说什么？', 'opinion_breakdown'),
                               ('给出2条物流差评原文', 'evidence')]:
            with self.subTest(question=question):
                result = self.run_agent(question, scope=selection, context=context)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(result['scope']['product_ids'], [A])
                self.assertEqual(result['results']['r1']['kind'], kind)
                self.assertTrue(result['task_acceptance']['complete'])

    def test_explicit_product_and_page_date_changes_win(self):
        selection, context, _ = self.saved_context()
        switched = self.run_agent(B + '物流差评主要体现在哪里？', scope=selection, context=context)
        self.assertEqual(switched['status'], 'ok', switched.get('error'))
        self.assertEqual(switched['scope']['product_ids'], [B])
        self.assertTrue(all(item['product_id'] == B for item in switched['results']['r1']['data']['items']))
        selection, context, _ = self.saved_context(A + '物流差评数量，日期2023-02-01至2023-02-15',
            date_from='2023-02-09', date_to='2023-02-12')
        changed = self.run_agent('物流差评有多少条？', scope=selection, context=context)
        self.assertEqual(changed['status'], 'ok', changed.get('error'))
        self.assertEqual((changed['scope']['product_ids'], changed['scope']['date_from'], changed['scope']['date_to']),
                         ([A], '2023-02-09', '2023-02-12'))

    def test_brand_multiple_skus_clarify_without_queries(self):
        result = self.run_agent('小米空调的物流差评主要体现在哪里？', scope=self.scope(product_ids=PRODUCTS))
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('共 5 款', result['answer'])
        self.assertIn('SKU', result['answer'])
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['total_model_calls'], 0)

    def test_cross_session_and_dataset_cannot_inherit(self):
        selection, context, parameters = self.saved_context()
        self.assertIsNotNone(context)
        self.assertIsNone(load_analysis_context(self.tasks, {**parameters, 'conversation_id': 'other'}))
        self.assertIsNone(load_analysis_context(self.tasks, {**parameters, 'dataset_version': 'other'}))

    def test_no_evidence_and_unclassified_are_not_complete(self):
        for classifier, scope in [(synthetic_classifier, self.scope(product_ids=[PRODUCTS[-1]])),
                                  (lambda settings, prompt, payload, schema, timeout: TopicCatalog(themes=[])
                                   if schema is TopicCatalog else TopicBatch(assignments=[dict(opinion_id=row['opinion_id'],
                                       state='unclassified', matches=[]) for row in payload['opinions']]), self.scope())]:
            result = self.run_agent('物流差评主要体现在哪里？', scope=scope, classifier=classifier)
            self.assertEqual(result['status'], 'unsupported', result.get('error'))
            self.assertEqual(result['task_acceptance']['status'], 'insufficient_evidence')
            self.assertFalse(result['task_acceptance']['complete'])
            self.assertFalse(result['public_answer']['highlights'])

    def test_goal_gate_rejects_counts_wrong_scope_and_missing_citations(self):
        result = self.run_agent('物流差评主要体现在哪里？')
        plan = build_query_plan(result['question'], result['scope'])
        selected = [result['results'][key] for key in result['result_ids']]
        wrong = validate_task_completion(result['task_goal'], {**result['scope'], 'product_ids': [B]}, plan, selected)
        self.assertEqual(wrong['status'], 'failed')
        counts = self.run_agent('物流差评有多少条？')
        only_counts = [counts['results'][key] for key in counts['result_ids']]
        self.assertEqual(validate_task_completion(result['task_goal'], result['scope'], plan, only_counts)['status'], 'failed')
        tampered = deepcopy(selected)
        for item in tampered[0]['data']['items']:
            item['evidence_ref'] = ''
        self.assertFalse(validate_task_completion(result['task_goal'], result['scope'], plan, tampered)['complete'])

    def test_unplanned_facet_is_rejected(self):
        plan = build_query_plan('差评主要集中在哪些方面？另外查看星级分布。', self.scope())
        with self.assertRaises(ValueError):
            plan.check_call('get_top_issues', {'aspect': 'service'})
