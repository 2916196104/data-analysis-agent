"""S2 preflight only: positive quotas with synthetic side effects, no real IO."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(os.environ['F01_OUTPUT_DIR'])
temporary=tempfile.TemporaryDirectory(prefix='OFFLINE-F01-',dir=OUT);CASE=Path(temporary.name)
os.environ['P7C_REAL_DIR']=str(CASE)
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
                  LLM_MODEL_ID='OFFLINE-F01-AGENT',LLM_API_KEY='SYNTHETIC-NOT-A-SECRET',LLM_BASE_URL='http://127.0.0.1:1')
fixture=ROOT/'frontend/src/features/agent/__fixtures__/p7c-s2-native-post.json'
body_path=Path(os.environ.get('F01_CAPTURE_POST_PATH',str(fixture)))
NATIVE_BODY=json.loads(body_path.read_text(encoding='utf-8'))
scope=dict(source='reviews_csv',dataset_version='reviews-csv',product_ids=['100013588055'],
           date_from='2021-09-11',date_to='2022-08-26',timezone='UTC')
limits=dict(http_submission=1,submission=1,worker_task=1,query_embedding=1,vector_search=1,
            model_initialize=1,model_encode_rpc=1,model_process_create=1)
(CASE/'authorization.json').write_text(json.dumps(dict(stage='P7-C-S2',approved=True,
    question=NATIVE_BODY['parameters']['question'],scope=scope,limits=limits,observation_seconds=900,
    paid_llm=0,vector_sync=0,pg_business_writes=0,automatic_retries=0,
    proof='SYNTHETIC_TEST_ONLY_NOT_LIVE_AUTHORIZATION')),encoding='utf-8')
(CASE/'offline.env').write_text('',encoding='utf-8')
sys.path[:0]=[str(Path(__file__).parent),str(ROOT/'src')]
def offline_audit(event,args):
    if event=='subprocess.Popen':raise AssertionError('F01 forbids service/model subprocesses')
    if event=='socket.connect':
        address=args[1]
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1') or address[1] in (5432,6379):
            raise RuntimeError('F01 forbids real network/database IO')
import sqlite3
from data_analysis_agent.api.schemas import AnalysisParameters
from data_analysis_agent.reviews.parameters import BaselineScope
from data_analysis_agent.jobs.submission import prepare_parameters
from data_analysis_agent.reviews.scope import review_scope
def native_parameters(body):
    return prepare_parameters('analysis',AnalysisParameters.model_validate(body['parameters']).model_dump(),
                              import_root=CASE,env_file=CASE/'offline.env')
import psycopg
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.jobs import store
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.reviews import embedding_process,embedding_store
import s2_guard as guard

EFFECTS=[]
VECTOR=[1.0]+[0.0]*1023
class Raw:
    def __init__(self):self.info=SimpleNamespace(backend_pid=902,transaction_status=psycopg.pq.TransactionStatus.IDLE);self.closed=False
    def execute(self,sql,p=None):EFFECTS.append(('sql',sql));return 'SYNTHETIC_SQL'
    def close(self):self.closed=True
def fake_connect(*a,**kw):EFFECTS.append(('connect',a,kw));return Raw()
NATIVE_ENQUEUE=store.enqueue
def fake_enqueue(*a,**kw):
    EFFECTS.append(('enqueue',a,kw))
    if a[0]=='SYNTHETIC':return dict(task=dict(task_id='SYNTHETIC-S2'),reused=False)
    return NATIVE_ENQUEUE(*a,**kw)
def fake_task(*a,**kw):EFFECTS.append(('worker',));return 'SYNTHETIC_WORKER'
def fake_embed(*a,**kw):EFFECTS.append(('embedding',));return [VECTOR]
def fake_rpc(*a,**kw):EFFECTS.append(('rpc',));return dict(status='ready',model_load_ms=1,metrics={})
def fake_init(*a,**kw):EFFECTS.append(('model_init',))
patches=[patch('psycopg.connect',side_effect=fake_connect),patch.object(store,'enqueue',fake_enqueue),
    patch.object(TaskWorker,'_execute',fake_task),patch.object(embedding_process.PersistentEmbeddingProcess,'embed_texts',fake_embed),
    patch.object(embedding_process.PersistentEmbeddingProcess,'_rpc',fake_rpc),
    patch.object(embedding_process.PersistentEmbeddingProcess,'__init__',fake_init)]
for p in patches:p.start()
guard.install()
sys.addaudithook(offline_audit)
base=guard.base

class S2Tests(unittest.TestCase):
    def setUp(self):
        base.COUNTS=dict.fromkeys(guard.LIMITS,0);base.STARTED=None;base.CURRENT.task_id=None
        (CASE/'execution-armed.json').write_text('{}')
    def reject(self,fn):
        before=len(EFFECTS)
        with self.assertRaisesRegex(RuntimeError,'p7c_|S2_authorization'):fn()
        self.assertEqual(len(EFFECTS),before)
    def parameters(self):return native_parameters(NATIVE_BODY)
    def test_independent_S2_authorization_and_limits(self):
        value=json.loads((CASE/'authorization.json').read_text(encoding='utf-8'))
        guard.validate_authorization(value)
        for changed in ({**value,'stage':'P7-C-S1'},{**value,'approved':False},{**value,'limits':dict.fromkeys(guard.LIMITS,0)},
                        {**value,'question':'changed'},{**value,'paid_llm':1}):self.reject(lambda:guard.validate_authorization(changed))
    def test_unarmed_gate_rejects(self):
        (CASE/'execution-armed.json').unlink();self.reject(lambda:base.take('submission'))
    def test_each_quota_is_one_shot_before_effect(self):
        for kind in guard.LIMITS:
            base.take(kind);self.reject(lambda:base.take(kind));self.assertEqual(base.COUNTS[kind],1)
    def test_concurrent_gate_is_atomic(self):
        def attempt(_):
            try:base.take('query_embedding');return 1
            except RuntimeError:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(attempt,range(24))),1)
    def test_900_second_deadline_rejects(self):
        import time
        base.STARTED=time.monotonic()-901;self.reject(lambda:base.take('submission'))
    def test_fixed_scope_question_and_history_rejected_before_admission(self):
        p=self.parameters()
        for change in ({'question':'changed'},{'product_ids':['other']},{'source':'other'},{'date_to':'2023-01-01'},
                       {'context_task_id':'old'},{'baseline_from':'2020-01-01'}):
            self.reject(lambda:store.enqueue('SYNTHETIC','analysis',{**p,**change},idempotency_key='SYNTHETIC'))
        self.assertEqual(base.COUNTS['submission'],0)
    def test_native_submission_and_worker_allow_once(self):
        p=self.parameters();value=store.enqueue('SYNTHETIC','analysis',p,idempotency_key='SYNTHETIC')
        self.assertEqual(value['task']['task_id'],'SYNTHETIC-S2')
        self.reject(lambda:store.enqueue('SYNTHETIC','analysis',p,idempotency_key='SYNTHETIC'))
        task=dict(kind='analysis',parameters=p,task_id='SYNTHETIC-S2')
        self.assertEqual(TaskWorker._execute(object(),task),'SYNTHETIC_WORKER')
        self.reject(lambda:TaskWorker._execute(object(),task))
        self.assertIsNone(base.CURRENT.task_id)
    def test_embedding_preserves_full_original_question_and_no_repeat(self):
        base.CURRENT.task_id='SYNTHETIC-S2'
        client=SimpleNamespace(last_metrics={},process=SimpleNamespace(pid=123))
        self.reject(lambda:embedding_process.PersistentEmbeddingProcess.embed_texts(client,['changed']))
        self.assertEqual(embedding_process.PersistentEmbeddingProcess.embed_texts(client,[base.QUESTION]),[VECTOR])
        self.reject(lambda:embedding_process.PersistentEmbeddingProcess.embed_texts(client,[base.QUESTION]))
    def test_encoding_rpc_and_model_load_each_once(self):
        base.CURRENT.task_id='SYNTHETIC-S2';base.COUNTS['query_embedding']=1
        client=SimpleNamespace(process=SimpleNamespace(pid=123))
        rpc=embedding_process.PersistentEmbeddingProcess._rpc
        rpc(client,dict(operation='initialize'),999999999)
        self.reject(lambda:rpc(client,dict(operation='initialize'),999999999))
        rpc(client,dict(operation='encode',texts=[base.QUESTION]),999999999)
        self.reject(lambda:rpc(client,dict(operation='encode',texts=[base.QUESTION]),999999999))
        self.reject(lambda:rpc(client,dict(operation='health'),999999999))
    def test_model_process_fixed_local_paths_and_once(self):
        base.CURRENT.task_id='SYNTHETIC-S2'
        ctor=embedding_process.PersistentEmbeddingProcess.__init__
        python=ROOT/'.cache/embedding-eval/venv/Scripts/python.exe';cache=ROOT/'.cache/embedding-eval/hf/hub'
        self.reject(lambda:ctor(object(),'other',cache))
        ctor(object(),python,cache)
        self.reject(lambda:ctor(object(),python,cache))
    def test_readonly_connection_and_wrong_addresses(self):
        for dsn in ('postgresql://postgres@127.0.0.1:5432/reviews','postgresql://rag_audit_ro@192.0.2.1:5432/reviews',
                    'postgresql://rag_audit_ro@127.0.0.1:5432/reviews?hostaddr=192.0.2.1'):
            self.reject(lambda:PostgresConnection(dsn))
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews',readonly=False,namespace='worker')
        try:self.assertTrue(con.readonly);self.assertEqual(con.execute('SELECT 1'),'SYNTHETIC_SQL')
        finally:con.close()
    def test_top5_sql_once_and_exact_limit(self):
        base.CURRENT.task_id='SYNTHETIC-S2'
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        try:
            self.reject(lambda:con.execute('SELECT embedding <=> ? LIMIT ?',(json.dumps(VECTOR),6)))
            self.assertEqual(con.execute('SELECT embedding <=> ? LIMIT ?',(json.dumps(VECTOR),5)),'SYNTHETIC_SQL')
            self.reject(lambda:con.execute('SELECT embedding <=> ? LIMIT ?',(json.dumps(VECTOR),5)))
        finally:con.close()
    def test_no_writes_or_sync(self):
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        try:
            for sql in ('INSERT INTO reviews VALUES (1)','BEGIN READ WRITE',"SELECT set_config('default_transaction_read_only','off',false)"):
                self.reject(lambda:con.execute(sql))
        finally:con.close()
        self.reject(lambda:embedding_store.sync_effective_opinions('SYNTHETIC',options={},execute=True))
        self.reject(lambda:embedding_store.migrate_embedding_store('SYNTHETIC'))
    def test_paid_sdk_and_http_remain_blocked(self):
        import openai,httpx,http.client
        self.reject(lambda:openai.OpenAI());self.reject(lambda:openai.AsyncOpenAI())
        self.reject(lambda:httpx.Client.send(None,None));self.reject(lambda:http.client.HTTPConnection.request(None,'GET','/'))
    def test_windows_asyncio_and_external_network_boundary(self):
        async def run():await asyncio.sleep(0);return type(asyncio.get_running_loop()).__name__
        self.assertIn('Proactor',asyncio.run(run()))
        with socket.socket() as sock:self.reject(lambda:sock.connect(('192.0.2.1',443)))
        self.reject(lambda:socket.getaddrinfo('example.invalid',443))
    def test_real_API_HTTP_wrapper_admits_one_POST_only(self):
        visited=[]
        async def app(scope,receive,send):visited.append(scope)
        async def receive():return {}
        async def send(value):pass
        async def check():
            wrapper=guard.S2Requests(app)
            await wrapper(dict(type='http',method='GET',path='/api/v1/options'),receive,send)
            await wrapper(dict(type='http',method='POST',path='/api/v1/tasks'),receive,send)
            before=len(visited)
            with self.assertRaises(RuntimeError):await wrapper(dict(type='http',method='POST',path='/api/v1/tasks'),receive,send)
            self.assertEqual(len(visited),before)
            with self.assertRaises(RuntimeError):await wrapper(dict(type='http',method='POST',path='/api/v1/chat'),receive,send)
        asyncio.run(check());self.assertEqual(len(visited),2)

class NativeContractTests(unittest.TestCase):
    """Native React body -> TaskInput -> prepare_parameters -> guarded native enqueue."""
    def setUp(self):
        base.COUNTS=dict.fromkeys(guard.LIMITS,0);base.STARTED=None;base.CURRENT.task_id=None
        (CASE/'execution-armed.json').write_text('{}')
        self.directory=tempfile.TemporaryDirectory(dir=CASE)
        self.addCleanup(self.directory.cleanup)
        self.tasks=Path(self.directory.name)/'OFFLINE-SYNTHETIC-tasks.sqlite3'
        with closing(sqlite3.connect(self.tasks)) as con:
            con.executescript(store.SCHEMA)
            con.execute(f'PRAGMA application_id={store.APPLICATION_ID}')
            con.execute('PRAGMA user_version=1')
        from fastapi import FastAPI
        from data_analysis_agent.api.routes.tasks import create_router
        self.app=FastAPI()
        self.app.include_router(create_router(self.tasks,None,import_root=CASE,env_file=CASE/'offline.env'),prefix='/api/v1')
        self.body=deepcopy(NATIVE_BODY)

    def task_count(self):
        with closing(sqlite3.connect(self.tasks)) as con:return con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]

    def post(self,body=None):
        import httpx
        async def send():
            # ASGI in memory, no socket, browser, API lifespan or Worker startup.
            request=httpx.Request('POST','http://127.0.0.1/api/v1/tasks',json=body or self.body,
                                  headers={'Idempotency-Key':'OFFLINE-F01-NATIVE'})
            transport=httpx.ASGITransport(app=guard.S2Requests(self.app),raise_app_exceptions=False)
            response=await transport.handle_async_request(request)
            await response.aread();await transport.aclose()
            return response
        return asyncio.run(send())

    def rejected(self,body):
        before=len(EFFECTS)
        response=self.post(body)
        self.assertIn(response.status_code,(400,422,500),response.text)
        self.assertEqual(self.task_count(),0)
        self.assertEqual(len(EFFECTS),before)
        self.assertEqual(base.COUNTS['submission'],0)
        self.assertTrue(all(base.COUNTS[key]==0 for key in guard.LIMITS if key!='http_submission'))

    def change(self,key,value,*,selection_only=False):
        body=deepcopy(self.body)
        if not selection_only:body['parameters'][key]=value
        if key in BaselineScope.model_fields:body['parameters']['selection_scope'][key]=deepcopy(value)
        # Every case starts with the real captured body and only native schema fields.
        self.assertLessEqual(set(body['parameters']),set(AnalysisParameters.model_fields))
        self.assertLessEqual(set(body['parameters']['selection_scope']),set(BaselineScope.model_fields))
        return body

    def test_native_API_serialization_enqueues_without_timezone(self):
        response=self.post()
        self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(self.task_count(),1)
        value=response.json()['result']['task']
        self.assertEqual(value['status'],'queued')
        parameters=value['parameters']
        self.assertNotIn('timezone',parameters)
        self.assertNotIn('timezone',parameters['selection_scope'])
        self.assertIn('model_identity',parameters)
        self.assertEqual(base.COUNTS['submission'],1)
        self.assertTrue(all(base.COUNTS[key]==0 for key in guard.LIMITS if key not in ('http_submission','submission')))
        (OUT/'contract-native-serialized.json').write_text(json.dumps(dict(proof='OFFLINE_IN_MEMORY_NATIVE_API_TEMPORARY_SQLITE_NO_WORKER',
            input=self.body,parameters=parameters),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    def test_absent_timezone_is_native_and_UTC_is_derived(self):
        p=native_parameters(self.body)
        self.assertNotIn('timezone',AnalysisParameters.model_fields)
        self.assertNotIn('timezone',p)
        self.assertNotIn('timezone',p['selection_scope'])
        _,_,scope=review_scope(**{key:p[key] for key in ('product_ids','source','dataset_version','date_from','date_to','as_of')})
        self.assertEqual(scope['timezone'],'UTC')
        base.check_scope(p);base.check_scope(p['selection_scope'])

    def test_wrong_product_is_rejected(self):self.rejected(self.change('product_ids',['100013588053']))
    def test_multiple_products_are_rejected(self):self.rejected(self.change('product_ids',['100013588055','100013588053']))
    def test_empty_product_scope_is_rejected(self):self.rejected(self.change('product_ids',[]))
    def test_changed_start_is_rejected(self):self.rejected(self.change('date_from','2021-09-12'))
    def test_out_of_range_end_is_rejected(self):self.rejected(self.change('date_to','2022-08-27'))
    def test_baseline_drift_is_rejected(self):self.rejected(self.change('baseline_from','2021-09-09'))
    def test_as_of_drift_is_rejected(self):self.rejected(self.change('as_of','2022-08-26'))
    def test_wrong_source_is_rejected(self):self.rejected(self.change('source','other-source'))
    def test_wrong_dataset_version_is_rejected(self):self.rejected(self.change('dataset_version','other-version'))
    def test_wrong_label_model_is_rejected(self):self.rejected(self.change('model','other-label-model'))
    def test_wrong_label_endpoint_is_rejected(self):self.rejected(self.change('base_url','https://offline.invalid/v1'))
    def test_wrong_label_prompt_is_rejected(self):self.rejected(self.change('prompt_version','other-label-prompt'))
    def test_selection_product_drift_is_rejected(self):self.rejected(self.change('product_ids',['100013588053'],selection_only=True))
    def test_selection_date_drift_is_rejected(self):self.rejected(self.change('date_to','2022-08-27',selection_only=True))
    def test_selection_identity_drift_is_rejected(self):self.rejected(self.change('model','other-label-model',selection_only=True))
    def test_wrong_question_is_rejected(self):self.rejected(self.change('question','商品100013588055有没有送货快的反馈？'))
    def test_history_is_rejected(self):self.rejected(self.change('context_task_id','OFFLINE-OLD-TASK'))

    def test_production_UTC_derivation_drift_fails_closed(self):
        p=native_parameters(self.body)
        original=review_scope
        def drift(**kwargs):
            where,values,scope=original(**kwargs)
            return where,values,{**scope,'timezone':'Asia/Shanghai'}
        before=len(EFFECTS)
        with patch('data_analysis_agent.reviews.scope.review_scope',side_effect=drift):
            with self.assertRaisesRegex(RuntimeError,'p7c_forbidden_scope'):base.check_scope(p)
        self.assertEqual(len(EFFECTS),before);self.assertEqual(self.task_count(),0)
        self.assertEqual(base.COUNTS['submission'],0)

    def test_source_and_dataset_guard_remain_fixed_on_native_parameters(self):
        for key,value in (('source','other-source'),('dataset_version','other-version')):
            # Schema serialization is real; native prepare also independently rejects these datasets.
            p=AnalysisParameters.model_validate(self.change(key,value)['parameters']).model_dump()
            with self.assertRaisesRegex(RuntimeError,'p7c_forbidden_scope'):base.check_scope(p)
        self.assertEqual(self.task_count(),0);self.assertEqual(base.COUNTS['submission'],0)


stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (S2Tests,NativeContractTests)]))
report=dict(tests=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors),
    failures=[dict(test=str(t),traceback=tr) for t,tr in result.failures+result.errors],
    proof='OFFLINE_NATIVE_API_SERIALIZATION_AND_GUARD_TEMP_SQLITE_NO_REAL_IO',
    native_input_path=str(body_path),native_request_has_timezone='timezone' in NATIVE_BODY['parameters'],
    actual_tasks=0,actual_query_embeddings=0,actual_top5=0,paid_llm=0)
(OUT/'contract-tests.log').write_text(stream.getvalue(),encoding='utf-8')
(OUT/'contract-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
for p in reversed(patches):p.stop()
shutil.copyfile(CASE/'runtime-events.jsonl',OUT/'offline-guard-events.jsonl')
temporary.cleanup();print(json.dumps(report,ensure_ascii=False))
raise SystemExit(0 if result.wasSuccessful() else 1)
