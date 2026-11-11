"""真实 P6-B 冻结输入、固定供应商响应和假时钟；不访问收费服务。"""
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

import test_p1a_independent_validation as independent
from data_analysis_agent.agents.review_multi_aspect import freeze_p6_goal
from data_analysis_agent.agents.review_plan import build_p6_query_plan
from data_analysis_agent.agents.review_preparation import prepare_p6_analysis
from data_analysis_agent.reviews.store import connect_review_database
from data_analysis_agent.agents.review_budget import P6SharedBudget, P6CostAuthorization, P6OfflineTransport, P6BudgetBlocked
from data_analysis_agent.agents.review_execution import execute_p6_prepared
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.agents import review_topics

ORIGINAL_TOPIC_CALL = review_topics._call


class FakeClock:
    def __init__(self): self.value = 0.0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds


class P6CBudgetExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = independent.IndependentStateValidation()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # 独立来源夹具默认禁止主题入口；C 测试恢复真实 wire/预算路径，只替换供应商响应。
        self.enterContext(patch.object(review_topics, '_call', ORIGINAL_TOPIC_CALL))
        self.settings = replace(independent.SETTINGS, p6_execution_enabled=True)
        self.clock = FakeClock()
        self.calls = []
        for target in ('data_analysis_agent.agents.review_intent._llm_intent',
                       'data_analysis_agent.agents.reviews.create_llm',
                       'data_analysis_agent.agents.review_retrieval.create_semantic_service'):
            blocked=self.enterContext(patch(target,side_effect=AssertionError('P6-C deterministic path forbids extra model')))
            self.addCleanup(blocked.assert_not_called)
        self.prepare()

    def prepare(self, count=3):
        if count < 3:
            keep=('service','effectiveness','logistics')[:count]
            with closing(connect_review_database(self.fixture.db)) as writer, writer:
                writer.execute('DELETE FROM review_opinions WHERE aspect NOT IN ('+','.join('?' for _ in keep)+')',keep)
            self.fixture.first=self.fixture.work(independent.A+'的差评主要集中在哪些方面？')
        self.auth = {'conversation_id':'independent-session', 'agent_identity':self.fixture.agent_identity,
                     'ranking_task':self.fixture.first, 'context':self.fixture.load()}
        goal = freeze_p6_goal('具体体现在哪里？', scope=self.fixture.first['result']['scope'], **self.auth).goal
        with closing(connect_review_database(self.fixture.db, readonly=True)) as connection, connection:
            connection.execute('BEGIN')
            self.prepared = prepare_p6_analysis(connection, goal, build_p6_query_plan(goal), **self.auth)

    def policy(self, **changes):
        return replace(P6CostAuthorization(authorized=True,model=self.settings.model,base_url=self.settings.base_url,
            currency='TEST',amount_limit=Decimal('100'),input_price=Decimal('0.0001'),output_price=Decimal('0.0001'),
            billing_categories=('input','output'),token_upper_bound_id='synthetic-bound-v1',token_upper_bound=lambda p,b:1000),**changes)

    def response(self, request, *, usage=True, empty=False):
        self.calls.append(request)
        body=json.loads(request['messages'][1]['content'])
        if request['stage']=='topic_discovery':
            content={'themes':[] if empty else [{'n':body['opinions'][0]['quote'][:24],
                'd':body['opinions'][0]['quote'],'q':[body['opinions'][0]['opinion_id']]}]}
        else:
            content={'rows':[{'i':r['opinion_id'],'s':'n' if empty else 'a',
                'm':[] if empty else [['t1',{'quote_id':r['opinion_id']}]]} for r in body['opinions']]}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(
            tool_calls=None,content=json.dumps(content,ensure_ascii=False)))],
            usage={'prompt_tokens':50,'completion_tokens':20} if usage else None)

    def budget(self, respond=None, policy=None):
        return P6SharedBudget(policy or self.policy(),offline_transport=P6OfflineTransport(respond or self.response),clock=self.clock)

    def execute(self, budget=None, **changes):
        return execute_p6_prepared(changes.pop('prepared',self.prepared),settings=changes.pop('settings',self.settings),
            budget=budget or self.budget(),**{**self.auth,**changes})

    def test_b_preparation_remains_incomplete_without_execution(self):
        self.assertEqual(self.prepared.diagnostics()['topic_model_calls'], 0)
        self.assertEqual(self.prepared.diagnostics()['status'], 'ready')

    def test_shared_budget_exists_and_default_authorization_blocks_calls(self):
        from data_analysis_agent.agents.review_budget import P6SharedBudget
        budget = P6SharedBudget()
        self.assertEqual(budget.snapshot()['attempts'], 0)

    def test_internal_executor_consumes_prepared_input(self):
        from data_analysis_agent.agents.review_execution import execute_p6_prepared
        from data_analysis_agent.agents.review_budget import P6SharedBudget
        result = execute_p6_prepared(self.prepared, settings=replace(independent.SETTINGS, p6_execution_enabled=True),
            budget=P6SharedBudget(), **self.auth)
        self.assertEqual(result.acceptance['status'], 'skipped_budget')

    def test_one_two_three_aspects_execute_in_frozen_order_with_six_attempt_ceiling(self):
        for count in (3,2,1):
            with self.subTest(count=count):
                if count < 3:self.prepare(count)
                self.calls.clear()
                result=self.execute()
                self.assertEqual(result.acceptance['status'],'complete',result.diagnostics())
                self.assertTrue(result.acceptance['complete'])
                self.assertEqual(len(self.calls),count*2)
                self.assertEqual([r['aspect'] for r in self.calls], [a for a in self.prepared.goal.required_aspects for _ in range(2)])
                self.assertTrue(all(r['max_retries']==0 and 0<r['timeout']<=20 for r in self.calls))
                self.assertEqual([r['max_tokens'] for r in self.calls],[1200,3000]*count)
                self.assertEqual(result.diagnostics()['budget']['attempts'],count*2)
                self.assertEqual(len(result.validated_results()),count)

    def test_cost_pre_reservation_blocks_later_calls_without_shrinking_target(self):
        budget=self.budget(policy=self.policy(amount_limit=Decimal('0.7')))
        result=self.execute(budget)
        self.assertEqual(result.acceptance['status'],'partial')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(result.acceptance['required_aspects'],['service','effectiveness','logistics'])
        self.assertEqual([r['status'] for r in result.acceptance['per_aspect']],['complete','skipped_budget','skipped_budget'])
        self.assertEqual(Decimal(result.diagnostics()['budget']['reserved_cost']),Decimal('0.62'))

    def test_unknown_authorization_prices_bounds_and_server_flag_fail_before_transport(self):
        for changes in ({'authorized':False},{'input_price':None},{'output_price':None},{'amount_limit':None},
                {'billing_categories':None},{'token_upper_bound':None},{'token_upper_bound_id':None},
                {'model':'wrong'},{'input_price':Decimal('NaN')}):
            with self.subTest(changes=changes):
                result=self.execute(self.budget(policy=self.policy(**changes)))
                self.assertEqual(result.acceptance['status'],'skipped_budget')
                self.assertEqual(result.diagnostics()['budget']['attempts'],0)
        result=self.execute(settings=replace(self.settings,p6_execution_enabled=False))
        self.assertEqual(result.acceptance['status'],'skipped_budget')
        self.assertEqual(self.calls,[])

    def test_usage_missing_and_provider_failures_never_release_reservation(self):
        budget=self.budget(respond=lambda r:self.response(r,usage=False))
        result=self.execute(budget)
        state=result.diagnostics()['budget']
        self.assertEqual(result.acceptance['status'],'complete')
        self.assertEqual(Decimal(state['reserved_cost']),Decimal('1.86'))
        self.assertIsNone(state['actual_cost_total'])
        self.assertFalse(state['actual_cost_complete'])
        self.assertTrue(all(r['actual_cost'] is None and r['usage_status']=='missing' for r in state['records']))

    def test_timeout_invalid_json_truncation_and_provider_error_do_not_retry(self):
        for error in ('timeout','json','truncated','provider'):
            self.calls.clear()
            def respond(request):
                if request['aspect']=='effectiveness':
                    if error=='timeout': self.calls.append(request);raise TimeoutError('offline')
                    if error=='provider': self.calls.append(request);raise RuntimeError('offline')
                    result=self.response(request)
                    if error=='json':result.choices[0].message.content='invalid JSON'
                    else:result.choices[0].finish_reason='length'
                    return result
                return self.response(request)
            with self.subTest(error=error):
                result=self.execute(self.budget(respond))
                self.assertEqual(result.acceptance['status'],'partial')
                self.assertEqual([r['status'] for r in result.acceptance['per_aspect']],['complete','failed','complete'])
                self.assertEqual(sum(r['aspect']=='effectiveness' for r in self.calls),1)
                self.assertEqual(result.diagnostics()['budget']['attempts'],5)

    def test_authentication_and_quota_failures_halt_remaining_provider_attempts(self):
        import httpx
        from openai import APIStatusError
        for code in (401,402,403):
            self.calls.clear()
            def respond(request):
                self.calls.append(request)
                response=httpx.Response(code,request=httpx.Request('POST','https://offline.invalid'))
                raise APIStatusError('offline',response=response,body={})
            with self.subTest(code=code):
                result=self.execute(self.budget(respond))
                self.assertEqual(len(self.calls),1)
                self.assertEqual([r['status'] for r in result.acceptance['per_aspect']],['failed','skipped_budget','skipped_budget'])
                self.assertEqual(result.diagnostics()['budget']['halted_reason'],f'provider_{code}')
                self.assertEqual(Decimal(result.diagnostics()['budget']['reserved_cost']),Decimal('0.22'))

    def test_request_and_whole_deadlines_reject_late_results_and_new_calls(self):
        for seconds, expected_attempts in ((21,5),(121,1)):
            self.calls.clear();self.clock=FakeClock()
            def respond(request):
                result=self.response(request)
                if request['aspect']=='service':self.clock.advance(seconds)
                return result
            with self.subTest(seconds=seconds):
                result=self.execute(self.budget(respond))
                self.assertFalse(result.acceptance['complete'])
                self.assertNotIn('service',result.acceptance['publishable_aspects'])
                self.assertEqual(len(self.calls),expected_attempts)

    def test_all_insufficient_evidence_is_distinct_from_all_budget_skipped(self):
        result=self.execute(self.budget(lambda r:self.response(r,empty=True)))
        self.assertEqual(result.acceptance['status'],'insufficient_evidence')
        self.assertEqual(result.acceptance['agent_status'],'unsupported')
        self.assertEqual(result.validated_results(),[])
        blocked=self.execute(self.budget(policy=self.policy(amount_limit=Decimal('0'))))
        self.assertEqual(blocked.acceptance['status'],'skipped_budget')
        self.assertEqual(blocked.acceptance['required_aspects'],result.acceptance['required_aspects'])

    def test_attempt_limits_restart_order_and_reentrancy_reject_before_provider(self):
        holder={}
        def respond(request):
            with self.assertRaises(P6BudgetBlocked):
                holder['budget'].request(self.settings,aspect=request['aspect'],stage=request['stage'],prompt='x',body='{}',
                    max_tokens=request['max_tokens'],provider=lambda timeout:self.fail('reentrant provider'))
            return self.response(request)
        budget=self.budget(respond);holder['budget']=budget
        with self.assertRaises(AttributeError):budget.authorization=self.policy(amount_limit=Decimal('999'))
        with self.assertRaises(AttributeError):budget.offline_transport=P6OfflineTransport(self.response)
        with self.assertRaises(AttributeError):budget.halted_reason=None
        self.assertTrue(self.execute(budget).acceptance['complete'])
        with self.assertRaises(P6BudgetBlocked):
            budget.request(self.settings,aspect='logistics',stage='topic_classification',prompt='x',body='{}',
                max_tokens=3000,provider=lambda timeout:self.fail('extra provider attempt'))
        with self.assertRaises(P6BudgetBlocked):budget.begin_aspect('service')
        with self.assertRaises(P6BudgetBlocked):budget.start(self.prepared.goal.required_aspects,lambda:None,input_hashes={})
        with self.assertRaises(P6BudgetBlocked):P6SharedBudget().start(('a','b','c','d'),lambda:None,input_hashes={})
        self.assertEqual(len(self.calls),6)

    def test_usage_bound_breaches_and_unknown_billing_halt_later_requests(self):
        for field,value in [('prompt_tokens',1001),('completion_tokens',1201),('billable_extra_tokens',1),
                            ('billable_categories',['input','output','unknown']),('missing_tokens_with_extra_billing',1)]:
            self.calls.clear()
            def respond(request):
                result=self.response(request)
                if field=='missing_tokens_with_extra_billing':result.usage={'billable_extra_tokens':1}
                else:result.usage[field]=value
                return result
            with self.subTest(field=field):
                result=self.execute(self.budget(respond))
                self.assertEqual(len(self.calls),1)
                self.assertFalse(result.acceptance['complete'])
                self.assertEqual(result.acceptance['publishable_aspects'],[])
                self.assertEqual(result.diagnostics()['budget']['halted_reason'],'usage_exceeded_authorized_bound')

    def test_frozen_corruption_json_plan_scope_and_identity_fail_globally_before_calls(self):
        corrupt=replace(self.prepared,payload_json='{}')
        wrong_plan=build_p6_query_plan(self.prepared.goal)
        wrong_plan.requirements[2]['parameters']['aspect']='quality'
        for changes in ({'prepared':corrupt},{'prepared':self.prepared.diagnostics()},{'plan':wrong_plan},
                {'scope':{**self.prepared.goal.expected_scope,'date_from':'2023-02-02'}},
                {'agent_identity':{**self.auth['agent_identity'],'model':'wrong'}}):
            budget=self.budget()
            with self.subTest(changes=list(changes)),self.assertRaises(ReviewDataError):self.execute(budget,**changes)
            self.assertEqual(budget.snapshot()['attempts'],0)
        self.assertEqual(self.calls,[])

    def test_database_changes_during_classification_cannot_change_frozen_inputs(self):
        from data_analysis_agent.reviews import analytics
        with closing(connect_review_database(self.fixture.db)) as writer:
            changed=[]
            def respond(request):
                if not changed:
                    with writer:writer.execute("UPDATE review_opinions SET sentiment='positive'")
                    changed.append(True)
                return self.response(request)
            with patch.object(analytics,'get_aspect_opinions',side_effect=AssertionError('classification SQL forbidden')), \
                 patch.object(analytics,'get_top_issues',side_effect=AssertionError('classification SQL forbidden')), \
                 patch('data_analysis_agent.reviews.analytics.connect_review_database',side_effect=AssertionError('no reconnect')):
                result=self.execute(self.budget(respond))
        self.assertTrue(result.acceptance['complete'])
        service=result.validated_results()[0]['data']
        self.assertEqual(service['topics'][0]['review_count'],5)
        self.assertEqual(service['topics'][0]['review_denominator'],5)
        self.assertEqual(service['summary']['successful_reviews'],6)
        self.assertEqual(service['topics'][0]['semantic_status'],'model_reviewed_not_human_confirmed')

    def test_mid_execution_source_corruption_never_returns_partial_success(self):
        def respond(request):
            response=self.response(request)
            self.auth['ranking_task']['result']['answer']='mutated source'
            return response
        budget=self.budget(respond)
        with self.assertRaises(ReviewDataError):self.execute(budget)
        self.assertEqual(len(self.calls),1)

    def test_classifier_source_or_dimension_mutation_is_a_global_failure(self):
        import data_analysis_agent.agents.review_execution as execution
        original=execution.analyze_aspect_topics
        def changed(*args,**kwargs):
            data=original(*args,**kwargs)
            if data['aspect']=='effectiveness':data['scope']['model']='forged'
            return data
        with patch.object(execution,'analyze_aspect_topics',side_effect=changed),self.assertRaises(ReviewDataError):self.execute()
        self.assertEqual(len(self.calls),4)

    def test_wire_body_limit_blocks_before_any_oversized_provider_request(self):
        rows=[{'parent_asin':independent.A,'user_id':f'long{i}','text':'慢'*398+f'{i:02d}',
               'timestamp':1676073600000} for i in range(20)]
        self.add_rows(rows)
        result=self.execute()
        self.assertEqual(result.acceptance['per_aspect'][0]['status'],'skipped_budget')
        self.assertFalse(any(r['aspect']=='service' for r in self.calls))
        self.assertTrue(all(len(r['messages'][1]['content'].encode('utf-8'))<=16000 for r in self.calls))

    def add_rows(self, rows, opinions=None):
        path=self.fixture.root/'c-extra.jsonl'
        path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows),encoding='utf-8')
        independent.import_amazon_jsonl(self.fixture.db,reviews_path=path,dataset_version='independent-state')
        for review in independent.pending_reviews(self.fixture.db,**self.fixture.identity,dataset_version='independent-state',limit=100):
            values=opinions or [dict(aspect='service',sentiment='negative',quote=review['text'])]
            independent.save_annotation(self.fixture.db,review,**self.fixture.identity,status='success',
                opinions=independent.validate_opinions(json.dumps({'opinions':values}),review['text']))
        self.fixture.first=self.fixture.work(independent.A+'的差评主要集中在哪些方面？')
        self.prepare()

    def test_topic_counts_deduplicate_reviews_and_do_not_extrapolate_samples(self):
        self.add_rows([{'parent_asin':independent.A,'user_id':'two','text':'客服回复慢，售后等待久',
            'timestamp':1676073600000}], opinions=[dict(aspect='service',sentiment='negative',quote=q)
                for q in ('客服回复慢','售后等待久')])
        result=self.execute()
        service=result.validated_results()[0]['data']
        self.assertEqual((service['topics'][0]['review_count'],service['topics'][0]['opinion_count']),(6,7))
        self.add_rows([{'parent_asin':independent.A,'user_id':f'sample{i}','text':f'服务问题{i}',
            'timestamp':1676160000000} for i in range(25)])
        result=self.execute()
        service=result.validated_results()[0]['data']
        self.assertEqual(service['population_mode'],'sample')
        self.assertEqual(service['topics'][0]['review_count'],20)
        self.assertEqual(service['topics'][0]['review_denominator'],20)
        self.assertEqual(service['total'],31)

    def test_agent_defers_execution_until_connection_is_closed_and_hides_results_until_d(self):
        from data_analysis_agent.agents import review_preparation, review_execution, review_budget
        original=review_preparation.prepare_p6_analysis
        handles=[]
        def prepare(connection,*args,**kwargs):
            handles.append(connection);return original(connection,*args,**kwargs)
        def respond(request):
            with self.assertRaises(Exception):handles[0].execute('SELECT 1')
            return self.response(request)
        settings=replace(self.settings,p6_preparation_enabled=True)
        budget=self.budget(respond)
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity',return_value=(settings,self.fixture.agent_identity)), \
             patch.object(review_preparation,'prepare_p6_analysis',side_effect=prepare), \
             patch.object(review_budget,'make_execution_budget',return_value=budget):
            task=self.fixture.work('具体体现在哪里？',self.fixture.first['task_id'])
        result=task['result']
        self.assertEqual(task['status'],'succeeded',task.get('error'))
        self.assertEqual(result['p6_execution']['status'],'complete')
        self.assertEqual(result['total_model_calls'],6)
        self.assertEqual(result['status'],'unsupported')
        self.assertFalse(result['task_acceptance']['complete'])
        self.assertFalse(result['p6_execution']['public_release_enabled'])
        self.assertNotIn(independent.QUOTES['service'],json.dumps(result,ensure_ascii=False))

    def test_real_transport_stays_closed_without_explicit_real_authorization(self):
        budget=P6SharedBudget(self.policy(),clock=self.clock)
        result=self.execute(budget)
        self.assertEqual(result.acceptance['status'],'skipped_budget')
        self.assertEqual(budget.snapshot()['attempts'],0)
        self.assertEqual(self.calls,[])

    def test_classification_failures_at_first_middle_last_preserve_all_targets(self):
        for aspect in self.prepared.goal.required_aspects:
            self.calls.clear()
            def respond(request):
                if request['aspect']==aspect and request['stage']=='topic_classification':
                    self.calls.append(request);raise TimeoutError('offline classification timeout')
                return self.response(request)
            with self.subTest(aspect=aspect):
                result=self.execute(self.budget(respond))
                self.assertEqual(result.acceptance['status'],'partial')
                self.assertEqual(len(self.calls),6)
                self.assertEqual(result.acceptance['required_aspects'],list(self.prepared.goal.required_aspects))
                self.assertNotIn(aspect,result.acceptance['publishable_aspects'])

    def test_dimension_deadline_and_config_timeout_are_checked_before_next_call(self):
        def upper(prompt,body):
            if json.loads(body)['stage']=='topic_classification':self.clock.advance(3)
            return 1000
        def respond(request):
            value=self.response(request)
            self.clock.advance(19)
            return value
        result=self.execute(self.budget(respond,policy=self.policy(token_upper_bound=upper)))
        self.assertFalse(result.acceptance['complete'])
        records=result.diagnostics()['budget']['records']
        self.assertEqual([r['timeout_seconds'] for r in records[:2]],[20,18])
        self.assertNotIn('service',result.acceptance['publishable_aspects'])
        self.clock=FakeClock();self.calls.clear()
        result=self.execute(settings=replace(self.settings,timeout_seconds=7))
        self.assertTrue(result.acceptance['complete'])
        self.assertTrue(all(r['timeout']==7 for r in self.calls))

    def test_invalid_token_upper_bound_never_reaches_supplier(self):
        for value in (None,True,0,-1,1.5):
            with self.subTest(value=value):
                result=self.execute(self.budget(policy=self.policy(token_upper_bound=lambda p,b:value)))
                self.assertEqual(result.diagnostics()['budget']['attempts'],0)
                self.assertEqual(result.acceptance['status'],'skipped_budget')
        self.assertEqual(self.calls,[])

    def test_preparation_switch_does_not_grant_execution_or_cost_authorization(self):
        from data_analysis_agent.core.config import AgentSettings
        from data_analysis_agent.jobs.parameters import AnalysisParameters
        values={'LLM_MODEL_ID':'m','LLM_API_KEY':'offline','LLM_BASE_URL':'https://offline.invalid/v1',
                'AGENT_P6_PREPARATION_ENABLED':'1'}
        self.assertFalse(AgentSettings.from_values(values).p6_execution_enabled)
        self.assertTrue(AgentSettings.from_values({**values,'AGENT_P6_EXECUTION_ENABLED':'1'}).p6_execution_enabled)
        for key in ('p6_execution_enabled','cost_authorization','budget','prepared_analysis','offline_transport'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                AnalysisParameters.model_validate({**self.fixture.scope,'question':'具体体现在哪里？',key:{}})
        settings=replace(self.settings,p6_preparation_enabled=True)
        with patch('data_analysis_agent.jobs.worker.configured_agent_identity',return_value=(settings,self.fixture.agent_identity)):
            task=self.fixture.work('具体体现在哪里？',self.fixture.first['task_id'])
        self.assertEqual(task['result']['p6_execution']['status'],'skipped_budget')
        self.assertEqual(task['result']['p6_execution']['budget']['attempts'],0)
        self.assertEqual(task['result']['status'],'error')
        self.assertEqual(task['status'],'failed')
        self.assertFalse(task['result']['task_acceptance']['complete'])


if __name__ == '__main__':
    unittest.main()
