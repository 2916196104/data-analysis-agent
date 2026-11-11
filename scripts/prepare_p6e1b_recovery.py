"""F04 read-only recovery preparation. No archive, process launch, SQL or authorization writer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import p6e1b_executor as ex
from scripts import p6e1b_windows_job as jobs

VERSION='p6-e1b-recovery-preparation-v1'
OPERATIONS=('archive_partial_cluster','restrict_archive_acl','reinitialize_fixed_target',
    'apply_reviewed_time_limits','start_isolated_instance','create_roles_and_two_databases',
    'initialize_test_schema','establish_test_connections','write_synthetic_fixtures_and_run_pg01_pg08',
    'record_new_observed_system_identifier','reuse_private_test_credentials','stop_only_attempt_owned_instance')
CODE_FILES=('scripts/p6e1b_executor.py','scripts/p6e1b_windows_job.py',
    '.cache/p6e1b-exec/execute.py','scripts/prepare_p6e1b_environment.py',
    'tests/p6e1b_environment.py','tests/p6e1b_postgresql_integration.py',
    'scripts/prepare_p6e1b_recovery.py')
HISTORY_FILES=tuple('.cache/p6e1b-exec/'+p for p in (
    'execution-journal.json','failure-initialize.json','partial-cluster-state.json','execution-summary.json'))
ENTRY_GAPS={
    'attempt_scope':'InitdbExecutor and the entry must accept an approved attempt_id with a new private ledger; do not reset the old ledger.',
    'history_link':'Replace unconditional historical FAIL rejection only for a new explicitly approved attempt bound to unchanged historical hashes.',
    'archive_receipt':'Derive archive_completed and archive_evidence_sha256 from verified archive evidence; do not ask the approver to assert a future fact.',
    'post_init_commands':'Keep the generic fallback closed; supply exact stage-owned pg_controldata/start/provision operations with bounded cleanup.',
    'runtime_identity':'Write a separate attempt authorization derived from the new approval and observed system identifier; never edit the old approval.',
    'sql_credentials':'Use a private libpq passfile and private materialized SQL, with no password argv/env or interactive password prompts; keep the reviewed SQL template immutable.',
    'server_job_lifetime':'Run start/provision/live/tests in one recovery coordinator holding the server Job until authorized shutdown; do not close a kill-on-close Job at the end of a short start CLI.',
    'stage_outputs':'Bind render/start/provision/live outputs and run markers to the new attempt, approval digest and completed initdb result.'}


class PreparationBlocked(ValueError):pass


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def file_sha256(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_new(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def plain_path(root,path):
    root=Path(root);path=Path(path)
    if not path.is_absolute() or path==root or root not in path.parents or path.resolve()!=path:
        raise PreparationBlocked('path_outside_fixed_workspace_or_reparsed')
    for parent in (path,*path.parents):
        if parent==root:break
        try:info=parent.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise PreparationBlocked('reparse_point_rejected')
    return path


def paths(root,attempt_id):
    if not isinstance(attempt_id,str) or not re.fullmatch('[a-f0-9]{32}',attempt_id):
        raise PreparationBlocked('explicit_unique_attempt_id_required')
    root=Path(root)
    source=ex.target_directory(root)
    archive=root/'.cache/p6e1b-exec/private/archives'/ex.RUN_ID/attempt_id/'cluster'
    work=root/'.cache/p6e1b-f04'/attempt_id
    private=root/'.cache/p6e1b-exec/private/recovery-preparation'/attempt_id
    for p in (source,archive,work,private):plain_path(root,p)
    if source==archive or source in archive.parents or archive in source.parents or source.drive!=archive.drive:
        raise PreparationBlocked('archive_must_be_disjoint_and_same_volume')
    return {'source':source,'archive':archive,'work':work,'private':private}


def inventory(root):
    """Only the fixed cluster, refusing a reparse before reading an entry."""
    root=Path(root);source=plain_path(root,ex.target_directory(root))
    if not source.is_dir():raise PreparationBlocked('original_partial_cluster_missing')
    entries=[];stack=[source]
    while stack:
        path=stack.pop();plain_path(root,path);info=path.lstat()
        directory=stat.S_ISDIR(info.st_mode)
        if not directory and not stat.S_ISREG(info.st_mode):raise PreparationBlocked('non_regular_entry')
        entries.append({'path':'.' if path==source else path.relative_to(source).as_posix(),
            'kind':'directory' if directory else 'file','bytes':None if directory else info.st_size,
            'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns,
            'attributes':getattr(info,'st_file_attributes',0),
            'sha256':None if directory else file_sha256(path)})
        if directory:
            with os.scandir(path) as children:stack.extend(Path(c.path) for c in children)
    return {'version':'p6-e1b-source-inventory-v1','run_id':ex.RUN_ID,'source':str(source),
        'entries':sorted(entries,key=lambda r:r['path'])}


def checked_acls(source_manifest,raw):
    if not isinstance(raw,list):raise PreparationBlocked('acl_list_required')
    names=[r['path'] for r in raw]
    if len(set(names))!=len(names) or set(names)!={r['path'] for r in source_manifest['entries']}:
        raise PreparationBlocked('complete_unique_acl_coverage_required')
    for row in raw:
        if (row.get('sections')!='Owner,Group,Access' or not isinstance(row.get('sddl'),str)
                or not row['sddl'].startswith('O:') or 'G:' not in row['sddl'] or 'D:' not in row['sddl']):
            raise PreparationBlocked('owner_group_dacl_required')
    return {'version':'p6-e1b-source-acl-v1','source_inventory_sha256':ex.sha256(source_manifest),
        'sacl':'NOT COLLECTED; no audit privilege requested',
        'entries':[{'path':r['path'],'sections':r['sections'],'sddl':r['sddl'],
                    'sddl_sha256':hashlib.sha256(r['sddl'].encode()).hexdigest()}
                   for r in sorted(raw,key=lambda r:r['path'])]}


def acl_capture_script(source,inventory_file,output_file):
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    return f"""# Read-only ACL capture. Does not launch PostgreSQL or change permissions.
$ErrorActionPreference='Stop'
$source={literal(source)}
$workspace={literal(ROOT)}
$manifest=Get-Content -LiteralPath {literal(inventory_file)} -Raw | ConvertFrom-Json
if ($manifest.source -ne $source -or $manifest.run_id -ne '{ex.RUN_ID}') {{ throw 'fixed source mismatch' }}
$ancestor=$source
while ($ancestor -ne $workspace) {{
    $item=Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {{ throw 'reparse ancestor' }}
    $ancestor=[IO.Directory]::GetParent($ancestor).FullName
    if (-not $ancestor) {{ throw 'workspace boundary missing' }}
}}
$sections=[System.Security.AccessControl.AccessControlSections]::Owner -bor [System.Security.AccessControl.AccessControlSections]::Group -bor [System.Security.AccessControl.AccessControlSections]::Access
$rows=@(foreach ($entry in $manifest.entries) {{
    if ($entry.path -ne '.' -and ($entry.path -match '(^|/)\\.\\.(/|$)' -or [IO.Path]::IsPathRooted($entry.path))) {{ throw 'invalid relative path' }}
    $target=if ($entry.path -eq '.') {{ $source }} else {{ Join-Path $source $entry.path }}
    $target=[IO.Path]::GetFullPath($target)
    if ($target -ne $source -and -not $target.StartsWith($source+'\\',[StringComparison]::OrdinalIgnoreCase)) {{ throw 'outside source' }}
    $probe=$target
    while ($probe) {{
        $item=Get-Item -LiteralPath $probe -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {{ throw 'reparse point' }}
        if ($probe -eq $source) {{ break }}
        $probe=[IO.Directory]::GetParent($probe).FullName
    }}
    $acl=Get-Acl -LiteralPath $target -ErrorAction Stop
    [ordered]@{{path=$entry.path;sections='Owner,Group,Access';sddl=$acl.GetSecurityDescriptorSddlForm($sections)}}
}})
$text=ConvertTo-Json -InputObject $rows -Depth 5
$stream=[IO.File]::Open({literal(output_file)},[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try {{ $bytes=[Text.UTF8Encoding]::new($false).GetBytes($text);$stream.Write($bytes,0,$bytes.Length) }} finally {{ $stream.Dispose() }}
Write-Output 'Fixed-cluster ACL captured; no permissions changed.'
"""


def bindings(root):
    root=Path(root)
    code={name:file_sha256(root/name) for name in CODE_FILES}
    history={name:file_sha256(root/name) for name in HISTORY_FILES}
    folder=root/'.cache/p6e1b-env-review'/ex.RUN_ID
    plan=read(folder/'plan.json')
    if (ex.sha256(plan)!=ex.ENVIRONMENT_PLAN_SHA256 or plan['port']!=55441
            or Path(plan['cluster_directory'])!=ex.target_directory(root)):
        raise PreparationBlocked('environment_plan_changed')
    receipt={'tree_provider_sha256':jobs.implementation_sha256(),
        'tree_validation_sha256':file_sha256(root/'.cache/p6e1b-f03/native-summary.json'),
        'tree_evidence_sha256':file_sha256(root/'.cache/p6e1b-f03/native-evidence.json')}
    jobs.checked_validation(root,receipt)
    return {'code_sha256':code,'historical_evidence_sha256':history,'native_validation':receipt,
        'environment_plan':plan,'original_authorization_sha256':file_sha256(folder/'authorization.approved.json')}


def recommended_policy():
    return {'version':ex.POLICY_VERSION,'execution_seconds':180,'termination_seconds':10,
        'cleanup_seconds':30,'max_log_bytes':1048576}


def make_draft(root,attempt_id,source_manifest,acls,bound):
    p=paths(root,attempt_id);policy=recommended_policy();ex.Policy.parse(policy)
    if p['archive'].exists():raise PreparationBlocked('archive_destination_already_exists')
    if source_manifest['source']!=str(p['source']):raise PreparationBlocked('source_manifest_scope_mismatch')
    if acls['source_inventory_sha256']!=ex.sha256(source_manifest):raise PreparationBlocked('acl_manifest_binding_mismatch')
    draft=ex.pending_recovery(root,policy)
    draft.update(preparation_version=VERSION,attempt_id=attempt_id,route='B',
        archive_directory=str(p['archive']),attempt_directory=str(Path(root)/'.cache/p6e1b-exec/private/attempts'/attempt_id),
        source_inventory_sha256=ex.sha256(source_manifest),source_acl_sha256=ex.sha256(acls),
        **bound['native_validation'],permissions={name:False for name in OPERATIONS},
        bindings={k:v for k,v in bound.items() if k not in ('native_validation','environment_plan')},
        databases=bound['environment_plan']['databases'],roles=bound['environment_plan']['roles'],port=55441,
        archive_acl_policy='protected_current_owner_SYSTEM_Administrators_on_all_entries; preserve original SDDL as evidence',
        archive_verification='same relative paths, bytes and SHA-256; original ACL snapshot retained; restrictive archive ACL checked separately',
        entry_readiness='BLOCKED: minimal entry changes listed, not implemented by this preparation tool',
        required_entry_changes=ENTRY_GAPS,
        runtime_facts={'archive_completed':False,'archive_evidence_sha256':'','new_system_identifier':None,
            'note':'Future observations, not approval fields. No manual success or identifier backfill.'})
    return policy,draft


def snapshot(root,attempt_id):
    p=paths(root,attempt_id)
    if p['work'].exists() or p['private'].exists():raise PreparationBlocked('attempt_materials_already_exist')
    if p['archive'].exists():raise PreparationBlocked('archive_destination_already_exists')
    source=inventory(root)
    p['work'].mkdir(parents=True,exist_ok=False);p['private'].mkdir(parents=True,exist_ok=False)
    write_new(p['private']/'source-files.json',source)
    write_new(p['work']/'capture-source-acl.ps1',acl_capture_script(p['source'],p['private']/'source-files.json',p['private']/'source-acl.raw.json'))
    return {'status':'SNAPSHOT_ONLY','attempt_id':attempt_id,'files':sum(r['kind']=='file' for r in source['entries']),
        'directories_including_root':sum(r['kind']=='directory' for r in source['entries']),
        'work_directory':str(p['work']),'archive_moved':False}


def draft(root,attempt_id):
    p=paths(root,attempt_id);source=read(p['private']/'source-files.json')
    if inventory(root)!=source:raise PreparationBlocked('source_changed_since_snapshot')
    acls=checked_acls(source,read(p['private']/'source-acl.raw.json'))
    policy,plan=make_draft(root,attempt_id,source,acls,bindings(root))
    write_new(p['private']/'source-acl.json',acls)
    write_new(p['work']/'recovery-policy.pending.json',policy)
    write_new(p['work']/'recovery-plan.pending.json',plan)
    return {'status':'PREPARATION_ONLY','attempt_id':attempt_id,'plan_sha256':ex.sha256(plan),
        'policy_sha256':ex.sha256(policy),'acl_entries':len(acls['entries']),
        'execution_admission':'BLOCKED','approved_by':'','authorized':False}


def check(root,attempt_id):
    p=paths(root,attempt_id);plan=read(p['work']/'recovery-plan.pending.json');policy=read(p['work']/'recovery-policy.pending.json')
    source=read(p['private']/'source-files.json');acls=read(p['private']/'source-acl.json')
    expected_policy,expected_plan=make_draft(root,attempt_id,source,acls,bindings(root))
    if policy!=expected_policy or plan!=expected_plan or inventory(root)!=source:
        raise PreparationBlocked('pending_materials_or_source_changed_regenerate_not_promote')
    return {'status':'PENDING_MATERIALS_MATCH_CURRENT_EVIDENCE','attempt_id':attempt_id,
        'plan_sha256':ex.sha256(plan),'policy_sha256':ex.sha256(policy),'execution_admission':'BLOCKED',
        'entry_changes_implemented':False,'archive_moved':False,'postgresql_calls':0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('snapshot','draft','check'));parser.add_argument('--attempt-id',required=True)
    args=parser.parse_args(argv)
    try:
        result=globals()[args.stage](ROOT,args.attempt_id)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (PreparationBlocked,ex.RecoveryBlocked,jobs.JobControlError,OSError,ValueError,KeyError,TypeError):
        print(json.dumps({'status':'BLOCKED','reason':'fixed_scope_evidence_or_materials_invalid','postgresql_calls':0}));return 2


if __name__=='__main__':raise SystemExit(main())
