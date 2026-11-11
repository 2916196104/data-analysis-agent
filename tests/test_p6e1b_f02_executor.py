"""Formal offline fault injection: no native programs, network or database calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import p6e1b_executor as ex


class FakeClock:
    def __init__(self):self.now=0.0
    def monotonic(self):return self.now
    def utc(self):return (datetime(2026,11,10,tzinfo=timezone.utc)+timedelta(seconds=self.now)).isoformat()


class FakeProcess:
    pid=4242
    def __init__(self,owner):
        self.owner=owner;self.returncode=None;self.communicate_calls=[];self.terminations=0;self.waits=[]
        self.stdout=None;self.stderr=None
    def poll(self):return self.returncode
    def communicate(self,timeout):
        f=self.owner;self.communicate_calls.append(timeout)
        if len(self.communicate_calls)==1:
            if f.timeout:
                f.clock.now+=timeout
                raise subprocess.TimeoutExpired('OFFLINE-ONLY',timeout,output=f.out,stderr=f.err)
            f.clock.now+=f.work_seconds
            if f.make_structure:f.structure()
            self.returncode=f.exit_code
            return f.out,f.err
        if f.hold_output:
            f.clock.now+=timeout
            raise subprocess.TimeoutExpired('OFFLINE-ONLY',timeout,output=None,stderr=None)
        return f.drain_out,f.drain_err
    def terminate(self):
        self.terminations+=1
        if self.owner.terminate_error:raise PermissionError('synthetic secret must never be recorded')
    def wait(self,timeout):
        self.waits.append(timeout)
        if self.owner.wait_timeout:
            self.owner.clock.now+=timeout
            raise subprocess.TimeoutExpired('OFFLINE-ONLY',timeout)
        self.owner.clock.now+=0.1;self.returncode=-15;return self.returncode


class FakePopen:
    def __init__(self,target,clock):
        self.target=target;self.clock=clock;self.calls=[];self.instances=[];self.timeout=False
        self.out=ex.SUCCESS_MARKER+' using: unit-only';self.err='';self.drain_out=self.out;self.drain_err=''
        self.exit_code=0;self.work_seconds=0.25;self.make_structure=True
        self.hold_output=False;self.terminate_error=False;self.wait_timeout=False;self.create_delay=0
    def __call__(self,argv,**kwargs):
        self.calls.append((argv,kwargs));self.clock.now+=self.create_delay
        process=FakeProcess(self);self.instances.append(process);return process
    def structure(self):
        for name in ('base/1','base/4','base/5','global','pg_wal'):(self.target/name).mkdir(parents=True,exist_ok=True)
        (self.target/'PG_VERSION').write_text('18\n')
        (self.target/'global/pg_control').write_bytes(b'0'*8192)
        for name in ('postgresql.conf','pg_hba.conf'):(self.target/name).write_text('OFFLINE ONLY')


class ExecutorFaultTests(unittest.TestCase):
    def setUp(self):
        for target in ('subprocess.Popen','subprocess.run','socket.socket.connect','socket.socket.bind','psycopg.connect'):
            blocked=self.enterContext(patch(target,side_effect=AssertionError('real operation forbidden')))
            self.addCleanup(blocked.assert_not_called)
        self.root=Path(self.enterContext(TemporaryDirectory(prefix='p6e1b-f02-unit-')))
        self.target=ex.target_directory(self.root);self.private=self.root/'.cache/p6e1b-exec/private/f02'
        self.password_file=self.private.parent/'bootstrap.pw';self.password_file.parent.mkdir(parents=True)
        self.password_file.write_text('UNIT-ONLY-PASSWORD\n')
        self.clock=FakeClock();self.fake=FakePopen(self.target,self.clock)
        self.policy={'version':ex.POLICY_VERSION,'execution_seconds':3,'termination_seconds':1,'cleanup_seconds':2,'max_log_bytes':4096}
        self.grant=ex.pending_recovery(self.root,self.policy)
        self.grant.update(authorized=True,approved_by='OFFLINE-UNIT-ONLY',expires_at='2099-01-01T00:00:00+00:00',
            reinitialize_authorized=True,archive_authorized=True,archive_completed=True,archive_evidence_sha256='a'*64,
            tree_strategy_reviewed=True,tree_provider_sha256='b'*64,route='B')
        self.tree_exited=True;self.tree_delay=0
        self.runner=ex.InitdbExecutor(self.root,self.private,clock=self.clock,popen=self.fake,tree_probe=self.tree,simulation=True)
    def tree(self,pid,deadline):
        self.clock.now+=self.tree_delay
        return {'kind':'offline_fake','all_exited':self.tree_exited,'pid':pid}
    def run_case(self,**kwargs):
        return self.runner.run(kwargs.get('target',self.target),kwargs.get('policy',self.policy),
            kwargs.get('grant',self.grant),self.password_file,kwargs.get('secrets',()))
    def categories(self,result):return {e['category'] for e in result['errors']}
    def log_text(self):return '\n'.join(p.read_text(encoding='utf-8') for p in self.private.glob('*') if p.is_file())

    def test_normal_zero_exit_requires_full_contract(self):
        result=self.run_case()
        self.assertEqual(result['state'],'completed');self.assertEqual(result['returncode'],0)
        self.assertTrue(result['structure_verified']);self.assertTrue(result['completion_marker_verified'])
        argv,options=self.fake.calls[0]
        self.assertEqual(options['stdin'],subprocess.DEVNULL)
        self.assertEqual(options['stdout'],subprocess.PIPE);self.assertEqual(options['stderr'],subprocess.PIPE)
        self.assertIn('--pwfile',argv);self.assertNotIn('-W',argv)
        self.assertNotIn('UNIT-ONLY-PASSWORD',' '.join(argv))
        self.assertTrue(all('utc' in e and 'monotonic' in e for e in result['events']))
        self.assertEqual(result['pid'],4242)

    def test_nonzero_exit_is_not_client_timeout(self):
        self.fake.exit_code=2;result=self.run_case()
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertEqual(result['failure_state'],'failed')
        self.assertIn('process_nonzero_exit',self.categories(result));self.assertNotIn('client_process_timeout',self.categories(result))

    def test_timeout_bytes_and_str_outputs_are_preserved(self):
        self.fake.timeout=True;self.fake.out=b'partial bytes';self.fake.err='partial string'
        self.fake.drain_out=None;self.fake.drain_err=None
        result=self.run_case()
        self.assertEqual(result['failure_state'],'timed_out')
        self.assertIn('partial bytes',self.log_text());self.assertIn('partial string',self.log_text())
        self.assertEqual(result['logs']['stdout']['raw_byte_length'],13)
        self.assertTrue(result['logs']['stdout']['available'])
        self.assertEqual(self.fake.instances[0].terminations,1)
        self.assertTrue(all(t is not None and t>0 for t in self.fake.instances[0].communicate_calls))

    def test_timeout_none_is_unknown_not_empty_output(self):
        self.fake.timeout=True;self.fake.out=None;self.fake.err=None;self.fake.drain_out=None;self.fake.drain_err=None
        result=self.run_case()
        for meta in result['logs'].values():
            self.assertFalse(meta['available']);self.assertIsNone(meta['raw_byte_length'])
            self.assertIsNone(meta['saved_byte_length']);self.assertIsNone(meta['path'])
        self.assertEqual(list(self.private.glob('*.stdout')),[])

    def test_empty_string_is_available_zero_bytes(self):
        result=self.run_case();meta=result['logs']['stderr']
        self.assertTrue(meta['available']);self.assertEqual(meta['raw_byte_length'],0)
        self.assertEqual(meta['saved_byte_length'],0);self.assertTrue(meta['persisted'])

    def test_sensitive_partial_outputs_are_redacted_before_saving(self):
        secret='SYNTHETIC-LONG-SECRET-VALUE'
        self.fake.timeout=True
        self.fake.out=(f'prefix {secret}\npostgresql://user:password@unit.invalid/reviews\n'
                       'api_key="UNLISTED-API-VALUE"\nAuthorization: Basic BASIC-VALUE\n'+secret[:12]).encode()
        self.fake.err='Bearer AUTH-VALUE';self.fake.drain_out=None;self.fake.drain_err=None
        result=self.run_case(secrets=[secret]);text=self.log_text()
        for forbidden in (secret,secret[:12],'postgresql://','UNLISTED-API-VALUE','BASIC-VALUE','AUTH-VALUE'):
            self.assertNotIn(forbidden,text)
        self.assertTrue(result['logs']['stdout']['available'])

    def test_termination_failure_is_cleanup_incomplete(self):
        self.fake.timeout=True;self.fake.terminate_error=True;self.fake.out='timeout-before-terminate-failure'
        result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete')
        self.assertFalse(result['direct_exited']);self.assertIn('resource_cleanup_failed',self.categories(result))
        self.assertIn('timeout-before-terminate-failure',self.log_text())
        self.assertTrue(result['logs']['stdout']['available'])

    def test_descendant_holding_output_does_not_cause_unbounded_communicate(self):
        self.fake.timeout=True;self.fake.hold_output=True
        result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete');self.assertTrue(result['direct_exited'])
        self.assertFalse(result['output_drained']);self.assertTrue(self.runner.pending_resources)
        self.assertIn('resource_cleanup_timeout',self.categories(result))
        self.assertTrue(all(v is not None for v in self.fake.instances[0].communicate_calls))

    def test_log_write_failure_is_separate_and_keeps_length_metadata(self):
        original=Path.open
        def deny(path,*args,**kwargs):
            if path.parent==self.private and path.suffix=='.stdout':raise OSError('synthetic log failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',deny):result=self.run_case()
        self.assertEqual(result['failure_state'],'failed');self.assertIn('log_collection_failed',self.categories(result))
        self.assertTrue(result['logs']['stdout']['available']);self.assertGreater(result['logs']['stdout']['raw_byte_length'],0)
        self.assertIsNone(result['logs']['stdout']['saved_byte_length'])

    def test_tree_cleanup_deadline_cannot_accept_late_verification(self):
        self.tree_delay=3;result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete');self.assertFalse(result['tree_verified'])
        self.assertIn('resource_cleanup_timeout',self.categories(result))

    def test_late_log_collection_cannot_publish_completed(self):
        original=Path.open
        def delayed(path,*args,**kwargs):
            if path.parent==self.private and path.suffix=='.stdout':self.clock.now+=3
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',delayed):result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete')
        self.assertIn('resource_cleanup_timeout',self.categories(result))

    def test_existing_partial_directory_prevents_any_process_creation(self):
        self.target.mkdir(parents=True);(self.target/'PG_VERSION').write_text('18')
        result=self.run_case()
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertEqual(self.fake.calls,[])
        self.assertEqual((self.target/'PG_VERSION').read_text(),'18')

    def test_failed_state_cannot_resume_even_if_reinvoked(self):
        self.fake.timeout=True;self.run_case();count=len(self.fake.calls)
        with self.assertRaises(ex.RecoveryBlocked):self.run_case()
        self.assertEqual(len(self.fake.calls),count)

    def test_new_executor_instance_cannot_bypass_failed_state_ledger(self):
        self.fake.timeout=True;self.run_case();count=len(self.fake.calls)
        another=ex.InitdbExecutor(self.root,self.private,clock=self.clock,popen=self.fake,tree_probe=self.tree,simulation=True)
        with self.assertRaises(ex.RecoveryBlocked):another.run(self.target,self.policy,self.grant,self.password_file)
        self.assertEqual(len(self.fake.calls),count)

    def test_running_state_write_failure_prevents_process_creation(self):
        original=Path.open
        def deny(path,*args,**kwargs):
            if path.name=='initialization-state.json':raise OSError('synthetic state write failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',deny):result=self.run_case()
        self.assertEqual(self.fake.calls,[]);self.assertEqual(result['state'],'needs_manual_recovery')
        self.assertIn('log_collection_failed',self.categories(result))

    def test_failure_and_simulated_success_cannot_authorize_downstream_steps(self):
        success=self.run_case()
        with self.assertRaises(ex.RecoveryBlocked):ex.require_completed(success)
        forged={**success,'simulation':False,'state':'completed','structure_verified':False}
        with self.assertRaises(ex.RecoveryBlocked):ex.require_completed(forged)
        with self.assertRaises(ex.RecoveryBlocked):ex.require_completed({'state':'completed'})

    def test_business_database_or_other_target_cannot_be_reused(self):
        with patch.dict('os.environ',{'DATABASE_URL':'postgresql://user:secret@unit.invalid/reviews'}):
            result=self.run_case(target=self.root/'reviews')
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertEqual(self.fake.calls,[])

    def test_approval_and_limits_are_never_changed(self):
        original=deepcopy(self.grant);policy=deepcopy(self.policy);self.run_case()
        self.assertEqual(self.grant,original);self.assertEqual(self.policy,policy)

    def test_old_authorization_or_wrong_scope_is_rejected(self):
        for change in ({'authorized':False},{'run_id':'other'},{'target_directory':'reviews'},
                       {'environment_plan_sha256':'0'*64},{'policy_sha256':'0'*64},
                       {'executor_sha256':'0'*64},{'archive_authorized':False},{'tree_strategy_reviewed':False}):
            runner=ex.InitdbExecutor(self.root,self.private,clock=self.clock,popen=self.fake,tree_probe=self.tree,simulation=True)
            result=runner.run(self.target,self.policy,{**self.grant,**change},self.password_file)
            self.assertEqual(result['state'],'needs_manual_recovery')
        self.assertEqual(self.fake.calls,[])

    def test_real_branch_requires_independently_verified_native_tree_provider(self):
        runner=ex.InitdbExecutor(self.root,self.private,clock=self.clock,popen=self.fake,tree_probe=self.tree,simulation=False)
        result=runner.run(self.target,self.policy,self.grant,self.password_file)
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertEqual(self.fake.calls,[])

    def test_unknown_tree_status_is_not_success(self):
        self.tree_exited=False;result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete');self.assertFalse(result['tree_verified'])

    def test_late_process_creation_is_terminated_and_never_completed(self):
        self.fake.create_delay=4;result=self.run_case()
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertIn('client_process_timeout',self.categories(result))
        self.assertEqual(self.fake.instances[0].terminations,1)

    def test_late_zero_exit_result_is_not_accepted(self):
        self.fake.work_seconds=4;result=self.run_case()
        self.assertEqual(result['failure_state'],'timed_out');self.assertNotEqual(result['state'],'completed')

    def test_cleanup_wait_timeout_stops_without_retrying_process(self):
        self.fake.timeout=True;self.fake.wait_timeout=True;result=self.run_case()
        self.assertEqual(result['failure_state'],'cleanup_incomplete');self.assertEqual(len(self.fake.calls),1)

    def test_structure_or_completion_marker_alone_cannot_pass(self):
        self.fake.make_structure=False;result=self.run_case()
        self.assertEqual(result['failure_state'],'failed');self.assertFalse(result['structure_verified'])

    def test_structure_and_pg_control_without_success_marker_cannot_pass(self):
        self.fake.out='shut down';result=self.run_case()
        self.assertTrue(result['structure_verified']);self.assertFalse(result['completion_marker_verified'])
        self.assertEqual(result['state'],'needs_manual_recovery')

    def test_native_entry_has_no_automatic_subprocess_fallback(self):
        runner=ex.InitdbExecutor(self.root,self.private,clock=self.clock)
        result=runner.run(self.target,self.policy,self.grant,self.password_file)
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertIsNone(result['pid'])

    def test_policy_and_executor_hashes_are_required_for_downstream_gate(self):
        result=self.run_case()
        forged={**result,'simulation':False,'executor_sha256':'0'*64}
        with self.assertRaises(ex.RecoveryBlocked):ex.require_completed(forged)

    def test_secret_inventory_failure_stops_before_process_creation(self):
        result=self.run_case(secrets=[None])
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertEqual(self.fake.calls,[])

    def test_log_truncation_is_explicit_and_cannot_hide_complete_claim(self):
        self.policy['max_log_bytes']=64;self.grant['policy_sha256']=ex.sha256(self.policy)
        self.fake.out=ex.SUCCESS_MARKER+' 中文'*1000
        result=self.run_case();self.assertTrue(result['logs']['stdout']['truncated'])
        self.assertLessEqual(result['logs']['stdout']['saved_byte_length'],64)
        self.assertNotEqual(result['state'],'completed')

    def test_invalid_or_unreviewed_limits_fail_closed(self):
        for value in (None,0,-1,True,float('inf'),float('nan')):
            policy={**self.policy,'execution_seconds':value}
            runner=ex.InitdbExecutor(self.root,self.private,clock=self.clock,popen=self.fake,tree_probe=self.tree,simulation=True)
            result=runner.run(self.target,policy,self.grant,self.password_file)
            self.assertEqual(result['state'],'needs_manual_recovery')
        self.assertEqual(self.fake.calls,[])

    def test_pending_recovery_has_no_approver_or_real_limits(self):
        policy=ex.pending_policy();grant=ex.pending_recovery(self.root,policy)
        self.assertFalse(grant['authorized']);self.assertFalse(grant['archive_authorized'])
        self.assertEqual(grant['approved_by'],'');self.assertIsNone(policy['execution_seconds'])


if __name__=='__main__':unittest.main()
