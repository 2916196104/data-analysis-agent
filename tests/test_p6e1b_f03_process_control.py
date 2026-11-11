"""Admission/failure tests only. These mocks are NOT native Windows tree evidence."""
from copy import deepcopy
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import p6e1b_executor as ex
from scripts import p6e1b_windows_job as jobs
from test_p6e1b_f02_executor import FakeClock, FakePopen


class ProcessAdmissionTests(unittest.TestCase):
    def setUp(self):
        for name in ('subprocess.Popen','subprocess.run','socket.socket.connect','socket.socket.bind','psycopg.connect'):
            blocker=self.enterContext(patch(name,side_effect=AssertionError('offline-only test')))
            self.addCleanup(blocker.assert_not_called)
        self.root=Path(self.enterContext(TemporaryDirectory(prefix='p6e1b-f03-unit-')))
        self.folder=self.root/'.cache/p6e1b-f03';self.folder.mkdir(parents=True)
        self.receipt_path=self.folder/'native-summary.json';self.evidence_path=self.folder/'native-evidence.json'
        for name in ('tests/p6e1b_f03_native.py','tests/fixtures/p6e1b_safe_child.py'):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic receipt fixture')
        self.cases=['synthetic.NativeJobTests.test_'+s for s in sorted(jobs.NATIVE_CASES)]
        self.receipt={'version':'p6-e1b-native-job-validation-v1','status':'PASS','tests':10,'failures':0,'errors':0,'skips':0,
            'test_ids':self.cases,**jobs.runtime_identity(),'job_provider_sha256':jobs.implementation_sha256(),
            'suite_sha256':self.hash(self.root/'tests/p6e1b_f03_native.py'),
            'fixture_sha256':self.hash(self.root/'tests/fixtures/p6e1b_safe_child.py')}
        self.audits=[{'test':t,'evidence':'real_windows_job','processes':[{'safety_cleanup_complete':True}]} for t in self.cases]
        self.grant={'tree_provider_sha256':jobs.implementation_sha256()}
        self.write_receipt()

    @staticmethod
    def hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    def write_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt),encoding='utf-8')
        self.evidence_path.write_text(json.dumps(self.audits),encoding='utf-8')
        self.grant.update(tree_validation_sha256=self.hash(self.receipt_path),tree_evidence_sha256=self.hash(self.evidence_path))
    def check(self):return jobs.checked_validation(self.root,self.grant)

    def test_empty_command_scope_rejected_before_kernel(self):
        with self.assertRaises(jobs.JobControlError):jobs.OwnedJobProcess(['python'],allowed_prefix=[])
    def test_relative_or_different_command_rejected(self):
        for prefix in (['python'],[str(self.root/'other.exe')]):
            with self.assertRaises(jobs.JobControlError):jobs.OwnedJobProcess([sys.executable],allowed_prefix=prefix)
    def test_unknown_fault_rejected_before_native_api(self):
        with self.assertRaises(jobs.JobControlError):jobs.OwnedJobProcess([sys.executable],allowed_prefix=[sys.executable],faults=['unknown'])
    def test_nonfinite_cleanup_limit_rejected_before_native_api(self):
        for limit in (0,-1,float('inf'),float('nan')):
            with self.assertRaises(jobs.JobControlError):jobs.OwnedJobProcess([sys.executable],allowed_prefix=[sys.executable],startup_cleanup_seconds=limit)
    def test_unvalidated_provider_cannot_launch(self):
        provider=jobs.WindowsJobProvider(self.root)
        with self.assertRaises(jobs.JobControlError):provider.spawn([sys.executable],stdin=subprocess.DEVNULL,shell=False)
    def test_native_and_fake_process_cannot_mix(self):
        with self.assertRaises(ex.RecoveryBlocked):
            ex.InitdbExecutor(self.root,self.root/'.cache/p6e1b-exec/private/f02',native_provider=jobs.WindowsJobProvider(self.root),popen=Mock())
    def test_native_and_simulation_cannot_mix(self):
        with self.assertRaises(ex.RecoveryBlocked):
            ex.InitdbExecutor(self.root,self.root/'.cache/p6e1b-exec/private/f02',native_provider=jobs.WindowsJobProvider(self.root),simulation=True)
    def test_untyped_native_provider_rejected(self):
        with self.assertRaises(ex.RecoveryBlocked):
            ex.InitdbExecutor(self.root,self.root/'.cache/p6e1b-exec/private/f02',native_provider=Mock())
    def test_receipt_is_not_recovery_permission(self):
        provider=jobs.WindowsJobProvider(self.root)
        with self.assertRaises(ex.RecoveryBlocked):provider.validate(self.root,self.grant,ex.pending_policy())
        self.assertFalse(provider.validated)
    def test_other_root_rejected(self):
        with self.assertRaises(jobs.JobControlError):jobs.WindowsJobProvider(self.root).validate(self.root/'other',self.grant,{})
    def test_unbound_receipt_rejected(self):
        self.grant['tree_validation_sha256']='0'*64
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_failed_native_receipt_rejected(self):
        self.receipt['status']='FAIL';self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_skipped_native_case_rejected(self):
        self.receipt['skips']=1;self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_missing_native_case_rejected(self):
        self.receipt['test_ids']=self.cases[:-1];self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_changed_provider_rejected(self):
        self.receipt['job_provider_sha256']='0'*64;self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_changed_fixture_rejected(self):
        (self.root/'tests/fixtures/p6e1b_safe_child.py').write_text('changed')
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_different_windows_build_rejected(self):
        self.receipt['windows']='different';self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_fake_evidence_rejected(self):
        self.audits[0]['evidence']='FakePopen';self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_unsafe_cleanup_evidence_rejected(self):
        self.audits[0]['processes'][0]['safety_cleanup_complete']=False;self.write_receipt()
        with self.assertRaises(jobs.JobControlError):self.check()
    def test_synthetic_receipt_validates_only_hash_contract_not_native_claim(self):
        self.assertEqual(self.check()['status'],'PASS')
        with self.assertRaises(ex.RecoveryBlocked):jobs.WindowsJobProvider(self.root).validate(self.root,self.grant,ex.pending_policy())
    def test_unknown_handle_query_error_is_not_accepted(self):
        api=object.__new__(jobs.Kernel);api.dll=Mock()
        error=OSError('synthetic');error.winerror=5;api.dll.GetHandleInformation.side_effect=error
        with self.assertRaises(OSError):api.invalid(123)
    def test_exact_invalid_handle_status_is_recognized(self):
        api=object.__new__(jobs.Kernel);api.dll=Mock()
        error=OSError('synthetic');error.winerror=-1073741816;api.dll.GetHandleInformation.side_effect=error
        self.assertTrue(api.invalid(123))
    def test_native_receipt_missing_keeps_executor_closed(self):
        clock=FakeClock();target=ex.target_directory(self.root);fake=FakePopen(target,clock)
        policy={'version':ex.POLICY_VERSION,'execution_seconds':3,'termination_seconds':1,'cleanup_seconds':2,'max_log_bytes':4096}
        grant=ex.pending_recovery(self.root,policy)
        grant.update(authorized=True,approved_by='OFFLINE-ONLY',expires_at='2099-01-01T00:00:00+00:00',route='B',
            reinitialize_authorized=True,archive_authorized=True,archive_completed=True,archive_evidence_sha256='a'*64,
            tree_strategy_reviewed=True,tree_provider_sha256=jobs.implementation_sha256())
        runner=ex.InitdbExecutor(self.root,self.root/'.cache/p6e1b-exec/private/f02',native_provider=jobs.WindowsJobProvider(self.root))
        result=runner.run(target,policy,grant,self.root/'no-password')
        self.assertEqual(result['state'],'needs_manual_recovery');self.assertIsNone(result['pid'])
    def test_drafts_do_not_approve_recovery_or_fill_receipts(self):
        draft=ex.pending_recovery(self.root,ex.pending_policy())
        self.assertFalse(draft['authorized']);self.assertFalse(draft['archive_authorized'])
        self.assertEqual(draft['approved_by'],'');self.assertEqual(draft['tree_validation_sha256'],'')
        self.assertEqual(draft['tree_evidence_sha256'],'')


if __name__=='__main__':unittest.main()
