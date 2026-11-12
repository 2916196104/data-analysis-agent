"""Offline safety and coverage logic only; no database, encoder or network."""
from types import SimpleNamespace
from unittest import TestCase, mock
import sys

from scripts import audit_all_products_rag_coverage as audit


class CoverageTests(TestCase):
    def example(self):
        values = dict(source='s', dataset_version='d', product_id='p', review_id=1, annotation_id=2,
            label_profile_key='a'*64, opinion_source='model', model_opinion_id=3, human_opinion_id=None,
            human_revision_id=None, aspect='logistics', sentiment='negative', quote_start=0, quote_end=3,
            review_content_sha256='h', embedding_input_sha256='q', text_builder_version='t',
            published_at='2021-01-01T00:00:00+00:00')
        row = dict(values, id=1, stable_versioned_opinion_key='key', effective_snapshot_sha256='snapshot',
                   embedding_space_id='space', invalidated_at=None, stored_vector='[1.0]')
        return SimpleNamespace(**values), row

    def classify(self, rows):
        doc, _ = self.example()
        return audit.classify_vectors({'key': doc}, {1: SimpleNamespace(annotation_id=2, human_revision_id=None)},
            {'key': 'snapshot'}, rows, 'space', 't', 'a'*64, lambda v: v)

    def test_default_never_invokes_execute(self):
        with mock.patch.object(sys, 'argv', ['audit']), mock.patch.object(audit, 'execute') as execute:
            self.assertEqual(audit.main(), 0)
        execute.assert_not_called()

    def test_authorization_requires_valid_profile_before_connection(self):
        with mock.patch.object(sys, 'argv', ['audit', '--execute-authorized']), mock.patch.object(audit, 'execute') as execute:
            self.assertEqual(audit.main(), 2)
        execute.assert_not_called()

    def test_current_member_and_different_dimensions(self):
        doc, row = self.example()
        counts, usable, bad = self.classify([row])
        self.assertEqual(usable, {'key'})
        self.assertEqual(counts['snapshot_matched_vectors'], 1)
        self.assertFalse(bad)
        groups = audit.grouped_coverage({'key': doc}, usable)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(x['effective'] == x['usable'] == 1 for x in groups))

    def test_invalid_snapshot_profile_and_source_do_not_authorize(self):
        _, row = self.example()
        for changed, key in (({'effective_snapshot_sha256': 'old'}, 'snapshot_mismatch'),
                             ({'label_profile_key': 'b'*64}, 'profile_mismatch'),
                             ({'product_id': 'other'}, 'source_metadata_mismatch'),
                             ({'invalidated_at': 'past'}, 'invalidated')):
            with self.subTest(key=key):
                counts, usable, _ = self.classify([{**row, **changed}])
                self.assertEqual(counts[key], 1)
                self.assertEqual(usable, set())

    def test_duplicate_current_keys_are_rejected(self):
        _, row = self.example()
        with self.assertRaisesRegex(audit.AuditError, 'DUPLICATE_CURRENT_VECTOR'):
            self.classify([row, {**row, 'id': 2}])

    def test_unmatched_old_revision_is_not_declared_certified(self):
        _, row = self.example()
        counts, usable, _ = self.classify([{**row, 'stable_versioned_opinion_key': 'old', 'human_revision_id': 5}])
        self.assertEqual(counts['revision_mismatch_unverified'], 1)
        self.assertFalse(usable)

    def test_empty_scope_does_not_invent_coverage(self):
        item = dict(source='s', dataset_version='d', product_id='p', reviews=0, text_reviews=0,
                    historical_vectors=0)
        result = audit.audit_product(None, item, None, None, None, float('inf'))
        self.assertEqual(result['retrieval_status'], 'NO_EFFECTIVE_OPINIONS')
        self.assertIsNone(result['opinion_coverage'])
