"""Frozen MVP questions through the Agent using SYNTHETIC SQLite/repository data.

These tests never assert real connectivity or real-case acceptance. They do not
connect to PostgreSQL, run BGE, download weights or construct an external LLM.
"""
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
import unittest
from unittest.mock import patch

import test_review_semantic_integration as fixtures
from test_agent import SETTINGS
from data_analysis_agent.agents.reviews import run_review_agent
from data_analysis_agent.agents.review_intent import resolve_review_request
from data_analysis_agent.agents.review_retrieval import retrieval_request
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SemanticRetrievalError


MANIFEST = Path(__file__).parent/'fixtures/rag_mvp_acceptance_v1.json'
MANIFEST_SHA256 = 'a50c41143fe142e4a0a7c3e83afc9fb620724124cdf44d874912f09f06782c29'
LOCAL_SETTINGS = replace(SETTINGS, semantic_parser_enabled=False, max_retries=0)


def frozen_cases():
    data = strict_json_loads(MANIFEST.read_text(encoding='utf-8'))
    if content_sha256(data) != MANIFEST_SHA256:
        raise ValueError('frozen_mvp_cases_changed')
    return data


def synthetic_source(scope, text, *, sentiment='negative', aspect='effectiveness', rid=1):
    source = fixtures.source(scope,text,text,rid=rid,sentiment=sentiment)
    row = {**asdict(source.opinions[0]), **asdict(source.state), 'aspect':aspect, 'review_text':text}
    identity = tuple(scope[k] for k in ('model','base_url','prompt_version'))
    profile = content_sha256(list(identity))
    doc = _document(row,identity,profile)
    return ReviewSource(_review_state(row,[doc],profile),(doc,),text)


class MvpOfflineTests(unittest.TestCase):
    def setUp(self):
        guard=ExitStack();self.addCleanup(guard.close)
        for target in ('psycopg.connect','socket.create_connection','socket.socket.connect',
                       'httpx.HTTPTransport.handle_request','httpx.AsyncHTTPTransport.handle_async_request',
                       'openai.OpenAI','data_analysis_agent.reviews.embedding.LocalBgeM3Embedder'):
            guard.enter_context(patch(target,side_effect=AssertionError('MVP offline: no real service')))
        self.f=fixtures.SemanticAgentTests('test_real_agent_registry_p0_encoder_repository_validation_and_answer')
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def run_agent(self,question,*,scope=None,factory=None):
        return run_review_agent(self.f.db,question,scope=scope or self.f.scope(),settings=LOCAL_SETTINGS,
            semantic_service_factory=factory or self.f.factory)

    def test_freeze_exact_ten_requests_scope_profile_and_budget(self):
        data=frozen_cases()
        self.assertEqual([c['id'] for c in data['cases']],[f'M{i:02d}' for i in range(1,11)])
        self.assertEqual(data['main_product_id'],'100013588053')
        self.assertEqual(data['isolation_product_id'],'100013588055')
        self.assertEqual((data['scope']['source'],data['scope']['dataset_version']),('reviews_csv','reviews-csv'))
        self.assertEqual((data['scope']['date_from'],data['scope']['date_to']),('2021-09-11','2022-08-26'))
        self.assertEqual(data['top_k'],5)
        self.assertEqual(data['initial_max_agent_submissions'],len(data['cases'])-1+len(data['cases'][-1]['steps']))
        self.assertEqual(data['initial_max_local_embedding_queries'],11)
        self.assertEqual(data['external_llm_calls']+data['external_embedding_calls'],0)
        self.assertEqual(data['automatic_retries'],0)
        self.assertEqual(data['gate']['minimum_passed_cases'],8)
        self.assertTrue(data['gate']['require_at_least_one_real_supported_answer'])
        self.assertEqual(data['gate']['supported_answer_product_id'],data['main_product_id'])
        self.assertTrue(data['gate']['insufficiency_requires_no_sufficient_evidence_in_fixed_candidates'])
        self.assertTrue(data['gate']['semantic_coverage_gap_with_sufficient_evidence_fails'])

    def test_frozen_questions_use_existing_routes_and_never_upgrade_p5(self):
        products=[dict(product_id='P1',title='SYNTHETIC ONE',brand='SYNTHETIC'),
                  dict(product_id='P2',title='SYNTHETIC TWO',brand='SYNTHETIC')]
        for case in frozen_cases()['cases']:
            with self.subTest(case=case['id']):
                scope,plan,direct=resolve_review_request(case['question'],self.f.scope(),products,settings=LOCAL_SETTINGS)
                self.assertTrue(direct)
                self.assertEqual(plan.analysis_type,case['route'])
                self.assertEqual(plan.task_contract.support_status,case['contract_support'])
                self.assertEqual([f.id for f in plan.task_contract.required_facts.items],case['fact_ids'])
                query=retrieval_request(case['question'])
                self.assertEqual(query.question,case['question'])
                self.assertEqual(query.top_k,5)

    def test_first_nine_fixed_questions_synthetic_answers_and_citations(self):
        rows=[('配送很快。','positive','logistics'),('配送很慢。','negative','logistics'),
              ('客服态度差。','negative','service'),('货到了，等了很久没人来安装。','negative','service'),
              ('制冷效果好。','positive','effectiveness'),('开了一整天房间还是热。','negative','effectiveness'),
              ('使用没多久主机就故障了。','negative','quality'),
              ('制冷效果好；噪音很大。','negative','effectiveness'),('制冷不足。','negative','effectiveness')]
        for case,(text,sentiment,aspect) in zip(frozen_cases()['cases'][:9],rows,strict=True):
            with self.subTest(case=case['id']):
                self.f.sources=[synthetic_source(self.f.scope(),text,sentiment=sentiment,aspect=aspect)]
                self.f.sources.append(synthetic_source(self.f.scope(),'包装完好。',sentiment='positive',aspect='packaging',rid=2))
                self.f.matches=[OpinionMatch(self.f.sources[0].opinions[0],0.1)]
                result=self.run_agent(case['question'])
                self.assertEqual(result['status'],'ok',result.get('error'))
                self.assertEqual(result['validation']['status'],'passed')
                self.assertEqual(result['total_model_calls'],0)
                self.assertEqual(self.f.encoder.calls[-1],[case['question']])
                refs=result['public_answer']['evidence_refs']
                if case['contract_support']=='supported':
                    self.assertEqual(len(refs),1)
                    ref=refs[0];self.assertEqual(ref['product_id'],'P1')
                    for quote in ref['opinions']:
                        self.assertEqual(text[quote['quote_start']:quote['quote_end']],quote['quote'])
                        self.assertTrue(quote['opinion_key'])
                        self.assertTrue(quote['annotation_id'])
                else:
                    self.assertEqual(refs,[])
                    self.assertIn('不能确认',result['public_answer']['summary'])
                    self.assertEqual(result['results']['r1']['data']['assessment']['reason'],'query_semantics_uncovered')
                self.assertIn('统计样本或总体分母',result['public_answer']['sample_note'])

    def test_case_ten_switch_wrong_scope_failure_and_recovery(self):
        question=frozen_cases()['cases'][-1]['question']
        first=self.f.scope();second=self.f.scope(product_ids=['P2'])
        for scope in (first,second):
            self.f.sources=[synthetic_source(scope,'制冷效果好。',sentiment='positive')]
            self.f.sources.append(synthetic_source(scope,'包装完好。',sentiment='positive',aspect='packaging',rid=2))
            self.f.matches=[OpinionMatch(self.f.sources[0].opinions[0],0.1)]
            result=self.run_agent(question,scope=scope)
            self.assertEqual(result['status'],'ok')
            self.assertEqual({r['product_id'] for r in result['public_answer']['evidence_refs']},set(scope['product_ids']))
        before=len(self.f.encoder.calls)
        invalid=self.run_agent(question,scope=self.f.scope(product_ids=['P1','P2']))
        self.assertEqual(invalid['status'],'unsupported')
        self.assertEqual(invalid['public_answer']['evidence_refs'],[])
        def failure(connection,scope):raise SemanticRetrievalError('synthetic_service_unavailable')
        failed=self.run_agent(question,scope=second,factory=failure)
        self.assertEqual(failed['status'],'error')
        self.assertEqual(failed['public_answer']['evidence_refs'],[])
        self.assertNotIn('answer',failed)
        self.assertEqual(len(self.f.encoder.calls),before)
        recovered=self.run_agent(question,scope=second)
        self.assertEqual(recovered['status'],'ok')
        self.assertEqual({r['product_id'] for r in recovered['public_answer']['evidence_refs']},{'P2'})

    def test_empty_candidates_do_not_claim_population_absence(self):
        self.f.matches=[]
        result=self.run_agent(frozen_cases()['cases'][0]['question'])
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['public_answer']['evidence_refs'],[])
        self.assertIn('不表示全量',result['public_answer']['summary'])

    def test_existing_sql_summary_has_no_embedding_or_topk_denominator(self):
        result=self.run_agent('好评主要集中在哪些方面？')
        self.assertEqual(result['status'],'ok',result.get('error'))
        self.assertEqual(self.f.encoder.calls,[])
        self.assertEqual(result['total_model_calls'],0)
        self.assertIn('positive_aspects',[v['kind'] for v in result['results'].values()])


if __name__=='__main__':
    unittest.main()
