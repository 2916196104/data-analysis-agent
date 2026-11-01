"""口语意图、只读统计及真实引用回归；数据和模型均受控。"""

import csv
import hashlib
import json
import unittest
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
import test_review_plan as plan_fixture
from test_agent import SETTINGS
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import QuestionScopeError, resolve_question_scope
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_prompts import detect_sentiment_directions
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


class AssistantSemanticsFixture(WorkbenchFixture):
    execute_plan = plan_fixture.QueryPlanTests.execute_plan
    run_sync = plan_fixture.QueryPlanTests.run_sync

    def setUp(self):
        super().setUp()
        path = self.root / 'air-conditioner.csv'
        rows = [dict(product_id=product, text=text, user_id=str(index), published_at=day, product_name=title,
                     brand='美的' if title.startswith('美的') else '小米' if title.startswith('小米') else '其他')
                for index, (product, text, day, title) in enumerate([
                    ('100013588053', '制冷很快，制冷很快，声音小', '2021-06-01', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷很快，噪音大', '2021-06-02', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷不够', '2021-06-03', '美的 1.5 匹风酷空调'),
                    ('100013588053', '待处理', '2021-06-04', '美的 1.5 匹风酷空调'),
                    ('100013588053', '制冷很快', '2020-09-01', '美的 1.5 匹风酷空调'),
                    ('OTHER', '制冷很快', '2021-06-01', '另一款空调'),
                    ('XIAOMI-TEST', '智能联动方便，安装收费高', '2021-06-01', '小米 米家 1.5 匹新一级能效空调'),
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
            if '智能联动方便' in text:
                values = [opinion('effectiveness', 'positive', '智能联动方便'), opinion('service', 'negative', '安装收费高')]
            else:
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


class AssistantSemanticsTests(AssistantSemanticsFixture, unittest.TestCase):
    def test_ten_questions_choose_supported_plan_or_request_missing_comparison(self):
        tools = ['get_opinion_breakdown'] * 4 + ['get_top_issues', 'get_positive_aspects',
                 'get_aspect_drilldown', 'compare_time_windows', 'compare_products', 'get_top_issues']
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

    def test_pros_cons_compares_both_named_products_without_model_or_business_writes(self):
        question = '美的空调与小米空调的优缺点都有哪些？'
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with patch('data_analysis_agent.agents.reviews.create_llm') as planner, patch('openai.OpenAI') as model:
            result = run_review_agent(self.db, question, scope=self.scope(), settings=SETTINGS)
        planner.assert_not_called()
        model.assert_not_called()
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['100013588053', 'XIAOMI-TEST'])
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual((result['query_executions'], result['total_model_calls']), (5, 0))
        self.assertEqual(result['analysis_intent'], 'pros_cons')
        groups = {(row['data']['scope']['product_ids'][0], row['data']['sentiment']): row['data']
                  for key, row in result['results'].items() if key != 'overview'}
        self.assertEqual(set(groups), {(p, s) for p in ('100013588053', 'XIAOMI-TEST') for s in ('positive', 'negative')})
        positive = groups[('100013588053', 'positive')]['groups'][0]
        self.assertEqual((positive['review_count'], positive['opinion_count']), (2, 2))
        answer = result['public_answer']
        self.assertEqual((answer['status'], answer['generation_status']), ('ok', 'template'))
        self.assertEqual(len(answer['highlights']), 4)
        for title in ('美的 1.5 匹风酷空调', '小米 米家 1.5 匹新一级能效空调'):
            for direction in ('优点（正面评价）', '缺点（负面评价）'):
                self.assertTrue(any(title in row['text'] and direction in row['text'] for row in answer['highlights']))
        self.assertTrue(set(answer['source_result_ids']) == set(result['result_ids']))
        self.assertIn('共 4 条评论，成功标注 3 条，覆盖率 75.0%', answer['sample_note'])
        self.assertEqual(len(answer['evidence_refs']), 4)
        task = {'status': 'succeeded', 'error': None, 'result': result}
        self.assertEqual(read_public_answer(task), answer)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_mixed_spellings_and_missing_negative_mentions_keep_both_directions(self):
        for question in ('优缺点都有哪些？', '优点和缺点分别是什么？', '好评和差评主要说什么？'):
            with self.subTest(question=question), patch('openai.OpenAI') as model:
                self.assertEqual(detect_sentiment_directions(question), {'positive', 'negative'})
                result = run_review_agent(self.db, question, scope=self.scope(product_ids=['OTHER']), settings=SETTINGS)
                self.assertEqual(result['status'], 'ok', result.get('error'))
                text = '\n'.join(row['text'] for row in result['public_answer']['highlights'])
                self.assertIn('优点（正面评价）', text)
                self.assertIn('缺点（负面评价）：当前范围没有匹配', text)
                self.assertNotIn('没有缺点', text)
                model.assert_not_called()

    def test_named_brands_aggregate_all_matching_models_without_choosing_arbitrary_skus(self):
        path = self.root / 'more-models.csv'
        rows = [dict(product_id='MIDEA-TEST2', text='质量稳定', user_id='m2', published_at='2021-06-02', product_name='美的 3匹风酷空调', brand='美的'),
                dict(product_id='XIAOMI-TEST2', text='售后回复慢', user_id='x2', published_at='2021-06-02', product_name='小米 3匹空调', brand='小米')]
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **self.identity, source='reviews_csv', limit=100):
            if review['text'] not in {'质量稳定', '售后回复慢'}:
                continue
            values = [opinion('quality', 'positive', '质量稳定')] if review['text'] == '质量稳定' else [opinion('service', 'negative', '售后回复慢')]
            save_annotation(self.db, review, **self.identity, status='success', opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))
        with patch('data_analysis_agent.agents.reviews.create_llm') as planner, patch('openai.OpenAI') as model:
            result = run_review_agent(self.db, '美的空调与小米空调的优缺点都有哪些？', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['scope']['product_ids'], ['100013588053', 'MIDEA-TEST2', 'XIAOMI-TEST', 'XIAOMI-TEST2'])
        self.assertEqual((result['query_executions'], result['total_model_calls']), (5, 0))
        self.assertEqual([group['label'] for group in result['query_plan']['product_groups']], ['美的空调', '小米空调'])
        self.assertEqual(len(result['public_answer']['highlights']), 6)
        text = '\n'.join(row['text'] for row in result['public_answer']['highlights'])
        self.assertIn('美的空调（2 款商品）的优点', text)
        self.assertIn('小米空调（2 款商品）的缺点', text)
        self.assertIn('质量稳定', text); self.assertIn('售后回复慢', text)
        self.assertIn('美的空调：共 5 条评论', result['public_answer']['sample_note'])
        self.assertEqual(read_public_answer({'status': 'succeeded', 'error': None, 'result': result}), result['public_answer'])
        planner.assert_not_called(); model.assert_not_called()

    def test_partial_or_ambiguous_named_product_match_does_not_use_page_as_comparison(self):
        catalog = [{'product_id': 'M1', 'title': '美的 1.5 匹风酷空调'},
                   {'product_id': 'M2', 'title': '美的 3 匹客厅空调'},
                   {'product_id': 'X1', 'title': '小米 米家智能空调'}]
        with self.assertRaisesRegex(QuestionScopeError, '对应多款商品'):
            resolve_question_scope('美的空调与小米空调的优缺点', self.scope(), catalog)
        branded = [{**row, 'title': row['title'].replace('客厅', '风酷'), 'brand': '小米' if row['product_id'] == 'X1' else '美的'} for row in catalog]
        with self.assertRaisesRegex(QuestionScopeError, '对应多款商品'):
            resolve_question_scope('美的风酷与小米空调的优缺点', self.scope(), branded)
        unique = [catalog[0], catalog[2]]
        self.assertEqual(resolve_question_scope('美的与小米的优缺点', self.scope(), unique)['product_ids'], ['M1', 'X1'])
        with patch('data_analysis_agent.agents.reviews.create_llm') as planner:
            result = run_review_agent(self.db, '美的空调与未知品牌空调的优缺点', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('未找到', result['answer'])
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['result_ids'], [])
        self.assertIn('未找到', result['public_answer']['summary'])
        self.assertEqual(read_public_answer({'status': 'succeeded', 'error': None, 'result': result})['summary'], result['answer'])
        self.assertEqual(read_public_answer({'status': 'succeeded', 'result': ['invalid historical result']})['status'], 'failed')
        planner.assert_not_called()

    def test_pros_cons_honors_query_budget_and_rejects_unhandled_composite_requests(self):
        with patch('data_analysis_agent.agents.reviews.create_llm') as planner:
            result = run_review_agent(self.db, '美的空调与小米空调的优缺点', scope=self.scope(),
                                      settings=replace(SETTINGS, max_tool_calls=4))
        self.assertEqual(result['status'], 'unsupported')
        self.assertIn('预算', result['answer'])
        self.assertEqual(result['query_executions'], 0)
        planner.assert_not_called()
        for question in ('按月查看优缺点趋势', '比较当前与基线的好评和差评', '制冷和静音的优缺点', '好评和差评各给5条原文'):
            self.assertIsNotNone(build_query_plan(question, self.scope()).unsupported_reason)

    def test_pros_cons_cannot_publish_invalid_counts_or_missing_annotations(self):
        original = analytics.get_opinion_breakdown
        with patch('data_analysis_agent.agents.review_tools.analytics.get_opinion_breakdown', wraps=analytics.get_opinion_breakdown) as query:
            def invalid(*args, **kwargs):
                data = original(*args, **kwargs)
                data['groups'][0]['review_count'] = 999
                return data
            query.side_effect = invalid
            result = run_review_agent(self.db, '优缺点有哪些', scope=self.scope(), settings=SETTINGS)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['validation']['status'], 'failed')
        self.assertNotIn('answer', result)
        self.assertEqual(result['public_answer']['status'], 'failed')
        result = run_review_agent(self.db, '优缺点有哪些', scope=self.scope(model='unlabeled-version'), settings=SETTINGS)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['public_answer']['highlights']), 2)
        self.assertTrue(any('暂无成功标注' in message for message in result['public_answer']['caveats']))


if __name__ == '__main__':
    unittest.main()
