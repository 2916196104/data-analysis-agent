"""P7-C isolated synthetic integration. Never real BGE-M3, PostgreSQL or paid LLM."""
from contextlib import closing
from copy import deepcopy
import csv
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_agent import SETTINGS
from test_review_workbench import WorkbenchFixture, IDENTITY
from test_semantic_retrieval import FakeEncoder, FakeRepository, FakeSnapshot
from data_analysis_agent.api.app import create_app
from data_analysis_agent.agents.review_retrieval import validate_retrieval
from data_analysis_agent.agents.review_validation import validate_report
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.jobs.parameters import AnalysisParameters
from data_analysis_agent.jobs.store import (
    TaskConflict, init_tasks, claim_task, get_task, worker_started, queue_health,
    analysis_result_integrity, connect_task_database,
)
from data_analysis_agent.jobs.worker import TaskWorker, WorkerLock
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource
from data_analysis_agent.reviews.embedding_store import OpinionMatch
from data_analysis_agent.reviews.store import import_amazon_jsonl, connect_review_database
from data_analysis_agent.reviews.parameters import BaselineScope

QUESTION = '商品100013588055有没有送货慢的反馈？'


class RagP7CTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        records = [
            ('送货很慢。', [('送货很慢', 'negative')]),
            ('商品到货时间太长了，等了八天才到货。物流太慢了。',
             [('商品到货时间太长了，等了八天才到货', 'negative'), ('物流太慢了', 'negative')]),
            ('送货到时挺快的。', [('送货到时挺快的', 'positive')]),
            ('物流送货态度太差。', [('物流送货态度太差', 'negative')]),
        ]
        path = self.root/'p7c-synthetic.csv'
        with path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['product_id','user_id','text','published_at','product_name'])
            writer.writeheader()
            writer.writerows(dict(product_id='100013588055',user_id=f'p7c-SYNTHETIC-{i}',text=text,
                published_at=f'2022-07-{i+1:02}',product_name='SYNTHETIC P7-C TEST PRODUCT')
                for i,(text,_) in enumerate(records))
        import_amazon_jsonl(self.db,reviews_path=path,dataset_version='reviews-csv')
        quotes = dict(records)
        for review in pending_reviews(self.db,**IDENTITY,source='reviews_csv',limit=100):
            opinions = [dict(aspect='logistics',sentiment=sentiment,quote=quote) for quote,sentiment in quotes[review['text']]]
            save_annotation(self.db,review,**IDENTITY,status='success',
                opinions=validate_opinions(json.dumps({'opinions':opinions}),review['text']))
        self.scope = AnalysisParameters.model_validate(dict(**IDENTITY,source='reviews_csv',dataset_version='reviews-csv',
            product_ids=['100013588055'],date_from='2021-09-11',date_to='2022-08-26',
            baseline_from='2021-09-10',baseline_to='2021-09-10')).model_dump()
        self.selection = {k:self.scope[k] for k in BaselineScope.model_fields}
        self.tasks = self.root/'p7c-SYNTHETIC-tasks.sqlite3'
        init_tasks(self.tasks,self.db)
        self.worker = TaskWorker(self.db,self.tasks,import_root=self.root,env_file=self.root/'offline.env')
        self.addCleanup(self.worker.stop)
        worker_started(self.tasks,self.worker.worker_id)
        self.identity = dict(model=SETTINGS.model,base_url=SETTINGS.base_url,prompt_version='p7c-offline')
        self.settings = replace(SETTINGS,rag_model_cache='SYNTHETIC-CACHE',rag_python='SYNTHETIC-PYTHON')
        for name in ('data_analysis_agent.jobs.worker.configured_agent_identity',
                     'data_analysis_agent.jobs.submission.configured_agent_identity'):
            self.enterContext(patch(name,return_value=(self.settings,self.identity)))
        self.pg = self.enterContext(patch('psycopg.connect',side_effect=AssertionError('P7-C forbids real PostgreSQL')))
        self.llm = self.enterContext(patch('data_analysis_agent.agents.reviews.create_llm',side_effect=AssertionError('P7-C forbids planning LLM')))
        self.openai = self.enterContext(patch('openai.OpenAI',side_effect=AssertionError('P7-C forbids paid LLM')))
        self.enterContext(patch('openai.AsyncOpenAI',side_effect=AssertionError('P7-C forbids paid LLM')))
        self.enterContext(patch('data_analysis_agent.agents.review_retrieval.LocalBgeM3Embedder',side_effect=AssertionError('P7-C forbids real model')))
        self.events=[]
        self.encoder=FakeEncoder(self.events)
        self.process_factory=self.enterContext(patch('data_analysis_agent.reviews.embedding_process.shared_embedding_process',return_value=self.encoder))
        options={k:v for k,v in self.scope.items() if k in ('source','dataset_version','product_ids','date_from','date_to','as_of','model','base_url','prompt_version')}
        corpus=read_effective_corpus(self.db,**options)
        with closing(connect_review_database(self.db,readonly=True)) as con:
            texts={r['id']:r['text'] for r in con.execute('SELECT id,text FROM reviews')}
        sources=[ReviewSource(state,tuple(d for d in corpus.documents if d.review_id==state.review_id),texts[state.review_id])
                 for state in corpus.reviews]
        self.snapshot=FakeSnapshot(self.events,sources)
        ordered=['送货很慢','物流太慢了','送货到时挺快的','物流送货态度太差','商品到货时间太长了，等了八天才到货']
        by_quote={d.quote:d for d in corpus.documents}
        self.snapshot.matches=[OpinionMatch(by_quote[q],.1+i*.03) for i,q in enumerate(ordered)]
        self.repository=FakeRepository(self.events,self.snapshot)
        self.connections=[]
        def bind(connection):
            self.assertFalse(connection.in_transaction, 'cold loading/encoding must occur outside a transaction')
            self.connections.append(connection)
            return self.repository
        self.repo_factory=self.enterContext(patch('data_analysis_agent.agents.review_retrieval.PostgresSemanticRepository',side_effect=bind))
        from data_analysis_agent.reviews.monitor_store import init_monitoring
        init_monitoring(self.db)
        self.business_hash=hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.client=self.enterContext(TestClient(create_app(self.db,self.tasks,import_root=self.root,
            env_file=self.root/'offline.env',embedded_worker=False),base_url='http://127.0.0.1',raise_server_exceptions=False))

    def submit(self,key='p7c-offline-once',question=QUESTION):
        body=dict(kind='analysis',parameters={**self.scope,'question':question,'conversation_id':'p7c-SYNTHETIC-conversation',
                                            'selection_scope':self.selection})
        response=self.client.post('/api/v1/tasks',json=body,headers={'Idempotency-Key':key})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['result']

    def execute(self):
        queued=self.submit()
        self.worker._execute(claim_task(self.tasks,self.worker.worker_id))
        task=get_task(self.tasks,queued['task']['task_id'])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),self.business_hash)
        for connection in self.connections:
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')
        self.pg.assert_not_called();self.llm.assert_not_called();self.openai.assert_not_called()
        return task

    def successful(self):
        task=self.execute();self.assertEqual(task['status'],'succeeded',task.get('error'))
        self.assertEqual(task['result']['validation']['status'],'passed')
        return task

    def export_state(self,name,task):
        target=os.environ.get('P7C_OFFLINE_ARTIFACT')
        if target:Path(target).with_name(name+'.json').write_text(json.dumps(task,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    def test_api_worker_agent_validator_public_json_and_integrity(self):
        task=self.successful();r=task['result']
        self.assertEqual(r['user_intent']['query_type'],'evidence')
        self.assertEqual(r['query_plan']['analysis_type'],'semantic_evidence')
        self.assertEqual(r['query_plan']['required_tools'],['search_semantic_evidence'])
        self.assertEqual(r['total_model_calls'],0)
        self.assertEqual(r['answer_generation']['strategy'],'template')
        self.assertNotIn('task_acceptance',r)
        self.assertEqual(self.encoder.calls,[[QUESTION]])
        self.assertEqual(len(self.snapshot.search_calls),1)
        self.assertEqual(self.snapshot.search_calls[0]['top_k'],5)
        self.assertEqual(task['parameters']['conversation_id'],'p7c-SYNTHETIC-conversation')
        self.assertEqual(task['progress']['analysis_integrity'],analysis_result_integrity(task['task_id'],task['status'],task['parameters'],r))
        detail=self.client.get('/api/v1/tasks/'+task['task_id']).json()['result']
        public=self.client.get('/api/v1/public/tasks/'+task['task_id']).json()['result']
        self.assertEqual(detail['result']['public_answer'],r['public_answer'])
        self.assertEqual(public['public_answer'],r['public_answer'])
        refs=[o for ref in public['public_answer']['evidence_refs'] for o in ref['opinions']]
        self.assertEqual(len(refs),3)
        self.assertNotIn('送货到时挺快的',r['answer'])
        self.assertNotIn('物流送货态度太差',r['answer'])
        self.assertIn('不作为统计样本',public['public_answer']['sample_note'])
        for ref in public['public_answer']['evidence_refs']:
            for opinion in ref['opinions']:
                row=next(e for e in r['results']['r1']['data']['retrieval']['evidence'] if e['key']==opinion['opinion_key'])
                self.assertEqual(row['review_text'][opinion['quote_start']:opinion['quote_end']],opinion['quote'])
                self.assertEqual(row['annotation_id'],opinion['annotation_id'])
        export=os.environ.get('P7C_OFFLINE_ARTIFACT')
        if export:
            Path(export).write_text(json.dumps(dict(proof='SYNTHETIC_SQLITE_FAKE_ENCODER_AND_REPOSITORY_NOT_REAL_POSTGRESQL',
                task_native=task,task_http=detail,public_http=public),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    def test_duplicate_submission_is_idempotent_and_changed_payload_conflicts(self):
        one=self.submit();two=self.submit()
        self.assertEqual(one['task']['task_id'],two['task']['task_id']);self.assertTrue(two['reused'])
        self.assertEqual(self.encoder.calls,[])
        response=self.client.post('/api/v1/tasks',json=dict(kind='analysis',parameters={**self.scope,
            'question':'商品100013588055有没有送货快的反馈？','conversation_id':'p7c-SYNTHETIC-conversation','selection_scope':self.selection}),
            headers={'Idempotency-Key':'p7c-offline-once'})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(queue_health(self.tasks)['counts']['queued'],1)

    def test_encoder_failure_and_timeout_do_not_search_or_retry(self):
        for error in (RuntimeError('SYNTHETIC encoder error'),TimeoutError('SYNTHETIC timeout')):
            with self.subTest(error=type(error).__name__):
                self.encoder.error=error
                queued=self.submit(key=type(error).__name__)
                self.worker._execute(claim_task(self.tasks,self.worker.worker_id))
                task=get_task(self.tasks,queued['task']['task_id'])
                self.assertEqual(task['status'],'failed')
                self.assertEqual(task['error']['message'],'embedding_failed')
                self.assertEqual(self.snapshot.search_calls,[])
                self.assertEqual(task['result']['public_answer']['evidence_refs'],[])
                self.export_state('offline-encoding-failed',task)
        self.assertEqual(len(self.encoder.calls),2)
        self.assertEqual(queue_health(self.tasks)['counts']['queued'],0)

    def test_repository_error_closes_snapshot_connection_and_publishes_no_answer(self):
        self.snapshot.search_error=RuntimeError('SYNTHETIC private repository error')
        task=self.execute();self.assertEqual(task['status'],'failed')
        self.assertIn('close',self.events)
        self.assertNotIn('answer',task['result'])
        self.assertEqual(task['result']['public_answer']['evidence_refs'],[])

    def test_wrong_source_annotation_rejected_without_answer(self):
        first=self.snapshot.matches[0]
        self.snapshot.matches[0]=OpinionMatch(replace(first.document,annotation_id=999999),first.cosine_distance)
        task=self.execute();self.assertEqual(task['status'],'failed')
        self.assertEqual(task['result']['public_answer']['evidence_refs'],[])

    def test_empty_retrieval_never_claims_no_slow_delivery_in_population(self):
        self.snapshot.matches=[]
        task=self.successful();answer=task['result']['public_answer']
        self.assertEqual(answer['evidence_refs'],[])
        self.assertIn('本次检索没有返回候选证据',answer['summary'])
        self.assertIn('不表示全量评论不存在相关反馈',answer['summary'])
        self.export_state('offline-empty',task)

    def test_non_supporting_top_candidates_are_not_licensed(self):
        self.snapshot.matches=self.snapshot.matches[2:4]
        task=self.successful()
        self.assertEqual(task['result']['results']['r1']['data']['assessment']['permitted_keys'],[])
        self.assertEqual(task['result']['public_answer']['evidence_refs'],[])

    def test_native_validator_rejects_tampered_quote_and_permission(self):
        task=self.successful();r=task['result']
        from data_analysis_agent.agents.review_plan import build_query_plan
        for field in ('span','permission'):
            changed=deepcopy(r['results'])
            data=changed['r1']['data']
            if field=='span':data['retrieval']['evidence'][0]['quote_start']=999
            else:data['assessment']['permitted_keys'].append(data['retrieval']['evidence'][2]['key'])
            self.assertFalse(validate_retrieval(data,QUESTION))
            validation=validate_report(changed,r['result_ids'],expected_scope=r['scope'],query_plan=build_query_plan(QUESTION,r['scope']))
            self.assertEqual(validation['status'],'failed')

    def test_public_api_refuses_corrupted_stored_source_reference(self):
        task=self.successful();changed=deepcopy(task['result'])
        changed['results']['r1']['data']['retrieval']['evidence'][0]['quote_end']=999
        with closing(connect_task_database(self.tasks)) as con,con:
            con.execute('UPDATE tasks SET result_json=? WHERE task_id=?',(json.dumps(changed),task['task_id']))
        response=self.client.get('/api/v1/public/tasks/'+task['task_id'])
        self.assertEqual(response.status_code,200,response.text)
        public=response.json()['result']
        self.assertEqual(public['public_analysis']['status'],'failed')
        self.assertEqual(public['public_answer']['evidence_refs'],[])

    def test_restarted_worker_marks_abandoned_task_failed_without_retry(self):
        queued=self.submit();claim_task(self.tasks,self.worker.worker_id)
        self.worker.lock.acquire()
        self.worker.lock.release()
        replacement=WorkerLock(self.db);replacement.acquire()
        try:worker_started(self.tasks,'SYNTHETIC-replacement-worker')
        finally:replacement.release()
        task=get_task(self.tasks,queued['task']['task_id'])
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['error']['code'],'worker_interrupted')
        self.assertEqual(queue_health(self.tasks)['counts']['queued'],0)
        self.assertEqual(self.encoder.calls,[])

    def test_slow_cold_embedding_keeps_native_heartbeat_lock_and_running_task(self):
        entered=threading.Event();release=threading.Event()
        def wait():
            entered.set()
            if not release.wait(12):raise TimeoutError('SYNTHETIC cold wait')
        self.encoder.callback=wait
        self.worker.start()
        self.addCleanup(release.set)
        queued=self.submit()
        self.assertTrue(entered.wait(3))
        initial=queue_health(self.tasks)['worker']['heartbeat_at']
        deadline=time.monotonic()+7
        while time.monotonic()<deadline and queue_health(self.tasks)['worker']['heartbeat_at']==initial:
            time.sleep(.05)
        self.assertNotEqual(queue_health(self.tasks)['worker']['heartbeat_at'],initial)
        self.assertEqual(get_task(self.tasks,queued['task']['task_id'])['status'],'running')
        other=WorkerLock(self.db)
        with self.assertRaises(TaskConflict):other.acquire()
        release.set()
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and get_task(self.tasks,queued['task']['task_id'])['status']=='running':time.sleep(.05)
        self.assertEqual(get_task(self.tasks,queued['task']['task_id'])['status'],'succeeded')
        self.worker.stop()
        self.assertIsNotNone(queue_health(self.tasks)['worker']['stopped_at'])
        other.acquire();other.release()
        self.assertEqual(self.encoder.calls,[[QUESTION]])


if __name__=='__main__':unittest.main()
