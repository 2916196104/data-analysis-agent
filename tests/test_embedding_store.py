"""只在显式提供的隔离 PG 管理地址下创建/删除 rag_vector_test_* 数据库。"""

import json
import os
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

from test_review_workbench import WorkbenchFixture, IDENTITY, opinion
from data_analysis_agent.cli.migrate import migrate_sqlite
from data_analysis_agent.reviews.embedding_store import (
    SPACE_ID, EmbeddingStoreError, SyncRetryRequired, migrate_embedding_store,
    search_effective_opinions, sync_effective_opinions, validate_vector,
)
from data_analysis_agent.reviews.human_review import save_human_review
from data_analysis_agent.reviews.embedding_process import LocalEmbeddingProcess
from data_analysis_agent.reviews.label_store import save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
from data_analysis_agent.reviews.rag_corpus import read_effective_corpus
from data_analysis_agent.reviews.store import delete_import_runs

UNIT_VECTOR = [1.0] + [0.0] * 1023


class FakeEncoder:
    """仅验证存储协议；不作为真实模型或语义质量的证据。"""
    def __init__(self, callback=None, vectors=None, space=SPACE_ID):
        self.calls = []
        self.callback, self.vectors, self.space = callback, vectors, space

    def encode(self, texts):
        self.calls.append(texts)
        if self.callback:
            self.callback()
        return dict(space_id=self.space, vectors=self.vectors if self.vectors is not None
                    else [UNIT_VECTOR[:] for t in texts])


class VectorValidationTests(unittest.TestCase):
    def test_shape_finite_nonzero_normalization_and_space(self):
        self.assertEqual(validate_vector(UNIT_VECTOR), UNIT_VECTOR)
        for vector in ([], [0.0] * 1024, [float('nan')] * 1024, [float('inf')] * 1024,
                       [True] * 1024, [1.0] * 1024, [1e308] * 1024):
            with self.subTest(first=repr(vector[:1])), self.assertRaises(EmbeddingStoreError):
                validate_vector(vector)

    def test_worker_receives_only_local_inputs_and_environment_without_credentials(self):
        with patch.dict(os.environ, {'DATABASE_URL':'private-dsn','LLM_API_KEY':'private-key','HF_TOKEN':'private-token'}):
            with patch('data_analysis_agent.reviews.embedding_process.subprocess.run',
                       return_value=SimpleNamespace(returncode=0,stdout=json.dumps(dict(space_id=SPACE_ID,vectors=[])))) as run:
                LocalEmbeddingProcess('isolated-python','local-cache').encode(['合成观点'])
        args, kwargs = run.call_args
        self.assertFalse({'DATABASE_URL','LLM_API_KEY','HF_TOKEN'} & set(kwargs['env']))
        self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
        self.assertEqual(json.loads(kwargs['input'])['texts'], ['合成观点'])
        self.assertNotIn('合成观点', repr(args))

    def test_worker_errors_do_not_echo_private_stderr_or_inputs(self):
        with patch('data_analysis_agent.reviews.embedding_process.subprocess.run',
                   return_value=SimpleNamespace(returncode=1,stdout='',stderr='PRIVATE synthetic quote')):
            with self.assertRaises(EmbeddingStoreError) as caught:
                LocalEmbeddingProcess('isolated-python','local-cache').encode(['PRIVATE synthetic quote'])
        self.assertNotIn('PRIVATE', str(caught.exception))


@unittest.skipUnless(os.environ.get('TEST_PGVECTOR_ADMIN_URL'), 'set isolated TEST_PGVECTOR_ADMIN_URL')
class EmbeddingStoreTests(WorkbenchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sqlite_db = self.db
        self.admin_url = os.environ['TEST_PGVECTOR_ADMIN_URL']
        self.name = 'rag_vector_test_' + uuid.uuid4().hex
        with psycopg.connect(self.admin_url, autocommit=True) as c:
            c.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(self.name)))
        self.addCleanup(self.drop_test_database)
        self.db = urlunsplit(urlsplit(self.admin_url)._replace(path='/' + self.name))
        migrate_sqlite(self.sqlite_db, self.db)
        with psycopg.connect(self.db, autocommit=True) as c:
            c.execute('CREATE EXTENSION vector')
        migrate_embedding_store(self.db)

    def drop_test_database(self):
        assert self.name.startswith('rag_vector_test_') and len(self.name) == 48
        with psycopg.connect(self.admin_url, autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.name)))

    def options(self, **changes):
        return dict(**IDENTITY, source='amazon_reviews_2023', dataset_version='amazon-reviews-2023',
                    product_ids=['P1'], date_from='2023-01-01', date_to='2023-01-06') | changes

    def corpus(self, **changes):
        return read_effective_corpus(self.db, **self.options(**changes))

    def sync(self, encoder=None, **kwargs):
        return sync_effective_opinions(self.db, options=self.options(), encoder=encoder or FakeEncoder(), **kwargs)

    def query(self, **changes):
        options = changes.pop('options', self.options())
        return search_effective_opinions(self.db, UNIT_VECTOR, options=options,
                                        embedding_space_id=SPACE_ID, **changes)

    def sql(self, query, parameters=None):
        with psycopg.connect(self.db, autocommit=True) as c:
            result = c.execute(query, parameters)
            return result.fetchall() if result.description else []

    def test_actual_schema_constraints_indexes_rollback_and_reapply(self):
        self.sync()
        with self.assertRaises(EmbeddingStoreError):
            migrate_embedding_store(self.db)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(3,)])
        self.assertEqual(self.sql("SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
            "WHERE attrelid='review_opinion_embeddings'::regclass AND attname='embedding'"), [('vector(1024)',)])
        indexes = self.sql("SELECT indexdef FROM pg_indexes WHERE tablename='review_opinion_embeddings'")
        self.assertTrue(all('hnsw' not in i[0] and 'ivfflat' not in i[0] for i in indexes))
        for statement in ("UPDATE review_opinion_embeddings SET human_opinion_id=1",
                          "UPDATE review_opinion_embeddings SET embedding_space_id='wrong'",
                          "UPDATE review_opinion_embeddings SET embedding='[1,2]'::vector",
                          "UPDATE review_opinion_embeddings SET embedding=array_fill(0.0::real,ARRAY[1024])::vector",
                          "UPDATE review_opinion_embeddings SET review_id=999999"):
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                self.sql(statement)
        migrate_embedding_store(self.db, rollback=True)
        self.assertEqual(self.sql("SELECT count(*) FROM reviews"), [(6,)])
        self.assertEqual(self.sql("SELECT extversion FROM pg_extension WHERE extname='vector'"), [('0.8.7',)])
        self.sql('DROP EXTENSION vector')
        with self.assertRaises(EmbeddingStoreError):
            migrate_embedding_store(self.db)
        self.assertEqual(self.sql("SELECT to_regclass('public.rag_embedding_sync_runs')"), [(None,)])
        self.sql('CREATE EXTENSION vector')
        migrate_embedding_store(self.db)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])

    def test_idempotence_multiple_opinions_provenance_and_original_evidence(self):
        encoder = FakeEncoder()
        first = self.sync(encoder)
        self.assertEqual((first['status'], first['metrics']['published_count']), ('succeeded', 3))
        second = self.sync(encoder)
        self.assertEqual(second['metrics']['attempted_count'], 0)
        self.assertEqual(second['metrics']['reused_count'], 3)
        self.assertEqual(len(encoder.calls), 1)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(3,)])
        hits = self.query()
        self.assertEqual(len(hits), 3)
        self.assertEqual({h.document.aspect for h in hits}, {'effectiveness','packaging','service'})
        original = self.sql('SELECT text FROM reviews WHERE id=1')[0][0]
        for h in hits:
            d = h.document
            self.assertEqual(original[d.quote_start:d.quote_end], d.quote)
            self.assertIsNotNone(d.model_opinion_id)
            self.assertTrue(d.evidence_ref.startswith('sha256:'))
        self.assertEqual(len(self.query(aspect='service', sentiment='negative')), 1)
        self.assertEqual(self.query(aspect='service', sentiment='positive'), ())

    def test_product_dataset_dates_and_label_profile_isolation(self):
        self.sync()
        self.assertEqual(self.query(options=self.options(product_ids=['P2'])), ())
        self.assertEqual(self.query(options=self.options(model='other-model')), ())
        self.assertEqual(self.query(options=self.options(base_url='https://other.invalid')), ())
        self.assertEqual(self.query(options=self.options(prompt_version='other-prompt')), ())
        self.assertEqual(self.query(options=self.options(date_from='2023-01-02')), ())
        # 同一商品 ID 在另一数据集中合法存在，也不能带出原数据集向量。
        self.sql("INSERT INTO products(source,dataset_version,product_id,title,raw_json) "
                 "SELECT source,'other-dataset',product_id,title,raw_json FROM products WHERE product_id='P1'")
        self.assertEqual(self.query(options=self.options(dataset_version='other-dataset')), ())

    def test_empty_human_snapshot_hides_stale_even_before_invalidation(self):
        self.sync()
        save_human_review(self.db, 1, annotation_id=self.annotations[1], expected_revision_id=None,
                          action='corrected', reviewer='synthetic', opinions=[])
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings WHERE invalidated_at IS NULL'), [(3,)])
        self.assertEqual(self.query(), ())
        result = self.sync()
        self.assertEqual(result['metrics']['invalidated_count'], 3)
        self.assertEqual(result['metrics']['scope_opinion_count'], 0)
        self.assertEqual(result['status'], 'succeeded')

    def test_human_confirm_correct_text_and_change_labels_create_new_source_keys(self):
        old = self.corpus().documents
        self.sync()
        confirmed = save_human_review(self.db, 1, annotation_id=self.annotations[1], expected_revision_id=None,
                                      action='confirmed', reviewer='synthetic')
        self.assertEqual(self.query(), ())
        self.sync()
        self.assertTrue(all(h.document.opinion_source == 'human' for h in self.query()))
        updated = save_human_review(self.db, 1, annotation_id=self.annotations[1],
            expected_revision_id=confirmed['revision_id'], action='corrected', reviewer='synthetic',
            opinions=[opinion('quality','neutral','包装破了')])
        self.sync()
        hit = self.query()[0].document
        self.assertEqual((hit.aspect,hit.sentiment,hit.human_revision_id), ('quality','neutral',updated['revision_id']))
        self.assertNotIn(hit.stable_versioned_opinion_key, {d.stable_versioned_opinion_key for d in old})
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinions WHERE annotation_id=%s', (self.annotations[1],)), [(3,)])

    def test_content_hash_change_rerun_and_success_beats_later_failure(self):
        self.sync()
        self.sql("UPDATE reviews SET content_sha256=repeat('a',64) WHERE id=1")
        self.assertEqual(self.query(), ())
        review = dict(id=1, text='好用，包装破了，客服回复慢。', content_sha256='a'*64)
        aid = save_annotation(self.db, review, **IDENTITY, status='success',
                              opinions=validate_opinions(json.dumps({'opinions':[opinion()]}), review['text']))
        self.sql("INSERT INTO review_annotations(review_id,content_sha256,requested_model,base_url,prompt_version,status,elapsed_ms,created_at) "
                 "VALUES(1,%s,%s,%s,%s,'failed',0,'2023-03-01')", ('a'*64, *IDENTITY.values()))
        self.sync()
        self.assertEqual([h.document.annotation_id for h in self.query()], [aid])
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings WHERE invalidated_at IS NOT NULL'), [(3,)])

    def test_encoding_has_no_open_transaction_and_concurrent_revision_requires_retry(self):
        def change():
            active = self.sql("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                              "AND pid<>pg_backend_pid() AND xact_start IS NOT NULL")[0][0]
            self.assertEqual(active, 0)
            save_human_review(self.db, 1, annotation_id=self.annotations[1], expected_revision_id=None,
                              action='corrected', reviewer='synthetic', opinions=[])
        with self.assertRaises(SyncRetryRequired):
            self.sync(FakeEncoder(change))
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])
        self.assertEqual(self.sql('SELECT status FROM rag_embedding_sync_runs'), [('retry_required',)])

    def test_changes_to_complete_opinion_set_without_version_change_are_detected(self):
        self.sync()
        self.sql("UPDATE review_opinions SET sentiment='neutral' WHERE id=%s", (self.corpus().documents[0].model_opinion_id,))
        # 完整集合摘要已变化，另外两条未改变的旧向量也暂时不作为当前完整证据。
        self.assertEqual(self.query(), ())
        result = self.sync()
        self.assertEqual(result['metrics']['reused_count'], 2)
        self.assertEqual(len(self.query()), 3)

    def test_model_failure_or_bad_vector_never_publishes_partial_data(self):
        for encoder in (FakeEncoder(vectors=[UNIT_VECTOR, [float('nan')]*1024, UNIT_VECTOR]),
                        FakeEncoder(space='other-space'), FakeEncoder(vectors=[])):
            with self.assertRaises(EmbeddingStoreError):
                self.sync(encoder)
            self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])
        self.assertEqual(self.sql("SELECT count(*) FROM rag_embedding_sync_runs WHERE status='failed'"), [(3,)])
        self.assertEqual(self.sync()['status'], 'succeeded')

    def test_overlength_explicit_partial_and_complete_review_cap(self):
        result = self.sync(FakeEncoder(vectors=[UNIT_VECTOR,None,UNIT_VECTOR]))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual((result['metrics']['overlength_count'], result['metrics']['not_indexed_count']), (1,1))
        self.assertEqual(len(self.query()), 2)
        result = self.sync(limit=2)
        self.assertEqual(result['metrics']['selected_opinion_count'], 0)
        self.assertEqual(result['status'], 'partial')

    def test_source_deletion_cascades_only_derived_rows_and_fails_closed(self):
        self.sync()
        self.sql('DELETE FROM review_opinions WHERE annotation_id=%s', (self.annotations[1],))
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])
        self.assertEqual(self.query(), ())
        self.assertEqual(self.sql('SELECT count(*) FROM reviews'), [(6,)])

    def test_existing_dataset_deletion_removes_embeddings_without_code_changes(self):
        self.sync()
        run_id = self.sql('SELECT import_run_id FROM reviews WHERE id=1')[0][0]
        result = delete_import_runs(self.db, [run_id])
        self.assertEqual(result['deleted_reviews'], 6)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])
        self.assertEqual(self.sql('SELECT count(*) FROM rag_embedding_sync_runs'), [(1,)])

    def test_simultaneous_syncs_are_serialized_and_do_not_duplicate(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def run():
            try:
                results.append(self.sync(FakeEncoder(lambda: barrier.wait(8))))
            except Exception as exc:
                errors.append(type(exc).__name__)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(3,)])

    def test_publishing_blocks_direct_revision_writer_until_commit(self):
        entered, release, writer_done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = read_effective_corpus
        def gated(db, **options):
            if not isinstance(db, str):
                entered.set()
                if not release.wait(8):
                    raise RuntimeError('test_timeout')
            return original(db, **options)
        def sync_thread():
            try:
                self.sync()
            except Exception as exc:
                errors.append(type(exc).__name__)
        def writer():
            try:
                with psycopg.connect(self.db) as c:
                    c.execute('SELECT id FROM reviews WHERE id=1 FOR UPDATE')
                    c.execute("INSERT INTO review_human_reviews(annotation_id,previous_id,action,reviewer,note,created_at) "
                              "VALUES(%s,NULL,'corrected','synthetic','','2023-03-01')", (self.annotations[1],))
                writer_done.set()
            except Exception as exc:
                errors.append(type(exc).__name__)
        with patch('data_analysis_agent.reviews.embedding_store.read_effective_corpus', side_effect=gated):
            t = threading.Thread(target=sync_thread)
            t.start()
            self.assertTrue(entered.wait(8))
            w = threading.Thread(target=writer)
            w.start()
            try:
                self.assertFalse(writer_done.wait(0.3))
            finally:
                release.set()
                t.join(10)
                w.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(3,)])
        self.assertEqual(self.query(), ())

    def test_busy_source_or_derived_table_leaves_retry_record_and_no_vectors(self):
        for table in ('reviews', 'review_opinion_embeddings'):
            with psycopg.connect(self.db, autocommit=True) as blocker:
                blocker.execute('BEGIN')
                blocker.execute('LOCK TABLE ' + table + ' IN ROW EXCLUSIVE MODE')
                with self.assertRaises(SyncRetryRequired):
                    self.sync()
                blocker.rollback()
        self.assertEqual(self.sql('SELECT status FROM rag_embedding_sync_runs'), [('retry_required',)] * 2)
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])

    def test_existing_source_row_lock_causes_retry_without_aborting_source_writer(self):
        with psycopg.connect(self.db) as writer:
            writer.execute('SELECT id FROM reviews WHERE id=1 FOR UPDATE')
            with self.assertRaises(SyncRetryRequired):
                self.sync()
            self.assertEqual(writer.execute('SELECT 1').fetchone(), (1,))
        self.assertEqual(self.sql('SELECT status FROM rag_embedding_sync_runs'), [('retry_required',)])
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])

    def test_dataset_removed_during_encoding_leaves_retry_without_publishing(self):
        runs = [r[0] for r in self.sql('SELECT id FROM import_runs')]
        with self.assertRaises(SyncRetryRequired):
            self.sync(FakeEncoder(lambda: delete_import_runs(self.db, runs)))
        self.assertEqual(self.sql('SELECT status FROM rag_embedding_sync_runs'), [('retry_required',)])
        self.assertEqual(self.sql('SELECT count(*) FROM review_opinion_embeddings'), [(0,)])


if __name__ == '__main__':
    unittest.main()
