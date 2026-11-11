"""Pure versioned business matcher. No Agent imports, no I/O while matching, no ID exceptions."""
from hashlib import sha256
import json

VERSION='agent-stage1-v2-matcher-v1'
ORDER=('analysis_state','effective_scope','request_goal','user_intent','analysis_intent','query_plan','required_tools',
       'tool_execution','tool_target','task_acceptance','pending_goal','final_status')
KINDS={'get_top_issues':'issues','get_positive_aspects':'positive_aspects','get_aspect_drilldown':'aspect_drilldown',
       'get_evidence':'evidence','search_semantic_evidence':'semantic_evidence'}


def content_hash(value):return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def matches(actual,expected):
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(k in actual and matches(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(matches(a,b) for a,b in zip(actual,expected))
    if isinstance(expected,bool) or expected is None:return type(actual) is type(expected) and actual==expected
    return actual==expected


def acceptance_matches(actual,expected):
    if isinstance(expected,dict) and expected.get('representation_rule')=='absent_or_explicit_failure':
        return actual is None or any(v is not None and matches(actual,v) for v in expected['permitted_representations'])
    return matches(actual,expected)


def state_presence(state):return {k:'present' if state.get(k) else 'absent' for k in ('ranking_basis','pending_goal')}


def state_source_valid(value,task,current,scope,kind):
    if not value:return True
    try:
        source=value['source'];params=task['parameters'];result=task['result']
        digest=content_hash(['analysis-task-integrity-v1',task['task_id'],task['status'],params,result])
        sealed=task['progress']['analysis_integrity']=={'version':'analysis-task-integrity-v1','sha256':digest}
        identity=('source','dataset_version','model','base_url','prompt_version')
        if (not sealed or source['task_sha256']!=digest or source['task_id']!=task['task_id']
                or params.get('conversation_id')!=current['parameters'].get('conversation_id')
                or any(params.get(k)!=current['parameters'].get(k) for k in identity)
                or value['scope']!=scope or value['scope_sha256']!=content_hash(scope)):
            return False
        if kind=='pending_goal':
            goal=value['goal']
            return (task['status']=='succeeded' and result['status']=='unsupported'
                and result['user_intent'].get('needs_clarification') is True and value.get('completed') is False
                and goal['levels']==['themes'] and goal['sentiment']=='negative' and goal['aspect'] is None
                and len(scope['product_ids'])==1 and goal['original_question']==params['question'])
        return (task['status']=='succeeded' and result['status']=='ok' and not task.get('error')
            and result['validation']['status']=='passed' and result['task_acceptance']['complete'] is True)
    except (KeyError,TypeError,ValueError):return False


def trusted_state(state,current,scope,tasks):
    for key in ('ranking_basis','pending_goal'):
        value=state.get(key)
        if value:
            parent=tasks.get((value.get('source') or {}).get('task_id'))
            if parent is None or not state_source_valid(value,parent,current,scope,key):return False
    return True


def project(turn):
    r=turn['task'].get('result') or {};events=turn.get('events',[])
    plan=next((e['plan'] for e in events if e['node']=='router_planner'),r.get('query_plan')) or {}
    req=plan.get('requirements',[]);blocked=not req;intent=r.get('user_intent') or {};goal=r.get('request_goal')
    sentiment=plan.get('sentiment') if not blocked else intent.get('sentiment',(goal or {}).get('sentiment'))
    aspects=list(dict.fromkeys(row.get('parameters',{}).get('aspect') for row in req if row.get('parameters',{}).get('aspect')))
    tools=list(dict.fromkeys(row['tool'] for row in req))
    state=r.get('analysis_state') or {};post=(turn.get('post_context') or {}).get('analysis_state') or {}
    outputs=[v for v in r.get('results',{}).values() if v.get('kind')!='context']
    calls=[e for e in events if e.get('node')=='tool_start']
    reads=[e for e in events if e.get('node')=='data_double']
    prep=r.get('p6_preparation') or {}
    execution={'business_queries':max(r.get('query_executions',0),prep.get('query_executions',0)),
        'registry_calls':len(calls),'classification_attempts':len(turn.get('simulated_model_attempts',[])),
        'retrieval_calls':max(sum(e.get('name')=='search_semantic_evidence' for e in calls),sum(e.get('node')=='rag_double' for e in events)),
        'data_reads':len(reads),'current_statistics_reads':sum(e.get('function')=='get_top_issues' for e in reads),
        'aspect_prefetch_reads':sum(e.get('function')=='get_aspect_opinions' for e in reads)}
    return {'analysis_state':state_presence(state),'effective_scope':r.get('scope'),
        'request_goal':goal,'user_intent':intent,'analysis_intent':'not_finalized' if blocked else plan.get('analysis_type'),
        'query_plan':{'blocked':blocked,'analysis_type':None if blocked else plan.get('analysis_type'),'aspects':aspects,'sentiment':sentiment},
        'required_tools':tools,'task_acceptance':r.get('task_acceptance'),
        'pending_goal':'awaiting_aspect' if post.get('pending_goal') else 'absent',
        'final_status':{'agent':r.get('status'),'worker':turn['task']['status']},'execution':execution,
        'raw_analysis_intent':r.get('analysis_intent'),'raw_sentiment_intent':r.get('sentiment_intent'),
        'raw_final_plan':r.get('query_plan'),'main_plan':plan,'state':state,'post_state':post,
        'outputs':outputs,'calls':calls,'reads':reads}


def no_publication(r,*,diagnostic_refs=False):
    pub=r.get('p6_publication') or {};answer=r.get('public_answer') or {};public=r.get('public_analysis') or {}
    refs_ok=not r.get('result_ids') or (diagnostic_refs and r.get('status')=='error' and
        answer.get('status') not in ('ok','partial') and public.get('status') not in ('ok','partial'))
    return (refs_ok and not any(d.get('topics') for d in pub.get('dimensions',[]))
        and not any(answer.get(k) for k in ('highlights','metrics','evidence_refs'))
        and not any(public.get(k) for k in ('statistics','highlights','evidence_refs')))


def p6_publication_valid(r,scope,required):
    pub=r.get('p6_publication') or {};dims=pub.get('dimensions',[]);prep=r.get('p6_preparation') or {}
    if not matches(pub.get('scope'),scope) or pub.get('required_aspects')!=required or [d.get('aspect') for d in dims]!=required:return False
    if pub.get('D')!=(prep.get('current_snapshot') or {}).get('denominator',{}).get('value'):return False
    D=pub.get('D');allowed=pub.get('publishable_aspects',[])
    for dim in dims:
        aspect=dim['aspect'];M=dim.get('M_a');S=dim.get('S_a');topics=dim.get('topics') or []
        if (type(D) is not int or type(M) is not int or type(S) is not int or not 0<=S<=M<=D
                or M!=(prep.get('current_snapshot') or {}).get('negative_review_counts',{}).get(aspect)):
            return False
        if aspect not in allowed:
            if topics:return False
            continue
        if dim.get('status')!='complete' or not topics or len(topics)>3:return False
        if dim.get('negative_rate')!=(round(M/D,6) if D else None):return False
        for topic in topics:
            n=topic.get('N_a_t');denom=topic.get('sample_denominator');evidence=topic.get('evidence') or []
            if type(n) is not int or not 0<n<=S or denom!=S or topic.get('sample_rate')!=round(n/S,6) or not evidence:return False
            for quote in evidence:
                try:
                    if (quote['product_id'] not in scope['product_ids'] or quote['aspect']!=aspect
                        or quote['sentiment']!='negative' or quote['topic_id']!=topic['topic_id']
                        or quote['text'][quote['quote_start']:quote['quote_end']]!=quote['quote'] or not quote['quote']):return False
                except (KeyError,TypeError):return False
    return True


def tool_target_matches(turn,e,c,p):
    r=turn['task'].get('result') or {};scope=c['expected_effective_scope'];stage=c['phase'];x=p['execution']
    if stage=='clarification':return x['registry_calls']==x['data_reads']==x['retrieval_calls']==x['classification_attempts']==0 and no_publication(r)
    if stage=='preparation_block':return no_publication(r) and x['aspect_prefetch_reads']==0 and x['classification_attempts']==0
    pub=r.get('p6_publication')
    if pub is not None:return p6_publication_valid(r,scope,(e.get('expected_request_goal') or {}).get('required_aspects',[]))
    queryscope={k:v for k,v in scope.items() if not k.startswith('baseline_')}
    if not all(matches(o.get('data',{}).get('scope'),queryscope) for o in p['outputs']):return False
    for tool in e['expected_required_tools']:
        candidates=[o for o in p['outputs'] if o['kind']==KINDS[tool]]
        if not candidates:return False
        for row in candidates:
            data=row['data'];goal=e.get('expected_request_goal') or {}
            if row['kind'] in ('evidence','aspect_drilldown') and any(goal.get(k) and data.get(k)!=goal[k] for k in ('aspect','sentiment')):return False
    if e['expected_task_acceptance'].get('contract')=='frozen_RAG_separate':
        permitted=any(o['data'].get('assessment',{}).get('permitted_keys') for o in p['outputs'] if o['kind']=='semantic_evidence')
        if permitted!=e['expected_task_acceptance']['permitted_evidence']:return False
    return True


def assess(turn,e,c,tasks=None):
    tasks=tasks or {};r=turn['task'].get('result') or {};p=project(turn);x=p['execution'];stage=c['phase']
    current=turn['task'];scope=c['expected_effective_scope'];checks={}
    checks['analysis_state']=matches(p['analysis_state'],e['expected_analysis_state']) and trusted_state(p['state'],current,scope,tasks)
    checks['effective_scope']=p['effective_scope']==scope
    checks['request_goal']=matches(p['request_goal'],e['expected_request_goal'])
    if p['request_goal'] is not None:
        # Confirmed-query wrappers keep their declared original purpose; ordinary user text stays exact.
        original=p['request_goal'].get('original_question')
        checks['request_goal'] &= bool(original) and (original==turn['input'] or turn['input'].startswith('执行本轮已确认的只读查询'))
    known=c.get('known_constraints',{})
    checks['user_intent']=matches(p['user_intent'],e['expected_user_intent'])
    if stage=='clarification':
        checks['user_intent'] &= all(p['user_intent'].get(k)==v for k,v in known.items())
        question=p['user_intent'].get('clarification_question') or r.get('answer') or ''
        checks['user_intent'] &= bool(question) and all(any(word in question for word in words) for words in c.get('clarification_alternatives',[]))
    checks['analysis_intent']=p['analysis_intent']==e['expected_analysis_intent']
    checks['query_plan']=matches(p['query_plan'],e['expected_query_plan'])
    checks['required_tools']=p['required_tools']==e['expected_required_tools']
    execution_rule=c.get('execution') or {}
    checks['tool_execution']=all(call.get('plan_authorized') is True for call in p['calls'])
    if stage=='clarification':
        checks['tool_execution'] &= (x['business_queries']==x['registry_calls']==x['classification_attempts']==x['retrieval_calls']==x['data_reads']==0)
        checks['tool_execution'] &= not p['main_plan'].get('requirements') and not (r.get('task_acceptance') or {}).get('complete') and no_publication(r)
    elif stage=='preparation_block':
        checks['tool_execution'] &= ((r.get('p6_preparation') or {}).get('reason')==execution_rule['preparation_reason']
            and x['current_statistics_reads']==execution_rule['current_ranking_validation_reads']
            and x['business_queries']==execution_rule['current_ranking_validation_reads']
            and x['aspect_prefetch_reads']==x['classification_attempts']==x['registry_calls']==0
            and not r.get('p6_execution') and no_publication(r))
    elif 'classification_attempts' in execution_rule:
        checks['tool_execution'] &= x['classification_attempts']==execution_rule['classification_attempts']
    checks['tool_target']=tool_target_matches(turn,e,c,p)
    actual_acceptance=p['task_acceptance'];expected_acceptance=e['expected_task_acceptance']
    if expected_acceptance.get('contract')=='frozen_RAG_separate':
        actual_acceptance={'contract':'frozen_RAG_separate','permitted_evidence':any(
            o['data'].get('assessment',{}).get('permitted_keys') for o in p['outputs'] if o['kind']=='semantic_evidence')}
    elif e.get('clarification_must_execute_zero') and not expected_acceptance.get('representation_rule'):
        # V1 unreviewed clarification rows retain their original not_completed label; absence is allowed by the original contract.
        expected_acceptance={'representation_rule':'absent_or_explicit_failure',
            'permitted_representations':[None,{'status':'failed','complete':False}]}
    checks['task_acceptance']=acceptance_matches(actual_acceptance,expected_acceptance)
    if stage=='partial_publication':
        pub=r.get('p6_publication') or {};children={d['aspect']:d['status'] for d in pub.get('dimensions',[])}
        required=execution_rule['frozen_order']
        checks['task_acceptance'] &= (pub.get('required_aspects')==required and
            (r.get('task_acceptance') or {}).get('required_aspects')==required and children==execution_rule['child_statuses']
            and pub.get('publishable_aspects')==execution_rule['publishable_aspects'] and pub.get('complete') is False)
    checks['pending_goal']=p['pending_goal']==e['expected_pending_goal'] and trusted_state(p['post_state'],current,scope,tasks)
    if e['expected_pending_goal']=='awaiting_aspect' and p['pending_goal']=='awaiting_aspect':
        pending=p['post_state']['pending_goal'];old=p['state'].get('pending_goal')
        if old:
            checks['pending_goal'] &= all(pending.get(k)==old.get(k) for k in ('goal','scope','source','completed','status'))
    checks['final_status']=p['final_status']==e['expected_final_status']
    rejected=(r.get('status')=='error' and current['status']=='failed' and
        (r.get('task_acceptance') or {}).get('complete') is False and not r.get('answer') and no_publication(r,diagnostic_refs=True))
    expected_deviations=c.get('expected_injected_deviations',[])
    if stage=='plan_fault_rejection':
        checks['tool_execution'] &= (x['registry_calls']==x['business_queries']==x['classification_attempts']==x['data_reads']==x['retrieval_calls']==0
            and (r.get('task_plan_check') or {}).get('passed') is False
            and (r.get('evidence_completion') or {}).get('status','not_started')=='not_started')
        checks['tool_target']=x['registry_calls']==x['business_queries']==0
        checks['task_acceptance'] &= rejected
    elif stage=='result_fault_rejection':checks['task_acceptance'] &= rejected
    differences=[k for k in ORDER if not checks[k]]
    failures=[k for k in differences if k not in expected_deviations]
    if c.get('score_mode')=='injected_error_rejection' and not rejected:
        if 'task_acceptance' not in failures:failures.append('task_acceptance')
    # No per-ID override, no PENDING_HUMAN exclusions in v2.
    actual={k:p[k] for k in ORDER if k not in ('tool_execution','tool_target')}
    actual['execution']=x;actual['raw_analysis_intent']=p['raw_analysis_intent'];actual['raw_sentiment_intent']=p['raw_sentiment_intent']
    return {'id':turn['id'],'status':'FAIL' if failures else 'PASS','phase':stage,'checks':checks,
        'first_deviation':failures[0] if failures else None,'failures':failures,'raw_field_differences':differences,
        'expected_injected_deviations':expected_deviations,'actual':actual,'expected':{k:v for k,v in e.items() if k.startswith('expected_')},
        'effective_scope_contract':scope,'execution_contract':execution_rule,
        'raw_final_analysis_intent':r.get('analysis_intent'),'raw_final_plan':r.get('query_plan'),
        'injected_fault':bool(c.get('score_mode')=='injected_error_rejection')}


def ratio(rows,predicate):
    n=sum(bool(predicate(r)) for r in rows);d=len(rows)
    return {'passed':n,'total':d,'percent':round(n*100/d,2) if d else None}


def summarize(run,oracle):
    expectations={f"{g['id']}-T{i}":t for g in oracle['groups'] for i,t in enumerate(g['turns'],1)}
    observed={t['id']:t for t in run['turns']}
    if set(expectations)!=set(observed):raise ValueError('Missing, duplicate or extra evaluation turns')
    if len(run['turns'])!=len(observed):raise ValueError('Duplicate evaluation turn')
    tasks={tid:t['task'] for tid,t in observed.items()}
    rows=[assess(observed[tid],e,oracle['execution_contracts'][tid],tasks) for tid,e in expectations.items()]
    natural=[r for r in rows if not r['injected_fault']]
    metrics={
      'state_context':ratio(natural,lambda r:all(r['checks'][k] for k in ('analysis_state','effective_scope','pending_goal'))),
      'intent_router':ratio(natural,lambda r:all(r['checks'][k] for k in ('user_intent','analysis_intent','effective_scope'))),
      'request_goal':ratio(natural,lambda r:r['checks']['request_goal']),
      'planner':ratio(natural,lambda r:all(r['checks'][k] for k in ('query_plan','required_tools','effective_scope'))),
      'tools_stage_and_target':ratio(natural,lambda r:r['checks']['tool_execution'] and r['checks']['tool_target']),
      'validator_injected_error_interception':ratio([r for r in rows if r['injected_fault']],lambda r:r['checks']['task_acceptance'] and r['checks']['final_status']),
      'pre_tool_block':ratio([r for r in rows if r['phase'] in ('clarification','preparation_block','plan_fault_rejection')],lambda r:r['checks']['tool_execution']),
      'strict_business_completion':ratio([r for r in rows if expectations[r['id']]['strict_business_completion'] and not r['injected_fault']],lambda r:r['status']=='PASS' and (
           ((observed[r['id']]['task'].get('result') or {}).get('task_acceptance') or {}).get('complete') is True or
           expectations[r['id']]['expected_task_acceptance'].get('permitted_evidence') is True)),
      'turn_end_to_end':ratio(rows,lambda r:r['status']=='PASS')}
    group_results={g['id']:'PASS' if all(r['status']=='PASS' for r in rows if r['id'].startswith(g['id']+'-T')) else 'FAIL' for g in oracle['groups']}
    metrics['conversation_end_to_end']=ratio(list(group_results.values()),lambda s:s=='PASS')
    return {'matcher_version':VERSION,'metrics':metrics,'groups':group_results,'turns':rows,
       'pass_count':sum(r['status']=='PASS' for r in rows),'fail_count':sum(r['status']=='FAIL' for r in rows),'pending_count':0}
