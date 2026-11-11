"""Owned Windows jobs: suspended creation, fail-closed assignment, bounded cleanup."""
import ctypes
from ctypes import wintypes as w
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time


def implementation_sha256():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def runtime_identity():
    # sys reads native version data directly; platform.platform() may launch cmd /c ver.
    version=str(sys.getwindowsversion()) if hasattr(sys,'getwindowsversion') else sys.platform
    return {'windows':version,'python':sys.version}


class JobControlError(OSError):
    def __init__(self,stage,code=0,cleanup_incomplete=True,audit=None):
        super().__init__(stage);self.stage=stage;self.code=code;self.cleanup_incomplete=cleanup_incomplete
        self.audit=audit


class BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
              ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),
              ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',BasicLimits),('io',ctypes.c_uint64*6),('process_memory',ctypes.c_size_t),
              ('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_=[('user',ctypes.c_int64),('kernel',ctypes.c_int64),('period_user',ctypes.c_int64),
              ('period_kernel',ctypes.c_int64),('faults',w.DWORD),('total',w.DWORD),('active',w.DWORD),('terminated',w.DWORD)]


class ThreadEntry(ctypes.Structure):
    _fields_=[('size',w.DWORD),('usage',w.DWORD),('tid',w.DWORD),('pid',w.DWORD),
              ('priority',w.LONG),('delta',w.LONG),('flags',w.DWORD)]


class ProcessIds(ctypes.Structure):
    _fields_=[('assigned',w.DWORD),('listed',w.DWORD),('pids',ctypes.c_size_t*512)]


class Kernel:
    def __init__(self):
        if os.name!='nt':raise JobControlError('windows_required')
        self.dll=ctypes.WinDLL('kernel32',use_last_error=True)
        signatures={
            'CreateJobObjectW':([ctypes.c_void_p,w.LPCWSTR],w.HANDLE),
            'SetInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD],w.BOOL),
            'AssignProcessToJobObject':([w.HANDLE,w.HANDLE],w.BOOL),
            'IsProcessInJob':([w.HANDLE,w.HANDLE,ctypes.POINTER(w.BOOL)],w.BOOL),
            'QueryInformationJobObject':([w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)],w.BOOL),
            'TerminateJobObject':([w.HANDLE,w.UINT],w.BOOL),
            'CreateToolhelp32Snapshot':([w.DWORD,w.DWORD],w.HANDLE),
            'Thread32First':([w.HANDLE,ctypes.POINTER(ThreadEntry)],w.BOOL),
            'Thread32Next':([w.HANDLE,ctypes.POINTER(ThreadEntry)],w.BOOL),
            'OpenThread':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            'ResumeThread':([w.HANDLE],w.DWORD),
            'CloseHandle':([w.HANDLE],w.BOOL),
            'GetHandleInformation':([w.HANDLE,ctypes.POINTER(w.DWORD)],w.BOOL),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=result

    def check(self,value,stage):
        if not value:raise JobControlError(stage,ctypes.get_last_error())
        return value

    def close(self,handle):self.check(self.dll.CloseHandle(handle),'close_handle')

    def invalid(self,handle):
        flags=w.DWORD()
        try:ok=self.dll.GetHandleInformation(handle,ctypes.byref(flags))
        except OSError as exc:
            # Strict handle checking can raise STATUS_INVALID_HANDLE instead of FALSE.
            if (getattr(exc,'winerror',0) or 0)&0xffffffff==0xc0000008:return True
            raise
        return not ok and ctypes.get_last_error()==6


class OwnedJobProcess:
    def __init__(self,argv,*,allowed_prefix,env=None,faults=(),before_resume=None,startup_cleanup_seconds=10):
        if not allowed_prefix or not Path(allowed_prefix[0]).is_absolute() or list(argv[:len(allowed_prefix)])!=list(allowed_prefix):
            raise JobControlError('command_scope_mismatch')
        if set(faults)-{'assign_invalid_handle','terminate_invalid_handle','close_invalid_handle'}:
            raise JobControlError('unknown_fault')
        if not math.isfinite(startup_cleanup_seconds) or startup_cleanup_seconds<=0:
            raise JobControlError('finite_startup_cleanup_required')
        self.api=Kernel();self.events=[];self.closed=False;self.drained=False;self.cleanup_failed=False
        self.process=None;self.job=None;self.faults=set(faults);self.observed_pids=set()
        self.closed_handles=[];self.output=None;self.before_resume=before_resume
        self.event('job_create_started')
        try:
            self.job=self.api.check(self.api.dll.CreateJobObjectW(None,None),'create_job')
            limits=ExtendedLimits();limits.basic.flags=0x2000
            self.api.check(self.api.dll.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)),'set_job_limits')
            self.event('job_created',kill_on_close=True,breakaway_allowed=False)
            safe_env={k:v for k,v in (env or os.environ).items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH','COMSPEC')}
            safe_env.update(LC_ALL='C',LANG='C',PYTHONIOENCODING='utf-8')
            self.process=subprocess.Popen(list(argv),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',env=safe_env,shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW|0x00000004,close_fds=True)
            self.event('process_created_suspended',pid=self.pid)
            handle=0 if 'assign_invalid_handle' in self.faults else self.job
            self.api.check(self.api.dll.AssignProcessToJobObject(handle,int(self.process._handle)),'assign_job')
            member=w.BOOL()
            self.api.check(self.api.dll.IsProcessInJob(int(self.process._handle),self.job,ctypes.byref(member)),'query_membership')
            if not member.value:raise JobControlError('membership_not_confirmed')
            self.event('assigned_before_resume',pid=self.pid)
            self._resume_primary()
        except BaseException as exc:
            self.event('startup_failed',stage=getattr(exc,'stage',type(exc).__name__))
            safe=self.emergency_cleanup(time.monotonic()+startup_cleanup_seconds)
            audit={'pid':getattr(self.process,'pid',None),'events':self.events,
                   'direct_exited':self.process is not None and self.process.poll() is not None,
                   'safety_cleanup_complete':safe,'output_drained':self.drained,'closed_handles':self.closed_handles}
            error=JobControlError(getattr(exc,'stage','startup_failed'),getattr(exc,'code',0),not safe,audit)
            error.owned_process=self
            raise error from None

    @property
    def pid(self):return self.process.pid
    @property
    def stdout(self):return self.process.stdout
    @property
    def stderr(self):return self.process.stderr

    def event(self,name,**fields):
        self.events.append({'event':name,'utc':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),**fields})

    def _resume_primary(self):
        snapshot=self.api.dll.CreateToolhelp32Snapshot(4,0)
        if snapshot==w.HANDLE(-1).value:raise JobControlError('thread_snapshot',ctypes.get_last_error())
        tids=[]
        try:
            entry=ThreadEntry();entry.size=ctypes.sizeof(entry)
            found=self.api.dll.Thread32First(snapshot,ctypes.byref(entry))
            while found:
                if entry.pid==self.pid:tids.append(entry.tid)
                found=self.api.dll.Thread32Next(snapshot,ctypes.byref(entry))
        finally:self.api.close(snapshot)
        if len(tids)!=1:raise JobControlError('primary_thread_ambiguous')
        thread=self.api.check(self.api.dll.OpenThread(2,False,tids[0]),'open_primary_thread')
        try:
            if self.before_resume:self.before_resume(self)
            previous=self.api.dll.ResumeThread(thread)
            if previous!=1:raise JobControlError('unexpected_suspend_count',int(previous))
            self.event('primary_thread_resumed',tid=int(tids[0]),previous_suspend_count=int(previous))
        finally:self.api.close(thread)

    def poll(self):return self.process.poll()
    def wait(self,timeout):
        code=self.process.wait(timeout=timeout);self.event('parent_exit_observed',returncode=code);return code
    def communicate(self,timeout):
        self.event('output_wait_started',timeout=timeout)
        try:
            out,err=self.process.communicate(timeout=timeout)
            self.drained=True;self.output=(out,err);self.event('output_eof',parent_returncode=self.poll());return out,err
        except subprocess.TimeoutExpired:
            self.event('output_wait_timeout',parent_returncode=self.poll());raise

    def accounting(self):
        value=Accounting();self.api.check(self.api.dll.QueryInformationJobObject(self.job,1,ctypes.byref(value),ctypes.sizeof(value),None),'query_job_accounting')
        return {'total':int(value.total),'active':int(value.active),'terminated':int(value.terminated)}

    def members(self):
        value=ProcessIds();self.api.check(self.api.dll.QueryInformationJobObject(self.job,3,ctypes.byref(value),ctypes.sizeof(value),None),'query_job_members')
        if value.listed>512:raise JobControlError('job_member_capacity')
        pids=[int(value.pids[i]) for i in range(value.listed)];self.observed_pids.update(pids);return pids

    def terminate(self):
        self.event('job_termination_requested')
        handle=0 if 'terminate_invalid_handle' in self.faults else self.job
        try:self.api.check(self.api.dll.TerminateJobObject(handle,1),'terminate_job')
        except JobControlError:self.cleanup_failed=True;self.event('job_termination_failed');raise

    def wait_empty(self,deadline):
        while time.monotonic()<deadline:
            counts=self.accounting()
            if counts['active']==0:
                self.event('job_empty',**counts);return True
            time.sleep(min(0.01,max(0,deadline-time.monotonic())))
        self.event('job_wait_timeout');return False

    def close_owned(self,deadline):
        if self.closed:return not self.cleanup_failed
        if not self.job:return False
        if not self.wait_empty(deadline) or self.poll() is None or not self.drained or not self.readers_finished():
            self.cleanup_failed=True;return False
        try:
            for stream in (self.stdout,self.stderr):
                if not stream.closed:stream.close()
            job_value=int(self.job)
            close_target=0 if 'close_invalid_handle' in self.faults else self.job
            self.api.check(self.api.dll.CloseHandle(close_target),'close_job')
            self.job=None
            self.event('job_handle_closed')
            job_invalid=self.api.invalid(job_value)
            root_value=int(self.process._handle)
            self.api.close(root_value);self.process._handle.Detach()
            root_invalid=self.api.invalid(root_value)
            self.closed_handles=[{'kind':'job','invalid_after_close':job_invalid},{'kind':'root_process','invalid_after_close':root_invalid}]
            if not (job_invalid and root_invalid):raise JobControlError('handle_release_not_confirmed')
            if time.monotonic()>deadline:raise JobControlError('handle_close_deadline')
            self.closed=True;self.event('handles_released',pipes_closed=self.stdout.closed and self.stderr.closed)
            return not self.cleanup_failed
        except BaseException as exc:
            self.cleanup_failed=True;self.event('handle_close_failed',stage=getattr(exc,'stage',None),
                error_type=type(exc).__name__,error_code=getattr(exc,'code',None),
                winerror=getattr(exc,'winerror',None),errno=getattr(exc,'errno',None));return False

    def readers_finished(self):
        readers=[getattr(self.process,name,None) for name in ('stdout_thread','stderr_thread')]
        return self.drained and all(thread is None or not thread.is_alive() for thread in readers)

    def verify_and_close(self,deadline):
        exited=bool(self.job) and self.wait_empty(deadline) and self.poll() is not None
        closed=self.close_owned(deadline)
        return {'kind':'windows_job_v1','pid':self.pid,'all_exited':exited,
                'handles_released':closed,'output_drained':self.drained,'source_sha256':implementation_sha256(),
                'reader_threads_finished':self.readers_finished(),'observed_member_pids':sorted(self.observed_pids)}

    def emergency_cleanup(self,deadline):
        """Only this test-created job/process. Does not erase an earlier cleanup failure."""
        try:
            if self.process is not None:
                # A pre-assignment failure leaves only the never-resumed direct child.
                if self.job:self.api.dll.TerminateJobObject(self.job,1)
                if self.process.poll() is None:self.process.kill()
                self.process.wait(timeout=max(0.001,deadline-time.monotonic()))
                if not self.drained:
                    self.process.communicate(timeout=max(0.001,deadline-time.monotonic()));self.drained=True
                for stream in (self.process.stdout,self.process.stderr):
                    if stream and not stream.closed:stream.close()
                if self.job and not self.wait_empty(deadline):return False
                handle=self.process._handle
                if not handle.closed:
                    handle_value=int(handle);handle.Close()
                    invalid=self.api.invalid(handle_value)
                    self.closed_handles.append({'kind':'root_process','invalid_after_close':invalid})
                    if not invalid:raise JobControlError('safety_process_handle_not_released')
            if self.job:
                job_value=int(self.job);self.api.close(self.job);self.job=None
                invalid=self.api.invalid(job_value)
                self.closed_handles.append({'kind':'job','invalid_after_close':invalid})
                if not invalid:raise JobControlError('safety_job_handle_not_released')
            self.closed=True;self.event('safety_cleanup_finished')
            return True
        except BaseException:
            self.cleanup_failed=True;self.event('safety_cleanup_incomplete');return False


NATIVE_CASES=frozenset(('normal_exit_assignment_before_execution_and_handles',
    'direct_child_timeout_terminates_tree','parent_exits_descendant_remains_in_job',
    'descendant_holds_both_output_pipes','assignment_native_failure_never_resumes',
    'termination_native_failure_stays_incomplete','real_wait_deadline_stays_incomplete',
    'native_handle_close_failure_stays_incomplete','multilevel_tree_all_descendants_terminated',
    'other_owned_job_is_not_affected'))


def checked_validation(root,grant):
    """A reviewed, hash-bound OS test receipt; never an execution authorization itself."""
    if os.name!='nt':raise JobControlError('windows_required')
    root=Path(root);folder=root/'.cache/p6e1b-f03'
    receipt=folder/'native-summary.json';evidence=folder/'native-evidence.json'
    for path,key in ((receipt,'tree_validation_sha256'),(evidence,'tree_evidence_sha256')):
        if (not path.is_file() or path.resolve()!=path
                or hashlib.sha256(path.read_bytes()).hexdigest()!=grant.get(key)):
            raise JobControlError('native_validation_not_bound_to_approval')
    value=json.loads(receipt.read_text(encoding='utf-8'))
    cases={test.rsplit('.test_',1)[-1] for test in value.get('test_ids',[])}
    expected={'version':'p6-e1b-native-job-validation-v1','status':'PASS','tests':len(NATIVE_CASES),
        'failures':0,'errors':0,'skips':0,**runtime_identity(),
        'job_provider_sha256':implementation_sha256(),
        'suite_sha256':hashlib.sha256((root/'tests/p6e1b_f03_native.py').read_bytes()).hexdigest(),
        'fixture_sha256':hashlib.sha256((root/'tests/fixtures/p6e1b_safe_child.py').read_bytes()).hexdigest()}
    if (any(value.get(k)!=v for k,v in expected.items()) or cases!=NATIVE_CASES
            or grant.get('tree_provider_sha256')!=implementation_sha256()):
        raise JobControlError('native_validation_stale_or_failed')
    audits=json.loads(evidence.read_text(encoding='utf-8'))
    if (len(audits)!=len(NATIVE_CASES) or {a['test'] for a in audits}!=set(value['test_ids'])
            or any(a.get('evidence')!='real_windows_job' for a in audits)
            or any(not p.get('safety_cleanup_complete') for a in audits for p in a['processes'])):
        raise JobControlError('native_evidence_incomplete')
    return value


class WindowsJobProvider:
    """One reviewed initdb invocation; no arbitrary command, fallback or test faults."""
    def __init__(self,root):
        self.root=Path(root);self.process=None;self.validated=False;self.proof=None

    def validate(self,root,grant,policy_value):
        from scripts.p6e1b_executor import check_grant, target_directory
        if Path(root)!=self.root or self.root.resolve()!=self.root:raise JobControlError('provider_root_mismatch')
        policy=check_grant(self.root,target_directory(self.root),policy_value,grant)
        checked_validation(self.root,grant);self.validated=True
        self.code_sha256=implementation_sha256();self.cleanup_seconds=policy.cleanup_seconds

    def spawn(self,argv,**kwargs):
        from scripts.p6e1b_executor import RUN_ID, target_directory
        expected=[r'C:\Program Files\PostgreSQL\18\bin\initdb.exe','-D',str(target_directory(self.root)),
            '-U',f'p6_e1b_{RUN_ID}_bootstrap','--encoding=UTF8','--locale=C',
            '--auth-host=scram-sha-256','--auth-local=scram-sha-256','--pwfile',
            str(self.root/'.cache/p6e1b-exec/private/bootstrap.pw')]
        if (not self.validated or self.code_sha256!=implementation_sha256() or self.process is not None
                or list(argv)!=expected or kwargs.get('stdin')!=subprocess.DEVNULL
                or kwargs.get('stdout')!=subprocess.PIPE or kwargs.get('stderr')!=subprocess.PIPE
                or kwargs.get('shell') is not False):raise JobControlError('unreviewed_native_launch')
        try:
            self.process=OwnedJobProcess(argv,allowed_prefix=expected,env=kwargs.get('env'),
                startup_cleanup_seconds=self.cleanup_seconds)
        except JobControlError as exc:
            self.process=getattr(exc,'owned_process',None)
            raise
        return self.process

    def probe(self,pid,deadline):
        if self.process is None or self.process.pid!=pid:raise JobControlError('unowned_process')
        self.proof=self.process.verify_and_close(deadline);return self.proof
