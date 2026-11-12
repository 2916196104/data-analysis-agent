"""Synthetic tests only: no PostgreSQL, real embeddings, processes or saved real receipts."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from psycopg.pq import TransactionStatus
from scripts import run_055_real_rag_once as entry
from data_analysis_agent.core.database import PostgresConnection
from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.embedding_store import OpinionMatch, SPACE, SPACE_ID
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SnapshotInfo, _scope

IDENTITY = ('SYNTHETIC-LABEL', 'https://offline.invalid/v1', 'SYNTHETIC-PROMPT')
PROFILE = content_sha256(list(IDENTITY))
VECTOR = [1.0] + [0.0] * 1023
PRIVATE = 'PRIVATE_REVIEW_MARKER'
FAILURE = 'PRIVATE_FAILURE_MARKER postgresql://user:password@invalid/reviews'
IDENTITY_ROW = dict(database_name='reviews', effective_user='rag_audit_ro', login_user='rag_audit_ro',
    isolation_level='repeatable read', read_only='on', timezone='UTC', statement_timeout='15s', lock_timeout='3s')


def source(rid, positive):
    quote = '送货慢' if positive else '送货很快'
    text = quote + '。内部内容' + PRIVATE + '。'
    row = dict(source='reviews_csv', dataset_version='reviews-csv', product_id='100013588055',
        review_id=rid, annotation_id=100+rid, human_revision_id=None, processing_status='success',
        human_status='unreviewed', error_code=None, opinion_source='model', model_opinion_id=500+rid,
        human_opinion_id=None, review_content_sha256=content_sha256(['SYNTHETIC', rid, text]),
        published_at='2021-09-11T12:00:00.000+00:00', review_text=text,
        import_run_id=3, line_number=rid, file_sha256=content_sha256('SYNTHETIC-FILE'),
        quote=quote, quote_start=0, quote_end=len(quote), aspect='logistics',
        sentiment='negative' if positive else 'positive')
    doc = _document(row, IDENTITY, PROFILE)
    return ReviewSource(_review_state(row, (doc,), PROFILE), (doc,), text)


class Cursor:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return list(self.rows)


class Raw:
    def __init__(self, events):
        self.events, self.closed = events, False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def execute(self, sql, parameters=None):
        self.events.append(sql)
        if sql.startswith('BEGIN'):
            self.info.transaction_status = TransactionStatus.INTRANS
        if sql.startswith('SELECT current_database()'):
            return Cursor((IDENTITY_ROW,))
        return Cursor()

    def rollback(self):
        self.events.append('ROLLBACK')
        self.info.transaction_status = TransactionStatus.IDLE

    def close(self):
        self.events.append('CLOSE')
        self.closed = True


class EntryTests(unittest.TestCase):
    def run_fixture(self, *, positive=True, source_failure=False, encoder_failure=False,
                    validator_failure=False, cleanup_failure=False, connect_failure=False,
                    connect_rollback_failure=False, count=5):
        events = []
        sources = [source(i, positive) for i in range(1, count+1)]
        encoders, connections = [], []

        def initialize(conn, private_target, *, readonly=False, namespace='business'):
            self.assertTrue(readonly)
            if connect_failure:
                raise RuntimeError(FAILURE)
            conn.namespace, conn.readonly, conn.raw = namespace, True, Raw(events)
            connections.append(conn)
            if connect_rollback_failure:
                conn.raw.rollback = Mock(side_effect=RuntimeError(FAILURE))
                raise RuntimeError(FAILURE)

        class Encoder:
            space = SPACE

            def __init__(self, *args, **kwargs):
                self.calls, self.closed = [], False
                encoders.append(self)

            def embed_texts(self, texts):
                self.calls.append(texts)
                self_outer.assertEqual(connections[0].raw.info.transaction_status, TransactionStatus.IDLE)
                if encoder_failure:
                    raise RuntimeError(FAILURE)
                return [VECTOR]

            def close(self):
                self.closed = True
                if cleanup_failure:
                    raise RuntimeError(FAILURE)

        self_outer = self

        class Repository:
            def __init__(self, conn):
                self.conn = conn

            @contextmanager
            def snapshot(self, scope):
                conn = self.conn
                class Snapshot:
                    embedding_space_id, text_builder_version = SPACE_ID, 'quote-utf8-v1'
                    info = SnapshotInfo('SYNTHETIC-SNAPSHOT', datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat())

                    def search(self, vector, *, top_k, aspect, sentiment):
                        self_outer.assertEqual((top_k, aspect, sentiment), (5, None, None))
                        self_outer.assertEqual(vector, VECTOR)
                        # Fake raw driver only; exercise the runner's actual SQL once guard.
                        conn.execute('SELECT synthetic_embedding <=> ?::vector(1024) LIMIT 5', ('SYNTHETIC',)).fetchall()
                        return tuple(OpinionMatch(s.opinions[0], 0.1+i/100) for i, s in enumerate(sources))

                    def load_sources(self, keys):
                        if source_failure:
                            s = sources[0]
                            return (replace(s, state=replace(s.state, effective_snapshot_sha256='f'*64)), *sources[1:])
                        return tuple(sources)

                snapshot = Snapshot()
                snapshot.scope = scope
                yield snapshot

        scope = dict(**entry.RANGE, model=IDENTITY[0], base_url=IDENTITY[1], prompt_version=IDENTITY[2])
        with patch.object(entry, 'load_configuration', return_value=(scope, SimpleNamespace(python='SYNTHETIC', cache='SYNTHETIC', timeout=1))), \
             patch.object(entry, 'PersistentEmbeddingProcess', Encoder), \
             patch.object(entry, 'PostgresSemanticRepository', Repository), \
             patch.object(PostgresConnection, '__init__', initialize):
            if validator_failure:
                with patch.object(entry, 'validate_retrieval', return_value=False):
                    record = entry.run_once(authorized=True)
            else:
                record = entry.run_once(authorized=True)
        # These in-memory synthetic records are never written into the real record directory.
        record['test_fixture'] = 'SYNTHETIC_NOT_A_REAL_RETRIEVAL'
        self.assertNotIn(PRIVATE, json.dumps(record))
        self.assertNotIn(FAILURE, json.dumps(record))
        self.assertNotIn('offline.invalid', json.dumps(record))
        return record, events, encoders, connections

    def test_default_never_opens_database_or_encoder_and_writes_not_executed(self):
        with patch.object(entry, 'load_configuration') as config, \
             patch.object(entry, 'OnceConnection') as database, \
             patch.object(entry, 'PersistentEmbeddingProcess') as encoder:
            record = entry.run_once()
        config.assert_not_called()
        database.assert_not_called()
        encoder.assert_not_called()
        self.assertEqual(record['status'], 'NOT_EXECUTED')
        self.assertIsNone(record['category'])
        self.assertIsNone(record['retrieved_count'])
        self.assertIsNone(record['candidates'])
        self.assertTrue(all(n == 0 for n in record['calls'].values()))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = entry.save_artifacts(record, root)
            self.assertEqual(json.loads((root/relative).read_text(encoding='utf-8'))['status'], 'NOT_EXECUTED')
            report = root/entry.REPORT
            self.assertIn('NOT_EXECUTED', report.read_text(encoding='utf-8'))
            report.write_text('SYNTHETIC prior execution report', encoding='utf-8')
            entry.save_artifacts(record, root)
            self.assertEqual(report.read_text(encoding='utf-8'), 'SYNTHETIC prior execution report')

    def test_a_real_production_service_adapter_and_source_rules_use_prepared_vector_once(self):
        record, events, encoders, conns = self.run_fixture()
        self.assertEqual((record['status'], record['category']), ('COMPLETED', 'A'), record['error'])
        self.assertEqual(encoders[0].calls, [[entry.QUESTION]])
        self.assertEqual(record['retrieved_count'], 5)
        self.assertTrue(all(r['delivery_slow_supported'] and r['is_permitted_key'] for r in record['candidates']))
        self.assertEqual(record['calls']['top_k_sql_attempts'], 1)
        self.assertEqual(record['calls']['top_k_sql_completed'], 1)
        self.assertEqual(record['calls']['query_embedding_successes'], 1)
        self.assertTrue(all(record['cleanup'][key] for key in ('rollback', 'connection_closed', 'encoder_closed')))
        self.assertEqual(events[-2:], ['ROLLBACK', 'CLOSE'])
        self.assertTrue(conns[0].raw.closed and encoders[0].closed)
        self.assertNotIn('SELECT', json.dumps(record['candidates']))
        self.assertTrue(all(r['source_authentication'] == r['validator'] == 'PASS' for r in record['candidates']))
        for candidate in record['candidates']:
            self.assertTrue(candidate['stable_versioned_opinion_key'].startswith('rag-opinion-v1:'))
            self.assertEqual(len(candidate['effective_snapshot_sha256']), 64)
            self.assertEqual(candidate['manual_relevance'], 'NOT_REVIEWED')

    def test_b_insufficient_facts_preserves_authentic_candidates_without_general_absence_claim(self):
        record, *_ = self.run_fixture(positive=False)
        self.assertEqual(record['category'], 'B', record['error'])
        self.assertEqual(record['retrieved_count'], 5)
        self.assertTrue(all(not r['is_permitted_key'] and not r['delivery_slow_supported'] for r in record['candidates']))
        self.assertIn('不能证明', record['limitation'])

    def test_empty_top_k_is_b_and_not_fabricated_evidence(self):
        record, *_ = self.run_fixture(count=0)
        self.assertEqual(record['category'], 'B', record['error'])
        self.assertEqual(record['retrieved_count'], 0)
        self.assertEqual(record['candidates'], [])
        self.assertTrue(record['recall_count_differs_from_baseline_expectation'])

    def test_c_encoding_failure_closes_everything_without_sql_or_retry(self):
        record, _, encoders, conns = self.run_fixture(encoder_failure=True)
        self.assertEqual(record['category'], 'C')
        self.assertEqual(record['error']['stage'], 'query_embedding')
        self.assertEqual(record['calls']['query_embedding_attempts'], 1)
        self.assertEqual(record['calls']['top_k_sql_attempts'], 0)
        self.assertTrue(encoders[0].closed and conns[0].raw.closed)
        self.assertIsNone(record['candidates'])

    def test_c_connection_failure_does_not_encode_or_retry(self):
        record, _, encoders, _ = self.run_fixture(connect_failure=True)
        self.assertEqual(record['category'], 'C')
        self.assertEqual(record['error']['stage'], 'connect')
        self.assertEqual(record['calls']['connection_attempts'], 1)
        self.assertEqual(record['calls']['query_embedding_attempts'], 0)
        self.assertTrue(encoders[0].closed)
        self.assertIsNone(record['cleanup']['rollback'])

    def test_d_complete_source_snapshot_failure_withholds_candidates(self):
        record, *_ = self.run_fixture(source_failure=True)
        self.assertEqual(record['category'], 'D', record['error'])
        self.assertEqual(record['source_authentication'], 'FAIL')
        self.assertEqual(record['calls']['top_k_sql_completed'], 1)
        self.assertIsNone(record['candidates'])
        self.assertIsNone(record['retrieved_count'])
        self.assertFalse(record['publication_allowed'])

    def test_failed_connection_setup_still_closes_when_rollback_itself_fails(self):
        record, _, encoders, conns = self.run_fixture(connect_rollback_failure=True)
        self.assertEqual(record['category'], 'C')
        self.assertEqual(record['error']['stage'], 'connect')
        self.assertFalse(record['cleanup']['rollback'])
        self.assertTrue(record['cleanup']['connection_closed'])
        self.assertTrue(conns[0].raw.closed and encoders[0].closed)

    def test_d_validator_rejection_withholds_even_source_validated_results(self):
        record, *_ = self.run_fixture(validator_failure=True)
        self.assertEqual(record['category'], 'D', record['error'])
        self.assertEqual(record['source_authentication'], 'PASS')
        self.assertEqual(record['validator'], 'FAIL')
        self.assertIsNone(record['candidates'])
        self.assertFalse(record['publication_allowed'])

    def test_cleanup_failure_cannot_be_reported_as_a(self):
        record, *_ = self.run_fixture(cleanup_failure=True)
        self.assertEqual((record['status'], record['category']), ('FAILED', 'C'))
        self.assertEqual(record['error']['stage'], 'cleanup')
        self.assertIsNone(record['candidates'])
        self.assertFalse(record['publication_allowed'])

    def test_second_embedding_and_second_sql_are_blocked_before_actual_delegation(self):
        record = entry.new_record(True)
        encoder = SimpleNamespace(space=SPACE, embed_texts=Mock(return_value=[VECTOR]))
        once = entry.OnceEncoder(encoder, record)
        once.embed_texts([entry.QUESTION])
        with self.assertRaises(entry.RunFailure):
            once.embed_texts([entry.QUESTION])
        self.assertEqual(encoder.embed_texts.call_count, 1)
        events = []
        def initialize(conn, *args, **kwargs):
            conn.raw, conn.namespace, conn.readonly = Raw(events), 'business', True
        with patch.object(PostgresConnection, '__init__', initialize):
            conn = entry.OnceConnection(record)
            with self.assertRaises(entry.RunFailure):
                entry.OnceConnection(record)
            conn.execute('SELECT synthetic_embedding <=> ?::vector(1024)', ('SYNTHETIC',))
            with self.assertRaises(entry.RunFailure):
                conn.execute('SELECT synthetic_embedding <=> ?::vector(1024)', ('SYNTHETIC',))
        self.assertEqual(len([sql for sql in events if '<=>' in sql]), 1)


if __name__ == '__main__':
    unittest.main()

