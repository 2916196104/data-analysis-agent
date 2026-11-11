"""F04 full control-flow fault injection. Only temporary synthetic files and FakePopen."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import p6e1b_recovery as r
from scripts import p6e1b_executor as ex
from scripts import prepare_p6e1b_recovery as prep
from test_p6e1b_f02_executor import FakeClock, FakePopen

SID='S-1-5-21-111-222-333-1001'
SYSTEM_ID='8123456789012345678'


class OfflinePlatform(r.NativePlatform):
    def __init__(self,root):
        self.root=root;self.p=r.layout(root);self.calls=[];self.fault=None;self.c=None;self.server=None
        self.private_applied=False;self.sql_sha256=None;self.secrets={k:'OFFLINE-SECRET-'+k for k in ('bootstrap','creator','reader','writer','task')}
        self.original_acls=self.rows(self.p['source'],False);self.init_fake=None;self.client_count=0;self.ps_command=None
        self.pending_clients=[];self.init_runner=None;self.native_job_mode=False
    def record(self,name):
        self.calls.append(name)
        if self.fault==name:raise RuntimeError('synthetic sensitive failure must not be logged')
    def operator_sid(self):return SID if self.fault!='user' else 'S-1-5-21-1-2-3-999'
    def inventory(self,path):return r.inventory_at(self.root,path)
    def rows(self,path,private):
        return [{'path':row['path'],'sections':'Owner,Group,Access',
            'sddl':'O:'+SID+'G:SYD:P(A;;FA;;;SY)','owner':SID,'protected':private,
            'rules':[{'sid':sid,'allow':True,'full_control':True} for sid in sorted(r.PRIVATE_SIDS|{SID})]}
            for row in self.inventory(path)['entries']]
    def acls(self,path):
        if path==self.p['source']:
            rows=deepcopy(self.original_acls)
            if self.fault=='source_acl':rows[0]['sddl']+='(A;;FR;;;WD)'
            return rows
        rows=self.rows(path,self.private_applied)
        if self.fault=='archive_acl':rows[0]['protected']=False
        if self.fault=='archive_unexpected_user':rows[0]['rules'].append({'sid':'S-1-1-0','allow':True,'full_control':True})
        return rows
    def preflight(self):self.record('preflight')
    def move(self,source,destination):
        self.record('move');self.assert_temp(source);self.assert_temp(destination)
        destination.parent.mkdir(parents=True);source.rename(destination)
        if self.fault=='partial_move':source.mkdir()
        if self.fault=='archive_content':(destination/'PG_VERSION').write_text('CORRUPTED')
    def assert_temp(self,path):
        assert self.root!=r.ROOT and self.root in path.parents and r.ROOT not in path.parents
    def restrict_acl(self,path):self.record('restrict_acl');self.private_applied=True
    def credentials(self):return self.secrets
    def initialize(self,grant):
        self.record('initialize');clock=FakeClock();fake=FakePopen(self.p['source'],clock);self.init_fake=fake
        if self.fault=='init_timeout':fake.timeout=True
        if self.fault=='init_output_unknown':fake.out=None
        if self.fault=='init_structure':fake.make_structure=False
        def tree(pid,deadline):return {'kind':'offline_fake','all_exited':self.fault!='init_job','pid':pid}
        runner=ex.InitdbExecutor(self.root,ex.log_directory(self.root,r.ATTEMPT),attempt_id=r.ATTEMPT,
            simulation=True,popen=fake,tree_probe=tree,clock=clock)
        result=runner.run(self.p['source'],self.c.policy,grant,self.root/'.cache/p6e1b-exec/private/bootstrap.pw',list(self.secrets.values()))
        if self.fault=='init_missing_proof':result['completion_marker_verified']=False
        return result
    def control_identity(self):
        self.record('identity')
        return {'system_identifier':'BAD' if self.fault=='identity_invalid' else SYSTEM_ID,
            'cluster_state':'shut down','source':str(self.p['source']),
            'control_sha256':prep.file_sha256(self.p['source']/'global/pg_control')}
    def start(self):
        if self.native_job_mode:return r.NativePlatform.start(self)
        self.record('start');self.server=SimpleNamespace(closed=False)
    def server_owned(self):
        if self.native_job_mode:return r.NativePlatform.server_owned(self)
        return self.server is not None and not self.server.closed
    def live_identity(self,system_id):
        self.record('live_identity');assert system_id==SYSTEM_ID
    def provision(self,context):
        if self.native_job_mode:return r.NativePlatform.provision(self,context)
        self.record('provision');assert self.server_owned()
        if self.fault=='early_close':self.server.closed=True
    def live(self,context):self.record('live');assert self.server_owned()
    def pg_case(self,name,context):
        self.record(name);assert self.server_owned()
        return {'case':name,'status':'FAIL' if self.fault==name+'_failure' else 'PASS',
            'tests_run':1,'failures':1 if self.fault==name+'_failure' else 0,'errors':0,'skipped':0}
    def stop(self):
        if self.native_job_mode:
            self.calls.append('stop');return r.NativePlatform.stop(self)
        self.calls.append('stop')
        if self.server:self.server.closed=True
        return {'owned_only':True,'released':self.fault!='cleanup'}


class RecoveryEntryTests(unittest.TestCase):
    def setUp(self):
        for name in ('subprocess.Popen','subprocess.run','socket.socket.connect','socket.socket.bind','psycopg.connect',
                     'openai.OpenAI','httpx.Client.send','httpx.AsyncClient.send'):
            guard=self.enterContext(patch(name,side_effect=AssertionError('real operation prohibited')))
            self.addCleanup(guard.assert_not_called)
        self.root=Path(self.enterContext(TemporaryDirectory(prefix='p6e1b-entry-offline-')))
        extras=['.cache/p6e1b-f03/native-summary.json','.cache/p6e1b-f03/native-evidence.json',
                'tests/p6e1b_f03_native.py','tests/fixtures/p6e1b_safe_child.py']
        env='.cache/p6e1b-env-review/'+ex.RUN_ID+'/'
        schema_files=prep.read(r.ROOT/(env+'plan.json'))['schema_source_sha256']
        extras.extend('src/data_analysis_agent/'+name for name in schema_files)
        for name in (*r.CODE_FILES,*prep.HISTORY_FILES,*extras,env+'plan.json',env+'authorization.approved.json'):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((r.ROOT/name).read_bytes())
        self.p=r.layout(self.root);source=self.p['source'];(source/'global').mkdir(parents=True)
        (source/'PG_VERSION').write_text('18\n');(source/'global/pg_control').write_bytes(b'OFFLINE-OLD-PARTIAL')
        private=self.root/'.cache/p6e1b-exec/private';private.mkdir(parents=True,exist_ok=True)
        (private/'bootstrap.pw').write_text('OFFLINE-SECRET-bootstrap\n')
        self.fake=OfflinePlatform(self.root);source_manifest=self.fake.inventory(source)
        self.p['private'].mkdir(parents=True);prep.write_new(self.p['private']/'source-files.json',source_manifest)
        prep.write_new(self.p['private']/'source-acl.json',r.source_acls(source_manifest,self.fake.acls(source)))
        self.p['work'].mkdir(parents=True);self.policy=r.policy_v2()
        # The original fixed plan stays byte-identical. Resource paths are shadowed only in simulation.
        bound=prep.bindings(r.ROOT)
        with patch.object(prep,'bindings',return_value=bound):self.plan=r.pending_plan(self.root,SID)
        self.plan.update(authorized=True,approved_by='OFFLINE-UNIT-ONLY',expires_at='2099-01-01T00:00:00+00:00',
            permissions={k:True for k in prep.OPERATIONS})
        self.plan_file=self.p['work']/'recovery-plan.approved.json';self.policy_file=self.p['work']/'recovery-policy.approved.json'
        self.save();self.original_history={name:prep.file_sha256(self.root/name) for name in prep.HISTORY_FILES}
    def save(self):
        self.plan_file.write_text(json.dumps(self.plan),encoding='utf-8');self.policy_file.write_text(json.dumps(self.policy),encoding='utf-8')
    def coordinator(self):
        c=r.Coordinator(self.root,self.plan_file,self.policy_file,simulation=True,platform=self.fake);self.fake.c=c;return c
    def run_case(self):return self.coordinator().run()
    def failed_before(self,fault,next_stage):
        self.fake.fault=fault;result=self.run_case();self.assertEqual(result['state'],'failed')
        self.assertNotIn(next_stage,self.fake.calls);return result

    def test_full_serial_chain_and_owned_server_cleanup(self):
        result=self.run_case();self.assertEqual(result['state'],'completed');self.assertTrue(result['simulation'])
        self.assertEqual(result['verified_stages'],list(r.STAGES));self.assertEqual(self.fake.calls[-1],'stop')
        self.assertLess(self.fake.calls.index('start'),self.fake.calls.index('PG08'));self.assertTrue(self.fake.server.closed)
        for name,sha in self.original_history.items():self.assertEqual(prep.file_sha256(self.root/name),sha)
    def test_no_authorization_cannot_create_attempt(self):
        self.plan['authorized']=False;self.save()
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertFalse(self.p['attempt'].exists());self.assertTrue(self.p['source'].exists())
    def test_each_operation_requires_permission_before_move(self):
        for name in prep.OPERATIONS:
            self.plan['permissions'][name]=False;self.save()
            with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
            self.plan['permissions'][name]=True
        self.assertEqual(self.fake.calls,[]);self.assertFalse(self.p['attempt'].exists())
    def test_expired_or_missing_approver_rejected(self):
        for fields in ({'expires_at':'2020-01-01T00:00:00+00:00'},{'approved_by':''}):
            old=deepcopy(self.plan);self.plan.update(fields);self.save()
            with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
            self.plan=old
    def test_unknown_existing_attempt_never_reused(self):
        self.p['attempt'].mkdir(parents=True);(self.p['attempt']/'state.json').write_text('unknown')
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertEqual((self.p['attempt']/'state.json').read_text(),'unknown')
    def test_failed_attempt_cannot_be_bypassed_with_new_coordinator(self):
        self.failed_before('init_timeout','start')
        before=prep.file_sha256(self.p['attempt']/'result.json')
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertEqual(prep.file_sha256(self.p['attempt']/'result.json'),before)
    def test_completed_attempt_cannot_repeat(self):
        self.run_case()
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
    def test_original_history_is_not_rewritten_on_failure(self):
        self.failed_before('move','initialize')
        for name,sha in self.original_history.items():self.assertEqual(prep.file_sha256(self.root/name),sha)
    def test_source_content_change_blocks_move(self):
        (self.p['source']/'PG_VERSION').write_text('changed')
        result=self.run_case();self.assertEqual(result['state'],'failed');self.assertNotIn('move',self.fake.calls)
    def test_source_acl_change_blocks_move(self):self.failed_before('source_acl','move')
    def test_partial_archive_blocks_initdb(self):self.failed_before('partial_move','initialize')
    def test_archive_content_corruption_blocks_initdb(self):self.failed_before('archive_content','initialize')
    def test_archive_permissions_must_be_verified(self):self.failed_before('archive_acl','initialize')
    def test_existing_archive_cannot_be_overwritten(self):
        self.p['archive'].mkdir(parents=True);(self.p['archive']/'keep').write_text('KEEP')
        self.failed_before('unused','initialize');self.assertNotIn('move',self.fake.calls)
        self.assertEqual((self.p['archive']/'keep').read_text(),'KEEP')
    def test_json_claim_of_archive_success_cannot_unlock_attempt(self):
        self.plan['future_facts']['archive_completed']=True;self.save()
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertFalse(self.p['attempt'].exists())
    def test_receipt_generated_from_post_archive_observations(self):
        self.run_case();receipt=prep.read(self.p['attempt']/'archive-receipt.json')
        self.assertTrue(receipt['archive_completed']);self.assertTrue(receipt['simulation'])
        self.assertEqual(receipt['original_inventory_sha256'],self.plan['source_inventory_sha256'])
        self.assertTrue(self.p['archive'].is_dir());self.assertNotEqual(receipt['observed_archive_acl_sha256'],self.plan['source_acl_sha256'])
    def test_initdb_timeout_stops_after_finite_fake_wait(self):
        self.failed_before('init_timeout','identity');self.assertEqual(len(self.fake.init_fake.calls),1)
    def test_unknown_output_blocks_start(self):self.failed_before('init_output_unknown','start')
    def test_missing_structure_blocks_start(self):self.failed_before('init_structure','start')
    def test_job_failure_blocks_start(self):self.failed_before('init_job','start')
    def test_missing_completed_proof_blocks_identity(self):self.failed_before('init_missing_proof','identity')
    def test_invalid_system_identifier_blocks_render(self):self.failed_before('identity_invalid','start')
    def test_wrong_live_system_identifier_stops_provision(self):
        def wrong(system_id):self.fake.calls.append('wrong_live_identity');raise ex.RecoveryBlocked('wrong_identity')
        self.fake.live_identity=wrong;result=self.run_case()
        self.assertEqual(result['state'],'failed');self.assertNotIn('provision',self.fake.calls)
    def test_server_must_stay_owned_until_pg_finishes(self):self.failed_before('early_close','PG01')
    def test_pg07_failure_blocks_pg08_and_preserves_result(self):
        result=self.failed_before('PG07_failure','PG08');self.assertIn('PG07',self.fake.calls)
        self.assertNotIn('PG07',result['verified_stages']);self.assertTrue(self.fake.server.closed)
    def test_cleanup_failure_prevents_parent_completion(self):
        self.fake.fault='cleanup';result=self.run_case();self.assertEqual(result['state'],'failed');self.assertFalse(result['cleanup']['released'])
    def test_user_identity_must_match(self):
        self.fake.fault='user'
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertFalse(self.p['attempt'].exists())
    def test_database_and_role_substitution_rejected(self):
        for key,value in (('databases',{'business':'reviews','tasks':'tasks'}),('roles',{'reader':'postgres'})):
            old=deepcopy(self.plan);self.plan[key]=value;self.save()
            with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
            self.plan=old
    def test_run_attempt_port_source_and_archive_cannot_change(self):
        for key,value in (('run_id','b'*32),('attempt_id','b'*32),('port',5432),('route','A'),('source',str(self.root/'reviews')),('archive',str(self.p['source']))):
            old=deepcopy(self.plan);self.plan[key]=value;self.save()
            with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
            self.plan=old
    def test_changed_code_sha_invalidates_approval(self):
        (self.root/'scripts/p6e1b_recovery.py').write_text('changed')
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
    def test_changed_history_or_old_approval_invalidates_new_plan(self):
        (self.root/prep.HISTORY_FILES[0]).write_text('changed')
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
    def test_approval_file_mutation_after_validation_stops_all_operations(self):
        coordinator=self.coordinator();self.plan_file.write_text(self.plan_file.read_text()+' ')
        with self.assertRaises(ex.RecoveryBlocked):coordinator.run()
        self.assertFalse(self.p['attempt'].exists())
    def test_policy_change_requires_new_approval(self):
        self.policy['execution_seconds']=181;self.save()
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
    def test_native_receipt_tampering_rejected(self):
        (self.root/'.cache/p6e1b-f03/native-summary.json').write_text('{}')
        with self.assertRaises((ex.RecoveryBlocked,r.jobs.JobControlError)):self.coordinator()
    def test_pending_filename_not_accepted_as_approval(self):
        with self.assertRaises(ex.RecoveryBlocked):r.Coordinator(self.root,self.p['work']/'recovery-plan.pending.json',self.policy_file,simulation=True,platform=self.fake)
    def test_simulation_artifacts_never_unlock_native_completion(self):
        result=self.run_case()
        with self.assertRaises(ex.RecoveryBlocked):r.require_native_result(result)
        with self.assertRaises(ex.RecoveryBlocked):ex.require_completed(prep.read(self.p['attempt']/'initdb-result.json')['result'])
    def test_simulation_cannot_use_actual_workspace(self):
        with self.assertRaises(ex.RecoveryBlocked):r.validate_plan(r.ROOT,self.plan,self.policy,approved=True,simulation=True)
    def test_native_cannot_use_injected_platform_or_temporary_root(self):
        with self.assertRaises(ex.RecoveryBlocked):r.Coordinator(self.root,self.plan_file,self.policy_file,platform=self.fake)
    def test_private_passfile_and_sql_do_not_publish_secrets(self):
        self.run_case();private=(self.p['attempt']/'provision.private.psql').read_text();review=(self.p['attempt']/'provision.review.psql').read_text()
        self.assertIn('passfile',private);self.assertNotIn('\\password ',private)
        self.assertIn('-reuse-previous=off',private)
        for secret in self.fake.secrets.values():
            self.assertNotIn(secret,review)
            self.assertNotIn(secret,(self.p['attempt']/'result.json').read_text())
    def test_no_environment_database_fallback(self):
        with patch.dict('os.environ',{'DATABASE_URL':'postgresql://unit.invalid/reviews','PGPORT':'5432'}):result=self.run_case()
        self.assertEqual(result['state'],'completed')
    def test_missing_observed_receipt_cannot_initialize(self):
        c=self.coordinator()
        with self.assertRaises(ex.RecoveryBlocked):c.runtime_grant()
        self.assertNotIn('initialize',self.fake.calls)
    def test_archive_unexpected_user_is_rejected(self):self.failed_before('archive_unexpected_user','initialize')
    def test_native_client_whitelist_rejects_arbitrary_command(self):
        self.coordinator()
        with self.assertRaises(ex.RecoveryBlocked):self.fake.short(['cmd.exe','/c','anything'],1,'provision')
    def test_native_powershell_command_cannot_be_injected_via_short(self):
        self.coordinator()
        with self.assertRaises(ex.RecoveryBlocked):self.fake.short(['powershell.exe','-Command','anything'],1,'acl')
    def test_approval_changed_after_move_blocks_initdb(self):
        original=self.fake.move
        def move(source,destination):original(source,destination);self.plan_file.write_text(self.plan_file.read_text()+' ')
        self.fake.move=move;result=self.run_case()
        self.assertEqual(result['state'],'failed');self.assertNotIn('initialize',self.fake.calls)
    def test_native_adapter_server_job_is_held_through_pg08(self):
        self.fake.native_job_mode=True;owned=[];commands=[]
        platform=self.fake
        class FakeJob:
            def __init__(self,argv,**kw):
                commands.append(list(argv));self.closed=False;self.job=object();self.drained=False;self.active=argv[-1]=='start'
                self.pid=4242;self.output=None
                if self.active:
                    (platform.p['source']/'postmaster.pid').write_text('5555\n');owned.append(self)
                if argv[-1]=='stop':owned[0].active=False
            def communicate(self,timeout):self.drained=True;self.output=('OK','');return self.output
            def poll(self):return 0
            def members(self):return [5555] if self.active else []
            def accounting(self):return {'active':1 if self.active else 0}
            def verify_and_close(self,deadline):
                if self.active:raise AssertionError('server Job closed before authorized stop')
                self.closed=True;return {'all_exited':True,'handles_released':True,'reader_threads_finished':True}
            def emergency_cleanup(self,deadline):self.active=False;self.closed=True;return True
            def terminate(self):self.active=False
            def wait(self,timeout):return 0
        with patch.object(r.jobs,'OwnedJobProcess',FakeJob):result=self.run_case()
        self.assertEqual(result['state'],'completed');self.assertTrue(owned[0].closed)
        self.assertEqual([Path(c[0]).name for c in commands],['pg_ctl.exe','psql.exe','pg_ctl.exe'])
        for cmd in commands:
            for secret in self.fake.secrets.values():self.assertNotIn(secret,' '.join(cmd))
        self.assertIn('passfile=',commands[1][4]);self.assertTrue(result['cleanup']['graceful'])
    def test_native_identity_adapter_checks_actual_row(self):
        self.coordinator();self.fake.native_job_mode=True
        self.fake.server=SimpleNamespace(closed=False,job=object(),members=lambda:[5555],accounting=lambda:{'active':1})
        (self.p['source']/'postmaster.pid').write_text('5555\n')
        row=('postgres',self.plan['bootstrap_role'],'127.0.0.1',55441,str(self.p['source']),18,SYSTEM_ID)
        connection=SimpleNamespace(execute=lambda q:SimpleNamespace(fetchone=lambda:row))
        class Context:
            def __enter__(self):return connection
            def __exit__(self,*args):return False
        with patch('psycopg.connect',return_value=Context()) as connect:
            r.NativePlatform.live_identity(self.fake,SYSTEM_ID)
            with self.assertRaises(ex.RecoveryBlocked):r.NativePlatform.live_identity(self.fake,'99999999999999')
        self.assertEqual(connect.call_count,2)
    def test_direct_stage_call_cannot_skip_attempt_ledger(self):
        c=self.coordinator()
        for action in (c.archive,c.initialize,c.observe_identity,c.make_authorization):
            with self.assertRaises(ex.RecoveryBlocked):action()
        self.assertEqual(self.fake.calls,[]);self.assertFalse(self.p['attempt'].exists())
    def test_pending_init_resource_cannot_be_declared_released(self):
        self.coordinator()
        self.fake.pending_clients=[SimpleNamespace(closed=False,emergency_cleanup=lambda deadline:False)]
        self.assertFalse(r.NativePlatform.stop(self.fake)['released'])
    def test_late_live_stage_cannot_start_pg_cases(self):
        now=[0.0]
        def late(context):self.fake.record('live');now[0]+=61
        self.fake.live=late
        with patch.object(r.time,'monotonic',side_effect=lambda:now[0]):result=self.run_case()
        self.assertEqual(result['state'],'failed');self.assertNotIn('PG01',self.fake.calls)
    def test_duplicate_approval_keys_are_rejected_before_attempt_creation(self):
        text=self.plan_file.read_text();self.plan_file.write_text('{"authorized":false,'+text[1:])
        with self.assertRaises(ValueError):self.coordinator()
        self.assertFalse(self.p['attempt'].exists())
    def test_native_port_probe_does_not_treat_query_error_as_unused(self):
        self.coordinator();commands=[]
        def observe(body,*args,**kwargs):
            commands.append(body);return {'port_unused':True,'no_postgresql_processes':True}
        self.fake.powershell=observe;r.NativePlatform.preflight(self.fake)
        self.assertIn('Get-NetTCPConnection -ErrorAction Stop',commands[0])
        self.assertNotIn('SilentlyContinue',commands[0])
    def test_frozen_schema_source_change_rejects_old_plan(self):
        (self.root/'src/data_analysis_agent/jobs/store.py').write_text('changed schema')
        with self.assertRaises(ex.RecoveryBlocked):self.coordinator()
        self.assertFalse(self.p['attempt'].exists())


if __name__=='__main__':unittest.main()
