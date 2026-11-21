"""Positive UTC trends use real SQLite aggregates and the native Agent without models."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
from scripts.frontend_fixture import seed_positive_trend
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_compression import read_public_analysis
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.reviews.analytics import get_trend, get_aspect_trend

QUESTION = '按月分析物流好评占比趋势。'


class PositiveTrendTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.scope = {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
            'product_ids': ['100013588055'], 'date_from': '2023-01-01', 'date_to': '2023-04-30',
            'baseline_from': '2022-12-01', 'baseline_to': '2022-12-31', 'as_of': None}
        for name in ('openai.OpenAI', 'data_analysis_agent.agents.reviews.create_llm',
                     'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            self.enterContext(patch(name, side_effect=AssertionError('no model/retrieval: ' + name)))
        seed_positive_trend(self.db, self.root, IDENTITY)

    def run_positive(self, question=QUESTION, *, context=None, **changes):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = run_review_agent(self.db, question, scope={**self.scope, **changes}, settings=SETTINGS,
                                  conversation_context=context)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['public_answer']['generation_status'], 'template')
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        return result

    def test_monthly_counts_denominators_nulls_scope_and_public_history(self):
        result = self.run_positive()
        self.assertEqual(result['query_plan']['required_tools'], ['get_aspect_trend'])
        self.assertEqual(result['sentiment_intent'], 'positive')
        data = result['results']['r1']['data']
        self.assertEqual(data['sentiment'], 'positive')
        self.assertEqual(data['summary']['successful_reviews'], 8)
        self.assertEqual(data['summary']['positive_review_count'], 5)
        expected = [('2023-01', 1, 2, .5), ('2023-02', 2, 3, .666667),
                    ('2023-03', 0, 0, None), ('2023-04', 0, 0, None)]
        for bucket, (month, count, denominator, rate) in zip(data['buckets'], expected):
            self.assertEqual(bucket['bucket'], month)
            self.assertEqual(bucket['aspects']['logistics']['positive_review_count'], count)
            self.assertEqual(bucket['positive_review_count'], denominator)
            self.assertEqual(bucket['aspects']['logistics']['positive_rate'], rate)
        answer = result['public_answer']
        text = answer['summary'] + ''.join(row['text'] for row in answer['highlights']) + ''.join(answer['caveats'])
        self.assertIn('2023-01：正向评论 1 条，分母 2 条含正向观点评论，占比 50.0%', text)
        self.assertIn('变化 +16.7 个百分点', text)
        self.assertIn('暂无占比', text)
        self.assertNotIn('负面', text)
        point = result['compact_context']['statistics'][0]['trends'][0]['points'][0]
        self.assertEqual(point['metric']['denominator_name'], 'positive_review_count')
        task = {'status': 'succeeded', 'result': result}
        self.assertEqual(read_public_analysis(task)['status'], 'ok')
        self.assertEqual(read_public_answer(task), answer)

    def test_overall_positive_rate_uses_successful_reviews(self):
        result = self.run_positive('按月分析整体好评占比趋势。')
        self.assertEqual(result['query_plan']['required_tools'], ['get_trend'])
        point = result['compact_context']['statistics'][0]['trends'][0]['points'][0]
        self.assertEqual(point['metric']['denominator_name'], 'successful_reviews')
        self.assertEqual((point['metric']['numerator'], point['metric']['denominator']), (2, 3))

    def test_no_positive_month_keeps_null_while_no_matching_aspect_is_zero(self):
        result = self.run_positive(date_from='2023-03-01', date_to='2023-03-31')
        self.assertIsNone(result['results']['r1']['data']['buckets'][0]['aspects']['logistics']['positive_rate'])
        self.assertIn('无可用的正向观点评论分母', result['public_answer']['highlights'][0]['text'])
        effect = self.run_positive('按月分析使用效果好评占比趋势。')
        self.assertEqual(effect['results']['r1']['data']['buckets'][0]['aspects']['effectiveness']['positive_rate'], 0.0)

    def test_day_followup_keeps_positive_aspect_and_negative_switch_keeps_trend(self):
        first = self.run_positive()
        context = {'user': {'task_id': 'positive', 'question': QUESTION},
            'users': [{'task_id': 'positive', 'question': QUESTION}],
            'validated': {'task_id': 'positive', 'scope': first['scope'], 'analysis_type': 'aspect_trend',
                'sentiment': 'positive', 'queries': [{'kind': 'aspect_trend', 'parameters': first['results']['r1']['parameters']}]}}
        daily = self.run_positive('改为按日', context=context)
        self.assertEqual(daily['results']['r1']['parameters']['sentiment'], 'positive')
        self.assertEqual(daily['results']['r1']['data']['period'], 'day')
        negative = self.run_positive('那差评呢？', context=context)
        self.assertEqual(negative['query_plan']['required_tools'], ['get_aspect_trend'])
        self.assertEqual(negative['sentiment_intent'], 'negative')

    def test_wrong_sentiment_and_denominator_or_monthly_counts_are_rejected(self):
        result = self.run_positive()
        for mutate in (lambda d: d.update(sentiment='negative'),
                       lambda d: d['definitions'].update(positive_aspect_rate_denominator='successful_reviews'),
                       lambda d: d['buckets'][0]['aspects']['logistics'].update(positive_rate=1/3),
                       lambda d: d['buckets'][0].update(positive_review_count=20)):
            with self.subTest(mutate=mutate):
                results = deepcopy(result['results']); mutate(results['r1']['data'])
                self.assertEqual(validate_report(results, result['result_ids'], sentiment_intent='positive',
                    query_plan=build_query_plan(QUESTION, self.scope),
                    expected_scopes=expected_result_scopes(results, self.scope))['status'], 'failed')

    def test_confirmed_positive_trend_and_conflicting_original_direction(self):
        params = {'aspect': 'logistics', 'sentiment': 'positive', 'period': 'month'}
        wrapper = lambda question: '执行本轮已确认的只读查询\n' + json.dumps(
            {'tool': 'get_aspect_trend', 'parameters': params, 'question': question}, ensure_ascii=False)
        self.run_positive(wrapper(QUESTION))
        result = run_review_agent(self.db, wrapper('按月分析物流差评占比趋势。'), scope=self.scope, settings=SETTINGS)
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(result['query_executions'], 0)
        self.assertEqual(result['total_model_calls'], 0)

    def test_default_negative_analytics_payload_is_unchanged(self):
        scope = {k:v for k,v in self.scope.items() if not k.startswith('baseline_')}
        self.assertEqual(get_trend(self.db, **scope), get_trend(self.db, **scope, sentiment='negative'))
        self.assertNotIn('sentiment', get_trend(self.db, **scope))
        self.assertEqual(get_aspect_trend(self.db, **scope, aspect='logistics'),
                         get_aspect_trend(self.db, **scope, aspect='logistics', sentiment='negative'))

    def test_http_positive_trend_and_invalid_sentiment(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from data_analysis_agent.api.routes.analytics import create_router
        app = FastAPI(); app.include_router(create_router(self.db), prefix='/api/v1')
        params = {k:v for k,v in self.scope.items() if not k.startswith('baseline_') and v is not None}
        with TestClient(app) as client:
            response = client.get('/api/v1/analytics/aspect-trend', params={**params, 'aspect': 'logistics', 'sentiment': 'positive'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['result']['buckets'][0]['aspects']['logistics']['positive_rate'], .5)
            self.assertEqual(client.get('/api/v1/analytics/trend', params={**params, 'sentiment': 'all'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
