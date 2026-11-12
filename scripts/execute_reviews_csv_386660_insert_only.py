"""Frozen 386660 insert-only executor; real execution remains disabled pending isolation tests."""
import argparse
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from data_analysis_agent.core.serialization import content_sha256, strict_json_loads
from data_analysis_agent.core.database import PostgresConnection, _parameters
from data_analysis_agent.reviews.embedding_store import SPACE_ID, SOURCE_LOCK, _eligible, validate_vector
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION, read_effective_corpus
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, _scope, _verified_sources, load_review_contexts
from data_analysis_agent.reviews.store import check_review_schema
from scripts.plan_reviews_csv_386660 import PRODUCT, SOURCE, DATASET, PROFILE, LIMIT

PLAN_PATH = ROOT / '.cache/rag-386660-plan/plan-20261112T074413309304Z.json'
EXPECTED_PLAN = '6afd1067ee2aab47dd715f4863bd0361d34c844bd5e8cbbefcb44917263ab980'
WRITER_ROLE = 'rag_386660_writer'
RECEIPT_DIR = ROOT / '.cache/rag-386660-execution'
# Tests exercise the implementation with synthetic adapters only. No real execution entry exists yet.
ISOLATED_TEST_GATE = False
SOURCE_FILES = frozenset(('scripts/plan_reviews_csv_386660.py', 'scripts/audit_all_products_rag_coverage.py',
    'src/data_analysis_agent/reviews/embedding_store.py', 'src/data_analysis_agent/reviews/rag_corpus.py',
    'src/data_analysis_agent/reviews/semantic_retrieval.py', 'src/data_analysis_agent/reviews/scope.py',
    'src/data_analysis_agent/core/serialization.py'))
READ_TABLES = frozenset(('products','import_runs','reviews','review_annotations','review_opinions',
    'review_human_reviews','review_human_opinions','review_workbench_schema','review_labeling_schema',
    'review_database_schema','review_opinion_embeddings'))
FIELDS = ('source','dataset_version','product_id','review_id','annotation_id','label_profile_key',
          'opinion_source','model_opinion_id','human_opinion_id','human_revision_id','stable_versioned_opinion_key',
          'aspect','sentiment','quote_start','quote_end','review_content_sha256','embedding_input_sha256',
          'published_at','text_builder_version')
INSERT_SQL = ('INSERT INTO review_opinion_embeddings (' + ','.join(FIELDS) + ''',
                effective_snapshot_sha256,embedding_space_id,embedding,sync_run_id)
                VALUES (''' + ','.join('?' for _ in FIELDS) + ''',?,?,?::vector(1024),NULL) RETURNING id''')
SOURCE_LOCK_SQL = 'LOCK TABLE ' + SOURCE_LOCK + ', review_workbench_schema, review_labeling_schema, review_database_schema IN EXCLUSIVE MODE NOWAIT'


class ExecutionError(Exception):
    pass


def require(ok, code):
    if not ok:
        raise ExecutionError(code)


def check_writer_sql(sql, parameters=None):
    """Defense in depth for this executor; DB permissions remain independently required."""
    normalized = sql.strip()
    fixed = {'BEGIN ISOLATION LEVEL READ COMMITTED', "SET LOCAL statement_timeout='15s'",
             "SET LOCAL lock_timeout='3s'", "SET LOCAL TIME ZONE 'UTC'",
             'SET default_transaction_read_only = off',
             'LOCK TABLE review_opinion_embeddings IN SHARE ROW EXCLUSIVE MODE', SOURCE_LOCK_SQL}
    if normalized in fixed:
        require(parameters is None, 'WRITER_UNEXPECTED_PARAMETERS')
        return
    if normalized in (INSERT_SQL, _parameters(INSERT_SQL)):
        require(parameters is not None and len(parameters) == len(FIELDS)+3
                and tuple(parameters[:3]) == (SOURCE,DATASET,PRODUCT)
                and parameters[FIELDS.index('label_profile_key')] == PROFILE
                and parameters[FIELDS.index('text_builder_version')] == TEXT_BUILDER_VERSION
                and parameters[len(FIELDS)+1] == SPACE_ID, 'WRITER_INSERT_SCOPE_MISMATCH')
        return
    text = re.sub(r"'(?:[^']|'')*'", "''", normalized).upper()
    require(';' not in text and text.startswith(('SELECT ', 'WITH ', 'SHOW '))
            and not re.search(r'\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|DO|GRANT|REVOKE)\b',text)
            and not re.search(r'\b(SETVAL|PG_NOTIFY|DBLINK|LO_IMPORT|LO_EXPORT)\s*\(',text), 'WRITER_SQL_BLOCKED')


class GuardedWriter(PostgresConnection):
    def __init__(self, target):
        from psycopg.conninfo import conninfo_to_dict
        identity = conninfo_to_dict(target)
        require(identity.get('host') == '127.0.0.1' and identity.get('hostaddr') == '127.0.0.1'
                and identity.get('port','5432') == '5432' and identity.get('dbname') == 'reviews'
                and identity.get('user') == WRITER_ROLE and not identity.get('service'), 'WRITER_TARGET_MISMATCH')
        super().__init__(target, readonly=False)

    def _execute(self, sql, parameters=None):
        check_writer_sql(sql,parameters)
        return super()._execute(sql,parameters)


def read_and_validate_plan(path=PLAN_PATH):
    require(Path(path).resolve() == PLAN_PATH.resolve(), 'PLAN_PATH_MISMATCH')
    value = strict_json_loads(Path(path).read_text(encoding='utf-8'))
    require(type(value) is dict, 'INVALID_PLAN')
    digest = value.get('plan_sha256')
    require(digest == EXPECTED_PLAN and content_sha256({k:v for k,v in value.items() if k != 'plan_sha256'}) == digest,
            'PLAN_DIGEST_MISMATCH')
    require(value.get('version') == 'reviews-csv-386660-readonly-plan-v1' and value.get('status') == 'READONLY_PLAN'
            and value.get('write_authorized') is False and value.get('live_embedding_executed') is False, 'PLAN_STATE_INVALID')
    scope = value.get('scope', {})
    require((scope.get('source'),scope.get('dataset_version'),scope.get('product_ids'),scope.get('as_of')) ==
            (SOURCE,DATASET,[PRODUCT],None), 'PLAN_SCOPE_MISMATCH')
    require(value.get('profile_key') == PROFILE and value.get('embedding_space_id') == SPACE_ID
            and value.get('text_builder_version') == TEXT_BUILDER_VERSION, 'PLAN_PROFILE_OR_SPACE_MISMATCH')
    documents = value.get('selected_documents')
    require(type(documents) is list and 0 < len(documents) <= LIMIT and value.get('selected_count') == len(documents)
            and value.get('max_count') == LIMIT and value.get('usable_vectors') == 0, 'PLAN_COUNT_INVALID')
    keys = [d.get('stable_versioned_opinion_key') for d in documents]
    require(all(isinstance(k,str) and re.fullmatch(r'rag-opinion-v1:[0-9a-f]{64}',k) for k in keys)
            and len(set(keys)) == len(keys), 'PLAN_KEY_INVALID')
    require(all((d.get('source'),d.get('dataset_version'),d.get('product_id'),d.get('label_profile_key'),d.get('text_builder_version'))
                == (SOURCE,DATASET,PRODUCT,PROFILE,TEXT_BUILDER_VERSION) and type(d.get('review_id')) is int
                and d['review_id'] > 0 for d in documents), 'PLAN_DOCUMENT_SCOPE_MISMATCH')
    snapshots = value.get('selected_review_snapshots')
    require(type(snapshots) is dict and set(snapshots) == {str(d['review_id']) for d in documents}
            and all(isinstance(h,str) and re.fullmatch(r'[0-9a-f]{64}',h) for h in snapshots.values()), 'PLAN_REVIEW_GROUP_INVALID')
    require(isinstance(value.get('corpus_sha256'),str) and re.fullmatch(r'[0-9a-f]{64}',value['corpus_sha256']), 'PLAN_CORPUS_HASH_INVALID')
    sources = {key.replace('\\','/'): digest for key,digest in value.get('source_sha256',{}).items()}
    require(set(sources) == SOURCE_FILES, 'PLAN_SOURCE_LIST_MISMATCH')
    for name in SOURCE_FILES:
        require(sha256((ROOT/name).read_bytes()).hexdigest() == sources[name], 'PLAN_SOURCE_CHANGED')
    return value


def options_for(plan, identity):
    require(content_sha256(list(identity)) == PROFILE, 'IDENTITY_MISMATCH')
    return {**plan['scope'], 'model':identity[0], 'base_url':identity[1], 'prompt_version':identity[2]}


def verify_current(connection, plan, identity):
    """Caller owns a readonly RR snapshot or the production source locks."""
    options = options_for(plan, identity)
    date_row = connection.execute("""SELECT count(*), min((published_at::timestamptz AT TIME ZONE 'UTC')::date),
        max((published_at::timestamptz AT TIME ZONE 'UTC')::date) FROM reviews
        WHERE source=? AND dataset_version=? AND product_id=?""", (SOURCE,DATASET,PRODUCT)).fetchone()
    require(date_row is not None and date_row[1] is not None and date_row[2] is not None
            and (str(date_row[1]),str(date_row[2])) == (options['date_from'],options['date_to']), 'PLAN_DATE_RANGE_CHANGED')
    corpus = read_effective_corpus(connection, **options)
    require(len(corpus.reviews) == date_row[0] and corpus.label_profile_key == PROFILE
            and content_sha256(asdict(corpus)) == plan['corpus_sha256'], 'PLAN_CORPUS_CHANGED')
    docs = {d.stable_versioned_opinion_key:d for d in corpus.documents}
    require(len(docs) == len(corpus.documents), 'DUPLICATE_CORPUS_KEY')
    planned = plan['selected_documents']
    keys = [d['stable_versioned_opinion_key'] for d in planned]
    require(all(key in docs for key in keys), 'PLAN_SELECTED_KEY_MISSING')
    selected = [docs[key] for key in keys]
    metadata = [{k:v for k,v in asdict(d).items() if k not in ('base_url','quote','embedding_input')} for d in selected]
    require(metadata == planned, 'PLAN_SOURCE_METADATA_CHANGED')
    groups = defaultdict(list)
    for d in corpus.documents:
        groups[d.review_id].append(d)
    require(all({d.stable_versioned_opinion_key for d in groups[rid]} ==
                {d.stable_versioned_opinion_key for d in selected if d.review_id == rid}
                for rid in {d.review_id for d in selected}), 'PLAN_REVIEW_GROUP_CHANGED')
    eligible = {row['key']:row['snapshot'] for row in _eligible(corpus)}
    require(all(eligible.get(d.stable_versioned_opinion_key) == plan['selected_review_snapshots'][str(d.review_id)]
                for d in selected), 'PLAN_REVIEW_SNAPSHOT_CHANGED')
    existing = connection.execute('''SELECT stable_versioned_opinion_key FROM review_opinion_embeddings
        WHERE embedding_space_id=? AND text_builder_version=? AND stable_versioned_opinion_key=ANY(?::text[])''',
        (SPACE_ID,TEXT_BUILDER_VERSION,list(docs))).fetchall()
    existing_keys = [row[0] for row in existing]
    require(len(existing_keys) == len(set(existing_keys)), 'DUPLICATE_STORED_KEY')
    require(not set(existing_keys) & set(keys), 'PLAN_VECTOR_ALREADY_EXISTS')
    require(content_sha256(sorted(existing_keys)) == plan['historical_stored_keys_sha256'], 'STORED_KEYS_CHANGED')
    review_ids = sorted({d.review_id for d in selected})
    rows = load_review_contexts(connection, _scope(options), review_ids)
    contexts = {row['review_id']:row for row in rows}
    require(len(rows) == len(review_ids) and set(contexts) == set(review_ids), 'SOURCE_CONTEXT_MISSING')
    states = {state.review_id:state for state in corpus.reviews}
    sources = []
    for rid in review_ids:
        row = contexts[rid]
        for doc in groups[rid]:
            require(all(row[k] == getattr(doc,k) for k in ('source','dataset_version','product_id','review_id',
                    'published_at','file_sha256','line_number','import_run_id'))
                    and row['content_sha256'] == doc.review_content_sha256, 'SOURCE_CONTEXT_CHANGED')
        sources.append(ReviewSource(states[rid], tuple(groups[rid]), row['text']))
    require(set(_verified_sources(sources,keys,_scope(options))) == set(keys), 'SOURCE_AUTHENTICATION_FAILED')
    return selected


def check_identity(connection, readonly):
    row = connection.execute("""SELECT current_database(),current_user,session_user,
        current_setting('transaction_read_only'),current_setting('transaction_isolation'),current_setting('TimeZone')""").fetchone()
    role = 'rag_audit_ro' if readonly else WRITER_ROLE
    isolation = 'repeatable read' if readonly else 'read committed'
    require(tuple(row) == ('reviews',role,role,'on' if readonly else 'off',isolation,'UTC'), 'DATABASE_IDENTITY_MISMATCH')


def check_writer_permissions(connection):
    row = connection.execute("""SELECT r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,
        pg_has_role(current_user,'pg_write_all_data','MEMBER'),
        pg_has_role(current_user,c.relowner,'MEMBER'),c.relrowsecurity,c.relforcerowsecurity,
        has_table_privilege(current_user,c.oid,'INSERT'),
        has_table_privilege(current_user,c.oid,'UPDATE,DELETE,TRUNCATE'),
        has_any_column_privilege(current_user,c.oid,'UPDATE'),
        EXISTS(SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgenabled<>'D')
        FROM pg_catalog.pg_roles r JOIN pg_catalog.pg_class c ON c.oid='public.review_opinion_embeddings'::regclass
        WHERE r.rolname=current_user""").fetchone()
    require(row is not None and not any(row[:9]) and row[9] is True and not any(row[10:]), 'WRITER_PRIVILEGES_UNSAFE')


def check_source_permissions(connection):
    rows = connection.execute("""SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,
        has_table_privilege(current_user,c.oid,'SELECT') FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(?::text[])""", (sorted(READ_TABLES),)).fetchall()
    require(len(rows) == len(READ_TABLES) and {row[0] for row in rows} == READ_TABLES
            and all(row[1] in ('r','p') and not row[2] and not row[3] and row[4] for row in rows),
            'SOURCE_PERMISSIONS_OR_RLS_UNSAFE')


def execute_frozen_batch(plan, identity, readonly_factory, writer_factory, encoder):
    """Synthetic-only until real isolated PostgreSQL lock/permission tests pass."""
    require(ISOLATED_TEST_GATE is True, 'ISOLATED_POSTGRESQL_TESTS_REQUIRED')
    # Reject altered plans even if a caller attempts to inject a dict directly.
    require(content_sha256({k:v for k,v in plan.items() if k != 'plan_sha256'}) == EXPECTED_PLAN
            and plan.get('plan_sha256') == EXPECTED_PLAN, 'PLAN_DIGEST_MISMATCH')
    require(read_and_validate_plan() == plan, 'PLAN_FILE_CHANGED')
    require(type(encoder) is LocalEmbeddingProcess and
            Path(encoder.python) == (ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve() and
            Path(encoder.cache_dir) == (ROOT/'.cache/embedding-eval/hf/hub').resolve(), 'ENCODER_NOT_APPROVED')
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    receipt = RECEIPT_DIR / ('run-' + uuid4().hex + '.json')
    with receipt.open('x', encoding='utf-8') as handle:
        json.dump({'status':'RUNNING','plan_sha256':EXPECTED_PLAN,'commit':'NOT_ATTEMPTED'},handle)
    result = dict(version='reviews-csv-386660-insert-v1', plan_sha256=EXPECTED_PLAN,
                  started_at_utc=datetime.now(timezone.utc).isoformat(), status='FAILED', encoding_attempts=0, insert_attempts=0, inserted=0,
                  rollback=None, reader_rollback=None, reader_closed=None, writer_closed=None, commit='NOT_ATTEMPTED', error=None)
    reader = writer = None
    try:
        reader = readonly_factory()
        reader.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        reader.execute("SET LOCAL statement_timeout='15s'")
        reader.execute("SET LOCAL lock_timeout='3s'")
        reader.execute("SET LOCAL TIME ZONE 'UTC'")
        check_identity(reader, True)
        check_review_schema(reader)
        check_source_permissions(reader)
        selected = verify_current(reader,plan,identity)
        reader.rollback()
        result['reader_rollback'] = True
        reader.close()
        result['reader_closed'] = bool(reader.raw.closed)
        require(result['reader_closed'], 'READER_CLOSE_UNCONFIRMED')
        reader = None
        result['encoding_attempts'] = 1
        output = encoder.encode([d.embedding_input for d in selected])
        require(type(output) is dict and output.get('space_id') == SPACE_ID and type(output.get('vectors')) is list
                and len(output['vectors']) == len(selected), 'ENCODER_OUTPUT_INVALID')
        vectors = [validate_vector(v) for v in output['vectors']]
        writer = writer_factory()
        require(type(writer) is GuardedWriter, 'UNGUARDED_WRITER_BLOCKED')
        writer.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        writer.execute("SET LOCAL statement_timeout='15s'")
        writer.execute("SET LOCAL lock_timeout='3s'")
        writer.execute("SET LOCAL TIME ZONE 'UTC'")
        check_identity(writer, False)
        check_writer_permissions(writer)
        writer.execute('LOCK TABLE review_opinion_embeddings IN SHARE ROW EXCLUSIVE MODE')
        writer.execute(SOURCE_LOCK_SQL)
        require(read_and_validate_plan() == plan, 'PLAN_OR_SOURCES_CHANGED')
        check_writer_permissions(writer)
        check_review_schema(writer)
        check_source_permissions(writer)
        current = verify_current(writer,plan,identity)
        require(current == selected, 'PLAN_CHANGED_AFTER_ENCODING')
        for document, vector in zip(current,vectors):
            result['insert_attempts'] += 1
            row = writer.execute(INSERT_SQL,
                (*[getattr(document,f) for f in FIELDS],plan['selected_review_snapshots'][str(document.review_id)],
                 SPACE_ID,json.dumps(vector,allow_nan=False,separators=(',',':')))).fetchone()
            require(row is not None and type(row[0]) is int and row[0] > 0, 'INSERT_NOT_CONFIRMED')
        require(result['insert_attempts'] == plan['selected_count'] <= LIMIT, 'INSERT_COUNT_INVALID')
        result['commit'] = 'ATTEMPTING'
        writer.commit()
        result.update(status='COMPLETED', inserted=result['insert_attempts'], commit='CONFIRMED')
    except Exception as exc:
        result['error'] = str(exc) if isinstance(exc,ExecutionError) else type(exc).__name__
        if writer is not None:
            try:
                writer.rollback()
                result['rollback'] = True
            except Exception:
                result['rollback'] = False
            if result['commit'] == 'ATTEMPTING':
                result['commit'] = 'UNKNOWN'
    finally:
        for name, conn in (('reader_closed',reader),('writer_closed',writer)):
            if conn is not None:
                try:
                    conn.close()
                    result[name] = bool(conn.raw.closed)
                except Exception:
                    result[name] = False
                if not result[name]:
                    result['status'] = 'FAILED'
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    try:
        receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        result['receipt_status'] = 'SAVED'
    except OSError:
        # COMMIT may already be confirmed; never claim a rollback in this case.
        result['receipt_status'] = 'WRITE_FAILED'
        result['status'] = 'FAILED'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-plan', action='store_true', help='Check frozen local plan without database or encoding.')
    args = parser.parse_args()
    if not args.inspect_plan:
        print(json.dumps({'status':'WRITE_BLOCKED','reason':'ISOLATED_POSTGRESQL_TESTS_REQUIRED'}))
        return 2
    try:
        plan = read_and_validate_plan()
        print(json.dumps({'status':'PLAN_VALID','selected_count':plan['selected_count'],
                          'write_status':'BLOCKED','plan_sha256':plan['plan_sha256']}))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc) if isinstance(exc,ExecutionError) else type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
