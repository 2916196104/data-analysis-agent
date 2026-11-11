"""Fixed-target initdb process control. No database client or implicit authorization."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import quote
from uuid import uuid4

RUN_ID='c5412c7c4bed43fba4a721909eea1041'
ENVIRONMENT_PLAN_SHA256='40a30688281cf0cdd2a73af02bd5c345e2671d9fcc672a84c434947b80a09e2b'
POLICY_VERSION='p6-e1b-initdb-policy-v1'
RECOVERY_VERSION='p6-e1b-initdb-recovery-v1'
RECOVERY_ATTEMPT='a4b479a8d9154994a402f7b13f1a00a6'
SUCCESS_MARKER='Success. You can now start the database server'
STATES=('not_started','running','completed','failed','timed_out','cleanup_incomplete','needs_manual_recovery')


def sha256(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def implementation_sha256():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
def target_directory(root):return Path(root)/'.cache/p6e1b-env'/RUN_ID/'cluster'
def log_directory(root,attempt_id=None):
    if attempt_id is None:return Path(root)/'.cache/p6e1b-exec/private/f02'
    if attempt_id!=RECOVERY_ATTEMPT:raise RecoveryBlocked('fixed_attempt_required')
    return Path(root)/'.cache/p6e1b-exec/private/attempts'/attempt_id/'initdb'
def adapter_sha256(root):
    path=Path(root)/'.cache/p6e1b-exec/execute.py'
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
def audit_sha256(value):
    try:return sha256(value)
    except (ValueError,TypeError):return None


class RecoveryBlocked(ValueError):pass


class Clock:
    def monotonic(self):return time.monotonic()
    def utc(self):return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Policy:
    execution_seconds: float
    termination_seconds: float
    cleanup_seconds: float
    max_log_bytes: int

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict) or value.get('version')!=POLICY_VERSION:raise RecoveryBlocked('policy_version_invalid')
        for key in ('execution_seconds','termination_seconds','cleanup_seconds'):
            v=value.get(key)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:
                raise RecoveryBlocked('finite_reviewed_limits_required')
        if value['cleanup_seconds']<value['termination_seconds']:raise RecoveryBlocked('cleanup_limit_too_small')
        if type(value.get('max_log_bytes')) is not int or value['max_log_bytes']<=0:
            raise RecoveryBlocked('log_limit_required')
        return cls(**{key:value[key] for key in cls.__dataclass_fields__})


def pending_policy():
    return {'version':POLICY_VERSION,'execution_seconds':None,'termination_seconds':None,
            'cleanup_seconds':None,'max_log_bytes':None}


def pending_recovery(root,policy):
    return {'version':RECOVERY_VERSION,'run_id':RUN_ID,'environment_plan_sha256':ENVIRONMENT_PLAN_SHA256,
            'target_directory':str(target_directory(root)),'executor_sha256':implementation_sha256(),
            'adapter_sha256':adapter_sha256(root),
            'policy_version':POLICY_VERSION,'policy_sha256':sha256(policy),'authorized':False,
            'approved_by':'','expires_at':'','reinitialize_authorized':False,'archive_authorized':False,
            'archive_completed':False,'archive_evidence_sha256':'','tree_strategy_reviewed':False,
            'tree_provider_sha256':'','tree_validation_sha256':'','tree_evidence_sha256':'','route':None}


def check_grant(root,target,policy_value,grant,*,now=None):
    if not isinstance(grant,dict):raise RecoveryBlocked('recovery_authorization_required')
    expected=target_directory(root)
    if Path(target)!=expected or expected.resolve()!=expected:raise RecoveryBlocked('fixed_target_mismatch')
    for path in (expected,*expected.parents):
        if path==Path(root) or Path(root) not in path.parents:continue
        try:info=path.lstat()
        except FileNotFoundError:continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise RecoveryBlocked('reparse_or_non_directory_target')
    policy=Policy.parse(policy_value)
    required={'version':RECOVERY_VERSION,'run_id':RUN_ID,'environment_plan_sha256':ENVIRONMENT_PLAN_SHA256,
              'target_directory':str(expected),'executor_sha256':implementation_sha256(),
              'adapter_sha256':adapter_sha256(root),
              'policy_version':POLICY_VERSION,'policy_sha256':sha256(policy_value),
              'authorized':True,'reinitialize_authorized':True,'route':'B',
              'archive_authorized':True,'archive_completed':True,'tree_strategy_reviewed':True}
    if any(grant.get(k)!=v for k,v in required.items()):raise RecoveryBlocked('independent_recovery_authorization_required')
    if not isinstance(grant.get('approved_by'),str) or not grant['approved_by'].strip():
        raise RecoveryBlocked('approver_required')
    try:expiry=datetime.fromisoformat(grant['expires_at'])
    except (KeyError,ValueError,TypeError):raise RecoveryBlocked('expiry_required') from None
    if expiry.tzinfo is None or expiry<=(now or datetime.now(timezone.utc)):
        raise RecoveryBlocked('recovery_authorization_expired')
    for k in ('archive_evidence_sha256','tree_provider_sha256'):
        if not re.fullmatch('[a-f0-9]{64}',grant.get(k,'')):raise RecoveryBlocked('independent_evidence_required')
    return policy


class Redactor:
    def __init__(self,known_secrets=()):
        values=[]
        for value in known_secrets:
            if not isinstance(value,str):raise RecoveryBlocked('secret_inventory_unavailable')
            if value:values.extend((value,quote(value,safe='')))
        self.values=sorted(set(values),key=len,reverse=True)

    def __call__(self,text):
        for value in self.values:text=text.replace(value,'[REDACTED]')
        text=re.sub(r'(?i)postgres(?:ql)?://[^\s\x27\x22]+','[REDACTED_DSN]',text)
        text=re.sub(r'(?im)^.*\b(?:hostaddr|host|dbname)\s*=.*$','[REDACTED_CONNINFO]',text)
        text=re.sub(r'(?im)^.*\bauthorization\b["\x27]?\s*[:=].*$','[REDACTED_AUTHORIZATION]',text)
        text=re.sub(r'(?i)\b(?:api[_-]?key|password|passwd|access[_-]?token|secret)\b["\x27]?\s*[:=]\s*(?:"[^"]*"|\x27[^\x27]*\x27|\S+)',
                    '[REDACTED_CREDENTIAL]',text)
        text=re.sub(r'(?i)\bBearer\s+\S+','Bearer [REDACTED]',text)
        text=re.sub(r'\bsk-[A-Za-z0-9_-]{8,}','[REDACTED_API_KEY]',text)
        for value in self.values:
            for length in range(len(value)-1,2,-1):
                if text.endswith(value[:length]):
                    text=text[:-length]+'[REDACTED_PARTIAL]';break
        return text


def normalize_output(value):
    if value is None:return None,None
    if isinstance(value,bytes):return value.decode('utf-8','replace'),len(value)
    if isinstance(value,str):return value,len(value.encode('utf-8'))
    raise TypeError('unsupported_output_type')


class InitdbExecutor:
    """Real execution requires a reviewed native provider AND independent recovery grant."""
    def __init__(self,root,private,*,clock=None,popen=None,tree_probe=None,simulation=False,previous_state='not_started',native_provider=None,attempt_id=None):
        self.root=Path(root);self.target=target_directory(root);self.private=Path(private)
        self.attempt_id=attempt_id
        if previous_state not in STATES:raise RecoveryBlocked('unknown_initialization_state')
        if self.private!=log_directory(self.root,attempt_id) or self.private.resolve()!=self.private:
            raise RecoveryBlocked('private_log_scope_mismatch')
        self.ledger=self.private/'initialization-state.json';self.ledger_enabled=False;self.ledger_ok=False
        if self.ledger.exists():
            try:previous_state=json.loads(self.ledger.read_text(encoding='utf-8'))['state']
            except (OSError,KeyError,ValueError,TypeError):raise RecoveryBlocked('state_ledger_unreadable') from None
            if previous_state not in STATES:raise RecoveryBlocked('state_ledger_invalid')
        self.clock=clock or Clock();self.popen=popen or subprocess.Popen
        self.tree_probe=tree_probe;self.simulation=simulation
        self.native_provider=native_provider
        if native_provider is not None:
            from scripts.p6e1b_windows_job import WindowsJobProvider
            if type(native_provider) is not WindowsJobProvider or simulation or popen or tree_probe or clock:
                raise RecoveryBlocked('native_provider_cannot_mix_with_simulation_or_callbacks')
            self.popen=native_provider.spawn;self.tree_probe=native_provider.probe
        if simulation and popen is None:raise RecoveryBlocked('simulation_requires_fake_process')
        self.state=previous_state;self.events=[];self.errors=[];self.history=[previous_state]
        self.output={'stdout':None,'stderr':None};self.pending_resources=[];self.result=None
        self.log_metadata={};self.manifest_saved=False

    def event(self,name,**values):
        self.events.append({'event':name,'utc':self.clock.utc(),'monotonic':self.clock.monotonic(),**values})

    def transition(self,state):
        if state not in STATES:raise ValueError('unknown_state')
        self.state=state;self.history.append(state);self.event('state',state=state)
        if self.ledger_enabled:
            try:
                with self.ledger.open('w',encoding='utf-8') as stream:
                    json.dump({'version':'p6-e1b-initdb-state-v1','run_id':RUN_ID,'state':state,
                               'attempt_id':self.attempt_id,
                               'history':self.history,'events':self.events,'errors':self.errors},stream)
                self.ledger_ok=True
            except Exception as exc:
                self.ledger_ok=False;self.ledger_enabled=False;self.error('log_collection_failed',exc)

    def error(self,category,exc=None):
        self.errors.append({'category':category,'error_type':type(exc).__name__ if exc else None})
        self.event('error',category=category,error_type=type(exc).__name__ if exc else None)

    def collect(self,stdout,stderr):
        for key,value in (('stdout',stdout),('stderr',stderr)):
            _,size=normalize_output(value)
            _,previous=normalize_output(self.output[key])
            if size is not None and (previous is None or size>=previous):self.output[key]=value

    def save_logs(self,policy,redact,attempt):
        metadata=self.log_metadata;safe_values={}
        for name,value in self.output.items():
            text,size=normalize_output(value)
            meta={'available':value is not None,'raw_byte_length':size,'saved_byte_length':None,
                  'truncated':False,'path':None,'persisted':False}
            metadata[name]=meta
            safe_values[name]=None
            if text is not None:
                safe=redact(text);encoded=safe.encode('utf-8');meta['truncated']=len(encoded)>policy.max_log_bytes
                saved=encoded[:policy.max_log_bytes].decode('utf-8','ignore').encode('utf-8')
                path=self.private/(attempt+'.'+name)
                try:
                    with path.open('xb') as stream:stream.write(saved)
                    meta.update(saved_byte_length=len(saved),path=path.name,persisted=True)
                except Exception as exc:self.error('log_collection_failed',exc)
                safe_values[name]=safe
        try:
            with (self.private/(attempt+'.logs.json')).open('x',encoding='utf-8') as stream:
                json.dump(metadata,stream,sort_keys=True)
            self.manifest_saved=True
        except Exception as exc:self.error('log_collection_failed',exc)
        return metadata,safe_values

    def structure_valid(self):
        for name in ('base','base/1','base/4','base/5','global','pg_wal'):
            p=self.target/name
            if not p.is_dir() or p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:return False
        for name in ('PG_VERSION','global/pg_control','postgresql.conf','pg_hba.conf'):
            p=self.target/name
            if not p.is_file() or p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:return False
        return ((self.target/'PG_VERSION').read_text().strip()=='18'
                and (self.target/'global/pg_control').stat().st_size>=8192
                and all((self.target/'base'/name).is_dir() for name in ('1','4','5')))

    def run(self,target,policy_value,grant,password_file,known_secrets=()):
        if self.state!='not_started':raise RecoveryBlocked('initialization_cannot_automatically_resume')
        process=None;direct_exited=False;drained=False;tree_verified=False;returncode=None
        structure_verified=False;marker_verified=False;log_complete=False
        logs={};safe={};attempt='initdb-'+uuid4().hex;failure=None
        policy=None;redact=None
        try:
            policy=check_grant(self.root,target,policy_value,grant)
            if Path(target).exists():raise RecoveryBlocked('existing_partial_directory_requires_manual_recovery')
            expected_private=log_directory(self.root,self.attempt_id)
            if self.private!=expected_private or self.private.resolve()!=self.private:
                raise RecoveryBlocked('private_log_scope_mismatch')
            if self.attempt_id is not None:
                receipt_path=expected_private.parent/'archive-receipt.json'
                receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
                if (grant.get('attempt_id')!=self.attempt_id or receipt.get('attempt_id')!=self.attempt_id
                        or receipt.get('archive_completed') is not True
                        or receipt.get('source')!=str(self.target)
                        or grant.get('archive_evidence_sha256')!=sha256(receipt)
                        or receipt.get('simulation') is not self.simulation):
                    raise RecoveryBlocked('verified_attempt_archive_receipt_required')
            if not self.tree_probe:raise RecoveryBlocked('verified_tree_provider_required_before_launch')
            if not self.simulation:
                if self.native_provider is None:raise RecoveryBlocked('native_windows_tree_provider_not_independently_verified')
                self.native_provider.validate(self.root,grant,policy_value)
            password_file=Path(password_file)
            if (password_file!=self.root/'.cache/p6e1b-exec/private/bootstrap.pw' or not password_file.is_file()
                    or password_file.is_symlink() or getattr(password_file.lstat(),'st_file_attributes',0)&0x400
                    or password_file.resolve()!=password_file or password_file.stat().st_size==0):
                raise RecoveryBlocked('controlled_password_file_required')
            redact=Redactor(known_secrets)
            self.private.mkdir(parents=True,exist_ok=True)
            self.ledger_enabled=True;self.transition('running')
            if not self.ledger_ok:raise RuntimeError('running_state_not_persisted')
            deadline=self.clock.monotonic()+policy.execution_seconds
            self.event('process_create_started')
            argv=[r'C:\Program Files\PostgreSQL\18\bin\initdb.exe','-D',str(self.target),'-U',f'p6_e1b_{RUN_ID}_bootstrap',
                  '--encoding=UTF8','--locale=C','--auth-host=scram-sha-256','--auth-local=scram-sha-256','--pwfile',str(password_file)]
            process=self.popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,encoding='utf-8',errors='replace',shell=False,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                env={**{k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH','COMSPEC')},
                     'LC_ALL':'C','LANG':'C'})
            self.event('process_created',pid=process.pid)
            self.event('wait_started',deadline=deadline)
            try:
                if self.clock.monotonic()>=deadline:raise subprocess.TimeoutExpired('initdb',policy.execution_seconds)
                out,err=process.communicate(timeout=max(0,deadline-self.clock.monotonic()))
                self.collect(out,err);drained=True
                returncode=process.poll();direct_exited=returncode is not None
                self.event('process_exit_observed',returncode=returncode,direct_exited=direct_exited)
                if self.clock.monotonic()>deadline:raise subprocess.TimeoutExpired('initdb',policy.execution_seconds,out,err)
                if not direct_exited:raise RuntimeError('exit_not_observed')
                if returncode!=0:self.error('process_nonzero_exit');failure='failed'
            except subprocess.TimeoutExpired as exc:
                self.collect(exc.stdout,exc.stderr);self.event('execution_timed_out');self.error('client_process_timeout')
                failure='timed_out';self.transition('timed_out')
            except Exception as exc:
                self.error('process_wait_failed',exc);failure='failed'
            cleanup_deadline=self.clock.monotonic()+policy.cleanup_seconds
            if not direct_exited:
                terminate_deadline=min(cleanup_deadline,self.clock.monotonic()+policy.termination_seconds)
                self.event('termination_started',deadline=terminate_deadline)
                try:
                    process.terminate();self.event('termination_requested')
                    if self.clock.monotonic()>=terminate_deadline:raise TimeoutError('termination_deadline')
                    returncode=process.wait(timeout=max(0,terminate_deadline-self.clock.monotonic()))
                    direct_exited=returncode is not None
                    if self.clock.monotonic()>terminate_deadline:raise TimeoutError('termination_deadline')
                    self.event('process_exit_observed',returncode=returncode,direct_exited=direct_exited)
                except Exception as exc:
                    self.error('resource_cleanup_timeout' if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else 'resource_cleanup_failed',exc)
                    self.event('termination_failed')
                    returncode=process.poll();direct_exited=returncode is not None
            if not drained and direct_exited and self.clock.monotonic()<cleanup_deadline:
                self.event('output_drain_started',deadline=cleanup_deadline)
                try:
                    out,err=process.communicate(timeout=max(0,cleanup_deadline-self.clock.monotonic()))
                    self.collect(out,err);drained=self.clock.monotonic()<=cleanup_deadline
                except subprocess.TimeoutExpired as exc:
                    self.collect(exc.stdout,exc.stderr);self.error('resource_cleanup_timeout',exc)
                except Exception as exc:self.error('log_collection_failed',exc)
            if direct_exited and drained and self.clock.monotonic()<cleanup_deadline:
                self.event('tree_verification_started',deadline=cleanup_deadline)
                try:
                    proof=self.tree_probe(process.pid,cleanup_deadline)
                    expected_kind='offline_fake' if self.simulation else 'windows_job_v1'
                    tree_verified=(proof.get('kind')==expected_kind and proof.get('all_exited') is True
                                   and proof.get('pid')==process.pid and self.clock.monotonic()<=cleanup_deadline)
                    if not self.simulation:
                        from scripts.p6e1b_windows_job import implementation_sha256 as tree_sha256
                        tree_verified=bool(tree_verified and proof.get('handles_released') is True
                            and proof.get('output_drained') is True and proof.get('reader_threads_finished') is True
                            and proof.get('source_sha256')==tree_sha256())
                except Exception as exc:self.error('resource_cleanup_timeout' if isinstance(exc,TimeoutError) else 'resource_cleanup_failed',exc)
            self.event('cleanup_observed',direct_exited=direct_exited,output_drained=drained,tree_verified=tree_verified)
            if not (direct_exited and drained and tree_verified):
                if self.clock.monotonic()>=cleanup_deadline:self.error('resource_cleanup_timeout')
                self.error('resource_cleanup_incomplete');failure='cleanup_incomplete'
                self.pending_resources.append(process)
            if drained:
                for name in ('stdout','stderr'):
                    stream=getattr(process,name,None)
                    if stream is not None:
                        try:stream.close()
                        except Exception as exc:self.error('resource_cleanup_failed',exc);failure='cleanup_incomplete'
                if self.clock.monotonic()>cleanup_deadline:
                    self.error('resource_cleanup_timeout');failure='cleanup_incomplete'
            try:logs,safe=self.save_logs(policy,redact,attempt)
            except Exception as exc:self.error('log_collection_failed',exc);failure=failure or 'failed'
            log_complete=bool(logs) and self.manifest_saved and all(m['available'] and m['persisted'] and not m['truncated'] for m in logs.values())
            if not failure:
                marker_verified=SUCCESS_MARKER in (safe.get('stdout') or '')
                try:structure_verified=self.structure_valid()
                except Exception as exc:self.error('completion_check_failed',exc)
                if not (log_complete and marker_verified and structure_verified):
                    self.error('completion_contract_failed');failure='failed'
            if self.clock.monotonic()>cleanup_deadline:
                self.error('resource_cleanup_timeout');failure='cleanup_incomplete'
            if failure:
                if self.state!=failure:self.transition(failure)
                self.transition('needs_manual_recovery')
            elif not self.errors:self.transition('completed')
            else:self.transition('failed');self.transition('needs_manual_recovery');failure='failed'
            if self.state=='completed' and (not self.ledger_ok or self.clock.monotonic()>cleanup_deadline):
                failure='cleanup_incomplete' if self.clock.monotonic()>cleanup_deadline else 'failed'
                if failure=='cleanup_incomplete':self.error('resource_cleanup_timeout')
                self.transition(failure);self.transition('needs_manual_recovery')
        except Exception as exc:
            self.error('recovery_precondition_failed' if isinstance(exc,RecoveryBlocked) else 'execution_failed',exc)
            if self.native_provider is not None and getattr(exc,'audit',None):
                self.event('native_startup_failure',stage=exc.stage,code=exc.code,audit=exc.audit)
            if policy is not None and redact is not None and process is not None and not self.log_metadata:
                try:logs,safe=self.save_logs(policy,redact,attempt)
                except Exception as logging_exc:self.error('log_collection_failed',logging_exc)
            native_process=self.native_provider.process if self.native_provider else None
            failure='cleanup_incomplete' if process is not None or (native_process is not None and getattr(exc,'cleanup_incomplete',False)) else 'failed'
            if process is not None:self.pending_resources.append(process)
            elif native_process is not None and getattr(exc,'cleanup_incomplete',False):self.pending_resources.append(native_process)
            self.transition(failure);self.transition('needs_manual_recovery')
        self.result={'version':'p6-e1b-initdb-result-v1','run_id':RUN_ID,'environment_plan_sha256':ENVIRONMENT_PLAN_SHA256,
            'executor_sha256':implementation_sha256(),'policy_sha256':audit_sha256(policy_value),'recovery_plan_sha256':audit_sha256(grant),
            'adapter_sha256':adapter_sha256(self.root),'policy_version':policy_value.get('version') if isinstance(policy_value,dict) else None,
            'state':self.state,'failure_state':failure,'history':list(self.history),'events':list(self.events),
            'pid':getattr(process,'pid',None),'returncode':returncode,'direct_exited':direct_exited,'output_drained':drained,
            'tree_verified':tree_verified,'completion_marker_verified':marker_verified,'structure_verified':structure_verified,
            'log_collection_complete':log_complete,'logs':logs,'errors':list(self.errors),'simulation':self.simulation,
            'native_tree_proof':self.native_provider.proof if self.native_provider else None,'attempt_id':self.attempt_id}
        return self.result


def require_completed(result):
    if (not result or result.get('state')!='completed' or result.get('failure_state') is not None
            or result.get('returncode')!=0 or not all(result.get(k) is True for k in (
                'direct_exited','output_drained','tree_verified','completion_marker_verified','structure_verified','log_collection_complete'))
            or result.get('run_id')!=RUN_ID or result.get('environment_plan_sha256')!=ENVIRONMENT_PLAN_SHA256
            or result.get('executor_sha256')!=implementation_sha256()
            or not re.fullmatch('[a-f0-9]{64}',result.get('adapter_sha256') or '')
            or result.get('errors') or not result.get('logs')
            or any(not m.get('available') or not m.get('persisted') or m.get('truncated') for m in result['logs'].values())):
        raise RecoveryBlocked('initialization_not_independently_completed')
    if result.get('simulation') is not False:raise RecoveryBlocked('offline_result_cannot_authorize_database_steps')
