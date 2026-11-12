"""Synthetic-only failure injection. No PostgreSQL or embedding process is started."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from dataclasses import make_dataclass, asdict
from unittest import TestCase, mock
import json

from scripts import execute_reviews_csv_386660_insert_only as executor


class Cursor:
    def __init__(self, value): self.value = value
    def fetchone(self): return self.value


class FakeConnection:
    def __init__(self, fail_at=None, commit_error=False):
        self.raw = SimpleNamespace(closed=False)
        self.sql, self.staged, self.committed = [], [], []
        self.fail_at, self.commit_error, self.rollbacks = fail_at, commit_error, 0
    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if sql.startswith('INSERT INTO'):
            if self.fail_at is not None and len(self.staged) == self.fail_at:
                raise RuntimeError('SYNTHETIC_CONFLICT')
            self.staged.append(params)
            return Cursor((len(self.staged),))
        return Cursor((1,))
    def commit(self):
        if self.commit_error: raise RuntimeError('SYNTHETIC_COMMIT_UNKNOWN')
        self.committed.extend(self.staged)
        self.staged.clear()
    def rollback(self):
        self.rollbacks += 1
        self.staged.clear()
    def close(self): self.raw.closed = True


class FakeEncoder:
    def __init__(self):
        self.python = str((executor.ROOT/'.cache/embedding-eval/venv/Scripts/python.exe').resolve())
        self.cache_dir = str((executor.ROOT/'.cache/embedding-eval/hf/hub').resolve())
        self.calls = []
    def encode(self, texts):
        self.calls.append(texts)
        return {'space_id':executor.SPACE_ID, 'vectors':[[1.0] + [0.0]*1023 for _ in texts]}


class InsertOnlyTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = executor.read_and_validate_plan()

    def validate_mutation(self, mutate):
        plan = deepcopy(self.plan)
        mutate(plan)
        plan['plan_sha256'] = executor.content_sha256({k:v for k,v in plan.items() if k != 'plan_sha256'})
        with mock.patch.object(executor, 'strict_json_loads', return_value=plan), \
             mock.patch.object(executor, 'EXPECTED_PLAN', plan['plan_sha256']):
            with self.assertRaises(executor.ExecutionError): executor.read_and_validate_plan()

    def test_real_frozen_plan_and_default_execution_gate(self):
        self.assertEqual(len(self.plan['selected_documents']), 50)
        with self.assertRaisesRegex(executor.ExecutionError,'ISOLATED_POSTGRESQL_TESTS_REQUIRED'):
            executor.execute_frozen_batch(self.plan, (), None, None, None)

    def test_tampered_digest_rejected(self):
        plan = deepcopy(self.plan); plan['selected_count'] = 49
        with mock.patch.object(executor,'strict_json_loads',return_value=plan):
            with self.assertRaisesRegex(executor.ExecutionError,'PLAN_DIGEST_MISMATCH'): executor.read_and_validate_plan()

    def test_duplicate_keys_and_over_limit_rejected(self):
        self.validate_mutation(lambda p: p['selected_documents'].__setitem__(1,deepcopy(p['selected_documents'][0])))
        def over(p): p['selected_documents'].append(deepcopy(p['selected_documents'][0])); p['selected_count']=51
        self.validate_mutation(over)

    def test_scope_profile_and_space_tampering_rejected(self):
        for field, value in (('source','amazon_reviews_2023'),('dataset_version','other'),('product_ids',['100013588053'])):
            with self.subTest(field=field): self.validate_mutation(lambda p: p['scope'].__setitem__(field,value))
        for field in ('profile_key','embedding_space_id','text_builder_version'):
            with self.subTest(field=field): self.validate_mutation(lambda p: p.__setitem__(field,'bad'))
        self.validate_mutation(lambda p: p['selected_documents'][0].__setitem__('product_id','100013588055'))

    def test_group_and_source_digests_cannot_be_changed(self):
        self.validate_mutation(lambda p: p['selected_review_snapshots'].clear())
        self.validate_mutation(lambda p: p['source_sha256'].clear())
        original = Path.read_bytes
        def tampered(path):
            return b'changed' if path.name == 'embedding_store.py' else original(path)
        with mock.patch.object(Path,'read_bytes',tampered):
            with self.assertRaisesRegex(executor.ExecutionError,'PLAN_SOURCE_CHANGED'):
                executor.read_and_validate_plan()

    def test_writer_identity_and_privileges_fail_closed(self):
        class IdentityConnection:
            def __init__(self,row): self.row=row
            def execute(self,*args): return Cursor(self.row)
        safe=(False,False,False,False,False,False,False,False,False,True,False,False,False)
        executor.check_writer_permissions(IdentityConnection(safe))
        for index in (0,5,6,7,8,10,11,12):
            flags=list(safe); flags[index]=True
            with self.subTest(index=index), self.assertRaises(executor.ExecutionError):
                executor.check_writer_permissions(IdentityConnection(tuple(flags)))
        missing=list(safe); missing[9]=False
        with self.assertRaises(executor.ExecutionError): executor.check_writer_permissions(IdentityConnection(tuple(missing)))
        with self.assertRaises(executor.ExecutionError):
            executor.check_identity(IdentityConnection(('reviews','rag_audit_ro','rag_audit_ro','off','read committed','UTC')),False)

    def test_source_permissions_or_rls_fail_closed(self):
        class SourceConnection:
            def __init__(self,rows): self.rows=rows
            def execute(self,*args): return SimpleNamespace(fetchall=lambda:self.rows)
        rows=[(name,'r',False,False,True) for name in executor.READ_TABLES]
        executor.check_source_permissions(SourceConnection(rows))
        for replacement in ((rows[0][0],'r',True,False,True),(rows[0][0],'v',False,False,True),
                            (rows[0][0],'r',False,False,False)):
            with self.subTest(replacement=replacement), self.assertRaises(executor.ExecutionError):
                executor.check_source_permissions(SourceConnection([replacement,*rows[1:]]))

    def run_batch(self, *, fail_at=None, changed_at=None, commit_error=False):
        reader, writer, encoder = FakeConnection(), FakeConnection(fail_at,commit_error), FakeEncoder()
        docs = [SimpleNamespace(**d,embedding_input='SYNTHETIC') for d in self.plan['selected_documents']]
        calls = []
        def check(*args):
            calls.append(1)
            if changed_at == len(calls): raise executor.ExecutionError('PLAN_CORPUS_CHANGED')
            return docs
        with TemporaryDirectory() as folder, \
             mock.patch.object(executor,'RECEIPT_DIR',Path(folder)), \
             mock.patch.object(executor,'ISOLATED_TEST_GATE',True), \
             mock.patch.object(executor,'LocalEmbeddingProcess',FakeEncoder), \
             mock.patch.object(executor,'GuardedWriter',FakeConnection), \
             mock.patch.object(executor,'read_and_validate_plan',return_value=self.plan), \
             mock.patch.object(executor,'check_identity'), mock.patch.object(executor,'check_writer_permissions'), \
             mock.patch.object(executor,'check_review_schema'), mock.patch.object(executor,'check_source_permissions'), \
             mock.patch.object(executor,'verify_current',side_effect=check):
            result = executor.execute_frozen_batch(self.plan,('synthetic',)*3,lambda:reader,lambda:writer,encoder)
            receipts = list(Path(folder).glob('*.json'))
            self.assertEqual(len(receipts),1)
            serialized=receipts[0].read_text(encoding='utf-8')
            self.assertNotIn('SYNTHETIC',serialized)
        return result, reader, writer, encoder

    def test_simulated_success_only_inserts_frozen_product(self):
        result, reader, writer, encoder = self.run_batch()
        self.assertEqual((result['status'], result['inserted'],result['commit']),('COMPLETED',50,'CONFIRMED'))
        self.assertEqual(len(encoder.calls),1)
        self.assertEqual(len(encoder.calls[0]),50)
        self.assertEqual(len(writer.committed),50)
        self.assertTrue(reader.raw.closed and writer.raw.closed)
        for sql,params in writer.sql:
            self.assertNotIn('ON CONFLICT',sql.upper())
            self.assertFalse(sql.lstrip().upper().startswith(('UPDATE','DELETE')))
            if sql.startswith('INSERT'):
                self.assertEqual(params[:3],('reviews_csv','reviews-csv','100019386660'))
                self.assertEqual(params[5],executor.PROFILE)

    def test_source_change_before_encoding_blocks_calls(self):
        result, reader, writer, encoder = self.run_batch(changed_at=1)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(encoder.calls,[])
        self.assertEqual(writer.sql,[])
        self.assertTrue(reader.raw.closed)

    def test_source_change_after_encoding_rolls_back_before_insert(self):
        result, _, writer, encoder = self.run_batch(changed_at=2)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['insert_attempts'],0)
        self.assertTrue(result['rollback'] and writer.raw.closed)
        self.assertEqual(writer.committed,[])
        self.assertEqual(len(encoder.calls),1)

    def test_unique_conflict_rolls_back_entire_batch(self):
        result, _, writer, _ = self.run_batch(fail_at=7)
        self.assertEqual(result['status'],'FAILED')
        self.assertTrue(result['rollback'])
        self.assertEqual(writer.staged,[])
        self.assertEqual(writer.committed,[])
        self.assertTrue(writer.raw.closed)

    def test_uncertain_commit_not_called_rollback_success(self):
        result, _, _, _ = self.run_batch(commit_error=True)
        self.assertEqual(result['commit'],'UNKNOWN')
        self.assertEqual(result['inserted'],0)
        self.assertEqual(result['status'],'FAILED')

    def verify_synthetic(self, *, already_exists=False, changed=False, bad_source=False, partial_group=False):
        row = deepcopy(self.plan['selected_documents'][0])
        Doc = make_dataclass('SyntheticDocument', [(name, object) for name in (*row.keys(),'base_url','quote','embedding_input')])
        doc = Doc(**row, base_url='SYNTHETIC', quote='SYNTHETIC', embedding_input='SYNTHETIC')
        docs = [doc]
        if partial_group:
            other = deepcopy(doc)
            other.stable_versioned_opinion_key = 'rag-opinion-v1:' + 'f'*64
            docs.append(other)
        review_id=doc.review_id
        State = make_dataclass('SyntheticState',[('review_id',int),('effective_snapshot_sha256',str)])
        snapshot = self.plan['selected_review_snapshots'][str(review_id)]
        Corpus = make_dataclass('SyntheticCorpus',[('documents',list),('reviews',list),('label_profile_key',str)])
        corpus = Corpus(docs,[State(review_id,snapshot)],executor.PROFILE)
        plan = {'scope':deepcopy(self.plan['scope']), 'corpus_sha256':executor.content_sha256(asdict(corpus)),
                'selected_documents':[row], 'selected_review_snapshots':{str(review_id):snapshot},
                'historical_stored_keys_sha256':executor.content_sha256([])}
        if changed: plan['corpus_sha256']='0'*64
        class ReadCursor:
            def __init__(self, val): self.val=val
            def fetchone(self): return self.val
            def fetchall(self): return self.val
        class ReadConnection:
            def execute(self,sql,parameters=None):
                if 'count(*)' in sql:
                    return ReadCursor((1,plan['scope']['date_from'],plan['scope']['date_to']))
                return ReadCursor([(doc.stable_versioned_opinion_key,)] if already_exists else [])
        context = {k:getattr(doc,k) for k in ('source','dataset_version','product_id','review_id',
                   'published_at','file_sha256','line_number','import_run_id')}
        context.update(content_sha256=doc.review_content_sha256,text='SYNTHETIC')
        with mock.patch.object(executor,'options_for',return_value=plan['scope']), \
             mock.patch.object(executor,'read_effective_corpus',return_value=corpus), \
             mock.patch.object(executor,'_scope',return_value='synthetic'), \
             mock.patch.object(executor,'load_review_contexts',return_value=[context]), \
             mock.patch.object(executor,'_verified_sources',side_effect=(executor.ExecutionError('SOURCE_AUTHENTICATION_FAILED')
                 if bad_source else lambda *args: {doc.stable_versioned_opinion_key:True})):
            return executor.verify_current(ReadConnection(),plan,('synthetic',)*3)

    def test_live_revalidation_detects_snapshot_drift_existing_vector_and_source_failure(self):
        self.assertEqual(len(self.verify_synthetic()),1)
        for options, code in (({'already_exists':True},'PLAN_VECTOR_ALREADY_EXISTS'),
                              ({'changed':True},'PLAN_CORPUS_CHANGED'),
                              ({'bad_source':True},'SOURCE_AUTHENTICATION_FAILED'),
                              ({'partial_group':True},'PLAN_REVIEW_GROUP_CHANGED')):
            with self.subTest(options=options), self.assertRaisesRegex(executor.ExecutionError,code):
                self.verify_synthetic(**options)

    def test_writer_sql_guard_blocks_cross_product_and_mutation(self):
        for sql in ('UPDATE review_opinion_embeddings SET invalidated_at=now()',
                    'DELETE FROM review_opinion_embeddings',
                    'WITH x AS (DELETE FROM reviews RETURNING id) SELECT * FROM x',
                    'SELECT pg_notify(\'x\',\'y\')',
                    'INSERT INTO review_opinion_embeddings DEFAULT VALUES'):
            with self.subTest(sql=sql), self.assertRaises(executor.ExecutionError):
                executor.check_writer_sql(sql)
        row = self.plan['selected_documents'][0]
        params = [row[name] for name in executor.FIELDS] + ['synthetic-snapshot',executor.SPACE_ID,'[1]']
        executor.check_writer_sql(executor.INSERT_SQL,params)
        executor.check_writer_sql(executor._parameters(executor.INSERT_SQL),params)
        for index, changed in ((2,'100013588053'),(2,'100013588055'),(0,'amazon_reviews_2023'),
                               (1,'amazon-reviews-2023'),(5,'wrong-profile'),
                               (len(executor.FIELDS)+1,'wrong-space')):
            altered=list(params); altered[index]=changed
            with self.subTest(index=index), self.assertRaises(executor.ExecutionError):
                executor.check_writer_sql(executor.INSERT_SQL,altered)
