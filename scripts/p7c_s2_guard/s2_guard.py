"""Independent S2 admission; reuse S1 network/readonly functions, never S1 limits."""
import http.client
import json
from pathlib import Path
import sys
import re

import p7c_real_guard as base
from s1_guard import numeric_loopback,network_audit,validate_pg_target,readonly_command

ROOT=Path(r'E:\AllProject\data-analysis-agent')
LIMITS=dict(http_submission=1,submission=1,worker_task=1,query_embedding=1,vector_search=1,
            model_initialize=1,model_encode_rpc=1,model_process_create=1)
INSTALLED=False

def validate_authorization(value):
    if (value.get('stage')!='P7-C-S2' or value.get('approved') is not True
        or value.get('question')!=base.QUESTION or value.get('scope')!=base.SCOPE
        or value.get('limits')!=LIMITS or value.get('observation_seconds')!=900
        or any(value.get(k)!=0 for k in ('paid_llm','vector_sync','pg_business_writes','automatic_retries'))):
        raise RuntimeError('explicit_independent_S2_authorization_required')

def install():
    global INSTALLED
    if INSTALLED:base.deny('s2_duplicate_install')
    validate_authorization(json.loads((base.OUT/'authorization.json').read_text(encoding='utf-8')))
    INSTALLED=True;base.LIMITS=dict(LIMITS);base.COUNTS=dict.fromkeys(LIMITS,0)
    base.local_address=numeric_loopback
    sys.addaudithook(network_audit)
    base.install()
    import psycopg
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.reviews import embedding,embedding_process,embedding_store
    original_connect=psycopg.connect
    original_public=PostgresConnection.execute
    original_execute=PostgresConnection._execute
    original_process_init=embedding_process.PersistentEmbeddingProcess.__init__
    def connect(conninfo='',**kw):
        validate_pg_target(conninfo,{k:kw[k] for k in ('host','hostaddr','port','dbname','user','service','servicefile') if k in kw})
        if re.search(r'transaction_read_only\s*=\s*(off|false|0)',kw.get('options',''),re.I):base.deny('s2_readonly_off')
        kw['options']=(kw.get('options','')+' -c default_transaction_read_only=on').strip()
        return original_connect(conninfo,**kw)
    def public(self,sql,parameters=None):
        readonly_command(sql)
        return original_public(self,sql,parameters)
    def execute(self,sql,parameters=None):
        readonly_command(sql)
        if '<=>' in sql:
            # The underlying candidate consumes vector_search before invoking real SQL.
            if not parameters or parameters[-1]!=5 or not getattr(base.CURRENT,'task_id',None):base.deny('s2_vector_binding')
            vector=json.loads(parameters[0])
            import hashlib
            base.event('top5_bound',top_k=5,dimension=len(vector),vector_sha256=hashlib.sha256(json.dumps(vector).encode()).hexdigest(),
                       scope=base.SCOPE,sql=sql)
        return original_execute(self,sql,parameters)
    def initialize_process(self,python,cache_dir,*,timeout=600,worker=None):
        expected_python=(ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve()
        expected_cache=(ROOT/'.cache/embedding-eval/hf/hub').resolve()
        if (not getattr(base.CURRENT,'task_id',None) or Path(python).resolve()!=expected_python
            or Path(cache_dir).resolve()!=expected_cache or worker is not None or not 0<timeout<=600):base.deny('s2_local_model_boundary')
        base.take('model_process_create')
        return original_process_init(self,python,cache_dir,timeout=timeout,worker=worker)
    psycopg.connect=connect;PostgresConnection.execute=public;PostgresConnection._execute=execute
    embedding_process.PersistentEmbeddingProcess.__init__=initialize_process
    embedding.LocalBgeM3Embedder.__init__=base.blocked('s2_inprocess_model')
    http.client.HTTPConnection.request=base.blocked('s2_outbound_http')
    for name in ('sync_effective_opinions','migrate_embedding_store'):
        setattr(embedding_store,name,base.blocked('s2_vector_sync'))
    base.event('s2_guard_ready',stage='P7-C-S2',limits=LIMITS,S1_startup_only_install_used=False)

class S2Requests:
    """Read requests pass through; exactly one POST /tasks may enter the real API."""
    def __init__(self,application):self.application=application
    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.application(scope,receive,send)
        if scope['type']=='http':
            method=scope.get('method');path=scope.get('path')
            if method in ('GET','HEAD','OPTIONS'):return await self.application(scope,receive,send)
            if method=='POST' and path=='/api/v1/tasks':
                base.take('http_submission')
                return await self.application(scope,receive,send)
        base.deny('s2_unapproved_http')
