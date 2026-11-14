"""F02 one-run gates. No authorization creation or automatic retries."""
import http.client
import json
import os
from pathlib import Path
import sys

import p7c_real_guard as base
from s1_guard import network_audit, validate_pg_target
from f02_state import LIMITS, RunLedger, safe_path

ROOT=Path(__file__).resolve().parents[2]
INSTALLED=False


def validate_authorization(value):
    RunLedger.validate(value,safe_path(base.OUT))


def install():
    global INSTALLED
    if INSTALLED:base.deny('s2_duplicate_install')
    value=json.loads((base.OUT/'claim.json').read_text(encoding='utf-8'))
    validate_authorization(value)
    base.LEDGER=RunLedger(base.OUT,value)
    base.LIMITS=dict(LIMITS);base.COUNTS=base.LEDGER.counts()
    INSTALLED=True
    sys.addaudithook(network_audit)
    base.install()
    import psycopg
    from data_analysis_agent.reviews import embedding,embedding_process,embedding_store,semantic_retrieval
    original_connect=psycopg.connect
    original_init=embedding_process.PersistentEmbeddingProcess.__init__
    original_popen=embedding_process.subprocess.Popen
    original_search=embedding_store._search_effective_opinions
    worker=(ROOT/'scripts/p7c_s2_guard/embedding_worker.py').resolve()
    python=(ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve()
    cache=(ROOT/'.cache/embedding-eval/hf/hub').resolve()

    def connect(conninfo='',**kw):
        validate_pg_target(conninfo,{k:kw[k] for k in ('host','hostaddr','port','dbname','user','service','servicefile') if k in kw})
        if kw.get('options','') not in ('','-c search_path=public -c timezone=UTC'):
            base.deny('s2_connection_options')
        kw['options']=(kw.get('options','')+' -c default_transaction_read_only=on').strip()
        return original_connect(conninfo,**kw)

    def initialize_process(self,executable,cache_dir,*,timeout=600,worker=None):
        base.LEDGER.check_task(getattr(base.CURRENT,'task_id',None))
        if (safe_path(executable)!=python or safe_path(cache_dir)!=cache or worker is not None
            or not 0<timeout<=600):base.deny('s2_local_model_boundary')
        return original_init(self,executable,cache_dir,timeout=timeout,worker=str(candidate_worker))

    candidate_worker=worker
    def popen(args,*a,**kw):
        base.LEDGER.check_task(getattr(base.CURRENT,'task_id',None))
        if args!=[str(python),'-B',str(worker)] or a:base.deny('s2_process_command')
        env={k:v for k,v in os.environ.items() if k.upper() in (
            'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA',
            'HOMEDRIVE','HOMEPATH','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')}
        env.update(PYTHONIOENCODING='utf-8',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                   HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
        sub=embedding_process.subprocess
        approved=dict(stdin=sub.PIPE,stdout=sub.PIPE,stderr=sub.DEVNULL,text=True,encoding='utf-8',
                      env=env,cwd=ROOT,bufsize=1,creationflags=sub.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if set(kw)-set(approved) or any(value!=approved[key] for key,value in kw.items()):
            base.deny('s2_process_options')
        if base.LEDGER.counts()['model_initialize']!=0:base.deny('s2_process_initialization_used')
        base.take('model_process_create')
        process=original_popen(args,**approved)
        base.CURRENT.model_process=process
        return process

    def search(db,vector,*,options,embedding_space_id,top_k=5,aspect=None,sentiment=None,corpus=None):
        from data_analysis_agent.agents.review_retrieval import retrieval_request
        request=retrieval_request(base.QUESTION)
        expected={**base.SCOPE,**base.IDENTITY};expected.pop('timezone');expected['as_of']=None
        actual=dict(options);actual.setdefault('as_of',None)
        base.LEDGER.check_task(getattr(base.CURRENT,'task_id',None))
        if (actual!=expected or embedding_space_id!=embedding_store.SPACE_ID or top_k!=5
            or aspect!=request.aspect or sentiment!=request.sentiment
            or corpus is not None and corpus.scope!={**expected,'timezone':'UTC'}):base.deny('s2_retrieval_scope')
        embedding_store.validate_vector(vector);base.LEDGER.check_vector(vector)
        if getattr(base.CURRENT,'retrieval_active',False):base.deny('s2_nested_retrieval')
        base.CURRENT.retrieval_active=True
        try:return original_search(db,vector,options=options,embedding_space_id=embedding_space_id,
                                   top_k=top_k,aspect=aspect,sentiment=sentiment,corpus=corpus)
        finally:base.CURRENT.retrieval_active=False

    psycopg.connect=connect
    embedding_process.PersistentEmbeddingProcess.__init__=initialize_process
    embedding_process.subprocess.Popen=popen
    embedding_store._search_effective_opinions=search
    semantic_retrieval.search_effective_opinions=search
    embedding.LocalBgeM3Embedder.__init__=base.blocked('s2_inprocess_model')
    http.client.HTTPConnection.request=base.blocked('s2_outbound_http')
    for name in ('sync_effective_opinions','migrate_embedding_store'):
        setattr(embedding_store,name,base.blocked('s2_vector_sync'))
    base.event('s2_guard_ready',stage='F02_CANDIDATE',limits=LIMITS,quota_storage='SQLite FULL/BEGIN IMMEDIATE')


class S2Requests:
    def __init__(self,application):self.application=application
    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan':return await self.application(scope,receive,send)
        if scope['type']=='http':
            if scope.get('method') in ('GET','HEAD','OPTIONS'):
                return await self.application(scope,receive,send)
            if scope.get('method')=='POST' and scope.get('path')=='/api/v1/tasks':
                base.take('http_submission')
                return await self.application(scope,receive,send)
        base.deny('s2_unapproved_http')
