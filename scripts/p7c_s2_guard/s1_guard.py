"""S1 startup-only boundary layered on the preserved P7-C guard candidate."""
import os
from pathlib import Path
import re

import p7c_real_guard as base
from f02_network import audit as network_audit

INSTALLED=False

def numeric_loopback(address):
    return (isinstance(address,tuple) and len(address) in (2,4)
            and address[0] in ('127.0.0.1','::1')
            and type(address[1]) is int and 1<=address[1]<=65535)

def validate_pg_target(target,overrides=None):
    from psycopg.conninfo import conninfo_to_dict
    try:
        info=conninfo_to_dict(str(target))
        info.update(overrides or {})
    except Exception:base.deny('s1_pg_address')
    if (info.get('host'),str(info.get('port')),info.get('dbname'),info.get('user'))!=('127.0.0.1','5432','reviews','rag_audit_ro'):
        base.deny('s1_pg_address')
    if info.get('hostaddr') not in (None,'127.0.0.1') or info.get('service') or info.get('servicefile'):
        base.deny('s1_pg_address')
    return info

def readonly_command(sql,parameters=None):
    from f02_sql import validate_sql
    if validate_sql(sql,parameters):base.deny('s1_vector_sql')

def install():
    global INSTALLED
    if INSTALLED:base.deny('s1_duplicate_install')
    INSTALLED=True
    base.LIMITS={k:0 for k in base.LIMITS};base.COUNTS={k:0 for k in base.COUNTS}
    base.local_address=numeric_loopback
    # Keep asyncio's numeric loopback socketpair, without granting DNS names.
    import sys
    sys.addaudithook(network_audit)
    base.install()
    import psycopg
    import http.client
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.jobs import store,submission
    from data_analysis_agent.jobs.worker import TaskWorker
    from data_analysis_agent.reviews import embedding,embedding_process,embedding_store,semantic_retrieval
    original_connect=psycopg.connect
    original_execute=PostgresConnection._execute
    original_public_execute=PostgresConnection.execute
    def connect(conninfo='',**kw):
        keys=('host','hostaddr','port','dbname','user','service','servicefile')
        validate_pg_target(conninfo,{k:kw[k] for k in keys if k in kw})
        if re.search(r'transaction_read_only\s*=\s*(off|false|0)',kw.get('options',''),re.I):
            base.deny('s1_readonly_connection_options')
        kw['options']=(kw.get('options','')+' -c default_transaction_read_only=on').strip()
        return original_connect(conninfo,**kw)
    def execute(self,sql,parameters=None):
        readonly_command(sql,parameters)
        if '<=>' in sql:base.deny('s1_vector_sql')
        return original_execute(self,sql,parameters)
    def public_execute(self,sql,parameters=None):
        return original_public_execute(self,sql,parameters)
    psycopg.connect=connect;PostgresConnection._execute=execute;PostgresConnection.execute=public_execute
    http.client.HTTPConnection.request=base.blocked('s1_outbound_http')
    for module in (store,submission):
        module.enqueue=base.blocked('s1_business_task')
    TaskWorker._execute=base.blocked('s1_business_task')
    for name in ('embed_texts','health','_rpc','_call'):
        setattr(embedding_process.PersistentEmbeddingProcess,name,base.blocked('s1_model_operation'))
    embedding_process.shared_embedding_process=base.blocked('s1_model_operation')
    embedding_process.LocalEmbeddingProcess.encode=base.blocked('s1_model_operation')
    embedding.LocalBgeM3Embedder.__init__=base.blocked('s1_model_operation')
    for name in ('sync_effective_opinions','migrate_embedding_store'):
        setattr(embedding_store,name,base.blocked('s1_vector_sync'))
    embedding_store.search_effective_opinions=base.blocked('s1_vector_search')
    embedding_store._search_effective_opinions=base.blocked('s1_vector_search')
    semantic_retrieval.search_effective_opinions=base.blocked('s1_vector_search')
    base.event('s1_guard_ready',mode='STARTUP_ONLY',limits=base.LIMITS,production_source_changed=False)

class HealthOnly:
    """Forward the real lifespan and health GET; reject all other HTTP paths."""
    def __init__(self,application):self.application=application
    async def __call__(self,scope,receive,send):
        if scope['type']=='lifespan' or (scope['type']=='http' and scope.get('method')=='GET' and scope.get('path')=='/api/v1/health'):
            return await self.application(scope,receive,send)
        if scope['type']=='websocket':
            return await send(dict(type='websocket.close',code=1008))
        await send(dict(type='http.response.start',status=403,headers=[(b'content-type',b'application/json')]))
        await send(dict(type='http.response.body',body=b'{"error":"P7-C-S1 startup checks only"}'))
