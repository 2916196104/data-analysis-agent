"""Preparation only: synthetic sources/SDK/mock processes, never a paid request."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from scripts import run_siliconflow_schema_probe as probe
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.agents.review_structured_evidence import model_payload, audit_output, schemas
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from fixtures.shadow_siliconflow_probe_mock import response


class SyntheticProbeTests(unittest.TestCase):
    OS_RECEIPTS=[]

    def setUp(self):
        self.directory=Path(self.enterContext(TemporaryDirectory(prefix='sf-probe-offline-')))
        self.env=self.directory/'.env.shadow'
        self.env.write_text(f'SHADOW_MODEL_ID={probe.provider.MODEL}\nSHADOW_BASE_URL={probe.provider.BASE_URL}\n'
                            'SHADOW_API_KEY=SYNTHETIC-ONLY-KEY\n',encoding='utf-8')
        self.config=probe.provider.scoped_config(self.env)
        self.calls=[];self.clients=[]
        self.addCleanup(lambda:[c.close() for c in self.clients])
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('business/model/network forbidden')))

    def judge(self, mode='supported'):
        def handle(request):
            body=json.loads(request.content);self.calls.append((request,body))
            payload=json.loads(body['messages'][1]['content'])
            return httpx.Response(400 if mode=='http400' else 200,json=response(payload,mode))
        client=probe.ProbeJudge(self.config,transport=httpx.MockTransport(handle))
        self.clients.append(client);return client

    def test_default_preflight_reads_no_business_data_or_model_and_sends_nothing(self):
        with patch.object(probe.provider,'OpenAI',side_effect=AssertionError('must not create SDK')):
            check=probe.preflight(self.env)
        self.assertEqual(check['status'],'prepared_not_authorized');self.assertEqual(check['missing'],[])
        self.assertEqual(check['real_api_calls'],0);self.assertEqual(check['real_database_calls'],0)
        self.assertEqual(check['real_embedding_calls'],0);self.assertEqual(check['max_api_requests'],1)
        self.assertFalse(check['publication_allowed']);self.assertFalse(check['semantic_verified'])

    def test_fixed_synthetic_sources_pass_actual_p0_and_b1_input(self):
        contract,result=probe.fixed_inputs();request=probe.fixed_request();candidate=request.candidates[0]
        self.assertEqual(result.scope.source,'SYNTHETIC');self.assertEqual(result.scope.product_ids,('SYNTHETIC-PRODUCT',))
        self.assertEqual(request.contract,contract);self.assertEqual(len(request.contract.required_facts.items),1)
        self.assertEqual(request.contract.required_facts.relations,());self.assertEqual(len(request.candidates),1)
        self.assertEqual(candidate.quote,'制冷不足');self.assertEqual((candidate.quote_start,candidate.quote_end),(4,8))
        self.assertEqual(candidate.review_text[4:8],candidate.quote)
        self.assertEqual(probe.fixed_request(),request)

    def test_actual_b1_schema_and_fact_matrix_not_simplified(self):
        body=probe.probe_body(model_payload(probe.fixed_request()))
        schema=body['response_format']['json_schema']['schema']
        self.assertEqual(schema,probe.provider.strict_output_schema())
        self.assertIn('FactJudgment',schema['$defs']);self.assertIn('RelationJudgment',schema['$defs'])
        self.assertIn('Span',schema['$defs']);self.assertTrue(body['response_format']['json_schema']['strict'])
        self.assertEqual(body['max_tokens'],768);self.assertFalse(body['enable_thinking'])
        for field in ('max_completion_tokens','reasoning_effort','tools'):self.assertNotIn(field,body)
        for name,value in schemas().items():
            p=Path(__file__).parents[1]/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json'
            self.assertEqual(value,json.loads(p.read_text(encoding='utf-8')))

    def test_modified_question_candidate_quote_context_scope_and_fact_cannot_be_sent(self):
        original=json.loads(model_payload(probe.fixed_request()))
        mutations=[lambda v:v['candidates'][0].update(review_text='another synthetic comment'),
            lambda v:v['candidates'][0].update(quote='other synthetic quote'),
            lambda v:v['contract'].update(original_question='different query'),
            lambda v:v['contract']['scope'].update(product_ids=['OTHER']),
            lambda v:v['contract']['required_facts']['items'][0].update(description='another target')]
        for mutate in mutations:
            value=json.loads(canonical_json(original));mutate(value);client=self.judge()
            with self.assertRaises(probe.provider.ShadowProviderError):client.judge(canonical_json(value))
            self.assertEqual(client.metrics['model_calls'],0)
        self.assertEqual(self.calls,[])

    def test_budget_is_explicit_heuristic_and_separate_from_business_experiment(self):
        check=probe.preflight(self.env)
        self.assertEqual(check['local_input_token_proxy'],check['request_body_bytes']+1024)
        self.assertLessEqual(check['local_input_token_proxy'],8000)
        self.assertIsNone(check['token_estimate_error_bound']);self.assertIsNone(check['guaranteed_max_cost_cny'])
        self.assertTrue(check['budget_separate_from_S1_S2_S3'])
        self.assertEqual(check['conditional_peak_cost_cny'],'0.02201')
        self.assertEqual(check['conditional_off_peak_cost_cny'],'0.011005')

    def test_real_sdk_mock_returns_traceable_quote_with_zero_business_permission(self):
        request=probe.fixed_request();client=self.judge();raw=client.judge(model_payload(request))
        outcome=audit_output(request,raw)
        self.assertEqual(outcome.execution_state,'validated_shadow');self.assertTrue(outcome.structural_goal_satisfied)
        self.assertFalse(outcome.semantic_verified);self.assertFalse(outcome.publication_allowed)
        self.assertEqual(outcome.permitted_target_fact_ids,())
        judgment=json.loads(raw)['fact_judgments'][0]
        self.assertEqual(judgment['support_refs'][0]['text'],probe.QUOTE)
        self.assertEqual(client.metrics['reasoning_tokens'],0)
        self.assertFalse(client.metrics['reasoning_content_present'])
        self.assertEqual(client.metrics['finish_reason'],'stop');self.assertEqual(client.metrics['total_tokens'],130)
        self.assertEqual(client.client.max_retries,0);self.assertEqual(client.client.timeout.read,20)
        with self.assertRaisesRegex(ValueError,'single_attempt_only'):client.judge(model_payload(request))
        self.assertEqual(len(self.calls),1)

    def test_http_failure_invalid_json_model_drift_and_length_do_not_retry_or_downgrade(self):
        for mode in ('http400','invalid_json','model_drift','length'):
            client=self.judge(mode)
            with self.assertRaises(probe.provider.ShadowProviderError):client.judge(model_payload(probe.fixed_request()))
            self.assertEqual(client.metrics['model_calls'],1)
        self.assertEqual(len(self.calls),4)
        for _,body in self.calls:self.assertEqual(body['response_format']['type'],'json_schema')
        self.assertEqual(self.clients[0].metrics['http_status'],400)
        self.assertEqual(self.clients[-1].metrics['finish_reason'],'length')
        self.assertEqual(self.clients[-2].metrics['response_model'],'other-model')

    def test_reasoning_is_observed_counted_once_and_fails_closed(self):
        client=self.judge('reasoning')
        with self.assertRaises(probe.provider.ShadowProviderError):client.judge(model_payload(probe.fixed_request()))
        self.assertEqual(client.metrics['reasoning_tokens'],5);self.assertTrue(client.metrics['reasoning_content_present'])
        self.assertEqual(client.metrics['output_tokens'],30)
        self.assertEqual(client.metrics['cost_cny_at_peak_rates'],'0.0004008')
        self.assertNotIn('SYNTHETIC private reasoning',canonical_json(client.metrics))
        self.assertEqual(client.metrics['state'],'unexpected_reasoning_in_non_thinking_mode')

    def test_http200_and_json_are_not_enough_unknown_fact_or_span_rejected_by_b2(self):
        request=probe.fixed_request()
        for mode in ('unknown_fact','bad_span'):
            client=self.judge(mode);outcome=audit_output(request,client.judge(model_payload(request)))
            self.assertEqual(client.metrics['http_status'],200)
            self.assertEqual(outcome.execution_state,'output_invalid');self.assertFalse(outcome.publication_allowed)

    def test_paid_action_requires_explicit_flag_before_preflight_or_side_effects(self):
        with patch.object(probe,'preflight',side_effect=AssertionError('no preflight yet')):
            with self.assertRaisesRegex(ValueError,'explicit_paid_synthetic_authorization_required'):probe.run_once()
        for flag in ('--model','--payload','--max-calls','--output-dir','--business-env'):
            with patch.object(sys,'argv',['probe',flag,'value']),redirect_stdout(StringIO()),redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):probe.main()

    def test_probe_factory_requires_matching_claim_and_is_once_only(self):
        destination=self.directory/'probe';destination.mkdir()
        config=self.config.model_copy(update=dict(usage_file=str(destination/'provider-usage.json')))
        with patch.object(probe,'ROOT',self.directory),patch.object(probe,'RUN_DIR',destination),\
             patch.object(probe,'ProbeJudge',return_value='SYNTHETIC-CLIENT') as make:
            with self.assertRaises(FileNotFoundError):probe.create_probe_judge(**config.model_dump(mode='json'))
            (destination/'attempt.json').write_text(canonical_json(dict(input_sha256='wrong',env_sha256=config.env_sha256)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'identity_mismatch'):probe.create_probe_judge(**config.model_dump(mode='json'))
            (destination/'attempt.json').write_text(canonical_json(dict(
                input_sha256=json.loads(model_payload(probe.fixed_request()))['input_sha256'],env_sha256=config.env_sha256)),encoding='utf-8')
            self.assertEqual(probe.create_probe_judge(**config.model_dump(mode='json')),'SYNTHETIC-CLIENT')
            with self.assertRaises(FileExistsError):probe.create_probe_judge(**config.model_dump(mode='json'))
            make.assert_called_once()

    def test_runner_records_outcome_and_cannot_repeat_even_after_failure(self):
        for state,missing_usage in (('validated_shadow',False),('model_failed',False),('model_failed',True)):
            destination=self.directory/(state+str(missing_usage));calls=[]
            class Adapter:
                def __init__(self,**kw):self.config=json.loads(kw['judge_factory'].config_json)
                def run(self,c,r,**kw):
                    calls.append(kw['request_id'])
                    usage=dict(request_id=probe.REQUEST_ID,input_sha256=json.loads(model_payload(probe.fixed_request()))['input_sha256'],
                        usage_known=True,input_tokens=100,output_tokens=30,reasoning_tokens=0,reasoning_content_present=False,
                        cost_cny_at_peak_rates='0.0004008',http_status=200,finish_reason='stop',
                        request_started_at_utc='2026-11-06T01:00:00+00:00',completed_at_utc='2026-11-06T01:00:01+00:00')
                    if not missing_usage:Path(self.config['usage_file']).write_text(canonical_json(usage),encoding='utf-8')
                    return SimpleNamespace(outcome=SimpleNamespace(execution_state=state),judgments=None,
                        debug_record=lambda:dict(outcome=dict(publication_allowed=False,semantic_verified=False)))
            with patch.object(probe,'ROOT',self.directory),patch.object(probe,'RUN_DIR',destination),\
                 patch.object(probe,'StructuredEvidenceShadow',Adapter):
                value=probe.run_once(paid_synthetic_authorized=True)
                self.assertEqual(calls,[probe.REQUEST_ID]);self.assertFalse(value['publication_allowed'])
                self.assertEqual(value['b1_b2_output_valid'],True if state=='validated_shadow' else None)
                self.assertEqual(value['http_strict_request_accepted'],None if missing_usage else True)
                self.assertEqual(value['non_thinking_observation'],'unknown' if missing_usage else 'no_reasoning_observed')
                if missing_usage:self.assertIsNone(value['estimated_cost_cny_range'])
                else:self.assertEqual(value['estimated_cost_cny_at_observed_time'],'0.0004008')
                self.assertTrue((destination/'result.json').exists())
                with self.assertRaises(FileExistsError):probe.run_once(paid_synthetic_authorized=True)
                self.assertEqual(len(calls),1)

    def worker(self,mode='supported',*,marker=None,timeout=5,cancel_event=None):
        c,result=probe.fixed_inputs()
        config=self.config.model_dump(mode='json')|dict(mode=mode,marker=str(marker) if marker else None)
        adapter=StructuredEvidenceShadow(settings=ShadowSettings(enabled=True,timeout_seconds=timeout),
            judge_factory=JudgeFactory(entrypoint='fixtures.shadow_siliconflow_probe_mock:create_mock_probe_judge',
                                      config_json=canonical_json(config)))
        run=adapter.run(c,result,trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                        request_id=probe.REQUEST_ID,cancel_event=cancel_event)
        self.assertTrue(run.diagnostics['reaped']);self.assertTrue(run.diagnostics['threads_released'])
        self.assertFalse(run.outcome.publication_allowed);self.assertFalse(run.outcome.semantic_verified)
        self.OS_RECEIPTS.append(dict(test=self.id(),mode=mode,**run.debug_record()))
        return run

    def test_actual_b2_worker_mock_success_bad_span_crash_and_invalid_json(self):
        for mode,state in (('supported','validated_shadow'),('bad_span','output_invalid'),
                           ('exit','model_failed'),('invalid_json','model_failed')):
            with self.subTest(mode=mode):
                run=self.worker(mode);self.assertEqual(run.outcome.execution_state,state,run.debug_record())
                if mode=='supported':self.assertEqual(run.judgments.fact_judgments[0].support_refs[0].text,probe.QUOTE)

    def test_actual_b2_worker_deadline_and_cancel_after_mock_http_starts(self):
        marker=self.directory/'deadline'
        run=self.worker('hang',marker=marker,timeout=2)
        self.assertTrue(marker.exists());self.assertEqual(int(marker.read_text()),run.diagnostics['process_id'])
        self.assertEqual(run.outcome.reason_codes,('deadline_exceeded',))
        marker=self.directory/'cancel';event=Event()
        def cancel():
            end=monotonic()+4
            while not marker.exists() and monotonic()<end:sleep(.01)
            event.set()
        thread=Thread(target=cancel);thread.start()
        try:run=self.worker('hang',marker=marker,cancel_event=event)
        finally:thread.join(timeout=5)
        self.assertTrue(marker.exists());self.assertEqual(int(marker.read_text()),run.diagnostics['process_id'])
        self.assertEqual(run.outcome.execution_state,'cancelled');self.assertFalse(thread.is_alive())

    def test_business_factory_and_three_compatibility_blockers_remain_closed(self):
        self.assertEqual(probe.provider.compatibility_blockers(),('model_strict_schema_unverified',
            'native_tokenizer_binding_unverified','model_non_thinking_budget_unverified'))
        with self.assertRaisesRegex(ValueError,'compatibility_unverified'):
            probe.provider.create_judge(**self.config.model_dump(mode='json'))
        with self.assertRaises(ValueError):
            probe.provider.create_judge(**(self.config.model_dump(mode='json')|dict(probe_only=True)))
        check=probe.provider.preflight(self.env)
        self.assertEqual(check['status'],'blocked_compatibility');self.assertEqual(len(check['missing']),3)


if __name__=='__main__':unittest.main()
