"""Isolated PostgreSQL vector-transaction probe. Default: no connections, no writes.

This tests the real GuardedWriter SQL and the unmodified vector migration, not the
complete frozen-plan executor. It cannot authorize production execution.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from scripts import execute_reviews_csv_386660_insert_only as executor

ADMIN = 'rag386660_it_admin'
READER = 'rag386660_it_reader'
WRITER = 'rag_386660_writer'


class Stop(Exception):
    pass


def require(value, code):
    if not value:
        raise Stop(code)


def configuration():
    run_id = os.environ.get('RAG386660_IT_RUN_ID', '')
    port_text = os.environ.get('RAG386660_IT_PORT', '')
    sysid = os.environ.get('RAG386660_IT_SYSTEM_ID', '')
    password = os.environ.get('RAG386660_IT_SYNTHETIC_PASSWORD', '')
    require(re.fullmatch('[a-f0-9]{16}',run_id) is not None, 'INVALID_RUN_ID')
    require(port_text.isdecimal() and 1024 <= int(port_text) <= 65535
            and int(port_text) not in (5432,55441), 'INVALID_ISOLATED_PORT')
    require(re.fullmatch('[0-9]{10,30}',sysid) is not None, 'SYSTEM_ID_REQUIRED')
    require(re.fullmatch('[a-f0-9]{64}',password) is not None, 'SYNTHETIC_PASSWORD_REQUIRED')
    return dict(run_id=run_id, port=int(port_text), system_id=sysid, password=password,
                database='rag386660_it_' + run_id)


def connect(config, role):
    import psycopg
    return psycopg.connect(host='127.0.0.1',hostaddr='127.0.0.1',port=config['port'],
        dbname=config['database'],user=role,password=config['password'],connect_timeout=5,autocommit=True)


def verify_container(config):
    require(not any(os.environ.get(k) for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH')),
            'DOCKER_ENVIRONMENT_OVERRIDE')
    context = subprocess.run(['docker','context','inspect'],capture_output=True,text=True,timeout=8,check=False)
    require(context.returncode == 0,'LOCAL_DOCKER_CONTEXT_UNAVAILABLE')
    contexts=json.loads(context.stdout)
    require(type(contexts) is list and len(contexts)==1 and
            contexts[0].get('Endpoints',{}).get('docker',{}).get('Host') in
            ('npipe:////./pipe/dockerDesktopLinuxEngine','npipe:////./pipe/docker_engine'), 'NONLOCAL_DOCKER_CONTEXT')
    name = 'rag386660-it-' + config['run_id']
    inspected = subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=8,check=False)
    require(inspected.returncode == 0,'ISOLATED_CONTAINER_NOT_AVAILABLE')
    values = json.loads(inspected.stdout)
    require(type(values) is list and len(values) == 1,'CONTAINER_INSPECT_INVALID')
    obj = values[0]
    ports = obj.get('NetworkSettings',{}).get('Ports',{}).get('5432/tcp')
    all_ports = obj.get('NetworkSettings',{}).get('Ports',{})
    require(obj.get('Name') == '/' + name and obj.get('State',{}).get('Running') is True
            and obj.get('Config',{}).get('Image') == 'pgvector/pgvector:pg18'
            and (obj.get('Config',{}).get('Labels') or {}).get('rag386660.run_id') == config['run_id']
            and ports == [{'HostIp':'127.0.0.1','HostPort':str(config['port'])}]
            and all(key == '5432/tcp' or not value for key,value in all_ports.items())
            and all(mount.get('Type') != 'bind' for mount in obj.get('Mounts',[])), 'CONTAINER_IDENTITY_MISMATCH')
    command = ('export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -U '+ADMIN+' -d '+config['database']+
               " -AtX -c 'SELECT system_identifier FROM pg_control_system()'")
    probe = subprocess.run(['docker','exec',name,'sh','-c',command],capture_output=True,text=True,timeout=8,check=False)
    require(probe.returncode == 0 and probe.stdout.strip() == config['system_id'], 'CONTAINER_SYSTEM_ID_MISMATCH')


def verify_admin(connection, config):
    row = connection.execute("""SELECT current_database(),current_user,current_setting('server_version_num')::int,
        (SELECT system_identifier::text FROM pg_control_system())""").fetchone()
    require(row[0] == config['database'] and row[1] == ADMIN and 180000 <= row[2] < 190000
            and row[3] == config['system_id'], 'ISOLATED_IDENTITY_OR_VERSION_MISMATCH')


def vector_parameters(n, product=executor.PRODUCT):
    other = product != executor.PRODUCT
    data = dict(source=executor.SOURCE,dataset_version=executor.DATASET,product_id=product,
        review_id=2 if other else 1,annotation_id=2 if other else 1,label_profile_key=executor.PROFILE,
        opinion_source='model',model_opinion_id=52 if other else n,human_opinion_id=None,human_revision_id=None,
        stable_versioned_opinion_key='rag-opinion-v1:'+f'{n:064x}',aspect='logistics',sentiment='negative',
        quote_start=0,quote_end=3,review_content_sha256='a'*64,embedding_input_sha256='b'*64,
        published_at='2022-01-01T00:00:00.000+00:00',text_builder_version=executor.TEXT_BUILDER_VERSION)
    vector = json.dumps([1.0] + [0.0]*1023, separators=(',',':'))
    return (*[data[field] for field in executor.FIELDS], 'c'*64, executor.SPACE_ID, vector)


def bootstrap(config):
    from psycopg import sql
    connection = connect(config, ADMIN)
    try:
        verify_admin(connection,config)
        existing = connection.execute("""SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n
            ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')""").fetchone()[0]
        require(existing == 0, 'ISOLATED_DATABASE_NOT_EMPTY')
        found = connection.execute('SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)',([READER,WRITER],)).fetchall()
        require(not found, 'TEST_ROLE_ALREADY_EXISTS')
        connection.execute('BEGIN')
        try:
            connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
            ext=connection.execute("SELECT extversion FROM pg_catalog.pg_extension WHERE extname='vector'").fetchone()
            require(ext is not None and ext[0] == '0.8.7','PGVECTOR_VERSION_MISMATCH')
            for role in (READER,WRITER):
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(config['password'])))
            connection.execute('CREATE TABLE public.rag386660_test_scope(run_id text PRIMARY KEY,system_id text NOT NULL)')
            connection.execute('INSERT INTO public.rag386660_test_scope VALUES (%s,%s)',(config['run_id'],config['system_id']))
            connection.execute('''CREATE TABLE public.products(source text NOT NULL,dataset_version text NOT NULL,
                product_id text NOT NULL,PRIMARY KEY(source,dataset_version,product_id))''')
            for table in ('reviews','review_annotations','review_opinions','review_human_reviews','review_human_opinions'):
                connection.execute(sql.SQL('CREATE TABLE public.{} (id integer PRIMARY KEY)').format(sql.Identifier(table)))
            for table in ('import_runs','review_workbench_schema','review_labeling_schema','review_database_schema'):
                connection.execute(sql.SQL('CREATE TABLE public.{} (id integer PRIMARY KEY)').format(sql.Identifier(table)))
            migration=(ROOT/'migrations/003_review_opinion_embeddings.up.sql').read_text(encoding='utf-8')
            connection.execute(migration,prepare=False)
            connection.execute('INSERT INTO public.products VALUES (%s,%s,%s),(%s,%s,%s)',
                (executor.SOURCE,executor.DATASET,executor.PRODUCT,executor.SOURCE,executor.DATASET,'100013588055'))
            for table, count in (('reviews',2),('review_annotations',2),('review_opinions',52)):
                connection.execute(sql.SQL('INSERT INTO public.{} SELECT generate_series(1,%s)').format(sql.Identifier(table)),(count,))
            connection.execute(executor._parameters(executor.INSERT_SQL),vector_parameters(52,'100013588055'))
            for role in (READER,WRITER):
                connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                connection.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT INSERT ON public.review_opinion_embeddings TO {}').format(sql.Identifier(WRITER)))
            connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE public.review_opinion_embeddings_id_seq TO {}').format(sql.Identifier(WRITER)))
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
    finally:
        connection.close()


def guarded(connection):
    # Test adapter bypasses the production 5432/reviews constructor so a dedicated
    # database and separate port can be used. This is not the whole executor.
    instance=object.__new__(executor.GuardedWriter)
    instance.raw=connection
    instance.readonly=False
    instance.namespace='isolated-synthetic'
    return instance


def sqlstate(exc):
    seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        state=getattr(exc,'sqlstate',None)
        if isinstance(state,str): return state
        exc=exc.__cause__ or exc.__context__
    return None


def run(config):
    from psycopg import Error as PgError
    admin = connect(config,ADMIN)
    writer = reader = None
    not_run = ('reader_insert_denied','guarded_insert_commit','unique_conflict_atomicity','update_guard',
               'delete_guard','vector_lock','source_locks','database_update_denied','database_delete_denied',
               'product_level_database_enforcement','other_product_unchanged','full_executor','concurrency',
               'rls_triggers','transaction_timeout','interrupted_connection','commit_unknown')
    result = dict(status='WRITE_BLOCKED', evidence='REAL_PG_SUBPATH_ONLY', tests={k:'NOT_EXECUTED' for k in not_run},
                  complete_executor='NOT_EXECUTED',isolated_identity='UNVERIFIED', commit='NOT_ATTEMPTED')
    try:
        verify_admin(admin,config)
        mark=admin.execute('SELECT run_id,system_id FROM public.rag386660_test_scope').fetchall()
        require(mark == [(config['run_id'],config['system_id'])], 'FIXTURE_MARKER_MISMATCH')
        ext=admin.execute("SELECT extversion FROM pg_catalog.pg_extension WHERE extname='vector'").fetchone()
        require(ext is not None and ext[0]=='0.8.7','PGVECTOR_VERSION_MISMATCH')
        writer=connect(config,WRITER); reader=connect(config,READER)
        for connection, role in ((writer,WRITER),(reader,READER)):
            row=connection.execute('SELECT current_database(),current_user,session_user').fetchone()
            require(row==(config['database'],role,role),'TEST_ROLE_IDENTITY_MISMATCH')
        result['isolated_identity']='PASS'
        target_before=admin.execute('SELECT count(*) FROM public.review_opinion_embeddings WHERE product_id=%s',(executor.PRODUCT,)).fetchone()[0]
        sentinel_before=admin.execute("SELECT count(*),md5(string_agg(id::text||embedding::text,'' ORDER BY id)) FROM public.review_opinion_embeddings WHERE product_id='100013588055'").fetchone()
        require(target_before==0 and sentinel_before[0]==1,'FIXTURE_ALREADY_USED')
        # Server privilege check, not a simulated refusal.
        try:
            reader.execute('BEGIN')
            reader.execute(executor._parameters(executor.INSERT_SQL),vector_parameters(1))
        except PgError as exc:
            result['tests']['reader_insert_denied'] = 'PASS' if exc.sqlstate == '42501' else 'FAIL'
        else:
            result['tests']['reader_insert_denied'] = 'FAIL'
        finally:
            reader.execute('ROLLBACK')
        require(result['tests']['reader_insert_denied']=='PASS','READER_DENIAL_FAILED')
        guarded_writer=guarded(writer)
        guarded_writer.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        for index in (1,2,3):
            guarded_writer.execute(executor.INSERT_SQL,vector_parameters(index))
        result['commit']='ATTEMPTING'
        try:
            writer.execute('COMMIT')
        except Exception:
            result['commit']='UNKNOWN'
            raise
        result['commit']='CONFIRMED'
        count=admin.execute('SELECT count(*) FROM public.review_opinion_embeddings WHERE product_id=%s',(executor.PRODUCT,)).fetchone()[0]
        result['tests']['guarded_insert_commit']='PASS' if count==3 else 'FAIL'
        require(count==3,'INSERT_COUNT_MISMATCH')
        guarded_writer.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        try:
            guarded_writer.execute(executor.INSERT_SQL,vector_parameters(4))
            guarded_writer.execute(executor.INSERT_SQL,vector_parameters(1))
        except Exception as exc:
            writer.execute('ROLLBACK')
            count=admin.execute('SELECT count(*) FROM public.review_opinion_embeddings WHERE product_id=%s',(executor.PRODUCT,)).fetchone()[0]
            result['tests']['unique_conflict_atomicity']='PASS' if count==3 and sqlstate(exc)=='23505' else 'FAIL'
        else:
            writer.execute('ROLLBACK')
            result['tests']['unique_conflict_atomicity']='FAIL'
        for name, statement in (('update_guard',"UPDATE review_opinion_embeddings SET invalidated_at=now()"),
                                ('delete_guard','DELETE FROM review_opinion_embeddings')):
            try: guarded_writer.execute(statement)
            except executor.ExecutionError: result['tests'][name]='PASS'
            else: result['tests'][name]='FAIL'
        for name, statement in (('vector_lock','LOCK TABLE review_opinion_embeddings IN SHARE ROW EXCLUSIVE MODE'),
                                ('source_locks',executor.SOURCE_LOCK_SQL)):
            writer.execute('BEGIN')
            try:
                guarded_writer.execute(statement)
                result['tests'][name]='PASS'
            except Exception as exc:
                result['tests'][name]='BLOCKED_PERMISSION' if sqlstate(exc)=='42501' else 'FAIL'
            finally:
                writer.execute('ROLLBACK')
        for name,statement in (('database_update_denied',"UPDATE review_opinion_embeddings SET invalidated_at=now() WHERE false"),
                               ('database_delete_denied','DELETE FROM review_opinion_embeddings WHERE false')):
            writer.execute('BEGIN')
            try:
                writer.execute(statement)
                result['tests'][name]='FAIL'
            except PgError as exc:
                result['tests'][name]='PASS' if exc.sqlstate=='42501' else 'FAIL'
            finally:
                writer.execute('ROLLBACK')
        # The role receives table-level INSERT; probe whether the DB itself limits
        # another synthetic product. The probe is always rolled back.
        writer.execute('BEGIN')
        try:
            writer.execute(executor._parameters(executor.INSERT_SQL),vector_parameters(51,'100013588055'))
            result['tests']['product_level_database_enforcement']='BLOCKED_CROSS_PRODUCT_INSERT_ALLOWED'
        except PgError as exc:
            result['tests']['product_level_database_enforcement']='PASS' if exc.sqlstate=='42501' else 'FAIL'
        finally:
            writer.execute('ROLLBACK')
        after=admin.execute("SELECT count(*),md5(string_agg(id::text||embedding::text,'' ORDER BY id)) FROM public.review_opinion_embeddings WHERE product_id='100013588055'").fetchone()
        result['tests']['other_product_unchanged']='PASS' if after==sentinel_before else 'FAIL'
        result['tests'].update(full_executor='NOT_EXECUTED', concurrency='NOT_EXECUTED', rls_triggers='NOT_EXECUTED',
            transaction_timeout='NOT_EXECUTED', interrupted_connection='NOT_EXECUTED', commit_unknown='NOT_EXECUTED')
    except (Exception,KeyboardInterrupt) as exc:
        result['error'] = str(exc) if isinstance(exc,Stop) else type(exc).__name__
        result['sqlstate'] = sqlstate(exc)
    finally:
        result['connection_close_failures']=0
        for connection in (reader,writer,admin):
            if connection is not None:
                try: connection.close()
                except Exception: result['connection_close_failures'] += 1
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--bootstrap-authorized',action='store_true')
    group.add_argument('--run-authorized',action='store_true')
    args=parser.parse_args()
    if not (args.bootstrap_authorized or args.run_authorized):
        print(json.dumps({'status':'NOT_EXECUTED','database_connections':0,'production_write':'BLOCKED'}))
        return 0
    try:
        config=configuration()
        verify_container(config)
        if args.bootstrap_authorized:
            bootstrap(config)
            result={'status':'SYNTHETIC_FIXTURE_CREATED','production_write':'BLOCKED'}
        else:
            result=run(config)
        result['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        out=ROOT/'.cache/rag386660-isolated-tests'
        out.mkdir(parents=True,exist_ok=True)
        path=out/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        with path.open('x',encoding='utf-8') as handle: json.dump(result,handle,ensure_ascii=False,indent=2)
        print(json.dumps({'status':result['status'],'record':str(path.relative_to(ROOT))}))
        return 0 if args.bootstrap_authorized else 2
    except (Exception,KeyboardInterrupt) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc) if isinstance(exc,Stop) else type(exc).__name__,
                          'production_write':'BLOCKED'}))
        return 2


if __name__=='__main__':
    sys.exit(main())
