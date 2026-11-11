"""E0 独立准入探针：真实 SDK + MockTransport；明确的未满足契约保持 FAIL。"""
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx
from openai import OpenAI as OriginalOpenAI
from psycopg.pq import TransactionStatus
from data_analysis_agent.agents.review_budget import P6SharedBudget, P6CostAuthorization, P6BudgetBlocked, make_execution_budget
from data_analysis_agent.agents.review_topics import call_topic_model, TopicCatalog, DISCOVER_PROMPT
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.database import PostgresConnection, connect_database
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.store import connect_review_database


class Clock:
    def __init__(self): self.value = 0.
    def __call__(self): return self.value


class IndependentReadiness(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('E0 no network')))
        self.pg = self.enterContext(patch('psycopg.connect', side_effect=AssertionError('E0 no PostgreSQL')))
        self.addCleanup(self.network.assert_not_called)
        self.addCleanup(self.pg.assert_not_called)
        self.settings = AgentSettings(model='Pro/deepseek-ai/DeepSeek-V3.2', api_key='offline-not-a-real-key',
            base_url='https://api.siliconflow.cn/v1', p6_preparation_enabled=True,
            p6_execution_enabled=True, p6_publication_enabled=True)
        self.clock, self.sent, self.clients, self.client_options = Clock(), [], [], []

    def budget(self):
        authorization = P6CostAuthorization(authorized=True, allow_real=True, model=self.settings.model,
            base_url=self.settings.base_url, currency='TEST', amount_limit=Decimal('100'),
            input_price=Decimal('0.0001'), output_price=Decimal('0.0001'), billing_categories=('input','output'),
            token_upper_bound_id='SYNTHETIC-ONLY', token_upper_bound=lambda prompt, body: 1000)
        budget = P6SharedBudget(authorization, clock=self.clock)
        budget.start(('service',), lambda: None, input_hashes={'service':content_sha256({})})
        budget.begin_aspect('service')
        return budget

    def call(self, budget, *, usage=None, actual_model=None, delay=0, status=200):
        def handler(request):
            self.sent.append(json.loads(request.content))
            self.clock.value += delay
            if status != 200:
                return httpx.Response(status, json={'error':{'code':'insufficient_quota' if status == 429 else 'offline', 'message':'offline'}})
            return httpx.Response(200, headers={'x-siliconcloud-trace-id':'offline-trace-only'}, json={
                'id':'offline-response', 'object':'chat.completion', 'created':1,
                'model':actual_model or self.settings.model,
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
                    'content':json.dumps({'themes':[{'n':'响应速度','d':'客服回复缓慢','q':['u1']}]},ensure_ascii=False)}}],
                'usage':usage or {'prompt_tokens':50,'completion_tokens':20,'total_tokens':70}})
        def client(**kwargs):
            self.client_options.append({k:v for k,v in kwargs.items() if k != 'api_key'})
            transport = httpx.Client(transport=httpx.MockTransport(handler))
            self.clients.append(transport)
            return OriginalOpenAI(**kwargs, http_client=transport)
        with patch('openai.OpenAI', side_effect=client):
            return call_topic_model(self.settings, DISCOVER_PROMPT,
                {'stage':'topic_discovery','opinions':[{'opinion_id':'u1','quote':'客服回复缓慢','aspect':'service'}]},
                TopicCatalog, 20, shared_budget=budget, aspect='service')

    def test_default_budget_cannot_obtain_paid_authorization_with_all_flags_enabled(self):
        budget = make_execution_budget(self.settings)
        budget.start(('service',), lambda:None, input_hashes={'service':content_sha256({})})
        budget.begin_aspect('service')
        with self.assertRaises(P6BudgetBlocked) as caught: budget.ensure_authorized(self.settings)
        self.assertEqual(caught.exception.code, 'cost_not_authorized')
        self.assertEqual(budget.snapshot()['attempts'], 0)

    def test_real_sdk_serialization_disables_thinking_and_retries_and_closes_client(self):
        budget = self.budget(); result = self.call(budget)
        self.assertEqual(result.themes[0].name, '响应速度')
        self.assertEqual(len(self.sent), 1)
        self.assertIs(self.sent[0]['enable_thinking'], False)
        self.assertEqual(self.sent[0]['max_tokens'], 1200)
        self.assertEqual(self.client_options[0]['max_retries'], 0)
        self.assertLessEqual(self.client_options[0]['timeout'], 20)
        self.assertTrue(self.clients[0].is_closed)
        self.assertEqual(budget.snapshot()['records'][0]['status'], 'accepted')

    def test_actual_sdk_503_has_one_attempt_and_keeps_reservation(self):
        budget = self.budget()
        with self.assertRaises(Exception): self.call(budget, status=503)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')
        self.assertIsNone(budget.snapshot()['actual_cost_total'])
        self.assertTrue(self.clients[0].is_closed)

    def test_actual_sdk_402_halts_next_request_before_transport(self):
        budget = self.budget()
        with self.assertRaises(Exception): self.call(budget, status=402)
        self.assertEqual(budget.halted_reason, 'provider_402')
        with self.assertRaises(P6BudgetBlocked): self.call(budget)
        self.assertEqual(len(self.sent), 1)

    def test_actual_sdk_late_response_cannot_be_accepted(self):
        budget = self.budget()
        with self.assertRaises(P6BudgetBlocked): self.call(budget, delay=21)
        self.assertNotEqual(budget.snapshot()['records'][0]['status'], 'accepted')
        self.assertEqual(budget.snapshot()['attempts'], 1)
        self.assertTrue(self.clients[0].is_closed)

    def test_native_cache_usage_without_cache_price_is_not_an_exact_actual_bill(self):
        budget = self.budget()
        self.call(budget, usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
            'prompt_tokens_details':{'cached_tokens':80},'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20})
        # 未提供缓存单价时可继续保守预留，但不能将不区分缓存的乘积认证为精确账单。
        self.assertFalse(budget.snapshot()['actual_cost_complete'], budget.snapshot())

    def test_unexpected_reasoning_with_thinking_disabled_is_rejected(self):
        budget = self.budget()
        with self.assertRaises((P6BudgetBlocked, ValueError)):
            self.call(budget, usage={'prompt_tokens':50,'completion_tokens':120,'total_tokens':170,
                'completion_tokens_details':{'reasoning_tokens':100}})

    def test_response_model_outside_authorized_identity_is_rejected(self):
        budget = self.budget()
        with self.assertRaises((P6BudgetBlocked, ValueError)):
            self.call(budget, actual_model='unapproved-model-with-unknown-billing')

    def test_explicit_quota_error_429_halts_the_budget(self):
        budget = self.budget()
        with self.assertRaises(Exception): self.call(budget, status=429)
        self.assertIsNotNone(budget.halted_reason, '429 insufficient_quota is not normalized; this is not ordinary rate limiting')

    def test_pg_factory_begins_repeatable_read_only_and_always_closes_on_error(self):
        raw = MagicMock(); raw.info.transaction_status = TransactionStatus.IDLE
        with patch('psycopg.connect', return_value=raw) as factory:
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                with closing(connect_database('postgresql://offline.invalid/e0_test', readonly=True)) as conn, conn:
                    conn.execute('BEGIN')
                    raise RuntimeError('injected')
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs['connect_timeout'], 10)
        self.assertTrue(factory.call_args.kwargs['autocommit'])
        self.assertIn('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY', [row.args[0] for row in raw.execute.call_args_list])
        raw.rollback.assert_called_once(); raw.close.assert_called_once()

    def test_pg_review_schema_check_failure_closes_connection(self):
        raw = MagicMock()
        with patch('data_analysis_agent.reviews.store.connect_database', return_value=raw), \
             patch('data_analysis_agent.reviews.store.check_review_schema', side_effect=ValueError('schema')):
            with self.assertRaises(ValueError): connect_review_database('postgresql://offline.invalid/e0_test', readonly=True)
        raw.close.assert_called_once()

    def test_same_counts_new_human_revision_is_not_a_stale_ranking_signal(self):
        import test_p6c_budget_execution as fixtures
        from data_analysis_agent.reviews.human_review import init_workbench, save_human_review
        case = fixtures.P6CBudgetExecutionTests(); case.setUp(); self.addCleanup(case.doCleanups)
        old_prepared = case.prepared
        old = old_prepared.get_input('service')
        item = old['items'][0]
        init_workbench(case.fixture.db)
        save_human_review(case.fixture.db, item['review_id'], annotation_id=item['annotation_id'],
            expected_revision_id=None, action='corrected', reviewer='E0-SYNTHETIC-ONLY',
            opinions=[{'aspect':'service','sentiment':'negative','quote':'回复缓慢'}])
        case.prepare()
        current = case.prepared.get_input('service')
        self.assertEqual(old_prepared.validate()['current_snapshot']['negative_review_counts'],
                         case.prepared.validate()['current_snapshot']['negative_review_counts'])
        self.assertTrue(case.prepared.validate()['current_ranking_matches'])
        self.assertNotEqual(old['opinions'], current['opinions'])
        self.assertEqual(old_prepared.get_input('service'), old)

    def test_source_validity_is_not_proof_of_theme_semantic_quality(self):
        import test_p6c_budget_execution as fixtures
        from data_analysis_agent.agents.review_publication import publish_p6_execution
        case = fixtures.P6CBudgetExecutionTests(); case.setUp(); self.addCleanup(case.doCleanups)
        def response(request):
            result = case.response(request)
            if request['stage'] == 'topic_discovery':
                body = json.loads(result.choices[0].message.content)
                for theme in body['themes']:
                    theme.update(n='安装师傅迟到', d='预约安装师傅未按时上门')
                result.choices[0].message.content = json.dumps(body,ensure_ascii=False)
            return result
        executed = case.execute(case.budget(response))
        published = publish_p6_execution(executed,case.prepared,
            settings=replace(case.settings,p6_preparation_enabled=True,p6_publication_enabled=True),**case.auth)
        self.assertTrue(published['task_acceptance']['complete'])
        for dim in published['p6_publication']['dimensions']:
            for topic in dim['topics']:
                self.assertEqual(topic['name'], '安装师傅迟到')
                self.assertEqual(topic['semantic_status'], 'model_reviewed_not_human_confirmed')
                self.assertTrue(all('安装' not in r['quote'] and '上门' not in r['quote'] for r in topic['evidence']))


class ProviderContractBoundaryTests(unittest.TestCase):
    setUp = IndependentReadiness.setUp
    budget = IndependentReadiness.budget

    def contract(self, **changes):
        from data_analysis_agent.agents.review_provider import P6ProviderContract
        return replace(P6ProviderContract(model=self.settings.model, base_url=self.settings.base_url,
            source_ref='offline-synthetic-provider-contract-v1', source_sha256=content_sha256({'synthetic':True}),
            cached_input_price=Decimal('0.00001'), completion_tokens_include_reasoning=True), **changes)

    def with_contract(self, **changes):
        policy = replace(self.budget().authorization, provider_contract=self.contract(**changes))
        return self.new_budget(policy)

    def new_budget(self, policy):
        budget = P6SharedBudget(policy, clock=self.clock)
        budget.start(('service',), lambda:None, input_hashes={'service':content_sha256({})})
        budget.begin_aspect('service')
        return budget

    def send(self, budget, *, usage='normal', model='requested', status=200, error=None, finish='stop'):
        def handler(request):
            self.sent.append(json.loads(request.content))
            self.assertTrue(request.url.path.endswith('/chat/completions'))
            headers = {'x-request-id':'offline-request-123', 'x-siliconcloud-trace-id':'offline-trace-456'}
            if status != 200:
                return httpx.Response(status, headers=headers, json={'error':error or {'code':'rate_limit_exceeded'}})
            packet = {'id':'offline-response-789', 'object':'chat.completion', 'created':1,
                'choices':[{'index':0,'finish_reason':finish,'message':{'role':'assistant','content':json.dumps({
                    'themes':[{'n':'响应速度','d':'客服回复缓慢','q':['u1']}]},ensure_ascii=False)}}]}
            if model != 'missing':packet['model'] = self.settings.model if model == 'requested' else model
            if usage != 'missing':packet['usage'] = {'prompt_tokens':50,'completion_tokens':20,'total_tokens':70} if usage == 'normal' else usage
            return httpx.Response(200, headers=headers, json=packet)
        def client(**kwargs):
            transport = httpx.Client(transport=httpx.MockTransport(handler)); self.clients.append(transport)
            return OriginalOpenAI(**kwargs, http_client=transport)
        with patch('openai.OpenAI', side_effect=client):
            return call_topic_model(self.settings, DISCOVER_PROMPT,
                {'stage':'topic_discovery','opinions':[{'opinion_id':'u1','quote':'客服回复缓慢','aspect':'service'}]},
                TopicCatalog, 20, shared_budget=budget, aspect='service')

    def last(self, budget): return budget.snapshot()['records'][-1]

    def assert_stopped_before_transport(self, budget):
        before = len(self.sent)
        attempts = budget.snapshot()['attempts']
        with self.assertRaises(P6BudgetBlocked):self.send(budget)
        self.assertEqual(len(self.sent), before)
        self.assertEqual(budget.snapshot()['attempts'], attempts)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_raw_usage_identity_and_http_audit_are_saved_without_sdk_default_fields(self):
        budget = self.budget(); usage = {'prompt_tokens':50,'completion_tokens':20,'total_tokens':70}
        self.send(budget, usage=usage); row = self.last(budget)
        self.assertEqual(row['raw_usage'], usage)
        self.assertEqual(row['response_model'], self.settings.model)
        self.assertEqual(row['request_id'], 'offline-request-123')
        self.assertEqual(row['response_id'], 'offline-response-789')
        self.assertEqual(row['trace_id'], 'offline-trace-456')
        self.assertEqual(row['http_status'], 200)
        self.assertEqual(row['actual_cost'], '0.0070')
        self.assertEqual(row['cost_status'], 'exact')
        self.assertEqual(row['identity_source'], 'response')
        row['raw_usage']['prompt_tokens'] = 999
        self.assertEqual(self.last(budget)['raw_usage'], usage)

    def test_cache_is_priced_once_under_pinned_server_contract(self):
        usage = {'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
            'prompt_tokens_details':{'cached_tokens':80},'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20}
        budget = self.with_contract(); self.send(budget, usage=usage); row = self.last(budget)
        self.assertEqual(Decimal(row['actual_cost']), Decimal('0.0048'))  # 20*0.0001+80*0.00001+20*0.0001
        self.assertEqual(row['billing_usage']['uncached_tokens'], 20)
        self.assertEqual(row['billing_usage']['cached_tokens'], 80)
        self.assertEqual(row['raw_usage'], usage)
        self.assertEqual(row['provider_contract_version'], 'review-p6-provider-contract-v1')
        self.assertEqual(row['provider_contract_sha256'], self.contract().source_sha256)
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')

    def test_unknown_cache_price_stays_unknown_and_blocks_later_paid_calls(self):
        budget = self.budget()
        self.send(budget, usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
            'prompt_tokens_details':{'cached_tokens':80}})
        row = self.last(budget)
        self.assertIsNone(row['actual_cost']); self.assertEqual(row['cost_status'], 'unknown')
        self.assertIsNone(budget.snapshot()['actual_cost_total'])
        self.assertFalse(budget.snapshot()['actual_cost_complete'])
        self.assertEqual(row['cost_reason'], 'cached_input_price_unknown')
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')
        self.assert_stopped_before_transport(budget)

    def test_zero_cache_requires_no_discount_and_does_not_release_reservation(self):
        budget = self.budget()
        self.send(budget, usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
            'prompt_tokens_details':{'cached_tokens':0}})
        self.assertEqual(self.last(budget)['actual_cost'], '0.0120')
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')
        self.assertIsNone(budget.halted_reason)

    def test_cache_hit_or_miss_subtotals_derive_the_complement_without_double_counting(self):
        for fields in ({'prompt_cache_hit_tokens':80}, {'prompt_cache_miss_tokens':20},
                       {'prompt_tokens_details':{'cached_tokens':80}}):
            with self.subTest(fields=fields):
                budget = self.with_contract()
                self.send(budget, usage={'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,**fields})
                self.assertEqual(Decimal(self.last(budget)['actual_cost']), Decimal('0.0048'))

    def test_missing_usage_and_partial_token_fields_are_unknown_and_not_free(self):
        for usage in ('missing', None, {}, {'prompt_tokens':50}, {'completion_tokens':20},
                      {'prompt_tokens_details':{'cached_tokens':5}}):
            with self.subTest(usage=usage):
                budget = self.budget(); self.send(budget, usage=usage)
                row = self.last(budget)
                self.assertIsNone(row['actual_cost'])
                self.assertIsNone(budget.snapshot()['actual_cost_total'])
                self.assertEqual(row['cost_status'], 'unknown')
                self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')
                self.assertEqual(row['raw_usage'], None if usage == 'missing' else usage)

    def test_cache_or_token_total_disagreement_halts_and_preserves_raw_receipt(self):
        cases = [{'total_tokens':71}, {'prompt_cache_hit_tokens':10,'prompt_cache_miss_tokens':45},
                 {'prompt_tokens_details':{'cached_tokens':20},'prompt_cache_hit_tokens':10},
                 {'prompt_tokens_details':{'cached_tokens':51}}, {'prompt_tokens':-1},
                 {'completion_tokens':True}, {'total_tokens':'70'}]
        for changes in cases:
            with self.subTest(changes=changes):
                budget = self.with_contract()
                usage = {'prompt_tokens':50,'completion_tokens':20,'total_tokens':70,**changes}
                with self.assertRaises(P6BudgetBlocked):self.send(budget, usage=usage)
                self.assertEqual(self.last(budget)['raw_usage'], usage)
                self.assertIsNone(self.last(budget)['actual_cost'])
                self.assertEqual(budget.halted_reason, 'usage_contract_invalid')

    def test_missing_total_is_not_fabricated_but_known_two_counter_cost_is_computable(self):
        budget = self.budget(); self.send(budget, usage={'prompt_tokens':50,'completion_tokens':20})
        self.assertIsNone(self.last(budget)['billing_usage']['total_tokens'])
        self.assertEqual(self.last(budget)['actual_cost'], '0.0070')

    def test_nonzero_reasoning_is_rejected_even_with_inclusive_completion_contract(self):
        budget = self.with_contract()
        usage = {'prompt_tokens':50,'completion_tokens':120,'total_tokens':170,
                 'completion_tokens_details':{'reasoning_tokens':100}}
        with self.assertRaises(P6BudgetBlocked):self.send(budget, usage=usage)
        row = self.last(budget)
        self.assertEqual(row['raw_usage'], usage)
        self.assertEqual(row['billing_usage']['completion_tokens'], 120)
        self.assertEqual(row['billing_usage']['reasoning_tokens'], 100)
        self.assertIsNone(row['actual_cost'])
        self.assertEqual(budget.halted_reason, 'unexpected_reasoning_tokens')
        self.assert_stopped_before_transport(budget)

    def test_zero_or_absent_reasoning_never_adds_tokens_to_completion_bill(self):
        for details in (None, {}, {'reasoning_tokens':0}):
            with self.subTest(details=details):
                budget = self.with_contract()
                self.send(budget, usage={'prompt_tokens':50,'completion_tokens':20,'total_tokens':70,
                    'completion_tokens_details':details})
                self.assertEqual(self.last(budget)['actual_cost'], '0.0070')
                self.assertEqual(self.last(budget)['billing_usage']['reasoning_tokens'], (details or {}).get('reasoning_tokens'))

    def test_reasoning_over_completion_or_negative_or_boolean_is_inconsistent(self):
        for value in (21, -1, True, '0'):
            with self.subTest(value=value):
                budget = self.budget()
                with self.assertRaises(P6BudgetBlocked):
                    self.send(budget, usage={'prompt_tokens':50,'completion_tokens':20,'total_tokens':70,
                        'completion_tokens_details':{'reasoning_tokens':value}})
                self.assertEqual(budget.halted_reason, 'usage_contract_invalid')

    def test_unknown_billing_categories_and_extra_tokens_stop_instead_of_becoming_exact(self):
        for changes in ({'billable_categories':['input','output','reasoning']}, {'billable_extra_tokens':1},
                        {'unknown_fee_tokens':1}, {'completion_tokens_details':{'audio_tokens':2}},
                        {'prompt_tokens_details':{'audio_tokens':2}},
                        {'completion_tokens_details':{'rejected_prediction_tokens':2}}):
            with self.subTest(changes=changes):
                budget = self.budget()
                with self.assertRaises(P6BudgetBlocked):
                    self.send(budget, usage={'prompt_tokens':50,'completion_tokens':20,'total_tokens':70,**changes})
                self.assertIsNone(self.last(budget)['actual_cost'])
                self.assertEqual(budget.halted_reason, 'usage_exceeded_authorized_bound')

    def test_output_bound_cannot_be_hidden_by_a_reasoning_breakdown(self):
        budget = self.budget()
        with self.assertRaises(P6BudgetBlocked):
            self.send(budget, usage={'prompt_tokens':50,'completion_tokens':1201,'total_tokens':1251,
                'completion_tokens_details':{'reasoning_tokens':1}})
        self.assertEqual(budget.halted_reason, 'usage_exceeded_authorized_bound')
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')

    def test_exact_model_and_server_pinned_aliases_are_allowed_without_changing_goal_identity(self):
        for model in (self.settings.model, 'verified-offline-alias-v1'):
            with self.subTest(model=model):
                budget = self.with_contract(response_model_aliases=('verified-offline-alias-v1',))
                self.send(budget, model=model)
                self.assertTrue(self.last(budget)['identity_verified'])
                self.assertEqual(self.last(budget)['response_model'], model)
                self.assertEqual(budget.authorization.model, self.settings.model)

    def test_unknown_missing_null_or_redirected_response_model_stops_and_cannot_publish(self):
        from data_analysis_agent.agents.review_budget import P6InputIntegrityError
        for model in ('unknown-model', 'missing', None, 'redirected-new-billing-model'):
            with self.subTest(model=model):
                budget = self.with_contract(response_model_aliases=('verified-offline-alias-v1',))
                with self.assertRaises(P6InputIntegrityError):self.send(budget, model=model)
                self.assertFalse(self.last(budget)['identity_verified'])
                self.assertEqual(budget.halted_reason, 'response_model_unverified')
                self.assertIsNone(self.last(budget)['actual_cost'])
                self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')
                self.assert_stopped_before_transport(budget)

    def test_alias_and_cache_contract_require_server_source_version_and_matching_endpoint(self):
        changes = [{'source_ref':''}, {'source_sha256':'unverified'}, {'version':'v0'},
            {'model':'other'}, {'base_url':'https://other.invalid/v1'}, {'response_model_aliases':['alias']},
            {'cached_input_price':Decimal('NaN')}, {'cached_input_price':Decimal('0.001')}]
        for change in changes:
            with self.subTest(change=change):
                budget = self.with_contract(**change)
                with self.assertRaises(P6BudgetBlocked):self.send(budget)
                self.assertEqual(budget.snapshot()['attempts'], 0)
        self.assertEqual(self.sent, [])

    def test_client_cannot_supply_aliases_provider_contract_or_fee_profile(self):
        from data_analysis_agent.jobs.parameters import AnalysisParameters
        from pydantic import ValidationError
        scope = {'source':'reviews_csv','dataset_version':'offline-only','model':'labels',
            'base_url':'https://labels.invalid/v1','prompt_version':'v1','product_ids':['offline-product'],
            'date_from':'2023-02-01','date_to':'2023-02-28','baseline_from':'2023-01-01','baseline_to':'2023-01-31',
            'question':'差评具体体现在哪里？'}
        AnalysisParameters.model_validate(scope)
        for name in ('provider_contract', 'response_model_aliases', 'cost_authorization'):
            with self.subTest(name=name), self.assertRaises(ValidationError) as caught:
                AnalysisParameters.model_validate({**scope,name:{'allow_real':True}})
            self.assertTrue(any(r['loc'] == (name,) and r['type'] == 'extra_forbidden' for r in caught.exception.errors()))

    def test_ordinary_rate_limit_has_one_attempt_and_is_not_quota(self):
        budget = self.budget()
        with self.assertRaises(Exception):self.send(budget, status=429, error={'code':'rate_limit_exceeded'})
        row = self.last(budget)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(row['http_status'], 429)
        self.assertEqual(row['provider_error_code'], 'rate_limit_exceeded')
        self.assertEqual(row['provider_error_class'], 'rate_limit')
        self.assertIsNone(budget.halted_reason)
        self.assertIsNone(row['actual_cost'])
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')

    def test_402_and_explicit_quota_code_or_type_stop_later_requests(self):
        for status, error in ((402, {'code':'balance_error'}), (429, {'code':'insufficient_quota'}),
                              (429, {'type':'insufficient_quota'}), (400, {'code':'insufficient_quota'})):
            with self.subTest(status=status, error=error):
                budget = self.budget()
                with self.assertRaises(Exception):self.send(budget, status=status, error=error)
                row = self.last(budget)
                self.assertEqual(row['provider_error_class'], 'insufficient_quota')
                self.assertEqual(row['http_status'], status)
                self.assertIsNotNone(budget.halted_reason)
                self.assertEqual(row['provider_error_code'], error.get('code'))
                self.assertEqual(row['provider_error_type'], error.get('type'))
                self.assert_stopped_before_transport(budget)

    def test_unknown_provider_errors_stop_and_do_not_log_message_or_body(self):
        for status, error in ((400, {'code':20012}), (429, {'code':'undocumented_error'}),
                              (418, {'code':'unknown','message':'secret-and-review-text'})):
            with self.subTest(status=status):
                budget = self.budget()
                with self.assertRaises(Exception):self.send(budget, status=status, error=error)
                self.assertEqual(self.last(budget)['provider_error_code'], error['code'])
                self.assertEqual(budget.halted_reason, 'provider_error_unknown')
                self.assertNotIn('secret-and-review-text', json.dumps(budget.snapshot()))
                self.assert_stopped_before_transport(budget)

    def test_provider_failure_keeps_http_request_identity_and_full_reserve(self):
        budget = self.budget()
        with self.assertRaises(Exception):self.send(budget, status=503, error={'code':'overloaded'})
        row = self.last(budget)
        self.assertEqual(row['request_id'], 'offline-request-123')
        self.assertEqual(row['trace_id'], 'offline-trace-456')
        self.assertEqual(row['provider_error_code'], 'overloaded')
        self.assertIsNone(budget.snapshot()['actual_cost_total'])
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.2200')

    def test_accepted_transport_with_invalid_theme_json_keeps_measured_cost_but_no_theme(self):
        budget = self.budget()
        with self.assertRaises(ValueError):self.send(budget, finish='length')
        self.assertEqual(self.last(budget)['status'], 'failed')
        self.assertEqual(self.last(budget)['actual_cost'], '0.0070')
        self.assertEqual(budget.snapshot()['attempts'], 1)

    def test_final_system_and_wire_schema_are_passed_to_upper_bound_without_byte_estimates(self):
        observed = []
        def bound(prompt, body):
            observed.append((prompt, body)); return 1000
        budget = self.new_budget(replace(self.budget().authorization, token_upper_bound=bound))
        self.send(budget)
        self.assertIn('传输时仅使用紧凑 JSON', observed[0][0])
        self.assertIn('schema', json.loads(observed[0][1]))
        self.assertEqual(observed[0][0], self.sent[0]['messages'][0]['content'])
        self.assertEqual(observed[0][1], self.sent[0]['messages'][1]['content'])
        self.assertFalse(make_execution_budget(self.settings).authorization)

    def test_unknown_token_bound_or_billing_prices_rejects_before_real_sdk_transport(self):
        for changes in ({'token_upper_bound':None}, {'token_upper_bound_id':None}, {'input_price':None},
                        {'billing_categories':None}, {'token_upper_bound':lambda p,b:0}):
            with self.subTest(changes=changes):
                budget = self.new_budget(replace(self.budget().authorization, **changes))
                with self.assertRaises(P6BudgetBlocked):self.send(budget)
                self.assertEqual(budget.snapshot()['attempts'], 0)
        self.assertEqual(self.sent, [])

    def worker_via_sdk(self, *, response_model=None, contract=None):
        from test_p6d_publication import PublicationFixture
        fixture = PublicationFixture(); self.addCleanup(fixture.close)
        c = fixture.case
        policy = replace(self.budget().authorization, model=fixture.settings.model,
                         base_url=fixture.settings.base_url, provider_contract=contract)
        if contract:
            policy = replace(policy, provider_contract=replace(contract,
                model=fixture.settings.model, base_url=fixture.settings.base_url))
        budget = P6SharedBudget(policy, clock=c.clock)
        def handler(request):
            body = json.loads(request.content)
            wire = json.loads(body['messages'][1]['content'])
            aspect = c.prepared.goal.required_aspects[len(self.sent)//2]
            self.sent.append(body)
            simulated = c.response({'stage':wire['stage'],'aspect':aspect,
                'messages':[{}, {'content':body['messages'][1]['content']}]})
            model = response_model(aspect, fixture.settings.model) if response_model else fixture.settings.model
            return httpx.Response(200, headers={'x-request-id':'worker-offline-id'}, json={
                'id':'worker-offline-response','object':'chat.completion','created':1,'model':model,
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
                    'content':simulated.choices[0].message.content}}],
                'usage':{'prompt_tokens':50,'completion_tokens':20,'total_tokens':70}})
        def client(**kwargs):
            transport=httpx.Client(transport=httpx.MockTransport(handler));self.clients.append(transport)
            return OriginalOpenAI(**kwargs,http_client=transport)
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity',
                    return_value=(fixture.settings,c.fixture.agent_identity)), \
             patch('data_analysis_agent.agents.review_budget.make_execution_budget',return_value=budget), \
             patch('openai.OpenAI',side_effect=client):
            task = c.fixture.work('具体体现在哪里？',c.fixture.first['task_id'])
        return task, budget

    def test_worker_model_mismatch_after_first_success_hard_fails_without_partial_publication(self):
        task, budget = self.worker_via_sdk(response_model=lambda aspect, model:
            'unknown-redirected-model' if aspect == 'effectiveness' else model)
        self.assertEqual(task['status'], 'failed')
        self.assertNotIn('p6_publication', task['result'])
        self.assertNotIn('answer', task['result'])
        self.assertEqual(budget.halted_reason, 'response_model_unverified')
        self.assertEqual((len(self.sent),budget.snapshot()['attempts']),(3,3))
        self.assertEqual(budget.snapshot()['reserved_cost'], '0.8400')
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_real_worker_with_pinned_alias_and_sdk_budget_keeps_p6d_independent_publication(self):
        alias = 'offline-approved-model-alias'
        task, budget = self.worker_via_sdk(response_model=lambda a,m:alias,
            contract=self.contract(response_model_aliases=(alias,)))
        self.assertEqual(task['status'], 'succeeded',task.get('error'))
        result = task['result']
        self.assertTrue(result['task_acceptance']['complete'])
        self.assertEqual(result['p6_publication']['required_aspects'], ['service','effectiveness','logistics'])
        self.assertEqual(result['answer_model_calls'], 0)
        self.assertEqual(budget.snapshot()['attempts'], 6)
        self.assertEqual(budget.snapshot()['reserved_cost'], '1.8600')
        self.assertTrue(all(row['response_model'] == alias and row['identity_verified'] for row in budget.snapshot()['records']))
        self.assertEqual([len(d['topics']) for d in result['p6_publication']['dimensions']], [1,1,1])


if __name__ == '__main__':
    unittest.main()
