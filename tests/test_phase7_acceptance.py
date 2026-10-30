"""阶段 7 的固定语义预期；数据库与供应商评测另外保存逐题证据。"""
import unittest

from scripts.phase7_cases import cases
from scripts.evaluate_review_agent import SCOPE
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_prompts import detect_sentiment_intent
from scripts.phase7_evaluation import BoundedLiveTransport
from scripts.phase7_performance import distribution
import httpx
import json


class Phase7PlanTests(unittest.TestCase):
    def test_fixed_semantic_cases(self):
        rows = cases()
        self.assertEqual(len(rows), 43)
        self.assertEqual(len({row['id'] for row in rows}), 43)
        for row in rows:
            with self.subTest(case=row['id']):
                plan = build_query_plan(row['effective_question'], {**SCOPE, **row['scope']})
                self.assertEqual(bool(plan.unsupported_reason), row['status'] == 'unsupported')
                self.assertEqual(plan.sentiment, row['sentiment'])
                if row['status'] == 'ok':
                    for tool, parameters in row['expected']:
                        self.assertTrue(any(request['tool'] == tool and all(request['parameters'].get(k) == v
                            for k, v in parameters.items()) for request in plan.requirements), plan.to_dict())

    def test_negation_does_not_cross_clause_or_reversal(self):
        for text in ('不是问好评，我想知道差评', '不是问好评而是差评', '并非优点，只想知道缺点'):
            self.assertEqual(detect_sentiment_intent(text), 'negative')
        for text in ('不是问差评，我想知道好评', '不是问差评而是好评'):
            self.assertEqual(detect_sentiment_intent(text), 'positive')
        self.assertIsNone(detect_sentiment_intent('好评和差评都要'))

    def test_live_budget_rejects_requests_before_network(self):
        for ledger, body in (([{'input_bytes': 1}] * 36, {'max_tokens': 2048}),
                             ([], {'max_tokens': 2049}), ([], {'max_tokens': 0}),
                             ([{'input_bytes': 600000}], {'max_tokens': 2048})):
            with self.subTest(body=body, count=len(ledger)):
                transport = BoundedLiveTransport(ledger)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'budget exceeded'):
                        transport.handle_request(httpx.Request('POST', 'https://offline.invalid', content=json.dumps(body).encode()))
                finally:
                    transport.close()

    def test_percentiles_require_enough_samples(self):
        with self.assertRaises(AssertionError):
            distribution([1, 2, 3])
        self.assertEqual(distribution(list(range(1, 51)))['p95_ms'], 48)
