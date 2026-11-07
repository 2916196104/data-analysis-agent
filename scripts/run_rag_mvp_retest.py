"""Manual frozen MVP retest; default is file-only preflight, never real execution."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import logging
from pathlib import Path
import subprocess
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.embedding_budget import Frozen053Budget, require

FIRST = Path('outputs/rag-semantic-retrieval/mvp-first-20261107-v1')
OUTPUT = Path('outputs/rag-semantic-retrieval/mvp-retest-053-v1')
SYNC = Path('outputs/rag-semantic-retrieval/mvp-053-sync-execution-v1/result.json')
RUNNER_SHA = '5d32350d2062d9ec43905229fb3234faa3ac95e04762eaba4e359869027925ae'
SYNC_SHA = '279431baf8cc0eadeafb4566e033021004d855c1d9b0a9e9b5d84db432603e99'
MANIFEST_SHA = 'a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29'


def load(path):
    return strict_json_loads(path.read_text(encoding='utf-8'))


def sync_receipt(root, plan):
    raw = (root/SYNC).read_bytes()
    require(sha256(raw).hexdigest() == SYNC_SHA, 'sync_receipt_changed')
    result = strict_json_loads(raw.decode('utf-8')); m = result['metrics']
    require(result['run_id'] == 4 and result['status'] == 'partial', 'unexpected_sync_run')
    expected = dict(scope_opinion_count=3591, selected_opinion_count=200, attempted_count=200,
        successful_count=200, published_count=200, reused_count=0, failed_count=0, overlength_count=0,
        invalidated_count=0, indexed_current_after=200, not_indexed_count=3391)
    require(all(m[k] == v for k,v in expected.items()), 'sync_counts_changed')
    require(m['locked_budget'] == dict(S=200,N=200,U=0,I=0,P=200), 'sync_budget_changed')
    rows = m['published_rows']
    require(len(rows) == len({r['id'] for r in rows}) == len({r['key'] for r in rows}) == 200
        and {r['key'] for r in rows} == {d['stable_versioned_opinion_key'] for d in plan['selected_documents']}, 'sync_key_set_changed')
    return result


def preflight(root):
    require(Path(sys.executable).resolve() == (root/'.venv/Scripts/python.exe').resolve(), 'use_project_venv')
    require(sys.flags.optimize == 0, 'assertions_required_by_frozen_first_runner')
    require(not (root/OUTPUT).exists(), 'retest_already_claimed_do_not_rerun')
    runner = root/FIRST/'run_once.py'
    require(sha256(runner.read_bytes()).hexdigest() == RUNNER_SHA, 'first_runner_changed')
    manifest = load(root/'tests/fixtures/rag_mvp_acceptance_v1.json')
    require(content_sha256(manifest) == MANIFEST_SHA, 'frozen_manifest_changed')
    budget = Frozen053Budget.load(root)
    values = read_settings(root/'.env'); budget.check_target(values.get('DATABASE_URL'))
    sync_receipt(root, budget.plan())
    from data_analysis_agent.jobs.submission import configured_identity, configured_agent_identity
    _, label = configured_identity(root/'.env')
    settings, _ = configured_agent_identity(root/'.env')
    require(content_sha256([label[k] for k in ('model','base_url','prompt_version')]) == manifest['annotation_profile_sha256'], 'annotation_profile_changed')
    require(settings.rag_python and Path(settings.rag_python).is_file()
        and settings.rag_model_cache and Path(settings.rag_model_cache).is_dir(), 'local_query_encoder_missing')
    protected = load(root/FIRST/'preflight.json')['protected_sha256']
    require(all(sha256((root/p).read_bytes()).hexdigest() == digest for p,digest in protected.items()), 'first_protected_files_changed')
    ignored = subprocess.run(['git','check-ignore','--quiet','--',str(OUTPUT/'receipt.json')],cwd=root,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    require(ignored.returncode == 0, 'private_output_not_git_ignored')
    return dict(manifest=manifest, protected_sha256=protected, manifest_sha256=MANIFEST_SHA,
        sync_receipt_sha256=SYNC_SHA, first_runner_sha256=RUNNER_SHA)


def load_first_runner(root):
    # Execute only after both authorizations. Import does not invoke its guarded main.
    path = root/FIRST/'run_once.py'; raw = path.read_bytes()
    require(sha256(raw).hexdigest() == RUNNER_SHA, 'first_runner_changed')
    module = ModuleType('frozen_first_mvp_runner'); module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def build_review(receipt, manifest, base_url):
    """Readback checks locate/authorize citations; semantic sufficiency stays unjudged."""
    from data_analysis_agent.agents.review_retrieval import validate_retrieval
    cases = {case['id']:case for case in manifest['cases']}
    actions = []
    for item in receipt['cases']:
        identifier = item['id']; case = cases[identifier[:3]]
        task = item.get('task', {}); result = task.get('result') or {}
        entries = [(key,v['data']) for key,v in result.get('results', {}).items() if v.get('kind') == 'semantic_evidence']
        data = [v for _,v in entries]
        raw = item.get('retrieval', {}); evidence = raw.get('evidence', [])
        assessment = data[0].get('assessment', {}) if len(data) == 1 else {}
        refs = (result.get('public_answer') or {}).get('evidence_refs', [])
        allowed = set(assessment.get('permitted_keys', [])); by_key = {r['key']:r for r in evidence}
        errors = []
        if len(evidence) > manifest['top_k'] or len(by_key) != len(evidence): errors.append('candidate_limit_or_key_duplicate')
        if result.get('total_model_calls',0) != 0: errors.append('unexpected_model_calls')
        if evidence and not item.get('p0_validated'): errors.append('live_p0_missing')
        if len(data) > 1: errors.append('unexpected_retrieval_count')
        if data:
            if assessment.get('policy') != 'reported_experience_v2': errors.append('formal_policy_changed')
            restored = deepcopy(data[0]); restored['scope']['base_url'] = base_url
            if not validate_retrieval(restored, item['question']): errors.append('formal_transport_or_permission_invalid')
            if restored['retrieval'] != raw: errors.append('captured_retrieval_mismatch')
        if raw and not data: errors.append('retrieval_result_missing')
        for rank, row in enumerate(evidence, 1):
            start, end, text = row['quote_start'], row['quote_end'], row['review_text']
            if row['rank'] != rank or row['product_id'] not in item['product_ids']: errors.append('rank_or_product_invalid')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or text[start:end] != row['quote']:
                errors.append('unicode_slice_invalid')
            if row['source'] != manifest['scope']['source'] or row['dataset_version'] != manifest['scope']['dataset_version']:
                errors.append('dataset_invalid')
        for ref in refs:
            if not ref.get('opinions') or not entries or ref['source_result_id'] != entries[0][0]:
                errors.append('citation_result_binding_invalid')
            for quote in ref['opinions']:
                row = by_key.get(quote['opinion_key'])
                if row is None or row['key'] not in allowed:
                    errors.append('unpermitted_citation'); continue
                if any(ref[k] != row[k] for k in ('review_id','product_id','published_at','opinion_source')):
                    errors.append('citation_source_mismatch')
                if ref['evidence_ref'] != f"sha256:{row['file_sha256']}#line={row['line_number']}": errors.append('citation_file_mismatch')
                if any(quote[k] != row[k] for k in ('quote','quote_start','quote_end','aspect','sentiment','annotation_id','effective_snapshot_sha256')):
                    errors.append('citation_identity_or_slice_mismatch')
                if quote.get('truncated'): errors.append('licensed_quote_truncated')
        cited_keys = [q['opinion_key'] for ref in refs for q in ref['opinions']]
        if cited_keys != assessment.get('permitted_keys', []) or len(cited_keys) != len(set(cited_keys)):
            errors.append('citation_permission_set_mismatch')
        if case['contract_support'] == 'retrieval_only' and (allowed or refs): errors.append('retrieval_only_permission_bypassed')
        if identifier in ('M10B','M10C'):
            expected = 'unsupported' if identifier == 'M10B' else 'error'
            if result.get('status') != expected or refs or item['query_encodings'] or item['pgvector_queries']:
                errors.append('isolation_or_failure_behavior_invalid')
            if identifier == 'M10C' and result.get('answer'): errors.append('fault_published_answer')
        else:
            if result.get('status') != 'ok' or result.get('validation', {}).get('status') != 'passed' or not item.get('p0_validated'):
                errors.append('normal_action_failed_or_unvalidated')
            if not raw or len(data) != 1: errors.append('normal_action_missing_retrieval')
        actions.append(dict(id=identifier, question=item['question'], product_ids=item['product_ids'],
            contract_support=case['contract_support'], fact_ids=case['fact_ids'], acceptance=case['acceptance'],
            receipt_file=identifier+'.json', structural_errors=sorted(set(errors)),
            live_p0_validated=item.get('p0_validated',False), candidate_count=len(evidence), citation_count=len(refs),
            candidate_judgments=assessment.get('candidate_judgments', []), permitted_keys=list(assessment.get('permitted_keys', [])),
            full_context_pointer=identifier+'.json#/retrieval/evidence',
            semantic_review=dict(sufficient_evidence_in_fixed_candidates=None, answer_directly_supported_in_full_context=None,
                hallucination_or_scope_violation=None, capability_gap=None, pass_result=None, reason=None)))
    return dict(status='pending_actual_evidence_review', actions=actions,
        acceptance_case_ids=list(cases), required_passes=8, required_supported_product_id=manifest['main_product_id'],
        case_passes={identifier:None for identifier in cases}, case_M10_requires_all=['M10A','M10B','M10C','M10D'],
        validated_primary_supported_answers=None, formal_accuracy=None, publication_allowed=False, semantic_verified=False,
        coverage_note='053: 200 indexed; 3391 effective opinions remain unindexed at sync run 4')


def execute_once(root, checked):
    directory = root/OUTPUT
    directory.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    save('attempt.json',dict(status='claimed', automatic_retries=0, manifest_sha256=MANIFEST_SHA, sync_receipt_sha256=SYNC_SHA))
    previous_logging = logging.root.manager.disable
    try:
        runner = load_first_runner(root)
        Frozen053Budget.load(root).check_target(runner.db)
        require(runner.manifest == checked['manifest'], 'runner_manifest_changed')
        require(not runner._processes, 'existing_embedding_process_do_not_take_over')
        runner.OUT = directory
        # Queue/conversation identities are local to this new isolated queue, as in the first execution.
        original_client = runner.TestClient
        class RetestClient(original_client):
            def post(self, url, **kwargs):
                if url == '/api/v1/tasks':
                    kwargs['json']['parameters']['conversation_id'] = OUTPUT.name
                    kwargs['headers']['Idempotency-Key'] = OUTPUT.name+'-'+runner.current['id']
                return super().post(url, **kwargs)
        runner.TestClient = RetestClient
        save('preflight.json', {k:v for k,v in checked.items() if k != 'manifest'})
        runner.main()  # Original create_app -> API -> claim_task -> TaskWorker._execute -> real Agent.
        receipt = load(directory/'receipt.json')
        require(receipt['api_submissions'] <= 13 and receipt['agent_tasks'] <= 13
            and receipt['query_encodings'] <= 11 and receipt['pgvector_queries'] <= 11, 'execution_budget_exceeded')
        require(receipt['external_attempts'] == receipt['business_write_attempts'] == 0, 'forbidden_operation_attempted')
        require(receipt.get('execution_finished') and receipt.get('protected_files_unchanged')
            and receipt.get('embedding_workers_closed') and receipt.get('business_connections_closed')
            and receipt.get('business_connections_readonly'), 'execution_or_cleanup_incomplete')
        expected = [f'M{i:02d}' for i in range(1,10)]+['M10A','M10B','M10C','M10D']
        require([c['id'] for c in receipt['cases']] == expected and receipt['agent_tasks'] == 13, 'fixed_actions_incomplete')
        review = build_review(receipt, checked['manifest'], runner.label['base_url'])
        save('semantic-review.json', review)
        errors = sum(bool(a['structural_errors']) for a in review['actions'])
        summary = dict(status='BLOCKED' if errors else 'PENDING_SEMANTIC_REVIEW', acceptance_status='NOT_YET_ADJUDICATED',
            agent_tasks=receipt['agent_tasks'], query_encodings=receipt['query_encodings'], pgvector_queries=receipt['pgvector_queries'],
            structural_error_actions=errors, actual_case_passes=None, validated_primary_supported_answers=None,
            automatic_retries=0, publication_allowed=False, semantic_verified=False)
        save('summary.json', summary)
        return summary
    except Exception:
        summary = dict(status='BLOCKED', error='retest_stopped_preserve_all_receipts', automatic_retries=0,
            publication_allowed=False, semantic_verified=False)
        save('summary.json', summary)
        return summary
    finally:
        logging.disable(previous_logging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-once',action='store_true')
    parser.add_argument('--authorize-postgres-readonly-acceptance',action='store_true')
    parser.add_argument('--authorize-local-query-encoding',action='store_true')
    args = parser.parse_args(argv)
    try:
        checked = preflight(ROOT)
        if args.execute_once:
            require(args.authorize_postgres_readonly_acceptance and args.authorize_local_query_encoding, 'both_separate_authorizations_required')
            result = execute_once(ROOT,checked)
        else:
            result = dict(status='READY_FOR_RETEST_AUTHORIZATION', execution='not_started', agent_tasks=0,
                query_encodings=0, pgvector_queries=0, maximum_agent_tasks=13, maximum_query_encodings=11,
                maximum_pgvector_queries=11, automatic_retries=0, publication_allowed=False, semantic_verified=False)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result['status']=='BLOCKED' else 0
    except Exception:
        print('{"status":"BLOCKED","error":"offline_preflight_or_authorization_failed","execution":"not_started"}')
        return 1


if __name__ == '__main__': raise SystemExit(main())
