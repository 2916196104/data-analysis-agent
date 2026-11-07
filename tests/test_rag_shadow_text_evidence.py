"""Preparation and SDK Mock tests only. Never the paid runner or historical experiments."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from scripts import run_rag_shadow_text_evidence as entry
from experiments import text_evidence_shadow as port
from data_analysis_agent.agents.review_shadow_adapter import JudgeFactory, ShadowSettings
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from test_shadow_text_evidence_prototype import text_reply

REAL_ADAPTER = port.TextEvidenceShadow


class TextReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory(prefix='text-prep-',dir=entry.privacy.PRIVATE_ROOT))).resolve()
        self.enterContext(patch.object(entry,'OUTPUT_DIR',self.directory/'run'))
        self.env = self.directory/'synthetic.env'
        self.env.write_text(f'SHADOW_MODEL_ID={port.provider.MODEL}\nSHADOW_BASE_URL={port.provider.BASE_URL}\n'
                            'SHADOW_API_KEY=SYNTHETIC-TEXT-KEY\n',encoding='utf-8')
        for target in ('psycopg.connect','socket.create_connection','socket.socket.connect',
                       'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target,side_effect=AssertionError('real I/O forbidden in text preparation')))
        self.data = entry.load_fixed()
        self.contract,self.result,self.request = entry.prepare_cases(self.data['cases'])
        self.reply = text_reply(self.request,[c['expected_status'] for c in self.data['cases']],
                               [c.get('veto_text') for c in self.data['cases']])
        self.capture = self.directory/'mock-http.jsonl'

    def make_factory(self, reply=None, mode='valid'):
        calls=[]
        def factory(*, settings, judge_factory, development_diagnostic_dir):
            calls.append(1)
            self.assertEqual(judge_factory.entrypoint,port.FACTORY)
            config = strict_json_loads(judge_factory.config_json)
            self.assertEqual(set(config),{'model','base_url','env_file','env_sha256','usage_file','estimated_budget_risk_accepted'})
            return REAL_ADAPTER(settings=settings,development_diagnostic_dir=development_diagnostic_dir,
                judge_factory=JudgeFactory(entrypoint='fixtures.shadow_text_evidence_http_mock:create_mock_judge',
                    config_json=canonical_json(config | dict(reply=canonical_json(reply if reply is not None else self.reply),
                        capture_file=str(self.capture),mode=mode))))
        return factory,calls

    def mock_run(self, reply=None, mode='valid'):
        factory,calls=self.make_factory(reply,mode)
        with patch.object(entry.port,'TextEvidenceShadow',side_effect=factory):
            summary=entry.run_authorized(self.env,send_authorized=True,estimated_budget_risk_accepted=True)
            saved=(entry.OUTPUT_DIR/'execution-summary.json').read_bytes()
            with self.assertRaises(ValueError):
                entry.run_authorized(self.env,send_authorized=True,estimated_budget_risk_accepted=True)
            self.assertEqual(saved,(entry.OUTPUT_DIR/'execution-summary.json').read_bytes())
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.capture.read_text(encoding='utf-8').splitlines()),1)
        self.assertFalse(summary['publication_allowed']); self.assertFalse(summary['semantic_verified'])
        self.assertEqual(summary['permitted_target_fact_ids'],[])
        self.assertIsNone(summary['formal_accuracy'])
        return summary

    def diagnostic(self):
        return strict_json_loads((entry.OUTPUT_DIR/'diagnostics/failure.json').read_text(encoding='utf-8'))

    def test_preflight_no_client_and_new_request_budget_no_old_probe(self):
        with (patch.object(port.provider,'OpenAI',side_effect=AssertionError('no client in preflight')),
              patch.object(port.provider,'compatibility_evidence',side_effect=AssertionError('do not reuse B1 probe'))):
            check=entry.preflight(self.env)
        self.assertEqual(check['missing'],[])
        self.assertEqual(check['input_estimate'],7453)
        self.assertEqual(check['max_model_calls'],1)
        self.assertEqual(check['output_limit'],2500)
        self.assertEqual(check['estimated_input_peak_cost_cny'],'0.034906')
        self.assertEqual(check['max_retries'],0)
        self.assertEqual(check['provider_new_schema_acceptance'],'unverified')
        self.assertFalse(check['spending_authorized']); self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_all_gold_fields_are_excluded_before_preparation(self):
        cases=deepcopy(self.data['cases'])
        for case in cases:
            for field in set(case)-{'id','question','quote','review_text'}: case[field]='DO-NOT-SEND-GOLD'
        original=entry.b1.model_payload(self.request)
        self.assertEqual(original,entry.b1.model_payload(entry.prepare_cases(cases)[2]))
        for c,case in zip(self.request.candidates,self.data['cases']):
            self.assertEqual(c.review_text,case['review_text'])
            self.assertEqual(c.review_text[c.quote_start:c.quote_end],case['quote'])
            self.assertEqual(c.review_text_sha256,sha256(case['review_text'].encode()).hexdigest())
        for name in ('shadow_quality_cases_v1.json','shadow_single_four_state_v1.json','shadow_v3_independent_cases_v1.json'):
            old=strict_json_loads((entry.FIXTURE.parent/name).read_text(encoding='utf-8'))
            for field in ('quote','review_text','id'):
                self.assertFalse({c[field] for c in old['cases']} & {c[field] for c in cases})

    def test_missing_authorization_never_constructs_adapter(self):
        with patch.object(entry.port,'TextEvidenceShadow') as adapter:
            for send,fee in ((False,False),(True,False),(False,True)):
                with self.assertRaises(ValueError):
                    entry.run_authorized(self.env,send_authorized=send,estimated_budget_risk_accepted=fee)
            adapter.assert_not_called()
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_mock_sdk_new_schema_and_exact_full_source_four_states(self):
        summary=self.mock_run()
        self.assertEqual(summary['status'],'completed',summary)
        self.assertTrue(summary['structure_valid'])
        self.assertEqual(summary['label_match_count'],5)
        self.assertFalse((entry.OUTPUT_DIR/'diagnostics').exists())
        body=strict_json_loads(self.capture.read_text(encoding='utf-8'))
        self.assertEqual(body,port.request_body(entry.b1.model_payload(self.request)))
        schema=body['response_format']['json_schema']['schema']
        self.assertEqual(schema,port.strict_output_schema())
        self.assertNotEqual(schema,port.provider.request_body('{}')['response_format']['json_schema']['schema'])
        self.assertEqual(set(schema['$defs']['TextReference']['properties']),{'key','source_identity_sha256','text'})
        judgments=strict_json_loads((entry.OUTPUT_DIR/'shadow-judgments.json').read_text(encoding='utf-8'))
        for row,candidate in zip(judgments['fact_judgments'],self.request.candidates):
            for name in ('support_refs','refutation_refs','context_veto_refs'):
                for ref in row[name]:
                    self.assertEqual(candidate.review_text[ref['start']:ref['end']],ref['text'])
                    self.assertEqual(ref['end'],ref['start']+len(ref['text']))
        self.assertEqual(judgments['fact_judgments'][0]['support_refs'][0]['start'],10)
        self.assertNotEqual(10,len(self.request.candidates[0].review_text[:10].encode('utf-16-le'))//2)
        self.assertFalse(port.provider.SYSTEM_PROMPT==port.SYSTEM_PROMPT)
        self.assertEqual(port.provider.PROMPT_VERSION,'p5-shadow-siliconflow-instructions-v2')

    def test_illegal_coordinates_are_schema_failure_with_private_raw(self):
        self.reply['fact_judgments'][0]['support_refs'][0].update(start=0,end=999)
        summary=self.mock_run()
        self.assertFalse(summary['structure_valid'])
        self.assertEqual(summary['validated_label_count'],0)
        diagnostic=self.diagnostic()
        self.assertEqual(diagnostic['failure_stage'],'text_schema')
        self.assertEqual(diagnostic['failure_details'][0]['candidate_key'],self.request.candidates[0].key)
        self.assertEqual(diagnostic['failure_details'][0]['reference_type'],'support_refs')
        self.assertIn('start',diagnostic['raw_model_text'])
        self.assertEqual(diagnostic['evidence_status'],'unvalidated_diagnostic_only')

    def test_missing_text_is_located_and_no_partial_judgments_escape(self):
        self.reply['fact_judgments'][3]['context_veto_refs'][0]['text']='不存在的撤回句。'
        summary=self.mock_run()
        self.assertFalse(summary['structure_valid'])
        diagnostic=self.diagnostic()
        self.assertEqual(diagnostic['failure_stage'],'text_location')
        self.assertEqual(diagnostic['failure_details'][0]['candidate_key'],self.request.candidates[3].key)
        self.assertEqual(diagnostic['failure_details'][0]['reference_type'],'context_veto_refs')
        self.assertFalse((entry.OUTPUT_DIR/'shadow-judgments.json').exists())

    def test_source_identity_failure_has_distinct_stage(self):
        self.reply['fact_judgments'][0]['support_refs'][0]['source_identity_sha256']='0'*64
        self.mock_run()
        self.assertEqual(self.diagnostic()['failure_stage'],'source_binding')

    def test_input_and_matrix_binding_fail_before_location(self):
        for variant in ('input','matrix'):
            request=self.request
            reply=deepcopy(self.reply)
            if variant=='input': reply['input_sha256']='0'*64
            else: reply['fact_judgments'][0]['fact_id']='invented'
            run=entry.text.resolve_output(request,canonical_json(reply))
            self.assertEqual(run.failure_stage,'input_binding' if variant=='input' else 'matrix_binding')
            self.assertIsNone(run.resolved_output)

    def test_correct_labels_missing_basis_fail_original_b2(self):
        self.reply['fact_judgments'][2]['refutation_refs']=[]
        self.mock_run()
        self.assertEqual(self.diagnostic()['failure_stage'],'b2_audit')
        self.assertEqual(self.diagnostic()['error_codes'],['refutation_without_basis'])

    def test_legal_locations_wrong_labels_saved_as_semantic_failure(self):
        self.reply['fact_judgments'][2].update(status='not_supported',refutation_refs=[],reason_code='no_direct_support')
        summary=self.mock_run()
        self.assertTrue(summary['structure_valid'])
        self.assertEqual(summary['stop_reason'],'fixed_label_mismatch')
        self.assertEqual(summary['label_match_count'],4)
        self.assertEqual(self.diagnostic()['failure_stage'],'semantic_labels')
        self.assertEqual(entry.load_fixed(),self.data)

    def test_correct_label_but_semantically_insufficient_unique_ref_is_not_verified(self):
        self.reply['fact_judgments'][0]['support_refs'][0]['text']='日历'
        summary=self.mock_run()
        self.assertTrue(summary['structure_valid'])
        self.assertEqual(summary['label_match_count'],5)
        self.assertEqual(summary['semantic_basis_review'],'pending_independent_reading')
        self.assertFalse(summary['semantic_verified'])

    def test_sdk_failure_retains_metadata_with_no_raw_and_no_retry(self):
        summary=self.mock_run(mode='sdk_error')
        self.assertFalse(summary['structure_valid'])
        self.assertEqual(self.diagnostic()['failure_stage'],'provider')
        self.assertIsNone(self.diagnostic()['raw_model_text'])

    def test_invalid_json_preserved_as_text_schema_failure_not_api_failure(self):
        factory,calls=self.make_factory()
        def invalid_factory(**kwargs):
            adapter=factory(**kwargs)
            config=strict_json_loads(adapter.judge_factory.config_json)
            config['reply']='{'
            adapter.judge_factory=JudgeFactory(entrypoint=adapter.judge_factory.entrypoint,config_json=canonical_json(config))
            return adapter
        with patch.object(entry.port,'TextEvidenceShadow',side_effect=invalid_factory):
            summary=entry.run_authorized(self.env,send_authorized=True,estimated_budget_risk_accepted=True)
        self.assertFalse(summary['structure_valid'])
        self.assertEqual(summary['failure_stage'],'text_schema')
        self.assertEqual(self.diagnostic()['raw_model_text'],'{')
        self.assertEqual(len(calls),1)

    def test_diagnostic_disk_failure_does_not_turn_invalid_output_valid(self):
        self.reply['fact_judgments'][0]['support_refs'][0]['text']='does not exist'
        with patch.object(port.privacy,'_atomic_write',side_effect=OSError('SYNTHETIC disk failure')):
            summary=self.mock_run(mode='write_failure')
        self.assertFalse(summary['structure_valid'])
        self.assertFalse(summary['failure_diagnostic_saved'])

    def test_drift_and_truncation_rejected_with_zero_retries(self):
        for mode in ('drift','length'):
            with self.subTest(mode=mode),TemporaryDirectory(dir=self.directory) as directory:
                self.capture=Path(directory)/'http.jsonl'
                with patch.object(entry,'OUTPUT_DIR',Path(directory)/'run'):
                    summary=self.mock_run(mode=mode)
                    self.assertFalse(summary['structure_valid'])

    def test_timeout_and_cancellation_reap_actual_mock_worker(self):
        for cancel in (False,True):
            with self.subTest(cancel=cancel),TemporaryDirectory(dir=self.directory) as directory:
                directory=Path(directory)
                capture=directory/'http.jsonl'
                config=port.provider.scoped_config(self.env).model_copy(update={'estimated_budget_risk_accepted':True,
                    'usage_file':str(directory/'usage.json')})
                factory=JudgeFactory(entrypoint='fixtures.shadow_text_evidence_http_mock:create_mock_judge',
                    config_json=canonical_json(config.model_dump(mode='json') | dict(reply=canonical_json(self.reply),
                        capture_file=str(capture),mode='hang')))
                event=Event()
                def cancel_after_request():
                    limit=monotonic()+4
                    while not capture.exists() and monotonic()<limit: sleep(.02)
                    event.set()
                thread=Thread(target=cancel_after_request) if cancel else None
                if thread: thread.start()
                adapter=port.TextEvidenceShadow(settings=ShadowSettings(enabled=True,timeout_seconds=5 if cancel else 2),
                    judge_factory=factory,development_diagnostic_dir=str(directory/'diagnostics'))
                run=adapter.run(self.contract,self.result,trusted_contract_sha256=content_sha256(self.contract.model_dump(mode='json')),
                    request_id=self.request.request_id,cancel_event=event)
                if thread: thread.join(timeout=5)
                self.assertTrue(capture.exists())
                self.assertEqual(run.outcome.execution_state,'cancelled' if cancel else 'model_failed')
                self.assertTrue(run.diagnostics['reaped']); self.assertTrue(run.diagnostics['threads_released'])
                self.assertTrue(run.diagnostics['failure_diagnostic_saved'])
                self.assertEqual(run.diagnostics['failure_stage'],'cancelled' if cancel else 'timeout')
                self.assertEqual(len(capture.read_text(encoding='utf-8').splitlines()),1)

    def test_same_text_in_distinct_reviews_is_bound_per_candidate(self):
        cases=deepcopy(self.data['cases'][:2])
        for case in cases: case.update(quote='客服挖苦我。',review_text='独立新记录：客服挖苦我。')
        contract,result,request=entry.prepare_cases(cases)
        reply=text_reply(request,['supported','supported'])
        run=entry.text.evaluate(contract,result,canonical_json(reply),
            trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=request.request_id)
        self.assertEqual(run.outcome.execution_state,'validated_shadow')
        self.assertNotEqual(request.candidates[0].source_identity_sha256,request.candidates[1].source_identity_sha256)

    def test_repeated_overlap_and_hash_drift_rejected(self):
        cases=deepcopy(self.data['cases'][:1])
        cases[0].update(review_text='测试：哈哈哈',quote='哈哈哈')
        contract,result,request=entry.prepare_cases(cases)
        reply=text_reply(request)
        reply['fact_judgments'][0]['support_refs'][0]['text']='哈哈'
        run=entry.text.evaluate(contract,result,canonical_json(reply),
            trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=request.request_id)
        self.assertEqual(run.outcome.reason_codes,('text_reference_ambiguous',))
        row=result.evidence[0]
        result=replace(result,evidence=(replace(row,review_text_sha256='0'*64),))
        run=entry.text.evaluate(contract,result,canonical_json(reply),
            trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=request.request_id)
        self.assertEqual(run.outcome.execution_state,'input_invalid')

    def test_ambiguity_diagnostic_and_redaction_use_existing_privacy_rules(self):
        cases=deepcopy(self.data['cases'][:1]); cases[0].update(review_text='测试：哈哈哈',quote='哈哈哈')
        request=entry.prepare_cases(cases)[2]
        reply=text_reply(request); reply['fact_judgments'][0]['support_refs'][0]['text']='哈哈'
        reply['fact_judgments'][0]['rationale']='SYNTHETIC-TEXT-KEY authorization: bearer hidden'
        capture=port.TextFailureCapture(str(self.directory/'diag'),request.model_dump(mode='json'),65536)
        capture.capture(canonical_json(reply),secrets=('SYNTHETIC-TEXT-KEY',)); capture.finish()
        saved=strict_json_loads((self.directory/'diag/failure.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['error_codes'],['text_reference_ambiguous'])
        self.assertNotIn('SYNTHETIC-TEXT-KEY',saved['raw_model_text'])
        self.assertTrue(saved['redaction_applied'])

    def test_unknown_reference_key_cannot_leak_secret_into_diagnostic_metadata(self):
        self.reply['fact_judgments'][0]['support_refs'][0]['key']='SYNTHETIC-TEXT-KEY'
        capture=port.TextFailureCapture(str(self.directory/'diag'),self.request.model_dump(mode='json'),65536)
        capture.capture(canonical_json(self.reply),secrets=('SYNTHETIC-TEXT-KEY',)); capture.finish()
        saved=(self.directory/'diag/failure.json').read_text(encoding='utf-8')
        self.assertNotIn('SYNTHETIC-TEXT-KEY',saved)
        self.assertIsNone(strict_json_loads(saved)['failure_details'][0]['candidate_key'])

    def test_fixed_protocol_or_fixture_drift_blocks_preflight(self):
        for field in ('FIXTURE_SHA256','PROMPT_SHA256','SCHEMA_SHA256','STRICT_SCHEMA_SHA256','INPUT_SHA256','BODY_SHA256'):
            with patch.object(entry,field,'0'*64),self.assertRaises(ValueError): entry.preflight(self.env)
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_usage_budget_and_new_schema_binding_are_not_bypassed(self):
        usage=dict(request_id=self.request.request_id,input_sha256=entry.INPUT_SHA256,provider='siliconflow',
            base_url=port.provider.BASE_URL,model=port.provider.MODEL,response_model=port.provider.MODEL,
            prompt_version=port.PROMPT_VERSION,system_prompt_sha256=entry.PROMPT_SHA256,
            output_schema_sha256=entry.STRICT_SCHEMA_SHA256,state='response_received',model_calls=1,usage_known=True,
            reasoning_tokens=0,reasoning_content_present=False,finish_reason='stop',input_tokens=7453,output_tokens=2500,
            cost_cny_at_peak_rates='0.034906')
        self.assertIsNone(entry.usage_problem(usage,self.request))
        for field,value in (('cost_cny_at_peak_rates','NaN'),('cost_cny_at_peak_rates','0.04'),
                            ('input_tokens',8001),('output_tokens',2501),('model_calls',2),('model_calls',True),
                            ('output_schema_sha256','0'*64),('input_sha256','0'*64)):
            self.assertIsNotNone(entry.usage_problem(usage | {field:value},self.request))

    def test_malformed_schema_key_is_closed_without_diagnostic_exception(self):
        self.reply['fact_judgments'][0]['key']=[]
        run=entry.text.resolve_output(self.request,canonical_json(self.reply))
        self.assertEqual(run.failure_stage,'text_schema')
        self.assertIsNone(run.resolved_output)

    def test_conversion_never_succeeds_without_original_audit(self):
        rejected=entry.b1.ShadowOutcome('output_invalid',('forced_audit_rejection',))
        with patch.object(entry.b1,'audit_output',return_value=rejected) as audit:
            run=entry.text.resolve_output(self.request,canonical_json(self.reply))
        audit.assert_called_once()
        self.assertIsNone(run.resolved_output)
        self.assertEqual(run.outcome,rejected)
        self.assertEqual(run.failure_stage,'b2_audit')


if __name__=='__main__': unittest.main()
