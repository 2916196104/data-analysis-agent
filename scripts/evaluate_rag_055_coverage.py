"""Measure the effect of full 055 opinion coverage with a session-local pgvector index."""

import argparse
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
import gzip
import json
import sys
from urllib.parse import urlsplit

from baseline_rag_055 import evaluate_case, load
from evaluate_retrieval_quality import ROOT, PROVENANCE, business_options, inventory, private_directory, write_json
from data_analysis_agent.core.config import read_settings
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess, PersistentEmbeddingProcess
from data_analysis_agent.reviews.embedding_store import SOURCE_LOCK, SPACE_ID, validate_vector
from data_analysis_agent.reviews.retrieval_evaluation import EvaluationError, require
from data_analysis_agent.reviews.semantic_retrieval import PostgresSemanticRepository, _scope, load_review_contexts


OUTPUT_ROOT = ROOT / 'outputs/rag-retrieval-quality'
BASELINE = OUTPUT_ROOT / '055-stage1-query-routing-20261121'
OBSERVATIONS = OUTPUT_ROOT / '055-stage0-20261121/reference-observations.json'
FIELDS = (*PROVENANCE, 'stable_versioned_opinion_key', 'effective_snapshot_sha256', 'embedding_space_id', 'embedding')
TEMP_TABLE = 'pg_temp.review_opinion_embeddings'
LIVE_TABLE = 'public.review_opinion_embeddings'


class SessionRepository(PostgresSemanticRepository):
    @contextmanager
    def snapshot(self, scope):
        self.db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            with super().snapshot(scope) as snapshot:
                yield snapshot
        finally:
            self.db.rollback()


def setup():
    settings = read_settings(ROOT / '.env')
    db = settings['DATABASE_URL']
    target = urlsplit(db)
    require(target.scheme in ('postgres', 'postgresql') and target.hostname in ('127.0.0.1', 'localhost', '::1')
            and target.path == '/reviews' and not target.query, 'local_reviews_postgresql_required')
    return settings, db, business_options(settings)


def distribution(corpus, available):
    by_group = defaultdict(Counter)
    for doc in corpus.documents:
        for group in ((doc.published_at[:7], doc.aspect, doc.sentiment), ('all', doc.aspect, doc.sentiment)):
            by_group[group]['effective'] += 1
            by_group[group]['indexed'] += int(doc.stable_versioned_opinion_key in available)
    return [dict(month=month, aspect=aspect, sentiment=sentiment, **counts)
            for (month, aspect, sentiment), counts in sorted(by_group.items())]


def encode_missing(settings, missing):
    encoder = LocalEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'])
    vectors, metrics = {}, []
    for start in range(0, len(missing), 300):
        batch = missing[start:start + 300]
        result = encoder.encode([doc.embedding_input for doc in batch])
        require(result['space_id'] == SPACE_ID and len(result['vectors']) == len(batch), 'encoding_binding_mismatch')
        require(result['metrics']['outbound_connection_attempts'] == 0, 'encoder_network_attempt')
        metrics.append(result['metrics'])
        for doc, vector in zip(batch, result['vectors'], strict=True):
            if vector is not None:
                vectors[doc.stable_versioned_opinion_key] = validate_vector(vector)
    return vectors, metrics


def insert_vectors(connection, table, corpus, vectors):
    require(table in (TEMP_TABLE, LIVE_TABLE), 'unknown_vector_table')
    docs = {doc.stable_versioned_opinion_key: doc for doc in corpus.documents}
    require(vectors.keys() <= docs.keys(), 'unexpected_vector_key')
    snapshots = {review.review_id: review.effective_snapshot_sha256 for review in corpus.reviews}
    rows = []
    for key, vector in vectors.items():
        doc = docs[key]
        rows.append((*[getattr(doc, field) for field in FIELDS[:-3]], snapshots[doc.review_id], SPACE_ID,
                     json.dumps(validate_vector(vector), allow_nan=False, separators=(',', ':'))))
    placeholders = ','.join(['?'] * (len(FIELDS) - 1) + ['?::vector(1024)'])
    cursor = connection.executemany(f'INSERT INTO {table} ({",".join(FIELDS)}) VALUES ({placeholders})', rows)
    require(cursor.rowcount == len(rows), 'incomplete_vector_insert')


def existing_vector_digest(connection, keys):
    rows = connection.execute(
        f'SELECT to_jsonb(e)::text FROM {LIVE_TABLE} e '
        'WHERE stable_versioned_opinion_key=ANY(?::text[]) AND embedding_space_id=? ORDER BY id',
        (sorted(keys), SPACE_ID))
    return content_sha256([row[0] for row in rows])


def assess_references(corpus, sources, old_rows, new_rows, original_keys, new_keys):
    docs = {doc.stable_versioned_opinion_key: doc for doc in corpus.documents}
    observations = load(OBSERVATIONS)['observations']
    checked = []
    for item in observations:
        reference = item['opinion']
        doc = docs[reference['key']]
        original = sources[doc.review_id]['text']
        require(original[doc.quote_start:doc.quote_end] == doc.quote == reference['quote'], 'reference_changed')
        cases = []
        for stage in item['stages']:
            case_id = stage['id']
            before = old_rows[case_id]['retrieval']
            after = new_rows[case_id]['retrieval']
            cases.append(dict(case_id=case_id,
                ranks=[next((hit['rank'] for hit in row['evidence'] if hit['key'] == reference['key']), None)
                       if row else None for row in (before, after)],
                allowed=[reference['key'] in row['citation_keys'] if row else None for row in (before, after)]))
        checked.append(dict(review_id=doc.review_id, key=reference['key'], indexed_before=reference['key'] in original_keys,
                            indexed_after=reference['key'] in new_keys, cases=cases))
    return checked


def experiment(output):
    settings, db, options = setup()
    baseline = load(BASELINE / 'results.json')
    scope = _scope(options)
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        before, corpus, stored = inventory(snapshot.connection, options)
        require(before == baseline['freeze']['inventory'], 'baseline_inventory_changed')
        require(before['invalid_count'] == 0, 'unexpected_invalid_vectors')
        sources = {row['review_id']: row for row in load_review_contexts(
            snapshot.connection, scope, [review.review_id for review in corpus.reviews])}
        catalog = [dict(row) for row in snapshot.connection.execute(
            'SELECT product_id,title,brand FROM products WHERE source=? AND dataset_version=?',
            (scope.source, scope.dataset_version))]
    require(len(sources) == len(corpus.reviews), 'incomplete_raw_source')
    for doc in corpus.documents:
        require(sources[doc.review_id]['text'][doc.quote_start:doc.quote_end] == doc.quote, 'source_span_mismatch')
    original_keys = {doc.stable_versioned_opinion_key for doc in stored}
    missing = [doc for doc in corpus.documents if doc.stable_versioned_opinion_key not in original_keys]
    vectors, metrics = encode_missing(settings, missing)
    require(bool(vectors), 'no_encoded_vectors')
    manifest = dict(initial_inventory_sha256=before['snapshot_sha256'], encoded_sha256=content_sha256(vectors),
                    encoded_keys=sorted(vectors))
    with gzip.open(output / 'vectors.json.gz', 'xt', encoding='utf-8') as cache:
        json.dump(vectors, cache, separators=(',', ':'), allow_nan=False)
    write_json(output / 'plan.json', manifest)

    # Only the temporary relation receives writes in the experiment.
    with closing(PostgresConnection(db, readonly=True)) as connection:
        repository = SessionRepository(connection)
        with repository.snapshot(scope) as snapshot:
            require(inventory(snapshot.connection, options)[0] == before, 'baseline_inventory_changed_before_temp')
        with connection:
            connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ WRITE')
            connection.execute(f'CREATE TEMP TABLE review_opinion_embeddings (LIKE {LIVE_TABLE} INCLUDING ALL)')
            connection.execute(
                f'INSERT INTO {TEMP_TABLE} ({",".join(FIELDS)}) SELECT {",".join(FIELDS)} FROM {LIVE_TABLE} '
                'WHERE stable_versioned_opinion_key=ANY(?::text[]) AND embedding_space_id=? '
                'AND invalidated_at IS NULL ORDER BY id', (sorted(original_keys), SPACE_ID))
            insert_vectors(connection, TEMP_TABLE, corpus, vectors)

        with repository.snapshot(scope) as snapshot:
            after, copied_corpus, index = inventory(snapshot.connection, options)
        augmented_keys = original_keys | vectors.keys()
        require(corpus == copied_corpus and {doc.stable_versioned_opinion_key for doc in index} == augmented_keys
                and after['invalid_count'] == 0 and after['stored_count'] == len(augmented_keys),
                'temporary_index_mismatch')
        encoder = PersistentEmbeddingProcess(settings['RAG_PYTHON'], settings['RAG_MODEL_CACHE'], timeout=90)
        try:
            results = [evaluate_case(case, options, catalog, repository, encoder) for case in baseline['cases']
                       if case['retrieval'] is not None]
        finally:
            encoder.close()

    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        current, live_corpus, _ = inventory(snapshot.connection, options)
    require(current == before and live_corpus == corpus, 'business_snapshot_changed')
    old_rows = {case['id']: case for case in baseline['cases']}
    new_rows = {case['id']: case for case in results}
    for row in results:
        require(row['retrieval'] is not None and
                row['retrieval']['query'] == old_rows[row['id']]['retrieval']['query'], 'retrieval_request_changed')
    write_json(output / 'results.json', dict(status='temporary_index_verified', baseline=str(BASELINE.name),
        business_before=before, business_after_readback=current, temporary_index=after,
        reviews_by_status=dict(Counter(review.processing_status for review in corpus.reviews)),
        reviews_with_opinions=sum(review.opinion_count > 0 for review in corpus.reviews),
        distribution_before=distribution(corpus, original_keys), distribution_temporary=distribution(corpus, augmented_keys),
        encoding_batches=metrics, overlength_count=len(missing)-len(vectors), cases=results,
        reference_observations=assess_references(corpus, sources, old_rows, new_rows, original_keys, augmented_keys)))
    return dict(before=before['valid_count'], temporary=after['valid_count'], cases=len(results), business_inserted=0)


def apply(output):
    _, db, options = setup()
    report = load(output / 'results.json')
    plan = load(output / 'plan.json')
    with gzip.open(output / 'vectors.json.gz', 'rt', encoding='utf-8') as cache:
        vectors = json.load(cache)
    expected = report['business_before']
    require(report['status'] == 'temporary_index_verified'
            and expected == load(BASELINE / 'results.json')['freeze']['inventory']
            and expected['snapshot_sha256'] == plan['initial_inventory_sha256']
            and content_sha256(vectors) == plan['encoded_sha256'] and sorted(vectors) == plan['encoded_keys']
            and len(vectors) == report['temporary_index']['valid_count'] - expected['valid_count']
            and len(vectors) == expected['not_indexed_count'],
            'backfill_receipts_do_not_match')
    receipt = output / 'apply-receipt.json'
    with receipt.open('x', encoding='utf-8') as file:
        json.dump(dict(status='started_requires_reconciliation_if_interrupted', plan_sha256=content_sha256(plan)), file)
    with closing(PostgresConnection(db)) as connection, connection:
        connection.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
        connection.execute("SET LOCAL lock_timeout='3s'")
        connection.execute("SET LOCAL statement_timeout='30s'")
        connection.execute(f'LOCK TABLE {LIVE_TABLE} IN SHARE ROW EXCLUSIVE MODE')
        connection.execute('LOCK TABLE ' + SOURCE_LOCK + ' IN EXCLUSIVE MODE NOWAIT')
        require(connection.execute("SELECT COUNT(*) FROM pg_trigger WHERE tgrelid='public.review_opinion_embeddings'::regclass "
                                   "AND NOT tgisinternal AND tgenabled <> 'D'").fetchone()[0] == 0,
                'unexpected_embedding_triggers')
        # inventory requires a read-only snapshot, so check the still-committed state through a second connection.
        scope = _scope(options)
        with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
            current, corpus, old = inventory(snapshot.connection, options)
            old_keys = {doc.stable_versioned_opinion_key for doc in old}
            require(current == expected and not (old_keys & vectors.keys()), 'business_snapshot_or_missing_set_changed')
            original_digest = existing_vector_digest(snapshot.connection, old_keys)
        insert_vectors(connection, LIVE_TABLE, corpus, vectors)
        require(existing_vector_digest(connection, old_keys) == original_digest, 'existing_vectors_changed_in_transaction')
    with PostgresSemanticRepository(db).snapshot(scope) as snapshot:
        after, after_corpus, new = inventory(snapshot.connection, options)
        require(existing_vector_digest(snapshot.connection, old_keys) == original_digest, 'existing_vectors_changed')
    require(after_corpus == corpus and
            {doc.stable_versioned_opinion_key for doc in new} ==
            (old_keys | vectors.keys()) and after['invalid_count'] == 0
            and after['not_indexed_count'] == 0,
            'committed_backfill_readback_mismatch')
    result = dict(status='applied', inserted=len(vectors), before=expected['valid_count'],
                  after=after['valid_count'], inventory_after=after, existing_vectors_sha256=original_digest,
                  plan_sha256=content_sha256(plan))
    write_json(receipt, result)
    return {key: result[key] for key in ('status', 'inserted', 'before', 'after')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply', action='store_true', help='Append the verified missing vectors to the local business index.')
    args = parser.parse_args()
    try:
        output = private_directory(args.output)
        if args.apply:
            result = apply(output)
        else:
            require(not any(output.iterdir()), 'use_empty_output_directory')
            result = experiment(output)
        print(json.dumps(result))
    except EvaluationError as exc:
        print(f'coverage_failed: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'coverage_failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
