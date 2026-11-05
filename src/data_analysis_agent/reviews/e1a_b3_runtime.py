"""Fixed eight-case B3 controller; no retrieval, model client or database access.

Freeze/preflight never predict. Run needs a separately pinned execution permit and
an OS isolation probe. Reference-based auditing is a separate, post-seal command.
"""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import time
from typing import Literal

from pydantic import Field

from data_analysis_agent.core.serialization import canonical_json, strict_json_loads
from data_analysis_agent.core.validation import Text
from data_analysis_agent.reviews.e1_preparation import load, require
from data_analysis_agent.reviews.e1a_protocol import digest, RULE_VERSION
from data_analysis_agent.reviews.e1a_prediction import build_prediction_input, validate_observed_sources
from data_analysis_agent.reviews.e1a_b3_worker import WorkerRequest, LaunchGrant, FIXED_CASES
from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
from data_analysis_agent.reviews.exploratory_e1 import ExploratoryInput, ExploratorySources
from data_analysis_agent.reviews.query_contract import ContractModel, Digest, ScopeRef
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
from data_analysis_agent.reviews.e1a_windows_worker import AppContainer, IsolationError, stage_python

EXPERIMENT_ID = 'e1a-b3-r0-debug-v1'
RELEASE_PIN = 'fece48317074ddf52b74ba1ec2692094b4b48d8d0b15ed9d1f95213cc5f3f6cc'
PUBLICATION_PIN = '4da5f61566ecfe4c15de33a6297acb139f093e3d68dcfef92be13a99de18a456'
SOURCES_PIN = 'ecb19362428a75b55552d181688c6dff49a820e973b63a3274efef46276eea55'
INPUTS_PIN = 'f14ec3941b20a08e4859b80b9f041d944f66733d397ebef5017c3a28f1e2f4a1'
BUNDLE_PIN = 'f0ee9e6d2a0bcaa16baf17118d97fe7da9843098b919a6335160dd2729ef7b5a'


def read(path):
    return strict_json_loads(Path(path).read_bytes())


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical_json(value) + '\n')


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def source_files():
    base = Path(__file__).resolve().parents[2]
    return {p.relative_to(base).as_posix(): file_sha(p) for p in sorted(base.rglob('*.py'))}


class CasePin(ContractModel):
    case_id: Text
    query_id: Text
    mode: Literal['filtered', 'unfiltered']
    request_sha256: Digest
    input_sha256: Digest
    batch_sha256: Digest
    context_sha256: Digest
    contract_sha256: Digest
    contract_revision: int = Field(ge=1)
    candidate_keys: tuple[Text, ...]


class RunConfig(ContractModel):
    version: Literal['e1a-b3-config-v1'] = 'e1a-b3-config-v1'
    experiment_id: Literal['e1a-b3-r0-debug-v1'] = EXPERIMENT_ID
    data_kind: Literal['synthetic', 'frozen_query']
    engine: Literal['R0'] = 'R0'
    engine_version: Literal['explicit-clauses-zh-v1'] = RULE_VERSION
    experiment_kind: Literal['exploratory_current_observation_history_unproven'] = 'exploratory_current_observation_history_unproven'
    source_policy: Literal['local_saved_sources_cross_time_v1'] = 'local_saved_sources_cross_time_v1'
    history_status: Literal['history_unproven'] = 'history_unproven'
    independent_seal: Literal[False] = False
    context_policy: Literal['quote_full_review_veto'] = 'quote_full_review_veto'
    context_version: Literal['1'] = '1'
    attempts_per_case: Literal[1] = 1
    automatic_retries: Literal[0] = 0
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    scope_ref: ScopeRef
    release_sha256: Digest
    observed_sources_sha256: Digest
    code_files: dict[str, Digest]
    code_sha256: Digest
    cases: tuple[CasePin, ...]


class ExecutionPermit(ContractModel):
    version: Literal['e1a-b3-execution-permit-v1'] = 'e1a-b3-execution-permit-v1'
    action: Literal['b3_eight_case_r0_debug']
    data_kind: Literal['synthetic', 'frozen_query']
    config_sha256: Digest
    approval_ref: Text
    authorization_basis_sha256: Digest


def validate_config(config, *, expected_sha256, check_code=True):
    config = load(config, RunConfig)
    require(digest(config) == expected_sha256, 'b3_config_pin_mismatch')
    require(tuple((r.case_id, r.query_id, r.mode) for r in config.cases) == FIXED_CASES, 'b3_fixed_eight_case_scope')
    require(digest(config.code_files) == config.code_sha256, 'b3_code_manifest_hash')
    if check_code: require(config.code_files == source_files(), 'b3_code_changed_after_freeze')
    if config.data_kind == 'frozen_query':
        require(config.release_sha256 == RELEASE_PIN and config.observed_sources_sha256 == SOURCES_PIN, 'b3_real_identity_pin')
    return config


def project_request(case_id, p, sources):
    """Controller verifies the full saved source set, then sends current-list rows only."""
    verified = validate_observed_sources(p, sources, expected_sources_sha256=p.prepared.observed_sources_sha256)
    batch = p.prepared.candidate_batch; by_key = {r.key:r for r in verified.records}
    comparison = load(dict(version=batch.version, scope_ref=batch.scope_ref.model_dump(mode='json'),
        query_id=batch.query_id, mode=batch.mode, candidates=[dict(
            **by_key[h.key].model_dump(mode='json', exclude={'published_at'}),
            query_id=batch.query_id, mode=batch.mode, rank=h.rank) for h in batch.candidates]), EvidenceBatch)
    return WorkerRequest(case_id=case_id, prediction=p, input_sha256=digest(p), expected_scope=p.contract.scope_ref,
                         comparison_batch=comparison, comparison_sha256=digest(comparison))


def freeze_requests(requests, *, release_sha256, destination, timeout_seconds=30.0):
    requests = [load(r, WorkerRequest) for r in requests]
    require(tuple((r.case_id, r.prediction.prepared.candidate_batch.query_id, r.prediction.prepared.candidate_batch.mode)
                  for r in requests) == FIXED_CASES, 'b3_fixed_eight_case_scope')
    scope = requests[0].expected_scope; kind = requests[0].prediction.data_kind
    sources_sha = requests[0].prediction.prepared.observed_sources_sha256
    pins = []
    shared = {}
    for r in requests:
        p = r.prediction; b = p.prepared.candidate_batch
        require(p.experiment_id == EXPERIMENT_ID and p.data_kind == kind and r.expected_scope == scope
                and p.prepared.observed_sources_sha256 == sources_sha, 'b3_mixed_input_identity')
        pair_contract = (p.contract_sha256, p.contract_revision)
        require(shared.setdefault(b.query_id, pair_contract) == pair_contract, 'b3_shared_contract_version')
        pins.append(CasePin(case_id=r.case_id, query_id=b.query_id, mode=b.mode, request_sha256=digest(r),
            input_sha256=digest(p), batch_sha256=p.prepared.batch_sha256, context_sha256=p.prepared.context_input_sha256,
            contract_sha256=p.contract_sha256, contract_revision=p.contract_revision, candidate_keys=tuple(h.key for h in b.candidates)))
    codes = source_files()
    config = RunConfig(data_kind=kind, scope_ref=scope, release_sha256=release_sha256,
        observed_sources_sha256=sources_sha, code_files=codes, code_sha256=digest(codes), cases=tuple(pins), timeout_seconds=float(timeout_seconds))
    validate_config(config, expected_sha256=digest(config))
    for pin, request in zip(config.cases, requests): validate_request(config, pin, request)
    dest = Path(destination); require(not dest.exists(), 'b3_destination_exists'); dest.mkdir(parents=True)
    for r in requests:
        path = dest / 'runtime' / r.case_id; path.mkdir(parents=True)
        write_new(path / 'request.json', r.model_dump(mode='json'))
    write_new(dest / 'config.json', config.model_dump(mode='json'))
    write_new(dest / 'freeze-receipt.json', dict(config_sha256=digest(config), frozen_at=datetime.now(timezone.utc).isoformat(),
        prediction_execution_authorized=False, real_predictions=0, runtime_contains_reference=False))
    return config


def validate_request(config, pin, value):
    r = load(value, WorkerRequest); p = r.prediction; b = p.prepared.candidate_batch
    require((r.case_id, b.query_id, b.mode, digest(r), digest(p), p.contract_sha256, p.contract_revision,
             p.prepared.batch_sha256, p.prepared.context_input_sha256, tuple(h.key for h in b.candidates)) ==
            (pin.case_id, pin.query_id, pin.mode, pin.request_sha256, pin.input_sha256, pin.contract_sha256,
             pin.contract_revision, pin.batch_sha256, pin.context_sha256, pin.candidate_keys), 'b3_request_identity_or_order_changed')
    require(r.expected_scope == p.contract.scope_ref == config.scope_ref and p.data_kind == config.data_kind
            and p.experiment_id == config.experiment_id and p.prepared.observed_sources_sha256 == config.observed_sources_sha256,
            'b3_request_scope_kind_or_source')
    from data_analysis_agent.reviews.e1a_protocol import validate_prediction_input
    from data_analysis_agent.reviews.evidence_judge import _validate_sources
    validate_prediction_input(p, expected_scope=config.scope_ref, expected_input_sha256=pin.input_sha256)
    require(digest(r.comparison_batch) == r.comparison_sha256, 'b3_comparison_hash')
    _validate_sources(b, r.comparison_batch, config.scope_ref)
    return r


def freeze_b2(*, publication_root, inputs_path, sources_path, bundle_path, destination):
    """Read-only B2 identity checks and runtime preparation. Does NOT call a judge."""
    root = Path(publication_root)
    require(file_sha(root / 'publication-manifest.json') == PUBLICATION_PIN, 'b3_publication_file_pin')
    manifest = read(root / 'publication-manifest.json')
    require(manifest['status'] == 'published_and_independently_validated' and manifest['independent_validation_passed'] == 48,
            'b3_b2_publication_incomplete')
    for name, expected in manifest['files'].items(): require(file_sha(root / name) == expected, 'b3_publication_file_changed')
    release = load(read(root / 'release-v1/reference-release.json'), ReferenceRelease)
    sources = load(read(sources_path), ExploratorySources); inputs = read(inputs_path)
    require(digest(release) == RELEASE_PIN and digest(sources) == SOURCES_PIN and digest(inputs) == INPUTS_PIN, 'b3_b2_input_pins')
    require(len(release.references) == 48, 'b3_b2_reference_count')
    bundle = read(bundle_path); require(digest(bundle) == BUNDLE_PIN, 'b3_question_source_pin')
    questions = {q['query_id']:q['question'] for q in bundle['queries']}
    # Existing saved input package: explicit query/mode map, no reference fields projected.
    prepared = [load(p, ExploratoryInput) for p in inputs]
    by_pair = {(p.candidate_batch.query_id, p.candidate_batch.mode):p for p in prepared}
    require(len(prepared) == len(by_pair) == 48, 'b3_input_count_or_duplicates')
    requests = []
    for cid, qid, mode in FIXED_CASES:
        row = next(r for r in release.references if r.case_id == cid)
        require((row.reference.query_id, row.reference.mode) == (qid, mode), 'b3_b2_case_binding')
        p = by_pair[qid, mode]
        require(p.context_input_sha256 == row.reference.context_input_sha256, 'b3_b2_context_binding')
        prediction = build_prediction_input(question=questions[qid], contract=row.contract,
            contract_revision=row.contract_revision, independent_target_fact_ids=row.reference.independent_target_fact_ids,
            prepared=p, experiment_id=EXPERIMENT_ID, data_kind='frozen_query', expected_scope=release.scope_ref,
            expected_question_sha256=row.question_sha256, expected_contract_sha256=row.reference.contract_sha256,
            expected_batch_sha256=row.reference.batch_sha256)
        requests.append(project_request(cid, prediction, sources))
    return freeze_requests(requests, release_sha256=RELEASE_PIN, destination=destination)


PROBE_SCRIPT = r'''
import ctypes as c,json,sys,os
from ctypes import wintypes as w
targets=json.loads(sys.argv[1]); readable=json.loads(sys.argv[2]); result=[]; allowed=[]
k=c.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];k.CreateFileW.restype=w.HANDLE
k.CloseHandle.argtypes=[w.HANDLE]
for path in targets:
    # Open for read only to check access, close without reading any content.
    h=k.CreateFileW(path,0x80000000,7,None,3,0x02000000,None)
    if h==c.c_void_p(-1).value: result.append(dict(path=path,denied=c.get_last_error()==5,winerror=c.get_last_error()))
    else: k.CloseHandle(h); result.append(dict(path=path,denied=False,winerror=0))
for path in readable:
    h=k.CreateFileW(path,0x80000000,7,None,3,0x02000000,None)
    can_read=h!=c.c_void_p(-1).value
    if can_read: k.CloseHandle(h)
    h=k.CreateFileW(path,0x40000000,7,None,3,0x02000000,None)
    cannot_write=h==c.c_void_p(-1).value and c.get_last_error()==5
    if h!=c.c_void_p(-1).value: k.CloseHandle(h)
    allowed.append(dict(path=path,readable=can_read,write_denied=cannot_write))
print(json.dumps(dict(version='e1a-os-probe-v1',targets=result,runtime_access=allowed)))
'''


def permission_probe(box, executable, *, protected_paths, readable_paths=()):
    require(bool(protected_paths) and all(Path(p).exists() for p in protected_paths), 'b3_probe_targets_missing')
    script = executable.parent / 'isolation-probe.py'
    if not script.exists(): script.write_text(PROBE_SCRIPT, encoding='utf-8')
    readable_paths = readable_paths or (script,)
    require(all(Path(p).is_file() for p in readable_paths), 'b3_probe_runtime_missing')
    result = box.run(executable, ['-I', '-B', str(script), json.dumps([str(Path(p).resolve()) for p in protected_paths]),
                                json.dumps([str(Path(p).resolve()) for p in readable_paths])],
                     cwd=executable.parent, timeout_seconds=15)
    raw = result.pop('stdout'); err = result.pop('stderr')
    receipt = dict(**result, stdout_sha256=sha256(raw).hexdigest(), stderr_sha256=sha256(err).hexdigest())
    try:
        payload = strict_json_loads(raw)
        passed = (result['exit_code'] == 0 and not result['timed_out'] and len(payload['targets']) == len(protected_paths)
            and all(r['denied'] for r in payload['targets']) and len(payload['runtime_access']) == len(readable_paths)
            and all(r['readable'] and r['write_denied'] for r in payload['runtime_access']))
        receipt['access_checks'] = payload['targets']
        receipt['runtime_access'] = payload['runtime_access']
    except (ValueError, KeyError, TypeError): passed = False
    receipt['passed'] = passed
    return receipt


def seal_attempt(destination, *, case_id, input_sha256, process_result):
    """Persist exact pipe bytes first, then interpret. Never repair or retry output."""
    dest = Path(destination); dest.mkdir()
    raw = process_result.get('stdout', b''); stderr = process_result.get('stderr', b'')
    for name, data in (('output.raw', raw), ('stderr.raw', stderr)):
        with (dest / name).open('xb') as stream: stream.write(data)
    status = 'raw_saved'
    if process_result.get('timed_out'): status = 'timeout'
    elif process_result.get('exit_code') != 0: status = 'execution_failed'
    elif len(raw) > 2 * 1024 * 1024 or len(stderr) > 2 * 1024 * 1024: status = 'output_limit_exceeded'
    elif not raw: status = 'missing_output'
    else:
        try:
            from data_analysis_agent.reviews.e1a_protocol import PredictionOutput
            out = load(strict_json_loads(raw), PredictionOutput)
            if out.input_sha256 != input_sha256: status = 'output_identity_failed'
            elif out.execution_status != 'completed': status = 'engine_' + out.execution_status
        except (ValueError, UnicodeError): status = 'invalid_output'
    receipt = dict(version='e1a-b3-attempt-v1', case_id=case_id, input_sha256=input_sha256, attempt=1, automatic_retry=False,
        status=status, raw_sha256=file_sha(dest/'output.raw'), stderr_sha256=file_sha(dest/'stderr.raw'),
        raw_bytes=len(raw), recorded_at=datetime.now(timezone.utc).isoformat(),
        process={k:v for k,v in process_result.items() if k not in ('stdout', 'stderr')})
    write_new(dest/'receipt.json', receipt)
    return receipt


def run_debug(prepared_dir, *, expected_config_sha256, permit, expected_permit_sha256, authorization_basis,
              expected_basis_sha256, destination, protected_paths):
    root = Path(prepared_dir); config = validate_config(read(root/'config.json'), expected_sha256=expected_config_sha256)
    permit = load(permit, ExecutionPermit)
    require(digest(permit) == expected_permit_sha256 and permit.config_sha256 == expected_config_sha256
            and permit.data_kind == config.data_kind and permit.authorization_basis_sha256 == expected_basis_sha256
            and sha256(authorization_basis).hexdigest() == expected_basis_sha256, 'b3_execution_not_authorized')
    requests = [validate_request(config, pin, read(root/'runtime'/pin.case_id/'request.json')) for pin in config.cases]
    if config.data_kind == 'frozen_query':
        project = Path(__file__).resolve().parents[3]
        required = [project/'outputs/rag-retrieval-quality/e1a-b2-publication-20261105/release-v1/reference-release.json',
                    project/'outputs/rag-retrieval-quality/baseline-v1/e1-human-adjudication-session (3).json',
                    project/'outputs/rag-retrieval-quality/q15-live-validation-fix-20261105/bundle.json']
        require({p.resolve() for p in required} <= {Path(p).resolve() for p in protected_paths}, 'b3_reference_probe_scope')
    dest = Path(destination); require(not dest.exists(), 'b3_run_destination_exists')
    require(not (root/'execution-claim.json').exists(), 'b3_main_attempt_already_claimed')
    write_new(root/'execution-claim.json', dict(config_sha256=expected_config_sha256,
        permit_sha256=expected_permit_sha256, destination=str(dest.resolve()), claimed_at=datetime.now(timezone.utc).isoformat()))
    dest.mkdir(parents=True)
    # Exclusive run directory prevents silent replacement/retry of the main attempt.
    (dest/'raw').mkdir(); (dest/'audit').mkdir(); (dest/'runtime').mkdir(); (dest/'isolation').mkdir()
    write_new(dest/'control.json', dict(config_sha256=expected_config_sha256, permit_sha256=expected_permit_sha256,
        authorization_basis_sha256=expected_basis_sha256, code_sha256=config.code_sha256, data_kind=config.data_kind))
    try:
        executable = stage_python(dest)
        staged_sources = {p.relative_to(executable.parent/'src').as_posix():file_sha(p)
                          for p in (executable.parent/'src').rglob('*.py')}
        require(staged_sources == config.code_files, 'b3_staged_source_code_mismatch')
        (executable.parent/'isolation-probe.py').write_text(PROBE_SCRIPT,encoding='utf-8')
    except (OSError, ValueError) as error:
        failed = dict(version='e1a-b3-raw-manifest-v1', config_sha256=expected_config_sha256, code_sha256=config.code_sha256,
            setup_failure_type=type(error).__name__, setup_failure_code=str(error),
            results=[dict(case_id=p.case_id,status='not_attempted_setup_failed',input_sha256=p.input_sha256) for p in config.cases],
            files={p.relative_to(dest).as_posix():file_sha(p) for p in dest.rglob('*') if p.is_file()})
        write_new(dest/'raw-manifest.json',failed)
        return failed
    public = {p.relative_to(dest).as_posix():file_sha(p) for p in sorted((dest/'python').rglob('*')) if p.is_file()}
    write_new(dest/'staged-code.json', public)
    results = []; stopped = False
    for pin, request in zip(config.cases, requests):
        if stopped:
            results.append(dict(case_id=pin.case_id, status='not_attempted_after_stop', input_sha256=pin.input_sha256)); continue
        path = dest/'runtime'/pin.case_id; path.mkdir(); write_new(path/'request.json', request.model_dump(mode='json'))
        grant = LaunchGrant(request_sha256=pin.request_sha256, config_sha256=expected_config_sha256,
            execution_permit_sha256=expected_permit_sha256, authorization_basis_sha256=expected_basis_sha256)
        write_new(path/'launch-grant.json', grant.model_dump(mode='json'))
        process_started = time.perf_counter()
        try:
            with AppContainer(dest) as box:
                box.grant(dest/'python'); box.grant(path)
                proof = permission_probe(box, executable, protected_paths=protected_paths,
                    readable_paths=[path/'request.json',path/'launch-grant.json'])
                write_new(dest/'isolation'/(pin.case_id+'.json'), proof)
                if not proof['passed']: raise IsolationError('b3_os_probe_not_passed')
                result = box.run(executable, ['-I', '-B', '-m', 'data_analysis_agent.reviews.e1a_b3_worker',
                    str((path/'request.json').resolve()), pin.request_sha256, str((path/'launch-grant.json').resolve()), digest(grant)],
                    cwd=executable.parent, timeout_seconds=config.timeout_seconds)
            receipt = seal_attempt(dest/'raw'/pin.case_id, case_id=pin.case_id, input_sha256=pin.input_sha256, process_result=result)
            results.append(receipt)
            # Fail closed on process/JSON failures. Semantic unknown is a normal result.
            stopped = receipt['status'] != 'raw_saved'
        except (IsolationError, OSError) as error:
            results.append(dict(case_id=pin.case_id, status='isolation_or_launch_failed', input_sha256=pin.input_sha256,
                elapsed_seconds=time.perf_counter()-process_started, failure_type=type(error).__name__, failure_code=str(error)))
            stopped = True
    manifest = dict(version='e1a-b3-raw-manifest-v1', config_sha256=expected_config_sha256,
        code_sha256=config.code_sha256, staged_code_sha256=digest(public), results=results,
        files={p.relative_to(dest).as_posix():file_sha(p) for p in sorted(dest.rglob('*')) if p.is_file()},
        completed_at=datetime.now(timezone.utc).isoformat(), history_status='history_unproven', independent_seal=False)
    write_new(dest/'raw-manifest.json', manifest)
    return manifest


def audit_sealed_run(prepared_dir, run_dir, *, expected_config_sha256, expected_manifest_sha256,
                     release, sources, destination):
    """Evaluation-side only; reads immutable raw files, never invokes a predictor."""
    from collections import Counter
    from data_analysis_agent.reviews.e1a_output_audit import audit_output
    from data_analysis_agent.reviews.e1a_evaluation import evaluate_e1a
    root = Path(prepared_dir); run = Path(run_dir).resolve(strict=True)
    config = validate_config(read(root/'config.json'), expected_sha256=expected_config_sha256)
    require(file_sha(run/'raw-manifest.json') == expected_manifest_sha256, 'b3_raw_manifest_pin')
    manifest = read(run/'raw-manifest.json')
    require(manifest['config_sha256'] == expected_config_sha256 and manifest['code_sha256'] == config.code_sha256, 'b3_run_manifest_identity')
    require(tuple(r['case_id'] for r in manifest['results']) == tuple(c[0] for c in FIXED_CASES), 'b3_raw_case_coverage')
    for name, expected in manifest['files'].items():
        path = (run/name).resolve(strict=True)
        require(run in path.parents and file_sha(path) == expected, 'b3_raw_files_changed')
    dest = Path(destination); require(not dest.exists(), 'b3_audit_destination_exists'); dest.mkdir(parents=True)
    rows = []
    for pin, receipt in zip(config.cases, manifest['results']):
        p = validate_request(config, pin, read(root/'runtime'/pin.case_id/'request.json')).prediction
        raw_path = run/'raw'/pin.case_id/'output.raw'
        raw = raw_path.read_bytes() if raw_path.exists() else b''
        if raw_path.exists(): require(file_sha(raw_path) == receipt['raw_sha256'], 'b3_raw_receipt_hash')
        try: output = strict_json_loads(raw) if raw else None
        except (ValueError, UnicodeError): output = {'unparseable_raw_sha256':sha256(raw).hexdigest()}
        audited = audit_output(p, output, release, sources, expected_scope=config.scope_ref,
            expected_input_sha256=pin.input_sha256, expected_sources_sha256=config.observed_sources_sha256,
            expected_release_sha256=config.release_sha256)
        terminal = receipt['status']
        if terminal in ('timeout', 'execution_failed', 'output_limit_exceeded', 'isolation_or_launch_failed', 'not_attempted_after_stop', 'not_attempted_setup_failed'):
            # A parseable partial output must not turn a killed process into success.
            audited = audited.model_copy(update=dict(execution='execution_failed', errors=tuple(sorted(set(audited.errors)|{terminal}))))
        rows.append(audited)
        write_new(dest/(pin.case_id+'.json'), dict(runtime_terminal=terminal, audit=audited.model_dump(mode='json')))
    metrics = evaluate_e1a(rows, expected_pairs=[(p.query_id,p.mode) for p in config.cases],
        expected_audit_sha256={(r.query_id,r.mode):digest(r) for r in rows})
    metrics['runtime_terminal_counts'] = dict(Counter(r['status'] for r in manifest['results']))
    metrics['raw_manifest_file_sha256'] = expected_manifest_sha256
    write_new(dest/'metrics.json', metrics)
    return metrics


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='E1-A B3 fixed eight-case controller. No implicit authorization.')
    sub = parser.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze', help='Read/check B2, write allowlisted runtime only; zero predictions')
    for name in ('publication-root', 'inputs', 'sources', 'bundle', 'destination'): freeze.add_argument('--'+name, required=True)
    check = sub.add_parser('check', help='Verify config, code and all eight requests; zero predictions')
    check.add_argument('--prepared', required=True); check.add_argument('--config-sha256', required=True)
    run = sub.add_parser('run', help='Requires new B3-B execution authorization; never implied by freeze')
    for name in ('prepared', 'config-sha256', 'permit', 'permit-sha256', 'authorization-basis', 'basis-sha256', 'destination'):
        run.add_argument('--'+name, required=True)
    run.add_argument('--protected-path', action='append', required=True)
    audit = sub.add_parser('audit', help='Audit sealed raw outputs in a separate controller step')
    for name in ('prepared', 'run-dir', 'config-sha256', 'raw-manifest-sha256', 'release', 'sources', 'destination'):
        audit.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    if args.command == 'freeze':
        config = freeze_b2(publication_root=args.publication_root, inputs_path=args.inputs, sources_path=args.sources,
            bundle_path=args.bundle, destination=args.destination)
        print(json.dumps(dict(config_sha256=digest(config), cases=len(config.cases), real_predictions=0)))
    elif args.command == 'check':
        config = validate_config(read(Path(args.prepared)/'config.json'), expected_sha256=args.config_sha256)
        for pin in config.cases: validate_request(config, pin, read(Path(args.prepared)/'runtime'/pin.case_id/'request.json'))
        print(json.dumps(dict(checked=8, real_predictions=0, prediction_execution_authorized=False)))
    elif args.command == 'run':
        manifest = run_debug(args.prepared, expected_config_sha256=args.config_sha256, permit=read(args.permit),
            expected_permit_sha256=args.permit_sha256, authorization_basis=Path(args.authorization_basis).read_bytes(),
            expected_basis_sha256=args.basis_sha256, destination=args.destination, protected_paths=args.protected_path)
        print(json.dumps(dict(manifest_sha256=digest(manifest), file_sha256=file_sha(Path(args.destination)/'raw-manifest.json'))))
    else:
        result = audit_sealed_run(args.prepared, args.run_dir, expected_config_sha256=args.config_sha256,
            expected_manifest_sha256=args.raw_manifest_sha256, release=read(args.release), sources=read(args.sources), destination=args.destination)
        print(json.dumps(result['overall']))


if __name__ == '__main__':
    main()
