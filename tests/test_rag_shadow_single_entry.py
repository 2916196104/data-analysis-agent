"""Single-request preparation: real B2/SDK, Mock HTTP only, no paid calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_rag_shadow_single as entry
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from test_shadow_quality_plan import authored_output


class SingleEntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory(prefix='single-prep-test-', dir=entry.diagnostics.PRIVATE_ROOT))).resolve()
        self.enterContext(patch.object(entry, 'OUTPUT_DIR', self.directory/'run'))
        self.env = self.directory/'synthetic.env'
        self.env.write_text(f'SHADOW_MODEL_ID={entry.provider.MODEL}\nSHADOW_BASE_URL={entry.provider.BASE_URL}\n'
                            'SHADOW_API_KEY=SYNTHETIC-INDEPENDENT-KEY\n', encoding='utf-8')
        for target in ('psycopg.connect','socket.create_connection','httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))
        self.data = entry.load_fixed()
        self.request = entry.prepare_batch(self.data['cases'])[2]

    def test_new_fixed_cases_and_gold_do_not_enter_payload(self):
        self.assertEqual([c['id'] for c in self.data['cases']], ['D71','D72','D73','D74','D75'])
        old = strict_json_loads((entry.ROOT/'tests/fixtures/shadow_quality_cases_v1.json').read_text(encoding='utf-8'))
        self.assertFalse({c['review_text'] for c in old['cases']} & {c['review_text'] for c in self.data['cases']})
        self.assertFalse({c['quote'] for c in old['cases']} & {c['quote'] for c in self.data['cases']})
        poisoned = deepcopy(self.data['cases'])
        for case in poisoned:
            for field in ('target_fact','expected_status','expected_reason','veto_text'):
                case[field] = 'DO-NOT-SEND-GOLD'
        self.assertEqual(entry.protocol.model_payload(self.request),
                         entry.protocol.model_payload(entry.prepare_batch(poisoned)[2]))
        self.assertTrue(all(c.review_id >= 71 for c in self.request.candidates))
        self.assertEqual([c.rank for c in self.request.candidates], [1,2,3,4,5])

    def test_preflight_exact_budget_private_path_and_all_four_states(self):
        with patch.object(entry.provider, 'OpenAI', side_effect=AssertionError('no SDK client in preflight')):
            check = entry.preflight(self.env)
        self.assertEqual(check['status'], 'ready_pending_explicit_single_authorization')
        self.assertEqual(check['input_estimate'], 7816)
        self.assertEqual(check['max_model_calls'], 1)
        self.assertEqual(check['max_retries'], 0)
        self.assertTrue(check['diagnostic_path_git_ignored'])
        self.assertFalse(check['spending_authorized'])
        self.assertEqual(check['development_diagnostic_dir'], str(entry.OUTPUT_DIR/'diagnostics'))
        self.assertFalse(entry.OUTPUT_DIR.exists())
        reply = authored_output(self.request, self.data['cases'])
        outcome = entry.protocol.audit_output(self.request, canonical_json(reply))
        self.assertEqual(outcome.execution_state, 'validated_shadow')
        self.assertFalse(outcome.publication_allowed)
        self.assertEqual([r['status'] for r in reply['fact_judgments']],
                         ['supported','not_supported','refuted','refuted','unknown'])
        for candidate, case in zip(self.request.candidates, self.data['cases']):
            self.assertEqual(candidate.review_text, case['review_text'])
            self.assertEqual(candidate.review_text[candidate.quote_start:candidate.quote_end], case['quote'])

    def test_missing_send_or_fee_authorization_has_no_side_effects(self):
        with patch.object(entry, 'StructuredEvidenceShadow') as adapter:
            for send, fee in ((False,False),(True,False),(False,True)):
                with self.assertRaises(ValueError):
                    entry.run_authorized(self.env, send_authorized=send, estimated_budget_risk_accepted=fee)
            adapter.assert_not_called()
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def mock_factory(self, reply, mode='valid'):
        calls = []
        def factory(*, settings, judge_factory, development_diagnostic_dir):
            calls.append(1)
            self.assertEqual(judge_factory.entrypoint, entry.FACTORY)
            self.assertEqual(settings.timeout_seconds, 30)
            self.assertEqual(development_diagnostic_dir, str(entry.OUTPUT_DIR/'diagnostics'))
            config = strict_json_loads(judge_factory.config_json)
            for field in ('expected_status','expected_reason','veto_text','target_fact'):
                self.assertNotIn(field, config)
            return StructuredEvidenceShadow(settings=settings, development_diagnostic_dir=development_diagnostic_dir,
                judge_factory=JudgeFactory(entrypoint='fixtures.shadow_diagnostic_http_mock:create_mock_judge',
                    config_json=canonical_json(config | dict(reply=reply, mode=mode))))
        return factory, calls

    def test_single_valid_mock_call_then_second_attempt_refused(self):
        reply = canonical_json(authored_output(self.request, self.data['cases']))
        factory, calls = self.mock_factory(reply)
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(summary['status'], 'completed', summary)
            self.assertTrue(summary['structure_valid'])
            self.assertEqual(summary['model_calls'], 1)
            self.assertEqual(sum(c['status_match'] for c in summary['comparison']['candidates']), 5)
            self.assertTrue(all(c['semantic_basis_review']=='pending_independent_reading' for c in summary['comparison']['candidates']))
            self.assertFalse(summary['publication_allowed']); self.assertFalse(summary['semantic_verified'])
            self.assertFalse((entry.OUTPUT_DIR/'diagnostics').exists())
            before = (entry.OUTPUT_DIR/'execution-summary.json').read_bytes()
            with self.assertRaises(ValueError):
                entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(before, (entry.OUTPUT_DIR/'execution-summary.json').read_bytes())

    def test_invalid_reference_one_call_diagnostic_and_no_valid_license(self):
        output = authored_output(self.request, self.data['cases'])
        output['fact_judgments'][0]['support_refs'][0]['end'] += 1
        factory, calls = self.mock_factory(canonical_json(output))
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(len(calls), 1)
            self.assertEqual(summary['status'], 'stopped')
            self.assertEqual(summary['reason_codes'], ['citation_span_mismatch'])
            self.assertFalse(summary['structure_valid']); self.assertFalse(summary['publication_allowed'])
            self.assertTrue(summary['failure_diagnostic_saved'])
            self.assertTrue(all(c['model_status'] is None for c in summary['comparison']['candidates']))
            self.assertFalse((entry.OUTPUT_DIR/'shadow-judgments.json').exists())
            saved = strict_json_loads((entry.OUTPUT_DIR/'diagnostics/failure.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['failure_details'][0]['candidate_key'], self.request.candidates[0].key)
            self.assertEqual(saved['failure_details'][0]['reference_type'], 'support_refs')
            with self.assertRaises(ValueError):
                entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(len(calls), 1)

    def test_sdk_failure_claim_stays_occupied_without_retry(self):
        factory, calls = self.mock_factory(None, mode='sdk_error')
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(summary['status'], 'stopped')
            self.assertEqual(summary['model_calls'], 1)
            self.assertFalse(summary['failure_diagnostic_saved'])
            self.assertFalse(summary['structure_valid'])
            with self.assertRaises(ValueError):
                entry.run_authorized(self.env, send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(len(calls), 1)

    def test_fixed_fixture_drift_rejected(self):
        changed = self.directory/'changed.json'
        changed.write_bytes(entry.FIXTURE.read_bytes()+b' ')
        with patch.object(entry, 'FIXTURE', changed), self.assertRaises(ValueError):
            entry.preflight(self.env)


if __name__ == '__main__': unittest.main()
