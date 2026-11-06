"""One NEW fixed five-candidate experiment. Default: offline preflight only."""
import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT/'src', ROOT/'tests'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from scripts.run_rag_shadow_quality import prepare_batch, compare, FACTORY, EXPECTED_PROMPT
from scripts.run_rag_shadow_boundaries import _save, _usage_problem
from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents import review_structured_evidence as protocol
from data_analysis_agent.agents import review_shadow_diagnostics as diagnostics
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads

FIXTURE = ROOT/'tests/fixtures/shadow_single_four_state_v1.json'
FIXTURE_SHA256 = '89dd2aef2d2ca5f5d1ddcb65d0c1d70b00943d8b00aa83cb6abbcde065b46fde'
INPUT_ESTIMATE = 7816
INPUT_SHA256 = '600d1797517befb5cebb86ab3d67792c850d2fb614eff9cfe97a63a491d2fab9'
OUTPUT_DIR = ROOT/'outputs/rag-semantic-retrieval/four-state-single-synthetic-v1'


def load_fixed():
    raw = FIXTURE.read_bytes()
    if sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError('fixed_fixture_changed')
    data = strict_json_loads(raw.decode('utf-8'))
    if ([c['id'] for c in data['cases']] != ['D71','D72','D73','D74','D75']
            or Counter(c['expected_status'] for c in data['cases']) != dict(
                supported=1, not_supported=1, refuted=2, unknown=1)):
        raise ValueError('fixed_cases_changed')
    return data


def preflight(shadow_env):
    data = load_fixed()
    if (provider.MODEL, provider.PROMPT_VERSION, sha256(provider.SYSTEM_PROMPT.encode()).hexdigest(),
            provider.INPUT_LIMIT, provider.OUTPUT_LIMIT) != (
            'deepseek-ai/DeepSeek-V4.1-Flash', 'p5-shadow-siliconflow-instructions-v2', EXPECTED_PROMPT, 8000, 2500):
        raise ValueError('fixed_protocol_changed')
    for name, schema in protocol.schemas().items():
        if schema != strict_json_loads((ROOT/'docs/schemas'/f'p5b1-{name.replace("_", "-")}.schema.json').read_text(encoding='utf-8')):
            raise ValueError('schema_changed')
    _, _, request = prepare_batch(data['cases'])
    payload = protocol.model_payload(request)
    estimate = provider.estimated_input_tokens(provider.request_body(payload))
    if estimate != INPUT_ESTIMATE or content_sha256(request.model_dump(mode='json')) != INPUT_SHA256:
        raise ValueError('fixed_request_changed')
    for field in ('expected_status','expected_reason','veto_text','target_fact','provenance'):
        if field in payload:
            raise ValueError('expectation_leak')
    if any(c['expected_reason'] in payload for c in data['cases']):
        raise ValueError('expectation_leak')
    missing = list(provider.preflight(shadow_env, estimated_budget_risk_accepted=True)['missing'])
    ignored = subprocess.run(['git','check-ignore','-q','--',str(OUTPUT_DIR/'diagnostics/failure.json')],
        cwd=ROOT, capture_output=True, timeout=5).returncode == 0
    if not ignored:
        missing.append('diagnostic_path_not_git_ignored')
    if OUTPUT_DIR.exists():
        missing.append('experiment_already_claimed')
    else:
        diagnostics.destination(str(OUTPUT_DIR))
    return dict(status='blocked' if missing else 'ready_pending_explicit_single_authorization', missing=missing,
        cases=[c['id'] for c in data['cases']], candidate_count=5, request_id=request.request_id,
        fixture_sha256=FIXTURE_SHA256, input_sha256=INPUT_SHA256, input_estimate=estimate,
        model=provider.MODEL, prompt_version=provider.PROMPT_VERSION,
        output_dir=str(OUTPUT_DIR), development_diagnostic_dir=str(OUTPUT_DIR/'diagnostics'),
        diagnostic_path_git_ignored=ignored, diagnostic_parent_created_by_run=True,
        diagnostic_raw_limit_bytes=diagnostics.MAX_RAW_BYTES, diagnostic_file_limit_bytes=diagnostics.MAX_FILE_BYTES,
        expected_answers_sent=False, max_model_calls=1, max_retries=0,
        input_limit=8000, output_limit=2500, outer_deadline_seconds=30,
        sdk_phase_timeouts=dict(read=20, connect=5, write=10, pool=5),
        conditional_peak_cost_cny='0.036', guaranteed_bill_cap=False, spending_authorized=False,
        real_llm_calls=0, real_database_calls=0, real_embedding_calls=0,
        publication_allowed=False, semantic_verified=False)


def run_authorized(shadow_env, *, send_authorized=False, estimated_budget_risk_accepted=False):
    if not send_authorized or not estimated_budget_risk_accepted:
        raise ValueError('explicit_send_and_fee_authorization_required')
    check = preflight(shadow_env)
    if check['missing']:
        raise ValueError('single_preflight_incomplete')
    data = load_fixed()
    contract, result, request = prepare_batch(data['cases'])
    config = provider.scoped_config(shadow_env).model_copy(update={
        'estimated_budget_risk_accepted': True, 'usage_file': str(OUTPUT_DIR/'provider-usage.json')})
    # Permanent once-only claim, including cancelled or failed attempts. No alternate output argument.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    summary = dict(status='stopped', stop_reason=None, model_calls=None, max_model_calls=1,
        structure_valid=False, comparison=compare(data['cases'], request, None),
        explicit_send_authorized=True, estimated_budget_risk_accepted=True,
        publication_allowed=False, semantic_verified=False, actual_bill_known=False)
    try:
        _save(OUTPUT_DIR/'preflight.json', check)
        _save(OUTPUT_DIR/'model-input.json', strict_json_loads(protocol.model_payload(request)))
        _save(OUTPUT_DIR/'contract.json', contract.model_dump(mode='json'))
        _save(OUTPUT_DIR/'retrieval.json', result.to_dict())
        adapter = StructuredEvidenceShadow(settings=ShadowSettings(enabled=True, timeout_seconds=30),
            judge_factory=JudgeFactory(entrypoint=FACTORY, config_json=canonical_json(config.model_dump(mode='json'))),
            development_diagnostic_dir=str(OUTPUT_DIR/'diagnostics'))
        run = adapter.run(contract, result, trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
                          request_id=request.request_id)
        _save(OUTPUT_DIR/'shadow-execution.json', run.debug_record())
        if run.judgments is not None:
            _save(OUTPUT_DIR/'shadow-judgments.json', run.judgments.model_dump(mode='json'))
        valid = run.outcome.execution_state == 'validated_shadow'
        summary.update(execution_state=run.outcome.execution_state, reason_codes=list(run.outcome.reason_codes),
            structure_valid=valid, total_ms=run.diagnostics['total_ms'],
            failure_diagnostic_saved=run.diagnostics.get('failure_diagnostic_saved', False))
        # Never score an invalid diagnostic as validated evidence.
        if valid:
            summary['comparison'] = compare(data['cases'], request, run.judgments)
        usage_path = OUTPUT_DIR/'provider-usage.json'
        usage = strict_json_loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else None
        summary.update(usage=usage, model_calls=usage.get('model_calls') if usage else None)
        problem = _usage_problem(usage, request.request_id, run.diagnostics['input_sha256'])
        if not problem:
            summary['known_peak_cost_cny'] = usage['cost_cny_at_peak_rates']
        if run.outcome.publication_allowed or run.outcome.semantic_verified or run.outcome.permitted_target_fact_ids:
            problem = 'shadow_isolation_violation'
        elif not run.diagnostics['reaped'] or not run.diagnostics['threads_released']:
            problem = 'worker_cleanup_failed'
        elif not valid:
            problem = problem or 'shadow_validation_failed'
        elif not problem and (usage['input_tokens'] > 8000 or usage['output_tokens'] > 2500
                              or Decimal(usage['cost_cny_at_peak_rates']) > Decimal('0.036')):
            problem = 'observed_budget_exceeded'
        summary.update(status='stopped' if problem else 'completed', stop_reason=problem)
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(status='stopped', stop_reason='cancelled' if isinstance(exc, KeyboardInterrupt) else 'execution_failed',
                       exception_type=type(exc).__name__)
    finally:
        _save(OUTPUT_DIR/'execution-summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight','run'), nargs='?', default='preflight')
    parser.add_argument('--authorize-fixed-single-shadow', action='store_true')
    parser.add_argument('--accept-estimated-token-risk', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'preflight':
            result = preflight(ROOT/'.env.shadow')
        else:
            summary = run_authorized(ROOT/'.env.shadow', send_authorized=args.authorize_fixed_single_shadow,
                estimated_budget_risk_accepted=args.accept_estimated_token_risk)
            # Private raw diagnostics and judgments never go to the console.
            result = {k:summary[k] for k in ('status','stop_reason','model_calls','max_model_calls','publication_allowed','semantic_verified')}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result['status'] in ('blocked','stopped'))
    except Exception:
        print(json.dumps(dict(status='blocked', error='single_configuration_or_authorization_failed', publication_allowed=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
