"""Scope-prefix counterexamples; immutable corpus and permission grammar are never rewritten."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch

import agent_stage1_multiturn_evaluation as harness
from run_agent_stage1_g25 import bind_source_revision
from test_review_reported_facts import evidence
from test_semantic_retrieval import FakeEncoder, FakeRepository, FakeSnapshot
from data_analysis_agent.agents.review_task_contract import evidence_question, evidence_scope_matches
from data_analysis_agent.agents.review_reported_facts import target
from data_analysis_agent.agents.review_retrieval import assess_retrieval, validate_retrieval, retrieval_request
from data_analysis_agent.agents.review_answers import read_public_answer
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import SemanticRetrievalService, ReviewSource
from data_analysis_agent.reviews.embedding_store import OpinionMatch

A,B='100013588053','100013588055'
QUESTION=B+'有没有物流很慢的反馈？'
PROPOSITION='有没有物流很慢的反馈？'


class G25ScopePrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bind_source_revision()
        cls.h=harness.Harness()
        cls.h.oracle=json.loads((harness.FIX/'oracle.v2.json').read_text(encoding='utf-8'))
        with redirect_stdout(io.StringIO()):cls.turns={t['id']:t for t in cls.h.run()}

    def data(self,tid='G25-T1'):
        return self.turns[tid]['task']['result']['results']['r1']['data']

    def scope(self,ids=None):
        return {**self.h.oracle['scope'],'product_ids':ids if ids is not None else [B]}

    def run_question(self,q,*,ids=None,factory=None):
        h=harness.Harness();h.oracle=deepcopy(self.h.oracle)
        group=deepcopy(next(g for g in h.oracle['groups'] if g['id']=='G25'))
        group['turns']=group['turns'][:1];group['turns'][0]['input']=q
        h.oracle['groups']=[group];h.oracle['scope']=self.scope(ids)
        if factory is not None:h.semantic_service=lambda connection,scope,settings=None:factory(h,scope)
        with redirect_stdout(io.StringIO()):turn=h.run()[0]
        self.assertEqual(h.forbidden_attempts,[])
        self.assertTrue(all(c.closed for c in h.connections))
        return turn

    def test_matched_catalog_bound_scope_identifies_only_existing_proposition(self):
        t=self.turns['G25-T1'];r=t['task']['result'];d=self.data()
        self.assertEqual(r['scope']['product_ids'],[B]);self.assertEqual(r['user_intent']['resolved_product_ids'],[B])
        self.assertIsNone(r['request_goal']);self.assertEqual(r['analysis_intent'],'semantic_evidence')
        self.assertEqual(r['query_plan']['required_tools'],['search_semantic_evidence'])
        self.assertEqual(r['query_contract']['evidence_question'],PROPOSITION)
        self.assertEqual(target(PROPOSITION)['facts'],['delivery_slow'])
        self.assertEqual(d['assessment_question'],PROPOSITION)
        self.assertEqual(d['assessment']['status'],'sufficient')
        self.assertEqual(len(d['assessment']['permitted_keys']),1)
        self.assertTrue(validate_retrieval(d,QUESTION));self.assertEqual(r['validation']['status'],'passed')
        catalog=next(i for i,e in enumerate(t['events']) if e['node']=='connection_double' and 'FROM products' in e['sql'])
        planned=next(i for i,e in enumerate(t['events']) if e['node']=='router_planner')
        self.assertLess(catalog,planned)

    def test_original_semantic_query_and_scope_digest_are_preserved(self):
        r=self.turns['G25-T1']['task']['result'];d=self.data()
        self.assertEqual(r['question'],QUESTION);self.assertEqual(r['query_contract']['original_question'],QUESTION)
        self.assertEqual(d['retrieval']['query']['question'],QUESTION)
        self.assertEqual(d['retrieval']['query'],retrieval_request(QUESTION).model_dump(mode='json'))
        self.assertEqual(r['query_contract']['scope_sha256'],content_sha256(r['query_contract']['scope']))
        self.assertEqual(d['scope']['product_ids'],[B])

    def test_text_alone_is_never_a_trusted_product_binding(self):
        self.assertEqual(evidence_question(QUESTION),QUESTION)
        self.assertIsNone(target(evidence_question(QUESTION)))
        self.assertEqual(evidence_question(QUESTION,{'product_ids':[]}),QUESTION)

    def test_conflicting_scope_does_not_remove_known_sku(self):
        self.assertEqual(evidence_question(QUESTION,self.scope([A])),QUESTION)
        self.assertIsNone(target(evidence_question(QUESTION,self.scope([A]))))

    def test_unknown_product_is_rejected_by_real_catalog_gate(self):
        t=self.run_question('999999999999有没有物流很慢的反馈？',ids=['999999999999'])
        r=t['task']['result'];self.assertEqual(r['query_executions'],0)
        self.assertEqual(r['results'],{});self.assertEqual(r['status'],'unsupported')
        self.assertFalse(any(e['node']=='rag_double' for e in t['events']))

    def test_matching_text_and_scope_still_cannot_bypass_missing_catalog(self):
        original=harness.MemoryReadConnection.execute
        def no_catalog(connection,sql,parameters=None):
            if 'SELECT product_id,title,brand FROM products' in sql:return harness.Cursor([])
            return original(connection,sql,parameters)
        with (patch.object(harness.MemoryReadConnection,'execute',no_catalog),
              patch('data_analysis_agent.agents.review_task_contract.evidence_question',wraps=evidence_question) as normalize):
            t=self.run_question(QUESTION)
        self.assertEqual(t['task']['result']['query_executions'],0)
        self.assertEqual(t['task']['result']['results'],{})
        normalize.assert_not_called()

    def test_multi_product_scope_cannot_license_a_single_product_prefix(self):
        self.assertEqual(evidence_question(QUESTION,self.scope([A,B])),QUESTION)
        q=A+'和'+QUESTION
        t=self.run_question(q,ids=[A,B]);r=t['task']['result']
        for row in r.get('results',{}).values():
            if row['kind']=='semantic_evidence':self.assertEqual(row['data']['assessment']['permitted_keys'],[])
        self.assertFalse((r.get('task_acceptance') or {}).get('complete'))

    def test_plain_supported_question_retains_exact_permission(self):
        t=self.run_question(PROPOSITION);d=t['task']['result']['results']['r1']['data']
        self.assertEqual(evidence_question(PROPOSITION,self.scope()),PROPOSITION)
        self.assertEqual(d['assessment'],self.data()['assessment'])

    def test_sku_in_middle_or_just_longer_prefix_is_not_removed(self):
        for q in ('有没有'+B+'物流很慢的反馈？',B+'9有没有物流很慢的反馈？','不是'+QUESTION):
            with self.subTest(q=q):
                self.assertEqual(evidence_question(q,self.scope()),q)
                self.assertIsNone(target(evidence_question(q,self.scope())))

    def test_negation_comparison_time_quantity_and_other_products_are_not_deleted(self):
        for suffix in ('没有物流很慢的反馈？','有没有并非物流很慢的反馈？',
                       '有没有比100013588053物流更慢的反馈？','有没有去年物流很慢的反馈？',
                       '有没有三条物流很慢的反馈？','和100013588053有没有物流很慢的反馈？'):
            q=B+suffix
            with self.subTest(q=q):self.assertEqual(evidence_question(q,self.scope()),q)

    def test_matching_date_prefix_uses_same_range_and_keeps_original_audit_input(self):
        q='2023-02-01到2023-02-28 '+QUESTION
        self.assertEqual(evidence_question(q,self.scope()),PROPOSITION)
        t=self.run_question(q);r=t['task']['result'];d=r['results']['r1']['data']
        self.assertEqual(r['query_contract']['original_question'],q)
        self.assertEqual(d['retrieval']['query']['question'],q)
        self.assertEqual((d['scope']['date_from'],d['scope']['date_to']),('2023-02-01','2023-02-28'))
        self.assertTrue(validate_retrieval(d,q))

    def test_conflicting_date_range_cannot_authorize_prefix_normalization(self):
        q='2023-01-01到2023-01-31 '+QUESTION
        self.assertFalse(evidence_scope_matches(q,self.scope()))
        self.assertEqual(evidence_question(q,self.scope()),QUESTION)
        t=self.run_question(q);self.assertEqual(t['task']['result']['query_executions'],0)
        self.assertEqual(t['task']['result']['results'],{})

    def test_normalization_does_not_license_wrong_fact_or_negated_context(self):
        for row in (evidence('物流很快。'),evidence('并非物流很慢。','物流很慢')):
            with self.subTest(quote=row['quote']):
                result=assess_retrieval(evidence_question(QUESTION,self.scope()),[row])
                self.assertEqual(result['status'],'insufficient');self.assertEqual(result['permitted_keys'],[])

    def test_only_one_of_four_real_candidates_has_permitted_context(self):
        d=self.data();rows=d['retrieval']['evidence'];a=d['assessment']
        self.assertEqual(len(rows),4)
        self.assertEqual(a['permitted_keys'],[rows[0]['key']])
        self.assertEqual([x['reason'] for x in a['candidate_judgments']],['explicit_entailment']+['context_unresolved']*3)
        self.assertEqual(a['fact_bindings'][0]['review_id'],101)
        for ref in a['fact_bindings']:
            row=next(x for x in rows if x['key']==ref['key'])
            self.assertEqual(row['review_text'][ref['quote_start']:ref['quote_end']],'物流很慢')

    def test_offsets_wrong_source_and_cross_product_fail_retrieval_validation(self):
        for field,value in [('quote_start',0),('product_id',A),('source','another-source'),('annotation_id',99999)]:
            d=deepcopy(self.data());d['retrieval']['evidence'][0][field]=value
            with self.subTest(field=field):self.assertFalse(validate_retrieval(d,QUESTION))

    def test_forged_normalization_or_permission_is_rejected(self):
        d=deepcopy(self.data());d['assessment_question']='有没有客服态度差的反馈？'
        self.assertFalse(validate_retrieval(d,QUESTION))
        d=deepcopy(self.data());d['assessment']['permitted_keys']=[r['key'] for r in d['retrieval']['evidence']]
        self.assertFalse(validate_retrieval(d,QUESTION))

    def test_053_vector_gap_never_becomes_supported_evidence(self):
        d=self.data('G24-T1')
        self.assertEqual(d['retrieval']['evidence'],[]);self.assertEqual(d['assessment']['permitted_keys'],[])
        self.assertEqual(d['assessment']['status'],'insufficient')
        self.assertEqual(d['assessment']['reason'],'no_candidates')

    def test_055_individually_supported_facts_still_need_same_review_relation(self):
        def separate_reviews(h,scope):
            identity=tuple(scope[k] for k in ('model','base_url','prompt_version'));profile=content_sha256(list(identity))
            sources=[]
            for rid,quote,aspect in ((901,'物流很慢','logistics'),(902,'客服态度差','service')):
                text=quote+'。';row=dict(source=scope['source'],dataset_version=scope['dataset_version'],product_id=B,
                    review_id=rid,annotation_id=rid+1000,human_revision_id=None,processing_status='success',
                    human_status='unreviewed',error_code=None,opinion_source='model',review_content_sha256=content_sha256(text),
                    published_at='2023-02-10T00:00:00+00:00',review_text=text,import_run_id=1,line_number=rid,file_sha256='2'*64)
                doc=_document(dict(**row,model_opinion_id=rid,human_opinion_id=None,quote=quote,quote_start=0,
                    quote_end=len(quote),aspect=aspect,sentiment='negative'),identity,profile)
                sources.append(ReviewSource(_review_state(row,[doc],profile),(doc,),text))
            events=[];snapshot=FakeSnapshot(events,sources)
            snapshot.matches=[OpinionMatch(s.opinions[0],.1) for s in sources]
            return SemanticRetrievalService(encoder=FakeEncoder(events),repository=FakeRepository(events,snapshot),scope=scope)
        q=B+'有没有同一评论既物流很慢又客服态度差的反馈？'
        t=self.run_question(q,factory=separate_reviews);r=t['task']['result'];d=r['results']['r1']['data']
        self.assertEqual(len(d['retrieval']['evidence']),2)
        self.assertEqual(d['assessment']['required_fact_ids'],['delivery_slow','service_bad'])
        self.assertTrue(all(x['support']=='direct_report' for x in d['assessment']['candidate_judgments']))
        self.assertFalse(d['assessment']['relation']['supported']);self.assertEqual(d['assessment']['permitted_keys'],[])
        self.assertTrue(validate_retrieval(d,q));self.assertEqual(r['public_answer']['highlights'],[])

    def test_persisted_public_answer_revalidates_source_and_exact_binding(self):
        task=self.turns['G25-T1']['task'];public=read_public_answer(task)
        self.assertEqual(public['status'],'ok');self.assertEqual(len(public['evidence_refs']),1)
        ref=public['evidence_refs'][0];self.assertEqual((ref['review_id'],ref['product_id']),(101,B))
        self.assertEqual(ref['opinions'][0]['quote'],'物流很慢')
        self.assertEqual(task['result']['total_model_calls'],0)
        self.assertEqual(self.h.forbidden_attempts,[])


if __name__=='__main__':unittest.main()
