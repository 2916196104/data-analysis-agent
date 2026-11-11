"""Literal A business assertions, independent Goal/Router calls and unchanged real Worker chain."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent_stage1_multiturn_evaluation import Harness, use_source_revision, ROOT
from data_analysis_agent.agents.review_acceptance import build_request_goal
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_semantics import QuestionScopeError
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs import analysis_context
from data_analysis_agent.jobs.analysis_state import has_saved_integrity, restore_pending_goal, restore_ranking
from data_analysis_agent.jobs.store import analysis_result_integrity

A, B = '100013588053', '100013588055'
PRODUCTS = [{'product_id':p,'title':'合成商品'+p,'brand':'合成'} for p in (A,B)]


class OracleV2BusinessFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        use_source_revision(ROOT/'docs/evaluation/agent-stage1-v2-fix/final/source-revision.v5.json')
        cls.h = Harness()
        cls.h.oracle = json.loads((ROOT/'tests/fixtures/agent_stage1/oracle.v2.json').read_text(encoding='utf-8'))
        cls.h.oracle['groups'] = [g for g in cls.h.oracle['groups'] if g['id'] in ('G02','G24','G30')]
        with redirect_stdout(io.StringIO()):cls.turns = {t['id']:t for t in cls.h.run()}

    def assert_clarification(self, tid):
        t=self.turns[tid];r=t['task']['result']
        self.assertEqual((t['task']['status'],r['status']),('succeeded','unsupported'))
        self.assertIsNone(r['request_goal']);self.assertTrue(r['user_intent']['needs_clarification'])
        self.assertFalse((r.get('query_plan') or {}).get('requirements'))
        self.assertEqual((r.get('query_plan') or {}).get('required_tools',[]),[])
        self.assertFalse((r.get('task_acceptance') or {}).get('complete'))
        self.assertEqual(r['scope']['product_ids'],[A])
        for key in ('query_executions','tool_calls','topic_model_calls','intent_model_calls','answer_model_calls','total_model_calls'):
            self.assertEqual(r[key],0,key)
        self.assertEqual(r['results'],{});self.assertEqual(r['result_ids'],[])
        self.assertEqual(t['simulated_model_attempts'],[])
        self.assertFalse(any(e['node'] in ('tool_start','data_double','rag_double','p6_execution') for e in t['events']))
        self.assertEqual(r['evidence_completion']['status'],'not_started')
        self.assertEqual(r['evidence_completion']['additional_tool_calls'],0)
        self.assertEqual(self.h.forbidden_attempts,[])
        return r

    def context(self,tid='G30-T3'):
        return deepcopy(self.turns[tid]['input_context'])

    def resolve(self,q,ctx):
        state={};scope={**self.h.oracle['scope'],'product_ids':[A]}
        with (patch('data_analysis_agent.agents.review_acceptance.build_request_goal',side_effect=AssertionError('Router must be independent')),
                patch('data_analysis_agent.agents.review_intent._llm_intent',side_effect=AssertionError('No model for ambiguity'))):
            bound,plan,_=resolve_review_request(q,scope,PRODUCTS,settings=replace(self.h.settings,semantic_parser_enabled=True),context=ctx,intent_state=state)
        return bound,plan,state

    def goal(self,q,ctx):
        with (patch('data_analysis_agent.agents.review_intent.resolve_review_request',side_effect=AssertionError('Goal must be independent')),
                patch('data_analysis_agent.agents.review_semantics.opinion_request',side_effect=AssertionError('Goal must not copy Router'))):
            return build_request_goal(q,ctx)

    def test_pending_sentiment_worker_clarifies_positive_without_executing(self):
        r=self.assert_clarification('G30-T3')
        self.assertEqual(r['user_intent']['sentiment'],'positive')
        self.assertIn('好评统计',r['answer']);self.assertIn('主题具体表现',r['answer'])

    def test_rag_aspect_worker_knows_dimension_but_not_level_or_fact(self):
        r=self.assert_clarification('G24-T2')
        self.assertEqual(r['user_intent']['aspect'],'service')
        self.assertIsNone(r['user_intent']['sentiment'])
        self.assertIn('统计',r['answer']);self.assertIn('检索',r['answer'])
        self.assertNotIn('客服回复慢',r['answer'])

    def test_goals_are_unfrozen_independently_of_router(self):
        for tid in ('G24-T2','G30-T3'):
            with self.subTest(tid=tid):self.assertIsNone(self.goal(self.turns[tid]['input'],self.context(tid)))

    def test_router_clarifies_independently_of_request_goal_and_models(self):
        for tid in ('G24-T2','G30-T3'):
            with self.subTest(tid=tid),self.assertRaises(QuestionScopeError):
                self.resolve(self.turns[tid]['input'],self.context(tid))

    def test_legacy_user_conditions_clarify_without_reconstructing_trusted_facts(self):
        ctx=self.context();ctx.pop('analysis_state')
        self.assertIsNone(self.goal('那好评呢？',ctx))
        with self.assertRaises(QuestionScopeError):self.resolve('那好评呢？',ctx)
        self.assertNotIn('analysis_state',ctx)

    def test_short_sentiment_forms_keep_ambiguity_not_case_id(self):
        for q in ('好评呢？','那么正面呢？','那差评呢？'):
            with self.subTest(q=q):
                self.assertIsNone(self.goal(q,self.context()))
                with self.assertRaises(QuestionScopeError):self.resolve(q,self.context())

    def test_rag_short_aspect_forms_are_not_implicit_statistics(self):
        for q in ('物流呢？','质量方面呢？','那包装怎么样？'):
            with self.subTest(q=q):
                self.assertIsNone(self.goal(q,self.context('G24-T2')))
                with self.assertRaises(QuestionScopeError):self.resolve(q,self.context('G24-T2'))

    def test_g02_without_pending_executes_valid_positive_population_statistics(self):
        t=self.turns['G02-T2'];r=t['task']['result']
        self.assertEqual((r['request_goal']['levels'],r['request_goal']['sentiment']),(['statistics'],'positive'))
        self.assertEqual(r['query_plan']['required_tools'],['get_positive_aspects'])
        self.assertTrue(r['task_acceptance']['complete'])
        self.assertTrue(any(e['node']=='tool_start' and e['name']=='get_positive_aspects' for e in t['events']))
        row=next(x['data'] for x in r['results'].values() if x['kind']=='positive_aspects')
        self.assertEqual(row['scope']['product_ids'],[A])
        self.assertEqual(t['simulated_model_attempts'],[])

    def test_explicit_statistics_after_rag_or_pending_still_takes_priority(self):
        for ctx in (self.context(),self.context('G24-T2')):
            for q in ('只统计客服差评数量。','只统计客服好评数量。'):
                with self.subTest(q=q,rag=ctx['validated']['analysis_type']):
                    expected='negative' if '差评' in q else 'positive'
                    g=self.goal(q,ctx);_,p,i=self.resolve(q,ctx)
                    self.assertEqual((g.levels,g.aspect,g.sentiment),(('statistics',),'service',expected))
                    self.assertEqual(p.required_tools,['get_top_issues' if expected=='negative' else 'get_positive_aspects'])
                    self.assertEqual(p.requirements[0]['parameters']['aspect'],'service')
                    self.assertEqual((i['aspect'],i['sentiment']),('service',expected))

    def test_explicit_positive_themes_do_not_require_level_clarification(self):
        q='那正面的具体表现呢？';g=self.goal(q,self.context());_,p,i=self.resolve(q,self.context())
        self.assertEqual((g.levels,g.sentiment),(('themes',),'positive'))
        self.assertEqual(p.required_tools,['get_aspect_drilldown']);self.assertEqual(i['sentiment'],'positive')

    def test_completed_single_theme_is_a_unique_sentiment_continuation(self):
        ctx=self.context();ctx['validated'].update(analysis_type='aspect_drilldown',queries=[
            {'kind':'aspect_drilldown','parameters':{'product_ids':[A],'aspect':'logistics','sentiment':'negative'}}])
        g=self.goal('那好评呢？',ctx);_,p,i=self.resolve('那好评呢？',ctx)
        self.assertEqual((g.levels,g.aspect,g.sentiment),(('themes',),'logistics','positive'))
        self.assertEqual(p.requirements[0]['parameters']['aspect'],'logistics')
        self.assertEqual(i['query_type'],'aspect_drilldown')

    def test_non_rag_short_aspect_preserves_evidence_level(self):
        ctx=self.context('G24-T2');ctx['validated'].update(analysis_type='evidence',sentiment='negative',queries=[
            {'kind':'evidence','parameters':{'product_ids':[A],'aspect':'logistics','sentiment':'negative','page_size':3}}])
        g=self.goal('客服呢？',ctx);_,p,_=self.resolve('客服呢？',ctx)
        self.assertEqual((g.levels,g.aspect),(('evidence',),'service'))
        self.assertEqual(p.required_tools,['get_evidence'])

    def test_pending_preserves_exact_original_goal_scope_and_sealed_source(self):
        t=self.turns['G30-T3'];before=t['input_context']['analysis_state']['pending_goal']
        after=t['post_context']['analysis_state']['pending_goal'];self.assertEqual(before,after)
        original=self.h.tasks[after['source']['task_id']]
        self.assertTrue(has_saved_integrity(original));self.assertEqual(restore_pending_goal(original),after)
        self.assertEqual(after['goal'],original['result']['request_goal'])
        self.assertEqual(after['scope'],original['result']['scope'])
        self.assertEqual(after['scope_sha256'],content_sha256(after['scope']))
        self.assertEqual(after['source']['task_sha256'],original['progress']['analysis_integrity']['sha256'])
        self.assertEqual((after['goal']['levels'],after['goal']['sentiment']),(['themes'],'negative'))
        self.assertEqual((after['status'],after['completed']),('awaiting_aspect',False))
        self.assertNotEqual(original['task_id'],t['id'])
        self.assertIsNone(restore_pending_goal(t['task']))
        self.assertEqual(t['post_context']['analysis_state']['ranking_basis'],t['input_context']['analysis_state']['ranking_basis'])

    def load(self,changes=None,tasks=None):
        current=self.turns['G30-T3']['task'];scope=deepcopy(self.h.oracle['scope']);scope.update(changes or {})
        parameters={**scope,'selection_scope':scope,'conversation_id':current['parameters']['conversation_id'],
            'context_task_id':current['task_id']}
        if changes and 'conversation_id' in changes:parameters['conversation_id']=changes['conversation_id']
        saved=tasks or self.h.tasks
        with patch.object(analysis_context,'get_task',lambda _,tid:deepcopy(saved[tid])):
            return analysis_context.load_analysis_context(None,parameters)

    def test_cross_session_identity_products_and_dates_cannot_restore_pending(self):
        for changes in ({'conversation_id':'another-session'},{'model':'another-label-model'},
                {'product_ids':[B]},{'date_from':'2023-02-15'}):
            with self.subTest(changes=changes):
                state=(self.load(changes) or {}).get('analysis_state',{})
                self.assertIsNone(state.get('pending_goal'));self.assertIsNone(state.get('ranking_basis'))

    def test_pending_source_tamper_cannot_be_recovered(self):
        tasks=deepcopy(self.h.tasks);task=tasks['G30-T2'];task['result']['request_goal']['sentiment']='positive'
        state=(self.load(tasks=tasks) or {}).get('analysis_state',{})
        self.assertIsNone(state.get('pending_goal'))
        self.assertIsNone(restore_pending_goal(task))

    def test_resealed_wrong_pending_semantics_are_still_rejected(self):
        task=deepcopy(self.h.tasks['G30-T2']);task['result']['request_goal']['sentiment']='positive'
        task['progress']['analysis_integrity']=analysis_result_integrity(task['task_id'],task['status'],task['parameters'],task['result'])
        self.assertTrue(has_saved_integrity(task));self.assertIsNone(restore_pending_goal(task))

    def test_ranking_source_tamper_is_not_a_trusted_completed_fact(self):
        task=deepcopy(self.h.tasks['G30-T1']);task['result']['task_acceptance']['complete']=False
        self.assertIsNone(restore_ranking(task))


if __name__=='__main__':unittest.main()
