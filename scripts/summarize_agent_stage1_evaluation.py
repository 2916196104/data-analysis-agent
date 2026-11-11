"""Compare the frozen independent oracle and preserved execution, never rewrite gold."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from agent_stage1_multiturn_evaluation import verify_freeze, use_source_revision

# These are review exclusions, not replacement expected answers. Original gold is retained.
REVIEW={
    'G10-T2':'无可信身份历史时，原预期仍指定 negative 和单商品，缺少独立语义依据。',
    'G11-T2':'新会话无商品/情感线索，原预期单商品及 negative 需人工裁决。',
    'G12-T1':'首轮具体表现未指定情感，原预期 negative 需人工裁决。',
    'G12-T2':'上一轮未形成有效范围，原预期单商品及 negative 需人工裁决。',
    'G17-T2':'原预期父级业务状态误写 failed；P6 契约要求 partial。保留原预期，状态字段待裁决。',
    'G18-T2':'原预期父级业务状态误写 failed；P6 契约要求 partial。保留原预期，状态字段待裁决。',
    'G19-T2':'原预期父级业务状态误写 failed；P6 契约要求 partial。保留原预期，状态字段待裁决。',
    'G20-T2':'原预期 not_completed 不属于已冻结业务验收状态；过期排名的 complete=false、unsupported 阻断可独立确认，状态枚举待裁决。',
    'G24-T2':'原预期将“客服呢？”澄清轮继续定义为冻结 RAG 请求且 RequestGoal=None；该新请求的层次本就不唯一，需人工裁决。',
    'G30-T3':'冻结前已声明短情感追问的业务层次待人工裁决。',
}


def subset(actual, expected):
    return isinstance(actual,dict) and all(k in actual and actual[k]==v for k,v in expected.items())


def assess(t):
    e=t['oracle']; r=t['task'].get('result') or {}; state=r.get('analysis_state') or {}
    post=(t.get('post_context') or {}).get('analysis_state') or {}
    primary=next((v['plan'] for v in t['events'] if v['node']=='router_planner'),r.get('query_plan') or {})
    req=primary.get('requirements',[])
    aspects=list(dict.fromkeys(row.get('parameters',{}).get('aspect') for row in req if row.get('parameters',{}).get('aspect')))
    tools=list(dict.fromkeys(row['tool'] for row in req))
    blocked=not req
    acceptance=r.get('task_acceptance')
    if e['kind']=='rag':
        rag=[v['data'] for v in r.get('results',{}).values() if v['kind']=='semantic_evidence']
        permits=[key for v in rag for key in v.get('assessment',{}).get('permitted_keys',[])]
        # The actual retrieval DTO holds the assessment inside support; retain it verbatim below.
        support=[v.get('assessment') or {} for v in rag]
        actual_acceptance={'contract':'frozen_RAG_separate','permitted_evidence':bool(permits), 'support':support}
    else: actual_acceptance=acceptance
    actual=dict(analysis_state={k:'present' if state.get(k) else 'absent' for k in ('ranking_basis','pending_goal')},
        pending_goal='awaiting_aspect' if post.get('pending_goal') else 'absent',request_goal=r.get('request_goal'),
        user_intent=r.get('user_intent',{}),analysis_intent='not_finalized' if blocked else primary.get('analysis_type'),
        query_plan=dict(blocked=blocked,analysis_type=None if blocked else primary.get('analysis_type'),aspects=aspects,sentiment=primary.get('sentiment')),
        required_tools=tools,task_acceptance=actual_acceptance,final_status=dict(agent=r.get('status'),worker=t['task']['status']))
    checks={}
    for name in ('analysis_state','pending_goal','request_goal','user_intent','analysis_intent','required_tools','final_status'):
        expected=e['expected_'+name]; value=actual[name]
        checks[name]=subset(value,expected) if isinstance(expected,dict) else value==expected
    checks['query_plan']=blocked if e['expected_query_plan']['blocked'] else subset(actual['query_plan'],e['expected_query_plan'])
    if e['clarification_must_execute_zero']:
        checks['task_acceptance']=(not acceptance or not acceptance.get('complete'))
    elif e['kind']=='rag': checks['task_acceptance']=subset(actual_acceptance,e['expected_task_acceptance'])
    else: checks['task_acceptance']=subset(acceptance,e['expected_task_acceptance'])
    checks['scope']=subset(r.get('scope'),e['expected_scope'])
    query_count=max(r.get('query_executions',0),r.get('p6_preparation',{}).get('query_executions',0))
    checks['clarification_zero_queries']=not e['clarification_must_execute_zero'] or query_count==0
    calls=[v for v in t['events'] if v['node']=='tool_start']
    checks['plan_authorized_tools']=all(v['plan_authorized'] for v in calls)
    # Match the business capability AND independently frozen scope, not just numeric validity.
    kinds={'get_top_issues':'issues','get_positive_aspects':'positive_aspects','get_aspect_drilldown':'aspect_drilldown',
           'get_evidence':'evidence','search_semantic_evidence':'semantic_evidence'}
    outputs=[v for v in r.get('results',{}).values() if v.get('kind')!='context']
    required_kinds=[kinds[name] for name in e['expected_required_tools']]
    output_scope_ok=all(subset(v.get('data',{}).get('scope'),e['expected_scope']) for v in outputs)
    output_capability_ok=all(any(v.get('kind')==kind for v in outputs) for kind in required_kinds)
    publication=r.get('p6_publication')
    if publication is not None:
        output_scope_ok=subset(publication.get('scope'),e['expected_scope'])
        output_capability_ok=(required_kinds==['aspect_drilldown'] and
            publication.get('required_aspects')==e['expected_request_goal'].get('required_aspects') and
            [d['aspect'] for d in publication['dimensions']]==publication['required_aspects'])
    checks['tool_target']=checks['scope'] and output_scope_ok and output_capability_ok
    mismatch=[name for name in ('analysis_state','pending_goal','scope','request_goal','user_intent','analysis_intent',
        'query_plan','required_tools','tool_target','task_acceptance','final_status','clarification_zero_queries','plan_authorized_tools') if not checks[name]]
    review=REVIEW.get(t['id'])
    fault=e.get('fault')
    # Fault-injection success is measured by rejection, never by forcing the corrupted planner to match gold.
    rejected=(r.get('status')=='error' and t['task']['status']=='failed' and not (acceptance or {}).get('complete') and not r.get('answer'))
    status='PENDING_HUMAN' if review else ('PASS' if rejected else 'FAIL') if fault else 'FAIL' if mismatch else 'PASS'
    return dict(id=t['id'],status=status,review_reason=review,fault=fault,checks=checks,mismatches=mismatch,
        first_deviation=mismatch[0] if mismatch else None,actual=actual,expected={k:v for k,v in e.items() if k.startswith('expected_')},
        query_executions=query_count,tool_calls=[{'name':v['name'],'parameters':v['parameters'],'authorized':v['plan_authorized']} for v in calls],
        validator_rejected=rejected if fault else None,
        goal_plan_failed_but_tools_executed=bool((r.get('task_plan_check') or {}).get('passed') is False and calls),
        raw_final_analysis_intent=r.get('analysis_intent'),raw_final_query_plan=r.get('query_plan'),
        acceptance=acceptance,scope=r.get('scope'),simulated_model_attempts=len(t['simulated_model_attempts']))


def ratio(rows, predicate):
    n=sum(bool(predicate(row)) for row in rows); d=len(rows)
    return {'passed':n,'total':d,'percent':round(100*n/d,2) if d else None}


def summarize(source):
    source=source.resolve()
    raw=json.loads(source.read_text(encoding='utf-8'))
    if raw.get('source_revision'):
        revision=raw['source_revision']
        assert sha256((ROOT/revision['path']).read_bytes()).hexdigest()==revision['sha256']
        use_source_revision(ROOT/revision['path'])
    assessed=[assess(t) for t in raw['turns']]
    natural=[a for a in assessed if not a['review_reason'] and not a['fault']]
    active=[a for a in natural if not a['expected']['expected_query_plan']['blocked']]
    faults=[a for a in assessed if a['fault']]
    metrics={
        'state_context_inheritance':ratio(natural,lambda a:all(a['checks'][k] for k in ('analysis_state','pending_goal','scope'))),
        'intent_router':ratio(natural,lambda a:all(a['checks'][k] for k in ('user_intent','analysis_intent','scope'))),
        'request_goal':ratio(natural,lambda a:a['checks']['request_goal']),
        'planner':ratio(natural,lambda a:all(a['checks'][k] for k in ('query_plan','required_tools','scope'))),
        'tool_target_match':ratio(active,lambda a:a['checks']['tool_target']),
        'validator_injected_error_interception':ratio(faults,lambda a:a['validator_rejected']),
        'turn_end_to_end_contract':ratio([a for a in assessed if not a['review_reason']],lambda a:a['status']=='PASS'),
    }
    eligible=[t for t in raw['turns'] if t['oracle']['strict_business_completion'] and not t['oracle'].get('fault') and t['id'] not in REVIEW
              and t['oracle']['expected_final_status']['agent']=='ok']
    byid={a['id']:a for a in assessed}
    metrics['turn_strict_business_completion']=ratio([byid[t['id']] for t in eligible],lambda a:a['status']=='PASS' and
        (bool((a['acceptance'] or {}).get('complete')) or bool((a['actual']['task_acceptance'] or {}).get('permitted_evidence'))))
    groups={}
    for a in assessed: groups.setdefault(a['id'].split('-')[0],[]).append(a)
    group_states={k:'PENDING_HUMAN' if any(a['status']=='PENDING_HUMAN' for a in vs) else 'FAIL' if any(a['status']=='FAIL' for a in vs) else 'PASS' for k,vs in groups.items()}
    scored=[v for v in group_states.values() if v!='PENDING_HUMAN']
    metrics['conversation_end_to_end_contract']={'passed':scored.count('PASS'),'total':len(scored),'percent':round(100*scored.count('PASS')/len(scored),2)}
    return dict(execution_evidence=source.relative_to(ROOT).as_posix(),execution_sha256=sha256(source.read_bytes()).hexdigest(),
        oracle_manifest=verify_freeze(),source_revision=raw.get('source_revision'),metrics=metrics,group_states=group_states,
        adjudication_exclusions=REVIEW,turns=assessed,real_database_connections=0,real_database_writes=0,real_paid_calls=0,
        real_embedding_calls=0,blocked_dependency_constructions=raw['forbidden_attempts'])


if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: python scripts/summarize_agent_stage1_evaluation.py <preserved-run.json>')
    source=Path(sys.argv[1])
    result=summarize(source); out=source.parent/('assessment-'+source.stem+'.json')
    with out.open('x',encoding='utf-8') as file:file.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result['metrics'],ensure_ascii=False,indent=2))
    for row in result['turns']:
        if row['status']!='PASS': print(row['id'],row['status'],row['first_deviation'],row['mismatches'])
