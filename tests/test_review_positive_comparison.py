"""Native SQL, Agent and HTTP comparisons; no external model or production DB."""
from copy import deepcopy
import hashlib
import json
import unittest

import test_review_positive_trend as fixture
from test_agent import SETTINGS
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_answers import public_answer_text, read_public_answer, synchronize_answer
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.reviews.analytics import compare_products, compare_time_windows


class PositiveComparisonTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PositiveTrendTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.db = self.f.db
        self.scope = {**self.f.scope, 'date_from': '2023-02-01', 'date_to': '2023-02-28',
                      'baseline_from': '2023-01-01', 'baseline_to': '2023-01-31'}

    def run_query(self, question, **scope):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        result = run_review_agent(self.db, question, scope={**self.scope, **scope}, settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['validation']['status'], 'passed')
        self.assertTrue(result['task_acceptance']['complete'], result['task_acceptance'])
        self.assertEqual(result['total_model_calls'], 0)
        self.assertEqual(result['public_answer']['generation_status'], 'template')
        self.assertEqual(result['answer'], public_answer_text(result['public_answer']))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        return result

    def test_window_overall_and_aspect_default_and_explicit_denominators(self):
        for question, expected in [
            ('对比当前窗口与基线的整体好评占比', (3, 4, .75, 2, 3, .666667, 8.333333)),
            ('对比当前窗口与基线的物流好评占比', (2, 3, .666667, 1, 2, .5, 16.666667)),
            ('对比当前窗口与基线的物流好评占成功标注评论比例', (2, 4, .5, 1, 3, .333333, 16.666667)),
        ]:
            with self.subTest(question=question):
                result = self.run_query(question)
                data = result['results']['r1']['data']
                actual = tuple(data[window][key] for window in ('current', 'baseline')
                               for key in ('comparison_count', 'comparison_denominator', 'comparison_rate'))
                self.assertEqual((*actual, data['changes']['comparison_rate_change_pp']), expected)
                self.assertNotIn('负面', result['answer'])
                self.assertEqual(read_public_answer({'status': 'succeeded', 'result': result}), result['public_answer'])

    def test_product_counts_deduplicate_multiple_opinions_and_do_not_merge_products(self):
        for denominator, expected in [(None, [(3, 5, .6), (1, 1, 1.0)]),
                                       ('successful_reviews', [(3, 7, .428571), (1, 1, 1.0)])]:
            scope = {k: v for k, v in self.scope.items() if not k.startswith('baseline_')}
            data = compare_products(self.db, **{**scope, 'date_from': '2023-01-01',
                'product_ids': ['100013588055', 'OTHER']}, sentiment='positive', aspect='logistics', denominator=denominator)
            self.assertEqual([(row['comparison_count'], row['comparison_denominator'], row['comparison_rate'])
                              for row in data['products']], expected)
        result = self.run_query('商品对比：物流好评占比', product_ids=['100013588055', 'OTHER'], date_from='2023-01-01',
                                baseline_from='2022-12-01', baseline_to='2022-12-31')
        self.assertEqual(result['query_plan']['required_tools'], ['compare_products'])
        self.assertEqual(len(result['public_answer']['highlights']), 2)
        self.assertNotIn('负面', result['answer'])

    def test_empty_product_and_window_keep_null_and_no_delta(self):
        result = self.run_query('对比当前窗口与基线的物流好评占比', date_from='2023-04-01', date_to='2023-04-30')
        data = result['results']['r1']['data']
        self.assertIsNone(data['current']['comparison_rate'])
        self.assertIsNone(data['changes']['comparison_rate_change_pp'])
        self.assertIn('没有可用分母', result['answer'])
        result = self.run_query('商品对比：整体好评占比', product_ids=['100013588055', 'OTHER'])
        self.assertEqual(result['results']['r1']['data']['no_reviews_product_ids'], ['OTHER'])
        self.assertIn('暂无可用好评占比', result['answer'])

    def test_natural_two_product_reference_keeps_aspect_and_explicit_denominator(self):
        for question, denominator in [('比较这两款商品的物流好评占比', 'positive_review_count'),
            ('比较这两款商品的物流好评占成功标注评论比例', 'successful_reviews'),
            ('比较商品100013588055和OTHER的物流好评占比', 'positive_review_count')]:
            with self.subTest(question=question):
                result = self.run_query(question, product_ids=['100013588055', 'OTHER'])
                self.assertEqual(result['results']['r1']['data']['aspect'], 'logistics')
                self.assertEqual(result['results']['r1']['data']['rate_denominator'], denominator)

    def test_model_identity_cutoff_and_latest_human_revision_apply_to_positive_counts(self):
        from data_analysis_agent.reviews.store import connect_review_database
        from data_analysis_agent.reviews.human_review import save_human_review
        scope = {k: v for k, v in self.scope.items() if not k.startswith('baseline_')}
        data = compare_products(self.db, **{**scope, 'model': 'unlabelled-version'}, sentiment='positive')
        self.assertIsNone(data['products'][0]['comparison_rate'])
        data = compare_products(self.db, **{**scope, 'as_of': '2023-02-01T00:00:00Z'}, sentiment='positive')
        self.assertEqual(data['products'], [])
        with connect_review_database(self.db, readonly=True) as connection:
            row = connection.execute("SELECT a.id annotation_id,r.id review_id FROM review_annotations a JOIN reviews r ON r.id=a.review_id WHERE r.product_id=? AND r.text=? AND r.published_at>=?",
                ('100013588055', '物流很快', '2023-02-01')).fetchone()
        save_human_review(self.db, row['review_id'], annotation_id=row['annotation_id'], expected_revision_id=None,
                          action='corrected', reviewer='SIMULATED', opinions=[])
        data = compare_products(self.db, **scope, sentiment='positive', aspect='logistics')
        self.assertEqual((data['products'][0]['comparison_count'], data['products'][0]['comparison_denominator']), (1, 2))

    def test_no_positive_is_null_for_aspect_but_zero_for_overall(self):
        scope = {**self.scope, 'date_from': '2023-03-01', 'date_to': '2023-03-31'}
        data = compare_time_windows(self.db, **scope, sentiment='positive', aspect='logistics')
        self.assertIsNone(data['current']['comparison_rate'])
        data = compare_time_windows(self.db, **scope, sentiment='positive')
        self.assertEqual(data['current']['comparison_rate'], 0.0)

    def test_quality_ratings_negative_comparisons_and_composite_need_no_model(self):
        for question, scope in [
            ('查看缺失正文、失败标注和覆盖率', {}), ('查看星级分布', {}),
            ('比较当前窗口与基线窗口', {}), ('商品对比', {'product_ids': ['100013588055', 'OTHER']}),
            ('查看数据质量和星级分布', {}),
        ]:
            with self.subTest(question=question):
                self.run_query(question, **scope)

    def test_optional_interpretation_failure_keeps_verified_comparison(self):
        result = run_review_agent(self.db, '对比当前窗口与基线的物流好评占比并解读', scope=self.scope, settings=SETTINGS)
        self.assertEqual(result['status'], 'ok', result.get('error'))
        self.assertEqual(result['public_answer']['generation_status'], 'fallback')
        self.assertEqual(result['answer'], public_answer_text(result['public_answer']))

    def test_default_negative_payload_is_unchanged(self):
        scope = {k: v for k, v in self.scope.items() if not k.startswith('baseline_')}
        for fn, values in [(compare_products, scope), (compare_time_windows, self.scope)]:
            self.assertEqual(fn(self.db, **values), fn(self.db, **values, sentiment='negative'))
            self.assertNotIn('sentiment', fn(self.db, **values))

    def test_comparison_sentiment_followup_preserves_scope_and_rejects_unsupported_facet_switch(self):
        def followup(previous, question):
            context = {'user': {'task_id': 'previous', 'question': previous['question']},
                'users': [{'task_id': 'previous', 'question': previous['question']}],
                'validated': {'task_id': 'previous', 'scope': previous['scope'],
                    'analysis_type': previous['analysis_intent'], 'sentiment': previous['sentiment_intent'],
                    'queries': [{'kind': previous['results']['r1']['kind'], 'parameters': previous['results']['r1']['parameters']}]}}
            return run_review_agent(self.db, question, scope=previous['scope'], settings=SETTINGS, conversation_context=context)
        first = self.run_query('商品对比：整体差评占比', product_ids=['100013588055', 'OTHER'])
        positive = followup(first, '那好评呢')
        self.assertEqual(positive['status'], 'ok', positive.get('error'))
        self.assertEqual(positive['results']['r1']['kind'], 'comparison')
        self.assertEqual(positive['results']['r1']['data']['sentiment'], 'positive')
        self.assertEqual(positive['scope'], first['scope'])
        self.assertEqual(positive['total_model_calls'], 0)
        first = self.run_query('对比当前窗口与基线的物流好评占比')
        negative = followup(first, '那差评呢')
        self.assertEqual(negative['status'], 'unsupported')
        self.assertEqual(negative['query_executions'], 0)

    def test_tampered_comparison_direction_denominator_counts_and_delta_rejected(self):
        result = self.run_query('对比当前窗口与基线的物流好评占比')
        def check(results):
            return validate_report(results, result['result_ids'], sentiment_intent='positive',
                query_plan=build_query_plan(result['question'], self.scope),
                expected_scopes=expected_result_scopes(results, self.scope))['status']
        for path, value in [(('sentiment',), 'negative'), (('aspect',), 'service'),
            (('rate_denominator',), 'successful_reviews'), (('current', 'comparison_count'), 3),
            (('current', 'comparison_denominator'), 4), (('current', 'comparison_rate'), .5),
            (('changes', 'comparison_rate_change_pp'), 16.6667), (('comparison_aspects',), [])]:
            with self.subTest(path=path):
                results = deepcopy(result['results']); target = results['r1']['data']
                for key in path[:-1]: target = target[key]
                target[path[-1]] = value
                self.assertEqual(check(results), 'failed')

    def test_conflicting_goal_stops_before_query_and_unknown_plan_is_not_direct(self):
        question = '执行本轮已确认的只读查询\n' + json.dumps({'tool': 'compare_time_windows',
            'parameters': {'sentiment': 'positive', 'aspect': 'logistics'}, 'question': '对比当前窗口与基线的差评占比'})
        result = run_review_agent(self.db, question, scope=self.scope, settings=SETTINGS)
        self.assertNotEqual(result['status'], 'ok')
        self.assertEqual(result['query_executions'], 0)
        plan = build_query_plan('对比当前窗口与基线的好评占比', self.scope)
        self.assertTrue(plan.deterministic())
        plan.requirements[0]['parameters']['denominator'] = 'review_count'
        self.assertFalse(plan.deterministic())

    def test_legacy_answer_and_sections_come_from_public_trend_and_reject_negative_primary_metric(self):
        result = self.run_query('按月分析物流好评占比趋势')
        self.assertNotIn('负面', result['answer'])
        for section in result['answer_sections']:
            self.assertEqual(section['text'], public_answer_text(result['public_answer'], result_id=section['result_id']))
        result['public_answer']['metrics'][0]['values'].append({'name': 'negative_rate', 'value': .5})
        with self.assertRaises(ValueError): synchronize_answer(result)

    def test_http_positive_product_and_window_comparison_and_invalid_options(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from data_analysis_agent.api.routes.analytics import create_router
        app = FastAPI(); app.include_router(create_router(self.db), prefix='/api/v1')
        with TestClient(app) as client:
            for endpoint in ('compare-products', 'compare-windows'):
                params = {k: v for k, v in self.scope.items() if v is not None
                          and (endpoint == 'compare-windows' or not k.startswith('baseline_'))}
                params.update(sentiment='positive', aspect='logistics')
                response = client.get('/api/v1/analytics/' + endpoint, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()['result']
                current = data['current'] if endpoint == 'compare-windows' else data['products'][0]
                self.assertEqual(current['comparison_rate'], .666667)
                for invalid in ({'sentiment': 'all'}, {'aspect': 'unknown'}, {'denominator': 'review_count'},
                                {'aspect': None, 'denominator': 'positive_review_count'}):
                    selected = {k: v for k, v in {**params, **invalid}.items() if v is not None}
                    self.assertEqual(client.get('/api/v1/analytics/' + endpoint, params=selected).status_code, 422)


if __name__ == '__main__': unittest.main()
