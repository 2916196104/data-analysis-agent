"""Synthetic boundary checks; durations never independently prove delivery delay."""
import unittest
from data_analysis_agent.agents.review_reported_facts import assess, context_ok, fact, quote_facts


def item(text, quote=None):
    quote = text if quote is None else quote
    start = text.index(quote)
    return {'key':'synthetic', 'review_id':1, 'review_text':text, 'quote':quote,
            'quote_start':start, 'quote_end':start + len(quote)}


class DurationSafetyTests(unittest.TestCase):
    def test_explicit_complaints_still_support(self):
        for text in ('物流太慢了', '商品到货时间太长了', '配送速度很慢',
                     '商品到货时间太长了，等了八天才到货', '等了很久，物流太慢了'):
            with self.subTest(text=text):
                row=item(text)
                self.assertIn('delivery_slow',quote_facts(text))
                self.assertTrue(context_ok(row,{'delivery_slow'}))
                self.assertEqual(assess('有没有配送慢的反馈？',[row])['permitted_keys'],['synthetic'])

    def test_duration_alone_never_supplies_fact(self):
        for text in ('等了0天才到货','等了1天才到货','等了2天才到货','等了八天才到货'):
            with self.subTest(text=text):
                self.assertIsNone(fact(text))
                self.assertNotIn('delivery_slow',quote_facts(text))
                result=assess('有没有配送慢的反馈？',[item(text)])
                self.assertEqual(result['permitted_keys'],[])
                self.assertEqual(result['candidate_judgments'][0]['support'],'unknown')

    def test_install_return_negation_and_opposites(self):
        for text in ('到货后等八天才安装','退货后等八天才到货','配送并不慢',
                     '送货很快但安装很慢','客服说物流慢，但买家明确否认'):
            with self.subTest(text=text):
                self.assertNotEqual(fact(text),'delivery_slow')
                self.assertEqual(assess('有没有配送慢的反馈？',[item(text)])['permitted_keys'],[])

    def test_neutral_duration_cannot_be_borrowed_from_other_quote(self):
        text='商品到货时间太长了，等了八天才到货'
        self.assertEqual(assess('有没有配送慢的反馈？',[item(text,'等了八天才到货')])['permitted_keys'],[])


if __name__ == '__main__': unittest.main()
