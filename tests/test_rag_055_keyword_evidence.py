"""Synthetic controls for the offline cooling-duration and fallback experiment."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from evaluate_rag_055_keyword_evidence import context_ok, duration_fact, eligible
from test_review_reported_facts import evidence


class CoolingDurationTests(unittest.TestCase):
    question = '有没有制冷比较慢的反馈？'
    quote = '这台空调开了两个小时才变凉'

    def test_self_report_and_bounded_prior_comparison(self):
        for clause in (self.quote, '我家这个空调开了四十分钟才感觉到凉意',
                       '我买的这台空调用了三小时才凉快'):
            self.assertTrue(duration_fact(clause))
            self.assertTrue(context_ok(evidence(clause + '。', clause), {'cooling_slow'}))
        text = '制冷很差，售后人员说是房间太大所以制冷慢，隔壁房间其他空调很凉快了，' + self.quote
        self.assertTrue(context_ok(evidence(text, self.quote), {'cooling_slow'}))

    def test_attribution_other_product_scope_and_retraction(self):
        texts = (
            f'朋友说，{self.quote}',
            f'客服说，其他空调很凉快了，{self.quote}',
            f'售后人员说是房间太大所以制冷慢，其他空调很凉快了，{self.quote}',
            f'制冷很差，售后人员说是房间太大所以制冷慢，{self.quote}',
            f'制冷很差，售后人员说是房间太大所以制冷慢，其他空调很凉快了，{self.quote}是隔壁师傅说的',
            f'如果{self.quote}，就申请退货。',
            f'并不是{self.quote}。',
            f'{self.quote}，更正：这是另一台空调。',
            f'{self.quote}。后来制冷很快。',
            f'{self.quote}，只在节能模式才这样。',
            f'{self.quote}，其实我记错了。',
            f'{self.quote}？',
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertFalse(context_ok(evidence(text,self.quote), {'cooling_slow'}))
        for clause in ('其他空调开了两小时才变凉', '隔壁这台空调开了两个小时才变凉',
                       '这台空调开了两小时还是完全不制冷', '这台空调开了两分钟就变凉'):
            self.assertFalse(duration_fact(clause))

    def test_room_temperature_drop_enters_the_keyword_pool_without_licensing_a_claim(self):
        from types import SimpleNamespace
        from evaluate_rag_055_hybrid import keyword_candidates
        from data_analysis_agent.agents import review_cooling_speed_evidence as cooling
        from data_analysis_agent.agents.review_retrieval import retrieval_request
        quotes = ('温度降的太慢了，开16度', '温度升得太慢了', '物流速度太慢')
        docs = [SimpleNamespace(stable_versioned_opinion_key=str(i), quote=quote, aspect='effectiveness',
                                sentiment='negative') for i,quote in enumerate(quotes)]
        req = retrieval_request(self.question)
        keys = {d.stable_versioned_opinion_key for d in docs}
        self.assertEqual([c.key for c in keyword_candidates(docs, req, keys, set())], ['0'])
        self.assertEqual(keyword_candidates(docs, req, keys, {'0'}), [])
        row = evidence('室外温度降的太慢了，开16度。', quotes[0])
        self.assertFalse(cooling.assess(self.question, [row])['permitted_keys'])

    def test_only_single_cooling_speed_intent_is_eligible(self):
        self.assertTrue(eligible(self.question))
        self.assertTrue(eligible('有买家反映制冷速度不快吗？'))
        for q in ('有没有完全不制冷的反馈？', '有没有送货慢的反馈？',
                  '有买家反映退货寄回很慢吗？', '有没有同一条评论说制冷慢且噪音大的反馈？',
                  '制冷慢的反馈有多少条？'):
            with self.subTest(q=q):
                self.assertFalse(eligible(q))
