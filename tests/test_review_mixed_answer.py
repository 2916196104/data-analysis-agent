"""Three-part mixed answers: real isolated SQL, complete dimensions, no external model."""
import csv
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from test_agent import SETTINGS
import test_review_compression as compression_fixture
from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator, read_public_answer
from data_analysis_agent.agents.review_answer_facts import is_mixed_overview
from data_analysis_agent.agents.review_compression import CompactAnalysisContext, build_analysis_artifacts
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import ASPECTS, validate_opinions


QUESTION = '查看整体趋势、包装原文、六维度主要问题。'


class MixedAnswerTests(WorkbenchFixture, unittest.TestCase):
    result = compression_fixture.CompressionTests.result

    def scope(self, **changes):
        return {**IDENTITY, 'source': 'reviews_csv', 'dataset_version': 'reviews-csv',
            'product_ids': ['MIXED-TEST'], 'date_from': '2020-01-01', 'date_to': '2022-12-31',
            'baseline_from': '2019-12-01', 'baseline_to': '2019-12-31', **changes}

    def setUp(self):
        super().setUp()
        values = [('logistics', 'negative', '物流延迟'), ('packaging', 'negative', '包装破损'),
                  ('service', 'negative', '客服回复慢'), ('effectiveness', 'negative', '制冷不足'),
                  ('price', 'negative', '价格偏高'), ('quality', 'positive', '做工牢固')]
        rows = []
        for index in range(36):
            month = f'{2020 + index // 12}-{index % 12 + 1:02}'
            for offset in (0, 3):
                rows.append(dict(product_id='MIXED-TEST', product_name='组合分析合成商品',
                    user_id=f'{month}-{offset}', published_at=month + '-15',
                    text='，'.join(quote for _, _, quote in values[offset:offset + 3])))
        path = self.root / 'mixed-answer.csv'
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        import_amazon_jsonl(self.db, reviews_path=path, dataset_version='reviews-csv')
        for review in pending_reviews(self.db, **IDENTITY, source='reviews_csv', limit=1000):
            opinions = [opinion(aspect, sentiment, quote) for aspect, sentiment, quote in values if quote in review['text']]
            save_annotation(self.db, review, **IDENTITY, status='success',
                opinions=validate_opinions(json.dumps({'opinions': opinions}), review['text']))

    def generate(self, question=QUESTION, **scope):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        full, plan = self.result(question, **scope)
        original = deepcopy(full)
        artifacts = build_analysis_artifacts(full, plan)
        context = artifacts['compact_context']
        with patch('openai.OpenAI', side_effect=AssertionError('mixed facts need no model')) as provider:
            answer, diagnostics = NaturalLanguageAnswerGenerator().generate(question, context, settings=SETTINGS, validated=True)
        provider.assert_not_called()
        self.assertEqual(diagnostics['strategy'], 'template')
        self.assertEqual(diagnostics['attempts'], 0)
        self.assertEqual(full, original)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        return full, plan, artifacts, answer, diagnostics

    def test_all_three_requests_reach_body_with_six_dimensions_and_original_quotes(self):
        full, _, artifacts, answer, _ = self.generate()
        text = answer['summary'] + ''.join(h['text'] for h in answer['highlights'])
        ids = {key: value['kind'] for key, value in full['results'].items() if key != 'overview'}
        self.assertEqual(set(ids), {key for h in answer['highlights'] for key in h['source_result_ids']})
        self.assertIn('趋势：', text)
        self.assertIn('六维度主要问题', text)
        for label in ASPECTS.values():
            self.assertIn(label + '：', text)
        self.assertIn('商品质量：0 条', text)
        evidence = next(value for value in full['results'].values() if value['kind'] == 'evidence')
        for review in evidence['data']['items']:
            self.assertIn(f"评论 #{review['review_id']}", text)
            for quote in review['opinions']:
                self.assertIn(quote['quote'], text)
        issues = next(s for s in artifacts['compact_context']['statistics'] if s['analysis_type'] == 'issues')
        self.assertEqual({row['aspect'] for row in issues['aspects']}, set(ASPECTS))
        self.assertEqual(len(answer['evidence_refs']), 3)
        self.assertIn('不能相加', ''.join(answer['caveats']))

    def test_long_trend_keeps_full_snapshot_and_metrics_but_compacts_body(self):
        full, _, artifacts, answer, _ = self.generate()
        trend = next(h for h in answer['highlights'] if h['text'].startswith('趋势：'))
        self.assertLess(len(trend['text']), 550)
        self.assertNotIn('逐期数据（UTC）：', trend['text'])
        self.assertNotIn('2020-01：负面评论', trend['text'])
        self.assertIn('最近两个有标注的相邻时段', trend['text'])
        self.assertIn('2022-11', trend['text'])
        self.assertIn('2022-12', trend['text'])
        self.assertIn('完整逐期数据见统计详情', trend['text'])
        raw = next(value for value in full['results'].values() if value['kind'] == 'trend')
        self.assertEqual(len(raw['data']['buckets']), 36)
        metrics = next(row for row in answer['metrics'] if row['finding_ref'] == trend['finding_ref'])
        series = next(s for s in artifacts['compact_context']['statistics'] if s['analysis_type'] == 'trend')['trends'][0]
        self.assertEqual(len([v for v in metrics['values'] if 'bucket' in v]), len(series['points']))

    def test_reordering_subrequests_still_publishes_each_part(self):
        _, _, _, answer, _ = self.generate('六维统计、包装原文、整体趋势')
        text = ''.join(h['text'] for h in answer['highlights'])
        self.assertIn('趋势：', text)
        self.assertIn('包装相关原文', text)
        self.assertIn('六维度主要问题', text)

    def test_single_observed_month_does_not_invent_adjacent_change(self):
        _, _, _, answer, _ = self.generate(date_to='2020-01-31')
        trend = next(h['text'] for h in answer['highlights'] if h['text'].startswith('趋势：'))
        self.assertIn('2020-01', trend)
        self.assertNotIn('相邻时段', trend)
        self.assertIn('暂不能判断稳定趋势', trend)

    def test_empty_window_keeps_null_rates_and_no_quotes(self):
        _, _, _, answer, _ = self.generate(date_from='2019-11-01', date_to='2019-11-30',
                                          baseline_from='2019-10-01', baseline_to='2019-10-31')
        text = ''.join(h['text'] for h in answer['highlights'])
        self.assertIn('各时间桶均无成功标注', text)
        self.assertIn('没有匹配原文', text)
        self.assertNotIn('0.0%', text)
        self.assertEqual(answer['evidence_refs'], [])

    def test_empty_evidence_is_explicit_and_never_borrowed_from_another_dimension(self):
        _, _, _, answer, _ = self.generate('查看整体趋势、质量原文、六维度主要问题。')
        self.assertIn('当前筛选条件没有匹配原文', ''.join(h['text'] for h in answer['highlights']))
        self.assertEqual(answer['evidence_refs'], [])

    def test_historical_three_aspect_context_does_not_claim_full_six_aspect_answer(self):
        _, _, artifacts, _, _ = self.generate()
        compact = deepcopy(artifacts['compact_context'])
        for stat in compact['statistics']:
            if stat['analysis_type'] == 'issues':
                stat['aspects'] = stat['aspects'][:3]
        self.assertFalse(is_mixed_overview(CompactAnalysisContext.model_validate(compact)))

    def test_saved_answer_rebuild_preserves_each_component(self):
        full, plan, artifacts, answer, diagnostics = self.generate()
        saved = {**full, **artifacts, 'public_answer': answer, 'answer_generation': diagnostics,
                 'analysis_intent': plan.analysis_type, 'sentiment_intent': plan.sentiment}
        rebuilt = read_public_answer({'status': 'succeeded', 'parameters': self.scope(), 'result': saved})
        self.assertEqual(rebuilt, answer)

    def test_ordinary_ranking_still_uses_existing_top_three(self):
        full, plan = self.result('差评主要集中在哪些方面？')
        compact = build_analysis_artifacts(full, plan)['compact_context']
        self.assertEqual(len(compact['statistics'][0]['aspects']), 3)


if __name__ == '__main__':
    unittest.main()
