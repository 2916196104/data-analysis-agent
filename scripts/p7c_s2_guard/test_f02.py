"""F02 real candidate wrappers with synthetic terminal adapters. No live business IO."""
import os,sys,json,io,time,sqlite3,subprocess,tempfile,unittest,uuid,asyncio
from pathlib import Path
from types import SimpleNamespace
from contextlib import closing,nullcontext
from copy import deepcopy
from unittest.mock import patch

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
OUT=Path(os.environ['F02_OUTPUT_DIR']).resolve();OUT.mkdir(exist_ok=True,parents=True)
TEMP=tempfile.TemporaryDirectory(prefix='OFFLINE-F02-',dir=OUT);CASES=Path(TEMP.name)
os.environ.update(P7C_REAL_DIR=str(CASES),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                  LLM_MODEL_ID='OFFLINE-F02-AGENT',LLM_API_KEY='SYNTHETIC',LLM_BASE_URL='http://127.0.0.1:1')
sys.path[:0]=[str(HERE),str(ROOT/'src')]
REAL_POPEN=subprocess.Popen
EFFECTS=[]
def offline(event,args):
    if event in ('socket.connect','socket.sendto','socket.getaddrinfo'):
        if event=='socket.connect':
            from f02_network import socketpair_call
            if socketpair_call():return
        raise RuntimeError('F02_real_network_forbidden')
sys.addaudithook(offline)
import p7c_real_guard as base
import s2_guard as guard
from f02_state import RunLedger,LIMITS,guard_hashes,git_head,AdmissionError,safe_path
from f02_sql import validate_sql,canonical
from f02_network import audit,isolation_status
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.jobs import store,submission
from data_analysis_agent.jobs.worker import TaskWorker
from data_analysis_agent.agents.review_tools import ReviewToolContext
from data_analysis_agent.reviews import embedding_process,embedding_store
from data_analysis_agent.api.schemas import TaskInput
from pydantic import TypeAdapter
from data_analysis_agent.jobs.submission import prepare_parameters
from data_analysis_agent.reviews.scope import review_scope
from data_analysis_agent.agents.review_retrieval import retrieval_request

def authorization(path):
    now=time.time()
    return dict(stage='P7-C-S2-R1B',approved=True,authorization_id='SYNTHETIC-OFFLINE-'+uuid.uuid4().hex,
        proof='OFFLINE_SYNTHETIC_NOT_A_LIVE_AUTHORIZATION',run_dir=str(path),task_db=str(path/'tasks.sqlite3'),
        question=base.QUESTION,scope=base.SCOPE,identity=base.IDENTITY,limits=LIMITS,
        observation_seconds=900,started_at=now,deadline=now+900,conversation_id='OFFLINE-CONVERSATION',
        git_head=git_head(),guard_hashes=guard_hashes(),paid_llm=0,vector_sync=0,pg_business_writes=0,automatic_retries=0)

def fresh():
    p=CASES/('p7c-s2-r1b-'+uuid.uuid4().hex)
    return RunLedger.create(p,authorization(p))

initial=fresh();base.OUT=initial.out
VECTOR=[1.0]+[0.0]*1023
NATIVE_ENQUEUE=store.enqueue
TOOL_RESULT={'kind':'context','result_id':'OFFLINE-RESULT'}
TOOL_ERROR=None;POPEN_ERROR=None
NATIVE_EMBED=embedding_process.PersistentEmbeddingProcess.embed_texts
NATIVE_EMBED_MODE=False
class Raw:
    info=SimpleNamespace(backend_pid=123,transaction_status=0)
    closed=False
    def execute(self,sql,p=None):EFFECTS.append(('sql',sql,p));return 'OFFLINE_SQL'
    def close(self):self.closed=True
def connect(*a,**kw):EFFECTS.append(('connect',kw));return Raw()
def native_enqueue(*a,**kw):EFFECTS.append(('enqueue',a[0]));return NATIVE_ENQUEUE(*a,**kw)
def tool(*a,**kw):
    EFFECTS.append(('tool',a[1]))
    if TOOL_ERROR:raise TOOL_ERROR
    return TOOL_RESULT
def popen(*a,**kw):
    EFFECTS.append(('popen',a[0]))
    if POPEN_ERROR:raise POPEN_ERROR
    return SimpleNamespace(pid=456,stdout=io.StringIO(''),stdin=io.StringIO(),poll=lambda:0,wait=lambda **kw:0)
def rpc(self,payload,deadline):
    EFFECTS.append(('rpc',payload['operation']))
    return dict(status='ready',model_load_ms=1,metrics={},**({'vectors':[VECTOR]} if payload['operation']=='encode' else {}))
def embed(self,texts):return NATIVE_EMBED(self,texts) if NATIVE_EMBED_MODE else [VECTOR]
patches=[patch('psycopg.connect',side_effect=connect),patch.object(store,'enqueue',side_effect=native_enqueue),
    patch.object(TaskWorker,'_execute',lambda *a: 'OFFLINE_WORKER'),patch.object(ReviewToolContext,'execute',side_effect=tool),
    patch.object(embedding_process.PersistentEmbeddingProcess,'embed_texts',side_effect=embed),
    patch.object(embedding_process.PersistentEmbeddingProcess,'_rpc',side_effect=rpc),
    patch.object(embedding_process.subprocess,'Popen',side_effect=popen)]
for p in patches:p.start()
guard.install()
FIXTURE=Path(os.environ.get('F02_CAPTURE_POST_PATH',str(ROOT/'frontend/src/features/agent/__fixtures__/p7c-s2-native-post.json')))
BODY=json.loads(FIXTURE.read_text(encoding='utf-8'))

class Fixture(unittest.TestCase):
    def setUp(self):
        global TOOL_ERROR,POPEN_ERROR,NATIVE_EMBED_MODE
        TOOL_ERROR=POPEN_ERROR=None;NATIVE_EMBED_MODE=False;EFFECTS.clear();base.OBSERVATION_ERRORS.clear()
        base.CURRENT.task_id=None;base.CURRENT.model_process=None;base.CURRENT.retrieval_active=False
        self.ledger=fresh();base.LEDGER=self.ledger;base.OUT=self.ledger.out;base.COUNTS=self.ledger.counts()
        self.env=self.ledger.out/'offline.env';self.env.write_text('',encoding='utf-8')
        self.p=prepare_parameters('analysis',TypeAdapter(TaskInput).validate_python(BODY).parameters.model_dump(),import_root=self.ledger.out,env_file=self.env)
        self.p['conversation_id']='OFFLINE-CONVERSATION'
        with closing(sqlite3.connect(self.ledger.out/'tasks.sqlite3')) as c:
            c.executescript(store.SCHEMA);c.execute(f'PRAGMA application_id={store.APPLICATION_ID}');c.execute('PRAGMA user_version=1')
    def ready(self):
        self.ledger.take('http_submission');self.ledger.take('submission')
        self.ledger.bind_task('OFFLINE-TASK',self.p['conversation_id'])
        self.ledger.take('worker_task',task_id='OFFLINE-TASK');base.CURRENT.task_id='OFFLINE-TASK'
    def model_command(self):return [str((ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve()),'-B',str(HERE/'embedding_worker.py')]
    def before_model(self):self.ready();self.ledger.take('query_embedding',task_id='OFFLINE-TASK')
    def bind_retrieval(self):
        self.before_model();subprocess.Popen(self.model_command())
        self.ledger.take('model_initialize',task_id='OFFLINE-TASK');self.ledger.take('model_encode_rpc',task_id='OFFLINE-TASK');self.ledger.bind_vector(VECTOR)
        base.CURRENT.retrieval_active=True
    def reject_without_effect(self,fn):
        before=len(EFFECTS)
        with self.assertRaises((RuntimeError,ValueError)):fn()
        self.assertEqual(len(EFFECTS),before)
    def vector_params(self):
        request=retrieval_request(base.QUESTION)
        from data_analysis_agent.core.serialization import content_sha256
        return (json.dumps(VECTOR),json.dumps([dict(key='rag-opinion-v1:'+'a'*64,snapshot='b'*64)]),
            '100013588055','reviews_csv','reviews-csv','2021-09-11T00:00:00.000+00:00','2022-08-27T00:00:00.000+00:00',
            embedding_store.SPACE_ID,'quote-utf8-v1',content_sha256(list(base.IDENTITY.values())),
            request.aspect,request.aspect,request.sentiment,request.sentiment,5)

class ObservationTests(Fixture):
    def test_both_tools_success_identity_once(self):
        self.ready()
        for name in ('get_analysis_context','search_semantic_evidence'):
            before=len(EFFECTS);result=ReviewToolContext.execute(object(),name,{})
            self.assertIs(result,TOOL_RESULT);self.assertEqual(len(EFFECTS)-before,1)
        rows=[json.loads(s) for s in (base.OUT/'runtime-events.jsonl').read_text().splitlines()]
        self.assertEqual([r['kind'] for r in rows if r['event']=='tool_end'],['context','context'])
    def test_metadata_name_collision(self):
        base.event('tool_end',kind='context',event_name='metadata',event='untrusted')
        row=json.loads((base.OUT/'runtime-events.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['kind'],'context');self.assertEqual(row['event'],'tool_end')
    def test_business_exception_preserved(self):
        global TOOL_ERROR
        self.ready();TOOL_ERROR=LookupError('ORIGINAL')
        with self.assertRaises(LookupError) as error:ReviewToolContext.execute(object(),'get_analysis_context',{})
        self.assertIs(error.exception,TOOL_ERROR);self.assertEqual(sum(e[0]=='tool' for e in EFFECTS),1)
    def broken_logs(self,at):
        original=Path.open;counter=[0]
        def opening(p,*a,**kw):
            if p.name=='runtime-events.jsonl':
                counter[0]+=1
                if counter[0]>=at:raise OSError('OFFLINE_LOG_FAILURE')
            return original(p,*a,**kw)
        return patch.object(Path,'open',opening)
    def test_log_failure_before_tool(self):
        self.ready()
        with self.broken_logs(1),self.assertRaisesRegex(RuntimeError,'observation_failed'):
            ReviewToolContext.execute(object(),'get_analysis_context',{})
        self.assertFalse(any(e[0]=='tool' for e in EFFECTS))
    def test_log_failure_after_tool_no_fake_success(self):
        self.ready()
        with self.broken_logs(2),self.assertRaisesRegex(RuntimeError,'observation_failed'):
            ReviewToolContext.execute(object(),'get_analysis_context',{})
        self.assertEqual(sum(e[0]=='tool' for e in EFFECTS),1);self.assertTrue(base.OBSERVATION_ERRORS)
    def test_business_exception_not_replaced_by_log_failure(self):
        global TOOL_ERROR
        self.ready();TOOL_ERROR=LookupError('ORIGINAL')
        with self.broken_logs(2),self.assertRaises(LookupError) as err:
            ReviewToolContext.execute(object(),'get_analysis_context',{})
        self.assertIs(err.exception,TOOL_ERROR)
    def test_worker_start_log_failure_clears_task_context(self):
        self.ledger.take('http_submission')
        value=store.enqueue(self.ledger.out/'tasks.sqlite3','analysis',self.p,idempotency_key='OFFLINE')
        task=dict(kind='analysis',parameters=self.p,task_id=value['task']['task_id'])
        worker=SimpleNamespace(task_db=self.ledger.out/'tasks.sqlite3')
        with self.broken_logs(2),self.assertRaisesRegex(RuntimeError,'observation_failed'):
            TaskWorker._execute(worker,task)
        self.assertIsNone(base.CURRENT.task_id)

class ProcessTests(Fixture):
    def test_native_call_path_hits_guard_before_popen_and_rpc(self):
        global NATIVE_EMBED_MODE
        self.ready();NATIVE_EMBED_MODE=True
        obj=embedding_process.PersistentEmbeddingProcess(self.model_command()[0],ROOT/'.cache/embedding-eval/hf/hub')
        try:
            self.assertEqual(obj.embed_texts([base.QUESTION]),[VECTOR])
            self.assertEqual([e[0] for e in EFFECTS if e[0] in ('popen','rpc')],['popen','rpc','rpc'])
            for kind in ('query_embedding','model_process_create','model_initialize','model_encode_rpc'):
                self.assertEqual(self.ledger.counts()[kind],1)
        finally:obj.close()
    def test_before_deadline_first_popen_then_duplicate(self):
        self.before_model();p=subprocess.Popen(self.model_command());self.assertEqual(p.pid,456)
        self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
    def test_901_seconds_first_creation_denied(self):
        self.before_model()
        with patch('f02_state.time.time',return_value=self.ledger.authorization['deadline']+1):
            self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
        self.assertEqual(self.ledger.counts()['model_process_create'],0)
    def test_missing_task_denied(self):
        self.before_model();base.CURRENT.task_id=None
        self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
    def test_wrong_task_denied(self):
        self.before_model();base.CURRENT.task_id='OTHER'
        self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
    def test_unauthorized_denied(self):
        self.before_model();self.ledger.authorization={**self.ledger.authorization,'approved':False}
        self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
    def test_creation_failure_keeps_quota(self):
        global POPEN_ERROR
        self.before_model();POPEN_ERROR=OSError('ORIGINAL_POPEN')
        with self.assertRaises(OSError):subprocess.Popen(self.model_command())
        self.assertEqual(self.ledger.counts()['model_process_create'],1)
        self.reject_without_effect(lambda:subprocess.Popen(self.model_command()))
    def test_popen_option_overrides_refused(self):
        self.before_model()
        for kw in ({'executable':'UNAPPROVED'},{'shell':True},{'env':{'HF_HUB_OFFLINE':'0'}},{'cwd':'OTHER'},{'preexec_fn':lambda:None}):
            self.reject_without_effect(lambda:subprocess.Popen(self.model_command(),**kw))
        self.assertEqual(self.ledger.counts()['model_process_create'],0)
    def test_constructor_is_not_creation(self):
        self.before_model()
        obj=embedding_process.PersistentEmbeddingProcess(self.model_command()[0],ROOT/'.cache/embedding-eval/hf/hub')
        self.assertEqual(obj.worker,HERE/'embedding_worker.py');self.assertEqual(self.ledger.counts()['model_process_create'],0)

class PersistenceTests(Fixture):
    def helper(self,mode):
        return REAL_POPEN([sys.executable,'-B',str(HERE/'f02_probe.py'),mode,str(self.ledger.out)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    def test_two_independent_processes_compete(self):
        children=[self.helper('consume') for _ in range(2)]
        results=[(p.communicate(timeout=20),p.returncode) for p in children]
        self.assertEqual(sorted(v[1] for v in results),[0,42]);self.assertEqual(self.ledger.counts()['http_submission'],1)
    def test_restart_rejects_used_quota(self):
        a=self.helper('consume');a.communicate(timeout=20);b=self.helper('consume');b.communicate(timeout=20)
        self.assertEqual((a.returncode,b.returncode),(0,42))
    def test_post_commit_crash_never_refunds(self):
        a=self.helper('crash');a.communicate(timeout=20);b=self.helper('consume');b.communicate(timeout=20)
        self.assertEqual((a.returncode,b.returncode),(17,42))
    def test_wrong_authorization_not_reused(self):
        changed={**self.ledger.authorization,'authorization_id':'OFFLINE-DIFFERENT-AUTH'}
        with self.assertRaises(RuntimeError):RunLedger(self.ledger.out,changed)
    def test_existing_directory_never_rearmed(self):
        with self.assertRaises(FileExistsError):RunLedger.create(self.ledger.out,self.ledger.authorization)
    def test_historical_directory_name_rejected(self):
        with self.assertRaises(RuntimeError):RunLedger.create(CASES/'p7c-s2-real-old',self.ledger.authorization)
    def test_missing_ledger_not_recreated(self):
        (self.ledger.out/'quota.sqlite3').unlink()
        with self.assertRaises(sqlite3.OperationalError):self.ledger.take('http_submission')
    def test_hash_and_git_drift_denied(self):
        for key,value in [('guard_hashes',{}),('git_head','0'*40)]:
            with self.assertRaises(RuntimeError):RunLedger(self.ledger.out,{**self.ledger.authorization,key:value})
    def test_stop_is_persistent(self):
        self.ledger.stop()
        with self.assertRaises(RuntimeError):self.ledger.take('http_submission')

class ContractTests(Fixture):
    def test_native_timezone_omitted_UTC_derived(self):
        self.assertNotIn('timezone',self.p);self.assertNotIn('timezone',self.p['selection_scope'])
        _,values,scope=review_scope(**{k:self.p[k] for k in ('product_ids','source','dataset_version','date_from','date_to','as_of')})
        self.assertEqual(scope['timezone'],'UTC');self.assertEqual(values[-1],'2022-08-27T00:00:00.000+00:00')
        base.check_scope(self.p);base.check_scope(self.p['selection_scope'])
    def test_scope_and_identity_matrix(self):
        changes=dict(product_ids=['100013588053'],source='amazon_reviews_2023',dataset_version='other',date_from='2021-09-10',date_to='2022-08-27',baseline_from='2021-09-09',baseline_to='2021-09-11',as_of='2022-08-01',model='other',base_url='http://wrong',prompt_version='other',timezone='Asia/Taipei')
        for params in (self.p,self.p['selection_scope']):
            for k,v in changes.items():
                with self.subTest(field=k),self.assertRaises((RuntimeError,ValueError)):
                    base.check_scope({**params,k:v})
    def test_native_API_route_enqueue_synthetic_only(self):
        from fastapi import FastAPI
        from data_analysis_agent.api.routes.tasks import create_router
        import httpx
        app=FastAPI();app.include_router(create_router(self.ledger.out/'tasks.sqlite3',None,import_root=self.ledger.out,env_file=self.env),prefix='/api/v1')
        body=deepcopy(BODY);body['parameters']['conversation_id']=self.p['conversation_id']
        async def invoke():
            transport=httpx.ASGITransport(app=guard.S2Requests(app))
            response=await transport.handle_async_request(httpx.Request('POST','http://127.0.0.1/api/v1/tasks',json=body,headers={'Idempotency-Key':'OFFLINE-ONE'}))
            await response.aread();await transport.aclose();return response
        response=asyncio.run(invoke());self.assertEqual(response.status_code,202,response.text)
        task=response.json()['result']['task'];self.assertEqual(task['status'],'queued');self.ledger.check_task(task['task_id'])
    def test_wrong_database_and_worker_task_rejected(self):
        self.reject_without_effect(lambda:store.enqueue('HISTORICAL.sqlite3','analysis',self.p,idempotency_key='OFFLINE'))
        self.ledger.take('http_submission')
        value=store.enqueue(self.ledger.out/'tasks.sqlite3','analysis',self.p,idempotency_key='OFFLINE')
        task=dict(kind='analysis',parameters=self.p,task_id=value['task']['task_id'])
        worker=SimpleNamespace(task_db=self.ledger.out/'tasks.sqlite3')
        self.reject_without_effect(lambda:TaskWorker._execute(worker,{**task,'task_id':'OTHER'}))
        self.assertEqual(TaskWorker._execute(worker,task),'OFFLINE_WORKER');self.assertIsNone(base.CURRENT.task_id)
    def test_conversation_history_and_selection_rejected(self):
        for change in ({'conversation_id':'OTHER'},{'context_task_id':'OLD'},{'selection_scope':{**self.p['selection_scope'],'product_ids':['053']}}):
            self.reject_without_effect(lambda:store.enqueue(self.ledger.out/'tasks.sqlite3','analysis',{**self.p,**change},idempotency_key='OFFLINE'))
    def test_symlink_path_refused(self):
        link=CASES/'link-to-run'
        try:link.symlink_to(self.ledger.out,target_is_directory=True)
        except OSError as exc:self.skipTest('symlink privilege unavailable: '+type(exc).__name__)
        try:
            with self.assertRaises(RuntimeError):safe_path(link/'tasks.sqlite3')
        finally:link.unlink()
    def test_reparse_attribute_refused(self):
        original=Path.lstat
        def info(p,*a,**kw):
            if p==self.ledger.out:return SimpleNamespace(st_file_attributes=0x400,st_mode=0x4000)
            return original(p,*a,**kw)
        with patch.object(Path,'lstat',info),self.assertRaises(RuntimeError):safe_path(self.ledger.out/'tasks.sqlite3')

class SqlTests(Fixture):
    def test_every_unsafe_SQL_rejected_before_sentinel(self):
        bad=['SET READ ONLY OFF','SET default_transaction_read_only TO off','SET transaction_read_only = false',"SELECT set_config('transaction_read_only','of' || 'f',false)",'BEGIN READ WRITE','INSERT INTO reviews VALUES (1)','UPDATE reviews SET id=2','DELETE FROM reviews','CREATE TABLE x(id int)','ALTER TABLE reviews RENAME TO x','DROP TABLE reviews','COPY reviews TO STDOUT','SELECT 1; SELECT 2','SELECT /* hidden */ 1','--hidden\nSELECT 1','SELECT pg_sleep(10)','SELECT 1 <-> 2']
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        try:
            for sql in bad:
                with self.subTest(sql=sql):self.reject_without_effect(lambda:con._execute(sql))
        finally:con.close()
    def test_safe_query_preserves_return(self):
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        try:self.assertEqual(con.execute(' SELECT  1 '),'OFFLINE_SQL');self.assertTrue(con.readonly)
        finally:con.close()
    def test_vector_SQL_fixed_scope_and_one_shot(self):
        self.bind_retrieval();query=next(r['sql'] for r in json.loads((HERE/'f02_sql_templates.json').read_text()) if r['binding']=='vector').replace('%s','?')
        params=self.vector_params();con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        try:
            for index,value in [(0,'[1]'),(2,'OTHER'),(3,'amazon_reviews_2023'),(5,'2099-01-01'),(7,'OTHER-SPACE'),(14,6)]:
                changed=list(params);changed[index]=value
                self.reject_without_effect(lambda:con.execute(query,changed))
            self.assertEqual(con.execute(query,params),'OFFLINE_SQL')
            self.reject_without_effect(lambda:con.execute(query,params))
        finally:con.close()
    def test_alternate_distance_operator_rejected(self):
        self.bind_retrieval();q=next(r['sql'] for r in json.loads((HERE/'f02_sql_templates.json').read_text()) if r['binding']=='vector')
        with self.assertRaises(RuntimeError):validate_sql(q.replace('<=>','<->'),self.vector_params(),task_id='OFFLINE-TASK',ledger=self.ledger)
    def test_retrieval_scope_wrapper_rejects_actual_options(self):
        self.bind_retrieval();scope={**base.SCOPE,**base.IDENTITY};scope.pop('timezone')
        for field,value in [('product_ids',['OTHER']),('date_from','2099-01-01'),('source','other')]:
            self.reject_without_effect(lambda:embedding_store._search_effective_opinions(None,VECTOR,options={**scope,field:value},embedding_space_id=embedding_store.SPACE_ID))
    def test_native_repository_alias_is_guarded(self):
        from data_analysis_agent.reviews import semantic_retrieval
        self.assertIs(semantic_retrieval.search_effective_opinions,embedding_store._search_effective_opinions)
    def test_global_metadata_not_allowed_during_task(self):
        q=next(r['sql'] for r in json.loads((HERE/'f02_sql_templates.json').read_text()) if r.get('startup_only') and r['binding']=='none')
        with self.assertRaises(RuntimeError):validate_sql(q,task_id='OFFLINE-TASK',ledger=self.ledger)
    def test_native_vector_builder_and_bound_corpus_scope(self):
        self.bind_retrieval();base.CURRENT.retrieval_active=False
        con=PostgresConnection('postgresql://rag_audit_ro@127.0.0.1:5432/reviews')
        options={**base.SCOPE,**base.IDENTITY};options.pop('timezone');options['as_of']=None
        from data_analysis_agent.core.serialization import content_sha256
        corpus=SimpleNamespace(scope={**options,'timezone':'UTC'},documents=(),reviews=(),label_profile_key=content_sha256(list(base.IDENTITY.values())))
        original=Raw.execute
        def sql_result(self,query,p=None):
            if '<=>' in query:
                EFFECTS.append(('native_vector_SQL',query,p));return SimpleNamespace(fetchall=lambda:[])
            return original(self,query,p)
        request=retrieval_request(base.QUESTION)
        try:
            with patch.object(embedding_store,'_snapshot',lambda db:nullcontext(con)),patch.object(Raw,'execute',sql_result):
                result=embedding_store._search_effective_opinions(con,VECTOR,options=options,
                    embedding_space_id=embedding_store.SPACE_ID,top_k=5,aspect=request.aspect,sentiment=request.sentiment,corpus=corpus)
            # Native search returns a tuple (embedding_store.py), including empty results.
            self.assertEqual(result,());self.assertEqual(self.ledger.counts()['vector_search'],1)
            self.assertEqual(sum(e[0]=='native_vector_SQL' for e in EFFECTS),1)
            self.assertFalse(base.CURRENT.retrieval_active)
        finally:con.close()

class NetworkImportTests(Fixture):
    def test_parent_and_child_network_events(self):
        events=[('socket.connect',(None,('192.0.2.1',443))),('socket.sendto',(None,b'x',('127.0.0.1',53))),('socket.getaddrinfo',('example.invalid',443,0,0,0)),('socket.gethostbyname',('example.invalid',)),('socket.gethostbyaddr',('192.0.2.1',)),('socket.__new__',(None,2,2,17))]
        for e,a in events:
            for child in (False,True):
                with self.subTest(event=e,child=child),self.assertRaises(RuntimeError):audit(e,a,child=child)
        audit('socket.connect',(None,('127.0.0.1',5432)))
    def test_paid_SDK_and_sync_rejected(self):
        import openai
        for fn in (openai.OpenAI,openai.AsyncOpenAI,embedding_store.sync_effective_opinions,embedding_store.migrate_embedding_store):
            self.reject_without_effect(lambda:fn())
    def test_import_paths_cache_hash_and_wrapper_identity(self):
        from f02_entry import wrapper_identity
        self.assertEqual(len(wrapper_identity()),6)
        for mode in ('origin','stale','hash'):
            p=REAL_POPEN([sys.executable,'-B',str(HERE/'f02_probe.py'),mode,str(self.ledger.out)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
            stdout,stderr=p.communicate(timeout=20);self.assertEqual(p.returncode,0,stdout+stderr)
    def test_no_false_OS_isolation_claim(self):
        self.assertEqual(isolation_status()['status'],'BLOCKED')
        self.assertFalse(isolation_status()['os_network_isolation_verified'])

if __name__=='__main__':
    classes=(ObservationTests,ProcessTests,PersistenceTests,ContractTests,SqlTests,NetworkImportTests)
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in classes)
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report=dict(tests=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        failures=[dict(test=str(t),traceback=tr) for t,tr in result.failures+result.errors],skipped=[dict(test=str(t),reason=reason) for t,reason in result.skipped],
        categories={c.__name__:len(unittest.defaultTestLoader.getTestCaseNames(c)) for c in classes},
        proof='OFFLINE_REAL_CANDIDATE_WRAPPERS_SYNTHETIC_IO_ONLY',real_business_submissions=0,real_PG=0,real_model_processes=0,real_query_embeddings=0,real_vector_searches=0,paid_llm=0)
    (OUT/'f02-tests.log').write_text(stream.getvalue(),encoding='utf-8');(OUT/'f02-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for p in reversed(patches):p.stop()
    TEMP.cleanup();print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(0 if result.wasSuccessful() else 1)
