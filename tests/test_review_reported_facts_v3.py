"""Bounded synonym/relationship and clause-context regressions; synthetic evidence."""
import unittest

from test_review_reported_facts import evidence
from data_analysis_agent.agents import review_cooling_speed_evidence as cooling
from data_analysis_agent.agents.review_retrieval import assess_retrieval, retrieval_request


class ReportedFactsV3Tests(unittest.TestCase):
    def test_common_question_frames_keep_the_same_proposition(self):
        from data_analysis_agent.agents.review_retrieval import wants_semantic_search
        for query in ('有用户反映降温不给力吗？', '有没有买家说制冷不够给力的评价？',
                      '请帮我找出降温效果不好的评论。', '是否有制冷不足的反馈？'):
            with self.subTest(query=query):
                self.assertTrue(wants_semantic_search(query))
                self.assertEqual(assess_retrieval(query,[evidence('制冷不佳。')])['status'],'sufficient')

    def test_question_frames_do_not_swallow_unknown_product_references(self):
        from data_analysis_agent.agents.review_semantics import product_mentions, match_product_mentions, QuestionScopeError
        for q in ('有没有买家说不存在品牌空调制冷不足的评价？',
                  '有没有买家说商品123211092382制冷不足的评价？'):
            with self.subTest(q=q), self.assertRaises(QuestionScopeError):
                match_product_mentions(product_mentions(q,[]),[])

    def test_unrelated_contrast_and_report_do_not_retract_cooling(self):
        for text in ('制冷不足。但是包装很结实。', '制冷不足，但包装完好。',
                     '制冷不足但是包装完好。', '制冷不足。客服说包装完好。',
                     '客服说包装完好。制冷不足。', '制冷不足。听说包装完好。',
                     '制冷不足。包装破损；包装完好。', '制冷不足；并非包装破损。',
                     '制冷不足，客服说包装完好。'):
            with self.subTest(text=text):
                result = assess_retrieval('有没有制冷不足的反馈？', [evidence(text, '制冷不足')])
                self.assertEqual(result['status'], 'sufficient')
                self.assertEqual(result['supported_target_fact_ids'], ['cooling_poor'])

    def test_reporting_frames_corrections_and_ambiguous_pronouns_still_veto(self):
        for text in ('客服说制冷不足。', '客服说，制冷不足。', '听说制冷不足。',
                     '如果制冷不足就退货。', '制冷不足。这话是假的。',
                     '制冷不足。但是不是真的。', '制冷不足。不过还挺好的。',
                     '制冷不足。包装上说，前一句不属实。', '制冷不足。后来制冷很好。',
                     '制冷不足。包装完好。其实写错了。', '别人说包装完好和制冷不足。',
                     '客服说包装完好，制冷不足。', '听说包装完好但制冷不足。',
                     '据说包装完好；制冷不足。', '后来包装破了，制冷不足。'):
            with self.subTest(text=text):
                self.assertEqual(assess_retrieval('有没有制冷不足的反馈？',
                    [evidence(text, '制冷不足')])['permitted_keys'], [])

    def test_synonyms_keep_effect_speed_and_failure_separate(self):
        cases = [('降温不给力', '制冷不够给力', 'cooling_poor'),
                 ('制冷效果不理想', '降温效果不好', 'cooling_poor'),
                 ('完全不降温', '根本降不下温', 'cooling_failure'),
                 ('降温速度慢', '制冷速度很慢', 'cooling_slow'),
                 ('降温效果好', '制冷效果不错', 'cooling_good'),
                 ('配送迟缓', '送货速度太慢', 'delivery_slow'),
                 ('送货速度快', '快递很快', 'delivery_fast'),
                 ('运行声音很大', '噪音有点大', 'noise_high'),
                 ('制热不给力', '升温效果不好', 'heating_poor')]
        for query, quote, fid in cases:
            with self.subTest(query=query):
                result = assess_retrieval(f'有没有{query}的反馈？', [evidence(quote + '。')])
                self.assertEqual(result['status'], 'sufficient')
                self.assertEqual(result['supported_target_fact_ids'], [fid])
        for query, quote in [('降温速度慢', '制冷不够给力'), ('完全不降温', '制冷不足'),
                             ('制冷不足', '制热不给力'), ('制冷不足', '降温速度慢')]:
            with self.subTest(query=query, quote=quote):
                self.assertEqual(assess_retrieval(f'有没有{query}的反馈？',
                    [evidence(quote + '。')])['permitted_keys'], [])

    def test_synonyms_do_not_remove_negation_or_report_scope(self):
        for text in ('并非降温不给力。', '朋友说降温不给力。', '降温不给力？',
                     '如果降温不给力就退货。', '降温不给力；制冷很好。'):
            with self.subTest(text=text):
                self.assertEqual(assess_retrieval('有没有制冷不够给力的反馈？',
                    [evidence(text, '降温不给力')])['permitted_keys'], [])
        self.assertEqual(assess_retrieval('有没有运行声音很大的反馈？',
            [evidence('邻居施工，声音很大。','声音很大')])['permitted_keys'], [])

    def test_slow_cooling_permission_uses_explicit_speed_with_exact_quote_binding(self):
        q = '有买家反映降温速度较慢吗？'
        for text, quote in (
            ('外观不错，制冷效果也挺慢。', '制冷效果也挺慢'),
            ('降温太慢了，开了20分钟都还没凉快。', '降温太慢了，开了20分钟都还没凉快'),
            ('冷暖效果：制冷效果很差也很慢。', '制冷效果很差也很慢'),
            ('我家这台空调制冷速度有点慢。', '我家这台空调制冷速度有点慢'),
        ):
            with self.subTest(text=text):
                row = evidence(text, quote)
                result = assess_retrieval(q, [row], policy=cooling.POLICY)
                self.assertEqual(result['permitted_keys'], [row['key']])
                binding, = result['fact_bindings']
                self.assertEqual(binding['fact_ids'], [cooling.SLOW])
                self.assertEqual(text[binding['quote_start']:binding['quote_end']], quote)
                self.assertEqual(assess_retrieval(q, [row])['permitted_keys'], [])

    def test_slow_cooling_permission_keeps_context_vetoes_and_speed_specificity(self):
        q = '有没有制冷速度不快的反馈？'
        for text, quote in (
            ('空调完全不制冷。', '空调完全不制冷'),
            ('冷气不足。', '冷气不足'),
            ('开了20分钟都还没凉快。', '开了20分钟都还没凉快'),
            ('制冷太慢了，开了20分钟都还没凉快。', '开了20分钟都还没凉快'),
            ('制冷效果很差，开了20分钟都还没凉快。', '制冷效果很差，开了20分钟都还没凉快'),
            ('制冷太慢了。根本不制冷。', '制冷太慢了'),
            ('制冷太慢了，制冷很快。', '制冷太慢了'),
            ('制冷太慢了？', '制冷太慢了'),
            ('并非制冷太慢了。', '制冷太慢了'),
            ('售后称，制冷太慢了。', '制冷太慢了'),
            ('还没亲自用过。制冷太慢了。', '制冷太慢了'),
            ('只在节能模式下，制冷太慢了。', '制冷太慢了'),
            ('买了三台空调，制冷太慢了。', '制冷太慢了'),
            ('旁边那台空调，制冷太慢了。', '制冷太慢了'),
            ('如果制冷太慢了，就退货。', '制冷太慢了'),
            ('制冷太慢了，其实说错了。', '制冷太慢了'),
            ('刚开机的前几秒制冷太慢了。', '制冷太慢了'),
        ):
            with self.subTest(text=text, quote=quote):
                result = assess_retrieval(q, [evidence(text, quote)], policy=cooling.POLICY)
                self.assertEqual(result['permitted_keys'], [])

    def test_cooling_v2_keeps_the_reported_experience_bound_to_its_quote(self):
        q = '有没有制冷很慢的反馈？'
        for quote, extra in (
            ('降温慢得要命', '，外观不错。'),
            ('空调制冷慢效果一般', '。安装服务不是很满意。'),
            ('制冷慢，而且没有节能模式', '。客服回复敷衍。'),
            ('开着空调制冷，室温下降速度比较慢', '。'),
            ('温度降得太慢了，调到18度', '，一个小时都降不下来。'),
            ('制冷效果一般。制冷30分钟，房间仍然没有变凉', '。也不知道别人买的空调是否也是这个效果。'),
        ):
            with self.subTest(quote=quote):
                row = evidence(quote+extra, quote)
                result = assess_retrieval(q, [row], policy=cooling.POLICY)
                self.assertEqual(result['permitted_keys'], [row['key']])
                self.assertEqual(result['fact_bindings'][0]['fact_ids'], [cooling.SLOW])
                self.assertFalse(assess_retrieval(q,[row],policy=cooling.PREVIOUS_POLICY)['permitted_keys'])

    def test_temperature_measurement_can_accompany_but_not_create_a_speed_judgment(self):
        q = '有没有制冷慢的反馈？'
        measurement = '开18度2个小时室内温度才降到26度'
        text = '冷暖效果：制冷特别慢，' + measurement + '，卧室只有十几平。'
        row = evidence(text, '制冷特别慢，' + measurement)
        for policy in (cooling.PREVIOUS_POLICY, cooling.POLICY):
            self.assertEqual(assess_retrieval(q,[row],policy=policy)['permitted_keys'], [row['key']])
        self.assertFalse(cooling.assess(q, [evidence(text, measurement)])['permitted_keys'])

    def test_cooling_v2_rejects_ambiguous_cross_clause_and_unrelated_reports(self):
        for text, quote in (
            ('不是制冷慢得离谱。', '制冷慢得离谱'),
            ('如果制冷慢效果一般就退货。', '制冷慢效果一般'),
            ('朋友说，制冷20分钟，还没有凉下来。', '制冷20分钟，还没有凉下来'),
            ('制冷20分钟。还没有凉下来。', '制冷20分钟。还没有凉下来'),
            ('制冷20分钟，还没有凉下来。', '还没有凉下来'),
            ('制热20分钟，还没有凉下来。', '制热20分钟，还没有凉下来'),
            ('制冷20分钟，还没有凉下来。这很正常。', '制冷20分钟，还没有凉下来'),
            ('制冷20分钟，还没有凉下来。根本不制冷。', '制冷20分钟，还没有凉下来'),
            ('制冷20分钟，还没有凉下来。制冷很快。', '制冷20分钟，还没有凉下来'),
            ('在说室外温度。温度降得太慢了，开16度。', '温度降得太慢了，开16度'),
            ('开窗自然降温。室温降得很慢。', '室温降得很慢'),
            ('只有出风口凉。开了一个小时。', '开了一个小时'),
            ('邻居家的空调，制冷慢效果一般。', '制冷慢效果一般'),
            ('只在特殊模式，制冷慢得要命。', '制冷慢得要命'),
            ('制冷慢，而且这话不属实。', '制冷慢'),
            ('也不知道别人买的空调是不是这个效果。制冷慢效果一般。', '制冷慢效果一般'),
        ):
            with self.subTest(text=text):
                row = evidence(text,quote)
                self.assertFalse(cooling.assess('有没有制冷慢的反馈？',[row])['permitted_keys'])

    def test_timed_progress_binds_two_ordered_observations_from_the_same_buyer(self):
        q = '有没有制冷慢的反馈？'
        quote = '空调开18度，运行了30分钟，室内还没有凉下来，三小时后降温效果不明显'
        self.assertTrue(cooling.assess(q, [evidence(quote)])['permitted_keys'])
        for text, span in (
            ('朋友说，' + quote, quote),
            ('如果' + quote + '就退货。', quote),
            (quote + '，我说的是另一台空调。', quote),
            (quote + '。后来换了空调。', quote),
            (quote.replace('三小时', '20分钟'), quote.replace('三小时', '20分钟')),
            (quote.replace('30分钟', '大半个小时').replace('三小时', '45分钟'),
             quote.replace('30分钟', '大半个小时').replace('三小时', '45分钟')),
            (quote, '三小时后降温效果不明显'),
        ):
            with self.subTest(text=text, span=span):
                self.assertFalse(cooling.assess(q,[evidence(text,span)])['permitted_keys'])

    def test_slow_cooling_in_same_review_still_requires_both_facts(self):
        q = '有没有同一评论既说制冷慢又抱怨噪音大的反馈？'
        a = evidence('制冷效果有点慢。噪音很大。', '制冷效果有点慢')
        b = evidence(a['review_text'], '噪音很大', key='synthetic-2')
        self.assertEqual(cooling.assess(q, [a, b])['permitted_keys'], [a['key'], b['key']])
        self.assertEqual(cooling.assess(q, [a, {**b, 'review_id': 2}])['permitted_keys'], [])

    def test_any_of_accepts_one_alternative_without_claiming_the_other(self):
        q = '有没有制冷好或者物流很快的反馈？'
        result = assess_retrieval(q, [evidence('物流很快。')])
        self.assertEqual(result['status'], 'sufficient')
        self.assertEqual(result['operator'], 'any_of')
        self.assertEqual(result['supported_target_fact_ids'], ['delivery_fast'])
        self.assertEqual(result['missing_required_fact_ids'], [])
        self.assertIsNone(result['relation'])
        for q in ('有没有制冷差或制冷慢的反馈？', q):
            query = retrieval_request(q)
            self.assertIsNone(query.aspect)
            self.assertIsNone(query.sentiment)

    def test_and_and_contrast_require_both_facts_in_one_review(self):
        text = '制冷很好，但是噪音很大。'
        a = evidence(text, '制冷很好')
        b = evidence(text, '噪音很大', key='synthetic-2')
        for q in ('有没有同一条评论同时提到制冷好和噪音大的反馈？',
                  '有没有制冷好但噪音大的反馈？', '有没有制冷好并且噪音大的反馈？',
                  '有没有同一评论既说制冷好又抱怨噪音大的反馈？'):
            with self.subTest(q=q):
                result = assess_retrieval(q, [a, b])
                self.assertEqual(result['status'], 'sufficient')
                self.assertEqual(result['operator'], 'all_of')
                self.assertEqual(result['relation']['review_ids'], [1])
                self.assertEqual(assess_retrieval(q, [a])['permitted_keys'], [])
                self.assertEqual(assess_retrieval(q, [a, {**b, 'review_id': 2}])['permitted_keys'], [])

    def test_nested_temporal_and_causal_queries_remain_unlicensed(self):
        for body in ('制冷好或物流快并且噪音大', '先制冷好后噪音大',
                     '不是制冷慢而是不制冷', '压缩机故障导致制冷不足'):
            with self.subTest(body=body):
                result = assess_retrieval(f'有没有{body}的反馈？', [evidence('制冷好；物流很快；噪音大。')])
                self.assertEqual(result['permitted_keys'], [])


if __name__ == '__main__':
    unittest.main()
