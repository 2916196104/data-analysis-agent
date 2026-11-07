"""One fixed Text Evidence feasibility experiment. Default: local preflight, no request."""
import argparse
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT,ROOT/'src'):
    if str(directory) not in sys.path: sys.path.insert(0,str(directory))

from experiments import text_evidence_shadow as port
from experiments import text_evidence_protocol as text
from experiments.text_evidence_synthetic import prepare_cases
from data_analysis_agent.agents import review_structured_evidence as b1
from data_analysis_agent.agents import review_shadow_diagnostics as privacy
from data_analysis_agent.agents.review_shadow_adapter import JudgeFactory, ShadowSettings
from data_analysis_agent.core.serialization import canonical_json, content_sha256, strict_json_loads

FIXTURE = ROOT/'tests/fixtures/shadow_text_evidence_cases_v1.json'
FIXTURE_SHA256 = '2210526b8fc75ed25a5410deca043bc3e6251bd6fdedb6154bfcb544b08b536b'
PROMPT_SHA256 = 'a658b5667e7596461d46795300a151159233a6a54cbbe1659125434a4791af95'
SCHEMA_SHA256 = 'fd779cd32978b76369a4dccdb310c9008e99667d49155834a69ec79b7a6685b1'
STRICT_SCHEMA_SHA256 = '332ff61606e85686a562a8281bd10b8794700d894c7cc10d992ad15edd469c8d'
INPUT_SHA256 = '881126f4713ce312da2478832144f1ce228ffdac8659a86741c6efe0d217a85c'
BODY_SHA256 = '06c4ec01c17a7a604698f4e4c23b952985c838da7bffac2a8991911af324da8b'
INPUT_ESTIMATE = 7453
OUTPUT_DIR = ROOT/'outputs/rag-semantic-retrieval/text-evidence-synthetic-v1'


def load_fixed():
    raw = FIXTURE.read_bytes()
    if sha256(raw).hexdigest()!=FIXTURE_SHA256: raise ValueError('fixed_cases_changed')
    data = strict_json_loads(raw.decode('utf-8'))
    if [c['id'] for c in data['cases']]!=['T601','T602','T603','T604','T605']:
        raise ValueError('fixed_cases_changed')
    return data


def save(path,value):
    with path.open('x',encoding='utf-8') as out: out.write(canonical_json(value))


def preflight(env_file):
    data = load_fixed()
    if port.PROMPT_VERSION!='p5-text-evidence-instructions-v1': raise ValueError('fixed_prompt_version_changed')
    if (port.provider.MODEL,port.provider.BASE_URL,port.provider.INPUT_LIMIT,port.provider.OUTPUT_LIMIT,
        port.provider.INPUT_PRICE,port.provider.OUTPUT_PRICE)!=('deepseek-ai/DeepSeek-V4.1-Flash',
        'https://api.siliconflow.cn/v1',8000,2500,Decimal('2'),Decimal('8')):
        raise ValueError('fixed_provider_changed')
    if (sha256(port.SYSTEM_PROMPT.encode()).hexdigest(),content_sha256(text.output_schema()),
        content_sha256(port.strict_output_schema()))!=(PROMPT_SHA256,SCHEMA_SHA256,STRICT_SCHEMA_SHA256):
        raise ValueError('fixed_text_protocol_changed')
    saved_schema = strict_json_loads((ROOT/'docs/schemas/p5-text-evidence-output-experiment-v1.schema.json').read_text(encoding='utf-8'))
    if saved_schema!=text.output_schema(): raise ValueError('saved_text_schema_changed')
    for name,schema in b1.schemas().items():
        if schema!=strict_json_loads((ROOT/'docs/schemas'/f'p5b1-{name.replace("_","-")}.schema.json').read_text(encoding='utf-8')):
            raise ValueError('active_b1_schema_changed')
    request = prepare_cases(data['cases'])[2]
    payload = b1.model_payload(request)
    body = port.request_body(payload)
    estimate = port.provider.estimated_input_tokens(body)
    if (content_sha256(request.model_dump(mode='json')),content_sha256(body),estimate)!=(INPUT_SHA256,BODY_SHA256,INPUT_ESTIMATE) or estimate>8000:
        raise ValueError('fixed_request_or_budget_changed')
    for field in ('expected_status','expected_reason','expected_reference_type','reference_standard','reference_policy','provenance','veto_text','target_fact'):
        if field in payload: raise ValueError('expectation_leak')
    missing = []
    try: port.provider.scoped_config(env_file)  # Configuration only, no SDK or old B1 compatibility probe.
    except Exception: missing.append('shadow_configuration_invalid')
    ignored = subprocess.run(['git','check-ignore','-q','--',str(OUTPUT_DIR/'diagnostics/failure.json')],
        cwd=ROOT,capture_output=True,timeout=5).returncode==0
    if not ignored: missing.append('private_path_not_git_ignored')
    if OUTPUT_DIR.exists(): missing.append('experiment_already_claimed')
    else: privacy.destination(str(OUTPUT_DIR))
    return dict(status='blocked' if missing else 'ready_pending_explicit_text_evidence_authorization',missing=missing,
        output_version=text.OUTPUT_VERSION,prompt_version=port.PROMPT_VERSION,system_prompt_sha256=PROMPT_SHA256,
        schema_sha256=SCHEMA_SHA256,strict_schema_sha256=STRICT_SCHEMA_SHA256,fixture_sha256=FIXTURE_SHA256,
        input_sha256=INPUT_SHA256,request_body_sha256=BODY_SHA256,input_estimate=estimate,
        input_estimation_method=port.provider.INPUT_ESTIMATE_METHOD,model=port.provider.MODEL,
        cases=[c['id'] for c in data['cases']],candidate_count=5,input_limit=8000,output_limit=2500,
        max_model_calls=1,max_retries=0,enable_thinking=False,outer_deadline_seconds=30,
        sdk_phase_timeouts=dict(read=20,connect=5,write=10,pool=5),
        provider_new_schema_acceptance='unverified',old_b1_compatibility_reused=False,
        estimated_input_peak_cost_cny=str((Decimal(estimate)*2+Decimal(2500)*8)/1000000),
        conditional_peak_cost_cny='0.036',guaranteed_bill_cap=False,spending_authorized=False,
        output_dir=str(OUTPUT_DIR),development_diagnostic_dir=str(OUTPUT_DIR/'diagnostics'),diagnostic_path_git_ignored=ignored,
        expected_answers_sent=False,real_llm_calls=0,real_database_calls=0,real_embedding_calls=0,
        publication_allowed=False,semantic_verified=False,permitted_target_fact_ids=[])


def usage_problem(usage,request):
    if not usage or (usage.get('request_id'),usage.get('input_sha256'))!=(request.request_id,INPUT_SHA256):
        return 'provider_usage_binding_invalid'
    if any(type(usage.get(k)) is not int for k in ('model_calls','input_tokens','output_tokens')):
        return 'provider_usage_invalid'
    if any(usage.get(k)!=v for k,v in dict(provider='siliconflow',base_url=port.provider.BASE_URL,
        model=port.provider.MODEL,prompt_version=port.PROMPT_VERSION,system_prompt_sha256=PROMPT_SHA256,
        output_schema_sha256=STRICT_SCHEMA_SHA256,state='response_received',model_calls=1,usage_known=True,
        reasoning_tokens=0,reasoning_content_present=False,finish_reason='stop').items()):
        return 'provider_observation_conflict'
    if usage.get('response_model') not in (port.provider.MODEL,'deepseek-ai/'+port.provider.DOCUMENTED_VERSION):
        return 'response_model_conflict'
    try:
        cost = Decimal(usage['cost_cny_at_peak_rates'])
        if not cost.is_finite() or cost<0 or cost>Decimal('0.036'): return 'observed_budget_exceeded'
        if not (0<=usage['input_tokens']<=8000 and 0<=usage['output_tokens']<=2500): return 'observed_budget_exceeded'
    except (ValueError,TypeError,KeyError,ArithmeticError): return 'provider_usage_invalid'
    return None


def run_authorized(env_file, *, send_authorized=False, estimated_budget_risk_accepted=False):
    if not send_authorized or not estimated_budget_risk_accepted: raise ValueError('explicit_text_send_and_fee_authorization_required')
    check = preflight(env_file)
    if check['missing']: raise ValueError('text_preflight_incomplete')
    data = load_fixed()
    contract,result,request = prepare_cases(data['cases'])
    config = port.provider.scoped_config(env_file).model_copy(update={
        'estimated_budget_risk_accepted':True,'usage_file':str(OUTPUT_DIR/'provider-usage.json')})
    privacy._private_mkdir(OUTPUT_DIR)  # Permanent once-only claim before any request, even failures/cancellation.
    summary = dict(status='stopped',stop_reason=None,model_calls=None,structure_valid=False,
        validated_label_count=0,label_match_count=None,formal_accuracy=None,comparison=[],
        prompt_version=port.PROMPT_VERSION,explicit_send_authorized=True,estimated_budget_risk_accepted=True,
        failure_stage=None,failure_diagnostic_saved=False,actual_bill_known=False,
        semantic_basis_review='pending_independent_reading',publication_allowed=False,semantic_verified=False,
        permitted_target_fact_ids=[])
    try:
        for name,value in (('preflight',check),('frozen-expectations',data),('model-input',strict_json_loads(b1.model_payload(request))),
                           ('contract',contract.model_dump(mode='json')),('retrieval',result.to_dict())):
            save(OUTPUT_DIR/f'{name}.json',value)
        adapter = port.TextEvidenceShadow(settings=ShadowSettings(enabled=True,timeout_seconds=30),
            judge_factory=JudgeFactory(entrypoint=port.FACTORY,config_json=canonical_json(config.model_dump(mode='json'))),
            development_diagnostic_dir=str(OUTPUT_DIR/'diagnostics'))
        run = adapter.run(contract,result,trusted_contract_sha256=content_sha256(contract.model_dump(mode='json')),
                          request_id=request.request_id)
        save(OUTPUT_DIR/'shadow-execution.json',run.debug_record())
        usage_path = OUTPUT_DIR/'provider-usage.json'
        usage = strict_json_loads(usage_path.read_text(encoding='utf-8')) if usage_path.exists() else None
        problem = usage_problem(usage,request)
        if not run.diagnostics['reaped'] or not run.diagnostics['threads_released']: problem = 'worker_cleanup_failed'
        valid = run.outcome.execution_state=='validated_shadow' and not problem
        summary.update(model_calls=usage.get('model_calls') if usage else None,usage=usage,
            structure_valid=valid,execution_state=run.outcome.execution_state,reason_codes=list(run.outcome.reason_codes),
            failure_stage=run.diagnostics['failure_stage'],failure_diagnostic_saved=run.diagnostics['failure_diagnostic_saved'],
            total_ms=run.diagnostics['total_ms'])
        if problem and run.raw_output is not None and not summary['failure_diagnostic_saved']:
            summary['failure_stage'] = 'provider_usage'
            try:
                capture = port.TextFailureCapture(str(OUTPUT_DIR/'diagnostics'),request.model_dump(mode='json'),65536)
                values,_ = port.provider._settings(config.env_file,config.env_sha256)
                capture.capture(run.raw_output,secrets=(values['SHADOW_API_KEY'].strip(),))
                capture.finish(stage='provider_usage',error_codes=(problem,))
                summary['failure_diagnostic_saved'] = True
            except Exception: summary['failure_diagnostic_saved'] = False
        if valid:
            by_key = {row.key:row for row in run.judgments.fact_judgments}
            summary['comparison'] = [dict(case=case['id'],expected_status=case['expected_status'],
                model_status=by_key[c.key].status,status_match=by_key[c.key].status==case['expected_status'],
                semantic_basis_review='pending_independent_reading') for case,c in zip(data['cases'],request.candidates)]
            summary.update(validated_label_count=5,label_match_count=sum(row['status_match'] for row in summary['comparison']),
                           known_peak_cost_cny=usage['cost_cny_at_peak_rates'])
            save(OUTPUT_DIR/'shadow-judgments.json',run.judgments.model_dump(mode='json'))
            if summary['label_match_count']!=5:
                problem = 'fixed_label_mismatch'
                capture = port.TextFailureCapture(str(OUTPUT_DIR/'diagnostics'),request.model_dump(mode='json'),65536)
                values,_ = port.provider._settings(config.env_file,config.env_sha256)
                capture.capture(run.raw_output,secrets=(values['SHADOW_API_KEY'].strip(),))
                try:
                    capture.finish(stage='semantic_labels',error_codes=(problem,),details=[dict(error_code=problem,
                        candidate_key=c.key,reference_type=None) for case,c in zip(data['cases'],request.candidates)
                        if by_key[c.key].status!=case['expected_status']])
                    summary['failure_diagnostic_saved'] = True
                except Exception: summary['failure_diagnostic_saved'] = False
                summary['failure_stage'] = 'semantic_labels'
        summary.update(status='completed' if valid and not problem else 'stopped',
                       stop_reason=problem or (None if valid else 'text_validation_failed'))
    except (Exception,KeyboardInterrupt) as exc:
        summary.update(status='stopped',stop_reason='cancelled' if isinstance(exc,KeyboardInterrupt) else 'execution_failed',
                       exception_type=type(exc).__name__)
    finally: save(OUTPUT_DIR/'execution-summary.json',summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('preflight','run'),nargs='?',default='preflight')
    parser.add_argument('--authorize-fixed-text-evidence-shadow',action='store_true')
    parser.add_argument('--accept-estimated-token-risk',action='store_true')
    args = parser.parse_args()
    try:
        if args.action=='preflight': result = preflight(ROOT/'.env.shadow')
        else:
            summary = run_authorized(ROOT/'.env.shadow',send_authorized=args.authorize_fixed_text_evidence_shadow,
                                    estimated_budget_risk_accepted=args.accept_estimated_token_risk)
            result = {k:summary[k] for k in ('status','stop_reason','model_calls','failure_stage','publication_allowed','semantic_verified')}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(result['status'] in ('stopped','blocked'))
    except Exception:
        print(json.dumps(dict(status='blocked',error='text_configuration_or_authorization_failed',
                              publication_allowed=False,semantic_verified=False)))
        return 1


if __name__=='__main__': raise SystemExit(main())
