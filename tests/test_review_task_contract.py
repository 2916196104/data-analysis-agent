"""Runtime contract regression and additional synthetic expressions; no real data/models."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_review_semantic_integration as semantic_fixture
from test_agent import SETTINGS
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_plan import build_query_plan
from data_analysis_agent.agents.review_task_contract import RuntimeQueryContract, task_matches
from data_analysis_agent.agents.review_compression import read_public_analysis
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.agents.review_retrieval import validate_retrieval, execute_retrieval
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalError
from data_analysis_agent.agents.review_validation import validate_report


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.f=semantic_fixture.SemanticAgentTests('test_real_agent_registry_p0_encoder_repository_validation_and_answer')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.scope=self.f.scope()
        self.products=[dict(product_id='P1',title='和风 KFR-35G/W（新风与净化）空调',brand='和风'),
                       dict(product_id='P2',title='另一款合成空调',brand='另一款')]

    def resolve(self,q,scope=None,products=None):
        return resolve_review_request(q,scope or self.scope,products or self.products,
                                      settings=replace(SETTINGS,semantic_parser_enabled=False))

    def test_known_p5_probes_have_explicit_routes_not_new_generalization_claim(self):
        cases=[
            ('P01','有没有制冷效果好的反馈？','semantic_evidence','supported'),
            ('P02','有没有降温不给力的反馈？','semantic_evidence','retrieval_only'),
            ('P03','不是制冷慢，而是完全不降温，有没有类似抱怨？','semantic_evidence','unsupported'),
            ('P04','是否有制冷不足的评论？','semantic_evidence','supported'),
            ('P05','这款商品有哪些优点和缺点？','pros_cons','supported'),
            ('P06','有没有制冷好或物流很快的反馈？','semantic_evidence','unsupported'),
            ('P07','有没有同一评论既说制冷好又抱怨噪音大的反馈？','semantic_evidence','supported'),
            ('P08','有没有先制冷正常、后来不制冷的反馈？','semantic_evidence','unsupported'),
            ('P09','好评主要集中在哪些方面？','positive_aspects','supported'),
            ('P10','制冷不足的反馈占全部评论多少？','unsupported','unsupported'),
            ('P11','2023-01-02至2023-01-04有没有制冷不足的反馈？','semantic_evidence','supported'),
            ('P12','为什么这款空调制冷不足，是否由压缩机故障导致？','unsupported','unsupported'),
            ('P13','有没有空调使用十年仍然耐用的反馈？','semantic_evidence','retrieval_only'),
            ('P14','是否有客服态度差的反馈？','semantic_evidence','supported'),
            ('P15','有没有配送很慢的反馈？','semantic_evidence','supported'),
        ]
        for pid,q,kind,status in cases:
            with self.subTest(pid=pid):
                _,plan,_=self.resolve(q)
                self.assertEqual(plan.analysis_type,kind);self.assertEqual(plan.task_contract.support_status,status)
                self.assertEqual(plan.task_contract.original_question,q)
                if status=='unsupported':self.assertTrue(plan.unsupported_reason)

    def test_additional_product_references_titles_unknown_and_cross_scope(self):
        for q in ('请介绍这台机器的优点和缺点','当前产品有什么优缺点？','所选机型都有哪些优点和缺点？'):
            with self.subTest(q=q):
                bound,plan,_=self.resolve(q);self.assertEqual(bound['product_ids'],['P1']);self.assertEqual(plan.analysis_type,'pros_cons')
        title=self.products[0]['title']
        _,plan,_=self.resolve(title+'有哪些优点和缺点？')
        self.assertEqual(plan.analysis_type,'pros_cons')
        for q in ('不存在品牌空调有哪些优点和缺点？','P2有没有制冷不足的反馈？'):
            with self.subTest(q=q),self.assertRaises(ValueError):self.resolve(q)
        _,plan,_=self.resolve('有没有制冷不足的反馈？',self.scope | {'product_ids':['P1','P2']})
        self.assertEqual(plan.task_contract.support_status,'unsupported')

    def test_date_prefix_normalizes_goal_but_preserves_embedding_and_reference(self):
        q='2023-01-02至2023-01-04有没有制冷不足的反馈？'
        r=self.f.run_question(q);self.f.assert_success(r)
        self.assertEqual(self.f.encoder.calls,[[q]])
        self.assertEqual(r['query_contract']['evidence_question'],'有没有制冷不足的反馈？')
        self.assertEqual(r['scope']['date_from'],'2023-01-02')
        data=r['results']['r1']['data'];self.assertTrue(validate_retrieval(data,q))
        self.assertEqual(data['assessment']['status'],'sufficient')
        self.assertTrue(r['public_answer']['evidence_refs'])
        task={'status':'succeeded','result':r};public=read_public_analysis(task)
        self.assertEqual(public['status'],'ok');self.assertEqual(read_public_answer(task,public_analysis=public),r['public_answer'])
        forged=deepcopy(data);forged['assessment_question']='有没有制热很快的反馈？'
        self.assertFalse(validate_retrieval(forged,q))
        forged=deepcopy(data)
        forged['scope']['date_to']=forged['retrieval']['scope']['date_to']='2023-01-05'
        self.assertFalse(validate_retrieval(forged,q))
        with self.assertRaisesRegex(SemanticRetrievalError,'question_scope_mismatch'):
            execute_retrieval(SimpleNamespace(question=q),{k:v for k,v in self.scope.items() if not k.startswith('baseline_')})

    def test_unseen_date_prefix_and_scope_expansion(self):
        q='从2023-01-02到2023-01-05期间，是否有制冷不佳的评论？'
        bound,plan,_=self.resolve(q)
        self.assertEqual(bound['date_to'],'2023-01-05')
        self.assertEqual(plan.task_contract.evidence_question,'是否有制冷不佳的评论？')
        for q in ('2022-12-31至2023-01-04有没有制冷不足的反馈？','查看2022-12-31至2023-01-04物流负面趋势'):
            with self.subTest(q=q),self.assertRaises(ValueError):self.resolve(q)

    def test_cause_and_fine_topic_ratios_never_become_other_sql_results(self):
        for q in ('空调不制冷是不是压缩机损坏造成的？','请查明制热故障的根因。',
                  '噪音大的反馈比例是多少？','制热不佳的评论占比是多少？',
                  '客服态度差的反馈占比是多少？','包装破损的评论比例？',
                  '物流配送迟缓的负面评论比例？','外观掉漆的反馈比例？',
                  '物流负面评论占全部评论多少？'):
            with self.subTest(q=q):
                _,p,_=self.resolve(q);self.assertEqual(p.task_contract.support_status,'unsupported')
                self.assertEqual(p.required_tools,[])
        for q in ('不要解释原因，只统计好评主要方面','查询物流负面评论占比','查看失败标注和缺失正文'):
            with self.subTest(q=q):
                _,p,_=self.resolve(q);self.assertNotEqual(p.task_contract.support_status,'unsupported')
        r=self.f.run_question('为什么这款空调制冷不足，是否由压缩机故障导致？')
        self.assertEqual(r['status'],'unsupported');self.assertEqual(r['query_executions'],0);self.assertEqual(self.f.encoder.calls,[])

    def test_actual_pros_cons_keeps_existing_sql_and_template_path(self):
        r=self.f.run_question('这款商品有哪些优点和缺点？')
        self.f.assert_success(r);self.assertEqual(r['analysis_intent'],'pros_cons')
        self.assertEqual(r['query_contract']['task_type'],'mixed')
        self.assertEqual(self.f.encoder.calls,[])
        self.assertEqual([e['tool'] for e in r['trace'] if e['event']=='tool_call'],
                         ['get_analysis_context','get_opinion_breakdown','get_opinion_breakdown'])
        self.assertEqual(read_public_analysis({'status':'succeeded','result':r})['status'],'ok')

    def test_contract_is_serializable_frozen_and_has_no_offline_run_identity(self):
        _,p,_=self.resolve('好评主要集中在哪些方面？')
        c=p.task_contract;raw=c.model_dump(mode='json')
        self.assertEqual(RuntimeQueryContract.model_validate_json(json.dumps(raw)),c)
        self.assertEqual(json.loads(json.dumps(p.to_dict()))['task_contract'],raw)
        self.assertEqual(c.statistics[0].denominator,'positive_review_count')
        self.assertEqual(c.target_entity,'review_population')
        _,opinions,_=self.resolve('包装差评具体有什么问题？')
        self.assertEqual(opinions.task_contract.target_entity,'review_opinion')
        self.assertEqual(opinions.task_contract.subtopic,'packaging')
        self.assertNotIn('run_id',raw);self.assertNotIn('manifest_sha256',raw)
        with self.assertRaises(ValueError):c.scope.product_ids=('P2',)
        with self.assertRaises(ValueError):RuntimeQueryContract.model_validate_json(json.dumps(raw | {'task_type':'causal'}))
        self.assertFalse(task_matches(c,[],scope=self.scope | {'product_ids':['P2']}))
        for bad in ({'baseline_to':None},{'baseline_from':'2023-01-03','baseline_to':'2023-01-05'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                build_query_plan('好评主要集中在哪些方面？',self.scope | bad)

    def test_all_any_and_relation_representation_does_not_license_unsupported_relations(self):
        _,same,_=self.resolve('有没有同一评论既说制冷好又抱怨噪音大的反馈？')
        self.assertEqual(same.task_contract.required_facts.relations[0].kind,'same_review')
        _,either,_=self.resolve('有没有制冷好或物流很快的反馈？')
        self.assertEqual(either.task_contract.required_facts.operator,'any_of')
        self.assertEqual(len(either.task_contract.required_facts.items),2)
        _,before,_=self.resolve('有没有先制冷好后噪音大的反馈？')
        self.assertEqual(before.task_contract.required_facts.relations[0].kind,'before')
        self.assertEqual(before.task_contract.support_status,'unsupported')
        for q in ('有没有制冷不足和配送迟缓的反馈？','有没有不仅省电而且安静的评论？'):
            with self.subTest(q=q):
                self.assertEqual(self.resolve(q)[1].task_contract.support_status,'unsupported')

    def test_fact_target_and_relation_cannot_be_replaced_after_planning(self):
        r=self.f.run_question();self.f.assert_success(r)
        c=build_query_plan(r['question'],r['scope']).task_contract
        selected=[r['results']['r1']]
        self.assertTrue(task_matches(c,selected,scope=r['scope']))
        alternate=c.required_facts.model_copy(update={'operator':'any_of'})
        self.assertFalse(task_matches(c.model_copy(update={'required_facts':alternate}),selected,scope=r['scope']))

    def test_numeric_valid_but_wrong_denominator_definition_is_blocked(self):
        r=self.f.run_question('好评主要集中在哪些方面？');self.f.assert_success(r)
        p=build_query_plan(r['question'],r['scope']);data=deepcopy(r['results'])
        data['r1']['data']['definitions']['positive_aspect_rate_denominator']='successful_reviews'
        check=validate_report(data,r['result_ids'],query_plan=p)
        self.assertEqual(check['status'],'failed')
        self.assertFalse(next(x for x in check['checks'] if x['name']=='查询目标匹配')['passed'])
        self.assertTrue(next(x for x in check['checks'] if x['name']=='数字与分母')['passed'])

    def test_correct_number_wrong_tool_and_stored_contract_drift_are_blocked(self):
        r=self.f.run_question('好评主要集中在哪些方面？');self.f.assert_success(r)
        p=build_query_plan(r['question'],r['scope'])
        self.assertFalse(task_matches(p.task_contract,[r['results']['overview']],scope=r['scope']))
        for mutate in ('scope','metric','question'):
            saved=deepcopy(r)
            if mutate=='scope':saved['query_contract']['scope']['product_ids']=['P2']
            elif mutate=='metric':saved['query_plan']['task_contract']['statistics'][0]['denominator']='review_count'
            else:saved['question']='制冷不足的反馈占全部评论多少？'
            self.assertEqual(read_public_analysis({'status':'succeeded','result':saved})['status'],'failed')


if __name__=='__main__':unittest.main()
