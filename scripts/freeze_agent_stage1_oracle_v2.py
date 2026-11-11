"""Freeze the user's ten A decisions; stdlib only, no Agent outputs or scoring."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/agent_stage1'
ADJ=ROOT/'docs/evaluation/agent-stage1-human-adjudication.v1.json'
PENDING=FIX/'oracle.v2.pending.json'
IDS=('G10-T2','G11-T2','G12-T1','G12-T2','G17-T2','G18-T2','G19-T2','G20-T2','G24-T2','G30-T3')


def digest(path):return sha256(path.read_bytes()).hexdigest()
def canonical_digest(value):return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def validate_option(option):
    e=option['proposed_expectations'];x=option['execution_constraints'];errors=[]
    if e['expected_query_plan']['blocked']:
        if e['expected_required_tools'] or not e['expected_user_intent'].get('needs_clarification'):
            errors.append('clarification must have no executable requirements and needs_clarification=true')
        if x.get('classification_attempts')!=0 or x.get('business_query_executions')!=0:
            errors.append('clarification must forbid queries and classification')
    a=e['expected_task_acceptance']
    if a.get('status')=='partial':
        if a.get('complete') is not False or e['expected_final_status']!={'agent':'partial','worker':'partial'}:
            errors.append('partial mapping is inconsistent')
        aspects=e['expected_request_goal'].get('required_aspects')
        if aspects!=x.get('frozen_order') or set(x.get('child_statuses',{}))!=set(aspects or []):
            errors.append('partial target and child coverage differ')
    if x.get('preparation_reason')=='stale_ranking':
        if x.get('aspect_prefetch_reads')!=0 or x.get('classification_attempts')!=0 or a.get('complete') is not False:
            errors.append('stale ranking must block classification and completion')
    if a.get('status')=='not_completed':errors.append('not_completed is not an acceptance status')
    return errors


def phase(turn,execution):
    if turn['expected_query_plan']['blocked']:return 'clarification'
    if execution.get('preparation_reason'):return 'preparation_block'
    if turn.get('fault')=='statistics_plan':return 'plan_fault_rejection'
    if turn.get('fault')=='wrong_result_product':return 'result_fault_rejection'
    if turn['expected_final_status']['agent']=='partial':return 'partial_publication'
    if turn['expected_task_acceptance'].get('status')=='insufficient_evidence':return 'insufficient_evidence'
    if turn['expected_final_status']['agent']=='error':return 'execution_failure'
    return 'completed'


def assemble(v1,pending,now):
    original={f"{g['id']}-T{i}":t for g in v1['groups'] for i,t in enumerate(g['turns'],1)}
    candidates={f"{g['id']}-T{i}":t['human_review'] for g in pending['groups'] for i,t in enumerate(g['turns'],1) if 'human_review' in t}
    if set(candidates)!=set(IDS):raise ValueError('ten-case candidate set changed')
    decisions=[];problems={}
    for tid in IDS:
        case=candidates[tid];selected=next(o for o in case['options'] if o['id']==tid+'-A')
        errors=validate_option(selected)
        if errors:problems[tid]=errors
        decisions.append({'case_id':tid,'selected':'A','selected_option_id':selected['id'],
            'selected_option_snapshot':deepcopy(selected),'selected_option_sha256':canonical_digest(selected),
            'business_rationale':case['independent_rationale'],'classification':case['classification'],
            'unselected_options':deepcopy([o for o in case['options'] if o['id']!=selected['id']])})
    if problems:raise ValueError('Cannot freeze contradictory selected options: '+json.dumps(problems,ensure_ascii=False))
    record={'version':'agent-stage1-human-adjudication-v1','record_created_at':now,
        'timezone':'Asia/Taipei','utc_offset':'+08:00','historical_decision_timestamp':None,
        'actor':{'description':'本轮会话用户；未提供真实身份或签名','real_name':None,'signature':None},
        'authority':{'decision_statement':'10轮全部选 A',
            'source':'用户本轮消息明确重申十项全部选 A，并要求正式冻结；未虚构其他消息 ID 或历史签署时间。',
            'scope':'business_semantics_and_evaluation_expectations_only',
            'excluded':['business_code_changes','database_operations','model_fees','deployment','P6-E1B_recovery']},
        'candidate_file_sha256':digest(PENDING),'worksheet_sha256':digest(ROOT/'docs/evaluation/agent-stage1-human-review.md'),
        'consistency_review':{'passed':True,'checks':'blocked-plan and partial/stale state consistency',
            'representation_clarifications':['not_finalized is a review projection, not an API status',
                'a blocked plan may preserve known sentiment but has no executable requirements',
                'stale planned tools do not authorize prefetch/classification',
                'G30 preserves the old negative pending goal, not a new completed positive goal']},
        'decisions':decisions}
    oracle=deepcopy(v1)
    oracle.update(version='agent-stage1-independent-oracle-v2',
        frozen_at_utc=datetime.fromisoformat(now).astimezone(timezone.utc).isoformat(),frozen_at=now,timezone='Asia/Taipei',
        authorship='Original v1 plus ten user-confirmed A contracts; no Agent output used to change expectations.',
        human_adjudication_reference='docs/evaluation/agent-stage1-human-adjudication.v1.json',
        v1_original_frozen_at_utc=v1['frozen_at_utc'],formal=True)
    contracts={};decision_by={d['case_id']:d for d in decisions}
    for group in oracle['groups']:
        selection=deepcopy(oracle['scope'])
        for i,turn in enumerate(group['turns'],1):
            tid=f"{group['id']}-T{i}";selection.update(turn.get('selection_changes') or {})
            execution={}
            if tid in decision_by:
                d=decision_by[tid];turn.update(deepcopy(d['selected_option_snapshot']['proposed_expectations']))
                execution=deepcopy(d['selected_option_snapshot']['execution_constraints'])
                turn.update(adjudication='HUMAN_CONFIRMED_A',strict_business_completion=False,
                    clarification_must_execute_zero=turn['expected_query_plan']['blocked'])
            effective={**deepcopy(selection),**turn['expected_scope']}
            expected_phase=phase(turn,execution)
            constraints={'phase':expected_phase,'expected_effective_scope':effective,'execution':execution,
                'score_mode':'injected_error_rejection' if turn.get('fault') else 'all_required_checks',
                'expected_injected_deviations':(['analysis_intent','query_plan','required_tools'] if turn.get('fault')=='statistics_plan'
                    else ['tool_target'] if turn.get('fault')=='wrong_result_product' else []),
                'known_constraints':{},'clarification_alternatives':[]}
            if expected_phase=='clarification':
                constraints['execution']={**execution,'business_query_executions':0,'classification_attempts':0,
                    'vector_retrieval_calls':0,'registry_tool_calls':0,'must_not_publish_evidence':True}
                constraints['known_constraints']={'aspect':(turn.get('expected_request_goal') or {}).get('aspect'),
                    'sentiment':turn['expected_query_plan'].get('sentiment')}
            # Explicit facts supplied in the user's adjudication, not inferred from runtime.
            if tid=='G24-T2':constraints['known_constraints']['aspect']='service'
            if tid=='G30-T3':
                constraints['known_constraints']['sentiment']='positive'
                constraints['clarification_alternatives']=[['统计','数量','占比'],['主题','具体表现','具体好评','好评内容']]
                constraints['pending_lifecycle']='preserve_existing_negative_unfinished_goal'
            if tid in decision_by:turn['expected_effective_scope']=deepcopy(effective)
            contracts[tid]=constraints
    unchanged=[tid for tid in original if tid not in decision_by]
    compiled={f"{g['id']}-T{i}":t for g in oracle['groups'] for i,t in enumerate(g['turns'],1)}
    assert len(compiled)==62 and all(compiled[t]==original[t] for t in unchanged)
    oracle['execution_contracts']=contracts
    record['unchanged_original_turns']=len(unchanged)
    return record,oracle


def main():
    paths=[ADJ,FIX/'oracle.v2.json',FIX/'scoring-contract.v2.json',FIX/'freeze-manifest.v2.json']
    if any(p.exists() for p in paths):raise SystemExit('Refuse to overwrite official adjudication/oracle/contract/manifest')
    now=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat(timespec='microseconds')
    record,oracle=assemble(read(FIX/'oracle.v1.json'),read(PENDING),now)
    contract={'version':'agent-stage1-v2-matcher-v1','frozen_at':now,'timezone':'Asia/Taipei',
       'principle':'No case-ID PASS logic. Compare independent expectations and explicit execution phase.',
       'projections':{
         'analysis_state':'scope-projected input ranking/pending presence; raw state retained',
         'analysis_intent':'not_finalized iff no executable main requirements; raw intent/sentiment retained as diagnostics',
         'blocked_plan':'no requirements; sentiment reflects only user_intent or partial Goal known sentiment, never a guessed raw routing hint',
         'effective_scope':'exact normalized scope identity/products/windows from gold, not outputs',
         'pending_goal':'post-turn legal state; if input pending exists and preservation is required, goal/scope/source task remain identical',
         'acceptance':'supports null, subset partial/failed, and explicit absent-or-failure rule',
         'rag':'frozen separate contract and permitted_keys; no RAG permission changes',
         'pre_tool_block':'catalog transaction/GUC reads before planning allowed; registry/data calls and classifiers after known block forbidden'},
       'phase_rules':{
         'clarification':'no executable plan; needs_clarification; zero query/registry/retrieval/classification; no facts or complete=true',
         'preparation_block':'preserve compiled plan and frozen target; verify named reason/statistics validation count; zero prefetch/classification/public topics',
         'partial_publication':'retain exact required_aspects and per-child statuses, publish only specified independently accepted aspects; parent partial/false',
         'plan_fault_rejection':'verify independent Goal, false plan check and final rejection; no tools/classification; injected planner deviations are reported separately',
         'result_fault_rejection':'verify final source/numeric rejection and no publication; altered artifact is the expected injected deviation',
         'completed_or_insufficient':'compare output scope/capability and acceptance; numeric success never substitutes for semantic task acceptance'},
       'first_deviation_order':['analysis_state','effective_scope','request_goal','user_intent','analysis_intent','query_plan','required_tools',
           'tool_execution','tool_target','task_acceptance','pending_goal','final_status'],
       'metrics':{'all_turn_contract':62,'natural_module_denominator':'all turns except explicitly injected faults (60)',
         'tool_before_block':'all clarification/preparation-block/plan-fault-block turns; denominator computed from frozen contracts',
         'strict_completion':'unchanged v1 strict_business_completion=true and expected successful analysis/RAG-permission goals; excludes deliberate faults',
         'conversation_contract':'all 30 groups, no PENDING_HUMAN exclusions',
         'v1_comparison':'historical only; expanded/phase-aware denominators are not quality trend evidence'},
       'matcher_path':'scripts/agent_stage1_v2_scoring.py','unit_test_path':'tests/test_agent_stage1_v2_scoring.py'}
    # The immutable contract also binds the implementation used to match it.
    bindings=['scripts/agent_stage1_v2_scoring.py','scripts/run_agent_stage1_oracle_v2.py',
              'scripts/freeze_agent_stage1_oracle_v2.py','tests/test_agent_stage1_v2_scoring.py']
    for p in bindings:
        if not (ROOT/p).is_file():raise SystemExit('Missing reviewed matcher/runner/tests: '+p)
    write(ADJ,record)
    oracle['human_adjudication_sha256']=digest(ADJ)
    write(FIX/'oracle.v2.json',oracle);write(FIX/'scoring-contract.v2.json',contract)
    manifest={'version':'agent-stage1-freeze-v2','frozen_at':now,'timezone':'Asia/Taipei',
      'oracle':'tests/fixtures/agent_stage1/oracle.v2.json','oracle_sha256':digest(FIX/'oracle.v2.json'),
      'adjudication_sha256':digest(ADJ),'scoring_contract_sha256':digest(FIX/'scoring-contract.v2.json'),
      'v1_oracle_sha256':digest(FIX/'oracle.v1.json'),'pending_oracle_sha256':digest(PENDING),
      'v1_manifest_sha256':digest(FIX/'freeze-manifest.json'),'corpus_sha256':digest(FIX/'corpus.v1.json'),
      'source_revision_path':'docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json',
      'source_revision_sha256':digest(ROOT/'docs/evaluation/agent-stage1-remaining-p1/source-revision.v4.json'),
      'evaluation_code_sha256':{p:digest(ROOT/p) for p in bindings},
      'existing_harness_sha256':digest(ROOT/'tests/agent_stage1_multiturn_evaluation.py'),
      'v1_scorer_sha256':digest(ROOT/'scripts/summarize_agent_stage1_evaluation.py'),
      'groups':30,'turns':62,'adjudicated_turns':10,'unchanged_turns':52,'frozen_before_execution':True}
    write(FIX/'freeze-manifest.v2.json',manifest)
    print(json.dumps({'record_created_at':now,'decisions':10,'unchanged_turns':52,'oracle_sha256':manifest['oracle_sha256'],
        'consistency':'passed; no Agent execution'},ensure_ascii=False))


if __name__=='__main__':main()
