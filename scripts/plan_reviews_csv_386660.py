"""One-product, read-only candidate plan. Default makes no database connection."""
import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
PRODUCT = '100019386660'
SOURCE, DATASET = 'reviews_csv', 'reviews-csv'
PROFILE = '1b58dc189d7d3ea6a55713a989f4555837eb83492690afdc020d808a268a5304'
LIMIT = 50
OUTPUT = ROOT / '.cache' / 'rag-386660-plan'


class StopPlan(Exception):
    pass


def require(condition, code):
    if not condition:
        raise StopPlan(code)


def select_groups(documents, usable, existing):
    """Select only absent rows, never split the missing part of a review group."""
    groups = defaultdict(list)
    keys = set()
    for doc in documents:
        key = doc.stable_versioned_opinion_key
        require(key not in keys, 'DUPLICATE_CURRENT_KEY')
        require((doc.source, doc.dataset_version, doc.product_id) == (SOURCE, DATASET, PRODUCT), 'PRODUCT_SCOPE_MISMATCH')
        keys.add(key)
        groups[doc.review_id].append(doc)
    require(usable <= keys, 'USABLE_KEY_OUTSIDE_CORPUS')
    selected, skipped_conflict = [], 0
    for review_id in sorted(groups):
        missing = [d for d in groups[review_id] if d.stable_versioned_opinion_key not in usable]
        if any(d.stable_versioned_opinion_key in existing for d in missing):
            skipped_conflict += 1
            continue
        if len(selected) + len(missing) <= LIMIT:
            selected.extend(missing)
    require(len(selected) <= LIMIT and not (set(d.stable_versioned_opinion_key for d in selected) & existing), 'SELECTION_INVARIANT')
    return selected, skipped_conflict


def local_preflight():
    from data_analysis_agent.core.config import read_settings
    from data_analysis_agent.core.serialization import content_sha256
    from data_analysis_agent.reviews.scope import model_identity
    from data_analysis_agent.reviews.prompts import PROMPT_VERSION
    from data_analysis_agent.reviews.embedding_store import SPACE_ID
    from scripts.audit_all_products_rag_coverage import static_check
    from scripts.reviews_csv_embedding_pilot_dry_run import SPACE
    values = read_settings(ROOT / '.env')
    identity = model_identity(values.get('LLM_MODEL_ID'), values.get('LLM_BASE_URL'), PROMPT_VERSION, required=True)
    require(content_sha256(list(identity)) == PROFILE and SPACE_ID == SPACE, 'PROFILE_OR_SPACE_CHANGED')
    static_check()
    return identity


def readonly_plan(identity):
    from data_analysis_agent.core.database import PostgresConnection
    from data_analysis_agent.core.serialization import content_sha256
    from data_analysis_agent.reviews.store import check_review_schema
    from data_analysis_agent.reviews.embedding_store import SPACE_ID, _eligible, validate_vector
    from data_analysis_agent.reviews.rag_corpus import TEXT_BUILDER_VERSION
    from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, _verified_sources
    from scripts.audit_all_products_rag_coverage import FIELDS, classify_vectors

    connection = None
    try:
        connection = PostgresConnection(urlunsplit(('postgresql', 'rag_audit_ro@127.0.0.1:5432', '/reviews', 'hostaddr=127.0.0.1', '')), readonly=True)
        connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for sql in ("SET LOCAL statement_timeout='15s'", "SET LOCAL lock_timeout='3s'", "SET LOCAL TIME ZONE 'UTC'",
                    "SET LOCAL transaction_timeout='110s'"):
            connection.execute(sql)
        tx = connection.execute("""SELECT current_database(),current_user,session_user,
            current_setting('transaction_isolation'),current_setting('transaction_read_only'),current_setting('TimeZone'),
            txid_current_snapshot()::text,transaction_timestamp()::text""").fetchone()
        require(tuple(tx[:6]) == ('reviews', 'rag_audit_ro', 'rag_audit_ro', 'repeatable read', 'on', 'UTC'), 'IDENTITY_MISMATCH')
        check_review_schema(connection)
        relations = connection.execute("""SELECT c.relrowsecurity,c.relforcerowsecurity,has_table_privilege(current_user,c.oid,'SELECT')
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(?::text[])""", (['products','reviews','import_runs',
                'review_annotations','review_opinions','review_workbench_schema','review_human_reviews',
                'review_human_opinions','review_opinion_embeddings'],)).fetchall()
        require(len(relations) == 9 and all(not row[0] and not row[1] and row[2] for row in relations), 'PERMISSIONS_OR_RLS_UNKNOWN')
        item = connection.execute("""SELECT count(r.id),min((r.published_at::timestamptz AT TIME ZONE 'UTC')::date),
            max((r.published_at::timestamptz AT TIME ZONE 'UTC')::date)
            FROM products p LEFT JOIN reviews r ON r.source=p.source AND r.dataset_version=p.dataset_version
            AND r.product_id=p.product_id WHERE p.source=? AND p.dataset_version=? AND p.product_id=?""",
            (SOURCE, DATASET, PRODUCT)).fetchone()
        require(item is not None and item[0] > 0 and item[1] is not None and item[2] is not None, 'PRODUCT_OR_DATES_UNAVAILABLE')
        options = dict(source=SOURCE, dataset_version=DATASET, product_ids=[PRODUCT], date_from=str(item[1]),
            date_to=str(item[2]), as_of=None, model=identity[0], base_url=identity[1], prompt_version=identity[2])
        scope = _scope(options)
        with PostgresSemanticRepository(connection).snapshot(scope) as snapshot:
            corpus = snapshot.corpus()
            docs = {d.stable_versioned_opinion_key:d for d in corpus.documents}
            states = {s.review_id:s for s in corpus.reviews}
            require(len(states) == item[0] and len(docs) == len(corpus.documents) and corpus.label_profile_key == PROFILE, 'CORPUS_IDENTITY_MISMATCH')
            require(len(docs) <= 10000, 'CORPUS_TOO_LARGE')
            keys = tuple(docs)
            verified = _verified_sources(snapshot.load_sources(keys), keys, scope) if keys else {}
            require(set(verified) == set(keys), 'SOURCE_VERIFICATION_FAILED')
            eligible = {v['key']:v['snapshot'] for v in _eligible(corpus)}
            require(set(eligible) == set(keys), 'ELIGIBLE_KEY_MISMATCH')
            rows = [dict(r) for r in connection.execute('SELECT r.id,' + ','.join('r.'+f for f in FIELDS) + ''' ,
                r.stable_versioned_opinion_key,r.effective_snapshot_sha256,r.embedding_space_id,r.invalidated_at,
                r.embedding::text stored_vector FROM review_opinion_embeddings r
                WHERE r.source=? AND r.dataset_version=? AND r.product_id=? ORDER BY r.id''', (SOURCE, DATASET, PRODUCT))]
            require(len(rows) <= 20000, 'VECTOR_INVENTORY_TOO_LARGE')
            counts, usable, bad = classify_vectors(docs, states, eligible, rows, SPACE_ID, TEXT_BUILDER_VERSION, PROFILE, validate_vector)
            require(not bad and not any(counts[k] for k in ('unexplained_nonmember', 'annotation_mismatch_unverified',
                    'revision_mismatch_unverified', 'source_metadata_mismatch', 'invalid_vector')), 'VECTOR_IDENTITY_UNCERTAIN')
            # A conflicting stored key cannot be inserted without an UPDATE; never select it.
            existing_rows = connection.execute('''SELECT stable_versioned_opinion_key FROM review_opinion_embeddings
                WHERE embedding_space_id=? AND text_builder_version=? AND stable_versioned_opinion_key=ANY(?::text[])''',
                (SPACE_ID, TEXT_BUILDER_VERSION, list(keys))).fetchall()
            existing = {r[0] for r in existing_rows}
            require(len(existing) == len(existing_rows) and usable <= existing, 'STORED_KEY_INCONSISTENT')
            selected, conflicts = select_groups(corpus.documents, usable, existing)
            verify_tx = connection.execute('SELECT txid_current_snapshot()::text,transaction_timestamp()::text').fetchone()
            require(tuple(verify_tx) == (tx[6], tx[7]), 'SNAPSHOT_CHANGED')
            metadata = [{k:v for k,v in asdict(d).items() if k not in ('base_url','quote','embedding_input')} for d in selected]
            payload = dict(version='reviews-csv-386660-readonly-plan-v1', status='READONLY_PLAN',
                scope={k:v for k,v in options.items() if k not in ('model','base_url','prompt_version')},
                profile_key=PROFILE, embedding_space_id=SPACE_ID, text_builder_version=TEXT_BUILDER_VERSION,
                transaction_snapshot=tx[6], observed_at=tx[7], effective_opinions=len(docs), usable_vectors=len(usable),
                selected_count=len(selected), max_count=LIMIT, conflicting_review_groups=conflicts,
                corpus_sha256=content_sha256(asdict(corpus)), selected_documents=metadata,
                selected_review_snapshots={str(d.review_id):eligible[d.stable_versioned_opinion_key] for d in selected},
                historical_stored_keys_sha256=content_sha256(sorted(existing)),
                source_sha256={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in (
                    Path(__file__).resolve(), ROOT/'src/data_analysis_agent/reviews/embedding_store.py',
                    ROOT/'src/data_analysis_agent/reviews/rag_corpus.py', ROOT/'src/data_analysis_agent/reviews/semantic_retrieval.py',
                    ROOT/'src/data_analysis_agent/reviews/scope.py', ROOT/'src/data_analysis_agent/core/serialization.py',
                    ROOT/'scripts/audit_all_products_rag_coverage.py')},
                write_authorized=False, live_embedding_executed=False)
            payload['plan_sha256'] = content_sha256(payload)
        connection.rollback()
        connection.close()
        require(bool(connection.raw.closed), 'CONNECTION_CLOSE_UNCONFIRMED')
        connection = None
        return payload
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-authorized', action='store_true', help='One explicitly authorized local readonly plan.')
    args = parser.parse_args()
    if not args.plan_authorized:
        print(json.dumps({'status':'NOT_EXECUTED', 'database_connections':0, 'write_authorized':False}))
        return 0
    try:
        payload = readonly_plan(local_preflight())
        OUTPUT.mkdir(parents=True, exist_ok=True)
        filename = datetime.now(timezone.utc).strftime('plan-%Y%m%dT%H%M%S%fZ.json')
        path = OUTPUT / filename
        with path.open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(json.dumps(dict(status=payload['status'], selected_count=payload['selected_count'],
            effective_opinions=payload['effective_opinions'], usable_vectors=payload['usable_vectors'],
            plan_sha256=payload['plan_sha256'], path=str(path.relative_to(ROOT)), write_authorized=False), ensure_ascii=False))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'status':'BLOCKED', 'error':str(exc) if isinstance(exc, StopPlan) else type(exc).__name__,
                          'write_authorized':False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
