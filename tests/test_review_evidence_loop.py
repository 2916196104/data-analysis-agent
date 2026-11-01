"""闭环执行和失败保护；完整 HTTP/后台 Worker 验收另由 acceptance 脚本运行。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from unittest.mock import patch

from scripts.insight_evidence_cases import EvidenceFixture,semantic_fixture
from test_agent import SETTINGS
import test_review_plan as plan_fixture
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_topics import TopicBatch
from data_analysis_agent.agents.review_evidence import assess_evidence
from data_analysis_agent.agents.review_evidence_loop import resolve_followups,check_sample_continuity
from data_analysis_agent.agents.review_compression import CompactAnalysisContext,read_public_analysis
from data_analysis_agent.agents.review_insights import fact_catalog
from data_analysis_agent.agents.review_evidence_semantics import evidence_relations
from data_analysis_agent.reviews import analytics


class EvidenceLoopTests(EvidenceFixture):
    execute_plan=plan_fixture.QueryPlanTests.execute_plan
    run_sync=plan_fixture.QueryPlanTests.run_sync

    def setUp(self):
        super().setUp();self.seed();self.products=['IE-PACK']

    def scope(self,**changes):
        return EvidenceFixture.scope(self,**{'dataset_version':'insight-evidence-v1','product_ids':self.products,
            'date_from':'2023-01-01','date_to':'2023-03-31',**changes})

    def test_packaging_auto_query_and_local_layer_relation_without_external_topic_text(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest()
        with patch('data_analysis_agent.agents.review_topics.analyze_aspect_topics',side_effect=AssertionError('no automatic raw egress')):
            result=self.run_sync('包装差评具体反映什么问题？')
        self.assertEqual(result['status'],'ok',result.get('error'))
        record=result['evidence_completion']
        self.assertEqual((record['cycles'],record['status'],record['additional_tool_calls']),(1,'completed',1))
        self.assertEqual(record['requests'][0]['tool'],'get_opinion_breakdown')
        self.assertEqual(record['requests'][0]['proposed_tool'],'get_aspect_drilldown')
        self.assertEqual(result['validation']['status'],'passed')
        self.assertEqual([row['result_id']for row in result['answer_sections']],result['result_ids'])
        self.assertIn('盒内固定',result['public_answer']['summary'])
        self.assertIn('商品完好',result['public_answer']['summary'])
        self.assertTrue(result['public_answer']['evidence_relations'][0]['bindings'])
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_logistics_two_products_and_time_windows_keep_distinct_sources(self):
        self.products=['IE-LOG-A','IE-LOG-B']
        comparison=self.run_sync('对比这两款商品的物流差评，具体发生在哪些环节？')
        self.assertEqual(comparison['status'],'ok',comparison.get('error'))
        text=comparison['public_answer']['summary']
        for token in ('IE-LOG-A','IE-LOG-B','发货前等待','运输追踪','预约履约'):self.assertIn(token,text)
        self.assertEqual(comparison['evidence_completion']['additional_tool_calls'],2)
        self.products=['IE-TREND']
        trend=self.run_sync('物流负面占比按月有什么变化？具体问题类型在首末月份是否不同？')
        self.assertEqual(trend['status'],'ok',trend.get('error'))
        self.assertEqual(trend['evidence_completion']['additional_tool_calls'],2)
        text=trend['public_answer']['summary']
        for token in ('2023-01-01','2023-03-31','发货前等待','预约履约','不能据此确认'):self.assertIn(token,text)
        self.assertEqual(read_public_analysis({'status':'succeeded','result':trend})['status'],'ok')
        invalid=deepcopy(trend)
        child=invalid['evidence_completion']['requests'][0]['result_ids'][0]
        invalid['results'][child]['execution_scope']['date_from']='2022-01-01'
        self.assertEqual(read_public_analysis({'status':'succeeded','result':invalid})['status'],'failed')

    def test_budget_exhaustion_fails_without_supplement_or_false_success(self):
        query='执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_top_issues','parameters':{'aspect':'packaging'},'question':'包装差评具体反映什么问题？'})
        with patch('openai.OpenAI',side_effect=AssertionError('no model')):
            result=run_review_agent(self.db,query,scope=self.scope(),settings=replace(SETTINGS,max_tool_calls=2))
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['evidence_completion']['status'],'failed')
        self.assertEqual(result['evidence_completion']['additional_tool_calls'],0)
        self.assertNotEqual(result['public_answer']['status'],'ok')

    def test_tool_failure_and_corrupted_evidence_fail_closed(self):
        for invalid in (False,True):
            original=analytics.get_opinion_breakdown
            def broken(*args,**kwargs):
                if not invalid:raise TimeoutError('synthetic query timeout')
                data=original(*args,**kwargs)
                data['items'][0]['opinions'][0]['quote']='不存在的伪造原文'
                return data
            with self.subTest(invalid=invalid),patch.object(analytics,'get_opinion_breakdown',side_effect=broken):
                result=self.run_sync('包装差评具体反映什么问题？')
            self.assertEqual(result['status'],'error')
            self.assertEqual(result['evidence_completion']['status'],'failed')
            self.assertNotEqual(result['public_answer']['status'],'ok')
            self.assertIn('r1',result['results'])

    def test_tampered_plan_dates_sentiment_and_products_are_not_authority(self):
        probe=self.snapshot('包装差评具体反映什么问题？',['IE-PACK'],[{'tool':'get_top_issues','parameters':{'aspect':'packaging'}}])
        context=CompactAnalysisContext.model_validate(probe['compact_context'])
        assessment=assess_evidence(context)
        for field,value in (('product_ids',['IE-LOG-A']),('sentiment','positive')):
            invalid=deepcopy(assessment);invalid['followup_queries'][0]['parameters'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):resolve_followups(invalid,context,probe['scope'])
        invalid=deepcopy(assessment);invalid['followup_queries'][0]['requested_subwindow']={'date_from':'2022-01-01','date_to':'2023-03-31'}
        with self.assertRaises(ValueError):resolve_followups(invalid,context,probe['scope'])

    def test_partial_and_failed_topic_work_never_start_completion(self):
        query='执行本轮已确认的只读查询\n'+json.dumps({'tool':'get_aspect_drilldown','parameters':{'aspect':'packaging','sentiment':'negative','product_ids':['IE-PACK']}})
        def partial(settings,prompt,payload,schema,timeout):
            if schema is TopicBatch and payload['opinions'][0]['quote']=='内衬移位导致配件在盒内晃动':raise TimeoutError('partial fixture')
            return semantic_fixture(settings,prompt,payload,schema,timeout)
        for failure in (False,True):
            with self.subTest(failure=failure),patch('data_analysis_agent.agents.review_topics.BATCH_SIZE',1),patch('data_analysis_agent.agents.review_topics._call',side_effect=TimeoutError('failed fixture')if failure else partial):
                result=run_review_agent(self.db,query,scope=self.scope(),settings=SETTINGS)
            self.assertEqual(result['status'],'error'if failure else'partial')
            self.assertFalse(result['evidence_completion']['automatic'])
            self.assertNotEqual(result['public_answer']['status'],'ok')

    def test_unrecognized_negated_or_wrong_product_quotes_do_not_gain_semantic_roles(self):
        result=self.run_sync('包装差评具体反映什么问题？')
        context=CompactAnalysisContext.model_validate(result['compact_context'])
        context.evidence[0].product_id='IE-LOG-A'
        catalog,_=fact_catalog(context)
        relations=evidence_relations(context,catalog)
        self.assertFalse(any(node['review_id']==context.evidence[0].review_id for relation in relations for node in relation['bindings']))
        for stat in context.statistics:
            for group in stat.get('filters',{}).get('groups',[]):group['quote']='没有外箱压瘪'
        for ref in context.evidence:
            for opinion in ref.opinions:opinion.quote='没有外箱压瘪'
        catalog,_=fact_catalog(context)
        self.assertFalse(evidence_relations(context,catalog))

    def test_multi_product_reference_does_not_guess(self):
        self.products=['IE-LOG-A','IE-LOG-B','IE-LOG-Z']
        with patch('openai.OpenAI',side_effect=AssertionError('must clarify before model')):
            result=run_review_agent(self.db,'对比这两款商品物流差评的具体环节？',scope=self.scope(),settings=SETTINGS)
        self.assertEqual(result['status'],'unsupported')
        self.assertFalse(result['evidence_completion']['automatic'])

    def test_changed_sample_between_transactions_is_rejected(self):
        result=self.run_sync('包装差评具体反映什么问题？')
        context=CompactAnalysisContext.model_validate(result['compact_context'])
        request=result['evidence_completion']['requests'][0]
        child=deepcopy({key:result['results'][key]for key in request['result_ids']})
        child[request['result_ids'][0]]['data']['summary']['successful_reviews']-=1
        with self.assertRaisesRegex(ValueError,'有效样本'):
            check_sample_continuity(context,request,child)
