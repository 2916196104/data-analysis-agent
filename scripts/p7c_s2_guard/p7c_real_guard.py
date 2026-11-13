"""One-shot admission and observation only. All allowed calls use production code."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from time import monotonic, perf_counter

OUT=Path(os.environ['P7C_REAL_DIR'])
QUESTION='商品100013588055有没有送货慢的反馈？'
SCOPE=dict(source='reviews_csv',dataset_version='reviews-csv',product_ids=['100013588055'],
           date_from='2021-09-11',date_to='2022-08-26',timezone='UTC')
IDENTITY=dict(model='Pro/deepseek-ai/DeepSeek-V3.2',base_url='https://api.siliconflow.cn/v1',
              prompt_version='review-opinions-v1-e61de599683cba6703c2f61e85125d0be4627c2763440486aa717e4bc884036e')
LIMITS=dict(submission=1,worker_task=1,query_embedding=1,vector_search=1,model_initialize=1,model_encode_rpc=1)
COUNTS=dict.fromkeys(LIMITS,0)
CURRENT=threading.local()
LOCK=threading.RLock()
STARTED=None
OBSERVATION_ERRORS=[]

def local_address(address):
    # Windows asyncio implements socketpair using an ephemeral loopback listener.
    return isinstance(address,tuple) and address[0] in ('127.0.0.1','::1','localhost')

def event(kind,**data):
    # Losing the evidence makes the run fail closed before the next side effect.
    with LOCK:
        row=dict(at=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),thread=threading.current_thread().name,
                 task_id=getattr(CURRENT,'task_id',None),event=kind,**data)
        try:
            with (OUT/'runtime-events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        except Exception:
            OBSERVATION_ERRORS.append(kind)
            raise RuntimeError('p7c_observation_failed') from None

def deny(kind):
    event('boundary_blocked',operation=kind)
    raise RuntimeError('p7c_forbidden_'+kind)

def blocked(kind):
    def reject(*a,**kw):return deny(kind)
    return reject

def take(kind):
    global STARTED
    with LOCK:
        if OBSERVATION_ERRORS or not (OUT/'execution-armed.json').is_file():return deny('unarmed_execution')
        if STARTED is not None and monotonic()-STARTED>=900:return deny('observation_deadline')
        if COUNTS[kind]>=LIMITS[kind]:return deny('duplicate_'+kind)
        if kind=='submission':STARTED=monotonic()
        COUNTS[kind]+=1
        event('quota_consumed',operation=kind,count=COUNTS[kind],limit=LIMITS[kind])
        state=OUT/'gate-state.json'
        state.write_text(json.dumps(dict(counts=COUNTS,limits=LIMITS,started_monotonic=STARTED,observation_errors=OBSERVATION_ERRORS)),encoding='utf-8')

def check_scope(p):
    from data_analysis_agent.reviews.scope import review_scope
    fields={k:p.get(k) for k in SCOPE if k!='timezone'}
    _,_,derived=review_scope(**fields,as_of=p.get('as_of'))
    # Native API parameters omit timezone; production scope derivation supplies UTC.
    if (fields!={k:v for k,v in SCOPE.items() if k!='timezone'}
        or derived['timezone']!=SCOPE['timezone']
        or p.get('timezone',derived['timezone'])!=SCOPE['timezone']):return deny('scope')
    if p.get('baseline_from')!='2021-09-10' or p.get('baseline_to')!='2021-09-10' or p.get('as_of') is not None:return deny('extra_date_scope')
    if any(p.get(k)!=v for k,v in IDENTITY.items()):return deny('label_identity')

def install():
    from psycopg.conninfo import conninfo_to_dict
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.jobs import store,submission
    from data_analysis_agent.jobs.worker import TaskWorker,WorkerLock
    from data_analysis_agent.agents.review_tools import ReviewToolContext
    from data_analysis_agent.reviews.embedding_process import PersistentEmbeddingProcess,LocalEmbeddingProcess
    from data_analysis_agent.agents import review_retrieval
    import httpx
    import openai
    original_init=PostgresConnection.__init__
    original_execute=PostgresConnection._execute
    original_close=PostgresConnection.close
    original_enqueue=store.enqueue
    original_task=TaskWorker._execute
    original_tool=ReviewToolContext.execute
    original_acquire=WorkerLock.acquire
    original_embedding=PersistentEmbeddingProcess.embed_texts
    original_rpc=PersistentEmbeddingProcess._rpc
    original_embedding_close=PersistentEmbeddingProcess.close

    def initialize(self,target,**kw):
        info=conninfo_to_dict(str(target))
        if (info.get('user'),info.get('host'),info.get('dbname'),info.get('port'))!=('rag_audit_ro','127.0.0.1','reviews','5432') or info.get('hostaddr') not in (None,'127.0.0.1'):return deny('database_identity')
        kw['readonly']=True
        original_init(self,target,**kw)
        event('pg_open',namespace=self.namespace,backend_pid=self.raw.info.backend_pid,readonly=self.readonly)

    def execute(self,sql,parameters=None):
        upper=sql.strip().upper()
        tokens=re.sub(r"'(?:[^']|'')*'","''",upper)
        if re.search(r'\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|DO|GRANT|REVOKE)\b',tokens):return deny('business_write_sql')
        if re.search(r'READ_ONLY\s*=\s*(OFF|FALSE|0)',upper):return deny('readonly_off')
        if not upper.startswith(('SELECT','WITH','SHOW','SET','BEGIN','COMMIT','ROLLBACK')):return deny('unapproved_sql')
        if not self.readonly:return deny('non_readonly_connection')
        if '<=>' in sql:
            if not getattr(CURRENT,'task_id',None) or not parameters or parameters[-1]!=5:return deny('unbound_vector_search')
            take('vector_search')
        start=perf_counter()
        try:
            value=original_execute(self,sql,parameters)
            event('pg_sql',namespace=self.namespace,backend_pid=self.raw.info.backend_pid,sql=sql,readonly=True,
                  parameter_count=len(parameters) if parameters else 0,
                  parameter_sha256=hashlib.sha256(repr(parameters).encode()).hexdigest(),duration_ms=(perf_counter()-start)*1000,status='ok')
            return value
        except Exception as exc:
            event('pg_sql_error',sql=sql,exception_type=type(exc).__name__)
            raise

    def close(self):
        pid=self.raw.info.backend_pid if self.raw is not None and not self.raw.closed else None
        original_close(self)
        if pid is not None:event('pg_closed',backend_pid=pid)

    def enqueue(path,kind,p,*,idempotency_key=None,parent_task_id=None):
        check_scope(p)
        if kind!='analysis' or p.get('question')!=QUESTION or not p.get('conversation_id') or p.get('context_task_id') or parent_task_id or not idempotency_key:return deny('task_payload')
        check_scope(p.get('selection_scope') or {})
        take('submission')
        value=original_enqueue(path,kind,p,idempotency_key=idempotency_key,parent_task_id=parent_task_id)
        if value.get('reused'):return deny('reused_task')
        event('task_enqueued',new_task_id=value['task']['task_id'],conversation_id=p['conversation_id'],question=p['question'])
        (OUT/'task-id.json').write_text(json.dumps(dict(task_id=value['task']['task_id'],conversation_id=p['conversation_id'])),encoding='utf-8')
        return value

    def task(self,value):
        p=value['parameters'];check_scope(p)
        if value['kind']!='analysis' or p.get('question')!=QUESTION or COUNTS['submission']!=1:return deny('worker_payload')
        take('worker_task');CURRENT.task_id=value['task_id']
        event('worker_task_start',question=p['question'],conversation_id=p['conversation_id'])
        start=perf_counter()
        try:return original_task(self,value)
        finally:
            event('worker_task_end',duration_ms=(perf_counter()-start)*1000)
            CURRENT.task_id=None

    def tool(self,name,parameters):
        if name not in ('get_analysis_context','search_semantic_evidence') or not getattr(CURRENT,'task_id',None):return deny('unapproved_tool')
        if name=='search_semantic_evidence' and parameters:return deny('semantic_parameters')
        event('tool_start',name=name,parameters=parameters)
        start=perf_counter()
        result=original_tool(self,name,parameters)
        event('tool_end',name=name,result_id=result.get('result_id'),kind=result.get('kind'),duration_ms=(perf_counter()-start)*1000)
        return result

    def acquire(self):
        original_acquire(self)
        identity=dict(self.handle.execute("SELECT current_user AS role,session_user AS login_role,current_database() AS database,current_setting('default_transaction_read_only') AS read_only,current_setting('transaction_read_only') AS transaction_read_only").fetchone())
        expected=dict(role='rag_audit_ro',login_role='rag_audit_ro',database='reviews',read_only='on',transaction_read_only='on')
        if identity!=expected:
            self.release();return deny('worker_lock_identity')
        event('worker_lock_acquired',identity=identity,backend_pid=self.handle.raw.info.backend_pid,advisory_key=self.handle._lock_key())

    def embedding(self,texts):
        if texts!=[QUESTION] or not getattr(CURRENT,'task_id',None):return deny('embedding_input')
        take('query_embedding');event('embedding_start',question=QUESTION)
        result=original_embedding(self,texts)
        vector=result[0]
        event('embedding_end',metrics=self.last_metrics,dimension=len(vector),
              norm=sum(v*v for v in vector)**.5,vector_sha256=hashlib.sha256(json.dumps(vector).encode()).hexdigest())
        return result

    def rpc(self,payload,deadline):
        operation=payload.get('operation')
        if operation not in ('initialize','encode'):return deny('unapproved_model_rpc')
        if operation=='encode' and (COUNTS['query_embedding']!=1 or payload.get('texts')!=[QUESTION]):return deny('unbound_model_rpc')
        take('model_initialize' if operation=='initialize' else 'model_encode_rpc')
        event('model_rpc_start',operation=operation,process_id=self.process.pid if self.process else None)
        result=original_rpc(self,payload,deadline)
        event('model_rpc_end',operation=operation,process_id=self.process.pid if self.process else None,model_load_ms=result.get('model_load_ms'),metrics=result.get('metrics'))
        return result

    def embedding_close(self):
        p=self.process
        original_embedding_close(self)
        event('embedding_closed',process_id=p.pid if p else None,exit_code=p.poll() if p else None,
              process_cleared=self.process is None,reader_stopped=self.reader is None or not self.reader.is_alive())

    PostgresConnection.__init__=initialize;PostgresConnection._execute=execute;PostgresConnection.close=close
    PostgresConnection.executemany=blocked('business_bulk_sql')
    store.enqueue=enqueue;submission.enqueue=enqueue
    # App imports are deliberately after installation, so route closures capture this guard.
    TaskWorker._execute=task;WorkerLock.acquire=acquire;ReviewToolContext.execute=tool
    PersistentEmbeddingProcess.embed_texts=embedding;PersistentEmbeddingProcess._rpc=rpc;PersistentEmbeddingProcess.close=embedding_close
    LocalEmbeddingProcess.encode=blocked('alternate_encoder')
    review_retrieval.LocalBgeM3Embedder=blocked('inprocess_encoder')
    openai.OpenAI=blocked('paid_llm_client');openai.AsyncOpenAI=blocked('paid_async_llm_client')
    httpx.Client.send=blocked('external_http');httpx.AsyncClient.send=blocked('external_async_http')
    def audit(kind,args):
        if kind=='socket.connect':
            address=args[1]
            if not local_address(address):return deny('outgoing_network')
    sys.addaudithook(audit)
    event('guard_ready',limits=LIMITS,paid_llm=0,embedding_sync=0,business_writes=0,fake_results=False)

    import uvicorn
    original_run=uvicorn.Server.run
    def run(server,*a,**kw):
        done=threading.Event()
        def watch():
            while not done.wait(.2):
                expired=STARTED is not None and monotonic()-STARTED>=900
                if expired or (OUT/'stop-server').exists():
                    event('shutdown_requested',deadline_expired=expired)
                    server.should_exit=True
                    return
        thread=threading.Thread(target=watch,name='p7c-deadline-control',daemon=True);thread.start()
        try:return original_run(server,*a,**kw)
        finally:done.set();thread.join(timeout=1);event('server_ended')
    uvicorn.Server.run=run
