"""Bounded synthetic delivery facts: no retrieval, production validation, or agent."""
import unittest

from data_analysis_agent.agents.review_reported_facts import assess, context_ok, fact, quote_facts, target


def row(text, quote=None, key='synthetic', review_id=1):
    quote = text if quote is None else quote
    start = text.index(quote)
    return {'key': key, 'review_id': review_id, 'review_text': text, 'quote': quote,
            'quote_start': start, 'quote_end': start + len(quote)}


class DeliveryFactTests(unittest.TestCase):
    question = '有没有配送慢的反馈？'

    def test_positive_complete_delivery_clauses(self):
        for text in ('送货很慢', '物流太慢了', '商品到货时间太长了',
                     '配送速度太慢', '快递到货很慢'):
            with self.subTest(text=text):
                self.assertEqual(fact(text), 'delivery_slow')
                result = assess(self.question, [row(text)])
                self.assertEqual(result['candidate_judgments'][0]['support'], 'direct_report')
                self.assertEqual(result['permitted_keys'], ['synthetic'])

    def test_two_delivery_clauses_in_one_opinion(self):
        text = '商品到货时间太长了，等了八天才到货'
        self.assertIn('delivery_slow', quote_facts(text))
        self.assertEqual(assess(self.question, [row(text)])['permitted_keys'], ['synthetic'])

    def test_wait_must_explicitly_refer_to_delivery(self):
        # P2-C3: a duration alone is not a slow-delivery complaint, even with 才.
        for text in ('到货后安装慢', '安装师傅来得慢', '等了八天才安装', '等待很久', '等了八天', '等了八天才到货'):
            with self.subTest(text=text):
                self.assertNotEqual(fact(text), 'delivery_slow')
                self.assertEqual(assess(self.question, [row(text)])['permitted_keys'], [])
        text = '物流太慢了，等了八天才安装'
        self.assertEqual(assess(self.question, [row(text, '等了八天才安装')])['permitted_keys'], [])

    def test_negation_correction_and_positive_delivery_do_not_license(self):
        for text in ('物流不慢', '配送并不慢', '送货很快，但是安装很慢',
                     '客服说物流慢，但买家明确纠正物流其实很快',
                     '物流太慢了，但其实物流很快', '配送慢才怪', '别人说配送慢'):
            with self.subTest(text=text):
                if text in ('物流不慢', '配送并不慢'):
                    self.assertNotIn('delivery_slow', quote_facts(text))
                self.assertEqual(assess(self.question, [row(text)])['permitted_keys'], [])
        for text, quote in (('不是物流慢', '物流慢'),
                            ('客服说物流慢，但买家明确纠正物流其实很快', '物流慢'),
                            ('物流太慢了，但其实物流很快', '物流太慢了')):
            with self.subTest(text=text, quote=quote):
                self.assertEqual(fact(quote), 'delivery_slow')
                self.assertEqual(assess(self.question, [row(text, quote)])['permitted_keys'], [])

    def test_other_delivery_clauses_must_be_understood(self):
        text = '商品到货时间太长了，等了八天才到货，而且安装态度不好，物流太慢了'
        for quote in ('商品到货时间太长了，等了八天才到货', '物流太慢了'):
            with self.subTest(quote=quote):
                evidence = row(text, quote)
                self.assertTrue(context_ok(evidence, {'delivery_slow'}))
                self.assertEqual(assess(self.question, [evidence])['permitted_keys'], ['synthetic'])

    def test_unknown_delivery_context_remains_fail_closed(self):
        for text in ('物流太慢了，但是后来很快就到了', '物流太慢了，其他物流情况未说明',
                     '配送速度太慢，如果不是地址弄错了'):
            with self.subTest(text=text):
                quote = text.split('，')[0]
                self.assertEqual(assess(self.question, [row(text, quote)])['permitted_keys'], [])

    def test_return_or_replacement_cannot_be_assumed_to_be_initial_delivery(self):
        for text in ('退货后，等了八天才到货', '换货后，等了八天才到货'):
            with self.subTest(text=text):
                self.assertEqual(assess(self.question, [row(text, '等了八天才到货')])['permitted_keys'], [])

    def test_unrelated_review_cannot_supply_a_missing_fact(self):
        query = '有没有同一评论既说配送慢又抱怨客服态度差的反馈？'
        a = row('配送慢', key='delivery', review_id=1)
        b = row('客服态度差', key='service', review_id=2)
        self.assertIsNotNone(target(query))
        self.assertEqual(assess(query, [a, b])['permitted_keys'], [])


if __name__ == '__main__':
    unittest.main()
