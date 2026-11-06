"""Synthetic encoder/repository tests. No real model, network or business database."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding import EmbeddingSpace
from data_analysis_agent.reviews.embedding_store import SPACE, SPACE_ID, OpinionMatch
from data_analysis_agent.reviews.rag_corpus import _document, _review_state, RagCorpus
from data_analysis_agent.reviews.scope import analysis_scope
from data_analysis_agent.reviews.semantic_retrieval import (
    SemanticQuery, RetrievalScope, SnapshotInfo, ReviewSource, SemanticResult,
    SemanticRetrievalService, PostgresSemanticRepository, SemanticRetrievalError,
)

OPTIONS = dict(model='SYNTHETIC-MODEL',base_url='https://offline.invalid/v1',prompt_version='SYNTHETIC-PROMPT',
    source='SYNTHETIC',dataset_version='synthetic-v1',product_ids=['SYNTHETIC-P1'],
    date_from='2031-01-01',date_to='2031-01-02')
VECTOR = [1.0]+[0.0]*1023
INFO = SnapshotInfo('SYNTHETIC-TRANSACTION','2031-02-01T00:00:00+00:00')


def source(rid=1, *, human=False, day='2031-01-01T23:59:59.999+00:00'):
    text='🎉合成：制冷不足；客服回应及时。' if rid==1 else '合成：质量可靠。'
    quotes=[('制冷不足','effectiveness','negative'),('客服回应及时','service','positive')] if rid==1 else [('质量可靠','quality','positive')]
    identity=tuple(OPTIONS[k] for k in ('model','base_url','prompt_version'));profile=content_sha256(list(identity))
    row=dict(source=OPTIONS['source'],dataset_version=OPTIONS['dataset_version'],product_id=OPTIONS['product_ids'][0],
        review_id=rid,annotation_id=100+rid,human_revision_id=700+rid if human else None,
        processing_status='success',human_status='corrected' if human else 'unreviewed',error_code=None,
        opinion_source='human' if human else 'model',review_content_sha256=content_sha256({'synthetic_record':text,'published':day}),
        published_at=day,review_text=text,import_run_id=3,line_number=10+rid,file_sha256=content_sha256('SYNTHETIC-FILE'))
    docs=[]
    for n,(quote,aspect,sentiment) in enumerate(quotes):
        start=text.index(quote)
        docs.append(_document(dict(**row,model_opinion_id=None if human else 500+10*rid+n,
            human_opinion_id=900+10*rid+n if human else None,quote=quote,quote_start=start,quote_end=start+len(quote),
            aspect=aspect,sentiment=sentiment),identity,profile))
    return ReviewSource(_review_state(row,docs,profile),tuple(docs),text)


class FakeEncoder:
    def __init__(self, events):self.events=events;self.calls=[];self.space=SPACE;self.vectors=[VECTOR];self.error=None;self.callback=None
    def embed_texts(self,texts):
        self.events.append('encode');self.calls.append(texts)
        if self.error:raise self.error
        if self.callback:self.callback()
        return self.vectors


class FakeSnapshot:
    embedding_space_id=SPACE_ID
    text_builder_version='quote-utf8-v1'
    def __init__(self,events,sources):
        self.events=events;self.sources=sources;self.info=INFO;self.scope=None
        self.matches=[OpinionMatch(sources[1].opinions[0],0.12),OpinionMatch(sources[0].opinions[0],0.2)]
        self.search_calls=[];self.source_calls=[];self.filter_results=True
        self.search_error=self.source_error=None;self.on_search=self.on_sources=None
    def search(self,vector,*,top_k,aspect,sentiment):
        self.events.append('search');self.search_calls.append(dict(vector=vector,top_k=top_k,aspect=aspect,sentiment=sentiment))
        if self.search_error:raise self.search_error
        if self.on_search:self.on_search()
        rows=self.matches
        if self.filter_results:rows=[m for m in rows if (aspect is None or m.document.aspect==aspect)and(sentiment is None or m.document.sentiment==sentiment)]
        return tuple(rows[:top_k])
    def load_sources(self,keys):
        self.events.append('sources');self.source_calls.append(keys)
        if self.source_error:raise self.source_error
        if self.on_sources:self.on_sources()
        # Independent sources, deliberately returned in review order, not search order.
        return tuple(s for s in self.sources if any(d.stable_versioned_opinion_key in keys for d in s.opinions))


class FakeRepository:
    def __init__(self,events,snapshot):self.events=events;self.value=snapshot;self.error=None
    @contextmanager
    def snapshot(self,scope):
        self.events.append('open')
        if self.error:raise self.error
        self.value.scope=self.value.scope or scope
        try:yield self.value
        finally:self.events.append('close')


def fixture():
    events=[];sources=[source(1),source(2)];snapshot=FakeSnapshot(events,sources)
    encoder=FakeEncoder(events);repo=FakeRepository(events,snapshot)
    service=SemanticRetrievalService(encoder=encoder,repository=repo,scope=deepcopy(OPTIONS))
    return service,encoder,repo,snapshot,events


class SemanticServiceTests(unittest.TestCase):
    def test_one_entry_accepts_model_dict_and_strict_json_consistently(self):
        service,_,_,_,_=fixture();query=dict(question='合成统一入口',top_k=1)
        values=[service.search(value).to_dict() for value in (query,SemanticQuery(**query),json.dumps(query))]
        self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])
        with self.assertRaisesRegex(SemanticRetrievalError,'invalid_retrieval_request'):
            service.search('{"question":"合成","top_k":1,"top_k":2}')

    def test_uniform_service_preserves_question_rank_source_context_and_serialization(self):
        service,encoder,_,snapshot,events=fixture();question='  合成问题：有没有制冷不足的反馈？  '
        result=service.search(SemanticQuery(question=question))
        self.assertIsInstance(result,SemanticResult)
        self.assertEqual(events,['encode','open','search','sources','close'])
        self.assertEqual(encoder.calls,[[question]])
        self.assertEqual([r.document.review_id for r in result.evidence],[2,1])
        self.assertEqual([r.rank for r in result.evidence],[1,2])
        self.assertEqual(snapshot.source_calls[0],tuple(m.document.stable_versioned_opinion_key for m in snapshot.matches))
        for row in result.evidence:
            self.assertEqual(row.review_text[row.document.quote_start:row.document.quote_end],row.document.quote)
            self.assertEqual(row.review_text_sha256,sha256(row.review_text.encode()).hexdigest())
            self.assertNotEqual(row.review_text_sha256,row.document.review_content_sha256)
            self.assertEqual(row.effective_snapshot_sha256,row.review_state.effective_snapshot_sha256)
        raw=result.to_dict();self.assertEqual(json.loads(json.dumps(raw,ensure_ascii=False)),raw)
        self.assertNotIn('base_url',json.dumps(raw));self.assertNotIn('offline.invalid',json.dumps(raw))
        self.assertEqual(raw['source_policy'],'current_effective_readonly_v1')
        self.assertNotIn('status',raw);self.assertNotIn('sufficient',raw)

    def test_empty_results_are_not_failures_or_proof_of_absence(self):
        service,_,_,snap,events=fixture();snap.matches=[]
        result=service.search(dict(question='合成空结果'))
        self.assertEqual(result.evidence,());self.assertEqual(snap.source_calls,[])
        self.assertEqual(events,['encode','open','search','close'])

    def test_explicit_filters_topk_and_full_source_snapshot_are_preserved(self):
        service,_,_,snap,_=fixture()
        result=service.search(dict(question='合成问题',top_k=1,aspect='effectiveness',sentiment='negative'))
        self.assertEqual(len(result.evidence),1);row=result.evidence[0]
        self.assertEqual((row.document.aspect,row.document.sentiment),('effectiveness','negative'))
        self.assertEqual(row.review_state.opinion_count,2) # Other effective opinion stays in snapshot.
        self.assertEqual(snap.search_calls[0]['top_k'],1);self.assertEqual(snap.search_calls[0]['sentiment'],'negative')

    def test_equal_distance_does_not_reorder_ties_or_deduplicate_reviews(self):
        service,_,_,snap,_=fixture();s=snap.sources[0]
        snap.matches=[OpinionMatch(s.opinions[1],0.2),OpinionMatch(s.opinions[0],0.2)]
        result=service.search(dict(question='合成问题'))
        self.assertEqual([r.key for r in result.evidence],[m.document.stable_versioned_opinion_key for m in snap.matches])
        self.assertEqual([r.document.review_id for r in result.evidence],[1,1])

    def test_distance_is_diagnostic_not_a_relevance_or_abstention_threshold(self):
        service,_,_,snap,_=fixture();snap.matches=[replace(snap.matches[0],cosine_distance=1.99)]
        result=service.search(dict(question='合成'))
        self.assertEqual(result.evidence[0].cosine_distance,1.99)
        self.assertNotIn('supported_target_fact_ids',result.to_dict())
        self.assertEqual(result.context_policy,'quote_full_review_veto')

    def test_bound_scope_is_copied_frozen_and_cannot_be_overridden_by_request(self):
        service,encoder,repo,_,events=fixture();options=deepcopy(OPTIONS)
        service=SemanticRetrievalService(encoder=encoder,repository=repo,scope=options)
        options['product_ids'][0]='OUTSIDE';self.assertEqual(service.scope.product_ids,('SYNTHETIC-P1',))
        with self.assertRaises(AttributeError):service.scope=RetrievalScope.model_validate_json(json.dumps(OPTIONS))
        with self.assertRaises(ValueError):service.scope.source='OUTSIDE'
        with self.assertRaisesRegex(SemanticRetrievalError,'invalid_retrieval_request'):
            service.search(dict(question='合成',source='OUTSIDE'))
        self.assertEqual(events,[])

    def test_bad_queries_filters_and_scope_fail_before_encoding_or_io(self):
        service,encoder,_,_,events=fixture()
        for updates in (dict(question=' '),dict(question=3),dict(question='合成',top_k=True),dict(question='合成',top_k=101),
                        dict(question='合成',aspect='cooling'),dict(question='合成',sentiment='mixed'),dict(question='合成',sentiment='all')):
            with self.subTest(updates=updates),self.assertRaisesRegex(SemanticRetrievalError,'invalid_retrieval_request'):service.search(updates)
        self.assertEqual(events,[]);self.assertEqual(encoder.calls,[])
        for updates in (dict(source=None),dict(dataset_version=' '),dict(product_ids=[]),dict(product_ids=['P1','P2']),dict(date_to='2030-01-01')):
            with self.subTest(updates=updates),self.assertRaisesRegex(SemanticRetrievalError,'invalid_retrieval_scope'):
                SemanticRetrievalService(encoder=encoder,repository=None,scope=OPTIONS|updates)

    def test_space_mismatch_prevents_encoding_and_encoder_drift_prevents_database(self):
        service,encoder,_,_,events=fixture();encoder.space=replace(SPACE,max_input_tokens=256)
        with self.assertRaisesRegex(SemanticRetrievalError,'encoder_embedding_space_mismatch'):service.search(dict(question='合成'))
        self.assertEqual(events,[])
        encoder.space=SPACE;encoder.callback=lambda:setattr(encoder,'space',replace(SPACE,model_revision='WRONG'))
        with self.assertRaisesRegex(SemanticRetrievalError,'encoder_embedding_space_mismatch'):service.search(dict(question='合成'))
        self.assertEqual(events,['encode'])

    def test_invalid_vectors_fail_without_repository_access(self):
        for vectors in ([],[VECTOR,VECTOR],[[0.0]*1024],[[float('nan')]*1024],[[1.0]],[[True]*1024]):
            service,encoder,_,_,events=fixture();encoder.vectors=vectors
            with self.subTest(size=len(vectors)),self.assertRaisesRegex(SemanticRetrievalError,'invalid_query_vector'):
                service.search(dict(question='合成'))
            self.assertEqual(events,['encode'])

    def test_backend_scope_space_builder_and_snapshot_drift_fail(self):
        for field,value,code in [('scope',None,'repository_scope_mismatch'),('embedding_space_id','WRONG','repository_embedding_space_mismatch'),
                                 ('text_builder_version','WRONG','repository_text_version_mismatch')]:
            service,_,_,snap,events=fixture()
            if field=='scope':value=service.scope.model_copy(update={'dataset_version':'OUTSIDE'})
            setattr(snap,field,value)
            with self.subTest(field=field),self.assertRaisesRegex(SemanticRetrievalError,code):service.search(dict(question='合成'))
            self.assertEqual(events[-1],'close')
        service,_,_,snap,_=fixture();snap.on_search=lambda:setattr(snap,'info',replace(INFO,transaction_snapshot='DIFFERENT'))
        with self.assertRaisesRegex(SemanticRetrievalError,'snapshot_identity_changed'):service.search(dict(question='合成'))

    def test_read_committed_or_writable_snapshot_is_not_accepted(self):
        for info in (replace(INFO,read_only=False),replace(INFO,isolation_level='read committed'),replace(INFO,observed_at='2031-01-01')):
            service,_,_,snap,_=fixture();snap.info=info
            with self.assertRaises(SemanticRetrievalError):service.search(dict(question='合成'))

    def test_candidate_metadata_cannot_replace_independent_source(self):
        for field,value in [('annotation_id',9000),('file_sha256','0'*64),('review_content_sha256','1'*64),('quote','伪造的合成观点')]:
            service,_,_,snap,_=fixture();first=snap.matches[0]
            snap.matches[0]=replace(first,document=replace(first.document,**{field:value}))
            with self.subTest(field=field),self.assertRaisesRegex(SemanticRetrievalError,'candidate_source_version_mismatch'):
                service.search(dict(question='合成'))

    def test_missing_source_duplicate_keys_and_filter_violation_are_not_silently_dropped(self):
        service,_,_,snap,_=fixture();snap.sources=[]
        with self.assertRaisesRegex(SemanticRetrievalError,'missing_source'):service.search(dict(question='合成'))
        service,_,_,snap,_=fixture();snap.matches.append(snap.matches[0])
        with self.assertRaisesRegex(SemanticRetrievalError,'duplicate_candidate_key'):service.search(dict(question='合成'))
        service,_,_,snap,_=fixture();snap.filter_results=False
        with self.assertRaisesRegex(SemanticRetrievalError,'candidate_filter_mismatch'):
            service.search(dict(question='合成',sentiment='negative'))

    def test_incomplete_full_opinion_set_count_and_snapshot_hash_are_rejected(self):
        for change in ('truncated','count','hash'):
            service,_,_,snap,_=fixture();s=snap.sources[0]
            snap.sources[0]=(replace(s,opinions=s.opinions[:1]) if change=='truncated' else
                replace(s,state=replace(s.state,opinion_count=99) if change=='count' else replace(s.state,effective_snapshot_sha256='0'*64)))
            with self.subTest(change=change),self.assertRaisesRegex(SemanticRetrievalError,'effective_snapshot_mismatch'):
                service.search(dict(question='合成'))

    def test_invalid_quote_span_context_and_embedding_hash_fail(self):
        for change in ('span','body','hash'):
            service,_,_,snap,_=fixture();s=snap.sources[0]
            if change=='body':s=replace(s,review_text='合成不同正文')
            else:
                first=replace(s.opinions[0],**({'quote_end':9999} if change=='span' else {'embedding_input_sha256':'0'*64}))
                s=replace(s,opinions=(first,*s.opinions[1:]))
            snap.sources[0]=s
            with self.subTest(change=change),self.assertRaisesRegex(SemanticRetrievalError,'source_document_hash_or_span_mismatch'):
                service.search(dict(question='合成'))

    def test_cross_dataset_product_and_date_sources_fail_scope_check(self):
        for fields in (dict(source='OUTSIDE'),dict(dataset_version='OUTSIDE'),dict(product_id='P2'),dict(published_at='2031-01-03T00:00:00+00:00')):
            service,_,_,snap,_=fixture();s=snap.sources[0];snap.sources[0]=replace(s,state=replace(s.state,**fields))
            with self.subTest(fields=fields),self.assertRaisesRegex(SemanticRetrievalError,'source_scope_mismatch'):
                service.search(dict(question='合成'))

    def test_label_profile_and_builder_conflicts_in_source_are_rejected(self):
        for fields,code in [(dict(label_profile_key='0'*64),'source_label_profile_mismatch'),(dict(text_builder_version='WRONG'),'source_text_version_mismatch')]:
            service,_,_,snap,_=fixture();s=snap.sources[0]
            snap.sources[0]=replace(s,opinions=(replace(s.opinions[0],**fields),*s.opinions[1:]))
            with self.assertRaisesRegex(SemanticRetrievalError,code):service.search(dict(question='合成'))

    def test_human_revision_identity_is_preserved(self):
        service,_,_,snap,_=fixture();s=source(1,human=True);snap.sources=[s];snap.matches=[OpinionMatch(s.opinions[0],0.3)]
        row=service.search(dict(question='合成')).evidence[0]
        self.assertEqual(row.document.opinion_source,'human');self.assertEqual(row.document.human_revision_id,701)
        self.assertIsNone(row.document.model_opinion_id);self.assertEqual(row.document.human_opinion_id,910)

    def test_conflicting_import_and_human_revision_state_fail(self):
        service,_,_,snap,_=fixture();s=snap.sources[0]
        snap.sources[0]=replace(s,opinions=(s.opinions[0],replace(s.opinions[1],line_number=99)))
        with self.assertRaisesRegex(SemanticRetrievalError,'conflicting_review_import_identity'):service.search(dict(question='合成'))
        service,_,_,snap,_=fixture();s=source(1,human=True)
        snap.sources=[replace(s,state=replace(s.state,human_status='unreviewed'))];snap.matches=[OpinionMatch(s.opinions[0],0.3)]
        with self.assertRaisesRegex(SemanticRetrievalError,'source_human_revision_state_mismatch'):service.search(dict(question='合成'))

    def test_utc_end_day_and_asof_boundary_are_checked(self):
        service,enc,repo,snap,_=fixture()
        narrow=OPTIONS|dict(date_to='2031-01-01',as_of='2031-01-01T23:59:59.999Z')
        service=SemanticRetrievalService(encoder=enc,repository=repo,scope=narrow)
        snap.sources=[source(1)];snap.matches=[OpinionMatch(snap.sources[0].opinions[0],0.2)]
        self.assertEqual(len(service.search(dict(question='合成')).evidence),1)
        snap.sources=[source(1,day='2031-01-02T00:00:00+00:00')]
        snap.matches=[OpinionMatch(snap.sources[0].opinions[0],0.2)]
        with self.assertRaisesRegex(SemanticRetrievalError,'source_scope_mismatch'):service.search(dict(question='合成'))

    def test_errors_are_safe_not_empty_and_resources_are_closed(self):
        for stage in ('encoder','open','search','sources'):
            service,enc,repo,snap,events=fixture();error=RuntimeError('PRIVATE DSN quote key')
            if stage=='encoder':enc.error=error
            elif stage=='open':repo.error=error
            elif stage=='search':snap.search_error=error
            else:snap.source_error=error
            with self.subTest(stage=stage),self.assertRaises(SemanticRetrievalError) as caught:service.search(dict(question='合成'))
            self.assertNotIn('PRIVATE',str(caught.exception))
            self.assertEqual(caught.exception.code,'embedding_failed' if stage=='encoder' else 'repository_failed')
            if stage in ('search','sources'):self.assertEqual(events[-1],'close')


class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class FakePG(PostgresConnection):
    def __init__(self,*,active=False,readonly='on',isolation='repeatable read'):
        self.active=active;self.readonly=readonly;self.isolation=isolation;self.commands=[];self.rollbacks=self.closes=0
    @property
    def in_transaction(self):return self.active
    def execute(self,query,parameters=None):
        self.commands.append(query)
        if query.startswith('BEGIN'):self.active=True
        if query=='SHOW transaction_read_only':return Cursor((self.readonly,))
        if query=='SHOW transaction_isolation':return Cursor((self.isolation,))
        return Cursor(('SYNTHETIC-SNAPSHOT',datetime(2031,2,1,tzinfo=timezone.utc)))
    def rollback(self):self.rollbacks+=1;self.active=False
    def close(self):self.closes+=1
    def commit(self):raise AssertionError('service cannot commit')


class PostgresAdapterTests(unittest.TestCase):
    def setup_adapter(self,connection=None):
        events=[];enc=FakeEncoder(events);conn=connection or FakePG();sources=[source(1),source(2)]
        repo=PostgresSemanticRepository('postgresql://synthetic.invalid/synthetic')
        service=SemanticRetrievalService(encoder=enc,repository=repo,scope=OPTIONS)
        corpus=RagCorpus(analysis_scope(**service.scope.options())[3],sources[0].opinions[0].label_profile_key,
            tuple(d for s in sources for d in s.opinions),tuple(s.state for s in sources))
        matches=(OpinionMatch(sources[1].opinions[0],0.1),OpinionMatch(sources[0].opinions[0],0.2))
        def context(db,scope,ids):
            self.assertIs(db,conn)
            rows=[]
            for s in sources:
                if s.state.review_id not in ids:continue
                d=s.opinions[0]
                row={k:getattr(d,k) for k in ('source','dataset_version','product_id','review_id','published_at','file_sha256','line_number','import_run_id')}
                row.update(text=s.review_text,content_sha256=d.review_content_sha256)
                rows.append(row)
            return rows
        return service,conn,corpus,matches,context,enc

    def test_production_adapter_reuses_search_and_source_functions_in_one_readonly_snapshot(self):
        service,conn,corpus,matches,context,enc=self.setup_adapter()
        with patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',return_value=conn) as connect, \
             patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',return_value=matches) as search, \
             patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus) as read_corpus, \
             patch('data_analysis_agent.reviews.semantic_retrieval.load_review_contexts',side_effect=context):
            result=service.search(dict(question='合成'))
        connect.assert_called_once_with('postgresql://synthetic.invalid/synthetic',readonly=True)
        self.assertEqual(conn.commands[0],'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        self.assertEqual((conn.rollbacks,conn.closes),(1,1))
        self.assertIs(search.call_args.args[0],conn);self.assertEqual(search.call_args.kwargs['embedding_space_id'],SPACE_ID)
        self.assertEqual(read_corpus.call_args.args,(conn,));self.assertEqual([r.document.review_id for r in result.evidence],[2,1])

    def test_shared_transaction_is_verified_and_never_closed_or_committed(self):
        conn=FakePG(active=True);repo=PostgresSemanticRepository(conn)
        scope=SemanticRetrievalService(encoder=None,repository=None,scope=OPTIONS).scope
        with repo.snapshot(scope) as snap:self.assertIs(snap.connection,conn)
        self.assertFalse(any(q.startswith('BEGIN') for q in conn.commands));self.assertEqual((conn.rollbacks,conn.closes),(0,0))
        for updates in (dict(active=False),dict(active=True,readonly='off'),dict(active=True,isolation='read committed')):
            conn=FakePG(**updates)
            with self.subTest(updates=updates),self.assertRaises(SemanticRetrievalError):
                with PostgresSemanticRepository(conn).snapshot(scope):self.fail('unsafe snapshot yielded')

    def test_owned_transaction_rolls_back_and_closes_on_failure(self):
        service,conn,_,_,_,_=self.setup_adapter()
        with patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',return_value=conn), \
             patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',side_effect=RuntimeError('PRIVATE DB failure')):
            with self.assertRaisesRegex(SemanticRetrievalError,'repository_failed'):service.search(dict(question='合成'))
        self.assertEqual((conn.rollbacks,conn.closes),(1,1))

    def test_non_postgres_target_is_rejected_before_connect(self):
        scope=SemanticRetrievalService(encoder=None,repository=None,scope=OPTIONS).scope
        with patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',side_effect=AssertionError('must not connect')):
            with self.assertRaisesRegex(SemanticRetrievalError,'postgres_required'):
                with PostgresSemanticRepository('synthetic.sqlite').snapshot(scope):self.fail('not PostgreSQL')

    def test_source_corpus_scope_mismatch_fails_before_context_read(self):
        service,conn,corpus,matches,_,_=self.setup_adapter()
        corpus=replace(corpus,scope={**corpus.scope,'dataset_version':'OUTSIDE'})
        with patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',return_value=conn), \
             patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',return_value=matches), \
             patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus), \
             patch('data_analysis_agent.reviews.semantic_retrieval.load_review_contexts',side_effect=AssertionError('must not hydrate outside scope')):
            with self.assertRaisesRegex(SemanticRetrievalError,'repository_scope_mismatch'):service.search(dict(question='合成'))

    def test_independent_context_version_conflict_fails_whole_result(self):
        service,conn,corpus,matches,context,_=self.setup_adapter()
        def stale(db,scope,ids):
            value=context(db,scope,ids);value[0]['content_sha256']='0'*64;return value
        with patch('data_analysis_agent.reviews.semantic_retrieval.connect_review_database',return_value=conn), \
             patch('data_analysis_agent.reviews.semantic_retrieval.search_effective_opinions',return_value=matches), \
             patch('data_analysis_agent.reviews.semantic_retrieval.read_effective_corpus',return_value=corpus), \
             patch('data_analysis_agent.reviews.semantic_retrieval.load_review_contexts',side_effect=stale):
            with self.assertRaisesRegex(SemanticRetrievalError,'review_context_version_mismatch'):service.search(dict(question='合成'))
        self.assertEqual((conn.rollbacks,conn.closes),(1,1))


if __name__=='__main__':unittest.main()
