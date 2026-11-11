"""Read-only preparation and current closed gates; no PostgreSQL or native child."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import prepare_p6e1b_recovery as prep
from scripts import p6e1b_executor as ex


ATTEMPT='a'*32


class RecoveryPreparationTests(unittest.TestCase):
    def setUp(self):
        for name in ('subprocess.Popen','subprocess.run','socket.socket.connect','socket.socket.bind','psycopg.connect'):
            guard=self.enterContext(patch(name,side_effect=AssertionError('F04 forbids real execution')))
            self.addCleanup(guard.assert_not_called)
        self.root=Path(self.enterContext(TemporaryDirectory(prefix='p6e1b-f04-unit-')))
        self.source=ex.target_directory(self.root);self.source.mkdir(parents=True)
        (self.source/'global').mkdir();(self.source/'PG_VERSION').write_text('18\n')
        (self.source/'global/pg_control').write_bytes(b'SYNTHETIC-NOT-A-CLUSTER')
        self.manifest=prep.inventory(self.root)
        self.raw=[{'path':row['path'],'sections':'Owner,Group,Access','sddl':'O:SYG:SYD:P(A;;FA;;;SY)'}
            for row in self.manifest['entries']]
        self.acls=prep.checked_acls(self.manifest,self.raw)
        self.bound={'native_validation':{'tree_provider_sha256':'1'*64,'tree_validation_sha256':'2'*64,'tree_evidence_sha256':'3'*64},
            'code_sha256':{'OFFLINE-ONLY':'4'*64},'historical_evidence_sha256':{'OFFLINE-ONLY':'5'*64},
            'original_authorization_sha256':'6'*64,
            'environment_plan':{'databases':{'business':'fixed-business','tasks':'fixed-tasks'},
                                'roles':{'reader':'fixed-reader'}}}
    def make(self):return prep.make_draft(self.root,ATTEMPT,self.manifest,self.acls,self.bound)

    def test_fixed_source_and_disjoint_archive(self):
        paths=prep.paths(self.root,ATTEMPT)
        self.assertEqual(paths['source'],self.source);self.assertNotIn(self.source,paths['archive'].parents)
        self.assertNotIn(paths['archive'],self.source.parents);self.assertEqual(paths['source'].drive,paths['archive'].drive)
        self.assertIn(ex.RUN_ID,str(paths['archive']));self.assertIn(ATTEMPT,str(paths['archive']))
    def test_invalid_or_pathlike_attempt_rejected(self):
        for attempt in ('',None,'../reviews','g'*32,'a'*31,'a'*33):
            with self.assertRaises(prep.PreparationBlocked):prep.paths(self.root,attempt)
    def test_outside_or_workspace_root_not_a_target(self):
        for path in (self.root,self.root.parent/'reviews'):
            with self.assertRaises(prep.PreparationBlocked):prep.plain_path(self.root,path)
    def test_missing_source_has_no_fallback(self):
        # A separate temporary root has no cluster; no automatic directory creation.
        with TemporaryDirectory() as other:
            with self.assertRaises(prep.PreparationBlocked):prep.inventory(Path(other))
            self.assertFalse(ex.target_directory(Path(other)).exists())
    def test_inventory_covers_root_files_directories_and_hashes(self):
        entries={r['path']:r for r in self.manifest['entries']}
        self.assertEqual(set(entries),{'.','global','global/pg_control','PG_VERSION'})
        self.assertEqual(entries['global/pg_control']['sha256'],prep.file_sha256(self.source/'global/pg_control'))
        self.assertIsNone(entries['.']['sha256']);self.assertIsNone(entries['.']['bytes'])
    def test_changed_content_changes_manifest(self):
        (self.source/'global/pg_control').write_bytes(b'CHANGED')
        self.assertNotEqual(self.manifest,prep.inventory(self.root))
    def test_manifest_reading_does_not_modify_content_or_mtime(self):
        self.assertEqual(self.manifest,prep.inventory(self.root))
    def test_missing_acl_entry_rejected(self):
        with self.assertRaises(prep.PreparationBlocked):prep.checked_acls(self.manifest,self.raw[:-1])
    def test_duplicate_acl_entry_rejected(self):
        with self.assertRaises(prep.PreparationBlocked):prep.checked_acls(self.manifest,self.raw+[self.raw[0]])
    def test_extra_acl_path_rejected(self):
        with self.assertRaises(prep.PreparationBlocked):prep.checked_acls(self.manifest,self.raw+[{'path':'../reviews'}])
    def test_acl_requires_owner_group_access_sections_and_dacl(self):
        for change in ({'sections':'Access'},{'sddl':'O:SYG:SY'},{'sddl':'O:SYD:P(A;;FA;;;SY)'},{'sddl':''}):
            raw=deepcopy(self.raw);raw[0].update(change)
            with self.assertRaises(prep.PreparationBlocked):prep.checked_acls(self.manifest,raw)
    def test_acl_binding_and_sacl_limit_explicit(self):
        self.assertEqual(self.acls['source_inventory_sha256'],ex.sha256(self.manifest))
        self.assertIn('NOT COLLECTED',self.acls['sacl'])
    def test_pending_contains_no_approver_expiry_or_approval(self):
        policy,plan=self.make()
        self.assertFalse(plan['authorized']);self.assertEqual(plan['approved_by'],'');self.assertEqual(plan['expires_at'],'')
        self.assertTrue(all(v is False for v in plan['permissions'].values()))
        for field in ('archive_authorized','reinitialize_authorized','archive_completed','tree_strategy_reviewed'):
            self.assertFalse(plan[field])
    def test_original_failure_and_source_versions_are_bound(self):
        _,plan=self.make()
        self.assertEqual(plan['bindings']['historical_evidence_sha256'],self.bound['historical_evidence_sha256'])
        self.assertEqual(plan['bindings']['code_sha256'],self.bound['code_sha256'])
        self.assertEqual(plan['attempt_id'],ATTEMPT);self.assertEqual(plan['run_id'],ex.RUN_ID)
    def test_policy_finite_and_is_not_execution_permission(self):
        policy,plan=self.make();parsed=ex.Policy.parse(policy)
        self.assertEqual(parsed.execution_seconds,180);self.assertEqual(parsed.termination_seconds,10)
        self.assertEqual(parsed.cleanup_seconds,30);self.assertEqual(parsed.max_log_bytes,1048576)
        with self.assertRaises(ex.RecoveryBlocked):ex.check_grant(self.root,self.source,policy,plan)
    def test_source_or_acl_scope_mismatch_rejected(self):
        self.manifest['source']=str(self.root/'reviews')
        with self.assertRaises(prep.PreparationBlocked):self.make()
    def test_old_acl_digest_rejected(self):
        self.acls['source_inventory_sha256']='0'*64
        with self.assertRaises(prep.PreparationBlocked):self.make()
    def test_existing_archive_is_never_overwritten(self):
        p=prep.paths(self.root,ATTEMPT)['archive'];p.mkdir(parents=True);(p/'keep').write_text('keep')
        with self.assertRaises(prep.PreparationBlocked):self.make()
        self.assertEqual((p/'keep').read_text(),'keep')
    def test_snapshot_does_not_move_source_or_create_archive(self):
        result=prep.snapshot(self.root,ATTEMPT);p=prep.paths(self.root,ATTEMPT)
        self.assertFalse(result['archive_moved']);self.assertFalse(p['archive'].exists())
        self.assertEqual(prep.inventory(self.root),self.manifest)
    def test_snapshot_repetition_rejected_not_new_attempt_or_reset(self):
        prep.snapshot(self.root,ATTEMPT)
        with self.assertRaises(prep.PreparationBlocked):prep.snapshot(self.root,ATTEMPT)
        self.assertEqual(prep.inventory(self.root),self.manifest)
    def test_write_new_cannot_overwrite_approved_or_history_files(self):
        path=self.root/'history.json';path.write_text('original')
        with self.assertRaises(FileExistsError):prep.write_new(path,{'authorized':True})
        self.assertEqual(path.read_text(),'original')
    def test_capture_script_only_reads_fixed_source_acl(self):
        p=prep.paths(self.root,ATTEMPT);script=prep.acl_capture_script(p['source'],p['private']/'source-files.json',p['private']/'source-acl.raw.json')
        for forbidden in ('Set-Acl','Move-Item','Remove-Item','initdb.exe','pg_ctl.exe','psql.exe','DATABASE_URL'):
            self.assertNotIn(forbidden,script)
        self.assertIn('CreateNew',script);self.assertIn('ReparsePoint',script);self.assertIn('Owner',script)
    def test_known_entry_gaps_are_not_presented_as_implemented(self):
        _,plan=self.make();self.assertIn('not implemented',plan['entry_readiness'])
        self.assertEqual(set(plan['required_entry_changes']),set(prep.ENTRY_GAPS))
        self.assertFalse(plan['runtime_facts']['archive_completed']);self.assertIsNone(plan['runtime_facts']['new_system_identifier'])
    def test_database_environment_cannot_change_pending_scope(self):
        with patch.dict('os.environ',{'DATABASE_URL':'postgresql://unit.invalid/reviews','PGPORT':'5432'}):_,plan=self.make()
        self.assertEqual(plan['target_directory'],str(self.source));self.assertEqual(plan['port'],55441)
    def test_no_runtime_or_destructive_cli_stage(self):
        for name in ('archive','initialize','start','provision','live','tests','approve'):
            with self.assertRaises(SystemExit):prep.main([name,'--attempt-id',ATTEMPT])
    def test_legacy_downstream_gates_remain_closed(self):
        spec=importlib.util.spec_from_file_location('f04_legacy_entry',prep.ROOT/'.cache/p6e1b-exec/execute.py')
        entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
        for name in ('initialize','render','start','provision','live','authorize'):
            with self.assertRaises(ex.RecoveryBlocked):getattr(entry,name)()
        with self.assertRaises(ex.RecoveryBlocked):entry.command('pg_ctl',['start'])


if __name__=='__main__':unittest.main()
