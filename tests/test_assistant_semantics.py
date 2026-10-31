"""口语意图、只读统计及真实引用回归；数据和模型均受控。"""

import csv
import hashlib
import json
import unittest
from contextlib import closing
from copy import deepcopy
from unittest.mock import patch

from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
import test_review_plan as plan_fixture
from test_agent import SETTINGS
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import resolve_question_scope
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import import_amazon_jsonl, connect_review_database


QUESTIONS = [
    '美的空调的使用效果主要集中在哪些方面？', '美的空调的使用效果主要集中在哪些地方？',
    '这款空调用起来怎么样？', '它的制冷效果怎么样？', '消费者主要吐槽什么？',
    '好评主要集中在哪些方面？', '使用效果里面，哪些问题被提及最多？',
    '和上一时间段相比有什么变化？', '换成另一款商品后，用户的评价有什么不同？', '这个商品有什么问题？',
]


class AssistantSemanticsTests(WorkbenchFixture, unittest.TestCase):
    execute_plan = plan_fixture.QueryPlanTests.execute_plan
    run_sync = plan_fixture.QueryPlanTests.run_sync

    def setUp(self):
        super().setUp()
        path = self.root / 'air-conditioner.csv'
        rows = [dict(product_id=product, text=text, user_id=str(index), published_at=day, product_title=title)
                for index, (product, text, day, title) in enumerate([
                    ('100013588053', '制冷很快，制冷很快，声音小', '2021-06-01', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷很快，噪音大', '2021-06-02', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷不够', '2021-06-03', '美的 1.5 匹风酷空调'),
                    ('100013588053', '待处理', '2021-06-04', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷很快', '2020-09-01', '美的 1.5 匹风酷空调'),
                    ('OTHER', '制冷很快', '2021-06-01', '另一款空调'),
                ])]
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        self.identity = {**IDENTITY, 'prompt_version': 'review-opinions-v1'}
        for review in pending_reviews(self.db, **self.identity, source='reviews_csv', limit=100):
            text = review['text']
            if text == '待处理':
                continue
            values = [opinion('effectiveness', 'positive', '制冷很快')] if '制冷很快' in text else [opinion('effectiveness', 'negative', '制冷不够')]
            if text.count('制冷很快') == 2:
                values.append(opinion('effectiveness', 'positive', '制冷很快'))
            # validate_opinions 负责把重复表述定位到原文，数据库保留观点单位。
            values = validate_opinions(json.dumps({'opinions': values}), text)
            save_annotation(self.db, review, **self.identity, status='success', opinions=values)

    def scope(self, **changes):
        return {**self.identity, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
                'product_ids': ['100013588053'], 'date_from': '2021-06-01', 'date_to': '2021-06-30',
                'baseline_from': '2020-08-25', 'baseline_to': '2020-09-30', **changes}

    def test_ten_questions_choose_supported_plan_or_request_missing_comparison(self):
        tools = ['get_opinion_breakdown'] * 4 + ['get_top_issues', 'get_positive_aspects',
                 'get_opinion_breakdown', 'compare_time_windows', 'compare_products', 'get_top_issues']
        for index, (question, tool) in enumerate(zip(QUESTIONS, tools)):
            with self.subTest(question=question):
                plan = build_query_plan(question, self.scope())
                self.assertEqual(plan.required_tools, [tool])
                if index == 8:
                    self.assertIn('对比商品', plan.unsupported_reason)
                    continue
                self.assertIsNone(plan.unsupported_reason)
                result = self.run_sync(question)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                self.assertEqual(result['validation']['status'], 'passed')
                self.assertEqual(result['scope']['product_ids'], ['100013588053'])
                self.assertEqual(result['scope']['dataset_version'], 'reviews-csv')
                self.assertTrue(result['public_answer']['source_result_ids'])
                for item in result['results'].values():
                    self.assertEqual(item['data']['scope']['source'], 'reviews_csv')
                    self.assertEqual(item['data']['scope']['product_ids'], ['100013588053'])
        self.assertEqual(build_query_plan(QUESTIONS[0], self.scope()).requirements,
                         build_query_plan(QUESTIONS[1], self.scope()).requirements)
        self.assertIsNone(build_query_plan(QUESTIONS[8], self.scope(product_ids=['100013588053', 'OTHER'])).unsupported_reason)

    def test_breakdown_uses_population_counts_and_traceable_samples(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = run_review_agent(self.db, QUESTIONS[0], scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        data = result['results']['r1']['data']
        self.assertEqual((data['summary']['review_count'], data['summary']['successful_reviews']), (4, 3))
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['groups'][0]['review_count'], 2)
        self.assertGreaterEqual(data['groups'][0]['opinion_count'], 2)
        self.assertNotIn('baseline', data)
        self.assertTrue(result['public_answer']['evidence_refs'])
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        corrupt = deepcopy(result['results'])
        corrupt['r1']['data']['groups'][0]['review_count'] = 999
        self.assertEqual(validate_report(corrupt, result['result_ids'])['status'], 'failed')
        corrupt = deepcopy(result['results'])
        corrupt['r1']['data']['items'][0]['opinions'][0]['quote'] = '编造观点'
        self.assertEqual(validate_report(corrupt, result['result_ids'])['status'], 'failed')

    def test_negative_ranking_does_not_query_baseline(self):
        with patch('data_analysis_agent.agents.review_tools.analytics.get_top_issues', wraps=analytics.get_top_issues) as query:
            result = self.run_sync(QUESTIONS[9])
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('baseline_from', query.call_args.kwargs)
        self.assertNotIn('baseline', result['results']['r1']['data'])

    def test_empty_topic_and_empty_baseline_report_data_limits(self):
        result = run_review_agent(self.db, '它的静音效果怎么样？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['results']['r1']['data']['total'], 0)
        self.assertTrue(result['public_answer']['highlights'])
        result = self.run_sync(QUESTIONS[7])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['results']['r1']['data']['baseline']['review_count'], 1)

    def test_explicit_scope_precedes_page_and_does_not_cross_dataset(self):
        with closing(connect_review_database(self.db, readonly=True)) as connection:
            products = connection.execute("SELECT product_id,title FROM products WHERE source='reviews_csv'").fetchall()
        resolved = resolve_question_scope('查询 OTHER 在 2020-09-01 至 2020-09-30 的使用效果', self.scope(), products)
        self.assertEqual(resolved['product_ids'], ['OTHER'])
        self.assertEqual(resolved['date_from'], '2020-09-01')
        self.assertEqual(resolved['dataset_version'], 'reviews-csv')
        selected = self.scope(product_ids=['OTHER'])
        result = run_review_agent(self.db, '100013588053 的使用效果主要集中在哪些方面', scope=selected, settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['100013588053'])


if __name__ == '__main__':
    unittest.main()
