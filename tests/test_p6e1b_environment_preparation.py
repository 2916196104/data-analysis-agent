"""环境方案生成器的纯离线验证，不是PostgreSQL集成验收。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('e1b_prepare', Path(__file__).resolve().parents[1] / 'scripts/prepare_p6e1b_environment.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class E1BEnvironmentPreparationTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'psycopg.connect', 'subprocess.run', 'subprocess.Popen'):
            blocked = self.enterContext(patch(target, side_effect=AssertionError('offline only')))
            self.addCleanup(blocked.assert_not_called)
        self.folder = Path(self.enterContext(TemporaryDirectory(prefix='e1b-plan-offline-')))
        self.plan = prepare.make_plan('b'*32, 55441)
        self.manifest = prepare.draft_manifest(self.plan)
        self.auth = self.folder / 'authorization.json'

    def save(self):
        self.auth.write_text(json.dumps(self.manifest), encoding='utf-8')

    def approve_fixture(self):
        # 仅临时单测夹具，不输出到真实环境或作为环境授权。
        self.manifest.update(authorized=True, synthetic_writes_authorized=True,
            approved_by='UNIT-TEST-ONLY', expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        self.manifest['environment_preparation'].update(authorized=True,
            **{p: True for p in prepare.PERMISSIONS}, expected_system_identifier='1234567890123456789')
        self.save()

    def test_draft_never_approves_or_invents_an_approver(self):
        self.assertFalse(self.manifest['authorized'])
        self.assertFalse(self.manifest['synthetic_writes_authorized'])
        self.assertEqual(self.manifest['approved_by'], '')
        self.assertEqual(self.manifest['expires_at'], '')
        self.save()
        with self.assertRaises(prepare.E1BBlocked): prepare.check_approval(self.plan, self.auth)

    def test_exact_five_targets_and_canonical_manifest_hash(self):
        self.approve_fixture()
        config = prepare.check_approval(self.plan, self.auth, require_cluster=True)
        self.assertEqual(set(config.dsns), {'creator','task_creator','reader','writer','task'})
        self.assertEqual(config.authorization_sha256, prepare.content_sha256(self.manifest))
        self.assertEqual(self.manifest['identity_sha256']['creator'], self.manifest['identity_sha256']['reader'])
        self.assertNotEqual(self.manifest['identity_sha256']['creator'], self.manifest['identity_sha256']['task_creator'])
        self.assertEqual(self.manifest['role_sha256']['creator'], self.manifest['role_sha256']['task_creator'])

    def test_each_environment_permission_is_explicit(self):
        self.approve_fixture(); original = deepcopy(self.manifest)
        for key in ('authorized', *prepare.PERMISSIONS):
            self.manifest = deepcopy(original); self.manifest['environment_preparation'][key] = False; self.save()
            with self.subTest(key=key), self.assertRaises(prepare.E1BBlocked): prepare.check_approval(self.plan,self.auth)

    def test_missing_cluster_identity_blocks_sql_rendering(self):
        self.approve_fixture(); self.manifest['environment_preparation']['expected_system_identifier']=''; self.save()
        prepare.check_approval(self.plan, self.auth)
        with self.assertRaises(prepare.E1BBlocked): prepare.check_approval(self.plan, self.auth, require_cluster=True)

    def test_changed_plan_or_source_pins_block(self):
        self.approve_fixture()
        path=self.folder/'plan.json'; path.write_text(json.dumps(self.plan),encoding='utf-8')
        self.assertEqual(prepare.checked_plan(path),self.plan)
        changed=deepcopy(self.plan); changed['cluster_directory']='C:/other'
        path.write_text(json.dumps(changed),encoding='utf-8')
        with self.assertRaises(prepare.E1BBlocked): prepare.checked_plan(path)
        changed=deepcopy(self.plan); changed['schema_source_sha256']['core/database.py']='0'*64
        path.write_text(json.dumps(changed),encoding='utf-8')
        with self.assertRaises(prepare.E1BBlocked): prepare.checked_plan(path)
        changed=deepcopy(self.plan); changed['port']=55442
        with self.assertRaises(prepare.E1BBlocked): prepare.check_approval(changed,self.auth)

    def test_invalid_scope_and_reserved_ports_are_rejected(self):
        for run,port in (('reviews',55441),('a'*32,5432),('a'*32,55432),('a'*32,1023),('a'*32,65536)):
            with self.subTest(run=run,port=port), self.assertRaises(prepare.E1BBlocked): prepare.make_plan(run,port)

    def test_sql_preserves_source_schema_and_runtime_privilege_boundaries(self):
        self.approve_fixture(); config=prepare.check_approval(self.plan,self.auth,require_cluster=True)
        sql=prepare.render_sql(self.plan,config)
        self.assertIn(prepare.postgres_schema(prepare.BUSINESS_SCHEMA+prepare.LABEL_SCHEMA+prepare.HUMAN_SCHEMA),sql)
        self.assertIn(prepare.postgres_schema(prepare.TASK_SCHEMA),sql)
        self.assertEqual(sql.count('CREATE DATABASE '),2)
        self.assertEqual(sql.count('CREATE ROLE '),4)
        self.assertEqual(sql.count('INSERT INTO public.p6_e1b_test_scope'),2)
        self.assertEqual(sql.count(config.authorization_sha256),2)
        self.assertIn('pg_control_system()',sql)
        self.assertEqual(sql.count('clock_timestamp()'),3)
        self.assertIn('fresh_cluster',sql)
        self.assertNotIn('DROP ',sql.upper()); self.assertNotIn('DELETE FROM ',sql.upper())
        self.assertNotIn('IF NOT EXISTS',sql.upper())
        self.assertNotRegex(sql, r'GRANT (INSERT|UPDATE).*p6_e1b_test_scope')
        self.assertNotRegex(sql, r'GRANT (INSERT|UPDATE).*_reader"')
        self.assertEqual(sql.count('idle_in_transaction_session_timeout'),4)

    def test_injection_and_hba_have_no_fallback_or_automatic_execution(self):
        text=prepare.render_injection(self.plan)
        for name in prepare.ENV_NAMES.values(): self.assertIn('$env:'+name,text)
        self.assertIn('EscapeDataString',text); self.assertIn('-AsSecureString',text)
        self.assertIn('--require-cluster',text)
        self.assertNotIn('DATABASE_URL',text); self.assertNotIn('--run',text)
        self.assertNotIn('Set-Content',text); self.assertNotIn('provision.psql',text)
        hba=prepare.render_hba(self.plan)
        self.assertEqual(hba.count('scram-sha-256'),3)
        self.assertNotIn(' trust',hba)
        self.assertTrue(hba.endswith('host all all ::0/0 reject\n'))

    def test_outputs_cannot_overwrite_an_existing_file(self):
        path=self.folder/'kept'; prepare.write_new(path,'original')
        with self.assertRaises(FileExistsError): prepare.write_new(path,'replacement')
        self.assertEqual(path.read_text(), 'original')


if __name__=='__main__': unittest.main()
