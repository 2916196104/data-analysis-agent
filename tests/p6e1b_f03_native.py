"""Explicit opt-in, real Windows tests. Only the dedicated harmless Python fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.p6e1b_windows_job import OwnedJobProcess, JobControlError, implementation_sha256, runtime_identity

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/p6e1b_safe_child.py'
OUT=ROOT/'.cache/p6e1b-f03'
AUDITS=[]


class NativeJobTests(unittest.TestCase):
    def setUp(self):
        if os.name!='nt':self.fail('Real Windows is required; no mock or skip substitutes')
        self.owned=[];self.token=uuid4().hex;self.work=OUT/'native'/self.token
        self.work.mkdir(parents=True)
        self.audit={'test':self.id(),'evidence':'real_windows_job','token':self.token,'processes':[]}
        self.addCleanup(self.cleanup)
        for name in ('socket.socket.connect','socket.socket.bind','psycopg.connect'):
            blocker=self.enterContext(patch(name,side_effect=AssertionError('network/database forbidden')))
            self.addCleanup(blocker.assert_not_called)
        original=subprocess.Popen
        def safe_popen(argv,*args,**kwargs):
            if list(argv[:2])!=[sys.executable,str(FIXTURE)]:raise AssertionError('only harmless fixture allowed')
            return original(argv,*args,**kwargs)
        self.enterContext(patch('subprocess.Popen',safe_popen))

    def spawn(self,mode='sleep',depth=0,label='child',**kwargs):
        argv=[sys.executable,str(FIXTURE),'--work',str(self.work),'--token',self.token,
              '--label',label,'--mode',mode,'--depth',str(depth)]
        value=OwnedJobProcess(argv,allowed_prefix=[sys.executable,str(FIXTURE)],**kwargs)
        self.owned.append(value);return value

    def ready(self,label='child',depth=0):
        path=self.work/f'{label}-{depth}.ready.json';deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if path.exists():
                try:
                    value=json.loads(path.read_text());self.assertEqual(value['token'],self.token);return value
                except json.JSONDecodeError:pass
            time.sleep(0.01)
        self.fail('fixture readiness deadline')

    def end_tree(self,process):
        process.members();process.terminate();process.wait(timeout=5)
        self.assertTrue(process.wait_empty(time.monotonic()+5))
        out,err=process.communicate(timeout=5)
        proof=process.verify_and_close(time.monotonic()+5)
        self.assertTrue(proof['all_exited']);self.assertTrue(proof['handles_released'])
        self.assertTrue(proof['output_drained']);self.assertTrue(process.stdout.closed and process.stderr.closed)
        self.assertTrue(proof['reader_threads_finished'])
        self.assertTrue(all(x['invalid_after_close'] for x in process.closed_handles))
        self.audit.update(proof=proof,stdout=out,stderr=err);return proof

    def cleanup(self):
        for process in self.owned:
            safe=True if process.closed else process.emergency_cleanup(time.monotonic()+10)
            self.audit['processes'].append({'pid':process.pid,'events':process.events,
                'latched_cleanup_incomplete':process.cleanup_failed,'safety_cleanup_complete':safe,
                'returncode':process.poll(),'handles':process.closed_handles})
            self.assertTrue(safe,'test-owned process safety cleanup incomplete')
        AUDITS.append(self.audit)

    def test_normal_exit_assignment_before_execution_and_handles(self):
        def suspended(process):
            self.assertFalse(any(self.work.glob('*.ready.json')))
            self.assertIsNone(process.poll());self.assertIn(process.pid,process.members())
        process=self.spawn('normal',before_resume=suspended)
        self.ready();out,err=process.communicate(timeout=10)
        self.assertEqual(process.poll(),0);self.assertIn('P6SAFE READY',out);self.assertIn('STDERR',err)
        proof=process.verify_and_close(time.monotonic()+5)
        self.assertTrue(proof['all_exited'] and proof['handles_released'])
        self.assertTrue(proof['reader_threads_finished'])
        self.audit.update(proof=proof,stdout=out,stderr=err)
        names=[x['event'] for x in process.events]
        self.assertLess(names.index('assigned_before_resume'),names.index('primary_thread_resumed'))

    def test_direct_child_timeout_terminates_tree(self):
        process=self.spawn();self.ready()
        with self.assertRaises(subprocess.TimeoutExpired):process.communicate(timeout=0.05)
        self.end_tree(process)

    def test_parent_exits_descendant_remains_in_job(self):
        process=self.spawn('parent_exit',depth=1);desc=self.ready(depth=0);self.ready(depth=1)
        self.assertEqual(process.wait(timeout=5),0)
        self.assertIn(desc['pid'],process.members());self.assertGreater(process.accounting()['active'],0)
        self.end_tree(process)

    def test_descendant_holds_both_output_pipes(self):
        process=self.spawn('parent_exit',depth=1);self.ready(depth=1);self.assertEqual(process.wait(timeout=5),0)
        with self.assertRaises(subprocess.TimeoutExpired):process.communicate(timeout=0.05)
        proof=self.end_tree(process)
        self.assertIn('READY child 0',self.audit['stdout']);self.assertIn('STDERR',self.audit['stderr'])
        self.assertTrue(proof['output_drained'])

    def test_assignment_native_failure_never_resumes(self):
        with self.assertRaises(JobControlError) as caught:self.spawn(faults=('assign_invalid_handle',))
        error=caught.exception;self.assertEqual(error.stage,'assign_job');self.assertNotEqual(error.code,0)
        self.assertFalse(error.cleanup_incomplete);self.assertTrue(error.audit['direct_exited'])
        self.assertTrue(error.audit['safety_cleanup_complete']);self.assertFalse(list(self.work.glob('*.ready.json')))
        self.assertEqual(len(error.audit['closed_handles']),2)
        self.assertTrue(all(h['invalid_after_close'] for h in error.audit['closed_handles']))
        self.assertNotIn('primary_thread_resumed',[x['event'] for x in error.audit['events']])
        self.audit['injected_native_failure']=error.audit

    def test_termination_native_failure_stays_incomplete(self):
        process=self.spawn(faults=('terminate_invalid_handle',));self.ready()
        with self.assertRaises(JobControlError):process.terminate()
        self.assertIsNone(process.poll());self.assertTrue(process.cleanup_failed)
        self.assertFalse(process.close_owned(time.monotonic()))
        self.audit['expected_state']='cleanup_incomplete'

    def test_real_wait_deadline_stays_incomplete(self):
        process=self.spawn();self.ready()
        with self.assertRaises(subprocess.TimeoutExpired):process.wait(timeout=0.01)
        self.assertFalse(process.close_owned(time.monotonic()+0.02));self.assertTrue(process.cleanup_failed)
        self.audit['expected_state']='cleanup_incomplete'

    def test_native_handle_close_failure_stays_incomplete(self):
        process=self.spawn('normal',faults=('close_invalid_handle',));self.ready();process.communicate(timeout=10)
        self.assertFalse(process.close_owned(time.monotonic()+5));self.assertTrue(process.cleanup_failed)
        self.assertIsNotNone(process.job);self.audit['expected_state']='cleanup_incomplete'

    def test_multilevel_tree_all_descendants_terminated(self):
        process=self.spawn('tree',depth=3);self.ready(depth=3)
        expected={self.ready(depth=d)['pid'] for d in range(4)}
        self.assertTrue(expected.issubset(set(process.members())))
        proof=self.end_tree(process);self.assertTrue(expected.issubset(set(proof['observed_member_pids'])))

    def test_other_owned_job_is_not_affected(self):
        target=self.spawn(label='target');other=self.spawn(label='other')
        self.ready('target');ready=self.ready('other')
        self.end_tree(target)
        self.assertIsNone(other.poll());self.assertIn(ready['pid'],other.members())
        self.audit['other_job_survived_target_termination']=True
        self.end_tree(other)


def main():
    if os.name!='nt':raise SystemExit('BLOCKED: native Windows required')
    OUT.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(NativeJobTests)
    started=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
    summary={'version':'p6-e1b-native-job-validation-v1','status':'PASS' if result.wasSuccessful() else 'FAIL',
        'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),
        'duration_seconds':time.monotonic()-started,**runtime_identity(),
        'job_provider_sha256':implementation_sha256(),
        'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'suite_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'test_ids':[a['test'] for a in AUDITS],
        'postgresql_commands':0,'database_connections':0,'paid_models':0,'embeddings':0}
    (OUT/'native-evidence.json').write_text(json.dumps(AUDITS,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'native-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__=='__main__':main()
