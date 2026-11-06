"""Fixed three-case shadow entrypoint. Default preflight is entirely offline.

The `run` action is for use ONLY after the separate real-data/model authorization.
It never invokes the Agent, changes an assessment or publishes an answer.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import json

from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_retrieval import assess_retrieval, create_semantic_service, retrieval_request
from data_analysis_agent.agents.review_shadow_adapter import StructuredEvidenceShadow, ShadowSettings, JudgeFactory
from data_analysis_agent.agents.review_shadow_openai import (
    MODEL, BASE_URL, INPUT_LIMIT, OUTPUT_LIMIT, INPUT_PRICE, OUTPUT_PRICE,
    scoped_config, offline_encoding, ShadowProviderError,
)
from data_analysis_agent.agents import review_shadow_siliconflow as siliconflow
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import is_postgres
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads
from data_analysis_agent.jobs.submission import configured_identity
from data_analysis_agent.reviews.embedding_process import close_embedding_processes

ROOT=Path(__file__).resolve().parents[1]
CASES=(('S1','有没有制冷不足的反馈？','explicit_support'),
       ('S2','有没有制冷效果好的反馈？','unrelated_context_overrefusal_probe'),
       ('S3','是否有客服态度差的反馈？','negation_hearsay_hypothesis_risk_probe'))
BOUND=dict(source='reviews_csv',dataset_version='reviews-csv',product_ids=['100013588055'],
           date_from='2021-09-11',date_to='2022-08-26',timezone='UTC')


def preflight(shadow_env, business_env, *, provider_name='openai',business_authorized=False,
              estimated_budget_risk_accepted=False):
    """Configuration only: no SDK instance, database connection, embedding or API probe."""
    if provider_name=='siliconflow':
        check=siliconflow.preflight(shadow_env,estimated_budget_risk_accepted=estimated_budget_risk_accepted)
        if not business_authorized:check['missing'].append('fixed_business_data_and_send_authorization_required')
        check['spending_authorized']=business_authorized and estimated_budget_risk_accepted
        try:
            settings,identity=configured_identity(business_env)
            db_configured=is_postgres(read_settings(business_env).get('DATABASE_URL',''))
            rag_configured=bool(settings.rag_python and settings.rag_model_cache)
            profile=content_sha256([identity[k] for k in ('model','base_url','prompt_version')])
            if not db_configured:check['missing'].append('postgres_not_configured')
            if not rag_configured:check['missing'].append('local_rag_not_configured')
        except ValueError:
            db_configured=rag_configured=False;profile=None
            check['missing'].append('business_scope_identity_not_configured')
        if not check['missing']:check['status']='ready_for_explicit_fixed_business_run'
        return check|dict(scope=BOUND,label_profile_key=profile,db_configured=db_configured,
            business_data_and_send_authorized=business_authorized,
            rag_configured=rag_configured,cases=[dict(case=cid,question=q,intended_probe=kind,
            phenomenon_observed='not_checked') for cid,q,kind in CASES])
    if provider_name!='openai':raise ValueError('unknown_shadow_provider')
    missing=[];provider=None;identity=None
    try:
        provider=scoped_config(shadow_env)
        offline_encoding(provider.tokenizer_cache)
    except ShadowProviderError as exc:
        missing.append(str(exc))
    tokenizer_ready=False
    try:
        offline_encoding(read_settings(shadow_env).get('SHADOW_TOKENIZER_CACHE',''))
        tokenizer_ready=True
    except ShadowProviderError:
        if 'offline_tokenizer_missing_or_changed' not in missing:missing.append('offline_tokenizer_missing_or_changed')
    try:
        settings,identity=configured_identity(business_env)
        db_configured=is_postgres(read_settings(business_env).get('DATABASE_URL',''))
        rag_configured=bool(settings.rag_python and settings.rag_model_cache)
        if not db_configured:missing.append('postgres_not_configured')
        if not rag_configured:missing.append('local_rag_not_configured')
    except ValueError:
        missing.append('business_scope_identity_not_configured');db_configured=rag_configured=False
    maximum=3*(INPUT_LIMIT*INPUT_PRICE+OUTPUT_LIMIT*OUTPUT_PRICE)/1000000
    return dict(status='configuration_ready_not_authorized' if not missing else 'configuration_incomplete',
        model=MODEL,base_url=BASE_URL,endpoint=BASE_URL+'/chat/completions',
        input_token_budget_per_request=INPUT_LIMIT,output_token_limit_per_request=OUTPUT_LIMIT,
        max_model_calls=3,total_input_budget=3*INPUT_LIMIT,total_output_limit=3*OUTPUT_LIMIT,
        price_usd_per_million=dict(uncached_input=str(INPUT_PRICE),output=str(OUTPUT_PRICE)),
        calculated_budget_usd=str(maximum),proposed_spending_budget_usd='0.06',
        input_budget_note='local tokenizer estimate including schema plus 1024 framing reserve; provider usage must be checked',
        sdk_timeout_seconds=20,outer_deadline_seconds=30,max_retries=0,
        scope=BOUND,label_model=identity['model'] if identity else None,
        label_prompt_version=identity['prompt_version'] if identity else None,
        label_profile_key=content_sha256([identity[k] for k in ('model','base_url','prompt_version')]) if identity else None,
        primary_key_reused=False,shadow_key_configured=provider is not None,
        offline_tokenizer_ready=tokenizer_ready,
        db_configured=db_configured,rag_configured=rag_configured,
        cases=[dict(case=cid,question=q,intended_probe=kind,phenomenon_observed='not_checked') for cid,q,kind in CASES],
        missing=missing,real_database_calls=0,real_embedding_calls=0,real_llm_calls=0,
        account_access_checked=False,publication_allowed=False)


def run_authorized(shadow_env,business_env,output_dir, *, provider_name='openai',business_authorized=False,
                   estimated_budget_risk_accepted=False):
    """Explicit real action, never called by preflight. At most three, stop on failure."""
    check=(preflight(shadow_env,business_env,provider_name=provider_name,business_authorized=business_authorized,
                    estimated_budget_risk_accepted=estimated_budget_risk_accepted) if provider_name!='openai'
           else preflight(shadow_env,business_env))
    if check['missing']:raise ValueError('shadow_preflight_incomplete')
    # No provider switch, dotenv entry or CLI argument can approve compatibility.
    if provider_name=='siliconflow':
        if not business_authorized or not estimated_budget_risk_accepted:
            raise ValueError('fixed_business_and_budget_authorization_required')
        if siliconflow.compatibility_blockers(estimated_budget_risk_accepted=estimated_budget_risk_accepted):
            raise ValueError('siliconflow_compatibility_unverified')
    destination=Path(output_dir).resolve()
    if not destination.is_relative_to(ROOT/'outputs/rag-semantic-retrieval'):
        raise ValueError('private_output_required')
    destination.mkdir(parents=True,exist_ok=False)
    def save(path,value):
        with path.open('x',encoding='utf-8') as out:out.write(canonical_json(value))
    save(destination/'preflight.json',check)
    provider=(siliconflow.scoped_config(shadow_env) if provider_name=='siliconflow' else scoped_config(shadow_env))
    if provider_name=='siliconflow':
        provider=provider.model_copy(update=dict(estimated_budget_risk_accepted=estimated_budget_risk_accepted))
    factory_module='review_shadow_siliconflow' if provider_name=='siliconflow' else 'review_shadow_openai'
    cost_key='cost_cny_at_peak_rates' if provider_name=='siliconflow' else 'cost_usd'
    budget=Decimal('0.108') if provider_name=='siliconflow' else Decimal('0.06')
    settings,identity=configured_identity(business_env)
    scope={**identity,**BOUND}
    db=read_settings(business_env)['DATABASE_URL']
    records=[];known_cost=Decimal('0');unknown_usage_cases=0
    failure=None;phase='initialize_retrieval'
    try:
        service=create_semantic_service(db,scope,settings)
        for cid,question,kind in CASES:
            phase='source_retrieval'
            case_dir=destination/cid;case_dir.mkdir()
            contract=build_query_plan(question,scope).task_contract
            if contract.support_status!='supported':raise ValueError('fixed_query_not_supported')
            result=service.search(retrieval_request(question))  # P0 owns RR/READ ONLY and releases before judge.
            save(case_dir/'contract.json',contract.model_dump(mode='json'))
            save(case_dir/'retrieval.json',result.to_dict())
            save(case_dir/'v2-assessment.json',assess_retrieval(contract.evidence_question,result.to_dict()['evidence']))
            config=provider.model_copy(update={'usage_file':str(case_dir/'provider-usage.json')})
            phase='shadow_prediction'
            adapter=StructuredEvidenceShadow(settings=ShadowSettings(enabled=True,timeout_seconds=30.0),
                judge_factory=JudgeFactory(entrypoint=f'data_analysis_agent.agents.{factory_module}:create_judge',
                                          config_json=canonical_json(config.model_dump(mode='json'))))
            run=adapter.run(contract,result,trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),request_id=cid)
            save(case_dir/'shadow-execution.json',run.debug_record())
            if run.judgments is not None:save(case_dir/'shadow-judgments.json',run.judgments.model_dump(mode='json'))
            usage_path=case_dir/'provider-usage.json'
            usage=strict_json_loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else None
            if usage and (usage.get('request_id'),usage.get('input_sha256'))!=(cid,run.diagnostics['input_sha256']):
                raise ValueError('usage_binding_mismatch')
            if usage and usage.get('usage_known') and cost_key in usage:
                known_cost+=Decimal(usage[cost_key])
            else:unknown_usage_cases+=1
            records.append(dict(case=cid,status=run.outcome.execution_state,semantic_review='pending',
                usage_known=bool(usage and usage.get('usage_known')),publication_allowed=False))
            if provider_name=='siliconflow' and run.outcome.execution_state=='validated_shadow':
                # A structurally valid result cannot conceal a missing/failed usage receipt.
                if not usage or not usage.get('usage_known') or usage.get('state')!='response_received':
                    raise ValueError('provider_usage_missing_or_failed')
                if (usage.get('reasoning_tokens')!=0 or usage.get('reasoning_content_present') is not False
                    or usage.get('finish_reason')!='stop' or usage.get('response_model') not in (
                        siliconflow.MODEL,'deepseek-ai/'+siliconflow.DOCUMENTED_VERSION)):
                    raise ValueError('provider_observation_conflict')
            if known_cost>budget:raise ValueError('spending_budget_exceeded')
            if run.outcome.execution_state!='validated_shadow':break
    except Exception as exc:
        failure=dict(phase=phase,code=getattr(exc,'code',type(exc).__name__))
        raise
    finally:
        close_embedding_processes()
        save(destination/'execution-summary.json',dict(completed_cases=records,planned_cases=3,
            failure=failure,provider=provider_name,
            **{('known_cost_cny_at_peak_rates' if provider_name=='siliconflow' else 'known_cost_usd'):str(known_cost)},
            unknown_usage_cases=unknown_usage_cases,
            total_cost_known=bool(records) and not unknown_usage_cases and provider_name=='openai',
            cost_is_peak_rate_estimate=provider_name=='siliconflow',
            actual_semantic_quality='not_automatically_scored',publication_allowed=False))
    return dict(output_dir=str(destination),cases=records,publication_allowed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','run'),nargs='?',default='preflight')
    parser.add_argument('--provider',choices=('openai','siliconflow'),default='siliconflow')
    parser.add_argument('--authorize-fixed-business-shadow',action='store_true')
    parser.add_argument('--accept-estimated-token-risk',action='store_true')
    parser.add_argument('--shadow-env',default=str(ROOT/'.env.shadow'))
    parser.add_argument('--business-env',default=str(ROOT/'.env'))
    parser.add_argument('--output-dir',default=str(ROOT/'outputs/rag-semantic-retrieval'/
        ('first-llm-shadow-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))))
    args=parser.parse_args()
    try:
        approvals=dict(business_authorized=args.authorize_fixed_business_shadow,
                       estimated_budget_risk_accepted=args.accept_estimated_token_risk)
        result=(preflight(args.shadow_env,args.business_env,provider_name=args.provider,**approvals) if args.action=='preflight'
                else run_authorized(args.shadow_env,args.business_env,args.output_dir,provider_name=args.provider,**approvals))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception:
        print(json.dumps(dict(status='stopped',error='configuration_or_execution_failed',publication_allowed=False)))
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
