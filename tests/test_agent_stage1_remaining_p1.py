"""Remaining P1: actual Worker/tool evidence and independently asserted intent goals."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

from agent_stage1_multiturn_evaluation import Harness
from data_analysis_agent.agents.review_acceptance import build_request_goal
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_semantics import QuestionScopeError
from data_analysis_agent.jobs import analysis_context
from data_analysis_agent.jobs.analysis_state import restore_pending_goal, pending_context
from data_analysis_agent.jobs.store import analysis_result_integrity

A,B='100013588053','100013588055'
SWITCH='不要分析物流了，改看商品质量。'
PRODUCTS=[{'product_id':p,'title':'合成商品'+p,'brand':'合成'} for p in (A,B)]


class RemainingP1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h=Harness()
        cls.h.oracle['groups']=[g for g in cls.h.oracle['groups'] if g['id'] in ('G05','G09','G25','G26')]
        with redirect_stdout(io.StringIO()):cls.outputs=cls.h.run()
        cls.turns={t['id']:t for t in cls.outputs}

    def result(self,tid):return self.turns[tid]['task']['result']

    def context(self,kind='aspect_drilldown',aspect='logistics',sentiment='negative'):
        scope={**self.h.oracle['scope'],'product_ids':[A]}
        return {'validated':{'scope':scope,'sentiment':sentiment,'queries':[
            {'kind':kind,'parameters':{'product_ids':[A],'aspect':aspect,'sentiment':sentiment,'page_size':3}}]}}

    def resolve(self,question,context):
        state={};scope={**self.h.oracle['scope'],'product_ids':[A]}
        with patch('data_analysis_agent.agents.review_intent._llm_intent',side_effect=AssertionError('no model')):
            bound,plan,_=resolve_review_request(question,scope,PRODUCTS,settings=self.h.settings,context=context,intent_state=state)
        return bound,plan,state

    def test_g05_goal_inherits_unique_themes_but_replaces_aspect(self):
        with patch('data_analysis_agent.agents.review_semantics.opinion_request',side_effect=AssertionError('independent goal')):
            goal=build_request_goal(SWITCH,self.context())
        self.assertEqual((goal.levels,goal.aspect,goal.sentiment),(('themes',),'quality','negative'))
        self.assertEqual(goal.original_question,SWITCH)

    def test_g05_router_independent_and_worker_executes_only_quality(self):
        with patch('data_analysis_agent.agents.review_acceptance.build_request_goal',side_effect=AssertionError('independent router')):
            _,plan,intent=self.resolve(SWITCH,self.context())
        self.assertEqual((intent['query_type'],intent['aspect'],intent['sentiment']),('aspect_drilldown','quality','negative'))
        self.assertEqual(plan.required_tools,['get_aspect_drilldown'])
        t=self.turns['G05-T2'];r=t['task']['result']
        self.assertEqual(r['scope']['product_ids'],[A]);self.assertTrue(r['task_acceptance']['complete'])
        themes=[e for e in t['events'] if e['node']=='tool_start' and e['name']=='get_aspect_drilldown']
        self.assertEqual([e['parameters']['aspect'] for e in themes],['quality'])
        data=next(v['data'] for v in r['results'].values() if v['kind']=='aspect_drilldown')
        self.assertEqual((data['total'],data['summary']['successful_reviews']),(2,8))
        self.assertTrue(data['topics'])
        for item in data['items']:
            self.assertEqual(item['product_id'],A)
            for opinion in item['opinions']:
                self.assertEqual((opinion['aspect'],opinion['sentiment']),('quality','negative'))
                self.assertEqual(item['text'][opinion['quote_start']:opinion['quote_end']],opinion['quote'])

    def test_switch_after_statistics_stays_statistics(self):
        ctx=self.context('issues')
        self.assertEqual(build_request_goal(SWITCH,ctx).levels,('statistics',))
        _,plan,_=self.resolve(SWITCH,ctx)
        self.assertEqual(plan.required_tools,['get_top_issues'])
        self.assertEqual(plan.requirements[0]['parameters']['aspect'],'quality')

    def test_switch_after_evidence_stays_evidence_and_uses_new_aspect(self):
        ctx=self.context('evidence')
        self.assertEqual(build_request_goal(SWITCH,ctx).levels,('evidence',))
        _,plan,_=self.resolve(SWITCH,ctx)
        self.assertEqual(plan.required_tools,['get_evidence'])
        self.assertEqual(plan.requirements[0]['parameters']['aspect'],'quality')

    def test_switch_without_history_and_with_multiple_levels_clarifies(self):
        ctx=self.context();ctx['validated']['queries'].append({'kind':'evidence','parameters':{'aspect':'service','sentiment':'negative'}})
        for history in (None,ctx):
            with self.subTest(history=bool(history)),self.assertRaises(QuestionScopeError):self.resolve(SWITCH,history)

    def test_g09_pending_recovers_new_window_not_old_ranking(self):
        t=self.turns['G09-T2'];r=t['task']['result'];state=t['post_context']['analysis_state']
        self.assertEqual(r['p6_preparation']['reason'],'ranking_scope_mismatch')
        self.assertEqual(r['query_executions'],0);self.assertIsNone(state['ranking_basis'])
        self.assertTrue(r['user_intent']['needs_clarification'])
        self.assertEqual(state['pending_goal']['scope']['product_ids'],[A])
        self.assertEqual(state['pending_goal']['scope']['date_from'],'2023-02-15')
        goal,_=pending_context(t['post_context'])
        self.assertEqual((goal['levels'],goal['sentiment']),(['themes'],'negative'))
        self.assertEqual(restore_pending_goal(t['task']),state['pending_goal'])

    def load_post(self,**changes):
        h=Harness();h.tasks=deepcopy(self.h.tasks)
        selection={**h.oracle['scope'],'date_from':'2023-02-15',**changes}
        parameters={**selection,'selection_scope':selection,'conversation_id':'stage1-G09','context_task_id':'G09-T2'}
        with patch.object(analysis_context,'get_task',lambda path,tid:deepcopy(h.tasks[tid])):
            return analysis_context.load_analysis_context(None,parameters)

    def test_g09_product_selection_conflict_clears_pending_and_ranking(self):
        state=self.load_post(product_ids=[B])['analysis_state']
        self.assertIsNone(state['pending_goal']);self.assertIsNone(state['ranking_basis'])

    def test_g09_identity_change_does_not_restore_pending(self):
        self.assertIsNone(self.load_post(model='other-profile'))

    def test_g09_new_date_after_pending_requires_new_goal_not_scope_rewrite(self):
        state=self.load_post(date_from='2023-02-20')['analysis_state']
        self.assertIsNone(state['pending_goal']);self.assertIsNone(state['ranking_basis'])

    def test_g09_followup_consumes_new_pending_without_reusing_old_sample(self):
        h=Harness();group=deepcopy(next(g for g in h.oracle['groups'] if g['id']=='G09'))
        follow=deepcopy(group['turns'][-1]);follow.update(input='客服方面呢？',selection_changes=None)
        group['turns'].append(follow);h.oracle['groups']=[group]
        with redirect_stdout(io.StringIO()):t=h.run()[-1]
        r=t['task']['result'];self.assertTrue(r['task_acceptance']['complete'])
        self.assertEqual((r['scope']['product_ids'],r['scope']['date_from']),([A],'2023-02-15'))
        self.assertIsNone(r['analysis_state']['ranking_basis'])
        data=next(v['data'] for v in r['results'].values() if v['kind']=='aspect_drilldown')
        self.assertEqual((data['aspect'],data['total']),('service',3))
        self.assertTrue(all(item['published_at'][:10]>='2023-02-15' for item in data['items']))

    def test_pending_cannot_cross_conversation(self):
        t=self.turns['G09-T2']['task'];p={**self.h.oracle['scope'],'selection_scope':self.h.oracle['scope'],
            'conversation_id':'other-session','context_task_id':t['task_id']}
        with patch.object(analysis_context,'get_task',return_value=deepcopy(t)):
            self.assertIsNone(analysis_context.load_analysis_context(None,p))

    def test_pending_tamper_is_not_recovered(self):
        t=deepcopy(self.turns['G09-T2']['task']);t['result']['scope']['product_ids']=[B]
        self.assertIsNone(restore_pending_goal(t))

    def test_g25_explicit_statistics_after_rag_executes_population_not_retrieval(self):
        t=self.turns['G25-T2'];r=t['task']['result']
        self.assertEqual(r['request_goal']['levels'],['statistics'])
        self.assertEqual(r['query_plan']['required_tools'],['get_top_issues'])
        self.assertEqual(r['scope']['product_ids'],[B]);self.assertTrue(r['task_acceptance']['complete'])
        self.assertFalse(any(e['node']=='rag_double' for e in t['events']))
        data=next(v['data'] for v in r['results'].values() if v['kind']=='issues')
        self.assertEqual((data['current']['successful_reviews'],data['current']['aspects']['logistics']['negative_review_count']),(8,4))
        self.assertEqual(t['simulated_model_attempts'],[])

    def test_rag_explicit_new_statistics_does_not_require_a_goal_hint(self):
        with patch('data_analysis_agent.agents.review_acceptance.build_request_goal',side_effect=AssertionError('independent router')):
            _,plan,intent=self.resolve('只统计物流差评数量。',self.context('semantic_evidence',None,None))
        self.assertEqual((intent['query_type'],intent['aspect'],intent['sentiment']),('aspect_summary','logistics','negative'))
        self.assertEqual(plan.required_tools,['get_top_issues'])

    def test_rag_ambiguous_short_count_and_aspect_remain_clarifications(self):
        for q in ('有多少条？','客服呢？'):
            with self.subTest(q=q),self.assertRaises(QuestionScopeError):self.resolve(q,self.context('semantic_evidence',None,None))

    def test_g25_t1_permission_failure_remains_no_published_claim(self):
        # 保留历史 ID；它曾固定记录已知缺陷，正式 Oracle v2 始终要求正确的有限命题许可。
        from data_analysis_agent.agents.review_retrieval import validate_retrieval
        r=self.result('G25-T1')
        data=next(v['data'] for v in r['results'].values() if v['kind']=='semantic_evidence')
        self.assertEqual(data['retrieval']['query']['question'],r['question'])
        self.assertEqual(data['assessment_question'],'有没有物流很慢的反馈？')
        self.assertEqual(data['assessment']['required_fact_ids'],['delivery_slow'])
        self.assertTrue(validate_retrieval(data,r['question']))
        keys=data['assessment']['permitted_keys'];self.assertEqual(len(keys),1)
        allowed=[row for row in data['retrieval']['evidence'] if row['key'] in keys]
        self.assertEqual([row['review_id'] for row in allowed],[101])
        self.assertEqual(allowed[0]['review_text'][allowed[0]['quote_start']:allowed[0]['quote_end']],'物流很慢')
        self.assertEqual(len(r['public_answer']['evidence_refs']),1)
        self.assertEqual(r['public_answer']['evidence_refs'][0]['review_id'],101)

    def test_g26_goal_inherits_unique_target_but_not_evidence_level(self):
        goal=build_request_goal('有多少条？',self.context('evidence'))
        self.assertEqual((goal.levels,goal.aspect,goal.sentiment),(('statistics',),'logistics','negative'))

    def test_g26_actual_statistic_is_population_count_not_three_examples(self):
        r=self.result('G26-T2');self.assertTrue(r['task_acceptance']['complete'])
        self.assertEqual(r['query_plan']['required_tools'],['get_top_issues'])
        params=r['query_plan']['requirements'][0]['parameters']
        self.assertNotIn('page_size',params);self.assertNotIn('top_n',params)
        data=next(v['data'] for v in r['results'].values() if v['kind']=='issues')
        self.assertEqual(data['current']['successful_reviews'],8)
        self.assertEqual(data['current']['aspects']['logistics']['negative_review_count'],3)
        self.assertEqual(len(data['issues']),6)
        self.assertFalse(any(e['node']=='tool_start' and e['name']=='get_evidence' for e in self.turns['G26-T2']['events']))

    def test_population_larger_than_three_is_not_limited_by_old_page_size(self):
        h=Harness();group=deepcopy(next(g for g in h.oracle['groups'] if g['id']=='G26'))
        group['turns'][0]['input']=group['turns'][0]['input'].replace(A,B)
        h.oracle['groups']=[group]
        with redirect_stdout(io.StringIO()):turns=h.run()
        first,last=[t['task']['result'] for t in turns]
        examples=next(v['data'] for v in first['results'].values() if v['kind']=='evidence')
        self.assertEqual(len(examples['items']),3);self.assertEqual(examples['total'],4)
        stats=next(v['data'] for v in last['results'].values() if v['kind']=='issues')
        self.assertEqual(stats['current']['aspects']['logistics']['negative_review_count'],4)
        self.assertEqual(last['scope']['product_ids'],[B]);self.assertTrue(last['task_acceptance']['complete'])

    def test_count_with_multiple_dimensions_or_sentiments_must_clarify(self):
        for aspect,sentiment in [('service','negative'),('logistics','positive')]:
            ctx=self.context('evidence');ctx['validated']['queries'].append({'kind':'evidence','parameters':{'aspect':aspect,'sentiment':sentiment}})
            self.assertIsNone(build_request_goal('有多少条？',ctx).aspect)
            with self.subTest(aspect=aspect,sentiment=sentiment),self.assertRaises(QuestionScopeError):self.resolve('有多少条？',ctx)

    def test_count_with_unknown_direction_must_clarify(self):
        with self.assertRaises(QuestionScopeError):self.resolve('有多少条？',self.context('evidence','logistics',None))


if __name__=='__main__':unittest.main()
