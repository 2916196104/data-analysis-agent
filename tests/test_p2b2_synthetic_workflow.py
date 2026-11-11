import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import p2b2_synthetic_workflow as workflow


def result(outcome='direct_support'):
    return {'version': 'p2b2-synthetic-v1', 'data_kind': 'synthetic', 'case_id': 'SYNTHETIC_EXAMPLE',
            'outcome': outcome, 'citations': [], 'published_claims': []}


REFERENCE = {'data_kind': 'synthetic', 'case_id': 'SYNTHETIC_EXAMPLE', 'outcome': 'direct_support'}


class SyntheticWorkflowTests(unittest.TestCase):
    def test_frozen_rb03_input_is_valid_but_execution_is_blocked(self):
        p = ROOT / 'outputs/agent-stage2-real-business-readiness-20261111/p2-b1-blind-input-v1/RB03.blind.v1.json'
        data = p.read_bytes(); checked = workflow.validate_blind_input(data)
        self.assertTrue(checked['input_valid'])
        self.assertFalse(checked['execution_ready'])
        with self.assertRaises(ValueError): workflow.validate_blind_input(data + b' ')

    def test_real_case_or_non_synthetic_data_rejected(self):
        for k, value in [('case_id', 'RB03'), ('data_kind', 'real'), ('published_claims', ['claim']),
                         ('citations', ['citation']), ('outcome', 'invented')]:
            p = result(); p[k] = value
            with self.subTest(field=k), self.assertRaises(ValueError): workflow.validate_prediction(p)

    def test_all_required_statuses_are_distinct(self):
        required = {'direct_support', 'unsupported', 'unknown', 'insufficient_evidence',
                    'explicit_opposite', 'execution_failure', 'not_executed'}
        self.assertTrue(required <= workflow.OUTCOMES)
        for outcome in required: self.assertEqual(workflow.validate_prediction(result(outcome))['outcome'], outcome)

    def test_score_requires_seal_and_external_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                workflow.score_synthetic(tmp, independent_anchor='x', synthetic_reference=REFERENCE)
            directory, anchor = workflow.seal_synthetic_prediction(result(), tmp)
            with self.assertRaises(ValueError):
                workflow.score_synthetic(directory, independent_anchor='0'*64, synthetic_reference=REFERENCE)
            self.assertTrue(workflow.score_synthetic(directory, independent_anchor=anchor, synthetic_reference=REFERENCE)['fact_support_accuracy'])

    def test_scoring_does_not_modify_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, anchor = workflow.seal_synthetic_prediction(result(), tmp)
            p = directory / 'prediction.json'; before = p.read_bytes()
            workflow.score_synthetic(directory, independent_anchor=anchor, synthetic_reference=REFERENCE)
            self.assertEqual(p.read_bytes(), before)

    def test_tampering_fails_even_if_receipt_is_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, anchor = workflow.seal_synthetic_prediction(result(), tmp)
            payload = json.dumps(result('unsupported')).encode()
            (directory / 'prediction.json').write_bytes(payload)
            (directory / 'receipt.json').write_text(json.dumps({'version': 'p2b2-synthetic-seal-v1',
                'sha256': hashlib.sha256(payload).hexdigest()}), encoding='utf-8')
            with self.assertRaises(ValueError): workflow.score_synthetic(directory, independent_anchor=anchor, synthetic_reference=REFERENCE)

    def test_collision_never_overwrites_and_workspace_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, _ = workflow.seal_synthetic_prediction(result(), tmp)
            before = (directory / 'prediction.json').read_bytes()
            with self.assertRaises(FileExistsError): workflow.seal_synthetic_prediction(result('unsupported'), tmp)
            self.assertEqual((directory / 'prediction.json').read_bytes(), before)
        with self.assertRaises(ValueError): workflow.seal_synthetic_prediction(result(), ROOT)

    def test_unknown_refusal_fail_and_not_executed_are_not_correct_rejections(self):
        for status in ('unknown', 'refusal', 'execution_failure', 'not_executed'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                directory, anchor = workflow.seal_synthetic_prediction(result(status), tmp)
                score = workflow.score_synthetic(directory, independent_anchor=anchor, synthetic_reference=REFERENCE)
                self.assertIsNone(score['fact_support_accuracy'])
                self.assertIsNone(score['unsupported_claim_rate'])
                self.assertIsNone(score['citation_precision'])
                self.assertEqual(score['refusal'], status == 'refusal')
                self.assertEqual(score['execution_failure'], status == 'execution_failure')
                self.assertEqual(score['not_executed'], status == 'not_executed')

    def test_scorer_refuses_non_synthetic_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory, anchor = workflow.seal_synthetic_prediction(result(), tmp)
            for update in ({'data_kind': 'real'}, {'case_id': 'RB03'}, {'extra': 'answer'}):
                ref = copy.deepcopy(REFERENCE); ref.update(update)
                with self.assertRaises(ValueError): workflow.score_synthetic(directory, independent_anchor=anchor, synthetic_reference=ref)

    def test_rb09_subquery_stays_unapproved(self):
        p = ROOT / 'outputs/agent-stage2-real-business-readiness-20261111/p2-b1-blind-input-v1/RB09.blind.v1.json'
        d = json.loads(p.read_text(encoding='utf-8'))
        self.assertIsNone(d['component_subquery'])
        self.assertIn('separate_effect_airflow_and_speed', d['query_conditions'])


if __name__ == '__main__':
    unittest.main()
