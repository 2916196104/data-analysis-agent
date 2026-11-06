"""P5-B2 tests: no database, external LLM, or embedding initialization."""
import sys
import unittest
from unittest.mock import patch

import test_p5b1_structured_evidence_design as fixtures
from data_analysis_agent.core.serialization import canonical_json, content_sha256
from data_analysis_agent.agents import p5b_shadow_contract as contract
from data_analysis_agent.agents import p5b_shadow_adapter as adapter


class P5B2ShadowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = fixtures.prepared(fixtures.authored_compound(operator='all_of'))

    def test_default_off_never_calls_worker(self):
        with patch.object(adapter, 'invoke_worker', side_effect=AssertionError('worker started')):
            out = adapter.run_shadow(self.request)
        self.assertEqual(out.execution_state, 'disabled')
        self.assertFalse(out.publication_allowed)
        self.assertFalse(out.semantic_verified)
        self.assertEqual(out.permitted_target_fact_ids, ())

    def test_id_only_descriptions_are_not_model_eligible(self):
        c = fixtures.contract()
        result, sources = fixtures.retrieval(c.original_question)
        with self.assertRaisesRegex(contract.PrototypeError, 'unresolved_fact_description'):
            contract.prepare_input(c, result,
                trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
                trusted_sources=sources, request_id='SYNTHETIC')

    def test_b2_contract_uses_trusted_source(self):
        c = fixtures.authored_compound()
        result, sources = fixtures.retrieval(c.original_question)
        request = contract.prepare_input(c, result,
            trusted_contract_sha256=content_sha256(c.model_dump(mode='json')),
            trusted_sources=sources, request_id='SYNTHETIC')
        self.assertEqual(request.candidates[0].quote, result.evidence[0].quote)
        self.assertFalse(contract.ShadowOutcome('validated_shadow', ()).publication_allowed)

    def test_genuine_subprocess_timeout(self):
        state, _ = adapter.invoke_worker(
            (sys.executable, '-c', 'import time; time.sleep(3)'),
            b'{}', timeout_seconds=0.2, max_output_bytes=1024)
        self.assertEqual(state, 'timeout')

    def test_output_cap_actually_interrupts_worker(self):
        state, _ = adapter.invoke_worker(
            (sys.executable, '-c', "import sys; sys.stdout.write('X'*100000); sys.stdout.flush()"),
            b'{}', timeout_seconds=3, max_output_bytes=1024)
        self.assertEqual(state, 'output_budget_exceeded')

    def test_stdin_nonreader_cannot_block_deadline(self):
        state, _ = adapter.invoke_worker(
            (sys.executable, '-c', 'import time; time.sleep(3)'),
            b'X' * 120000, timeout_seconds=0.2, max_output_bytes=1024)
        self.assertEqual(state, 'timeout')

    def test_invalid_output_fails_closed(self):
        setting = adapter.ShadowSettings(enabled=True, worker_argv=('test-worker',))
        with patch.object(adapter, 'invoke_worker', return_value=('completed', b'{bad json')):
            out = adapter.run_shadow(self.request, settings=setting)
        self.assertEqual(out.execution_state, 'output_invalid')
        self.assertFalse(out.publication_allowed)

    def test_valid_model_shape_remains_unlicensed(self):
        raw = fixtures.response(self.request,
             statuses={(f.id, c.key):'unknown'
                       for f in self.request.contract.required_facts.items for c in self.request.candidates})
        setting = adapter.ShadowSettings(enabled=True, worker_argv=('test-worker',))
        with patch.object(adapter, 'invoke_worker', return_value=('completed', canonical_json(raw).encode())):
            out = adapter.run_shadow(self.request, settings=setting)
        self.assertEqual(out.execution_state, 'validated_shadow')
        self.assertFalse(out.semantic_verified)
        self.assertFalse(out.publication_allowed)
        self.assertEqual(out.permitted_target_fact_ids, ())

    def test_worker_error_fails_closed(self):
        setting = adapter.ShadowSettings(enabled=True, worker_argv=('test-worker',))
        with patch.object(adapter, 'invoke_worker', return_value=('worker_failed', b'')):
            out = adapter.run_shadow(self.request, settings=setting)
        self.assertEqual(out.execution_state, 'model_failed')
        self.assertFalse(out.publication_allowed)

    def test_budget_blocks_before_worker(self):
        setting = adapter.ShadowSettings(enabled=True, worker_argv=('test-worker',),
                                          max_input_tokens_estimate=10)
        with patch.object(adapter, 'invoke_worker', side_effect=AssertionError('worker started')):
            out = adapter.run_shadow(self.request, settings=setting)
        self.assertEqual(out.execution_state, 'input_invalid')
        self.assertFalse(out.publication_allowed)


if __name__ == '__main__':
    unittest.main()
