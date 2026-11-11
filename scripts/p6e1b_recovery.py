"""Fixed F04 recovery coordinator. Default-deny; simulations can only use temporary roots."""
from contextlib import ExitStack
from datetime import datetime, timezone
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest
from urllib.parse import quote
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(ROOT/'tests')]
from scripts import p6e1b_executor as ex
from scripts import p6e1b_windows_job as jobs
from scripts import prepare_p6e1b_recovery as prep
from data_analysis_agent.core.serialization import strict_json_loads

ATTEMPT=ex.RECOVERY_ATTEMPT
VERSION='p6-e1b-recovery-entry-v2'
BIN=Path(r'C:\Program Files\PostgreSQL\18\bin')
CONTROL_SHELL=Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
CODE_FILES=(*prep.CODE_FILES,'scripts/p6e1b_recovery.py')
STAGES=('archive','initdb','identity','runtime_authorization','render','start','provision','live',
        'PG01','PG02','PG03','PG04','PG05','PG06','PG07','PG08')
EXES=('initdb','postgres','pg_ctl','psql','pg_controldata')
PRIVATE_SIDS={'S-1-5-18','S-1-5-32-544'}


def require(condition,reason):
    if not condition:raise ex.RecoveryBlocked(reason)


def read(path):return strict_json_loads(Path(path).read_text(encoding='utf-8-sig'))


def layout(root):
    values=prep.paths(Path(root),ATTEMPT)
    values['attempt']=Path(root)/'.cache/p6e1b-exec/private/attempts'/ATTEMPT
    for p in values.values():prep.plain_path(root,p)
    return values


def inventory_at(root,path):
    path=prep.plain_path(root,Path(path));entries=[];stack=[path]
    while stack:
        p=stack.pop();prep.plain_path(root,p);s=p.lstat()
        require(p.is_dir() or p.is_file(),'non_regular_archive_entry')
        require(not p.is_file() or s.st_nlink==1,'hard_link_not_in_preservation_contract')
        entries.append({'path':'.' if p==path else p.relative_to(path).as_posix(),
            'kind':'directory' if p.is_dir() else 'file','bytes':None if p.is_dir() else s.st_size,
            'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns,'attributes':getattr(s,'st_file_attributes',0),
            'sha256':None if p.is_dir() else prep.file_sha256(p)})
        if p.is_dir():
            with os.scandir(p) as children:stack.extend(Path(c.path) for c in children)
    return {'version':'p6-e1b-source-inventory-v1','run_id':ex.RUN_ID,'source':str(path),
            'entries':sorted(entries,key=lambda r:r['path'])}


def content_rows(manifest):
    rows=manifest['entries'];names=[r['path'] for r in rows]
    require(len(names)==len(set(names)),'duplicate_archive_path')
    for name in names:
        require(name=='.' or (not Path(name).is_absolute() and ':' not in name
            and '\\' not in name and all(p not in ('','.','..') for p in name.split('/'))),'invalid_relative_archive_path')
    return [{k:r[k] for k in ('path','kind','bytes','sha256')} for r in rows]


def source_acls(manifest,rows):
    return prep.checked_acls(manifest,[{k:r[k] for k in ('path','sections','sddl')} for r in rows])


def check_private_acls(rows,manifest,sid):
    require({r['path'] for r in rows}=={r['path'] for r in manifest['entries']}
            and len(rows)==len(manifest['entries']),'incomplete_archive_acl')
    for row in rows:
        rules=row['rules'];allowed={r['sid'] for r in rules if r['allow']}
        require(row['protected'] is True and row['owner']==sid and allowed==PRIVATE_SIDS|{sid}
            and all(r['allow'] and r['full_control'] for r in rules),'archive_acl_not_private')


def code_hashes(root):return {name:prep.file_sha256(Path(root)/name) for name in CODE_FILES}
def binary_hashes():return {**{name:prep.file_sha256(BIN/(name+'.exe')) for name in EXES},
                           'control_shell':prep.file_sha256(CONTROL_SHELL)}


def policy_v2():
    return {**prep.recommended_policy(),'stage_limits':{'identity':10,'client':60,'start':60,'stop':30,'suite':600}}


def pending_plan(root,operator_sid):
    root=Path(root);p=layout(root);bound=prep.bindings(root)
    source=read(p['private']/'source-files.json');acls=read(p['private']/'source-acl.json')
    require(inventory_at(root,p['source'])==source,'source_changed_since_preparation')
    return {'version':VERSION,'run_id':ex.RUN_ID,'attempt_id':ATTEMPT,'route':'B','port':55441,
        'environment_plan_sha256':ex.ENVIRONMENT_PLAN_SHA256,'source':str(p['source']),
        'archive':str(p['archive']),'attempt_directory':str(p['attempt']),
        'databases':bound['environment_plan']['databases'],'roles':bound['environment_plan']['roles'],
        'bootstrap_role':bound['environment_plan']['bootstrap_role'],'operator_sid':operator_sid,
        'policy_sha256':ex.sha256(policy_v2()),'code_sha256':code_hashes(root),'binary_sha256':binary_hashes(),
        'historical_evidence_sha256':bound['historical_evidence_sha256'],
        'original_authorization_sha256':bound['original_authorization_sha256'],
        **bound['native_validation'],'source_inventory_sha256':ex.sha256(source),'source_acl_sha256':ex.sha256(acls),
        'authorized':False,'approved_by':'','expires_at':'','permissions':{n:False for n in prep.OPERATIONS},
        'future_facts':{'archive_completed':False,'new_system_identifier':None},
        'implementation':'offline-tested entry; real execution still requires explicit approval'}


def validate_plan(root,plan,policy,*,approved,simulation=False):
    root=Path(root);p=layout(root)
    require((simulation and root!=ROOT and ROOT not in root.parents and root not in ROOT.parents)
            or (not simulation and root==ROOT),'simulation_cannot_use_real_workspace')
    fixed={'version':VERSION,'run_id':ex.RUN_ID,'attempt_id':ATTEMPT,'route':'B','port':55441,
        'environment_plan_sha256':ex.ENVIRONMENT_PLAN_SHA256,'source':str(p['source']),
        'archive':str(p['archive']),'attempt_directory':str(p['attempt'])}
    require(all(plan.get(k)==v for k,v in fixed.items()),'fixed_recovery_scope_mismatch')
    require(plan.get('future_facts')=={'archive_completed':False,'new_system_identifier':None},'approval_cannot_assert_future_success')
    ex.Policy.parse(policy)
    limits=policy.get('stage_limits',{})
    require(set(limits)=={'identity','client','start','stop','suite'} and all(type(v) in (int,float)
        and math.isfinite(v) and 0<v<=3600 for v in limits.values()),'finite_stage_limits_required')
    require(plan.get('policy_sha256')==ex.sha256(policy),'policy_binding_changed')
    require(plan.get('code_sha256')==code_hashes(root),'recovery_code_changed')
    require(plan.get('binary_sha256')==binary_hashes(),'postgresql_binary_changed')
    base=root/'.cache/p6e1b-env-review'/ex.RUN_ID
    env=read(base/'plan.json')
    require(ex.sha256(env)==ex.ENVIRONMENT_PLAN_SHA256,'original_environment_plan_changed')
    require(env['schema_source_sha256']=={name:prep.file_sha256(root/'src/data_analysis_agent'/name)
            for name in env['schema_source_sha256']},'frozen_schema_sources_changed')
    require(env['preparation_source_sha256']=={name:prep.file_sha256(root/name)
            for name in env['preparation_source_sha256']},'frozen_environment_preparation_changed')
    for key in ('databases','roles','bootstrap_role'):require(plan.get(key)==env[key],'database_or_role_scope_changed')
    require(plan.get('historical_evidence_sha256')=={name:prep.file_sha256(root/name) for name in prep.HISTORY_FILES},'original_failure_history_changed')
    require(plan.get('original_authorization_sha256')==prep.file_sha256(base/'authorization.approved.json'),'original_approval_changed')
    require(re.fullmatch(r'S-1-5-21-\d+-\d+-\d+-\d+',plan.get('operator_sid','')) is not None,'explicit_operator_identity_required')
    jobs.checked_validation(root,plan)
    source=read(p['private']/'source-files.json');acls=read(p['private']/'source-acl.json')
    require(ex.sha256(source)==plan.get('source_inventory_sha256') and ex.sha256(acls)==plan.get('source_acl_sha256'),'preservation_evidence_changed')
    content_rows(source)
    if approved:
        require(plan.get('authorized') is True and isinstance(plan.get('approved_by'),str) and plan['approved_by'].strip(),'independent_recovery_approval_required')
        require(set(plan.get('permissions',{}))==set(prep.OPERATIONS)
                and all(v is True for v in plan['permissions'].values()),'every_operation_requires_permission')
        try:expiry=datetime.fromisoformat(plan['expires_at'])
        except (ValueError,KeyError,TypeError):raise ex.RecoveryBlocked('explicit_approval_expiry_required') from None
        require(expiry.tzinfo is not None and expiry>datetime.now(timezone.utc),'recovery_approval_expired')
    else:
        require(plan.get('authorized') is False and plan.get('approved_by')==plan.get('expires_at')==''
            and all(v is False for v in plan['permissions'].values()),'pending_must_remain_unapproved')
    return env,source,acls


class Ledger:
    def __init__(self,path,bindings,simulation):
        self.path=path;self.number=0;self.state='authorized';self.simulation=simulation
        path.mkdir(parents=True,exist_ok=False)
        self.write('attempt.json',{'run_id':ex.RUN_ID,'attempt_id':ATTEMPT,'simulation':simulation,**bindings})
        self.event('authorized')
    def write(self,name,value):
        require(Path(name).name==name,'artifact_path_mismatch')
        prep.write_new(self.path/name,value)
    def event(self,state,**fields):
        self.number+=1;self.state=state
        self.write(f'{self.number:03d}-{state}.json',{'state':state,'attempt_id':ATTEMPT,'simulation':self.simulation,
            'utc':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),**fields})


class Coordinator:
    def __init__(self,root,plan_file,policy_file,*,simulation=False,platform=None):
        self.root=Path(root);self.paths=layout(self.root);self.plan_file=Path(plan_file);self.policy_file=Path(policy_file)
        work=self.paths['work']
        require(self.plan_file==work/'recovery-plan.approved.json' and self.policy_file==work/'recovery-policy.approved.json','approved_file_locations_required')
        for path in (self.plan_file,self.policy_file):prep.plain_path(root,path)
        self.plan=read(self.plan_file);self.policy=read(self.policy_file);self.simulation=simulation
        self.env,self.source,self.acls=validate_plan(root,self.plan,self.policy,approved=True,simulation=simulation)
        self.hashes=(prep.file_sha256(self.plan_file),prep.file_sha256(self.policy_file))
        require(not self.paths['attempt'].exists(),'attempt_exists_never_automatically_resume')
        require((simulation and platform is not None) or (not simulation and platform is None),'native_platform_cannot_be_injected')
        self.ledger=None
        self.platform=platform if simulation else NativePlatform(self)
        require(self.platform.operator_sid()==self.plan['operator_sid'],'execution_user_mismatch')
        self.receipt=None;self.init_result=None;self.system_id=None;self.context=None;self.position=0
    def guard(self):
        require(self.hashes==(prep.file_sha256(self.plan_file),prep.file_sha256(self.policy_file)),'approval_file_changed_during_attempt')
        validate_plan(self.root,self.plan,self.policy,approved=True,simulation=self.simulation)
    def artifact(self,name,value):self.ledger.write(name,{'simulation':self.simulation,'attempt_id':ATTEMPT,**value})
    def require_stage(self,name):
        require(self.ledger is not None and self.ledger.state=='running' and self.position<len(STAGES)
                and STAGES[self.position]==name,'stage_not_authorized_by_attempt_state')
        self.guard()
    def archive(self):
        self.require_stage('archive')
        p=self.paths;self.platform.preflight()
        require(not p['archive'].exists(),'archive_destination_already_exists')
        require(self.platform.inventory(p['source'])==self.source,'source_content_or_metadata_changed')
        require(source_acls(self.source,self.platform.acls(p['source']))==self.acls,'source_acl_changed')
        self.platform.move(p['source'],p['archive'])
        require(not p['source'].exists() and p['archive'].is_dir(),'archive_move_incomplete')
        self.platform.restrict_acl(p['archive'])
        self.verify_archive()
        observed=self.platform.inventory(p['archive']);acl_rows=self.platform.acls(p['archive'])
        self.receipt={'run_id':ex.RUN_ID,'attempt_id':ATTEMPT,'simulation':self.simulation,'source':str(p['source']),
            'archive':str(p['archive']),'archive_completed':True,'approved_plan_sha256':ex.sha256(self.plan),
            'original_inventory_sha256':ex.sha256(self.source),'original_acl_sha256':ex.sha256(self.acls),
            'observed_inventory_sha256':ex.sha256(observed),'observed_archive_acl_sha256':ex.sha256(acl_rows)}
        self.ledger.write('archive-receipt.json',self.receipt)
    def verify_archive(self):
        p=self.paths;prep.plain_path(self.root,p['archive'])
        require(not p['source'].exists() and p['archive'].is_dir(),'archive_and_empty_target_required')
        actual=self.platform.inventory(p['archive'])
        require(content_rows(actual)==content_rows(self.source),'archived_content_mismatch')
        check_private_acls(self.platform.acls(p['archive']),actual,self.plan['operator_sid'])
    def runtime_grant(self):
        self.require_stage('initdb')
        self.verify_archive()
        require(self.receipt is not None and read(self.paths['attempt']/'archive-receipt.json')==self.receipt,'observed_archive_receipt_required')
        grant=ex.pending_recovery(self.root,self.policy)
        grant.update(authorized=True,approved_by=self.plan['approved_by'],expires_at=self.plan['expires_at'],route='B',
            archive_authorized=True,reinitialize_authorized=True,archive_completed=True,tree_strategy_reviewed=True,
            archive_evidence_sha256=ex.sha256(self.receipt),attempt_id=ATTEMPT,
            **{k:self.plan[k] for k in ('tree_provider_sha256','tree_validation_sha256','tree_evidence_sha256')})
        return grant
    def initialize(self):
        self.require_stage('initdb')
        grant=self.runtime_grant();self.init_result=self.platform.initialize(grant)
        if self.simulation:
            require(self.init_result.get('simulation') is True,'simulation_artifact_required')
            # Reuse the business completion checks, without minting a native artifact.
            ex.require_completed({**self.init_result,'simulation':False})
        else:ex.require_completed(self.init_result)
        require(self.init_result.get('attempt_id')==ATTEMPT,'initdb_attempt_mismatch')
        self.control_sha256=prep.file_sha256(self.paths['source']/'global/pg_control')
        self.artifact('initdb-result.json',{'result':self.init_result})
    def observe_identity(self):
        self.require_stage('identity')
        require(self.init_result is not None and self.init_result['state']=='completed','completed_initdb_required')
        result=self.platform.control_identity()
        require(re.fullmatch('[0-9]{10,30}',result.get('system_identifier','')) is not None
                and result.get('cluster_state')=='shut down' and result.get('source')==str(self.paths['source'])
                and result.get('control_sha256')==self.control_sha256
                and prep.file_sha256(self.paths['source']/'global/pg_control')==self.control_sha256,'new_control_identity_not_confirmed')
        self.system_id=result['system_identifier'];self.artifact('identity.json',result)
    def make_authorization(self):
        self.require_stage('runtime_authorization')
        require(self.system_id is not None,'observed_identifier_required')
        from scripts import prepare_p6e1b_environment as envprep
        manifest=envprep.draft_manifest(self.env)
        manifest.update(authorized=True,synthetic_writes_authorized=True,approved_by=self.plan['approved_by'],expires_at=self.plan['expires_at'],
            recovery_attempt={'attempt_id':ATTEMPT,'approval_sha256':ex.sha256(self.plan),'simulation':self.simulation})
        manifest['environment_preparation'].update(authorized=True,expected_system_identifier=self.system_id,
            **{k:True for k in envprep.PERMISSIONS})
        self.artifact('authorization.runtime.json',manifest)
        self.context=self.platform.runtime_context(manifest)
        require(self.context.authorization_sha256==ex.sha256(read(self.paths['attempt']/'authorization.runtime.json')),'runtime_authorization_digest_mismatch')
    def pg_case(self,name):
        self.require_stage(name)
        suite_deadline=getattr(self.platform,'suite_deadline',None)
        require(suite_deadline is None or time.monotonic()<suite_deadline,'suite_deadline_reached')
        require(self.platform.server_owned(),'server_job_not_owned_or_closed')
        self.platform.live_identity(self.system_id)
        outcome=self.platform.pg_case(name,self.context)
        require(outcome.get('case')==name and outcome.get('status')=='PASS' and outcome.get('tests_run')==1
                and outcome.get('failures')==0 and outcome.get('errors')==0 and outcome.get('skipped')==0,'postgresql_case_failed_stop')
        self.artifact(name+'.json',outcome)
    def run(self):
        self.guard();require(not self.paths['attempt'].exists(),'attempt_exists_never_automatically_resume')
        self.ledger=Ledger(self.paths['attempt'],{'approval_sha256':self.hashes[0],'policy_sha256':self.hashes[1],
            'historical_evidence_sha256':self.plan['historical_evidence_sha256']},self.simulation)
        failure=None;cleanup=None
        try:
            for name in STAGES:
                self.guard();self.ledger.event('running',stage=name)
                limit_key='identity' if name=='identity' else 'start' if name=='start' else 'client'
                stage_deadline=None if name=='initdb' or name.startswith('PG') else time.monotonic()+self.policy['stage_limits'][limit_key]
                if name=='archive':self.archive()
                elif name=='initdb':self.initialize()
                elif name=='identity':self.observe_identity()
                elif name=='runtime_authorization':self.make_authorization()
                elif name=='render':self.platform.render(self.context)
                elif name=='start':
                    self.platform.start();require(self.platform.server_owned(),'server_job_not_owned_or_closed')
                    self.platform.live_identity(self.system_id)
                elif name=='provision':
                    require(self.platform.server_owned(),'server_job_not_owned_or_closed')
                    self.platform.live_identity(self.system_id);self.platform.provision(self.context)
                elif name=='live':
                    require(self.platform.server_owned(),'server_job_not_owned_or_closed')
                    self.platform.live_identity(self.system_id);self.platform.live(self.context)
                else:self.pg_case(name)
                require(stage_deadline is None or time.monotonic()<=stage_deadline,'late_stage_result_not_accepted')
                self.position+=1;self.ledger.event('verified',stage=name)
            self.guard()
        except Exception as exc:
            failure={'error_type':type(exc).__name__,'stage':STAGES[min(self.position,len(STAGES)-1)]}
            self.ledger.event('failed',**failure)
        finally:
            try:
                cleanup=self.platform.stop()
                require(cleanup.get('owned_only') is True and cleanup.get('released') is True,'owned_server_cleanup_incomplete')
            except Exception as exc:
                cleanup={'released':False,'error_type':type(exc).__name__};failure=failure or {'stage':'cleanup','error_type':type(exc).__name__}
                self.ledger.event('cleanup_incomplete')
        state='failed' if failure else 'completed'
        self.ledger.event(state)
        result={'version':VERSION,'run_id':ex.RUN_ID,'attempt_id':ATTEMPT,'simulation':self.simulation,
            'state':state,'verified_stages':list(STAGES[:self.position]),'failure':failure,'cleanup':cleanup,
            'approval_sha256':self.hashes[0],'policy_sha256':self.hashes[1]}
        self.ledger.write('result.json',result);return result


class NativePlatform:
    """Real OS implementation, constructed only after approval. No automatic invocation."""
    def __init__(self,coordinator):
        require(coordinator.root==ROOT and coordinator.simulation is False,'native_root_required')
        self.c=coordinator;self.root=coordinator.root;self.p=coordinator.paths;self.server=None
        self.secrets=None;self.auth_manifest=None;self.suite_deadline=None;self.client_count=0;self.ps_command=None
        self.pending_clients=[];self.init_runner=None
    def operator_sid(self):
        return self.powershell('[Security.Principal.WindowsIdentity]::GetCurrent().User.Value',json_result=False).strip()
    def short(self,argv,limit,tag):
        allowed={
            'control':[str(BIN/'pg_controldata.exe'),'-D',str(self.p['source'])],
            'provision':[str(BIN/'psql.exe'),'-X','-w','-d',self.conninfo('postgres'),'-v','ON_ERROR_STOP=1','-f',str(self.p['attempt']/'provision.private.psql')],
            'stop':[str(BIN/'pg_ctl.exe'),'-D',str(self.p['source']),'-w','-t','15','-m','fast','stop'],
            'acl':self.ps_command}
        require(tag in allowed and list(argv)==allowed[tag],'client_command_not_whitelisted')
        self.c.guard();owned=None;deadline=time.monotonic()+limit;self.client_count+=1
        prefix=f'client-{self.client_count:03d}-{tag}'
        try:
            owned=jobs.OwnedJobProcess(argv,allowed_prefix=argv,startup_cleanup_seconds=self.c.policy['cleanup_seconds'])
            out,err=owned.communicate(timeout=max(0.001,deadline-time.monotonic()))
            require(time.monotonic()<=deadline and owned.poll()==0,'controlled_client_failed_or_late')
            proof=owned.verify_and_close(time.monotonic()+self.c.policy['cleanup_seconds'])
            require(proof['all_exited'] and proof['handles_released'] and proof['reader_threads_finished'],'controlled_client_cleanup_incomplete')
            if self.c.ledger:
                redactor=self.redactor()
                for suffix,value in (('stdout',out),('stderr',err)):
                    require(value is not None,'client_output_unknown')
                    safe=redactor(value);require(len(safe.encode())<=self.c.policy['max_log_bytes'],'client_log_truncated')
                    self.c.ledger.write(prefix+'.'+suffix,safe)
            return out
        except Exception as exc:
            if owned is None:owned=getattr(exc,'owned_process',None)
            if self.c.ledger:self.c.ledger.write(prefix+'.error.json',{'error_type':type(exc).__name__,
                'output_collection':'incomplete_or_unknown','missing_output_is_not_empty':True})
            raise
        finally:
            if owned is not None and not owned.closed:
                if not owned.emergency_cleanup(time.monotonic()+self.c.policy['cleanup_seconds']):
                    self.pending_clients.append(owned);raise ex.RecoveryBlocked('controlled_client_cleanup_incomplete')
    def powershell(self,body,json_result=True):
        script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$ErrorActionPreference='Stop';"+body
        encoded=base64.b64encode(script.encode('utf-16-le')).decode()
        self.ps_command=[str(CONTROL_SHELL),'-NoProfile','-NonInteractive','-EncodedCommand',encoded]
        try:output=self.short(self.ps_command,self.c.policy['stage_limits']['client'],'acl')
        finally:self.ps_command=None
        return json.loads(output) if json_result else output
    @staticmethod
    def ps(value):return "'"+str(value).replace("'","''")+"'"
    def inventory(self,path):return inventory_at(self.root,path)
    def acls(self,path):
        prep.plain_path(self.root,path)
        body=f"$base={self.ps(path)};$rows=@();$stack=[Collections.Generic.Stack[string]]::new();$stack.Push($base);"
        body+="""while($stack.Count){$p=$stack.Pop();$item=Get-Item -LiteralPath $p -Force;
if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'reparse'}
$acl=Get-Acl -LiteralPath $p;$sections=[Security.AccessControl.AccessControlSections]'Owner,Group,Access';
$name=if($p -eq $base){'.'}else{$p.Substring($base.Length+1).Replace('\\','/')};
$rules=@($acl.Access|ForEach-Object{@{sid=$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value;allow=($_.AccessControlType -eq 'Allow');full_control=(($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -eq [Security.AccessControl.FileSystemRights]::FullControl)}});
$rows+=@{path=$name;sections='Owner,Group,Access';sddl=$acl.GetSecurityDescriptorSddlForm($sections);protected=$acl.AreAccessRulesProtected;owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;rules=$rules};
if($item.PSIsContainer){foreach($child in [IO.Directory]::EnumerateFileSystemEntries($p)){$stack.Push($child)}}
else{if(@(Get-Item -LiteralPath $p -Stream *|Where-Object Stream -ne ':$DATA').Count){throw 'extra_stream'}}};
ConvertTo-Json -InputObject @($rows|Sort-Object path) -Depth 6"""
        return self.powershell(body)
    def preflight(self):
        # Read-only OS observation; no connect or port-owner termination.
        value=self.powershell("$port=@(Get-NetTCPConnection -ErrorAction Stop|Where-Object LocalPort -eq 55441);"
            "$p=@(Get-CimInstance Win32_Process -ErrorAction Stop|Where-Object {$_.Name -in @('initdb.exe','postgres.exe','pg_ctl.exe')});"
            "@{port_unused=($port.Count -eq 0);no_postgresql_processes=($p.Count -eq 0)}|ConvertTo-Json")
        require(value['port_unused'] and value['no_postgresql_processes'],'port_or_old_process_state_not_clear')
        # Existing private parent must not grant another ordinary account access.
        root_rows=self.acls(self.root/'.cache/p6e1b-exec/private')
        require(all(r['owner'] in PRIVATE_SIDS|{self.c.plan['operator_sid']} and all(x['sid'] in PRIVATE_SIDS|{self.c.plan['operator_sid']}
            for x in r['rules'] if x['allow']) and any(x['sid']==self.c.plan['operator_sid'] and x['allow'] and x['full_control']
            for x in r['rules']) for r in root_rows),'private_directory_permissions_unsafe')
    def move(self,source,destination):
        require(source==self.p['source'] and destination==self.p['archive'] and not destination.exists(),'exact_archive_paths_required')
        prep.plain_path(self.root,destination);destination.parent.mkdir(parents=True,exist_ok=False)
        prep.plain_path(self.root,source);prep.plain_path(self.root,destination)
        os.rename(source,destination)
    def restrict_acl(self,path):
        require(path==self.p['archive'],'archive_acl_scope_mismatch');self.inventory(path)
        body=f"$base={self.ps(path)};$sid={self.ps(self.c.plan['operator_sid'])};"
        body+="""$stack=[Collections.Generic.Stack[string]]::new();$stack.Push($base);
while($stack.Count){$p=$stack.Pop();$i=Get-Item -LiteralPath $p -Force;if($i.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'reparse'}
$a=if($i.PSIsContainer){[Security.AccessControl.DirectorySecurity]::new()}else{[Security.AccessControl.FileSecurity]::new()};
$a.SetOwner([Security.Principal.SecurityIdentifier]::new($sid));$a.SetAccessRuleProtection($true,$false);
foreach($who in @($sid,'S-1-5-18','S-1-5-32-544')){
$inherit=if($i.PSIsContainer){[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}else{[Security.AccessControl.InheritanceFlags]::None};
$rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($who),[Security.AccessControl.FileSystemRights]::FullControl,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);$a.AddAccessRule($rule)};
Set-Acl -LiteralPath $p -AclObject $a;if($i.PSIsContainer){foreach($child in [IO.Directory]::EnumerateFileSystemEntries($p)){$stack.Push($child)}}};Write-Output 'private ACL applied'"""
        self.powershell(body,json_result=False)
    def credentials(self):
        if self.secrets is None:
            prep.plain_path(self.root,self.root/'.cache/p6e1b-exec/private/credentials.json')
            prep.plain_path(self.root,self.root/'.cache/p6e1b-exec/private/bootstrap.pw')
            self.secrets=read(self.root/'.cache/p6e1b-exec/private/credentials.json')
            require(set(self.secrets)=={'bootstrap','creator','reader','writer','task'}
                and all(isinstance(v,str) and v and '\n' not in v and '\r' not in v and '\x00' not in v for v in self.secrets.values()),'private_credentials_invalid')
            require((self.root/'.cache/p6e1b-exec/private/bootstrap.pw').read_text(encoding='utf-8').rstrip('\r\n')==self.secrets['bootstrap'],'bootstrap_password_sources_disagree')
        return self.secrets
    def redactor(self):
        values=list((self.secrets or {}).values())
        return ex.Redactor(values+[v.replace("'","''") for v in values]+[v.replace('\\','\\\\') for v in values])
    def initialize(self,grant):
        secrets=self.credentials()
        runner=ex.InitdbExecutor(self.root,ex.log_directory(self.root,ATTEMPT),attempt_id=ATTEMPT,
            native_provider=jobs.WindowsJobProvider(self.root))
        self.init_runner=runner
        return runner.run(self.p['source'],self.c.policy,grant,self.root/'.cache/p6e1b-exec/private/bootstrap.pw',list(secrets.values()))
    def control_identity(self):
        before=prep.file_sha256(self.p['source']/'global/pg_control')
        output=self.short([str(BIN/'pg_controldata.exe'),'-D',str(self.p['source'])],self.c.policy['stage_limits']['identity'],'control')
        sid=re.search(r'^Database system identifier:\s*(\d+)\s*$',output,re.M)
        state=re.search(r'^Database cluster state:\s*(.*?)\s*$',output,re.M)
        require(prep.file_sha256(self.p['source']/'global/pg_control')==before,'control_file_changed_during_identity_read')
        return {'system_identifier':sid.group(1) if sid else '', 'cluster_state':state.group(1) if state else '',
                'source':str(self.p['source']),'control_sha256':before}
    def runtime_context(self,manifest):
        from p6e1b_environment import load_authorization,ENV_NAMES
        secrets=self.credentials();values={}
        for role,name in ENV_NAMES.items():
            login='creator' if role=='task_creator' else role
            database='tasks' if role in ('task','task_creator') else 'business'
            values[name]=f"postgresql://{self.c.plan['roles'][login]}:{quote(secrets[login],safe='')}@127.0.0.1:55441/{self.c.plan['databases'][database]}?sslmode=disable"
        values['P6_E1B_AUTHORIZATION_FILE']=str(self.p['attempt']/'authorization.runtime.json')
        self.values=values;self.auth_manifest=manifest
        return load_authorization(values)
    def render(self,context):
        from scripts import prepare_p6e1b_environment as envprep
        sql=envprep.render_sql(self.c.env,context)
        require(sql.count('CREATE ROLE ')==4 and sql.count('CREATE DATABASE ')==2,'unexpected_provisioning_scope')
        self.c.ledger.write('provision.review.psql',sql)
        secrets=self.credentials();passfile=self.p['attempt']/'libpq.passfile'
        def escape(s):return s.replace('\\','\\\\').replace(':','\\:')
        lines=[f"127.0.0.1:55441:{db}:{self.c.plan['bootstrap_role']}:{escape(secrets['bootstrap'])}"
               for db in ('postgres',*self.c.plan['databases'].values())]
        self.c.ledger.write(passfile.name,'\n'.join(lines)+'\n')
        for role,user in self.c.plan['roles'].items():
            old='\\password '+user+'\n';require(sql.count(old)==1,'unexpected_password_template')
            sql=sql.replace(old,f'ALTER ROLE {envprep.ident(user)} PASSWORD {envprep.literal(secrets[role])};\n')
        for db in self.c.plan['databases'].values():
            old=f"\\connect {db} {self.c.plan['bootstrap_role']} 127.0.0.1 55441\n"
            require(sql.count(old)==1,'unexpected_connect_template')
            sql=sql.replace(old,'\\connect -reuse-previous=off '+envprep.literal(self.conninfo(db).replace('\\','\\\\'))+'\n')
        require('\\password ' not in sql,'interactive_password_forbidden')
        self.c.ledger.write('provision.private.psql',sql)
        self.sql_sha256=hashlib.sha256(sql.encode()).hexdigest()
        self.c.artifact('render-receipt.json',{'sql_sha256':self.sql_sha256,'authorization_sha256':context.authorization_sha256})
    def conninfo(self,database):
        require(database in ('postgres',*self.c.plan['databases'].values()),'client_database_not_allowed')
        def q(s):return "'"+str(s).replace('\\','\\\\').replace("'","\\'")+"'"
        return ' '.join(k+'='+q(v) for k,v in {'host':'127.0.0.1','port':'55441','dbname':database,
            'user':self.c.plan['bootstrap_role'],'passfile':self.p['attempt']/'libpq.passfile','sslmode':'disable','connect_timeout':'5'}.items())
    def start(self):
        require(self.server is None and self.c.init_result is not None,'completed_initdb_before_start')
        source=self.p['source'];prep.plain_path(self.root,source)
        from scripts import prepare_p6e1b_environment as envprep
        (source/'pg_hba.conf').write_text(envprep.render_hba(self.c.env),encoding='utf-8')
        with (source/'postgresql.conf').open('a',encoding='utf-8') as f:
            f.write("\nlisten_addresses='127.0.0.1'\nport=55441\nssl=off\nmax_connections=30\npassword_encryption='scram-sha-256'\nlog_statement='none'\nlog_min_error_statement='panic'\n")
        argv=[str(BIN/'pg_ctl.exe'),'-D',str(source),'-l',str(self.p['attempt']/'server.log'),'-w','-t','30','start']
        try:self.server=jobs.OwnedJobProcess(argv,allowed_prefix=argv,startup_cleanup_seconds=self.c.policy['cleanup_seconds'])
        except jobs.JobControlError as exc:
            self.server=getattr(exc,'owned_process',None);raise
        deadline=time.monotonic()+self.c.policy['stage_limits']['start']
        out,err=self.server.communicate(timeout=max(0.001,deadline-time.monotonic()))
        require(time.monotonic()<=deadline and self.server.poll()==0 and self.server_owned(),'server_start_not_owned')
        for suffix,value in (('stdout',out),('stderr',err)):
            require(value is not None,'server_client_output_unknown');safe=self.redactor()(value)
            require(len(safe.encode())<=self.c.policy['max_log_bytes'],'server_client_log_truncated')
            self.c.ledger.write('server-start.'+suffix,safe)
    def server_owned(self):
        if self.server is None or self.server.closed or not self.server.job:return False
        try:pid=int((self.p['source']/'postmaster.pid').read_text().splitlines()[0])
        except (OSError,ValueError,IndexError):return False
        return pid in self.server.members() and self.server.accounting()['active']>0
    def live_identity(self,system_id):
        require(self.server_owned(),'server_not_owned')
        import psycopg
        with psycopg.connect(self.conninfo('postgres'),options='-c statement_timeout=5000') as connection:
            row=connection.execute("SELECT current_database(),current_user,inet_server_addr()::text,inet_server_port(),"
                "current_setting('data_directory'),current_setting('server_version_num')::int/10000,"
                "(SELECT system_identifier::text FROM pg_control_system())").fetchone()
        require(row[0]=='postgres' and row[1]==self.c.plan['bootstrap_role'] and row[2]=='127.0.0.1' and row[3]==55441
            and Path(row[4])==self.p['source'] and row[5]==18 and row[6]==system_id,'live_cluster_identity_mismatch')
    def provision(self,context):
        path=self.p['attempt']/'provision.private.psql'
        require(prep.file_sha256(path)==self.sql_sha256,'rendered_sql_changed')
        argv=[str(BIN/'psql.exe'),'-X','-w','-d',self.conninfo('postgres'),'-v','ON_ERROR_STOP=1','-f',str(path)]
        self.short(argv,self.c.policy['stage_limits']['client'],'provision')
    def database_guard(self):
        import psycopg
        import httpx
        original=psycopg.connect;allowed={self.values[name] for name in self.values if name.endswith('_DSN')}
        def connect(target,*args,**kwargs):
            self.c.guard();require(target in allowed,'database_connection_outside_approved_targets')
            kwargs['connect_timeout']=5;return original(target,*args,**kwargs)
        stack=ExitStack();stack.enter_context(patch.dict(os.environ,self.values))
        stack.enter_context(patch('psycopg.connect',side_effect=connect))
        for name in ('httpx.Client.send','httpx.AsyncClient.send'):
            stack.enter_context(patch(name,side_effect=AssertionError('external HTTP forbidden')))
        return stack
    def live(self,context):
        import p6e1b_postgresql_integration as pg
        with self.database_guard():pg.verify_live_environment(context)
        self.suite_deadline=time.monotonic()+self.c.policy['stage_limits']['suite']
    def pg_case(self,name,context):
        import p6e1b_postgresql_integration as pg
        require(self.suite_deadline is not None and time.monotonic()<self.suite_deadline,'suite_deadline_reached')
        names=[n for n in unittest.defaultTestLoader.getTestCaseNames(pg.P6E1BPostgresTests) if n.startswith('test_'+name+'_')]
        require(len(names)==1,'postgresql_case_identity_invalid')
        with self.database_guard():
            suite=unittest.TestSuite([pg.P6E1BPostgresTests(names[0])]);result=pg.SafeResult();suite.run(result)
        require(time.monotonic()<=self.suite_deadline,'late_postgresql_case_result')
        return {'case':name,'status':'PASS' if result.wasSuccessful() else 'FAIL','tests_run':result.testsRun,
                'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)}
    def stop(self):
        resources=list(self.pending_clients)
        if self.init_runner is not None:resources.extend(self.init_runner.pending_resources)
        released=True
        for resource in resources:
            if not resource.closed:
                released=resource.emergency_cleanup(time.monotonic()+self.c.policy['cleanup_seconds']) and released
        if self.server is None:return {'owned_only':True,'released':released,'server_started':False}
        graceful=False
        try:
            if self.server_owned():
                self.short([str(BIN/'pg_ctl.exe'),'-D',str(self.p['source']),'-w','-t','15','-m','fast','stop'],
                    self.c.policy['stage_limits']['stop'],'stop');graceful=True
        finally:
            if not graceful:self.server.terminate()
            self.server.wait(timeout=self.c.policy['termination_seconds'])
            if not self.server.drained:self.server.communicate(timeout=self.c.policy['cleanup_seconds'])
            proof=self.server.verify_and_close(time.monotonic()+self.c.policy['cleanup_seconds'])
        return {'owned_only':True,'released':bool(released and proof['all_exited'] and proof['handles_released']),'graceful':graceful}


def require_native_result(value):
    require(value.get('version')==VERSION and value.get('run_id')==ex.RUN_ID and value.get('attempt_id')==ATTEMPT
        and value.get('simulation') is False and value.get('state')=='completed'
        and value.get('verified_stages')==list(STAGES) and value.get('failure') is None
        and value.get('cleanup',{}).get('released') is True,'native_recovery_not_completed')


def run_approved(root=ROOT):
    p=layout(root)
    try:
        coordinator=Coordinator(root,p['work']/'recovery-plan.approved.json',p['work']/'recovery-policy.approved.json')
        result=coordinator.run();require_native_result(result)
        print(json.dumps({'status':'PASS','attempt_id':ATTEMPT}));return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED_OR_FAILED','error_type':type(exc).__name__,'attempt_id':ATTEMPT}));return 2


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('check','run'));args=parser.parse_args()
    if args.stage=='run':return run_approved()
    try:
        p=layout(ROOT);plan=read(p['work']/'recovery-plan.pending.json');policy=read(p['work']/'recovery-policy.pending.json')
        validate_plan(ROOT,plan,policy,approved=False)
        print(json.dumps({'status':'PENDING_BINDINGS_VALID','real_recovery':'BLOCKED','attempt_id':ATTEMPT}));return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','error_type':type(exc).__name__}));return 2


if __name__=='__main__':raise SystemExit(main())
