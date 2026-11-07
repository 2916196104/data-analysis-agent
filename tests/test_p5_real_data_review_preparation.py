"""Synthetic-only tests; no historical private content or external services."""
from copy import deepcopy
import unittest

from scripts import prepare_p5_real_data_review as prep


def record(key='a', rid=1, text='😀冷风冷风', start=1, end=3):
    return dict(key=key, review_id=rid, annotation_id=rid + 100, sentiment='negative',
                quote=text[start:end], quote_start=start, quote_end=end, review_text=text)


def original_query():
    original = dict(task_type='evidence', target_entity='device', subtopic='example', sentiment_intent='negative',
        required_facts=dict(operator='all_of', items=[dict(id='f0', description='A', sentiment='neutral'),
            dict(id='f1', description='B', sentiment='negative')],
            relations=[dict(kind='same_review', fact_ids=['f0', 'f1']), dict(kind='before', fact_ids=['f0', 'f1'])]),
        polarity_structure=dict(negations=[dict(fact_id='f1', cue='未', scope='B')], contrast_fact_ids=[]),
        proposed_filters=dict(aspect=None, sentiment=None), uncertainty=['保留条件'],
        clarification=['原说明'], scope_ref=dict(run_id='archived'))
    original['version'] = 'rag-query-contract-v1'
    return dict(payload=dict(contract=original, independent_target_fact_ids=[]), revision=1,
                event_id='old-event', rationale='old confirmation')


class OfflineReviewPreparationTests(unittest.TestCase):
    def test_entire_exposed_review_excluded(self):
        pool, excluded = prep.pool_records([record(), record('b')], {1}, set())
        self.assertEqual(pool, {})
        self.assertEqual(len(excluded), 2)

    def test_exposed_text_under_new_id_excluded(self):
        row = record()
        pool, excluded = prep.pool_records([row], set(), {prep.text_digest(row['review_text'])})
        self.assertFalse(pool)
        self.assertEqual(excluded[0]['reason'], 'same_text_as_development_review')

    def test_unicode_quote_is_absolute_code_point_slice(self):
        pool, _ = prep.pool_records([record()], set(), set())
        self.assertEqual(pool['a']['quote_start'], 1)
        self.assertEqual(pool['a']['quote'], '冷风')

    def test_bad_slice_fails_closed(self):
        row = record()
        row['quote'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'archive_quote_mismatch'):
            prep.pool_records([row], set(), set())

    def test_duplicate_key_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_archive_key'):
            prep.pool_records([record(), record()], set(), set())

    def test_uf_collapsed_without_reordering_ranks(self):
        row = record()
        searches = [dict(query_id='Q', mode=m, hits=[dict(rank=1, **row)]) for m in ('U', 'F')]
        pairs = prep.memberships(searches, {'a': row})
        self.assertEqual(pairs, {('Q', 'a'): [dict(mode='U', rank=1), dict(mode='F', rank=1)]})

    def test_rank_rewrite_rejected(self):
        with self.assertRaisesRegex(ValueError, 'historical_rank_sequence_changed'):
            prep.memberships([dict(query_id='Q', mode='U', hits=[dict(rank=2, **record())])], {'a': record()})

    def test_membership_identity_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'historical_membership_source_mismatch'):
            prep.memberships([dict(query_id='Q', mode='U', hits=[dict(rank=1, **record(rid=2))])], {'a': record()})

    def test_quota_cap_and_no_fabricated_candidates(self):
        pool = {'a': record()}
        self.assertEqual(prep.select_units(pool, {('Q', 'a'): [dict(rank=1)]}, [], {'Q': 4}), [('Q', 'a')])
        with self.assertRaisesRegex(ValueError, 'invalid_review_quota'):
            prep.select_units(pool, {}, [], {'Q': 31})

    def test_same_review_not_counted_as_independent_selection_priority(self):
        pool = {'a': record(), 'b': record('b'), 'c': record('c', 2)}
        pairs = {('Q', key): [dict(rank=n)] for n, key in enumerate(pool, 1)}
        selected = prep.select_units(pool, pairs, [], {'Q': 2})
        self.assertEqual([pool[k]['review_id'] for _, k in selected], [1, 2])

    def test_old_seed_dedup_is_not_new_four_state_gold(self):
        ref = dict(key='a', quote_start=1, quote_end=3)
        released = dict(case_id='old-case', contract_revision=1, reference=dict(human_confirmed=True,
            contract_confirmed=True, query_id='Q', mode='U', contract_version='old', fact_findings=[dict(
                fact_id='f0', support='direct', evidence_refs=[ref], rationale='old rationale')]))
        second = deepcopy(released)
        second['reference']['mode'] = 'F'
        seeds = prep.support_seeds([released, second], {'a': record()})
        self.assertEqual(len(seeds), 1)
        self.assertEqual(len(seeds[0]['references']), 2)
        self.assertFalse(seeds[0]['new_four_state_confirmed'])
        self.assertIn('/references/0/reference/fact_findings/0/evidence_refs/0', seeds[0]['references'][0]['pointer'])

    def test_no_support_label_from_old_partial_or_not_supported(self):
        for support in ('partial', 'not_supported', 'unknown'):
            released = dict(reference=dict(human_confirmed=True, contract_confirmed=True,
                fact_findings=[dict(support=support)]))
            self.assertEqual(prep.support_seeds([released], {'a': record()}), [])

    def test_old_seed_quote_range_rejected(self):
        released = dict(reference=dict(human_confirmed=True, contract_confirmed=True, fact_findings=[dict(
            support='direct', evidence_refs=[dict(key='a', quote_start=0, quote_end=3)])]))
        with self.assertRaisesRegex(ValueError, 'old_support_outside_quote'):
            prep.support_seeds([released], {'a': record()})

    def test_mapping_keeps_every_condition_without_upgrading_runtime(self):
        query = original_query()
        runtime = dict(operation='unsupported', support_status='unsupported', task_type='statistics', required_facts={})
        before = deepcopy(query)
        mapping = prep.contract_mapping('Q', 'original question', query, runtime)
        self.assertEqual(query, before)
        for name in prep.SEMANTIC_FIELDS:
            self.assertEqual(mapping['experimental_semantic_draft'][name], query['payload']['contract'][name])
        self.assertEqual(mapping['current_runtime_unmodified'], runtime)
        self.assertIsNone(mapping['experimental_runtime_contract'])
        self.assertIsNone(mapping['trusted_runtime_sha256'])
        self.assertFalse(mapping['human_review_enabled'])
        self.assertEqual(mapping['independent_target_fact_ids'], [])

    def test_review_sidecar_never_promotes_human_confirmation(self):
        mapping = prep.contract_mapping('Q', 'question', original_query(), {})
        unit = dict(unit_id='R001', query_id='Q', **record())
        seed = dict(query_id='Q', fact_id='f0', key='a', provenance_status='previously_confirmed')
        rows = prep.review_rows([unit], {'Q': mapping}, [seed])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['prior_provenance_status'], 'previously_confirmed')
        self.assertEqual(rows[1]['prior_provenance_status'], 'not_available')
        for row in rows:
            self.assertIsNone(row['expected_status'])
            self.assertIsNone(row['expected_reference_type'])
            self.assertFalse(row['human_confirmed'])
            self.assertFalse(row['review_enabled'])


if __name__ == '__main__':
    unittest.main()
