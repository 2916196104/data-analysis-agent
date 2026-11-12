"""Offline tests of the preflight gate, not API/Worker or business E2E evidence."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import check_agent_api_startup as preflight


class StartupPreflightTests(unittest.TestCase):
    def run_check(self, *, missing=False, permissions_only=False, production_error=None):
        connection = MagicMock()
        statements = []

        def execute(query, parameters=None):
            statements.append(query)
            cursor = MagicMock()
            text = str(query)
            if 'AS database' in text:
                cursor.fetchone.return_value = dict(database='reviews', role='rag_audit_ro',
                    login_role='rag_audit_ro', read_only='on', schema_usage=True)
            elif 'rolsuper' in text:
                cursor.fetchone.return_value = dict(rolsuper=False, rolcreatedb=False,
                    rolcreaterole=False, rolreplication=False, rolbypassrls=False, write_all_data=False)
            elif 'unnest' in text:
                cursor.fetchall.return_value = [dict(name=name, exists=True,
                    can_select=not (missing and name=='review_monitor_schema'),
                    can_write=False, owner_member=False) for name in preflight.VERSION_TABLES]
            elif 'aclexplode' in text:
                cursor.fetchall.return_value = []
            elif isinstance(query, preflight.sql.Composed):
                cursor.fetchall.return_value = [{'version': 1}]
            else:
                self.assertIn(text, ('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY', 'ROLLBACK'))
            return cursor

        connection.execute.side_effect = execute
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            arguments = ['check', '--output', str(output)] + (['--permissions-only'] if permissions_only else [])
            with patch('sys.argv', arguments), redirect_stdout(io.StringIO()), \
                 patch.object(preflight.psycopg, 'connect', return_value=connection), \
                 patch('data_analysis_agent.api.app.check_business_database', side_effect=production_error) as check:
                code = preflight.main()
            result = json.loads(output.read_text(encoding='utf-8'))
        connection.close.assert_called_once()
        return code, result, check

    def test_missing_monitor_select_is_reported_without_starting_production_check(self):
        code, result, check = self.run_check(missing=True, permissions_only=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['missing_select'], ['review_monitor_schema'])
        self.assertEqual(len(result['version_values']), 3)
        self.assertEqual(result['production_api_check'], 'NOT_RUN')
        check.assert_not_called()

    def test_successful_acl_metadata_cannot_hide_a_failed_original_production_check(self):
        code, result, check = self.run_check(production_error=RuntimeError('startup failure'))
        self.assertEqual((code, result['status'], result['production_api_check']), (1, 'FAIL', 'FAIL'))
        check.assert_called_once_with(preflight.DB)

    def test_default_preflight_calls_original_check_but_never_starts_worker_or_tasks(self):
        code, result, check = self.run_check()
        self.assertEqual((code, result['status'], result['production_api_check']), (0, 'PASS', 'PASS'))
        check.assert_called_once_with(preflight.DB)
        self.assertFalse(result['api_started'])
        self.assertFalse(result['worker_started'])
        for key in ('task_submissions', 'task_database_writes', 'business_write_statements',
                    'acl_changes', 'model_calls', 'embedding_calls'):
            self.assertEqual(result[key], 0)


if __name__ == '__main__':
    unittest.main()
