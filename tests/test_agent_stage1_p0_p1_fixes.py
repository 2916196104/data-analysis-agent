"""Literal business expectations and real Worker chain; no database or model service."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

from agent_stage1_multiturn_evaluation import Harness
from data_analysis_agent.agents.review_acceptance import build_request_goal, build_task_goal, validate_task_completion
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_semantics import QuestionScopeError
from data_analysis_agent.jobs import analysis_context

A,B='100013588053','100013588055'
TO_THEMES='不是问数量，我想知道物流差评具体在抱怨什么。'
TO_COUNTS='不要归纳主题了，只统计客服差评数量。'
PRODUCTS=[{'product_id':p,'title':'合成商品'+p,'brand':'合成'} for p in (A,B)]


class MinimalBusinessFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h=Harness()
        cls.h.oracle['groups']=[g for g in cls.h.oracle['groups'] if g['id'] in
            {'G01','G02','G03','G04','G13','G14','G15','G22','G23','G28','G29'}]
        with redirect_stdout(io.StringIO()):cls.outputs=cls.h.run()
        cls.turns={t['id']:t for t in cls.outputs}

    def result(self,tid):return self.turns[tid]['task']['result']

    def assert_single_scope_after_failure(self,gid):
        r=self.result(gid+'-T2')
        self.assertEqual(r['scope']['product_ids'],[A])
        self.assertTrue(r['task_acceptance']['complete'])
        for row in r['results'].values():self.assertEqual(row['data']['scope']['product_ids'],[A])
        data=next(row['data'] for row in r['results'].values() if row['kind']=='issues')
        self.assertEqual(data['current']['successful_reviews'],8)
        aspect,count=('service',5) if gid=='G13' else ('logistics',0 if gid=='G14' else 3)
        self.assertEqual(data['current']['aspects'][aspect]['negative_review_count'],count)
        state=r['analysis_state']
        self.assertIsNone(state['ranking_basis'])
        self.assertIsNone(self.turns[gid+'-T2']['input_context']['validated'])

    def test_g13_unsupported_retains_user_product_not_failed_facts(self):self.assert_single_scope_after_failure('G13')
    def test_g14_missing_opinions_retains_product_even_when_counts_zero(self):self.assert_single_scope_after_failure('G14')
    def test_g15_classifier_error_retains_user_product(self):self.assert_single_scope_after_failure('G15')

    def test_request_goal_counts_to_themes_independent_literal_expectation(self):
        goal=build_request_goal(TO_THEMES)
        self.assertEqual((goal.levels,goal.aspect,goal.sentiment),(('themes',),'logistics','negative'))
        self.assertEqual(goal.original_question,TO_THEMES)

    def test_request_goal_themes_to_counts_independent_literal_expectation(self):
        goal=build_request_goal(TO_COUNTS)
        self.assertEqual((goal.levels,goal.aspect,goal.sentiment),(('statistics',),'service','negative'))
        self.assertEqual(goal.original_question,TO_COUNTS)

    def test_router_each_direction_without_reading_goal(self):
        for question,kind,aspect,tool in [(TO_THEMES,'aspect_drilldown','logistics','get_aspect_drilldown'),
                (TO_COUNTS,'aspect_summary','service','get_top_issues')]:
            with self.subTest(question=question),patch('data_analysis_agent.agents.review_acceptance.build_request_goal',
                    side_effect=AssertionError('Router must independently classify the effective request')):
                intent={};scope={**self.h.oracle['scope'],'product_ids':[A]}
                _,plan,_=resolve_review_request(question,scope,PRODUCTS,settings=self.h.settings,intent_state=intent)
                self.assertEqual((intent['query_type'],intent['aspect'],intent['sentiment']),(kind,aspect,'negative'))
                self.assertEqual(plan.required_tools,[tool])

    def test_request_goal_does_not_call_router_semantic_classifier(self):
        with patch('data_analysis_agent.agents.review_semantics.opinion_request',side_effect=AssertionError('independent goal')):
            self.assertEqual(build_request_goal(TO_THEMES).levels,('themes',))
            self.assertEqual(build_request_goal(TO_COUNTS).levels,('statistics',))

    def test_no_universal_theme_or_statistics_priority(self):
        for question,level,tool in [('物流差评有多少条？','statistics','get_top_issues'),
                ('物流差评主要体现在哪里？','themes','get_aspect_drilldown'),
                ('给我三条物流差评原文。','evidence','get_evidence')]:
            with self.subTest(question=question):
                self.assertEqual(build_request_goal(question).levels,(level,))
                self.assertEqual(build_query_plan(question,{**self.h.oracle['scope'],'product_ids':[A]}).required_tools,[tool])

    def test_g22_worker_completes_real_themes_and_preserves_original_question(self):
        r=self.result('G22-T2')
        self.assertEqual(r['request_goal']['levels'],['themes'])
        self.assertEqual(r['query_plan']['required_tools'],['get_aspect_drilldown'])
        self.assertTrue(r['task_acceptance']['complete'])
        self.assertEqual(r['request_goal']['original_question'],TO_THEMES)

    def test_g23_worker_only_statistics_no_classification(self):
        r=self.result('G23-T2')
        self.assertEqual(r['request_goal']['levels'],['statistics'])
        self.assertEqual(r['query_plan']['required_tools'],['get_top_issues'])
        self.assertEqual(self.turns['G23-T2']['simulated_model_attempts'],[])
        self.assertTrue(r['task_acceptance']['complete'])

    def test_g28_known_plan_conflict_stops_before_tools_and_completion_loop(self):
        t=self.turns['G28-T2'];r=t['task']['result']
        self.assertFalse(r['task_plan_check']['passed'])
        self.assertEqual(r['status'],'error');self.assertEqual(t['task']['status'],'failed')
        self.assertFalse(r['task_acceptance']['complete'])
        self.assertEqual(r['query_executions'],0)
        self.assertFalse(any(e['node'] in ('tool_start','data_double') for e in t['events']))
        self.assertEqual(r['evidence_completion']['status'],'not_started')
        self.assertNotIn('answer',r)
        self.assertTrue(any(e['node']=='connection_double' for e in t['events']))

    def test_confirmed_query_prefix_cannot_bypass_goal_conflict_block(self):
        h=Harness();group=deepcopy(next(g for g in h.oracle['groups'] if g['id']=='G03'))
        group['turns']=group['turns'][:1]
        group['turns'][0]['input']='执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_top_issues',
            'parameters':{'aspect':'logistics'},'question':'物流差评主要体现在哪里？'})
        h.oracle['scope']['product_ids']=[A]
        h.oracle['groups']=[group]
        with redirect_stdout(io.StringIO()):t=h.run()[0]
        r=t['task']['result']
        self.assertFalse(r['task_plan_check']['passed'])
        self.assertEqual((r['status'],r['query_executions']),('error',0))
        self.assertFalse(any(e['node']=='tool_start' for e in t['events']))

    def test_final_validator_still_rejects_conflicting_plan(self):
        scope={**self.h.oracle['scope'],'product_ids':[A]}
        plan=build_query_plan('物流差评有多少条？',scope)
        goal=build_task_goal('物流差评主要体现在哪里？',scope,plan,{},
            request_goal=build_request_goal('物流差评主要体现在哪里？'))
        checked=validate_task_completion(goal,scope,plan,[])
        self.assertFalse(checked['complete']);self.assertEqual(checked['status'],'failed')

    def test_original_g01_to_g04_and_source_tamper_barrier_remain(self):
        for tid in ['G01-T2','G02-T2','G03-T2','G04-T2']:
            self.assertEqual(self.result(tid)['status'],'ok',tid)
        self.assertEqual(self.result('G29-T1')['status'],'error')

    def context(self, *, selection=None, changes=None, conversation='same-session', question=None, invalid=False):
        h=Harness();scope=deepcopy(h.oracle['scope']);previous={**scope,'selection_scope':scope,'question':question or A+'物流差评具体体现在哪里？',
            'conversation_id':'same-session','context_task_id':None}
        if invalid:previous['date_from']='not-a-date'
        h.tasks['failed']={'task_id':'failed','kind':'analysis','parameters':previous,'progress':{}}
        h.finish(None,'failed',None,status='failed',result={'status':'error','scope':{**scope,'product_ids':[B]},
            'analysis_state':{'ranking_basis':{'forged':True}}},error={'code':'synthetic_failure'})
        current={**scope,**(changes or {})};chosen={**current,**(selection or {})}
        current.update(chosen,selection_scope=chosen,conversation_id=conversation,context_task_id='failed')
        with patch.object(analysis_context,'get_task',lambda *a:deepcopy(h.tasks['failed'])):
            context=analysis_context.load_analysis_context(None,current)
        return h,current,context

    def resolve(self,h,current,context,question='物流差评有多少条？'):
        scope={k:current[k] for k in h.oracle['scope']}
        return resolve_review_request(question,scope,PRODUCTS,settings=h.settings,context=context)[0]

    def test_failed_result_scope_is_not_a_user_condition(self):
        h,p,c=self.context();bound=self.resolve(h,p,c)
        self.assertEqual(bound['product_ids'],[A]);self.assertIsNone(c['validated'])
        self.assertIsNone(c['analysis_state']['ranking_basis'])

    def test_current_explicit_product_replaces_failed_previous_product(self):
        h,p,c=self.context();self.assertEqual(self.resolve(h,p,c,B+'物流差评有多少条？')['product_ids'],[B])

    def test_changed_page_product_selection_takes_priority(self):
        h,p,c=self.context(selection={'product_ids':[B]})
        self.assertEqual(self.resolve(h,p,c)['product_ids'],[B])

    def test_current_explicit_date_uses_new_range(self):
        h,p,c=self.context()
        bound=self.resolve(h,p,c,'2023-02-15到2023-02-28，物流差评有多少条？')
        self.assertEqual((bound['product_ids'],bound['date_from']),([A],'2023-02-15'))

    def test_page_date_change_does_not_reuse_failed_old_window(self):
        h,p,c=self.context(selection={'date_from':'2023-02-15'})
        self.assertEqual(self.resolve(h,p,c)['date_from'],'2023-02-15')

    def test_failed_explicit_date_remains_a_user_condition(self):
        h,p,c=self.context(question='2023-02-15到2023-02-28，'+A+'物流差评具体体现在哪里？')
        bound=self.resolve(h,p,c)
        self.assertEqual((bound['product_ids'],bound['date_from'],bound['date_to']),([A],'2023-02-15','2023-02-28'))

    def test_failed_date_outside_effective_selection_cannot_be_inherited(self):
        h,p,c=self.context(question='2022-02-15到2022-02-28，'+A+'物流差评具体体现在哪里？')
        with self.assertRaises(QuestionScopeError):self.resolve(h,p,c)

    def test_changed_page_window_supersedes_explicit_failed_window(self):
        h,p,c=self.context(question='2023-02-15到2023-02-20，'+A+'物流差评具体体现在哪里？',
            selection={'date_from':'2023-02-21'})
        bound=self.resolve(h,p,c)
        self.assertEqual((bound['date_from'],bound['date_to']),('2023-02-21','2023-02-28'))

    def test_cross_session_and_identity_change_restore_no_user_conditions(self):
        for kwargs in ({'conversation':'other-session'},{'changes':{'model':'other-label-model'}}):
            with self.subTest(kwargs=kwargs):self.assertIsNone(self.context(**kwargs)[2])

    def test_invalid_saved_scope_rejected(self):self.assertIsNone(self.context(invalid=True)[2])

    def test_unknown_previous_product_is_not_silently_replaced_with_page_scope(self):
        h,p,c=self.context(question='999999999999物流差评主要体现在哪里？')
        with self.assertRaises(QuestionScopeError):self.resolve(h,p,c)


if __name__=='__main__':unittest.main()
