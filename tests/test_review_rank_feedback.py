"""Ranking with bounded, source-checked feedback examples; isolated CSV/SQLite only."""
import csv
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.jobs.analysis_state import restore_ranking
from data_analysis_agent.jobs.store import init_tasks, enqueue, worker_started, claim_task, finish_task, get_task
from data_analysis_agent.reviews.analytics import get_top_issues
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions


QUESTION = '商品RANK-TEST的差评主要集中在哪些方面？'


class RankFeedbackTests(WorkbenchFixture, unittest.TestCase):
    def scope(self, **changes):
        return {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
            'product_ids': ['RANK-TEST'], 'date_from': '2021-06-01', 'date_to': '2021-06-30',
            'baseline_from': '2021-05-01', 'baseline_to': '2021-05-31', **changes}

    def setUp(self):
        super().setUp()
        self.enterContext(patch('openai.OpenAI', side_effect=AssertionError('no external model')))
        self.enterContext(patch('data_analysis_agent.agents.reviews.create_llm', side_effect=AssertionError('no planning model')))
        self.values = [('service', 'negative', '客服回复慢'), ('effectiveness', 'negative', '制冷效果不好'),
            ('logistics', 'negative', '物流很慢'), ('service', 'negative', '安装收费高'),
            ('logistics', 'negative', '物流延迟'), ('service', 'negative', '售后处理拖延'),
            ('service', 'negative', '客服沟通费劲'), ('effectiveness', 'negative', '运行声音大'),
            ('logistics', 'positive', '物流好快')]
        texts = ['客服回复慢，制冷效果不好，物流很慢', '安装收费高，物流延迟', '售后处理拖延',
                 '客服沟通费劲', '运行声音大', '物流好快']
        path = self.root / 'rank-feedback.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['product_id', 'product_name', 'user_id', 'published_at', 'text'])
            writer.writeheader()
            writer.writerows(dict(product_id='RANK-TEST', product_name='排名合成商品', user_id=str(index),
                                  published_at='2021-06-15', text=text) for index, text in enumerate(texts))
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', limit=100):
            values = [opinion(aspect, sentiment, quote) for aspect, sentiment, quote in self.values if quote in review['text']]
            save_annotation(self.db, review, **IDENTITY, status='success',
                opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))

    def run_local(self, question=QUESTION, **changes):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = run_review_agent(self.db, question, scope=self.scope(**changes),
            settings=replace(SETTINGS, semantic_parser_enabled=False))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(result['total_model_calls'], 0)
        return result

    def test_ranking_reads_bounded_examples_and_publishes_concrete_feedback(self):
        result = self.run_local()
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['analysis_intent'], 'issues')
        self.assertEqual(result['query_plan']['required_tools'], ['get_top_issues'])
        self.assertTrue(result['task_acceptance']['complete'])
        data = result['results']['r1']['data']
        self.assertEqual([row['aspect'] for row in data['rank_examples']], ['service', 'effectiveness', 'logistics'])
        self.assertTrue(all(len(row['items']) == 2 for row in data['rank_examples']))
        self.assertTrue(all('file_path' not in item for row in data['rank_examples'] for item in row['items']))
        self.assertEqual(result['query_executions'], 6)
        self.assertEqual(len(result['public_answer']['evidence_refs']), 6)
        answer = result['public_answer']
        summary = answer['summary']
        rendered = summary + ''.join(row['text'] for row in answer['highlights'])
        for text in ('客服沟通费劲', '运行声音大', '物流延迟'):
            self.assertIn(text, rendered)
        self.assertIn('评论 #', rendered)
        self.assertIn('原文样例', ''.join(answer['caveats']))
        self.assertNotIn('具体内容仍需查看', summary)
        self.assertLess(len(summary), 120)
        self.assertEqual(result['answer_generation']['attempts'], 0)

    def test_long_verified_examples_are_kept_out_of_the_bounded_summary(self):
        path = self.root / 'long-feedback.csv'
        long_values = [(aspect, sentiment, quote * 25) for aspect, sentiment, quote in self.values]
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['product_id', 'product_name', 'user_id', 'published_at', 'text'])
            writer.writeheader()
            writer.writerows(dict(product_id='RANK-TEST', product_name='排名合成商品', user_id='long-' + str(index),
                                  published_at='2021-06-25', text=quote)
                             for index, (_, _, quote) in enumerate(long_values))
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', limit=100):
            values = [opinion(aspect, sentiment, quote) for aspect, sentiment, quote in long_values if quote == review['text']]
            save_annotation(self.db, review, **IDENTITY, status='success',
                opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))
        result = self.run_local()
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertLess(len(result['public_answer']['summary']), 120)
        self.assertTrue(all('核验的买家反馈包括' in row['text'] for row in result['public_answer']['highlights']))
        self.assertIn('节选', ''.join(row['text'] for row in result['public_answer']['highlights']))

    def test_original_counts_and_denominators_are_unchanged(self):
        result = self.run_local()
        raw = get_top_issues(self.db, **{k: v for k, v in self.scope().items() if not k.startswith('baseline_')})
        data = deepcopy(result['results']['r1']['data'])
        data.pop('rank_examples')
        data.pop('topic_statistics', None)
        self.assertEqual(data, raw)

    def test_explicit_numbers_only_keep_existing_query_cost_and_no_examples(self):
        result = self.run_local('商品RANK-TEST的差评维度排名，只看数量。')
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertNotIn('rank_examples', result['results']['r1']['data'])
        self.assertEqual(result['query_executions'], 2)
        self.assertEqual(result['public_answer']['evidence_refs'], [])

    def test_tampered_examples_cannot_pass_native_validation(self):
        result = self.run_local()
        def text_change(data):
            data['rank_examples'][0]['items'][0]['opinions'][0]['quote'] = '伪造的反馈'
        def product_change(data):
            data['rank_examples'][0]['items'][0]['product_id'] = 'OTHER'
        def count_change(data):
            data['rank_examples'][0]['total'] += 1
        def sentiment_change(data):
            data['rank_examples'][0]['sentiment'] = 'positive'
        for mutate in (text_change, product_change, count_change, sentiment_change):
            with self.subTest(mutate=mutate.__name__):
                rows = deepcopy(result['results'])
                mutate(rows['r1']['data'])
                checked = validate_report(rows, result['result_ids'], sentiment_intent='negative',
                    expected_scopes=expected_result_scopes(rows, result['scope']))
                self.assertEqual(checked['status'], 'failed')

    def test_enriched_ranking_remains_a_verified_history_anchor(self):
        result = self.run_local()
        tasks = self.root / 'rank-tasks.sqlite3'
        init_tasks(tasks, self.db)
        worker_started(tasks, 'rank-worker')
        parameters = {**self.scope(), 'question': QUESTION, 'conversation_id': 'rank-session',
                      'model_identity': result['agent_model_identity']}
        task_id = enqueue(tasks, 'analysis', parameters)['task']['task_id']
        claim_task(tasks, 'rank-worker')
        finish_task(tasks, task_id, 'rank-worker', status='succeeded', result=result)
        ranking = restore_ranking(get_task(tasks, task_id))
        self.assertIsNotNone(ranking)
        self.assertEqual(ranking['ranking'][0]['aspect'], 'service')
        self.assertEqual(ranking['ranking'][0]['negative_review_count'], 4)


if __name__ == '__main__':
    unittest.main()
