import copy
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import p2b1_blind_protocol as protocol
import p2b1_build_blind_input as builder


class BlindAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = builder.load_sources()
        cls.packages = builder.build(cls.raw)
        cls.original = json.loads(cls.raw['candidate_file_sha256'])
        cls.units = {u['evidence_id']: u for u in cls.original['evidence_units']}

    def test_inputs_match_frozen_questions_scope_and_order(self):
        self.assertEqual([p['case_id'] for p in self.packages], ['RB03', 'RB09'])
        for p in self.packages:
            question, ids = protocol.QUESTIONS[p['case_id']]
            self.assertEqual(p['original_question'], question)
            self.assertEqual(p['scope'], protocol.SCOPE)
            self.assertEqual([d['evidence_id'] for d in p['candidate_documents']], list(ids))

    def test_documents_are_full_originals_and_opinions_not_human_slices(self):
        for p in self.packages:
            for d in p['candidate_documents']:
                raw = self.units[d['evidence_id']]
                self.assertEqual((d['review_id'], d['annotation_id'], d['review_text']),
                                 (raw['review_id'], raw['annotation_id'], raw['review_text']))
                self.assertEqual(hashlib.sha256(d['review_text'].encode()).hexdigest(), d['review_text_utf8_sha256'])
                self.assertEqual(d['original_opinion_spans'],
                                 [{k: o[k] for k in protocol.OPINION} for o in raw['historical_opinions']])
                for o in d['original_opinion_spans']:
                    self.assertEqual(d['review_text'][o['quote_start']:o['quote_end']], o['quote'])

    def test_whitelist_forbids_nested_answers_gold_files_and_q_ranges(self):
        for extra in ('candidate_decisions', 'final_candidate_label', 'permitted_quote_ranges',
                      'approved_proposal_ids', 'prediction', 'gold_path', 'score', 'human_notes'):
            for location in ('top', 'document', 'opinion'):
                p = copy.deepcopy(self.packages[0])
                obj = p if location == 'top' else p['candidate_documents'][0] if location == 'document' else p['candidate_documents'][0]['original_opinion_spans'][0]
                obj[extra] = 'forbidden'
                with self.subTest(extra=extra, location=location), self.assertRaises(ValueError):
                    protocol.validate(p)

    def test_independent_process_rejects_file_argument_and_gold_path_input(self):
        command = [sys.executable, '-I', str(ROOT / 'scripts/p2b1_blind_protocol.py')]
        gold = ROOT / 'outputs/agent-stage2-real-business-readiness-20261111/first8-review-v1/gold-reference-v1/real-business-gold-reference.v1.json'
        r = subprocess.run(command + [str(gold)], input=b'{}', capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(b'candidate_decisions', r.stdout + r.stderr)
        p = copy.deepcopy(self.packages[0]); p['gold_path'] = str(gold)
        r = subprocess.run(command, input=json.dumps(p, ensure_ascii=False).encode(), capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(b'candidate_decisions', r.stdout + r.stderr)

    def test_process_only_returns_preflight_no_prediction(self):
        for p in self.packages:
            r = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/p2b1_blind_protocol.py')],
                               input=json.dumps(p, ensure_ascii=False).encode(), capture_output=True, timeout=10, check=True)
            out = json.loads(r.stdout)
            self.assertEqual(out['status'], 'INPUT_PRECHECK_ONLY')
            self.assertIs(out['prediction_executed'], False)
            self.assertNotIn('assessment', out)
            self.assertNotIn('score', out)

    def test_no_subquery_without_independent_equivalence_review(self):
        for original in self.packages:
            self.assertIsNone(original['component_subquery'])
            self.assertFalse(protocol.validate(original)['component_subquery_ready'])
            p = copy.deepcopy(original); p['component_subquery'] = '有没有送货很慢的反馈？'
            with self.assertRaises(ValueError): protocol.validate(p)

    def test_unexpressed_conditions_are_not_silently_dropped(self):
        p = copy.deepcopy(self.packages[1]); p['query_conditions'].remove('separate_effect_airflow_and_speed')
        with self.assertRaises(ValueError): protocol.validate(p)

    def test_wrong_scope_or_question_rejected(self):
        for key, val in [('original_question', '不同问题'), ('scope', {})]:
            p = copy.deepcopy(self.packages[0]); p[key] = val
            with self.assertRaises(ValueError): protocol.validate(p)

    def test_inconsistent_document_or_span_rejected(self):
        for field, val in [('review_text', '篡改'), ('product_id', 'other'), ('file_sha256', '0' * 64)]:
            p = copy.deepcopy(self.packages[0]); p['candidate_documents'][0][field] = val
            with self.assertRaises(ValueError): protocol.validate(p)
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'][0]['original_opinion_spans'][0]['quote_end'] += 1
        with self.assertRaises(ValueError): protocol.validate(p)

    def test_candidate_reordering_or_replacement_rejected(self):
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'].reverse()
        with self.assertRaises(ValueError): protocol.validate(p)
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'][0]['evidence_id'] = 'EV01'
        with self.assertRaises(ValueError): protocol.validate(p)

    def test_relabelled_identity_and_spliced_historical_opinions_rejected(self):
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'][0]['review_id'] += 1
        with self.assertRaises(ValueError): protocol.validate(p)
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'][0]['annotation_id'] += 1
        with self.assertRaises(ValueError): protocol.validate(p)
        p = copy.deepcopy(self.packages[0]); p['candidate_documents'][1]['original_opinion_spans'].pop()
        with self.assertRaises(ValueError): protocol.validate(p)

    def test_forged_manifest_rejected(self):
        p = copy.deepcopy(self.packages[0]); p['source_manifest']['row_observation_sha256'] = '0' * 64
        with self.assertRaises(ValueError): protocol.validate(p)

    def test_source_bytes_tampering_fails_closed(self):
        for role in builder.FILES:
            raw = dict(self.raw); raw[role] += b' '
            with self.subTest(role=role), self.assertRaises(ValueError): builder.build(raw)

    def test_no_network_database_or_business_judgment_needed(self):
        with patch('socket.socket', side_effect=AssertionError('network denied')):
            built = builder.build(self.raw)
            self.assertEqual(len(built), 2)
            for p in built: self.assertFalse(protocol.validate(p)['prediction_executed'])

    def test_unknown_cannot_be_marked_correct_or_refused(self):
        p = copy.deepcopy(self.packages[1]); p['prediction_result'] = 'unknown_correct'
        with self.assertRaises(ValueError): protocol.validate(p)
        self.assertEqual(set(protocol.validate(self.packages[1])),
                         {'status', 'case_id', 'prediction_executed', 'original_question_comparable', 'component_subquery_ready'})

    def test_original_question_and_component_scoring_are_separate(self):
        for p in self.packages:
            result = protocol.validate(p)
            self.assertFalse(result['original_question_comparable'])
            self.assertFalse(result['component_subquery_ready'])
            self.assertIn('production_validator', p['non_claimed_capabilities'])


if __name__ == '__main__':
    unittest.main()
