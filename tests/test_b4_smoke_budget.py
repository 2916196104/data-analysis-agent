"""B4 process budget and production SDK parameters: MockTransport only, no paid calls."""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI, APIConnectionError

from scripts.b4_smoke_budget import B4Budget, BudgetBlocked, MODEL, BASE_URL, OUTPUTS, upstream_byte_candidate
from data_analysis_agent.agents import review_topics as topics
from data_analysis_agent.agents.review_answers import NaturalLanguageAnswerGenerator
from test_agent import SETTINGS, completion
from test_review_topic_budget import extracted
import test_review_insights as insight_fixture
from data_analysis_agent.agents.review_insights import fact_catalog
from data_analysis_agent.agents.review_compression import CompactAnalysisContext


def messages(stage, index=0, system='system prompt', **payload):
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(
        dict(stage=stage, index=index, **payload), ensure_ascii=False)}]


def response(content=None, usage=True):
    result = completion({'role': 'assistant', 'content': json.dumps(content or {})})
    data = result.json()
    data['model'] = MODEL
    if usage:
        data['usage']['completion_tokens_details'] = {'reasoning_tokens': 0}
    else:
        data.pop('usage')
    return httpx.Response(200, json=data)


class B4BudgetTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop('SSLKEYLOGFILE', None)
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('no real network')))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sent, self.clock = [], [0.0]
        self.handler = lambda r: response()
        def handle(request):
            self.sent.append(json.loads(request.content))
            # Reservation must already be durable when transport starts.
            ledger = json.loads(self.budget.ledger.read_text('utf-8'))
            self.assertGreaterEqual(ledger['requests'], len(self.sent))
            from hashlib import sha256
            current = next(r for r in ledger['records'] if r['request_sha256'] == sha256(request.content).hexdigest())
            self.assertEqual(current['status'], 'reserved')
            return self.handler(request)
        self.transport = httpx.MockTransport(handle)
        self.budget = B4Budget(self.root / 'ledger.json', mock_transport=self.transport, clock=lambda: self.clock[0])
        self.budget.begin_task('offline-b4')
        self.budget.install()
        self.addCleanup(self.budget.close)

    def send(self, stage='topic_classification', index=0, **changes):
        request = dict(model=MODEL, temperature=0, max_tokens=OUTPUTS[stage],
            response_format={'type': 'json_object'}, messages=messages(stage, index), extra_body={'enable_thinking': False})
        request.update(changes)
        with OpenAI(api_key='offline-not-a-key', base_url=BASE_URL,
                    max_retries=0, timeout=30 if stage.startswith('topic') else 15) as client:
            return client.chat.completions.create(**request)

    @contextmanager
    def blocked(self, reason=None):
        # Native SDK wraps transport exceptions; verify the original budget cause.
        with self.assertRaises((BudgetBlocked, APIConnectionError)) as caught:
            yield
        error = caught.exception
        if isinstance(error, APIConnectionError):
            error = error.__cause__
        self.assertIsInstance(error, BudgetBlocked)
        if reason:
            self.assertRegex(str(error), reason)

    def test_six_requests_share_one_durable_budget_and_seventh_is_never_sent(self):
        stages = ['topic_discovery'] + ['topic_classification'] * 3 + ['answer_draft', 'answer_support_review']
        for i, stage in enumerate(stages):
            self.send(stage, i)
        original = self.budget.snapshot()
        with self.blocked('total_request_limit'):
            self.send(index=7)
        self.assertEqual((len(self.sent), self.budget.snapshot()['requests']), (6, 6))
        self.assertEqual(original, self.budget.snapshot())
        self.assertLessEqual(Decimal(original['reserved_cny']), Decimal('1'))

    def test_fifth_topic_request_is_blocked_before_http(self):
        self.send('topic_discovery')
        for i in range(3): self.send(index=i)
        with self.blocked('topic_request_limit'):
            self.send(index=4)
        self.assertEqual(len(self.sent), 4)

    def test_third_answer_request_is_blocked_before_http(self):
        self.send('answer_draft')
        self.send('answer_support_review')
        with self.blocked('answer_request_limit'):
            self.send('answer_draft', 2)
        self.assertEqual(len(self.sent), 2)

    def test_fee_limit_reserves_full_input_and_output_without_cache_discount(self):
        for i in range(2):
            self.send(index=i, messages=messages('topic_classification', i, system='x' * 100000))
        original = self.budget.snapshot()
        with self.blocked('cost_limit'):
            self.send(index=2, messages=messages('topic_classification', 2, system='x' * 100000))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(original, self.budget.snapshot())
        for row in original['records']:
            self.assertEqual(Decimal(row['reserved_cny']), row['input_tokens_upper'] * Decimal('.000004')
                             + 3000 * Decimal('.000006'))

    def test_transport_timeout_preserves_reservation_and_does_not_retry(self):
        error = httpx.ReadTimeout('original offline timeout')
        self.handler = lambda r: (_ for _ in ()).throw(error)
        # The original httpx error is retained as the SDK timeout's cause.
        from openai import APITimeoutError
        with self.assertRaises(APITimeoutError) as caught: self.send()
        self.assertIs(caught.exception.__cause__, error)
        state = self.budget.snapshot()
        self.assertEqual((state['requests'], len(self.sent)), (1, 1))
        self.assertGreater(Decimal(state['reserved_cny']), 0)
        self.assertIsNone(state['records'][0]['usage_cost_upper_cny'])
        with self.blocked(): self.send(index=1)
        self.assertEqual(self.budget.snapshot()['reserved_cny'], state['reserved_cny'])

    def test_business_exception_is_not_replaced_by_observation(self):
        error = ValueError('original business exception')
        self.handler = lambda r: (_ for _ in ()).throw(error)
        with OpenAI(api_key='offline', base_url=BASE_URL, max_retries=0, timeout=30) as client:
            request = client._client.build_request('POST', BASE_URL + '/chat/completions', json=dict(model=MODEL,
                messages=messages('topic_discovery'), max_tokens=1200, response_format={'type':'json_object'}, enable_thinking=False))
            with self.assertRaises(ValueError) as caught: client._client.send(request)
        self.assertIs(caught.exception, error)

    def test_missing_usage_halts_and_never_means_zero_cost(self):
        self.handler = lambda r: response(usage=False)
        with self.blocked('identity_or_usage_unverified'): self.send()
        row = self.budget.snapshot()['records'][0]
        self.assertIsNone(row['usage'])
        self.assertIsNone(row['usage_cost_upper_cny'])
        self.assertGreater(Decimal(row['reserved_cny']), 0)
        with self.blocked(): self.send(index=1)
        self.assertEqual(len(self.sent), 1)

    def test_actual_success_response_is_unchanged_and_has_one_send(self):
        raw = response({'marker':'original'})
        self.handler = lambda r: raw
        value = self.send('answer_draft')
        self.assertEqual(value.choices[0].message.content, raw.json()['choices'][0]['message']['content'])
        self.assertEqual(value.usage.prompt_tokens, 10)
        self.assertEqual(len(self.sent), 1)

    def test_unknown_billable_categories_and_unexpected_thinking_halt(self):
        raw = response().json()
        raw['usage']['completion_tokens_details']['reasoning_tokens'] = 1
        self.handler = lambda r: httpx.Response(200, json=raw)
        with self.blocked('reasoning_not_verified_zero'): self.send()
        self.assertIsNone(self.budget.snapshot()['records'][0]['usage_cost_upper_cny'])

    def test_unknown_extra_input_category_cannot_be_ignored(self):
        raw = response().json()
        raw['usage']['prompt_tokens_details'] = {'audio_tokens': 4}
        self.handler = lambda r: httpx.Response(200, json=raw)
        with self.blocked('unknown_billable_category'): self.send()
        self.assertIsNone(self.budget.snapshot()['records'][0]['usage_cost_upper_cny'])

    def test_reasoning_usage_missing_is_unknown_not_zero(self):
        raw = response().json()
        raw['usage'].pop('completion_tokens_details')
        self.handler = lambda r: httpx.Response(200, json=raw)
        with self.blocked('reasoning_not_verified_zero'): self.send()
        self.assertGreater(Decimal(self.budget.snapshot()['reserved_cny']), 0)

    def test_thinking_or_unapproved_schema_is_blocked_before_transport(self):
        for changes in ({'extra_body': {'enable_thinking': True}},
                        {'response_format': {'type':'json_schema','json_schema':{'name':'extra'}}}):
            with self.subTest(changes=changes), self.blocked('unapproved_request_or_thinking'):
                self.send(**changes)
        self.assertFalse(self.sent)

    def test_provider_http_failure_is_original_sdk_status_and_retains_reservation(self):
        from openai import InternalServerError
        self.handler = lambda r: httpx.Response(503, json={'error':{'message':'offline overloaded'}})
        with self.assertRaises(InternalServerError) as caught: self.send()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(self.sent), 1)
        state = self.budget.snapshot()
        self.assertGreater(Decimal(state['reserved_cny']), 0)
        self.assertIsNone(state['records'][0]['usage_cost_upper_cny'])
        with self.blocked(): self.send(index=1)
        self.assertEqual(len(self.sent), 1)

    def test_failed_reservation_journal_prevents_http(self):
        with patch.object(self.budget, '_save', side_effect=OSError('offline full disk')):
            with self.assertRaises(APIConnectionError) as caught: self.send()
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertFalse(self.sent)

    def test_topic_and_answer_phase_deadlines_are_independent_and_bounded(self):
        self.send('topic_discovery')
        self.clock[0] = 120
        with self.blocked('deadline_exceeded'): self.send(index=1)
        self.send('answer_draft')
        self.clock[0] = 150
        with self.blocked('deadline_exceeded'): self.send('answer_support_review')
        self.assertEqual(len(self.sent), 2)

    def test_sdk_retries_async_raw_clients_and_direct_network_transport_cannot_bypass(self):
        with self.assertRaisesRegex(BudgetBlocked, 'retries'):
            OpenAI(api_key='offline', base_url=BASE_URL, max_retries=1)
        with httpx.Client() as client, self.assertRaisesRegex(BudgetBlocked, 'unregistered'):
            client.post(BASE_URL + '/chat/completions', json={})
        with httpx.HTTPTransport() as transport, self.assertRaisesRegex(BudgetBlocked, 'unguarded_network'):
            transport.handle_request(httpx.Request('POST', BASE_URL + '/chat/completions'))
        coroutine = httpx.AsyncClient.send(None, httpx.Request('POST', BASE_URL + '/chat/completions'))
        try:
            with self.assertRaisesRegex(BudgetBlocked, 'unplanned_async'): coroutine.send(None)
        finally:
            coroutine.close()
        self.assertFalse(self.sent)

    def test_real_execution_cannot_enable_unproven_bound_with_runtime_flags(self):
        self.budget.close()
        real = B4Budget(self.root / 'real.json')
        real.begin_task('real-not-executed')
        real.install()
        try:
            with self.blocked('bound_unproven'):
                with OpenAI(api_key='offline', base_url=BASE_URL, max_retries=0, timeout=30) as client:
                    client.chat.completions.create(model=MODEL, messages=messages('topic_discovery'), max_tokens=1200,
                        response_format={'type':'json_object'}, extra_body={'enable_thinking':False})
            self.assertEqual(real.snapshot()['requests'], 0)
        finally: real.close()

    def test_task_phase_and_request_deadlines_block_before_or_after_transport(self):
        self.clock[0] = 180
        with self.blocked('deadline_exceeded'): self.send()
        self.assertFalse(self.sent)
        self.clock[0] = 0
        def late(request):
            self.clock[0] += 31
            return response()
        self.handler = late
        with self.blocked('request_deadline_exceeded'): self.send()
        self.assertEqual(len(self.sent), 1)

    def test_concurrent_reservations_are_atomic(self):
        barrier, results = threading.Barrier(5), []
        def attempt(i):
            barrier.wait()
            try: self.send(index=i); results.append('sent')
            except (BudgetBlocked, APIConnectionError) as error:
                self.assertIsInstance(error if isinstance(error, BudgetBlocked) else error.__cause__, BudgetBlocked)
                results.append('blocked')
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results.count('sent'), 3)  # three distinct classification batches
        self.assertEqual(results.count('blocked'), 2)
        self.assertEqual(self.budget.snapshot()['requests'], 3)

    def test_full_messages_unicode_schema_and_protocol_are_included_in_candidate(self):
        payload = dict(messages=messages('topic_discovery', system='系统😀\n', schema={'中文':'café'}))
        upper = upstream_byte_candidate(payload)
        total_content = sum(len(m['content'].encode('utf-8')) for m in payload['messages'])
        protocol = '<｜begin▁of▁sentence｜><｜User｜><｜Assistant｜></think>'
        self.assertEqual(upper, total_content + len(protocol.encode('utf-8')))
        payload['messages'][1]['content'] += '更长的schema😀'
        self.assertGreater(upstream_byte_candidate(payload), upper)

    def test_second_guard_and_old_ledger_are_rejected(self):
        with self.assertRaises(FileExistsError): B4Budget(self.budget.ledger)
        other = B4Budget(self.root / 'other.json', mock_transport=self.transport)
        with self.assertRaisesRegex(BudgetBlocked, 'already_installed'): other.install()

    def test_fourth_classification_batch_workload_is_blocked_before_discovery(self):
        with self.assertRaisesRegex(BudgetBlocked, 'three_batches'):
            topics.analyze_aspect_topics(extracted(61), settings=replace(SETTINGS, model=MODEL, base_url=BASE_URL))
        self.assertFalse(self.sent)

    def test_semantic_batch_failure_stops_later_paid_attempts_without_rewriting_the_failure(self):
        def handler(request):
            payload = json.loads(json.loads(request.content)['messages'][1]['content'])
            if payload['stage'] == 'topic_discovery':
                return response({'themes':[{'n':'配送迟延','d':'明确抱怨配送迟延','q':['u1']}]})
            return response({'rows': []})  # Valid wire JSON, incomplete independent review.
        self.handler = handler
        result = topics.analyze_aspect_topics(extracted(duplicate=True),
            settings=replace(SETTINGS, model=MODEL, base_url=BASE_URL))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.budget.snapshot()['requests'], 2)
        self.assertNotEqual(result['analysis_status'], 'complete')
        self.assertEqual(result['coverage']['evaluated_opinions'], 0)
        self.assertEqual(result['failures'][0]['code'], 'assignment_coverage_mismatch')


class AnswerThinkingTests(unittest.TestCase):
    """Use current reviews_csv fixture and native Validator, not legacy Amazon scope."""
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('no real network')))
        self.full, self.context = insight_fixture.InsightTests().topic_context()
        self.assertEqual(self.full['validation']['status'], 'passed')
        self.sent = []

    def handler(self, request):
        body = json.loads(request.content)
        self.sent.append(body)
        payload = json.loads(body['messages'][-1]['content'])
        if payload['stage'] == 'answer_draft':
            draft = {'focus': payload['required_focus'], 'insights': [{'kind':'interpretation',
                'refs':['F1','F2'], 'text':'制冷速度关注降温快慢，运行噪声关注安静程度。'}]}
        elif payload['stage'] == 'answer_support_review':
            draft = {'decisions': [{'index':item['index'], 'supported':True, 'reason':'supported',
                'detail':'主题定义的描述性释义。'} for item in payload['insights']]}
        elif payload['stage'] == 'topic_discovery':
            draft = {'themes': [{'n':'配送迟延', 'd':'买家明确抱怨配送迟延', 'q':['u1']}]}
        else:
            draft = {'rows': [{'i':row['opinion_id'], 's':'a', 'm':[['t1',{'quote_id':row['opinion_id']}]]}
                for row in payload['opinions']]}
        return response(draft)

    def generate(self, settings):
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(self.handler)))
        with patch('openai.OpenAI', side_effect=client):
            return NaturalLanguageAnswerGenerator().generate(self.context['question'], self.context,
                settings=settings, validated=True)

    def test_both_native_answer_stages_disable_thinking_without_changing_facts_or_citations(self):
        before = deepcopy(self.context)
        baseline, baseline_diag = self.generate(SETTINGS)
        baseline_messages = [b['messages'] for b in self.sent]
        self.sent.clear()
        answer, diag = self.generate(replace(SETTINGS, model=MODEL, base_url=BASE_URL))
        self.assertEqual(diag['request_stages'], ['draft', 'support_review'])
        self.assertEqual(diag['generation_status'], 'llm')
        self.assertEqual([b['enable_thinking'] for b in self.sent], [False, False])
        self.assertEqual([b['max_tokens'] for b in self.sent], [360, 280])
        self.assertEqual([b['messages'] for b in self.sent], baseline_messages)
        self.assertEqual(answer, baseline)
        self.assertEqual(self.context, before)
        self.assertTrue(answer['evidence_refs'])

    def test_other_models_and_providers_keep_their_original_wire_parameters(self):
        for base_url, model in [
            (BASE_URL, 'deepseek-ai/DeepSeek-V3.2'), (BASE_URL, 'Pro/deepseek-ai/DeepSeek-V3.1'),
            ('https://api.siliconflow.com/v1', MODEL), ('https://other.invalid/v1', MODEL),
            ('https://api.siliconflow.cn.evil.invalid/v1', MODEL)]:
            with self.subTest(base_url=base_url, model=model):
                self.sent.clear()
                answer, _ = self.generate(replace(SETTINGS, model=model, base_url=base_url))
                self.assertEqual(answer['generation_status'], 'llm')
                self.assertEqual(len(self.sent), 2)
                self.assertTrue(all('enable_thinking' not in b and 'thinking_budget' not in b for b in self.sent))

    def test_actual_topic_sdk_and_both_answer_sdk_paths_use_same_six_request_ledger(self):
        # Transport integration only: topic workload and trusted answer-context fixture are independent.
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        budget = B4Budget(root / 'ledger.json', mock_transport=httpx.MockTransport(self.handler))
        budget.begin_task('native-paths-offline')
        budget.install()
        self.addCleanup(budget.close)
        settings = replace(SETTINGS, model=MODEL, base_url=BASE_URL)
        data = topics.analyze_aspect_topics(extracted(duplicate=True), settings=settings)
        self.assertEqual(data['analysis_status'], 'complete')
        self.assertEqual(data['coverage']['evaluated_opinions'], 61)
        answer, diag = NaturalLanguageAnswerGenerator().generate(self.context['question'], self.context,
            settings=settings, validated=True)
        self.assertEqual(diag['generation_status'], 'llm')
        self.assertEqual(budget.snapshot()['requests'], 6)
        self.assertEqual([r['stage'] for r in budget.snapshot()['records']],
            ['topic_discovery'] + ['topic_classification'] * 3 + ['answer_draft','answer_support_review'])
        self.assertTrue(all(b['enable_thinking'] is False for b in self.sent))
        self.assertEqual([b['max_tokens'] for b in self.sent], [1200, 3000, 3000, 3000, 360, 280])
        self.assertTrue(answer['evidence_refs'])

