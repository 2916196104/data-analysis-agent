"""Versioned implementation correction: known partial-Goal constraints need not be repeated in Intent.

The frozen v2 rules explicitly allow either representation. No expectations or ID rules change.
"""
import agent_stage1_v2_scoring as original
from agent_stage1_v2_scoring import (
    content_hash, matches, acceptance_matches, project, no_publication, ratio, ORDER,
)

VERSION='agent-stage1-v2-matcher-v1.1'


def assess(turn,e,c,tasks=None):
    result=original.assess(turn,e,c,tasks)
    if c['phase']=='clarification':
        r=turn['task'].get('result') or {};intent=r.get('user_intent') or {};goal=r.get('request_goal') or {}
        question=intent.get('clarification_question') or r.get('answer') or ''
        # Explicit Intent conflicts still reject. Only an absent slot can use its independently matched Goal.
        known=all((intent[k] if k in intent else goal.get(k))==v for k,v in c.get('known_constraints',{}).items())
        result['checks']['user_intent']=(matches(intent,e['expected_user_intent']) and known and bool(question)
            and all(any(word in question for word in words) for words in c.get('clarification_alternatives',[])))
    differences=[k for k in ORDER if not result['checks'][k]]
    failures=[k for k in differences if k not in c.get('expected_injected_deviations',[])]
    result.update(status='FAIL' if failures else 'PASS',raw_field_differences=differences,failures=failures,
        first_deviation=failures[0] if failures else None,matcher_version=VERSION)
    return result


def summarize(run,oracle):
    expectations={f"{g['id']}-T{i}":t for g in oracle['groups'] for i,t in enumerate(g['turns'],1)}
    observed={t['id']:t for t in run['turns']}
    if set(expectations)!=set(observed) or len(run['turns'])!=len(observed):raise ValueError('Missing, duplicate or extra evaluation turns')
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
