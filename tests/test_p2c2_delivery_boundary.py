"""Independent post-fix safety check: duration alone must substantiate delay."""
import unittest

from data_analysis_agent.agents.review_reported_facts import assess


class DeliveryDurationBoundaryTests(unittest.TestCase):
    def test_zero_day_wait_is_not_proof_of_slow_delivery(self):
        text = '等了0天才到货'
        row = {'key': 'synthetic', 'review_id': 1, 'review_text': text, 'quote': text,
               'quote_start': 0, 'quote_end': len(text)}
        result = assess('有没有配送慢的反馈？', [row])
        self.assertEqual(result['permitted_keys'], [])


if __name__ == '__main__':
    unittest.main()
