"""Offline harness tests. Native Agent/tools/Validator/Answer; synthetic I/O only."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import run_055_agent_e2e_once as entry
from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.database import PostgresConnection, _Row
from data_analysis_agent.reviews import analytics
from data_analysis_agent.reviews.embedding_store import OpinionMatch, SPACE, SPACE_ID
from data_analysis_agent.reviews.rag_corpus import _document, _review_state
from data_analysis_agent.reviews.semantic_retrieval import ReviewSource, SnapshotInfo
import test_055_real_rag_once as fixtures


class EntryTests(unittest.TestCase):
    def fixture(self, *, fail=None, tamper=None, positive=True, count=5):
        events, encoders, connections = [], [], []
        sources = [fixtures.source(i, positive) for i in range(1, count+1)]
        # Two independent opinion IDs in one current review: not two comments.
        if count >= 2:
            first = sources[0]
            row = {**asdict(first.state), **asdict(first.opinions[0]), 'review_text': first.review_text,
                   'processing_status': 'success', 'model_opinion_id': 999}
            second = _document(row, fixtures.IDENTITY, fixtures.PROFILE)
            docs = (first.opinions[0], second)
            sources[:2] = [ReviewSource(_review_state(row, docs, fixtures.PROFILE), docs, first.review_text)]

        class Cursor(fixtures.Cursor):
            def __iter__(self):
                return iter(self.rows)

        class Raw(fixtures.Raw):
            def execute(raw, sql, parameters=None):
                result = super().execute(sql, parameters)
                if 'FROM products' in sql:
                    return Cursor((_Row(('100013588055', 'SYNTHETIC', None), ('product_id', 'title', 'brand')),))
                return Cursor(result.rows)

        def initialize(conn, target, *, readonly=False, namespace='business'):
            self.assertTrue(readonly)
            if fail == 'connect':
                raise RuntimeError(fixtures.FAILURE)
            conn.namespace, conn.readonly, conn.raw = namespace, readonly, Raw(events)
            connections.append(conn)

        outer = self
        class Encoder:
            space = SPACE
            last_metrics = dict(cold_start=True)
            def __init__(self, *args, **kwargs):
                self.closed, self.calls = False, []
                encoders.append(self)
            def embed_texts(self, texts):
                self.calls.append(texts)
                outer.assertFalse(connections[0].in_transaction)
                if fail == 'encoding':
                    raise RuntimeError(fixtures.FAILURE)
                return [fixtures.VECTOR]
            def close(self):
                self.closed = True
                if fail == 'cleanup':
                    raise RuntimeError(fixtures.FAILURE)

        class Repository:
            def __init__(self, conn):
                self.conn = conn
            @contextmanager
            def snapshot(repo, scope):
                class Snapshot:
                    embedding_space_id, text_builder_version = SPACE_ID, 'quote-utf8-v1'
                    info = SnapshotInfo('SYNTHETIC', datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat())
                    def search(snapshot, vector, *, top_k, aspect, sentiment):
                        outer.assertEqual((top_k, aspect, sentiment), (5, None, None))
                        repo.conn.execute('SELECT synthetic_embedding <=> ?::vector(1024) LIMIT 5', ('SYNTHETIC',))
                        docs = [d for source in sources for d in source.opinions]
                        return tuple(OpinionMatch(d, 0.1+i/100) for i, d in enumerate(docs))
                    def load_sources(snapshot, keys):
                        if fail == 'source':
                            first = sources[0]
                            return (replace(first, state=replace(first.state, effective_snapshot_sha256='f'*64)), *sources[1:])
                        return tuple(sources)
                snapshot = Snapshot()
                snapshot.scope = scope
                yield snapshot

        scope = dict(**entry.RANGE, model=fixtures.IDENTITY[0], base_url=fixtures.IDENTITY[1], prompt_version=fixtures.IDENTITY[2],
                     baseline_from='2021-09-10', baseline_to='2021-09-10')
        settings = AgentSettings(model=fixtures.IDENTITY[0], base_url=fixtures.IDENTITY[1], api_key='SYNTHETIC-SECRET')
        summary = dict(review_count=7, eligible_text_reviews=7, successful_reviews=7, failed_reviews=0,
            unprocessed_reviews=0, negative_review_count=7, negative_rate=1., labeling_coverage=1., aspects={})
        native = entry.reviews.run_review_agent
        observed = []
        def call(*args, **kwargs):
            result = native(*args, **kwargs)
            observed.append(deepcopy(result))
            if tamper:
                tamper(result)
            return result

        with patch.object(entry, 'configuration', return_value=(scope, settings, SimpleNamespace(python='SYNTHETIC', cache='SYNTHETIC', timeout=1))), \
             patch.object(PostgresConnection, '__init__', initialize), \
             patch.object(entry.store, 'check_review_schema'), \
             patch.object(analytics, 'get_trend', return_value=dict(summary=summary)) as trend, \
             patch.object(entry, 'PersistentEmbeddingProcess', Encoder), \
             patch.object(entry, 'PostgresSemanticRepository', Repository), \
             patch.object(entry.reviews, 'run_review_agent', side_effect=call) as agent:
            record = entry.run_once(authorized=True)
        self.assertEqual(agent.call_count, 1)
        if trend.called:
            self.assertEqual(trend.call_args.kwargs['date_from'], '2021-09-11')
            self.assertNotIn('baseline_from', trend.call_args.kwargs)
        record['test_fixture'] = 'SYNTHETIC_NOT_REAL_EXECUTION'
        serialized = json.dumps(record, ensure_ascii=False)
        for secret in (fixtures.FAILURE, fixtures.PRIVATE, fixtures.IDENTITY[1], 'SYNTHETIC-SECRET', 'postgresql://'):
            self.assertNotIn(secret, serialized)
        return record, events, encoders, connections, observed

    def test_default_has_no_database_encoder_or_agent_calls(self):
        with patch.object(entry, 'configuration', return_value=({}, None, None)) as configuration, \
             patch.object(entry, 'ReadOnlyOnceConnection') as database, \
             patch.object(entry, 'PersistentEmbeddingProcess') as encoder, \
             patch.object(entry.reviews, 'run_review_agent') as agent:
            record = entry.run_once()
        configuration.assert_called_once()
        for resource in (database, encoder, agent):
            resource.assert_not_called()
        self.assertEqual(record['status'], 'NOT_EXECUTED')
        self.assertTrue(all(v == 0 for v in record['calls'].values()))
        self.assertIsNone(record['final_answer'])
        self.assertEqual(record['task_acceptance'], 'NOT_APPLICABLE')
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = entry.save_artifacts(record, root)
            self.assertEqual(json.loads((root/path).read_text(encoding='utf-8'))['status'], 'NOT_EXECUTED')

    def test_native_full_chain_prepares_once_and_deduplicates_reviews(self):
        record, events, encoders, connections, observed = self.fixture()
        self.assertEqual((record['status'], record['acceptance']), ('COMPLETED', 'PASS'), (record['error'], record['checks'], observed[0].get('error')))
        self.assertEqual(record['permitted_opinion_count'], 5)
        self.assertEqual(record['permitted_distinct_review_count'], 4)
        self.assertEqual(encoders[0].calls, [[entry.QUESTION]])
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(record['transactions']), 2)
        self.assertTrue(connections[0].raw.closed and encoders[0].closed)
        self.assertTrue(all(record['checks'].values()))
        self.assertEqual([e['tool'] for e in record['tools']], entry.TOOLS)
        self.assertTrue(all(v == 0 for v in record['model_calls'].values()))
        self.assertIn('本次检索', record['final_answer'])
        self.assertEqual(record['task_acceptance'], 'NOT_APPLICABLE')
        self.assertEqual([s['source_authentication'] for s in record['sources']], ['PASS']*5)

    def test_no_support_or_no_candidates_is_not_a_false_success(self):
        for args in (dict(positive=False), dict(count=0)):
            with self.subTest(args=args):
                record, *_ = self.fixture(**args)
                self.assertEqual(record['status'], 'FAILED')
                self.assertIsNone(record['final_answer'])
                self.assertFalse(record['publication_allowed'])

    def test_failures_close_resources_without_retry_or_sensitive_error_text(self):
        for fail in ('connect', 'encoding', 'source', 'cleanup'):
            with self.subTest(fail=fail):
                record, _, encoders, connections, _ = self.fixture(fail=fail)
                self.assertEqual(record['status'], 'FAILED')
                self.assertLessEqual(record['calls']['connection_attempts'], 1)
                self.assertLessEqual(record['calls']['query_embedding_attempts'], 1)
                self.assertLessEqual(record['calls']['top_k_sql_attempts'], 1)
                self.assertIsNone(record['final_answer'])
                self.assertFalse(record['publication_allowed'])
                self.assertTrue(all(c.raw.closed for c in connections))
                self.assertTrue(all(e.closed for e in encoders))

    def test_scope_citations_and_unjustified_population_claims_are_rejected(self):
        def cite(result):
            result['public_answer']['evidence_refs'][0]['opinions'][0]['opinion_key'] = 'forged-key'
        def scope(result):
            result['scope']['product_ids'] = ['OTHER']
        def generalize(result):
            result['public_answer']['summary'] = '所有评论都证明普遍送货慢；已检索891条未索引观点。'
        def validator(result):
            result['validation']['status'] = 'failed'
        for tamper in (cite, scope, generalize, validator):
            with self.subTest(tamper=tamper.__name__):
                record, *_ = self.fixture(tamper=tamper)
                self.assertEqual(record['status'], 'FAILED')
                self.assertIsNone(record['final_answer'])
                self.assertFalse(record['publication_allowed'])

    def test_nonreadonly_cte_baseline_and_second_calls_are_blocked_before_driver(self):
        record = entry.new_record(True)
        raw = fixtures.Raw([])
        conn = object.__new__(entry.ReadOnlyOnceConnection)
        conn.record, conn.raw, conn.readonly, conn.namespace = record, raw, True, 'SYNTHETIC'
        for sql, params in (("WITH x AS (DELETE FROM reviews RETURNING id) SELECT * FROM x", None),
                            ("SELECT 1; UPDATE reviews SET text=''", None),
                            ("SET default_transaction_read_only = off", None),
                            ("SELECT * FROM reviews WHERE published_at >= ?", ('2021-09-10',))):
            with self.subTest(sql=sql), self.assertRaises(entry.rag.RunFailure):
                conn.execute(sql, params)
        conn.execute('SELECT embedding <=> ?', ('SYNTHETIC',))
        with self.assertRaises(entry.rag.RunFailure):
            conn.execute('SELECT embedding <=> ?', ('SYNTHETIC',))
        self.assertEqual(record['calls']['top_k_sql_attempts'], 1)
        with self.assertRaises(entry.rag.RunFailure):
            conn.executemany('INSERT INTO x VALUES (?)', [(1,)])

    def test_full_body_quote_is_withheld_without_changing_acceptance_inputs(self):
        result = dict(answer='评论写道：“送货慢”', public_answer=dict(highlights=[dict(finding_ref='key', text='送货慢')],
            evidence_refs=[dict(opinions=[dict(opinion_key='key', quote='送货慢')])]))
        original = deepcopy(result)
        answer, public, redacted = entry.publishable_answer(result, [dict(key='key', quote='送货慢', review_text='送货慢')])
        self.assertNotIn('送货慢', answer)
        self.assertEqual(redacted, ['key'])
        self.assertEqual(result, original)


if __name__ == '__main__':
    unittest.main()
