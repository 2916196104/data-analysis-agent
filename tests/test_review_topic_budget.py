"""Ordinary negative-topic budgets: fixed responses and fake time, no network or DB."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import httpx
from openai import OpenAI

from test_agent import SETTINGS, completion
from data_analysis_agent.agents import review_topics as topics
from data_analysis_agent.agents.review_budget import P6SharedBudget, P6CostAuthorization, P6OfflineTransport
from data_analysis_agent.core.serialization import content_sha256


def extracted(count=60, *, sentiment='negative', duplicate=False):
    quotes = [f'物流配送迟延第{index}次' for index in range(count)]
    if duplicate:
        # 61 opinions, 60 distinct expressions, 53 reviews, as in the B4 workload.
        grouped = [[quote] for quote in quotes[:52]] + [quotes[52:] + [quotes[0]]]
    else:
        grouped = [[quote] for quote in quotes]
    items = []
    for index, group in enumerate(grouped):
        text, opinions, start = '，'.join(group), [], 0
        for quote in group:
            opinions.append(dict(aspect='logistics', sentiment=sentiment, quote=quote,
                                 quote_start=start, quote_end=start + len(quote)))
            start += len(quote) + 1
        items.append(dict(review_id=1000 + index, product_id='BUDGET-TEST', text=text,
                          annotation_id=2000 + index, revision_id=None, opinions=opinions))
    return dict(scope={'product_ids': ['BUDGET-TEST']}, aspect='logistics', sentiment=sentiment,
                summary={'successful_reviews': len(items), 'aspects': {'logistics': {}}},
                total=len(items), total_opinions=sum(map(len, grouped)), items=items)


class TopicBudgetTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('offline only')))

    def analyze(self, source=None, delays=(), *, settings=SETTINGS, late=(), unclassified=False, progress=None):
        source = extracted(duplicate=True) if source is None else source
        clock, calls = [0.0], []
        def respond(configuration, prompt, payload, schema, timeout):
            index = len(calls)
            calls.append(dict(schema=schema, payload=deepcopy(payload), timeout=timeout))
            delay = delays[index] if index < len(delays) else 0
            if delay > timeout and index not in late:
                clock[0] += timeout
                raise TimeoutError('fixed provider timeout')
            clock[0] += delay
            if schema is topics.TopicCatalog:
                return topics.TopicCatalog(themes=[dict(key='t1', name='配送迟延',
                    definition='买家明确抱怨物流配送迟延', support_ids=[payload['opinions'][0]['opinion_id']])])
            return topics.TopicBatch(assignments=[dict(opinion_id=row['opinion_id'],
                state='unclassified' if unclassified else 'assigned',
                matches=[] if unclassified else [dict(key='t1', start=0, end=len(row['quote']))])
                for row in payload['opinions']])
        def report(value):
            if progress:
                progress(value, clock)
        with patch.object(topics, 'perf_counter', side_effect=lambda: clock[0]), \
                patch.object(topics, '_call', side_effect=respond):
            data = topics.analyze_aspect_topics(source, settings=settings, progress=report)
        return data, calls

    def test_b4_scale_and_observed_latency_profile_complete_without_smaller_batches(self):
        data, calls = self.analyze(delays=[8.4, 25, 20.03, 25])
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual([len(c['payload']['opinions']) for c in calls[1:]], [20, 20, 20])
        self.assertEqual([c['timeout'] for c in calls], [30, 30, 30, 30])
        self.assertEqual(data['model_calls'], 4)
        self.assertEqual(data['coverage']['evaluated_opinions'], 61)
        self.assertEqual(data['topics'][0]['review_count'], 53)
        self.assertEqual(data['topics'][0]['opinion_count'], 61)
        self.assertEqual(data['population_mode'], 'full')
        self.assertTrue(topics.validate_topic_result(data))

    def test_one_timeout_is_retained_without_retry_or_false_complete(self):
        data, calls = self.analyze(delays=[8.4, 35, 2, 2])
        self.assertEqual(data['analysis_status'], 'partial')
        self.assertEqual([(f['batch'], f['code']) for f in data['failures']], [(1, 'timeout')])
        self.assertEqual(len(calls), 4)
        self.assertEqual(data['coverage']['not_evaluated_opinions'], 21)
        self.assertEqual(data['topics'][0]['opinion_count'], 40)
        self.assertTrue(topics.validate_topic_result(data))
        corrupt = deepcopy(data)
        corrupt['analysis_status'] = 'complete'
        self.assertFalse(topics.validate_topic_result(corrupt))

    def test_insufficient_remaining_time_does_not_start_a_short_request(self):
        def progress(value, clock):
            if value['completed'] == 40:
                clock[0] += 35
        data, calls = self.analyze(delays=[8.4, 25, 25], progress=progress)
        self.assertEqual(len(calls), 3)
        self.assertEqual(data['model_calls'], 3)
        self.assertEqual(data['failures'][0]['code'], 'budget_exhausted')
        self.assertEqual(data['failures'][0]['timeout_seconds'], 0)
        self.assertEqual(data['coverage']['not_evaluated_opinions'], 20)
        self.assertEqual(data['analysis_status'], 'partial')

    def test_response_past_request_deadline_is_not_accepted(self):
        data, calls = self.analyze(delays=[8.4, 31, 1, 1], late={1})
        self.assertEqual(data['analysis_status'], 'partial')
        self.assertEqual(data['failures'][0]['code'], 'timeout')
        self.assertEqual(data['coverage']['not_evaluated_opinions'], 21)
        self.assertEqual(len(calls), 4)

    def test_exhausted_total_budget_stops_even_if_provider_returns_valid_json(self):
        data, calls = self.analyze(delays=[121], late={0})
        self.assertEqual(len(calls), 1)
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertFalse(data['assignments'])
        self.assertEqual(data['failures'][0]['code'], 'timeout')

    def test_request_cap_stops_without_sending_or_repeating_another_batch(self):
        with patch.object(topics, 'MAX_MODEL_CALLS', 2):
            data, calls = self.analyze()
        self.assertEqual(len(calls), 2)
        self.assertEqual(data['model_calls'], 2)
        self.assertEqual(data['failures'][0]['code'], 'request_budget_exceeded')
        self.assertEqual(data['analysis_status'], 'partial')

    def test_eighty_expressions_remain_bounded_to_five_requests(self):
        data, calls = self.analyze(extracted(80))
        self.assertEqual(len(calls), topics.MAX_MODEL_CALLS)
        self.assertEqual(data['coverage']['evaluated_opinions'], 80)
        self.assertEqual(data['population_mode'], 'full')
        self.assertTrue(topics.validate_topic_result(data))

    def test_lower_configured_timeout_is_respected(self):
        data, calls = self.analyze(delays=[2, 3, 3, 3], settings=replace(SETTINGS, timeout_seconds=10))
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual([c['timeout'] for c in calls], [10] * 4)

    def test_positive_budget_and_payload_are_unchanged(self):
        data, calls = self.analyze(extracted(20, sentiment='positive'), delays=[8.4, 25, 25])
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertAlmostEqual(calls[-1]['timeout'], 26.6)
        self.assertIn('name', calls[-1]['payload']['themes'][0])

    def test_theme_meaning_and_all_original_evidence_remain_intact(self):
        source = extracted(duplicate=True)
        data, calls = self.analyze(source)
        self.assertTrue(all(set(theme) == {'key', 'name', 'definition'}
            for call in calls[1:] for theme in call['payload']['themes']))
        self.assertEqual(data['catalog'][0]['name'], '配送迟延')
        self.assertEqual(data['catalog'][0]['support_ids'], ['u1'])
        self.assertEqual({row['quote'] for call in calls[1:] for row in call['payload']['opinions']},
                         {op['quote'] for item in source['items'] for op in item['opinions']})
        for row in data['opinions']:
            item = next(item for item in data['items'] if item['review_id'] == row['review_id'])
            self.assertEqual(item['text'][row['quote_start']:row['quote_end']], row['quote'])

    def test_no_data_and_reviewed_but_unsupported_evidence_are_distinct(self):
        empty, empty_calls = self.analyze(extracted(0))
        self.assertEqual(empty['analysis_status'], 'insufficient_data')
        self.assertEqual(empty_calls, [])
        reviewed, calls = self.analyze(unclassified=True)
        self.assertEqual(reviewed['analysis_status'], 'complete')
        self.assertFalse(reviewed['failures'])
        self.assertFalse(reviewed['topics'])
        self.assertEqual(reviewed['coverage']['not_evaluated_opinions'], 0)
        self.assertTrue(topics.validate_topic_result(reviewed))

    def test_wire_output_caps_and_sdk_retries_stay_bounded(self):
        requests, options = [], []
        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            content = {'themes': []} if len(requests) == 1 else {'rows': [{'i': 'u1', 's': 'n', 'm': []}]}
            return completion({'role': 'assistant', 'content': json.dumps(content)})
        def client(**kwargs):
            options.append(kwargs)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with patch('openai.OpenAI', side_effect=client):
            topics._call(SETTINGS, topics.DISCOVER_PROMPT, {'opinions': []}, topics.TopicCatalog, 30)
            topics._call(SETTINGS, topics.CLASSIFY_PROMPT,
                         {'opinions': [{'opinion_id': 'u1', 'quote': '模拟观点'}]}, topics.TopicBatch, 30)
        self.assertEqual([r['max_tokens'] for r in requests], [1200, 3000])
        self.assertTrue(all(o['max_retries'] == 0 and o['timeout'] == 30 for o in options))
        self.assertTrue(all(len(r['messages'][1]['content'].encode()) <= topics.MAX_INPUT_BYTES for r in requests))
        self.assertEqual(1200 + (topics.MAX_MODEL_CALLS - 1) * 3000, 13200)

    def test_oversized_input_is_rejected_before_any_paid_request(self):
        with patch('openai.OpenAI') as provider:
            with self.assertRaisesRegex(topics.TopicResponseError, '字节预算'):
                topics._call(SETTINGS, topics.CLASSIFY_PROMPT,
                    {'opinions': [{'opinion_id': 'u1', 'quote': '过长' * 16000}]}, topics.TopicBatch, 30)
        provider.assert_not_called()

    def p6(self, *, classification_delay=3):
        source = extracted(3)
        rows, units, items = topics._units(source)
        frozen = {key: deepcopy(source[key]) for key in ('summary', 'aspect', 'sentiment', 'total', 'total_opinions')}
        frozen.update(query_scope=source['scope'], opinions=rows, units=units, items=items)
        settings = replace(SETTINGS, p6_execution_enabled=True)
        clock, requests = [0.0], []
        def respond(request):
            requests.append(request)
            payload = json.loads(request['messages'][1]['content'])
            if request['stage'] == 'topic_discovery':
                clock[0] += 2
                content = {'themes': [{'n': '配送迟延', 'd': '买家抱怨物流配送迟延', 'q': ['u1']}]}
            else:
                clock[0] += classification_delay
                content = {'rows': [{'i': r['opinion_id'], 's': 'a',
                    'm': [['t1', {'quote_id': r['opinion_id']}]]} for r in payload['opinions']]}
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(tool_calls=None, content=json.dumps(content)))],
                usage={'prompt_tokens': 50, 'completion_tokens': 20})
        policy = P6CostAuthorization(authorized=True, model=settings.model, base_url=settings.base_url,
            currency='TEST', amount_limit=Decimal('100'), input_price=Decimal('.0001'),
            output_price=Decimal('.0001'), billing_categories=('input', 'output'),
            token_upper_bound_id='offline-only-bound', token_upper_bound=lambda p, b: 1000)
        budget = P6SharedBudget(policy, offline_transport=P6OfflineTransport(respond), clock=lambda: clock[0])
        budget.start(('logistics',), lambda: None, input_hashes={'logistics': content_sha256(frozen)})
        budget.begin_aspect('logistics')
        with patch.object(topics, 'perf_counter', side_effect=lambda: clock[0]), \
                patch.object(topics, 'NEGATIVE_MAX_SECONDS', 0), \
                patch.object(topics, 'REQUEST_MAX_SECONDS', 0), patch.object(topics, 'MAX_MODEL_CALLS', 0):
            data = topics.analyze_aspect_topics(settings=settings, frozen_input=frozen, shared_budget=budget)
        return data, requests, budget.snapshot()

    def test_p6_shared_budget_ignores_ordinary_limits_and_preserves_wire_contract(self):
        data, requests, budget = self.p6()
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(budget['attempts'], 2)
        self.assertEqual([r['timeout'] for r in requests], [20, 20])
        self.assertEqual([r['max_tokens'] for r in requests], [1200, 3000])
        self.assertTrue(all(r['max_retries'] == 0 for r in requests))
        self.assertIn('name', json.loads(requests[-1]['messages'][1]['content'])['themes'][0])
        self.assertTrue(topics.validate_topic_result(data))

    def test_p6_late_response_still_fails_under_its_original_twenty_second_limit(self):
        data, requests, budget = self.p6(classification_delay=21)
        self.assertEqual(data['analysis_status'], 'failed')
        self.assertEqual(len(requests), 2)
        self.assertEqual(data['failures'][0]['code'], 'request_deadline_exceeded')
        self.assertFalse(data['assignments'])
        self.assertEqual(budget['records'][-1]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
