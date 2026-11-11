"""Independent matcher fixtures only; no Agent, database, SDK or recorded outputs."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_stage1_v2_scoring as score

SCOPE={'source':'fiction','dataset_version':'toy-v1','model':'toy-labels','base_url':'https://toy.invalid/v1',
 'prompt_version':'toy','product_ids':['P'],'date_from':'2030-02-01','date_to':'2030-02-28',
 'baseline_from':'2030-01-01','baseline_to':'2030-01-31','as_of':None}


def fixture(stage='clarification'):
    goal={'levels':['themes'],'aspect':None,'sentiment':None}
    r={'scope':deepcopy(SCOPE),'request_goal':{**goal,'original_question':'合成任务'},'user_intent':{'needs_clarification':True},
       'analysis_intent':'candidate_route','sentiment_intent':'negative','analysis_state':{},'query_executions':0,
       'status':'unsupported','result_ids':[],'results':{},'answer':'请选择要分析的范围和情感。',
       'public_answer':{'status':'unsupported','highlights':[],'metrics':[],'evidence_refs':[]}}
    t={'id':'toy-T1','input':'合成任务','task':{'task_id':'toy-T1','status':'succeeded','parameters':{**deepcopy(SCOPE),'conversation_id':'toy'},'result':r},
       'events':[],'post_context':{'analysis_state':{}},'simulated_model_attempts':[]}
    e={'expected_analysis_state':{'ranking_basis':'absent','pending_goal':'absent'},'expected_scope':deepcopy(SCOPE),
       'expected_request_goal':goal,'expected_user_intent':{'needs_clarification':True},'expected_analysis_intent':'not_finalized',
       'expected_query_plan':{'blocked':True,'analysis_type':None,'aspects':[],'sentiment':None},'expected_required_tools':[],
       'expected_task_acceptance':{'representation_rule':'absent_or_explicit_failure','permitted_representations':[None,{'status':'failed','complete':False}]},
       'expected_pending_goal':'absent','expected_final_status':{'agent':'unsupported','worker':'succeeded'},
       'clarification_must_execute_zero':True,'strict_business_completion':False}
    c={'phase':stage,'expected_effective_scope':deepcopy(SCOPE),'execution':{},'known_constraints':{'aspect':None,'sentiment':None},
       'expected_injected_deviations':[],'score_mode':'all_required_checks'}
    return t,e,c


def planned(stage='completed',tools=None):
    t,e,c=fixture(stage);r=t['task']['result'];tools=tools or [('get_top_issues','logistics')]
    goal={'levels':['statistics'],'aspect':'logistics','sentiment':'negative'}
    r.update(request_goal={**goal,'original_question':t['input']},user_intent={'query_type':'aspect_summary','aspect':'logistics','sentiment':'negative'},
       analysis_intent='issues',sentiment_intent='negative',status='ok',task_acceptance={'status':'passed','complete':True},
       query_plan={'analysis_type':'issues','sentiment':'negative','requirements':[{'tool':tool,'parameters':{'aspect':aspect}} for tool,aspect in tools]},
       validation={'status':'passed'},query_executions=1,result_ids=['r1'])
    r['results']={'r1':{'kind':'issues','data':{'scope':{k:v for k,v in SCOPE.items() if not k.startswith('baseline_')}}}}
    t['events']=[{'node':'tool_start','name':'get_top_issues','plan_authorized':True},
                 {'node':'data_double','function':'get_top_issues'}]
    e.update(expected_request_goal=goal,expected_user_intent={'query_type':'aspect_summary','aspect':'logistics','sentiment':'negative'},
       expected_analysis_intent='issues',expected_query_plan={'blocked':False,'analysis_type':'issues','aspects':['logistics'],'sentiment':'negative'},
       expected_required_tools=['get_top_issues'],expected_task_acceptance={'status':'passed','complete':True},
       expected_final_status={'agent':'ok','worker':'succeeded'},clarification_must_execute_zero=False,strict_business_completion=True)
    return t,e,c


def stale():
    t,e,c=planned('preparation_block');r=t['task']['result']
    r.update(status='unsupported',result_ids=[],results={},p6_preparation={'reason':'stale_ranking','query_executions':1},
             task_acceptance={'status':'failed','complete':False,'evidence_status':'not_executed'})
    t['events']=[{'node':'data_double','function':'get_top_issues'}]
    c['execution']={'preparation_reason':'stale_ranking','current_ranking_validation_reads':1}
    e.update(expected_final_status={'agent':'unsupported','worker':'succeeded'},
             expected_task_acceptance={'status':'failed','complete':False,'evidence_status':'not_executed'})
    return t,e,c


def partial():
    t,e,c=planned('partial_publication');r=t['task']['result'];required=['service','effectiveness','logistics']
    goal={'levels':['themes'],'aspect':None,'sentiment':'negative','required_aspects':required}
    statuses={'service':'complete','effectiveness':'failed','logistics':'complete'}
    r.update(request_goal={**goal,'original_question':t['input']},user_intent={'query_type':'aspect_drilldown','aspect':None,'sentiment':'negative'},
       analysis_intent='aspect_drilldown',status='partial',query_executions=4,
       query_plan={'analysis_type':'aspect_drilldown','sentiment':'negative','requirements':[
          {'tool':'get_aspect_drilldown','parameters':{'aspect':a}} for a in required]},
       task_acceptance={'status':'partial','parent_status':'partial','complete':False,'required_aspects':required},
       p6_preparation={'query_executions':4,'current_snapshot':{'denominator':{'value':8},'negative_review_counts':{a:2 for a in required}}},results={})
    r['p6_publication']={'scope':deepcopy(SCOPE),'D':8,'required_aspects':required,'publishable_aspects':['service','logistics'],
       'complete':False,'dimensions':[]}
    for aspect in required:
        topic={'topic_id':aspect,'N_a_t':2,'sample_denominator':2,'sample_rate':1.,'evidence':[
            {'product_id':'P','aspect':aspect,'sentiment':'negative','topic_id':aspect,'quote':'支持内容','quote_start':0,'quote_end':4,'text':'支持内容'}]}
        r['p6_publication']['dimensions'].append({'aspect':aspect,'status':statuses[aspect],'M_a':2,'S_a':2,
            'negative_rate':.25,'topics':[topic] if statuses[aspect]=='complete' else []})
    t['task']['status']='partial';t['simulated_model_attempts']=[{} for _ in range(5)]
    t['events']=[]
    e.update(expected_request_goal=goal,expected_user_intent={'query_type':'aspect_drilldown','aspect':None,'sentiment':'negative'},
       expected_analysis_intent='aspect_drilldown',expected_query_plan={'blocked':False,'analysis_type':'aspect_drilldown','aspects':required,'sentiment':'negative'},
       expected_required_tools=['get_aspect_drilldown'],expected_task_acceptance={'status':'partial','parent_status':'partial','complete':False},
       expected_final_status={'agent':'partial','worker':'partial'},strict_business_completion=False)
    c['execution']={'frozen_order':required,'classification_attempts':5,'child_statuses':statuses,'publishable_aspects':['service','logistics']}
    return t,e,c


def with_pending():
    t,e,c=fixture();scope=deepcopy(SCOPE);goal={'levels':['themes'],'aspect':None,'sentiment':'negative','original_question':'缺维度的主题请求'}
    parent={'task_id':'parent','status':'succeeded','parameters':{**scope,'conversation_id':'toy','question':goal['original_question']},
        'result':{'status':'unsupported','user_intent':{'needs_clarification':True}},'progress':{}}
    h=score.content_hash(['analysis-task-integrity-v1',parent['task_id'],parent['status'],parent['parameters'],parent['result']])
    parent['progress']['analysis_integrity']={'version':'analysis-task-integrity-v1','sha256':h}
    value={'source':{'task_id':'parent','task_sha256':h},'scope':scope,'scope_sha256':score.content_hash(scope),
       'goal':goal,'status':'awaiting_aspect','completed':False}
    t['task']['result']['analysis_state']={'pending_goal':deepcopy(value)}
    t['post_context']['analysis_state']={'pending_goal':deepcopy(value)}
    e['expected_analysis_state']['pending_goal']='present';e['expected_pending_goal']='awaiting_aspect'
    return t,e,c,{'parent':parent}


class MatcherTests(unittest.TestCase):
    def assert_pass(self,parts):self.assertEqual(score.assess(*parts)['status'],'PASS')
    def assert_fail(self,parts,node):self.assertFalse(score.assess(*parts)['checks'][node])

    def test_partial_goal_and_null_acceptance_clarify(self):self.assert_pass(fixture())
    def test_unfrozen_goal_is_distinct_from_statistics_placeholder(self):
        t,e,c=fixture();e['expected_request_goal']=None;t['task']['result']['request_goal']=None;self.assert_pass((t,e,c))
        t['task']['result']['request_goal']={'levels':['statistics'],'aspect':'service','sentiment':None}
        self.assert_fail((t,e,c),'request_goal')
    def test_explicit_failure_acceptance_is_valid_in_clarification(self):
        t,e,c=fixture();t['task']['result']['task_acceptance']={'status':'failed','complete':False};self.assert_pass((t,e,c))
    def test_complete_true_in_clarification_rejected(self):
        t,e,c=fixture();t['task']['result']['task_acceptance']={'status':'failed','complete':True};self.assert_fail((t,e,c),'task_acceptance')
    def test_error_diagnostic_ids_are_not_published_facts(self):
        r={'status':'error','result_ids':['debug-r1'],'public_answer':{'status':'failed'},'public_analysis':{'status':'failed'}}
        self.assertTrue(score.no_publication(r,diagnostic_refs=True))
        self.assertFalse(score.no_publication(r))
        r['public_answer']['evidence_refs']=[{'review_id':1}]
        self.assertFalse(score.no_publication(r,diagnostic_refs=True))
    def test_false_does_not_accept_zero(self):self.assertFalse(score.matches({'complete':0},{'complete':False}))
    def test_raw_candidate_hint_is_retained_not_finalized_plan(self):
        t,e,c=fixture();r=score.assess(t,e,c);self.assertEqual(r['actual']['analysis_intent'],'not_finalized')
        self.assertEqual(r['actual']['raw_sentiment_intent'],'negative');self.assertEqual(r['status'],'PASS')
    def test_guessing_sentiment_in_goal_rejected(self):
        t,e,c=fixture();t['task']['result']['request_goal']['sentiment']='negative';self.assert_fail((t,e,c),'request_goal')
    def test_guessing_sentiment_in_user_intent_rejected(self):
        t,e,c=fixture();t['task']['result']['user_intent']['sentiment']='negative';self.assert_fail((t,e,c),'user_intent')
    def test_clarification_not_allowed_to_have_executable_plan(self):
        t,e,c=fixture();t['task']['result']['query_plan']={'analysis_type':'issues','requirements':[{'tool':'get_top_issues'}]}
        self.assert_fail((t,e,c),'query_plan')
    def test_catalog_reads_before_block_are_allowed(self):
        t,e,c=fixture();t['events']=[{'node':'connection_double','sql':'SELECT product_id FROM products'}];self.assert_pass((t,e,c))
    def test_unaccounted_data_read_is_not_catalog_read(self):
        t,e,c=fixture();t['events']=[{'node':'data_double','function':'get_aspect_opinions'}];self.assert_fail((t,e,c),'tool_execution')
    def test_registry_overview_still_forbidden_after_known_block(self):
        t,e,c=fixture();t['events']=[{'node':'tool_start','name':'get_analysis_context','plan_authorized':True}];self.assert_fail((t,e,c),'tool_execution')
    def test_vector_service_start_in_clarification_rejected(self):
        t,e,c=fixture();t['events']=[{'node':'rag_double'}];self.assert_fail((t,e,c),'tool_execution')
    def test_classifier_call_in_clarification_rejected(self):
        t,e,c=fixture();t['simulated_model_attempts']=[{}];self.assert_fail((t,e,c),'tool_execution')
    def test_business_scope_exact_identity_check(self):
        t,e,c=planned();t['task']['result']['scope']['model']='other';self.assert_fail((t,e,c),'effective_scope')
    def test_actual_tool_wrong_product_rejected(self):
        t,e,c=planned();t['task']['result']['results']['r1']['data']['scope']['product_ids']=['OTHER'];self.assert_fail((t,e,c),'tool_target')
    def test_statistics_success_is_matched_without_model(self):self.assert_pass(planned())
    def test_stale_plan_can_exist_without_theme_artifacts(self):self.assert_pass(stale())
    def test_stale_prefetch_is_rejected_even_without_classifier(self):
        t,e,c=stale();t['events'].append({'node':'data_double','function':'get_aspect_opinions'});self.assert_fail((t,e,c),'tool_execution')
    def test_stale_classifier_and_hidden_success_are_rejected(self):
        t,e,c=stale();t['simulated_model_attempts']=[{}];self.assert_fail((t,e,c),'tool_execution')
        t['task']['result']['task_acceptance']['complete']=True;self.assert_fail((t,e,c),'task_acceptance')
    def test_partial_parent_and_child_mapping(self):self.assert_pass(partial())
    def test_partial_cannot_be_ok_or_complete(self):
        t,e,c=partial();t['task']['result']['status']='ok';self.assert_fail((t,e,c),'final_status')
        t['task']['result']['task_acceptance']['complete']=True;self.assert_fail((t,e,c),'task_acceptance')
    def test_partial_missing_duplicate_or_substituted_dimension_rejected(self):
        for mutate in ('missing','duplicate','replace'):
            t,e,c=partial();dims=t['task']['result']['p6_publication']['dimensions']
            if mutate=='missing':dims.pop()
            elif mutate=='duplicate':dims[2]=deepcopy(dims[0])
            else:dims[2]['aspect']='price'
            with self.subTest(mutate=mutate):self.assert_fail((t,e,c),'tool_target')
    def test_failed_dimension_cannot_publish_topics(self):
        t,e,c=partial();p=t['task']['result']['p6_publication'];p['dimensions'][1]['topics']=deepcopy(p['dimensions'][0]['topics'])
        self.assert_fail((t,e,c),'tool_target')
    def test_quote_tamper_and_sample_denominator_rejected(self):
        for change in ('quote','denominator'):
            t,e,c=partial();topic=t['task']['result']['p6_publication']['dimensions'][0]['topics'][0]
            if change=='quote':topic['evidence'][0]['quote']='无来源'
            else:topic['sample_denominator']=3
            with self.subTest(change=change):self.assert_fail((t,e,c),'tool_target')
    def test_pending_preserved_not_completed_or_replaced(self):
        t,e,c,tasks=with_pending();self.assert_pass((t,e,c,tasks))
        t['post_context']['analysis_state']['pending_goal']['goal']['sentiment']='positive';self.assert_fail((t,e,c,tasks),'pending_goal')
    def test_pending_cross_session_and_unknown_source_rejected(self):
        t,e,c,tasks=with_pending();tasks['parent']['parameters']['conversation_id']='other';self.assert_fail((t,e,c,tasks),'analysis_state')
        self.assert_fail((t,e,c,{}),'analysis_state')
    def test_pending_disappearing_is_not_completion(self):
        t,e,c,tasks=with_pending();t['post_context']['analysis_state']={};self.assert_fail((t,e,c,tasks),'pending_goal')
    def test_case_id_does_not_change_checks(self):
        t,e,c=fixture();before=score.assess(t,e,c)
        for name in ('random-id','G30-T3','G10-T2'):
            t['id']=name;after=score.assess(t,e,c)
            self.assertEqual((after['checks'],after['status']),(before['checks'],before['status']))
    def test_no_case_ids_in_matcher_source(self):
        source=Path(score.__file__).read_text(encoding='utf-8')
        for tid in ('G10','G11','G12','G17','G18','G19','G20','G24','G30'):self.assertNotIn(tid,source)
    def test_fault_requires_actual_rejection_and_zero_execution(self):
        t,e,c=planned('plan_fault_rejection');r=t['task']['result'];goal={'levels':['themes'],'aspect':'logistics','sentiment':'negative'}
        r.update(request_goal={**goal,'original_question':t['input']},user_intent={'query_type':'aspect_drilldown','aspect':'logistics','sentiment':'negative'},
            status='error',task_acceptance={'status':'failed','complete':False},task_plan_check={'passed':False},answer=None,results={},result_ids=[],query_executions=0)
        t['task']['status']='failed';t['events']=[]
        e.update(expected_request_goal=goal,expected_user_intent=r['user_intent'],expected_analysis_intent='aspect_drilldown',
            expected_query_plan={'blocked':False,'analysis_type':'aspect_drilldown','aspects':['logistics'],'sentiment':'negative'},
            expected_required_tools=['get_aspect_drilldown'],expected_task_acceptance={'status':'failed','complete':False},
            expected_final_status={'agent':'error','worker':'failed'})
        c.update(score_mode='injected_error_rejection',expected_injected_deviations=['analysis_intent','query_plan','required_tools'])
        self.assert_pass((t,e,c));t['simulated_model_attempts']=[{}];self.assert_fail((t,e,c),'tool_execution')
    def test_summary_no_pending_human_id_exclusion(self):
        t,e,c=planned();oracle={'groups':[{'id':'toy','turns':[e]}],'execution_contracts':{t['id']:c}}
        result=score.summarize({'turns':[t]},oracle)
        self.assertEqual((result['pass_count'],result['fail_count'],result['pending_count']),(1,0,0))
        self.assertEqual(result['metrics']['turn_end_to_end']['total'],1)
    def test_duplicate_run_turn_rejected(self):
        t,e,c=planned();oracle={'groups':[{'id':'toy','turns':[e]}],'execution_contracts':{t['id']:c}}
        with self.assertRaises(ValueError):score.summarize({'turns':[t,t]},oracle)


if __name__=='__main__':unittest.main()
