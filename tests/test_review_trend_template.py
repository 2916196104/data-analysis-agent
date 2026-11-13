"""P7-A: isolated SQLite + production Agent; no network, LLM or query encoding."""
import csv
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_answers import AnswerGenerationStrategy
from data_analysis_agent.agents.review_acceptance import validate_task_completion
from data_analysis_agent.agents.review_compression import CompactAnalysisContext
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.store import import_amazon_jsonl


QUESTION = '商品100013588055的物流负面评价按月有什么变化？'


class TrendTemplateTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.scope = {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
            'product_ids': ['100013588055'], 'date_from': '2023-01-01', 'date_to': '2023-03-31',
            'baseline_from': '2022-12-01', 'baseline_to': '2022-12-31', 'as_of': None}
        for target in ('openai.OpenAI', 'openai.AsyncOpenAI', 'socket.socket.connect',
                       'data_analysis_agent.agents.reviews.create_llm',
                       'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            self.enterContext(patch(target, side_effect=AssertionError('P7-A offline/zero-model only: ' + target)))

    def seed(self, months, *, annotate=True):
        rows = []
        for month, total, negative in months:
            for index in range(total):
                rows.append(dict(product_id='100013588055', user_id=f'{month}-{index}',
                    published_at=month + '-15', product_name='趋势测试商品',
                    text='物流很慢，送货很慢' if index < negative else '物流很快'))
        # Out-of-scope product and dates must never contribute to this task.
        rows += [dict(product_id=product, user_id=f'excluded-{index}', published_at=day,
                      product_name='范围对照', text='物流很慢，送货很慢')
                 for index, (product, day) in enumerate([
                     ('OTHER', '2023-01-15'), ('100013588055', '2022-12-15'),
                     ('100013588055', '2023-04-15')])]
        path = self.root / 'p7-trend.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        if annotate:
            for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', limit=1000):
                quotes = ['物流很慢', '送货很慢'] if '物流很慢' in review['text'] else ['物流很快']
                direction = 'negative' if len(quotes) == 2 else 'positive'
                values = [opinion('logistics', direction, quote) for quote in quotes]
                save_annotation(self.db, review, **IDENTITY, status='success',
                    opinions=validate_opinions(json.dumps({'opinions': values}), review['text']))

    def run_trend(self, question=QUESTION, *, required_tool='get_aspect_trend', **changes):
        scope = {**self.scope, **changes}
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = run_review_agent(self.db, question, scope=scope, settings=SETTINGS)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertEqual(result['task_acceptance']['status'], 'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['answer_generation']['attempts'], 0)
        self.assertEqual(result['public_answer']['generation_status'], 'template')
        self.assertEqual(result['query_plan']['required_tools'], [required_tool])
        self.assertEqual(result['user_intent']['query_type'], 'trend')
        self.assertEqual(result['user_intent']['aspect'], 'logistics' if required_tool == 'get_aspect_trend' else None)
        # Overall trend reuses the context query's cached get_trend result.
        self.assertEqual(result['query_executions'], 2 if required_tool == 'get_aspect_trend' else 1)
        return result

    def text(self, result):
        answer = result['public_answer']
        return answer['summary'] + ''.join(row['text'] for row in answer['highlights']) + ''.join(answer['caveats'])

    def test_nonempty_real_sql_months_counts_rates_sources_and_completion(self):
        self.seed([('2023-01', 30, 3), ('2023-02', 40, 8), ('2023-03', 50, 5)])
        result = self.run_trend()
        self.assertEqual(result['results']['r1']['data']['summary']['successful_reviews'], 120)
        answer = result['public_answer']; text = self.text(result)
        for month, count, denominator, rate in [('2023-01', 3, 30, '10.0%'),
                ('2023-02', 8, 40, '20.0%'), ('2023-03', 5, 50, '10.0%')]:
            self.assertIn(f'{month}：负面评论 {count} 条，分母 {denominator} 条成功标注评论，占比 {rate}', text)
        self.assertIn('有升有降', text)
        self.assertNotIn('持续', text)
        self.assertEqual(answer['source_result_ids'], ['r1'])
        self.assertEqual(answer['scope_summary'][0]['product_ids'], ['100013588055'])
        self.assertIn('2023-01-01 至 2023-03-31', answer['summary'])
        self.assertEqual([metric['bucket'] for metric in answer['metrics'][0]['values'] if 'bucket' in metric],
                         ['2023-01', '2023-02', '2023-03'])

    def test_empty_months_are_null_not_zero_rates(self):
        self.seed([])
        result = self.run_trend()
        self.assertIn('各时间桶均无成功标注', self.text(result))
        self.assertIn('占比 暂无占比', self.text(result))
        self.assertNotIn('0.0%', self.text(result))

    def test_single_valid_month_does_not_claim_trend(self):
        self.seed([('2023-01', 30, 3)])
        result = self.run_trend(date_to='2023-01-31')
        self.assertIn('暂不能判断稳定趋势', self.text(result))
        self.assertNotIn('相邻时段', self.text(result))

    def test_low_samples_keep_actual_values_and_limitations(self):
        self.seed([('2023-01', 2, 1), ('2023-02', 3, 2), ('2023-03', 4, 1)])
        text = self.text(self.run_trend())
        self.assertIn('样本量较少', text)
        self.assertIn('暂不能判断稳定趋势', text)
        self.assertIn('分母 3 条成功标注评论，占比 66.7%', text)

    def test_same_count_different_denominators_change_rate_only(self):
        self.seed([('2023-01', 20, 5), ('2023-02', 50, 5), ('2023-03', 25, 5)])
        text = self.text(self.run_trend())
        self.assertIn('5 → 5 条，数量变化 +0 条', text)
        self.assertIn('变化 +10.0 个百分点', text)
        self.assertIn('数量变化与占比变化分别计算', text)

    def test_increasing_count_can_have_decreasing_rate(self):
        self.seed([('2023-01', 20, 5), ('2023-02', 40, 8), ('2023-03', 100, 10)])
        text = self.text(self.run_trend())
        self.assertIn('8 → 10 条，数量变化 +2 条', text)
        self.assertIn('变化 -10.0 个百分点', text)
        self.assertIn('负面占比呈下降变化', text)

    def test_unannotated_data_does_not_become_zero_negative_rate(self):
        self.seed([('2023-01', 30, 3)], annotate=False)
        text = self.text(self.run_trend())
        self.assertIn('暂无成功标注', text)
        self.assertIn('分母 0 条成功标注评论，占比 暂无占比', text)

    def invalid_result(self, mutate):
        self.seed([('2023-01', 30, 3)])
        result = self.run_trend()
        mutate(result['results']['r1']['data'])
        plan = build_query_plan(QUESTION, result['scope'])
        checked = validate_report(result['results'], result['result_ids'],
            sentiment_intent='negative', expected_scope=result['scope'], query_plan=plan)
        self.assertEqual(checked['status'], 'failed')
        acceptance = validate_task_completion(result['task_goal'], result['scope'], plan,
            [result['results'][key] for key in result['result_ids']])
        self.assertEqual(acceptance['status'], 'failed')

    def test_cross_product_contamination_is_rejected(self):
        self.invalid_result(lambda data: data['scope'].update(product_ids=['OTHER']))

    def test_cross_date_contamination_is_rejected(self):
        self.invalid_result(lambda data: data['scope'].update(date_from='2022-12-01'))

    def test_inconsistent_denominator_is_rejected(self):
        self.invalid_result(lambda data: data['buckets'][0].update(successful_reviews=300))

    def test_missing_bucket_is_rejected(self):
        self.invalid_result(lambda data: data['buckets'].pop())

    def test_reordered_bucket_is_rejected(self):
        self.invalid_result(lambda data: data['buckets'].reverse())

    def test_out_of_range_bucket_is_rejected(self):
        self.invalid_result(lambda data: data['buckets'][0].update(bucket='2022-12'))

    def test_day_period_and_partial_month_are_explicit(self):
        self.seed([('2023-01', 30, 3)])
        result = self.run_trend(QUESTION.replace('按月', '按日'), date_from='2023-01-14', date_to='2023-01-16')
        self.assertIn('2023-01-15：负面评论 3 条', self.text(result))
        monthly = self.run_trend(date_from='2023-01-14', date_to='2023-01-16')
        self.assertIn('未覆盖完整自然月', self.text(monthly))

    def test_compressed_long_series_discloses_omissions(self):
        months = [(f'{2020 + index // 12}-{index % 12 + 1:02}', 1, 1) for index in range(36)]
        self.seed(months)
        result = self.run_trend(date_from='2020-01-01', date_to='2022-12-31',
                                baseline_from='2019-12-01', baseline_to='2019-12-31')
        series = result['compact_context']['statistics'][0]['trends'][0]
        self.assertGreater(series['omitted_point_count'], 0)
        self.assertIn(f"省略 {series['omitted_point_count']} 个", self.text(result))
        self.assertIn('完整时序见原始统计明细', self.text(result))

    def test_trend_does_not_publish_advice_from_question(self):
        self.seed([('2023-01', 30, 3)])
        result = self.run_trend(QUESTION + '请给出改善建议。')
        self.assertNotIn('建议优先复核', self.text(result))
        self.assertFalse(result['public_answer']['insights'])

    def test_overall_trend_uses_same_zero_model_pipeline(self):
        self.seed([('2023-01', 30, 3)])
        result = self.run_trend(QUESTION.replace('物流', '整体'), required_tool='get_trend')
        self.assertIn('整体负面评价', self.text(result))

    def test_unsupported_positive_trend_and_cause_do_not_fall_back_to_model(self):
        self.seed([('2023-01', 30, 3)])
        for question in ('商品100013588055的好评按月有什么变化？',
                         '商品100013588055物流负面评价增加是什么原因？'):
            with self.subTest(question=question):
                result = run_review_agent(self.db, question, scope=self.scope, settings=SETTINGS)
                self.assertEqual(result['status'], 'unsupported')
                self.assertEqual(result['query_executions'], 0)
                self.assertEqual(result['total_model_calls'], 0)

    def test_comparison_remains_outside_trend_template_policy(self):
        from test_review_answers import AnswerTests
        fixture = AnswerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        _, raw = fixture.context('比较两个商品的主要负面问题', product_ids=['P1', 'P2'])
        from data_analysis_agent.agents.review_answer_facts import build_answer_facts
        context = CompactAnalysisContext.model_validate(raw)
        facts, _ = build_answer_facts(context)
        self.assertEqual(AnswerGenerationStrategy.choose(context, facts), 'llm')


if __name__ == '__main__':
    unittest.main()
