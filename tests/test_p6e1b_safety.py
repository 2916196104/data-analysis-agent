"""只测不连接数据库的安全门，绝不把本组测试计为PG-01～08通过。"""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from p6e1b_environment import ENV_NAMES, E1BBlocked, load_authorization
from data_analysis_agent.core.database import database_identity


class P6E1BSafetyTests(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('no network')))
        self.pg = self.enterContext(patch('psycopg.connect', side_effect=AssertionError('no database')))
        self.addCleanup(self.network.assert_not_called); self.addCleanup(self.pg.assert_not_called)
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='p6e1b-gate-')))
        self.run_id = 'a' * 32
        self.values = {'P6_E1B_AUTHORIZATION_FILE': str(self.root / 'authorization.json')}
        self.manifest = {'version':'p6-e1b-authorization-v1','run_id':self.run_id,
            'purpose':'synthetic-p6-postgresql-integration','authorized':True,'synthetic_writes_authorized':True,
            'approved_by':'OFFLINE-GATE-TEST-ONLY','expires_at':'2099-01-01T00:00:00+00:00',
            'allow_database_or_schema_drop':False,'identity_sha256':{},'role_sha256':{}}
        for role, name in ENV_NAMES.items():
            user = 'creator' if role == 'task_creator' else role
            kind = 'tasks' if role in ('task', 'task_creator') else 'business'
            dsn = f'postgresql://{user}:fake-only@offline.invalid/p6_e1b_{self.run_id}_{kind}'
            self.values[name] = dsn
            self.manifest['identity_sha256'][role] = hashlib.sha256(database_identity(dsn).encode()).hexdigest()
            self.manifest['role_sha256'][role] = hashlib.sha256(user.encode()).hexdigest()
        self.save()

    def save(self):
        Path(self.values['P6_E1B_AUTHORIZATION_FILE']).write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_missing_environment_never_falls_back_to_business_or_generic_admin_url(self):
        for values in ({}, {'DATABASE_URL':'postgresql://secret@offline.invalid/reviews'},
                       {'TEST_POSTGRES_ADMIN_URL':'postgresql://secret@offline.invalid/postgres'}):
            with self.subTest(), self.assertRaises(E1BBlocked): load_authorization(values)

    def test_complete_synthetic_configuration_only_passes_local_pins_not_live_identity(self):
        config = load_authorization(self.values)
        self.assertEqual(config.run_id, self.run_id)
        self.assertNotIn('fake-only', repr(config)); self.assertNotIn('offline.invalid', repr(config))

    def test_missing_any_role_or_authorization_is_blocked(self):
        for key in self.values:
            with self.subTest(key=key), self.assertRaises(E1BBlocked):
                load_authorization({k:v for k,v in self.values.items() if k != key})

    def test_reviews_or_other_run_target_is_rejected_even_if_identity_hash_is_recomputed(self):
        for name in ('reviews', 'p6_e1b_' + 'b'*32 + '_business'):
            values = dict(self.values)
            values[ENV_NAMES['reader']] = 'postgresql://reader:fake-only@offline.invalid/' + name
            self.manifest['identity_sha256']['reader'] = hashlib.sha256(database_identity(values[ENV_NAMES['reader']]).encode()).hexdigest()
            self.save()
            with self.subTest(name=name), self.assertRaises(E1BBlocked): load_authorization(values)

    def test_expired_unapproved_or_drop_permission_cannot_start(self):
        original = deepcopy(self.manifest)
        for changes in ({'expires_at':'2000-01-01T00:00:00+00:00'}, {'authorized':False},
                        {'synthetic_writes_authorized':False}, {'approved_by':''},
                        {'allow_database_or_schema_drop':True}, {'version':'old'}):
            self.manifest = {**original, **changes}; self.save()
            with self.subTest(changes=changes), self.assertRaises(E1BBlocked):load_authorization(self.values)

    def test_pin_or_role_mismatch_is_blocked_without_leaking_credentials(self):
        values = {**self.values, ENV_NAMES['writer']:self.values[ENV_NAMES['reader']]}
        with self.assertRaises(E1BBlocked) as caught:load_authorization(values)
        self.assertNotIn('fake-only', str(caught.exception)); self.assertNotIn('postgresql', str(caught.exception))
        self.manifest['identity_sha256']['creator'] = '0'*64; self.save()
        with self.assertRaises(E1BBlocked):load_authorization(self.values)

    def test_schema_or_connection_target_overrides_are_rejected(self):
        for query in ('options=-csearch_path=other', 'dbname=reviews', 'host=other.invalid', 'service=production'):
            values = {**self.values, ENV_NAMES['reader']:self.values[ENV_NAMES['reader']] + '?' + query}
            with self.subTest(query=query), self.assertRaises(E1BBlocked):load_authorization(values)

    def test_run_entry_with_no_test_authorization_is_blocked_before_any_connection(self):
        import p6e1b_postgresql_integration as integration
        result=self.root/'blocked.json';output=io.StringIO()
        with patch.dict('os.environ',{'DATABASE_URL':'postgresql://production-secret@offline.invalid/reviews'},clear=True), \
             patch('sys.argv',['p6e1b','--run','--output',str(result)]),redirect_stdout(output):
            self.assertEqual(integration.main(),2)
        saved=json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(saved['status'],'BLOCKED')
        self.assertEqual(saved['postgresql_cases_executed'],0)
        self.assertNotIn('production-secret',output.getvalue())


if __name__ == '__main__': unittest.main()
