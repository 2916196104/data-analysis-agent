"""P8-B mixed subgoal boundaries, using only an in-memory scope."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import patch

from test_review_unknown_product_id import SCOPE
from test_agent import SETTINGS, tools, final
import test_review_agent as review_agent_fixture
import test_review_followup_p7b as followup_fixture
from data_analysis_agent.agents.review_acceptance import build_task_goal, validate_goal_plan, validate_task_completion, build_request_goal
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_validation import validate_report, expected_result_scopes
from data_analysis_agent.agents.reviews import run_review_agent, create_llm
from data_analysis_agent.agents.review_multi_aspect import P6RequestGoal
from data_analysis_agent.core.serialization import canonical_json, content_sha256


class MixedGoalsP8BTests(unittest.TestCase):
    def test_global_trend_packaging_evidence_and_global_summary(self):
        question = '查看整体趋势、包装原文、六维度主要问题。'
        scope = deepcopy(SCOPE)
        plan = build_query_plan(question, scope)
        goal = build_task_goal(question, scope, plan, {})
        self.assertTrue(validate_goal_plan(goal, plan)['passed'])
        self.assertEqual([(g['family'], g['aspect']) for g in goal['subgoals']],
                         [('trend', None), ('evidence', 'packaging'), ('summary', None)])
        self.assertTrue(all(g['scope'] == scope for g in goal['subgoals']))

    def test_distinct_aspects_are_preserved_per_clause(self):
        question = '包装趋势、物流原文、六维统计'
        scope = deepcopy(SCOPE)
        plan = build_query_plan(question, scope)
        goal = build_task_goal(question, scope, plan, {})
        self.assertTrue(validate_goal_plan(goal, plan)['passed'])
        self.assertEqual([(g['family'], g['aspect']) for g in goal['subgoals']],
                         [('trend', 'packaging'), ('evidence', 'logistics'), ('summary', None)])
        self.assertEqual({r['tool'] for r in plan.requirements},
                         {'get_aspect_trend', 'get_evidence', 'get_top_issues'})
        evidence = next(r for r in plan.requirements if r['tool'] == 'get_evidence')
        self.assertEqual(evidence['parameters']['aspect'], 'logistics')

    def test_reordering_and_separators(self):
        for q in ('六维统计，物流原文；包装趋势', '物流原文、六维统计、包装趋势'):
            with self.subTest(q=q):
                plan = build_query_plan(q, deepcopy(SCOPE))
                goal = build_task_goal(q, deepcopy(SCOPE), plan, {})
                self.assertTrue(validate_goal_plan(goal, plan)['passed'])
                self.assertEqual({g['family']: g['aspect'] for g in goal['subgoals']},
                                 {'trend':'packaging','evidence':'logistics','summary':None})

    def test_ambiguous_clause_is_rejected(self):
        for q in ('趋势、包装物流原文、六维统计', '整体趋势、原文、六维统计',
                  '整体趋势只看最近一年、包装原文、六维统计', '整体趋势、包装原文仅第3页、六维统计'):
            with self.subTest(q=q):
                self.assertTrue(build_query_plan(q, deepcopy(SCOPE)).unsupported_reason)

    def test_optional_statistic_targets_preserve_old_goal_defaults_and_reject_unknown_version(self):
        self.assertEqual(set(asdict(build_request_goal('那好评呢？'))),
                         {'original_question','levels','aspect','sentiment','topic','statistic_families','source', 'denominator', 'aspects'})
        from data_analysis_agent.agents.review_acceptance import RequestGoal
        old = asdict(build_request_goal('那好评呢？'))
        old.pop('denominator'); old.pop('aspects')
        restored = RequestGoal(**old)
        self.assertIsNone(restored.denominator)
        self.assertEqual(restored.aspects, ())
        q='整体趋势、包装原文、六维统计'; plan=build_query_plan(q, deepcopy(SCOPE))
        goal=build_task_goal(q,deepcopy(SCOPE),plan,{})
        goal['version']='review-mixed-goal-v99'
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])

    def test_p6_old_goal_round_trip(self):
        basis={'ranking_basis': {'source': {'task_id':'synthetic'},
                                'scope_sha256':'synthetic', 'scope':deepcopy(SCOPE)}}
        value=P6RequestGoal('具体体现在哪里？',('themes',),None,'negative',
            required_aspects=('service',),selection_basis_json=canonical_json(basis),
            selection_basis_sha256=content_sha256(basis))
        serialized=value.to_dict()
        self.assertEqual(P6RequestGoal.from_dict(serialized),value)
        self.assertNotIn('subgoals',serialized)

    def test_task_goal_json_round_trips_for_old_and_mixed_contracts(self):
        for q in ('差评主要集中在哪些方面？', '整体趋势、包装原文、六维统计'):
            with self.subTest(question=q):
                plan=build_query_plan(q,deepcopy(SCOPE))
                goal=build_task_goal(q,deepcopy(SCOPE),plan,{})
                restored=json.loads(json.dumps(goal,ensure_ascii=False))
                self.assertTrue(validate_goal_plan(restored,plan)['passed'])
                self.assertEqual(restored['expected_scope'],goal['expected_scope'])

    def test_missing_or_scope_expanding_plan_is_rejected(self):
        q='整体趋势、包装原文、六维统计'; plan=build_query_plan(q,deepcopy(SCOPE))
        goal=build_task_goal(q,deepcopy(SCOPE),plan,{})
        plan.requirements.pop()
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])
        plan=build_query_plan(q,deepcopy(SCOPE))
        plan.requirements[0]['parameters']['product_ids']=['OTHER']
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])
        plan=build_query_plan(q,deepcopy(SCOPE))
        plan.optional_tools=['get_data_quality']
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])
        goal.pop('subgoals');goal['version']='review-request-goal-v1'
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])

    def test_cannot_remove_evidence_level_from_mixed_goal(self):
        q='整体趋势、包装原文、六维统计'
        plan=build_query_plan(q,deepcopy(SCOPE))
        goal=build_task_goal(q,deepcopy(SCOPE),plan,{})
        goal['levels']=['statistics']
        self.assertFalse(validate_goal_plan(goal,plan)['passed'])


class MixedIntegrationP8BTests(unittest.TestCase):
    def setUp(self):
        self.fixture=followup_fixture.FollowupP7BTests(methodName='test_normal_followup_native_worker_inherits_trusted_scope_ranking_and_goal')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_mixed(self, question, calls):
        from data_analysis_agent.reviews.parameters import BaselineScope
        scope={k:self.fixture.scope[k] for k in BaselineScope.model_fields if k in self.fixture.scope}
        messages=[tools(*calls),final('ok','r1','r2','r3')]
        requests=[]
        with patch('data_analysis_agent.agents.reviews.create_llm',new=create_llm), \
             review_agent_fixture.ReviewAgentTests.mock_model(self,messages,requests), \
             patch('httpx.HTTPTransport.handle_request',side_effect=AssertionError('network forbidden')):
            result=run_review_agent(self.fixture.db,question,scope=scope,
                                    settings=replace(SETTINGS,semantic_parser_enabled=False))
        return result

    def test_real_sql_tools_validator_acceptance_and_tampering(self):
        question='查看整体趋势、包装原文、六维度主要问题。'
        result=self.run_mixed(question,[('get_trend',{'period':'month'}),
            ('get_evidence',{'sentiment':'negative','aspect':'packaging','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        self.assertEqual(result['status'],'ok',result.get('error'))
        self.assertEqual(result['validation']['status'],'passed')
        self.assertEqual(result['task_acceptance']['status'],'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        rows=result['results']; ids=result['result_ids']
        self.assertEqual({rows[k]['kind'] for k in ids},{'trend','issues','evidence'})
        self.assertGreater(next(rows[k]['data']['total'] for k in ids if rows[k]['kind']=='evidence'),0)
        plan=build_query_plan(question,result['scope'])
        tampered=deepcopy(rows)
        evidence=next(v for v in tampered.values() if v['kind']=='evidence')
        evidence['data']['items'][0]['opinions'][0]['quote']='篡改观点'
        checked=validate_report(tampered,ids,sentiment_intent=plan.sentiment,query_plan=plan,
            expected_scopes=expected_result_scopes(tampered,result['scope']))
        self.assertEqual(checked['status'],'failed')
        selected=[rows[k] for k in ids[:-1]]
        self.assertFalse(validate_task_completion(result['task_goal'],result['scope'],plan,selected)['complete'])
        trend_id=next(k for k in ids if rows[k]['kind']=='trend')
        evidence_id=next(k for k in ids if rows[k]['kind']=='evidence')
        issues_id=next(k for k in ids if rows[k]['kind']=='issues')
        cases=[((trend_id,'data','summary','negative_rate'),0.99),
               ((trend_id,'data','summary','labeling_coverage'),0.123),
               ((trend_id,'data','summary','successful_reviews'),900),
               ((evidence_id,'data','items',0,'opinions',0,'quote'),'虚构引文'),
               ((evidence_id,'data','items',0,'evidence_ref'),'sha256:fake#line=1'),
               ((issues_id,'data','issues',0,'negative_rate_change_pp'),0)]
        for path,value in cases:
            with self.subTest(path=path):
                corrupt=deepcopy(rows); target=corrupt
                for key in path[:-1]:target=target[key]
                target[path[-1]]=value
                self.assertEqual(validate_report(corrupt,ids)['status'],'failed')
        self.assertEqual(validate_report(rows,ids[:1])['status'],'failed')
        for field,value in [('product_ids',['OTHER']),('source','other'),('date_from','2020-01-01')]:
            with self.subTest(scope_field=field):
                corrupt=deepcopy(rows)
                corrupt[trend_id]['data']['scope'][field]=value
                selected=[corrupt[k] for k in ids]
                self.assertFalse(validate_task_completion(result['task_goal'],result['scope'],plan,selected)['complete'])

    def test_distinct_aspects_full_execution(self):
        q='包装趋势、物流原文、六维统计'
        result=self.run_mixed(q,[('get_aspect_trend',{'period':'month','aspect':'packaging'}),
            ('get_evidence',{'sentiment':'negative','aspect':'logistics','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        self.assertEqual(result['status'],'ok',result.get('error'))
        self.assertEqual(result['validation']['status'],'passed')
        self.assertTrue(result['task_acceptance']['complete'])
        rows=[result['results'][k] for k in result['result_ids']]
        self.assertEqual(next(r for r in rows if r['kind']=='aspect_trend')['parameters']['aspect'],'packaging')
        self.assertEqual(next(r for r in rows if r['kind']=='evidence')['parameters']['aspect'],'logistics')
        self.assertTrue(all(r['data']['scope']['product_ids']==['100013588055'] for r in rows))

    def test_unknown_product_stops_before_tools(self):
        q='商品 XYZ-ABC123 的整体趋势、包装原文、六维统计'
        result=self.run_mixed(q,[])
        self.assertEqual(result['status'],'unsupported')
        self.assertEqual(result['query_executions'],0)

    def test_tool_failure_does_not_complete(self):
        q='包装趋势、物流原文、六维统计'
        with patch('data_analysis_agent.agents.review_tools.analytics.get_aspect_trend',side_effect=RuntimeError('synthetic')):
            result=self.run_mixed(q,[('get_aspect_trend',{'period':'month','aspect':'packaging'}),
                ('get_evidence',{'sentiment':'negative','aspect':'logistics','page':1,'page_size':3,'window':'current'}),
                ('get_top_issues',{})])
        self.assertNotEqual(result['status'],'ok')
        self.assertFalse(result.get('task_acceptance',{}).get('complete',False))

    def test_no_matching_evidence_is_not_complete(self):
        q='整体趋势、价格原文、六维统计'
        result=self.run_mixed(q,[('get_trend',{'period':'month'}),
            ('get_evidence',{'sentiment':'negative','aspect':'price','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        self.assertNotEqual(result['status'],'error',result.get('error'))
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertEqual(result['task_acceptance']['status'],'insufficient_evidence')

    def test_empty_evidence_cannot_be_hidden_by_goal_level_tampering(self):
        q='整体趋势、价格原文、六维统计'
        result=self.run_mixed(q,[('get_trend',{'period':'month'}),
            ('get_evidence',{'sentiment':'negative','aspect':'price','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        goal=deepcopy(result['task_goal']);goal['levels']=['statistics']
        plan=build_query_plan(q,result['scope'])
        rows=[row for key,row in result['results'].items() if key!='overview']
        self.assertFalse(validate_task_completion(goal,result['scope'],plan,rows)['complete'])

    def test_unplanned_extra_tool_result_cannot_complete(self):
        q='查看整体趋势、包装原文、六维度主要问题。'
        result=self.run_mixed(q,[('get_trend',{'period':'month'}),
            ('get_evidence',{'sentiment':'negative','aspect':'packaging','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        self.assertTrue(result['task_acceptance']['complete'])
        plan=build_query_plan(q,result['scope'])
        rows=[deepcopy(result['results'][key]) for key in result['result_ids']]
        extra=deepcopy(next(row for row in rows if row['kind']=='trend'))
        extra['result_id']='unexpected-extra'
        extra['parameters']['period']='day'
        self.assertFalse(validate_task_completion(result['task_goal'],result['scope'],plan,rows+[extra])['complete'])

    def test_malformed_mixed_subgoal_is_rejected_without_crashing(self):
        q='查看整体趋势、包装原文、六维度主要问题。'
        result=self.run_mixed(q,[('get_trend',{'period':'month'}),
            ('get_evidence',{'sentiment':'negative','aspect':'packaging','page':1,'page_size':3,'window':'current'}),
            ('get_top_issues',{})])
        goal=deepcopy(result['task_goal']);goal['subgoals']=[{}]
        plan=build_query_plan(q,result['scope'])
        rows=[result['results'][key] for key in result['result_ids']]
        self.assertFalse(validate_task_completion(goal,result['scope'],plan,rows)['complete'])


if __name__ == '__main__':
    unittest.main()
