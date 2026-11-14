"""Synthetic P3-A grammar tests; no real opinions or human labels."""
import unittest

from data_analysis_agent.agents.review_retrieval import assess_retrieval, retrieval_request
from data_analysis_agent.agents.review_semantics import product_mentions, match_product_mentions, QuestionScopeError


def evidence(text, quote=None, rid=1, key='synthetic-1'):
    quote=quote or text.rstrip('。')
    start=text.index(quote)
    return dict(key=key,review_id=rid,quote=quote,review_text=text,
                quote_start=start,quote_end=start+len(quote))


class ReportedFactsTests(unittest.TestCase):
    question='有没有制冷不足的反馈？'

    def test_equivalent_and_directional_entailment_with_exact_bindings(self):
        for quote in ('制冷不佳','空调制冷不给力','我觉得制冷很差','根本降不下温','空调完全不制冷'):
            with self.subTest(quote=quote):
                row=evidence('外壳颜色很漂亮；'+quote+'；快递员送到了楼下。',quote)
                result=assess_retrieval(self.question,[row])
                self.assertEqual(result['status'],'sufficient')
                self.assertEqual(result['supported_target_fact_ids'],['cooling_poor'])
                binding=result['fact_bindings'][0]
                self.assertEqual(binding['fact_ids'],['cooling_poor'])
                self.assertEqual(row['review_text'][binding['quote_start']:binding['quote_end']],quote)
                self.assertEqual(binding['key'],row['key'])
        self.assertEqual(assess_retrieval('有没有不制冷的反馈？',[evidence('制冷很差。')])['permitted_keys'],[])

    def test_topic_similarity_never_proves_speed_sleep_cause_or_reliability(self):
        for question in ('有没有制冷不快的反馈？','有没有噪音导致失眠的反馈？',
                         '有没有空调使用十年仍然耐用的反馈？','有没有压缩机故障造成制冷不足的反馈？',
                         '有没有所有空调都制冷不足的反馈？','有没有制冷不足超过三成的证据？'):
            with self.subTest(question=question):
                result=assess_retrieval(question,[evidence('不制冷；噪音很大。')])
                self.assertEqual(result['permitted_keys'],[])
                self.assertFalse(result['general_entailment'])

    def test_negation_report_hypothesis_correction_and_conflict_veto(self):
        for text in ('并非制冷很差。','朋友说制冷很差。','如果制冷很差，就退货。',
                     '据说制冷很差。','制冷很差？','制冷很差；更正，是操作问题。',
                     '制冷很差；实际用下来制冷效果很好。','制冷很差；其实我写错了。',
                     '制冷很差才怪。','制冷很差；这是另一台机器。',
                     '制冷很差；制冷仅限特定模式。','制冷很差；不过还挺好的。',
                     '张先生表示，制冷很差。','假设，制冷很差。','制冷很差；我不这么认为。',
                     '制冷很差；冷暖效果：制冷效果很好。'):
            with self.subTest(text=text):
                result=assess_retrieval(self.question,[evidence(text,'制冷很差')])
                self.assertEqual(result['permitted_keys'],[])
                self.assertEqual(result['candidate_judgments'][0]['reason'],'context_unresolved')

    def test_unknown_sentences_and_partial_words_cannot_supply_support(self):
        for quote in ('气流微弱','凉意迟迟未至','制冷','不制冷是假话','我问他制冷很差吗'):
            with self.subTest(quote=quote):
                result=assess_retrieval(self.question,[evidence(quote+'。')])
                self.assertEqual(result['permitted_keys'],[])
        result=assess_retrieval(self.question,[evidence('制冷很差；外观不错。','外观不错')])
        self.assertEqual(result['permitted_keys'],[])

    def test_full_review_retraction_or_irony_vetoes_exact_quote(self):
        for ending in ('没有这回事。', '没这回事。', '此事并未发生。', '这是反讽。'):
            with self.subTest(ending=ending):
                result = assess_retrieval('有没有送货慢的反馈？',
                    [evidence('送货很慢。' + ending, '送货很慢')])
                self.assertEqual(result['status'], 'insufficient')
                self.assertEqual(result['permitted_keys'], [])
                self.assertEqual(result['candidate_judgments'][0]['reason'], 'context_unresolved')
        positive = assess_retrieval('有没有送货慢的反馈？',
            [evidence('送货很慢。外壳颜色很漂亮。', '送货很慢')])
        self.assertEqual(positive['status'], 'sufficient')

    def test_service_role_and_subtopic_not_interchangeable(self):
        for question,quote in [('有没有客服态度差的反馈？','安装师傅态度差'),
                               ('有没有制热效果差的反馈？','制冷很差'),
                               ('有没有制冷效果好的反馈？','制冷很差')]:
            self.assertEqual(assess_retrieval(question,[evidence(quote)])['permitted_keys'],[])

    def test_multifact_requires_same_review_and_quotes_for_both(self):
        question='有没有同一评论既说制冷好又抱怨噪音大的反馈？'
        text='制冷效果很好；噪声太大；外观挺喜欢。'
        a=evidence(text,'制冷效果很好');b=evidence(text,'噪声太大',key='synthetic-2')
        result=assess_retrieval(question,[b,a])
        self.assertEqual(result['permitted_keys'],['synthetic-2','synthetic-1'])
        self.assertEqual(result['relation'],dict(type='same_review',supported=True,review_ids=[1]))
        self.assertEqual(result['fact_bindings'][0]['fact_ids'],['noise_high'])
        self.assertEqual(assess_retrieval(question,[a])['permitted_keys'],[])  # outside quote is not support
        self.assertEqual(assess_retrieval(question,[a,{**b,'review_id':2}])['permitted_keys'],[])
        query=retrieval_request(question)
        self.assertIsNone(query.aspect);self.assertIsNone(query.sentiment)

    def test_unknown_relations_and_temporal_queries_remain_unknown(self):
        for question in ('有没有先制冷好后噪音大的反馈？','有没有制冷好但是噪音大的反馈？',
                         '有没有不是制冷快而是无法降温的反馈？'):
            result=assess_retrieval(question,[evidence('制冷好；噪音大；无法降温。')])
            self.assertEqual(result['reason'],'query_semantics_uncovered')
            self.assertEqual(result['permitted_keys'],[])

    def test_generic_properties_are_not_product_names(self):
        for question in ('有没有空调使用十年仍然耐用的反馈？','请问是否有空调用了多年仍不制冷的评论？',
                         '有无空调制热不佳的反馈？','是否存在空调寿命短的评论？',
                         '想了解有没有空调不耐用的反馈？','有没有不是空调制冷差而是操作错误的反馈？',
                         '  请问有没有空调使用多年不制冷的评论？'):
            self.assertEqual(product_mentions(question,[]),[],question)

    def test_known_complex_title_unknown_product_and_irrelevant_entity(self):
        products=[dict(product_id='P1',title='和风 KFR-35G/W（新风与净化）空调',brand='和风')]
        mentions=product_mentions('有没有和风 KFR-35G/W（新风与净化）空调制冷不佳的评论？',products)
        self.assertEqual(match_product_mentions(mentions,products),['P1'])
        for question in ('有没有不存在品牌空调制冷不佳的评论？','有没有99999999的评论？'):
            with self.assertRaises(QuestionScopeError):
                match_product_mentions(product_mentions(question,products),products)
        self.assertEqual(product_mentions('有没有周末在书房使用空调制冷不佳的反馈？',[]),[])


if __name__=='__main__':unittest.main()
