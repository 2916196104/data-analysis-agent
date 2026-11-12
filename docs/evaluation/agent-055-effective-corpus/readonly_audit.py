"""Readonly 055 audit. Default: preflight; full audit requires an explicit authorization flag."""
import ast, json, logging, os, re, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlunsplit
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src'))
logging.disable(logging.CRITICAL)
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_store import SPACE_ID, _eligible, validate_vector
from data_analysis_agent.reviews.rag_corpus import _document
from data_analysis_agent.reviews.scope import review_scope, model_identity
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, _verified_sources
from data_analysis_agent.reviews.store import check_review_schema

PROFILE='1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
SPACE='embedding-space-v1:11e6754662dbd13fc56c6e624bc9416c1c2766f962787267fd4a57b1fa8d622e'
SCOPE=dict(source='reviews_csv',dataset_version='reviews-csv',product_ids=['100013588055'],
           date_from='2021-09-11',date_to='2022-08-26',as_of=None)
TABLES=['review_database_schema','review_labeling_schema','products','reviews','import_runs','review_annotations',
        'review_opinions','review_workbench_schema','review_human_reviews','review_human_opinions','review_opinion_embeddings']
METRICS=['scoped_reviews','text_reviews','successful_reviews','effective_opinions','model_opinions','human_opinions',
         'active_vectors','current_member_vectors','provenance_matched_vectors','snapshot_matched_vectors',
         'usable_vectors_U','missing_usable_opinions','effective_opinion_coverage','superseded_annotation_vectors',
         'superseded_human_vectors','unexplained_source_anomalies']

class StopAudit(Exception): pass
def require(ok,code):
    if not ok: raise StopAudit(code)

def safe_error(exc):
    if isinstance(exc,StopAudit): return str(exc)
    messages=[]; safe_codes=[]; seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc)); messages.append(str(exc).lower())
        code=getattr(exc,'code',None)
        if isinstance(code,str) and re.fullmatch('[a-z0-9_]+',code): safe_codes.append(code)
        exc=exc.__context__
    private=' '.join(messages)
    for fragment,code in [('no password supplied','AUDIT_PASSWORD_NOT_AVAILABLE'),
                          ('password authentication failed','AUDIT_AUTHENTICATION_REJECTED'),
                          ('connection refused','DATABASE_CONNECTION_REFUSED'),
                          ('permission denied','REQUIRED_READ_PERMISSION_DENIED'),
                          ('timeout','EXECUTION_TIMEOUT'),('timed out','EXECUTION_TIMEOUT')]:
        if fragment in private: return code
    return safe_codes[0] if safe_codes else 'AUDIT_FAILED_SAFE'


def exception_diagnostics(exc,stage):
    chain=[]; seen=set(); sqlstates=[]
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc)); code=getattr(exc,'sqlstate',None)
        code=code if isinstance(code,str) and re.fullmatch('[0-9A-Z]{5}',code) else None
        item=dict(type=type(exc).__name__,module=type(exc).__module__,sqlstate=code)
        for name in ('errno','winerror'):
            value=getattr(exc,name,None)
            if type(value) is int: item[name]=value
        chain.append(item)
        if code is not None: sqlstates.append(code)
        exc=exc.__context__
    return dict(failure_stage=stage,failure_exception_type=chain[0]['type'] if chain else None,
                sqlstate=sqlstates[0] if sqlstates else None,exception_chain=chain,
                sqlstate_note='Captured from driver exception chain; null means no SQLSTATE was exposed. Raw messages are omitted.')

def inspect_source():
    report=(ROOT/'docs/agent-055-effective-corpus-verification.md').read_text(encoding='utf-8')
    rows=re.findall(r'\| .((?:reviews|agents|core)/[^ ]+?\.py). \| .([a-f0-9]{64}). \|',report)
    require(len(rows)==10,'SOURCE_MANIFEST_MISSING')
    checks=[]
    for name,expected in rows:
        digest=sha256((ROOT/'src/data_analysis_agent'/name).read_bytes()).hexdigest()
        checks.append(dict(file=name,sha256=digest,matches=digest==expected))
    require(all(r['matches'] for r in checks),'SOURCE_REVISION_CHANGED')
    tree=ast.parse((ROOT/'src/data_analysis_agent/reviews/embedding_store.py').read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_search_effective_opinions')
    fields=next(ast.literal_eval(n.value) for n in ast.walk(fn) if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='fields' for t in n.targets))
    require(len(fields)==18 and SPACE_ID==SPACE,'PRODUCTION_CONTRACT_CHANGED')
    return checks,fields

def execute_once():
    require(sys.argv[1:] in ([],['--preflight'],['--effective-corpus-authorized']),'INVALID_MODE')
    mode='effective_corpus' if sys.argv[1:]==['--effective-corpus-authorized'] else 'preflight'
    started=time.monotonic(); now=datetime.now(timezone.utc)
    run_id=now.strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'docs/evaluation/agent-055-effective-corpus'/(mode+'-'+run_id+'.json')
    result=dict(version='agent055-effective-corpus-audit-v2',mode=mode,run_id=run_id,started_at_utc=now.isoformat(),
        effective_corpus_authorized=(mode=='effective_corpus'),effective_corpus_executed=False,
        status='PREPARING',stage='source_preflight',connection_attempts=0,database_connected=False,transaction_started=False,
        explicit_rollback=False,connection_closed=False,query_count=0,scope=dict(**SCOPE,label_profile_key=PROFILE,embedding_space_id=SPACE),
        limits=dict(connect_timeout_seconds=10,statement_timeout_seconds=15,lock_timeout_seconds=3,total_execution_seconds=120),
        metrics={key:None for key in METRICS},statements=[],
        credential_mechanism='LIBPQ_PASSWORD_FILE_NO_MANUAL_PASSWORD_READ',
        prohibited_actions=dict(database_mutations=0,embedding_calls=0,llm_calls=0,similarity_searches=0,agent_runs=0,worker_runs=0),
        runner_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    connection=None
    class AuditConnection(PostgresConnection):
        def __init__(self,target):
            try:
                super().__init__(target,readonly=True); result['database_connected']=True
            except Exception:
                raw=getattr(self,'raw',None)
                if raw is not None:
                    result['database_connected']=True; result['connection_closed']=bool(raw.closed)
                raise
        def _execute(self,statement,parameters=None):
            require(time.monotonic()-started<100,'EXECUTION_DEADLINE_APPROACHING')
            command=statement.lstrip().split(None,1)[0].upper()
            require(command in ('SELECT','WITH','SHOW','SET','BEGIN'),'NON_READ_STATEMENT_BLOCKED')
            item=dict(command=command,template_sha256=sha256(statement.encode()).hexdigest(),completed=False)
            result['statements'].append(item)
            if command in ('SELECT','WITH','SHOW'): result['query_count']+=1
            try:
                cursor=super()._execute(statement,parameters); item['completed']=True; return cursor
            except Exception as exc:
                item['sqlstate']=getattr(exc,'sqlstate',None); raise
    try:
        checks,fields=inspect_source(); result['source_checks']=checks; result['production_provenance_fields']=list(fields)
        require(not {'torch','transformers','openai'} & set(sys.modules),'MODEL_RUNTIME_IMPORTED')
        result['stage']='connect_fixed_audit_identity'
        target=urlunsplit(('postgresql','rag_audit_ro@127.0.0.1:5432','/reviews','',''))
        result['connection_attempts']=1
        connection=AuditConnection(target)
        del target
        result['stage']='transaction_preflight'
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY'); result['transaction_started']=True
        for sql in ["SET LOCAL statement_timeout='15s'","SET LOCAL lock_timeout='3s'","SET LOCAL TIME ZONE 'UTC'",
                    "SET LOCAL transaction_timeout='110s'"]: connection.execute(sql)
        identity_sql="""SELECT current_database() database_name,current_user effective_user,session_user login_user,
            current_setting('transaction_isolation') isolation_level,current_setting('transaction_read_only') read_only,
            current_setting('TimeZone') timezone,current_setting('statement_timeout') statement_timeout,
            current_setting('lock_timeout') lock_timeout,txid_current_snapshot()::text transaction_snapshot,
            transaction_timestamp()::text observed_at"""
        identity=dict(connection.execute(identity_sql).fetchone()); result['transaction']=identity
        require(identity['database_name']=='reviews' and identity['effective_user']==identity['login_user']=='rag_audit_ro','IDENTITY_MISMATCH')
        require(identity['isolation_level']=='repeatable read' and identity['read_only']=='on' and identity['timezone']=='UTC'
                and identity['statement_timeout']=='15s' and identity['lock_timeout']=='3s','TRANSACTION_SETTINGS_MISMATCH')
        result['preflight_status']='PASS'
        if mode=='preflight':
            result['status']='PREFLIGHT_PASS'
            result['stage']='preflight_complete'
            result['effective_corpus_status']='NOT_EXECUTED_REQUIRES_SEPARATE_AUTHORIZATION'
            result['controlled_semantic_retrieval_data_prerequisite']='UNDETERMINED'
            return
        result['effective_corpus_executed']=True
        result['stage']='required_objects_and_permissions'
        objects=[dict(r) for r in connection.execute("""SELECT t.name,c.oid IS NOT NULL object_exists,c.relkind,
            c.relrowsecurity,c.relforcerowsecurity,CASE WHEN c.oid IS NOT NULL THEN
            has_table_privilege(current_user,c.oid,'SELECT') END can_select FROM unnest(?::text[]) t(name)
            LEFT JOIN pg_catalog.pg_namespace n ON n.nspname='public'
            LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=t.name ORDER BY t.name""",(TABLES,))]
        result['required_objects']=objects
        require(len(objects)==11 and all(r['object_exists'] and r['can_select'] for r in objects),'REQUIRED_OBJECT_OR_SELECT_PERMISSION_MISSING')
        require(all(r['relkind'] in ('r','p') and not r['relrowsecurity'] and not r['relforcerowsecurity'] for r in objects),'NONSTANDARD_OBJECT_OR_RLS')
        require(connection.execute("SELECT has_schema_privilege(current_user,'public','USAGE')").fetchone()[0],'SCHEMA_USAGE_MISSING')
        check_review_schema(connection)
        storage=connection.execute("""SELECT format_type(a.atttypid,a.atttypmod),has_function_privilege(current_user,t.typoutput,'EXECUTE')
            FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
            WHERE a.attrelid='public.review_opinion_embeddings'::regclass AND a.attname='embedding' AND NOT a.attisdropped""").fetchall()
        require(len(storage)==1 and storage[0][0]=='vector(1024)' and storage[0][1],'VECTOR_OUTPUT_UNAVAILABLE')
        result['vector_storage']=dict(type=storage[0][0],output_function_executable=True)
        result['stage']='bind_scope_and_profile'
        where,filters,_=review_scope(**SCOPE,alias='r')
        baseline=[dict(r) for r in connection.execute('SELECT r.id,r.has_text FROM reviews r WHERE '+where,filters)]
        ids={r['id'] for r in baseline}
        utc_ids={r[0] for r in connection.execute("""SELECT r.id FROM reviews r WHERE r.source=? AND r.dataset_version=?
            AND r.product_id=? AND r.published_at::timestamptz>=?::timestamptz AND r.published_at::timestamptz<?::timestamptz""",
            ('reviews_csv','reviews-csv','100013588055','2021-09-11T00:00:00Z','2022-08-27T00:00:00Z'))}
        require(ids==utc_ids,'PRODUCTION_AND_UTC_RANGE_MISMATCH')
        result['metrics'].update(scoped_reviews=len(baseline),text_reviews=sum(r['has_text']!=0 for r in baseline))
        triads=connection.execute("""SELECT DISTINCT a.requested_model,a.base_url,a.prompt_version
            FROM review_annotations a JOIN reviews r ON r.id=a.review_id WHERE """+where,filters).fetchall()
        matches={model_identity(*tuple(r),required=True) for r in triads
                 if content_sha256(list(model_identity(*tuple(r),required=True)))==PROFILE}
        result['unique_profile_matches']=len(matches); require(len(matches)==1,'LABEL_PROFILE_NOT_UNIQUELY_BOUND')
        triad=next(iter(matches)); scope=_scope(dict(**SCOPE,model=triad[0],base_url=triad[1],prompt_version=triad[2]))
        result['stage']='production_effective_corpus'
        with PostgresSemanticRepository(connection).snapshot(scope) as snapshot:
            corpus=snapshot.corpus(); states={r.review_id:r for r in corpus.reviews}
            docs={d.stable_versioned_opinion_key:d for d in corpus.documents}
            require(set(states)==ids and len(states)==len(baseline) and corpus.label_profile_key==PROFILE,'CORPUS_SCOPE_OR_PROFILE_MISMATCH')
            require(len(docs)==len(corpus.documents) and sum(r.opinion_count for r in states.values())==len(docs),'CORPUS_COUNT_MISMATCH')
            result['metrics'].update(successful_reviews=sum(r.processing_status=='success' for r in states.values()),
                effective_opinions=len(docs),model_opinions=sum(d.opinion_source=='model' for d in docs.values()),
                human_opinions=sum(d.opinion_source=='human' for d in docs.values()))
            result['review_states']=dict(Counter(r.processing_status for r in states.values()))
            result['zero_opinion_reviews']=sum(r.opinion_count==0 for r in states.values())
            result['stage']='production_independent_sources'
            keys=tuple(docs); sources=snapshot.load_sources(keys) if keys else ()
            verified=_verified_sources(sources,keys,scope); require(set(verified)==set(docs),'SOURCE_KEY_SET_MISMATCH')
            eligible={e['key']:e['snapshot'] for e in _eligible(corpus)}
            result['source_verification']=dict(status='PASS',reviews=len(sources),opinions=len(verified),
                key_set_sha256=content_sha256(sorted(docs)),
                review_snapshot_set_sha256=content_sha256(sorted((r.review_id,r.effective_snapshot_sha256) for r in states.values())))
            result['stage']='candidate_vectors'
            vectors=[dict(r) for r in connection.execute('SELECT r.id,'+','.join('r.'+f for f in fields)+""",
                r.stable_versioned_opinion_key,r.effective_snapshot_sha256,r.embedding::text stored_vector
                FROM review_opinion_embeddings r WHERE """+where+"""
                AND r.label_profile_key=? AND r.embedding_space_id=? AND r.invalidated_at IS NULL ORDER BY r.id""",(*filters,PROFILE,SPACE))]
            result['metrics']['active_vectors']=len(vectors); require(all(v['review_id'] in ids for v in vectors),'VECTOR_PARENT_OUTSIDE_SCOPE')
            result['candidate_source_types']=dict(Counter(v['opinion_source'] for v in vectors))
            member=metadata=snapshots=numeric=0; usable=set(); verdicts=Counter(); details=[]
            for v in vectors:
                key=v['stable_versioned_opinion_key']; doc=docs.get(key)
                if doc is None:
                    state=states[v['review_id']]
                    old_rows=connection.execute("""SELECT r.source,r.dataset_version,r.product_id,r.id review_id,r.text review_text,
                        r.published_at,r.import_run_id,r.line_number,i.file_sha256,a.id annotation_id,
                        a.content_sha256 review_content_sha256,a.status processing_status,a.requested_model,a.base_url,a.prompt_version,
                        e.opinion_source,e.model_opinion_id,e.human_opinion_id,e.human_revision_id,
                        CASE WHEN e.opinion_source='model' THEN o.aspect ELSE ho.aspect END aspect,
                        CASE WHEN e.opinion_source='model' THEN o.sentiment ELSE ho.sentiment END sentiment,
                        CASE WHEN e.opinion_source='model' THEN o.quote ELSE ho.quote END quote,
                        CASE WHEN e.opinion_source='model' THEN o.quote_start ELSE ho.quote_start END quote_start,
                        CASE WHEN e.opinion_source='model' THEN o.quote_end ELSE ho.quote_end END quote_end
                        FROM review_opinion_embeddings e JOIN reviews r ON r.id=e.review_id
                        JOIN import_runs i ON i.id=r.import_run_id JOIN review_annotations a ON a.id=e.annotation_id AND a.review_id=r.id
                        LEFT JOIN review_opinions o ON o.id=e.model_opinion_id AND o.annotation_id=a.id
                        LEFT JOIN review_human_reviews h ON h.id=e.human_revision_id AND h.annotation_id=a.id
                        LEFT JOIN review_human_opinions ho ON ho.id=e.human_opinion_id AND ho.human_review_id=h.id
                        WHERE """+where+' AND e.id=?',(*filters,v['id'])).fetchall()
                    require(len(old_rows)==1,'HISTORICAL_SOURCE_MISSING_OR_OUTSIDE_SCOPE'); old=dict(old_rows[0])
                    require((old['requested_model'],old['base_url'],old['prompt_version'])==triad
                            and old['review_content_sha256']==v['review_content_sha256'],'HISTORICAL_SOURCE_VERSION_MISMATCH')
                    if state.review_content_sha256!=v['review_content_sha256']: reason='review_content_version_changed'
                    else:
                        rebuilt=_document(old,triad,PROFILE)
                        require(rebuilt.stable_versioned_opinion_key==key and all(v[f]==getattr(rebuilt,f) for f in fields),'HISTORICAL_SOURCE_KEY_OR_METADATA_MISMATCH')
                        if state.annotation_id is not None and state.annotation_id>v['annotation_id']: reason='superseded_annotation'
                        elif state.annotation_id==v['annotation_id'] and state.human_revision_id is not None and (
                                v['human_revision_id'] is None or state.human_revision_id>v['human_revision_id']): reason='superseded_human'
                        else: reason='nonmember_unexplained'
                else:
                    member+=1; same=all(v[f]==getattr(doc,f) for f in fields); snap=v['effective_snapshot_sha256']==eligible[key]
                    metadata+=same; snapshots+=snap; require(same,'STORED_PROVENANCE_MISMATCH')
                    reason='usable' if snap else 'effective_snapshot_mismatch'
                    if snap: usable.add(key)
                validate_vector(json.loads(v['stored_vector'])); numeric+=1; verdicts[reason]+=1
                details.append(dict(vector_id=v['id'],review_id=v['review_id'],key=key,verdict=reason))
            result['metrics'].update(current_member_vectors=member,provenance_matched_vectors=metadata,
                snapshot_matched_vectors=snapshots,usable_vectors_U=verdicts['usable'],missing_usable_opinions=len(set(docs)-usable),
                effective_opinion_coverage=len(usable)/len(docs) if docs else None,
                superseded_annotation_vectors=verdicts['superseded_annotation'],superseded_human_vectors=verdicts['superseded_human'],
                unexplained_source_anomalies=verdicts['nonmember_unexplained'])
            result['vector_verdicts']=dict(verdicts); result['candidate_details']=details
            result['numerical_integrity']=dict(checked=len(vectors),valid=numeric,invalid=0)
            result['source_mismatch_counts']=dict(provenance=member-metadata,snapshot=member-snapshots,unexplained=verdicts['nonmember_unexplained'])
            by_review=defaultdict(set); groups=defaultdict(Counter); months=defaultdict(Counter)
            for key,d in docs.items():
                by_review[d.review_id].add(key)
                for c in (groups[(d.opinion_source,d.aspect,d.sentiment)],months[d.published_at[:7]]):
                    c['total']+=1; c['covered' if key in usable else 'missing']+=1
            covered_reviews=Counter()
            for review_keys in by_review.values():
                covered=review_keys & usable; covered_reviews['full' if covered==review_keys else 'partial' if covered else 'none']+=1
            result['review_coverage']=dict(covered_reviews,effective_opinion_reviews=len(by_review),
                vector_covered_reviews=len({docs[k].review_id for k in usable}),raw_candidate_reviews=len({v['review_id'] for v in vectors}))
            result['opinion_coverage_groups']=[dict(opinion_source=k[0],aspect=k[1],sentiment=k[2],total=v['total'],covered=v['covered'],missing=v['missing']) for k,v in sorted(groups.items())]
            result['opinion_coverage_months']=[dict(month=k,total=v['total'],covered=v['covered'],missing=v['missing']) for k,v in sorted(months.items())]
            require(sum(verdicts.values())==len(vectors) and len(usable)==verdicts['usable'],'RECONCILIATION_FAILED')
            require(not verdicts['nonmember_unexplained'],'UNEXPLAINED_NONMEMBER_SOURCE')
        result['stage']='final_snapshot_check'; require(dict(connection.execute(identity_sql).fetchone())==identity,'SNAPSHOT_OR_SETTINGS_CHANGED')
        result['status']='COMPLETED'; result['controlled_semantic_retrieval_data_prerequisite']='READY_IN_THIS_SNAPSHOT' if usable else 'NO_USABLE_VECTORS'
    except Exception as exc:
        result['status']='BLOCKED'; result['failure_code']=safe_error(exc)
        result.update(exception_diagnostics(exc,result['stage']))
        if result['stage']=='connect_fixed_audit_identity' and result['failure_code']=='REQUIRED_READ_PERMISSION_DENIED':
            result['failure_code']='CONNECT_PERMISSION_DENIED'
        result['effective_corpus_status']='NOT_EXECUTED' if not result['effective_corpus_executed'] else 'INCOMPLETE'
        result['controlled_semantic_retrieval_data_prerequisite']='UNDETERMINED'
    finally:
        if connection is not None:
            try: connection.rollback(); result['explicit_rollback']=True
            except Exception: result['status']='BLOCKED'; result['cleanup_error']='ROLLBACK_NOT_CONFIRMED'
            finally:
                try: connection.close(); result['connection_closed']=bool(connection.raw.closed)
                except Exception: result['status']='BLOCKED'; result['cleanup_error']='CLOSE_NOT_CONFIRMED'
        else:
            result['rollback_applicability']='NO_ESTABLISHED_CONNECTION_OR_TRANSACTION'
            result['connection_closed']=None
            result['connection_close_applicability']='NO_CONNECTION_OBJECT_RETURNED'
        result['elapsed_seconds']=round(time.monotonic()-started,6); result['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        result['semantic_retrieval_executed']=False; result['semantic_retrieval_authorized']=False
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(status=result['status'],mode=mode,failure_code=result.get('failure_code'),
            failure_stage=result.get('failure_stage'),sqlstate=result.get('sqlstate'),exception_chain=result.get('exception_chain'),
            connection_attempts=result['connection_attempts'],database_connected=result['database_connected'],
            query_count=result['query_count'],metrics=result['metrics'],record_path=str(path)),ensure_ascii=False))


def supervised_main():
    if '--_worker' in sys.argv[1:]:
        sys.argv.remove('--_worker')
        execute_once()
        return
    if sys.argv[1:]==['--help']:
        print('Default/--preflight: one connection/settings check only. --effective-corpus-authorized: separately authorized full readonly audit. No automatic retries.')
        return
    require(sys.argv[1:] in ([],['--preflight'],['--effective-corpus-authorized']),'INVALID_MODE')
    mode='effective_corpus' if sys.argv[1:]==['--effective-corpus-authorized'] else 'preflight'
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    try:
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),*sys.argv[1:],'--_worker'],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120,check=False)
        summary=json.loads(completed.stdout)
        require(type(summary) is dict and summary.get('status') in ('BLOCKED','PREFLIGHT_PASS','COMPLETED'),'INVALID_RUNNER_SUMMARY')
        print(json.dumps(summary,ensure_ascii=False))
        raise SystemExit(0 if summary['status'] in ('PREFLIGHT_PASS','COMPLETED') else 2)
    except (subprocess.TimeoutExpired,ValueError,StopAudit) as exc:
        now=datetime.now(timezone.utc)
        code='SUPERVISOR_120_SECOND_DEADLINE' if isinstance(exc,subprocess.TimeoutExpired) else 'RUNNER_OUTPUT_UNAVAILABLE'
        evidence=dict(version='agent055-effective-corpus-audit-v2',mode=mode,status='BLOCKED',failure_code=code,
            failure_stage='supervisor',sqlstate=None,exception_chain=[dict(type=type(exc).__name__,sqlstate=None)],
            database_connected=None,connection_attempts=None,query_count=None,explicit_rollback=False,
            cleanup_note='Worker outcome/explicit cleanup cannot be certified. No retry.',
            metrics={key:None for key in METRICS},finished_at_utc=now.isoformat(),automatic_retry=False)
        path=ROOT/'docs/evaluation/agent-055-effective-corpus'/('supervisor-'+now.strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(status='BLOCKED',failure_code=code,record_path=str(path)),ensure_ascii=False))
        raise SystemExit(2)

if __name__=='__main__': supervised_main()

