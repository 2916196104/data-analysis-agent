"""完全合成的历史快照与封存前提测试；不读取真实归档或数据库。"""

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.historical_snapshot import SnapshotInput, verify_historical_snapshot
from data_analysis_agent.reviews.query_contract import ScopeRef
from data_analysis_agent.reviews.rag_corpus import CORPUS_CONTRACT_VERSION, _document, _review_state
from data_analysis_agent.reviews.retrieval_evaluation import run_binding
from data_analysis_agent.reviews.e1_preparation import PreparationError
from test_e1_readiness import fixture as batch_fixture, prepare


def fixture():
    text = '合成甲主题事实。合成乙主题事实。'
    identity = ('SYNTHETIC-MODEL', 'SYNTHETIC-ENDPOINT', 'SYNTHETIC-PROMPT')
    profile = content_sha256(list(identity))
    scope = ScopeRef(run_id=content_sha256('SYNTHETIC-RUN'),
        manifest_sha256=content_sha256('SYNTHETIC-MANIFEST'), scope_sha256=content_sha256('SYNTHETIC-SCOPE'))
    state = dict(source='SYNTHETIC', dataset_version='SYNTHETIC-V1', product_id='SYNTHETIC-DEVICE',
        review_id=1, review_content_sha256=content_sha256({'synthetic': text}), published_at='2031-01-01T00:00:00Z',
        annotation_id=2, human_revision_id=None, processing_status='success', human_status='unreviewed',
        opinion_source='model', error_code=None, opinion_count=2)
    metadata, docs = [], []
    for n, quote in enumerate(('合成甲主题事实', '合成乙主题事实'), 1):
        start = text.index(quote)
        item = dict(model_opinion_id=n, human_opinion_id=None, aspect='quality', sentiment='positive',
            quote_start=start, quote_end=start+len(quote), embedding_input_sha256=sha256(quote.encode('utf-8')).hexdigest())
        metadata.append(item)
        docs.append(_document(dict(**state, **item, review_text=text, quote=quote, import_run_id=3,
            line_number=4, file_sha256=content_sha256('SYNTHETIC-FILE')), identity, profile))
    expected = _review_state(state, docs, profile).effective_snapshot_sha256
    value = dict(version='rag-historical-snapshot-input-v1', data_kind='synthetic', scope_ref=scope.model_dump(mode='json'),
        basis='historical_export', history_receipt_sha256=content_sha256('SYNTHETIC-HISTORY-RECEIPT'),
        label_profile_key=profile, corpus_contract_version=CORPUS_CONTRACT_VERSION, collection_complete=True,
        state=state, review_text=text, opinions=metadata)
    args = dict(expected_scope=scope, expected_review_id=1, expected_snapshot_sha256=expected,
        expected_data_kind='synthetic', expected_historical_input_sha256=pin(value),
        expected_history_receipt_sha256=value['history_receipt_sha256'])
    return value, args


def pin(value):
    return content_sha256(SnapshotInput.model_validate_json(json.dumps(value)).model_dump(mode='json'))


def without_pins(args):
    return dict(args, expected_historical_input_sha256=None, expected_history_receipt_sha256=None)


class HistoricalSnapshotTests(unittest.TestCase):
    def test_complete_snapshot_matches_existing_corpus_rules(self):
        value, args = fixture()
        before = deepcopy(value)
        result = verify_historical_snapshot(value, **args)
        self.assertEqual(result.classification, 'rebuilt_match')
        self.assertEqual(result.computed_snapshot_sha256, args['expected_snapshot_sha256'])
        self.assertEqual(result.opinion_count, 2)
        self.assertEqual(result.execution_status, 'completed')
        self.assertEqual(value, before)
        self.assertEqual(result, verify_historical_snapshot(json.dumps(value), **args))
        self.assertNotIn('review_text', result.model_dump())

    def test_missing_additional_effective_opinion_is_not_a_complete_snapshot(self):
        value, args = fixture()
        value['opinions'].pop()
        result = verify_historical_snapshot(value, **without_pins(args))
        self.assertEqual(result.classification, 'missing_source')
        self.assertIn('opinion_count_mismatch', result.reason_codes)
        self.assertIsNone(result.computed_snapshot_sha256)

    def test_truncated_set_cannot_match_by_reducing_reported_count(self):
        value, args = fixture()
        value['opinions'].pop()
        value['state']['opinion_count'] = 1
        # A separately generated, synthetic historical export can still conflict
        # with the original frozen commitment; pins never manufacture equality.
        args['expected_historical_input_sha256'] = pin(value)
        result = verify_historical_snapshot(value, **args)
        self.assertEqual(result.classification, 'rebuilt_mismatch')
        self.assertNotEqual(result.computed_snapshot_sha256, args['expected_snapshot_sha256'])

    def test_historical_revision_change_is_reported_as_conflict(self):
        value, args = fixture()
        value['state'].update(human_revision_id=7, human_status='corrected', opinion_source='human')
        for n, opinion in enumerate(value['opinions'], 10):
            opinion.update(model_opinion_id=None, human_opinion_id=n)
        args['expected_historical_input_sha256'] = pin(value)
        result = verify_historical_snapshot(value, **args)
        self.assertEqual(result.classification, 'rebuilt_mismatch')
        self.assertIn('historical_snapshot_conflict', result.reason_codes)

    def test_annotation_change_cannot_reuse_frozen_snapshot(self):
        value, args = fixture()
        value['state']['annotation_id'] = 9
        args['expected_historical_input_sha256'] = pin(value)
        self.assertEqual(verify_historical_snapshot(value, **args).classification, 'rebuilt_mismatch')

    def test_wrong_opinion_count_and_duplicate_sources(self):
        for mutate in (lambda v:v['state'].update(opinion_count=3),
                       lambda v:(v['opinions'].append(deepcopy(v['opinions'][0])), v['state'].update(opinion_count=3))):
            value, args = fixture()
            mutate(value)
            result = verify_historical_snapshot(value, **without_pins(args))
            self.assertEqual(result.execution_status, 'validation_failed')
            self.assertIsNone(result.computed_snapshot_sha256)

    def test_state_fields_must_be_explicit_including_nulls(self):
        for field in ('processing_status', 'human_status', 'error_code', 'human_revision_id', 'opinion_source'):
            value, args = fixture()
            del value['state'][field]
            result = verify_historical_snapshot(value, **args)
            self.assertEqual(result.classification, 'unverified')
            self.assertEqual(result.execution_status, 'validation_failed')

    def test_snapshot_hash_conflict(self):
        value, args = fixture()
        args['expected_snapshot_sha256'] = '0'*64
        self.assertEqual(verify_historical_snapshot(value, **args).classification, 'rebuilt_mismatch')

    def test_current_state_even_with_matching_digest_is_not_historical_proof(self):
        value, args = fixture()
        value['basis'] = 'current_observation'
        args['expected_historical_input_sha256'] = pin(value)
        result = verify_historical_snapshot(value, **args)
        self.assertEqual(result.computed_snapshot_sha256, args['expected_snapshot_sha256'])
        self.assertEqual(result.classification, 'history_unproven')

    def test_external_historical_input_and_receipt_pins_are_both_required(self):
        for missing in ('expected_historical_input_sha256', 'expected_history_receipt_sha256'):
            value, args = fixture()
            args[missing] = None
            self.assertEqual(verify_historical_snapshot(value, **args).classification, 'history_unproven')

    def test_pin_conflicts_fail_validation_not_normal_mismatch(self):
        for name in ('expected_historical_input_sha256', 'expected_history_receipt_sha256'):
            value, args = fixture()
            args[name] = '0'*64
            result = verify_historical_snapshot(value, **args)
            self.assertEqual(result.execution_status, 'validation_failed')
            self.assertIn('historical_pin_conflict', result.reason_codes)

    def test_unproven_collection_and_absent_source(self):
        value, args = fixture()
        value['collection_complete'] = False
        result = verify_historical_snapshot(value, **without_pins(args))
        self.assertEqual(result.classification, 'unverified')
        self.assertIsNone(result.computed_snapshot_sha256)
        self.assertEqual(verify_historical_snapshot(None, **args).classification, 'missing_source')

    def test_scope_review_and_data_kind_cannot_be_rebound(self):
        mutations = (lambda v:v['scope_ref'].update(scope_sha256='0'*64),
                     lambda v:v['state'].update(review_id=9), lambda v:v.update(data_kind='frozen_query'))
        for mutate in mutations:
            value, args = fixture()
            mutate(value)
            self.assertIn('snapshot_identity_mismatch', verify_historical_snapshot(value, **args).reason_codes)

    def test_quote_digest_span_and_origin_integrity(self):
        mutations = (lambda v:v['opinions'][0].update(embedding_input_sha256='0'*64),
                     lambda v:v['opinions'][0].update(quote_end=999),
                     lambda v:v['opinions'][0].update(model_opinion_id=None, human_opinion_id=1),
                     lambda v:v['state'].update(published_at='2031-01-01T00:00:00'))
        for mutate in mutations:
            value, args = fixture()
            mutate(value)
            result = verify_historical_snapshot(value, **without_pins(args))
            self.assertEqual(result.execution_status, 'validation_failed')
            self.assertIn('snapshot_state_or_opinion_integrity_error', result.reason_codes)

    def test_extra_cached_hash_and_bad_trust_parameters_are_rejected(self):
        value, args = fixture()
        value['effective_snapshot_sha256'] = args['expected_snapshot_sha256']
        self.assertEqual(verify_historical_snapshot(value, **args).execution_status, 'validation_failed')
        value, args = fixture()
        args['expected_snapshot_sha256'] = 'bad'
        with self.assertRaisesRegex(ValueError, '^invalid_snapshot_trust_parameters$'):
            verify_historical_snapshot(value, **args)

    def test_empty_review_opinion_set_has_reproducible_snapshot(self):
        value, args = fixture()
        value['opinions'] = []
        value['state']['opinion_count'] = 0
        args['expected_snapshot_sha256'] = _review_state(value['state'], [], value['label_profile_key']).effective_snapshot_sha256
        args['expected_historical_input_sha256'] = pin(value)
        result = verify_historical_snapshot(value, **args)
        self.assertEqual(result.classification, 'rebuilt_match')
        self.assertEqual(result.opinion_count, 0)

    def test_snapshot_verifier_never_connects_or_calls_e1(self):
        with patch('socket.socket.connect', side_effect=AssertionError('no network')), \
             patch('psycopg.connect', side_effect=AssertionError('no database')), \
             patch('data_analysis_agent.reviews.evidence_judge.judge_evidence', side_effect=AssertionError('no E1')), \
             patch('data_analysis_agent.reviews.rag_corpus.read_effective_corpus', side_effect=AssertionError('no corpus read')):
            value, args = fixture()
            self.assertEqual(verify_historical_snapshot(value, **args).classification, 'rebuilt_match')


class ExistingPreparationBoundaryTests(unittest.TestCase):
    def test_external_collection_and_receipt_pins_cannot_be_missing(self):
        for field in ('expected_source_collection_sha256', 'expected_receipt_sha256'):
            run, sources, scope = batch_fixture()
            with self.assertRaises(PreparationError):
                prepare(run, sources, scope, **{field:None})

    def test_source_collection_identity_and_reference_errors_are_blocked(self):
        for mutate in (lambda s:s['records'][0].update(product_id='OTHER-SYNTHETIC'),
                       lambda s:s['records'][0].update(evidence_ref='invalid-synthetic-ref')):
            run, sources, scope = batch_fixture()
            mutate(sources)
            with self.assertRaises(PreparationError):
                prepare(run, sources, scope)

    def test_empty_frozen_query_mode_still_requires_external_pins(self):
        run, sources, scope = batch_fixture(())
        self.assertEqual(prepare(run, sources, scope).trusted_batch.candidates, ())
        with self.assertRaises(PreparationError):
            prepare(run, sources, scope, expected_source_collection_sha256=None)
        # An empty F list still depends on all sources in the complete U/F run.
        run, sources, scope = batch_fixture()
        run['searches'][1]['hits'] = []
        run['run_id'] = run_binding(run)
        ref = ScopeRef(run_id=run['run_id'], manifest_sha256=scope.manifest_sha256, scope_sha256=scope.scope_sha256)
        sources['scope_ref'] = ref.model_dump(mode='json')
        self.assertEqual(prepare(run, sources, ref, mode='filtered').trusted_batch.candidates, ())
        sources['records'] = []
        with self.assertRaisesRegex(PreparationError, 'missing_independent_source_record'):
            prepare(run, sources, ref, mode='filtered')

    def test_rank_order_is_checked_after_rebinding_synthetic_run(self):
        run, sources, scope = batch_fixture(('客服回复及时', '设备制冷良好'))
        run['searches'][0]['hits'].reverse()
        run['run_id'] = run_binding(run)
        ref = ScopeRef(run_id=run['run_id'], manifest_sha256=scope.manifest_sha256, scope_sha256=scope.scope_sha256)
        sources['scope_ref'] = ref.model_dump(mode='json')
        with self.assertRaises(PreparationError):
            prepare(run, sources, ref)


if __name__ == '__main__':
    unittest.main()
