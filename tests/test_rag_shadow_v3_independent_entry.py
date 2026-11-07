"""Preparation only: fixed gold, unchanged B1/B2/v2, real SDK over Mock HTTP."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_rag_shadow_v3_independent as entry
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from test_shadow_quality_plan import authored_output
from test_review_structured_shadow import request_for
import test_p5b1_structured_evidence_design as b1


class IndependentV3Tests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory(prefix='v3-prep-test-', dir=entry.diagnostics.PRIVATE_ROOT))).resolve()
        self.enterContext(patch.object(entry, 'OUTPUT_DIR', self.directory/'run'))
        self.env = self.directory/'synthetic.env'
        self.env.write_text(f'SHADOW_MODEL_ID={entry.provider.MODEL}\nSHADOW_BASE_URL={entry.provider.BASE_URL}\n'
                            'SHADOW_API_KEY=SYNTHETIC-V3-ONLY\n', encoding='utf-8')
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))
        self.data = entry.load_fixed()
        self.request = entry.prepare_batch(self.data['cases'])[2]
        self.output = authored_output(self.request, self.data['cases'])

    def assert_closed(self, outcome):
        self.assertFalse(outcome.publication_allowed)
        self.assertFalse(outcome.semantic_verified)
        self.assertFalse(outcome.permitted_target_fact_ids)

    def test_fresh_cases_and_all_expectations_are_local_only(self):
        self.assertEqual([c['id'] for c in self.data['cases']], ['E81','E82','E83','E84','E85'])
        for previous in ('shadow_single_four_state_v1.json', 'shadow_quality_cases_v1.json'):
            old = strict_json_loads((entry.FIXTURE.parent/previous).read_text(encoding='utf-8'))
            for field in ('id','quote','review_text'):
                self.assertFalse({c[field] for c in old['cases']} & {c[field] for c in self.data['cases']})
        poisoned = deepcopy(self.data['cases'])
        for case in poisoned:
            for field in set(case)-{'id','question','quote','review_text'}:
                case[field] = 'DO-NOT-SEND-FROZEN-EXPECTATION'
        payload = entry.protocol.model_payload(self.request)
        self.assertEqual(payload, entry.protocol.model_payload(entry.prepare_batch(poisoned)[2]))
        for field in ('provenance','reference_policy'):
            self.assertNotIn(self.data[field], payload)
        for case, candidate in zip(self.data['cases'], self.request.candidates):
            self.assertEqual(candidate.review_text, case['review_text'])
            self.assertEqual(candidate.review_text[candidate.quote_start:candidate.quote_end], case['quote'])
            self.assertEqual(candidate.review_text_sha256, sha256(case['review_text'].encode()).hexdigest())
            self.assertEqual(candidate.product_id, 'SYNTHETIC-P1')
            self.assertTrue(candidate.source_identity_sha256)

    def test_frozen_reference_standards_pass_unmodified_b1_b2(self):
        self.assertEqual([r['status'] for r in self.output['fact_judgments']],
                         ['supported','not_supported','refuted','refuted','unknown'])
        expected = {0: ('support_refs',10,29), 2: ('refutation_refs',12,34), 3: ('context_veto_refs',19,48)}
        for index, (field, start, end) in expected.items():
            span = self.output['fact_judgments'][index][field][0]
            candidate = self.request.candidates[index]
            self.assertEqual((span['start'],span['end']), (start,end))
            self.assertEqual(span['text'], candidate.review_text[start:end])
            self.assertEqual(span['end'], span['start']+len(span['text']))
            self.assertEqual(span['key'], candidate.key)
            self.assertEqual(span['source_identity_sha256'], candidate.source_identity_sha256)
        for index in (1,4):
            self.assertTrue(all(not self.output['fact_judgments'][index][field]
                                for field in ('support_refs','refutation_refs','context_veto_refs')))
        outcome = entry.protocol.audit_output(self.request, canonical_json(self.output))
        self.assertEqual(outcome.execution_state, 'validated_shadow')
        self.assert_closed(outcome)

    def test_preflight_freezes_actual_v3_body_and_keeps_v2_unchanged(self):
        before = (entry.provider.PROMPT_VERSION, entry.provider.SYSTEM_PROMPT)
        with patch.object(entry.provider, 'OpenAI', side_effect=AssertionError('preflight must not create SDK client')):
            check = entry.preflight(self.env)
        self.assertEqual(check['status'], 'ready_pending_explicit_v3_authorization')
        self.assertEqual(check['missing'], [])
        self.assertEqual(check['input_estimate'], 7859)
        self.assertLessEqual(check['input_estimate'], 8000)
        self.assertEqual(check['output_limit'], 2500)
        self.assertEqual(check['max_model_calls'], 1)
        self.assertEqual(check['max_retries'], 0)
        self.assertEqual(check['conditional_peak_cost_cny'], '0.036')
        self.assertFalse(check['spending_authorized'])
        self.assertTrue(check['diagnostic_path_git_ignored'])
        self.assertEqual(check['development_diagnostic_dir'], str(entry.OUTPUT_DIR/'diagnostics'))
        self.assertFalse(entry.OUTPUT_DIR.exists())
        body = entry.request_body(entry.protocol.model_payload(self.request))
        baseline = entry.provider.request_body(entry.protocol.model_payload(self.request))
        self.assertEqual(content_sha256(body), entry.BODY_SHA256)
        self.assertEqual(body['messages'][0]['content'], entry.fixed_prompt())
        body['messages'][0] = baseline['messages'][0]
        self.assertEqual(body, baseline)
        self.assertEqual((entry.provider.PROMPT_VERSION,entry.provider.SYSTEM_PROMPT), before)
        self.assertEqual(before[0], 'p5-shadow-siliconflow-instructions-v2')
        self.assertEqual(sha256(before[1].encode()).hexdigest(), entry.EXPECTED_PROMPT)

    def test_missing_authorization_is_side_effect_free(self):
        with patch.object(entry, 'StructuredEvidenceShadow') as adapter:
            for send, fee in ((False,False),(True,False),(False,True)):
                with self.assertRaises(ValueError):
                    entry.run_authorized(self.env, send_authorized=send, estimated_budget_risk_accepted=fee)
            adapter.assert_not_called()
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def mock_factory(self, output, mode='valid'):
        calls = []
        self.capture = self.directory/'mock-http.jsonl'
        def factory(*, settings, judge_factory, development_diagnostic_dir):
            calls.append(1)
            self.assertEqual(judge_factory.entrypoint, entry.FACTORY)
            self.assertEqual(settings.timeout_seconds, 30)
            self.assertEqual(development_diagnostic_dir, str(entry.OUTPUT_DIR/'diagnostics'))
            config = strict_json_loads(judge_factory.config_json)
            self.assertEqual(set(config), {'model','base_url','env_file','env_sha256','usage_file','estimated_budget_risk_accepted'})
            return StructuredEvidenceShadow(settings=settings, development_diagnostic_dir=development_diagnostic_dir,
                judge_factory=JudgeFactory(entrypoint='fixtures.shadow_v3_http_mock:create_mock_judge',
                    config_json=canonical_json(config | dict(reply=canonical_json(output), mode=mode, capture_file=str(self.capture)))))
        return factory, calls

    def run_mock(self, output, mode='valid'):
        factory, calls = self.mock_factory(output, mode)
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            before = (entry.OUTPUT_DIR/'execution-summary.json').read_bytes()
            with self.assertRaises(ValueError):
                entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(before, (entry.OUTPUT_DIR/'execution-summary.json').read_bytes())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.capture.read_text(encoding='utf-8').splitlines()), 1)
        self.assertEqual(summary['model_calls'], 1)
        self.assertFalse(summary['publication_allowed'])
        self.assertFalse(summary['semantic_verified'])
        self.assertIsNone(summary['formal_accuracy'])
        return summary

    def test_single_mock_sdk_call_records_v3_prompt_and_preserves_full_input(self):
        summary = self.run_mock(self.output)
        self.assertEqual(summary['status'], 'completed', summary)
        self.assertTrue(summary['structure_valid'])
        self.assertEqual(summary['validated_label_count'], 5)
        self.assertEqual(summary['label_match_count'], 5)
        self.assertEqual(summary['usage']['prompt_version'], entry.PROMPT_VERSION)
        self.assertEqual(summary['usage']['system_prompt_sha256'], entry.PROMPT_SHA256)
        self.assertEqual(summary['usage']['preflight_input_token_estimate'], 7859)
        self.assertFalse((entry.OUTPUT_DIR/'diagnostics').exists())
        actual = strict_json_loads(self.capture.read_text(encoding='utf-8'))
        self.assertEqual(actual, entry.request_body(entry.protocol.model_payload(self.request)))
        saved = strict_json_loads((entry.OUTPUT_DIR/'frozen-expectations.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, self.data)
        self.assertTrue(all(c['semantic_basis_review']=='pending_independent_reading' for c in summary['comparison']['candidates']))

    def test_invalid_veto_end_stays_invalid_retains_diagnostic_and_never_scores(self):
        self.output['fact_judgments'][3]['context_veto_refs'][0]['end'] += 7
        summary = self.run_mock(self.output)
        self.assertEqual(summary['status'], 'stopped')
        self.assertFalse(summary['structure_valid'])
        self.assertEqual(summary['reason_codes'], ['citation_span_mismatch'])
        self.assertEqual(summary['validated_label_count'], 0)
        self.assertIsNone(summary['label_match_count'])
        self.assertTrue(all(c['model_status'] is None for c in summary['comparison']['candidates']))
        self.assertEqual(sum(sum(row.values()) for row in summary['comparison']['confusion_matrix'].values()), 0)
        self.assertFalse((entry.OUTPUT_DIR/'shadow-judgments.json').exists())
        self.assertTrue(summary['failure_diagnostic_saved'])
        saved = strict_json_loads((entry.OUTPUT_DIR/'diagnostics/failure.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['evidence_status'], 'unvalidated_diagnostic_only')
        self.assertEqual(saved['raw_model_text'], canonical_json(self.output))
        detail = saved['failure_details'][0]
        self.assertEqual((detail['reference_type'],detail['start'],detail['end']), ('context_veto_refs',19,55))
        self.assertFalse(detail['bounds_valid'])
        self.assertTrue(detail['text_matches'])
        self.assertEqual(detail['coordinate_unit'], 'unicode_code_point')

    def test_semantic_label_mismatch_stops_without_changing_gold_or_retry(self):
        self.output['fact_judgments'][2].update(status='not_supported', refutation_refs=[], reason_code='no_direct_support')
        summary = self.run_mock(self.output)
        self.assertTrue(summary['structure_valid'])
        self.assertEqual(summary['status'], 'stopped')
        self.assertEqual(summary['stop_reason'], 'fixed_label_mismatch')
        self.assertEqual(summary['label_match_count'], 4)
        self.assertEqual(summary['comparison']['candidates'][2]['expected_status'], 'refuted')
        self.assertEqual(entry.load_fixed(), self.data)

    def test_sdk_error_consumes_only_attempt_without_retry(self):
        summary = self.run_mock(self.output, mode='sdk_error')
        self.assertEqual(summary['status'], 'stopped')
        self.assertFalse(summary['structure_valid'])
        self.assertFalse(summary['failure_diagnostic_saved'])

    def test_illegal_references_and_missing_refutation_keep_failing_closed(self):
        for variant in ('identity','quote_relative','changed_text','full_review_support','outside_quote_refutation','missing_basis'):
            with self.subTest(variant=variant):
                output = deepcopy(self.output)
                span = output['fact_judgments'][0]['support_refs'][0]
                if variant == 'identity': span['source_identity_sha256'] = '0'*64
                if variant == 'quote_relative': span.update(start=0,end=len(span['text']))
                if variant == 'changed_text': span['text'] += 'x'
                if variant == 'full_review_support':
                    output['fact_judgments'][0]['support_refs'] = [b1.span(self.request.candidates[0], self.request.candidates[0].review_text)]
                if variant == 'outside_quote_refutation':
                    row = output['fact_judgments'][3]
                    row['refutation_refs'], row['context_veto_refs'] = row['context_veto_refs'], []
                if variant == 'missing_basis': output['fact_judgments'][2]['refutation_refs'] = []
                outcome = entry.protocol.audit_output(self.request, canonical_json(output))
                self.assertEqual(outcome.execution_state, 'output_invalid')
                self.assert_closed(outcome)

    def test_unicode_code_points_are_not_utf16_units(self):
        request = request_for(rows=[('🙂开机不凉。','开机不凉。',901)])
        output = b1.response(request)
        ref = output['fact_judgments'][0]['support_refs'][0]
        self.assertEqual((ref['start'],ref['end']), (1,6))
        self.assertEqual(entry.protocol.audit_output(request,canonical_json(output)).execution_state, 'validated_shadow')
        ref['start'] += 1
        ref['end'] += 1
        outcome = entry.protocol.audit_output(request,canonical_json(output))
        self.assertEqual(outcome.execution_state, 'output_invalid')
        self.assert_closed(outcome)

    def test_frozen_prompt_fixture_request_and_budget_drift_are_rejected(self):
        for field, path in (('FIXTURE',entry.FIXTURE), ('PROMPT',entry.PROMPT)):
            changed = self.directory/path.name
            changed.write_bytes(path.read_bytes()+b'changed')
            with patch.object(entry,field,changed), self.assertRaises(ValueError):
                entry.preflight(self.env)
        for field, value in (('INPUT_ESTIMATE',8001),('BODY_SHA256','0'*64),('INPUT_SHA256','0'*64)):
            with patch.object(entry,field,value), self.assertRaises(ValueError):
                entry.preflight(self.env)
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def test_usage_with_v2_identity_cannot_be_accepted_as_v3(self):
        from scripts.run_rag_shadow_boundaries import _usage_problem
        summary = self.run_mock(self.output)
        usage = deepcopy(summary['usage'])
        rid, digest = usage['request_id'], usage['input_sha256']
        self.assertIsNone(entry.usage_problem(usage,rid,digest))
        self.assertIsNotNone(_usage_problem(usage,rid,digest))
        for field, value in (('prompt_version',entry.provider.PROMPT_VERSION),
                             ('system_prompt_sha256',entry.EXPECTED_PROMPT),('model_calls',2),
                             ('input_sha256','0'*64),('cost_cny_at_peak_rates','NaN')):
            wrong = usage | {field:value}
            self.assertIsNotNone(entry.usage_problem(wrong,rid,digest))


if __name__ == '__main__':
    unittest.main()
