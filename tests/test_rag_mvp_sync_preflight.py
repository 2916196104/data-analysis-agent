"""Synthetic, connection-double tests only. No real PostgreSQL/SQLite/models."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import asdict, replace
from hashlib import sha256
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_budget', ROOT/'scripts/run_rag_mvp_sync_preflight.py')
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
from data_analysis_agent.reviews.rag_corpus import RagCorpus, RagOpinionDocument, RagReviewState


def synthetic_corpus(options, profile):
    docs = []
    for i in range(3591):
        rid = i % 1699+1
        docs.append(RagOpinionDocument(source=options['source'], dataset_version=options['dataset_version'],
            product_id='100013588053', review_id=rid, annotation_id=rid, requested_model=options['model'],
            base_url=options['base_url'], prompt_version=options['prompt_version'], label_profile_key=profile,
            opinion_source='model', model_opinion_id=i+1, human_opinion_id=None, human_revision_id=None,
            aspect='quality', sentiment='neutral', quote='PRIVATE_SYNTHETIC_QUOTE', quote_start=i, quote_end=i+1,
            review_content_sha256='a'*64, opinion_text_sha256='b'*64, embedding_input='PRIVATE_SYNTHETIC_QUOTE',
            embedding_input_sha256='b'*64, stable_versioned_opinion_key='rag-opinion-v1:'+f'{i:064x}',
            published_at='2022-01-01T00:00:00.000+00:00', import_run_id=1, line_number=rid,
            file_sha256='c'*64, evidence_ref='SYNTHETIC'))
    docs.sort(key=lambda d: (d.review_id, d.quote_start))
    counts = budget.Counter(d.review_id for d in docs)
    reviews = [RagReviewState(source=options['source'], dataset_version=options['dataset_version'],
        product_id='100013588053', review_id=rid, review_content_sha256='a'*64,
        published_at='2022-01-01T00:00:00.000+00:00', annotation_id=rid, human_revision_id=None,
        processing_status='success', human_status='unreviewed', opinion_source='model', error_code=None,
        opinion_count=counts[rid], effective_snapshot_sha256='d'*64) for rid in range(1,1700)]
    return RagCorpus(budget.analysis_scope(**options)[3], profile, tuple(docs), tuple(reviews))


class ConnectionDouble:
    def __init__(self, existing=(), invalidated=(), baseline_count=200, readonly='on', isolation='repeatable read', snapshot_drift=False):
        self.existing, self.invalidated = list(existing), list(invalidated)
        self.baseline_count, self.readonly, self.isolation = baseline_count, readonly, isolation
        self.snapshot_drift = snapshot_drift
        self.raw = SimpleNamespace(closed=False)
        self.commands, self.statement_count, self.snapshot_calls = [], 0, 0
        self.rollback_count = 0

    def execute(self, sql, parameters=None):
        self.commands.append((sql, parameters)); self.statement_count += 1
        if sql.startswith('SHOW transaction_read_only'): return SimpleNamespace(fetchone=lambda: (self.readonly,))
        if sql.startswith('SHOW transaction_isolation'): return SimpleNamespace(fetchone=lambda: (self.isolation,))
        if sql.startswith('SELECT txid_current_snapshot'):
            self.snapshot_calls += 1
            return SimpleNamespace(fetchone=lambda: ('changed' if self.snapshot_drift and self.snapshot_calls>1 else 'SYNTHETIC', '2026-01-01'))
        if 'AND NOT (stable_versioned_opinion_key=ANY' in sql:
            return SimpleNamespace(fetchall=lambda: self.invalidated)
        if 'stable_versioned_opinion_key=ANY' in sql:
            return SimpleNamespace(fetchall=lambda: self.existing)
        if 'to_jsonb(c)::text' in sql:
            return SimpleNamespace(fetchone=lambda: {'row_count':self.baseline_count, 'rowset_sha256':'e'*64})
        return SimpleNamespace()

    def rollback(self): self.rollback_count += 1
    def close(self): self.raw.closed = True


class BudgetPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='budget-offline-'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        g = ExitStack(); self.addCleanup(g.close)
        g.enter_context(patch.dict(os.environ, {}, clear=True))
        g.enter_context(patch.object(budget, 'ROOT', self.root))
        g.enter_context(patch.object(sys, 'executable', str(self.root/'.venv/Scripts/python.exe')))
        self.real_pg = g.enter_context(patch('psycopg.connect', side_effect=AssertionError('no real PG')))
        self.real_sqlite = g.enter_context(patch('sqlite3.connect', side_effect=AssertionError('no real SQLite')))
        g.enter_context(patch('socket.socket.connect', side_effect=AssertionError('no real socket')))
        self.git = g.enter_context(patch.object(budget.subprocess, 'run', return_value=SimpleNamespace(returncode=0)))
        self.schema = g.enter_context(patch.object(budget, 'check_review_schema'))
        self.dsn = 'postgresql://synthetic:PRIVATE_PASSWORD@127.0.0.1:5432/synthetic_db'
        self.identity = g.enter_context(patch.object(budget.safety, 'frozen_database_identity', return_value=budget.database_identity(self.dsn)))
        self.manifest = json.loads((ROOT/'tests/fixtures/rag_mvp_acceptance_v1.json').read_text(encoding='utf-8'))
        profile = [self.manifest['annotation_model'], 'https://private-label.invalid/v1', self.manifest['annotation_prompt_version']]
        self.manifest['annotation_profile_sha256'] = budget.content_sha256(profile)
        g.enter_context(patch.object(budget.safety, 'MANIFEST_SHA', budget.content_sha256(self.manifest)))
        for name in budget.OWNER_SHA:
            p=self.root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes((ROOT/name).read_bytes())
        # Only this synthetic copy accepts the revised owner; never rewrite the consumed real plan/script pins.
        g.enter_context(patch.object(budget, 'OWNER_SHA', {name: sha256((self.root/name).read_bytes()).hexdigest()
                                                        for name in budget.OWNER_SHA}))
        mf=self.root/'tests/fixtures/rag_mvp_acceptance_v1.json';mf.parent.mkdir(parents=True);mf.write_text(json.dumps(self.manifest),encoding='utf-8')
        (self.root/'.env').write_text('DATABASE_URL='+self.dsn+'\nLLM_BASE_URL='+profile[1]+'\n',encoding='utf-8')
        base=self.root/budget.safety.BASELINE;base.mkdir(parents=True)
        (base/'preflight.json').write_text(json.dumps({'manifest_sha256':budget.safety.MANIFEST_SHA,
            'protected_sha256':{'.env':sha256((self.root/'.env').read_bytes()).hexdigest()}}),encoding='utf-8')
        self.options = dict(self.manifest['scope']);self.options.pop('baseline_from');self.options.pop('baseline_to')
        self.options.update(product_ids=['100013588053'],as_of=None,model=profile[0],base_url=profile[1],prompt_version=profile[2])
        self.corpus = synthetic_corpus(self.options, self.manifest['annotation_profile_sha256'])
        self.addCleanup(logging.disable,logging.NOTSET)

    def existing_row(self):
        doc=self.corpus.documents[0]
        return dict(id=7,stable_versioned_opinion_key=doc.stable_versioned_opinion_key,
            **{k:getattr(doc,k) for k in budget.OWNER_FIELDS},effective_snapshot_sha256='d'*64,
            embedding_space_id=budget.SPACE_ID,invalidated_at=None,sync_run_id=1)

    def run_batch(self, connection, corpus=None):
        with patch.object(budget,'ReadonlyBudgetConnection',return_value=connection) as connect, \
             patch.object(budget,'read_effective_corpus',return_value=corpus or self.corpus) as reader:
            result=budget.execute_readonly_once(self.root,self.dsn,self.options,self.manifest)
        connect.assert_called_once_with(self.dsn)
        return result,reader

    def test_default_preflight_no_connection_and_claim(self):
        output=io.StringIO()
        with redirect_stdout(output): code=budget.main([])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['connection_attempts'],0)
        self.assertFalse((self.root/budget.OUTPUT).exists())
        self.real_pg.assert_not_called();self.real_sqlite.assert_not_called()

    def test_complete_budget_one_snapshot_private_keys_and_full_055_digest(self):
        conn=ConnectionDouble();result,reader=self.run_batch(conn)
        self.assertEqual([result[k] for k in ['S','N','U','I','P']],[200,200,0,0,200])
        self.assertTrue(result['n_plus_u_equals_s'] and result['can_enter_write_authorization'])
        self.assertFalse(result['write_authorized'])
        self.assertEqual(result['baseline_055_count'],200)
        self.assertTrue(result['rolled_back'] and result['connection_closed'])
        self.assertEqual(conn.rollback_count,1);self.assertEqual(conn.snapshot_calls,2)
        reader.assert_called_once_with(conn,**self.options)
        self.schema.assert_called_once_with(conn)
        private=json.loads((self.root/budget.OUTPUT/'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(len(private['selected_documents']),200)
        self.assertEqual(len(private['complete_valid_keys']),3591)
        encoded=json.dumps(private);public=json.dumps(result)
        for secret in ['PRIVATE_PASSWORD','PRIVATE_SYNTHETIC_QUOTE',self.options['base_url']]:
            self.assertNotIn(secret,encoded);self.assertNotIn(secret,public)
        self.assertNotIn(self.corpus.documents[0].stable_versioned_opinion_key,public)
        invalid_sql,params=next((s,p) for s,p in conn.commands if 'AND NOT (stable_versioned_opinion_key=ANY' in s)
        self.assertEqual(len(params[-1]),3591)
        self.assertNotIn('text_builder_version=?',invalid_sql)
        self.assertEqual(params[0],'100013588053')
        hash_sql,hash_params=next((s,p) for s,p in conn.commands if 'to_jsonb(c)::text' in s)
        self.assertIn("string_agg(row_sha256,'' ORDER BY id)",hash_sql)
        self.assertEqual(hash_params[0],'100013588055')
        self.assertNotIn('invalidated_at IS NULL',hash_sql)
        self.assertFalse(any(s.lstrip().startswith(('INSERT','UPDATE','DELETE','CREATE')) or '<=>' in s for s,_ in conn.commands))

    def test_source_count_or_identity_drift_stops_without_derived_queries(self):
        cases=[replace(self.corpus,reviews=self.corpus.reviews[:-1]), replace(self.corpus,label_profile_key='changed')]
        for i,corpus in enumerate(cases):
            with self.subTest(case=i),patch.object(budget,'OUTPUT',Path(f'outputs/drift-{i}')):
                conn=ConnectionDouble();result,_=self.run_batch(conn,corpus)
                self.assertEqual(result['status'],'error');self.assertIsNone(result['S'])
                self.assertFalse(any('review_opinion_embeddings' in s for s,_ in conn.commands))
                self.assertTrue(conn.raw.closed)

    def test_cross_product_key_conflict_stops_and_never_echoes_rows(self):
        row=self.existing_row();row['product_id']='100013588055'
        conn=ConnectionDouble(existing=[row]);result,_=self.run_batch(conn)
        self.assertTrue(result['cross_product_unique_key_conflict'])
        self.assertFalse(result['can_enter_write_authorization'])
        self.assertIsNone(result['I']);self.assertIsNone(result['baseline_055_count'])
        self.assertNotIn(row['stable_versioned_opinion_key'],json.dumps(result))
        self.assertFalse(any('AND NOT (' in s or 'to_jsonb(c)' in s for s,_ in conn.commands))

    def test_unexpected_u_i_and_055_count_block_without_adjusting_query(self):
        cases=[ConnectionDouble(existing=[self.existing_row()]),ConnectionDouble(invalidated=[self.existing_row()]),ConnectionDouble(baseline_count=199)]
        for i,conn in enumerate(cases):
            with self.subTest(case=i),patch.object(budget,'OUTPUT',Path(f'outputs/unexpected-{i}')):
                result,_=self.run_batch(conn)
                self.assertEqual(result['status'],'blocked_unexpected_budget')
                self.assertFalse(result['can_enter_write_authorization'])
                self.assertEqual(conn.snapshot_calls,2)

    def test_readonly_or_isolation_failure_and_snapshot_drift_fail_closed(self):
        cases=[ConnectionDouble(readonly='off'),ConnectionDouble(isolation='read committed'),ConnectionDouble(snapshot_drift=True)]
        for i,conn in enumerate(cases):
            with self.subTest(case=i),patch.object(budget,'OUTPUT',Path(f'outputs/txn-{i}')):
                result,reader=self.run_batch(conn)
                self.assertEqual(result['status'],'error');self.assertFalse(result['can_enter_write_authorization'])
                if i<2:reader.assert_not_called()
                self.assertEqual(conn.rollback_count,1);self.assertTrue(conn.raw.closed)

    def test_connect_failure_sanitized_and_claim_blocks_retry(self):
        with patch.object(budget,'ReadonlyBudgetConnection',side_effect=RuntimeError('PRIVATE_PASSWORD '+self.dsn)) as connect:
            result=budget.execute_readonly_once(self.root,self.dsn,self.options,self.manifest)
            second=budget.execute_readonly_once(self.root,self.dsn,self.options,self.manifest)
        connect.assert_called_once()
        self.assertEqual(result['connection_attempts'],1);self.assertEqual(second['connection_attempts'],0)
        self.assertNotIn('PRIVATE_PASSWORD',json.dumps(result))
        self.assertIsNone(result['S'])
        with self.assertRaisesRegex(budget.safety.CheckFailed,'already_claimed'):budget.preflight(self.root)

    def test_error_during_inventory_and_rollback_still_close(self):
        conn=ConnectionDouble();conn.rollback=Mock(side_effect=RuntimeError('PRIVATE_PASSWORD'))
        with patch.object(budget,'ReadonlyBudgetConnection',return_value=conn), \
             patch.object(budget,'read_effective_corpus',side_effect=RuntimeError('PRIVATE_SYNTHETIC_QUOTE')):
            result=budget.execute_readonly_once(self.root,self.dsn,self.options,self.manifest)
        self.assertTrue(conn.raw.closed);self.assertFalse(result['rolled_back'])
        self.assertFalse(result['can_enter_write_authorization'])
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_guard_rejects_dml_ddl_and_similarity_before_driver_io(self):
        connection=object.__new__(budget.ReadonlyBudgetConnection)
        for sql in ['INSERT INTO x VALUES(1)','UPDATE x SET y=1','DELETE FROM x','CREATE TABLE x(id int)','SELECT embedding <=> ? FROM x']:
            with self.subTest(sql=sql),self.assertRaises(budget.safety.CheckFailed):connection._execute(sql)
        self.real_pg.assert_not_called()

    def test_unconfirmed_close_cannot_authorize(self):
        connection = ConnectionDouble()
        connection.close = lambda: None
        result, _ = self.run_batch(connection)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['can_enter_write_authorization'])
        self.assertEqual(result['close_error']['code'], 'connection_close_not_confirmed')

    def test_preflight_rejects_config_owner_hash_target_and_git_privacy_drift(self):
        self.identity.return_value='postgresql://other.invalid:5432/other'
        with self.assertRaisesRegex(budget.safety.CheckFailed,'target_mismatch'):budget.preflight(self.root)
        self.identity.return_value=budget.database_identity(self.dsn)
        self.git.return_value.returncode=1
        with self.assertRaisesRegex(budget.safety.CheckFailed,'not_git_ignored'):budget.preflight(self.root)
        self.git.return_value.returncode=0
        with patch.dict(os.environ,{'PGSSLMODE':'require'}):
            with self.assertRaisesRegex(budget.safety.CheckFailed,'environment_not_frozen'):budget.preflight(self.root)
        name=next(iter(budget.OWNER_SHA));(self.root/name).write_text('changed',encoding='utf-8')
        with self.assertRaisesRegex(budget.safety.CheckFailed,'implementation_changed'):budget.preflight(self.root)
        self.real_pg.assert_not_called()


if __name__ == '__main__':
    unittest.main()
