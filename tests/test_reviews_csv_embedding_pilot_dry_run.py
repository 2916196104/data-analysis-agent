"""Offline proposal checks; no model, database or network."""
import copy
import json
from unittest import TestCase, mock

from scripts import reviews_csv_embedding_pilot_dry_run as pilot


class PilotTests(TestCase):
    def setUp(self):
        self.record = json.loads(pilot.RECORD.read_bytes())

    def test_two_pilots_are_bounded_and_never_claim_live_selection(self):
        for product in pilot.PRODUCTS:
            with self.subTest(product=product):
                row = pilot.plan(self.record, product, 50)
                self.assertEqual(row['upper_bound_new_encodings'], 50)
                self.assertIsNone(row['selected_opinions'])
                self.assertFalse(row['live_snapshot_verified'] or row['write_ready'])
                self.assertEqual((row['database_connections'], row['embedding_calls'], row['database_writes']), (0, 0, 0))

    def test_other_sources_products_and_excess_limit_are_rejected(self):
        for product, limit in [('B019GBG0IE', 50), (pilot.PRODUCTS[0], 51), (pilot.PRODUCTS[0], 0)]:
            with self.subTest(product=product, limit=limit), self.assertRaises(ValueError):
                pilot.plan(self.record, product, limit)

    def test_bad_identity_counts_or_duplicate_are_rejected(self):
        for change in ('profile', 'count', 'duplicate'):
            record = copy.deepcopy(self.record)
            target = next(r for r in record['products'] if r.get('product_id') == pilot.PRODUCTS[0])
            if change == 'profile': record['profile_key'] = '0'*64
            if change == 'count': target['usable_vectors'] += 1
            if change == 'duplicate': record['products'].append(copy.deepcopy(target))
            with self.subTest(change=change), self.assertRaises(ValueError):
                pilot.plan(record, pilot.PRODUCTS[0], 50)

    def test_wrong_file_digest_blocks(self):
        with mock.patch.object(pilot.RECORD.__class__, 'read_bytes', return_value=b'{}'), \
             mock.patch('sys.argv', ['dry', '--product-id', pilot.PRODUCTS[0]]):
            self.assertEqual(pilot.main(), 2)
