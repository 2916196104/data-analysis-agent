"""Fixed N1/N2/N3 preparation; default action is local, read-only preflight.

The run action requires separate business-data/send and estimated-fee authorization.
It never calls the original experiment runner or the production Agent.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from scripts.run_rag_shadow_first import preflight as configuration_preflight
from data_analysis_agent.agents import review_shadow_siliconflow as provider
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_retrieval import assess_retrieval, create_semantic_service, retrieval_request
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.jobs.submission import configured_identity
from data_analysis_agent.reviews.embedding_process import close_embedding_processes
from data_analysis_agent.reviews.embedding_store import SPACE_ID

OUTPUT_DIR = ROOT/'outputs/rag-semantic-retrieval/semantic-boundary-shadow-v2'
EXPERIMENT = 'siliconflow-semantic-boundaries-v2'
CASES = (('N1', '是否有安装师傅态度很差的评论？', 'installer_bad'),
         ('N2', '有无客服态度很好的反馈？', 'service_good'),
         ('N3', '有没有噪音不大的反馈？', 'noise_low'))
BOUND = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588055'],
             date_from='2021-09-11', date_to='2022-08-26', timezone='UTC')
EXPECTED_PROFILE = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
EXPECTED_PROMPT = '6d0e268c77b3dfc77956ec31b154ff5f04a84db34d15271f40c1ee93c85c130d'
EXPECTED_SPACE = 'embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
PEAK_ESTIMATE = Decimal('0.108')  # Conditional post-response stop budget, NOT a billing cap.


def _scope(business_env):
    settings, identity = configured_identity(business_env)
    if content_sha256([identity[k] for k in ('model', 'base_url', 'prompt_version')]) != EXPECTED_PROFILE:
        raise ValueError('label_profile_changed')
    return settings, {**identity, **BOUND}


def preflight(shadow_env, business_env, *, business_authorized=False, estimated_budget_risk_accepted=False):
    check = configuration_preflight(shadow_env, business_env, provider_name='siliconflow',
        business_authorized=business_authorized, estimated_budget_risk_accepted=estimated_budget_risk_accepted)
    missing = check['missing']
    prompt_hash = sha256(provider.SYSTEM_PROMPT.encode('utf-8')).hexdigest()
    if (provider.MODEL, provider.BASE_URL, provider.PROMPT_VERSION, prompt_hash, SPACE_ID,
            provider.INPUT_LIMIT, provider.OUTPUT_LIMIT) != (
            'deepseek-ai/DeepSeek-V4.1-Flash', 'https://api.siliconflow.cn/v1',
            'p5-shadow-siliconflow-instructions-v2', EXPECTED_PROMPT, EXPECTED_SPACE, 8000, 2500):
        missing.append('fixed_protocol_changed')
    cases = []
    try:
        _, scope = _scope(business_env)
        for cid, question, fid in CASES:
            contract = build_query_plan(question, scope).task_contract
            query = retrieval_request(question)
            if (contract.support_status != 'supported' or contract.uncertainty
                    or tuple(f.id for f in contract.required_facts.items) != (fid,) or query.top_k != 5):
                raise ValueError('fixed_target_changed')
            cases.append(dict(case=cid, question=question, required_fact_ids=[fid],
                contract_sha256=content_sha256(contract.model_dump(mode='json')),
                query=query.model_dump(mode='json'), semantic_review='not_started',
                risk_coverage='not_observed'))
    except ValueError:
        missing.append('fixed_scope_or_target_not_verified')
    if OUTPUT_DIR.exists():
        missing.append('experiment_already_claimed')
    return check | dict(experiment=EXPERIMENT, cases=cases, scope=BOUND,
        output_dir=str(OUTPUT_DIR), missing=list(dict.fromkeys(missing)),
        status='blocked' if missing else 'ready_for_explicit_boundary_run',
        prompt_version=provider.PROMPT_VERSION, system_prompt_sha256=prompt_hash,
        entrypoint_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), embedding_space_id=SPACE_ID,
        context_policy='quote_full_review_veto', context_version='1',
        source_policy='current_effective_readonly_v1', semantic_verified=False,
        sdk_phase_timeouts=dict(read=20, connect=5, write=10, pool=5),
        old_experiment_reused=False, runtime_sources_checked=False, real_calls_executed=False)


def _save(path, value):
    with path.open('x', encoding='utf-8') as out:
        out.write(canonical_json(value))


def _usage_problem(usage, cid, digest):
    if not usage or (usage.get('request_id'), usage.get('input_sha256')) != (cid, digest):
        return 'provider_usage_missing_or_binding_invalid'
    if (usage.get('usage_known') is not True or usage.get('state') != 'response_received'
            or usage.get('model_calls') != 1 or usage.get('reasoning_tokens') != 0
            or usage.get('reasoning_content_present') is not False or usage.get('finish_reason') != 'stop'
            or usage.get('response_model') not in (provider.MODEL, 'deepseek-ai/'+provider.DOCUMENTED_VERSION)
            or usage.get('prompt_version') != provider.PROMPT_VERSION
            or usage.get('system_prompt_sha256') != EXPECTED_PROMPT):
        return 'provider_observation_conflict'
    try:
        cost = Decimal(usage['cost_cny_at_peak_rates'])
        if not cost.is_finite() or cost < 0:
            raise ValueError('invalid_cost')
    except (KeyError, ValueError, ArithmeticError):
        return 'provider_cost_unknown_or_invalid'
    return None


def run_authorized(shadow_env, business_env, *, business_authorized=False, estimated_budget_risk_accepted=False):
    check = preflight(shadow_env, business_env, business_authorized=business_authorized,
                      estimated_budget_risk_accepted=estimated_budget_risk_accepted)
    if check['missing'] or not business_authorized or not estimated_budget_risk_accepted:
        raise ValueError('boundary_preflight_incomplete')
    config = provider.scoped_config(shadow_env).model_copy(update={'estimated_budget_risk_accepted': True})
    settings, scope = _scope(business_env)
    db = read_settings(business_env)['DATABASE_URL']
    # mkdir is the once-only claim, before model/database work; failed attempts stay claimed.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    _save(OUTPUT_DIR/'preflight.json', check)
    records = [dict(case=cid, question=q, status='not_started', semantic_review='pending',
                    risk_coverage='not_reviewed', publication_allowed=False) for cid, q, _ in CASES]
    total = Decimal('0'); stop = None; phase = 'initialize_retrieval'; active = None
    try:
        service = create_semantic_service(db, scope, settings)
        for record, (cid, question, _) in zip(records, CASES):
            active = record; phase = 'source_retrieval'
            case_dir = OUTPUT_DIR/cid; case_dir.mkdir()
            contract = build_query_plan(question, scope).task_contract
            result = service.search(retrieval_request(question))
            _save(case_dir/'contract.json', contract.model_dump(mode='json'))
            _save(case_dir/'retrieval.json', result.to_dict())
            _save(case_dir/'v2-assessment.json', assess_retrieval(contract.evidence_question, result.to_dict()['evidence']))
            phase = 'shadow_prediction'
            record['usage_status'] = 'unknown_pending'
            case_config = config.model_copy(update={'usage_file': str(case_dir/'provider-usage.json')})
            adapter = StructuredEvidenceShadow(settings=ShadowSettings(enabled=True, timeout_seconds=30.0),
                judge_factory=JudgeFactory(entrypoint='data_analysis_agent.agents.review_shadow_siliconflow:create_judge',
                                          config_json=canonical_json(case_config.model_dump(mode='json'))))
            run = adapter.run(contract, result, trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
                              request_id=cid)
            _save(case_dir/'shadow-execution.json', run.debug_record())
            if run.judgments is not None:
                _save(case_dir/'shadow-judgments.json', run.judgments.model_dump(mode='json'))
            record.update(status=run.outcome.execution_state, candidates=len(result.evidence),
                          total_ms=run.diagnostics['total_ms'])
            if (run.outcome.publication_allowed or run.outcome.semantic_verified
                    or run.outcome.permitted_target_fact_ids):
                stop = 'shadow_isolation_violation'; break
            if not run.diagnostics['reaped'] or not run.diagnostics['threads_released']:
                stop = 'worker_cleanup_failed'; break
            if run.outcome.execution_state == 'skipped_empty':
                record.update(usage_status='not_applicable_no_call', model_calls=0)
                continue
            usage_path = case_dir/'provider-usage.json'
            usage = strict_json_loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else None
            if usage and usage.get('usage_known') and 'cost_cny_at_peak_rates' in usage:
                # Only count costs after the receipt's identity and observations pass.
                problem = _usage_problem(usage, cid, run.diagnostics['input_sha256'])
            else:
                problem = 'provider_usage_or_cost_unknown'
            record.update(usage_status='verified' if not problem else problem,
                          model_calls=usage.get('model_calls') if usage else None)
            if problem:
                stop = problem; break
            total += Decimal(usage['cost_cny_at_peak_rates'])
            if run.outcome.execution_state != 'validated_shadow':
                stop = 'shadow_execution_failed'; break
            if total > PEAK_ESTIMATE:
                stop = 'conditional_spending_estimate_exceeded'; break
    except (Exception, KeyboardInterrupt) as exc:
        stop = 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'execution_failed'
        if active is not None:
            active.update(status='execution_failed', failure_stage=phase, exception_type=type(exc).__name__)
    finally:
        try:
            close_embedding_processes()
        except Exception:
            stop = 'embedding_cleanup_failed'
        finally:
            summary = dict(experiment=EXPERIMENT, completed_at_utc=datetime.now(timezone.utc).isoformat(),
                status='stopped' if stop else 'completed', stop_reason=stop, cases=records,
                known_cost_cny_at_peak_rates=str(total),
                unverified_usage_or_cost_cases=sum(r.get('usage_status') not in
                    (None, 'verified', 'not_applicable_no_call') for r in records),
                actual_bill_known=False, cost_is_peak_rate_estimate=True, max_model_calls=3,
                semantic_verified=False, publication_allowed=False, semantic_review='pending',
                risk_coverage='requires_candidate_reading_no_automatic_gold')
            _save(OUTPUT_DIR/'execution-summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'run'), nargs='?', default='preflight')
    parser.add_argument('--authorize-fixed-boundary-shadow', action='store_true')
    parser.add_argument('--accept-estimated-token-risk', action='store_true')
    args = parser.parse_args()
    try:
        result = (preflight if args.action == 'preflight' else run_authorized)(ROOT/'.env.shadow', ROOT/'.env',
            business_authorized=args.authorize_fixed_boundary_shadow,
            estimated_budget_risk_accepted=args.accept_estimated_token_risk)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['status'] == 'stopped' else 0
    except Exception:
        print(json.dumps(dict(status='stopped', error='boundary_configuration_or_execution_failed', publication_allowed=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
