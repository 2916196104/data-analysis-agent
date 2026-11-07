"""Offline only: synthetic config/aggregates and connection doubles, no databases."""
from contextlib import ExitStack, redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('manual_d01', ROOT/'scripts/run_rag_mvp_d01.py')
d01 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d01)
ORIGINAL_IDENTITY_READER = d01.frozen_database_identity


def aggregates():
    rows = []
    for product, reviews, count in [('100013588053', 1699, 0), ('100013588055', 510, 200)]:
        row = dict.fromkeys(d01.COUNT_FIELDS, 0)
        row.update(product_id=product, reviews=reviews)
        for key in ('declared_scope_rows', 'rows_linked_to_scoped_reviews', 'f_scope', 'f_profile',
                    'f_space_builder', 'f_active_before_current_key_join'):
            row[key] = count
        row.update(min_dims=1024 if count else None, max_dims=1024 if count else None,
                   min_norm=1.0 if count else None, max_norm=1.0 if count else None)
        rows.append(row)
    return rows


class ConnectionDouble:
    def __init__(self, *, readonly='on', isolation='repeatable read', rows=None, error=None):
        self.readonly, self.isolation = readonly, isolation
        self.rows, self.error = aggregates() if rows is None else rows, error
        self.commands, self.closed, self.rollback_count = [], False, 0

    def execute(self, sql, parameters=None, **kwargs):
        self.commands.append((sql, parameters))
        if sql.startswith('SHOW transaction_read_only'):
            return SimpleNamespace(fetchone=lambda: {'transaction_read_only': self.readonly})
        if sql.startswith('SHOW transaction_isolation'):
            return SimpleNamespace(fetchone=lambda: {'transaction_isolation': self.isolation})
        if sql.startswith('WITH products('):
            if self.error:
                raise self.error
            return SimpleNamespace(fetchall=lambda: self.rows)
        return SimpleNamespace()

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


class ManualD01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='d01-offline-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        guards = ExitStack()
        self.addCleanup(guards.close)
        guards.enter_context(patch.dict(os.environ, {}, clear=True))
        guards.enter_context(patch.object(d01, 'ROOT', self.root))
        guards.enter_context(patch.object(sys, 'executable', str(self.root/'.venv/Scripts/python.exe')))
        self.real_pg = guards.enter_context(patch('psycopg.connect', side_effect=AssertionError('no real PostgreSQL')))
        self.real_sqlite = guards.enter_context(patch('sqlite3.connect', side_effect=AssertionError('no real SQLite')))
        guards.enter_context(patch('socket.socket.connect', side_effect=AssertionError('no real socket')))
        self.dsn = 'postgresql://synthetic_user:PRIVATE_PASSWORD@127.0.0.1:5432/synthetic_db'
        self.identity = d01.database_identity(self.dsn)
        self.anchor = guards.enter_context(patch.object(d01, 'frozen_database_identity', return_value=self.identity))
        (self.root/'docs').mkdir()
        (self.root/'tests/fixtures').mkdir(parents=True)
        (self.root/'.env').write_text('DATABASE_URL='+self.dsn+'\n', encoding='utf-8')
        (self.root/'docs/rag-mvp-final-acceptance.md').write_text(
            (ROOT/'docs/rag-mvp-final-acceptance.md').read_text(encoding='utf-8'), encoding='utf-8')
        (self.root/'tests/fixtures/rag_mvp_acceptance_v1.json').write_bytes(
            (ROOT/'tests/fixtures/rag_mvp_acceptance_v1.json').read_bytes())
        baseline = self.root/d01.BASELINE
        baseline.mkdir(parents=True)
        (baseline/'preflight.json').write_text(json.dumps({'manifest_sha256': d01.MANIFEST_SHA,
            'protected_sha256': {'.env': sha256((self.root/'.env').read_bytes()).hexdigest()}}), encoding='utf-8')
        self.addCleanup(logging.disable, logging.NOTSET)

    def run_double(self, connection=None, error=None):
        dsn, sql, parameters = d01.preflight(self.root)
        with patch('psycopg.connect', return_value=connection, side_effect=error) as connect:
            result = d01.execute_once(self.root, dsn, sql, parameters)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(connect.call_args.args, (self.dsn,))
        self.assertEqual(connect.call_args.kwargs['options'], d01.OPTIONS)
        self.assertEqual(connect.call_args.kwargs['connect_timeout'], 10)
        return result

    def test_default_command_is_offline_and_creates_no_claim(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = d01.main([])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'offline_preflight_passed')
        self.assertFalse((self.root/d01.OUTPUT).exists())
        self.real_pg.assert_not_called()
        self.real_sqlite.assert_not_called()

    def test_success_preserves_query_bindings_readonly_and_rolls_back(self):
        connection = ConnectionDouble()
        result = self.run_double(connection)
        self.assertEqual(result['diagnosis'], '053_vector_coverage_missing_in_current_snapshot')
        self.assertTrue(result['transaction_protection_confirmed'])
        self.assertTrue(result['rolled_back'] and result['connection_closed'])
        self.assertEqual(connection.rollback_count, 1)
        commands = [s for s, _ in connection.commands]
        self.assertEqual(commands[:3], ['BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',
            "SET LOCAL statement_timeout='10s'", "SET LOCAL lock_timeout='1s'"])
        self.assertEqual(commands[3:5], ['SHOW transaction_read_only', 'SHOW transaction_isolation'])
        self.assertEqual(len(commands), 6)
        sql, parameters = connection.commands[-1]
        self.assertTrue(sql.startswith('WITH products('))
        self.assertEqual(parameters['profile'], '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304')
        self.assertEqual((parameters['lower'], parameters['upper']),
                         ('2021-09-11T00:00:00.000+00:00', '2022-08-27T00:00:00.000+00:00'))
        stored = json.loads((self.root/d01.OUTPUT/'result.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['rows'], aggregates())
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(stored))

    def test_connect_failure_is_sanitized_claimed_and_cannot_retry(self):
        result = self.run_double(error=d01.psycopg.OperationalError('PRIVATE_PASSWORD '+self.dsn+' denied 10013'))
        self.assertEqual(result['error']['category'], 'os_access_denied')
        self.assertEqual(result['connection_attempts'], 1)
        self.assertEqual(result['d01_attempts'], 0)
        self.assertNotIn(self.dsn, json.dumps(result))
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(result))
        with self.assertRaisesRegex(d01.CheckFailed, 'already_claimed'):
            d01.preflight(self.root)
        # A racing caller which passed preflight must still stop at atomic mkdir.
        blocked = d01.execute_once(self.root, self.dsn, 'not_executed', {})
        self.assertEqual(blocked['connection_attempts'], 0)
        self.real_pg.assert_not_called()

    def test_readonly_or_isolation_failure_prevents_d01(self):
        for i, overrides in enumerate([{'readonly': 'off'}, {'isolation': 'read committed'}]):
            with self.subTest(overrides=overrides), patch.object(d01, 'OUTPUT', Path(f'outputs/synthetic-{i}')):
                connection = ConnectionDouble(**overrides)
                result = self.run_double(connection)
                self.assertEqual(result['d01_attempts'], 0)
                self.assertTrue(connection.closed)
                self.assertEqual(connection.rollback_count, 1)
                self.assertFalse(any(s.startswith('WITH products(') for s, _ in connection.commands))

    def test_query_exception_never_retries_and_closes(self):
        connection = ConnectionDouble(error=d01.psycopg.errors.QueryCanceled('PRIVATE_PASSWORD timeout'))
        result = self.run_double(connection)
        self.assertEqual(result['d01_attempts'], 1)
        self.assertEqual(result['d01_completed'], 0)
        self.assertEqual(result['automatic_retries'], 0)
        self.assertEqual(result['error']['sqlstate'], '57014')
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.closed)
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(result))

    def test_unexpected_private_columns_are_never_published(self):
        rows = aggregates()
        rows[0]['text'] = 'PRIVATE_REVIEW'
        result = self.run_double(ConnectionDouble(rows=rows))
        self.assertEqual(result['error']['code'], 'unexpected_result_columns')
        self.assertNotIn('rows', result)
        self.assertNotIn('PRIVATE_REVIEW', json.dumps(result))

    def test_cleanup_failure_still_attempts_close_and_is_sanitized(self):
        connection = ConnectionDouble()
        connection.rollback = Mock(side_effect=RuntimeError('PRIVATE_PASSWORD'))
        result = self.run_double(connection)
        self.assertEqual(result['status'], 'error')
        self.assertTrue(connection.closed)
        self.assertFalse(result['rolled_back'])
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(result))

    def test_manifest_sql_target_config_and_environment_drift_fail_closed(self):
        manifest = self.root/'tests/fixtures/rag_mvp_acceptance_v1.json'
        original = manifest.read_text(encoding='utf-8')
        manifest.write_text(original.replace('100013588053', '100013588099'), encoding='utf-8')
        with self.assertRaisesRegex(d01.CheckFailed, 'manifest_changed'): d01.preflight(self.root)
        manifest.write_text(original, encoding='utf-8')
        report = self.root/'docs/rag-mvp-final-acceptance.md'
        text = report.read_text(encoding='utf-8')
        report.write_text(text.replace('AND invalidated_at IS NULL)', 'AND invalidated_at IS NOT NULL)'), encoding='utf-8')
        with self.assertRaisesRegex(d01.CheckFailed, 'sql_changed'): d01.preflight(self.root)
        report.write_text(text, encoding='utf-8')
        self.anchor.return_value = 'postgresql://other.invalid:5432/synthetic_db'
        with self.assertRaisesRegex(d01.CheckFailed, 'target_mismatch'): d01.preflight(self.root)
        self.anchor.return_value = self.identity
        with patch.dict(os.environ, {'DATABASE_URL': self.dsn+'other'}):
            with self.assertRaisesRegex(d01.CheckFailed, 'environment_override_changed'): d01.preflight(self.root)
        with patch.dict(os.environ, {'PGSSLMODE': 'verify-full'}):
            with self.assertRaisesRegex(d01.CheckFailed, 'environment_not_frozen'): d01.preflight(self.root)
        (self.root/'.env').write_text('DATABASE_URL='+self.dsn+'other', encoding='utf-8')
        with self.assertRaisesRegex(d01.CheckFailed, 'config_file_changed'): d01.preflight(self.root)
        self.real_pg.assert_not_called()

    def test_local_identity_anchor_is_readonly_and_closed(self):
        queue = self.root/d01.BASELINE/'tasks.sqlite3'
        queue.touch()
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [(self.identity,)]
        with patch('sqlite3.connect', return_value=connection) as connect:
            self.assertEqual(ORIGINAL_IDENTITY_READER(self.root), self.identity)
        self.assertTrue(connect.call_args.args[0].endswith('?mode=ro'))
        self.assertTrue(connect.call_args.kwargs['uri'])
        connection.execute.assert_called_once_with('SELECT * FROM task_context')
        connection.close.assert_called_once()

    def test_result_validation_rejects_wrong_products_and_nonfinite_numbers(self):
        parameters = {'p053': '100013588053', 'p055': '100013588055'}
        bad = aggregates(); bad[0]['product_id'] = 'other'
        with self.assertRaisesRegex(d01.CheckFailed, 'scope_mismatch'): d01.checked_rows(bad, parameters)
        bad = aggregates(); bad[1]['min_norm'] = float('nan')
        with self.assertRaisesRegex(d01.CheckFailed, 'norm_aggregate'): d01.checked_rows(bad, parameters)
        rows = aggregates(); rows[0]['rows_linked_to_scoped_reviews'] = 1
        self.assertEqual(d01.diagnose(rows), '053_excluded_at_f_scope')
        for field in ('f_scope', 'f_profile', 'f_space_builder', 'f_active_before_current_key_join'): rows[0][field] = 1
        self.assertEqual(d01.diagnose(rows), 'current_key_and_complete_snapshot_verification_still_required')


if __name__ == '__main__':
    unittest.main()
