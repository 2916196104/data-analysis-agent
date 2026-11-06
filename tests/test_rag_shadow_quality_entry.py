"""Offline entry checks: real B2 subprocesses, synthetic provider only."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_rag_shadow_quality as entry
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from test_shadow_quality_plan import prepare_cases, authored_output


class QualityEntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(entry, 'OUTPUT_DIR', self.directory/'run'))
        self.enterContext(patch.object(entry.provider, 'preflight', return_value={'missing': []}))
        config = entry.provider.SiliconFlowShadowConfig(model=entry.provider.MODEL,
            base_url=entry.provider.BASE_URL, env_file='SYNTHETIC-NOT-READ', env_sha256='0'*64)
        self.enterContext(patch.object(entry.provider, 'scoped_config', return_value=config))
        for target in ('psycopg.connect', 'socket.create_connection', 'httpx.HTTPTransport.handle_request',
                       'httpx.AsyncHTTPTransport.handle_async_request', 'openai.OpenAI',
                       'data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            self.enterContext(patch(target, side_effect=AssertionError('real I/O forbidden')))

    def test_exact_planned_requests_and_no_answer_leak(self):
        check = entry.preflight('unused')
        self.assertEqual([b['input_estimate'] for b in check['batches']], list(entry.ESTIMATES))
        self.assertEqual(check['status'], 'ready_pending_explicit_authorization')
        self.assertFalse(check['spending_authorized'])
        self.assertFalse(entry.OUTPUT_DIR.exists())
        for _, cases, (_, _, request) in entry.prepared_batches(entry.load_fixed()):
            self.assertEqual(request, prepare_cases(cases))
            poisoned = deepcopy(cases)
            for c in poisoned:
                for key in ('expected_status', 'expected_reason', 'veto_text', 'target_fact'):
                    c[key] = 'DO-NOT-SEND-ANSWER'
            other = entry.prepare_batch(poisoned)[2]
            self.assertEqual(entry.protocol.model_payload(request), entry.protocol.model_payload(other))

    def test_no_authorization_cannot_claim_directory_or_create_adapter(self):
        with patch.object(entry, 'StructuredEvidenceShadow') as adapter:
            for send, fee in ((False, False), (True, False), (False, True)):
                with self.assertRaises(ValueError):
                    entry.run_authorized('unused', send_authorized=send, estimated_budget_risk_accepted=fee)
            adapter.assert_not_called()
        self.assertFalse(entry.OUTPUT_DIR.exists())

    def mock_adapter(self, *, mode='valid'):
        calls = []
        def factory(*, settings, judge_factory):
            self.assertEqual(judge_factory.entrypoint, entry.FACTORY)
            self.assertEqual(settings.timeout_seconds, 30)
            config = strict_json_loads(judge_factory.config_json)
            self.assertNotIn('expected_status', judge_factory.config_json)
            actual = StructuredEvidenceShadow(settings=ShadowSettings(enabled=True, timeout_seconds=0.2 if mode=='hang' else 5),
                judge_factory=JudgeFactory(entrypoint='fixtures.shadow_mock_judge:MockJudge', config_json=canonical_json({'mode':mode})))
            class Adapter:
                def run(_, contract, result, **kwargs):
                    calls.append(kwargs['request_id'])
                    run = actual.run(contract, result, **kwargs)
                    if mode != 'hang':
                        entry._save(Path(config['usage_file']), dict(request_id=kwargs['request_id'],
                            input_sha256=run.diagnostics['input_sha256'], usage_known=True,
                            state='response_received', model_calls=1, reasoning_tokens=0,
                            reasoning_content_present=False, finish_reason='stop', response_model=entry.provider.MODEL,
                            prompt_version=entry.provider.PROMPT_VERSION, system_prompt_sha256=entry.EXPECTED_PROMPT,
                            cost_cny_at_peak_rates='0.01', input_tokens=3000, output_tokens=500))
                    return run
            return Adapter()
        return factory, calls

    def test_three_calls_semantic_errors_do_not_retry_and_second_run_refused(self):
        factory, calls = self.mock_adapter()
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized('unused', send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(set(calls)), 3)
            self.assertEqual(len(summary['candidates']), 15)
            self.assertEqual(sum(r['status_match'] for r in summary['candidates']), 2)
            self.assertFalse(summary['publication_allowed'])
            self.assertFalse(summary['semantic_verified'])
            before = (entry.OUTPUT_DIR/'execution-summary.json').read_bytes()
            with self.assertRaises(ValueError):
                entry.run_authorized('unused', send_authorized=True, estimated_budget_risk_accepted=True)
            self.assertEqual(len(calls), 3)
            self.assertEqual(before, (entry.OUTPUT_DIR/'execution-summary.json').read_bytes())

    def test_timeout_stops_later_batches_and_preserves_na(self):
        factory, calls = self.mock_adapter(mode='hang')
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized('unused', send_authorized=True, estimated_budget_risk_accepted=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary['status'], 'stopped')
        self.assertEqual([r['status'] for r in summary['batches'][1:]], ['not_executed']*2)
        self.assertTrue(all(r['model_status'] is None for r in summary['candidates']))
        receipt = strict_json_loads((entry.OUTPUT_DIR/'Q1/shadow-execution.json').read_text())
        self.assertTrue(receipt['execution']['reaped'])
        self.assertTrue(receipt['execution']['threads_released'])

    def test_comparison_uses_fixed_gold_not_model_rationale(self):
        _, cases, (_, _, request) = next(entry.prepared_batches(entry.load_fixed()))
        output = authored_output(request, cases)
        output['fact_judgments'][1].update(status='refuted', rationale='I am correct')
        judgments = entry.protocol.parse(canonical_json(output), entry.protocol.JudgeOutput)
        report = entry.compare(cases, request, judgments)
        self.assertIn('opposite_experience_as_refutation', report['candidates'][1]['error_types'])
        self.assertEqual(report['candidates'][1]['semantic_basis_review'], 'pending_independent_reading')

    def test_fixture_drift_fails_closed(self):
        path = self.directory/'changed.json'
        path.write_bytes(entry.FIXTURE.read_bytes()+b' ')
        with patch.object(entry, 'FIXTURE', path), self.assertRaises(ValueError):
            entry.preflight('unused')

    def test_invalid_output_stops_after_one_attempt(self):
        factory, calls = self.mock_adapter(mode='unknown_fact')
        with patch.object(entry, 'StructuredEvidenceShadow', side_effect=factory):
            summary = entry.run_authorized('unused', send_authorized=True, estimated_budget_risk_accepted=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary['stop_reason'], 'shadow_validation_failed')
        self.assertTrue(all(r['model_status'] is None for r in summary['candidates']))


if __name__ == '__main__':
    unittest.main()
