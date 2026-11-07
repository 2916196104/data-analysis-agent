"""Synthetic publication transactions only; real DB/network/model calls are blocked."""
from collections import Counter
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews import embedding_budget as guard, embedding_store as store
from data_analysis_agent.reviews.scope import analysis_scope, review_scope
from test_rag_mvp_sync_preflight import synthetic_corpus

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('controlled_sync_cli', ROOT/'scripts/sync_opinion_embeddings.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
UNIT = [1.0]+[0.0]*1023


class FakeConnection:
    def __init__(self, *, existing=(), invalidated=(), baseline='e'*64, fail_insert=None,
                 conflict=False, final_baseline=None, commit_failure=False):
        self.existing, self.invalidated = list(existing), list(invalidated)
        self.baseline, self.final_baseline = baseline, final_baseline
        self.fail_insert, self.conflict, self.commit_failure = fail_insert, conflict, commit_failure
        self.commands, self.staged, self.committed = [], [], []
        self.rollback_count = self.commit_count = self.baseline_calls = 0
        self.closed = False

    def execute(self, sql, parameters=()):
        self.commands.append((sql, parameters))
        if 'AND NOT (stable_versioned_opinion_key=ANY' in sql:
            return SimpleNamespace(fetchall=lambda: self.invalidated)
        if 'stable_versioned_opinion_key=ANY' in sql:
            return SimpleNamespace(fetchall=lambda: self.existing)
        if 'to_jsonb(c)::text' in sql:
            self.baseline_calls += 1
            digest = self.final_baseline if self.baseline_calls > 1 and self.final_baseline else self.baseline
            return SimpleNamespace(fetchone=lambda: dict(row_count=200, rowset_sha256=digest))
        if sql.startswith('INSERT INTO review_opinion_embeddings'):
            if self.fail_insert is not None and len(self.staged) == self.fail_insert:
                raise store.DatabaseError('PRIVATE backend failure')
            if self.conflict:
                return SimpleNamespace(fetchone=lambda: None)
            assert parameters[2] == '100013588053'
            self.staged.append(parameters)
            return SimpleNamespace(fetchone=lambda: (1000+len(self.staged),))
        if sql.startswith('SELECT COUNT(*)'):
            return SimpleNamespace(fetchone=lambda: (len(self.staged),))
        if sql.startswith('UPDATE review_opinion_embeddings'):
            raise AssertionError('controlled batch must never UPDATE any vector')
        return SimpleNamespace()

    def rollback(self):
        self.rollback_count += 1
        self.staged.clear()

    def close(self): self.closed = True
    def __enter__(self): return self
    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.rollback()
        elif self.commit_failure:
            self.rollback()
            raise store.DatabaseError('PRIVATE commit failure')
        else:
            self.commit_count += 1
            self.committed.extend(self.staged)
            self.staged.clear()


class SyncBudgetTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.pg = stack.enter_context(patch('psycopg.connect', side_effect=AssertionError('no real PG')))
        self.sqlite = stack.enter_context(patch('sqlite3.connect', side_effect=AssertionError('no real SQLite')))
        stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('no network')))
        stack.enter_context(patch('data_analysis_agent.reviews.embedding_process.subprocess.run', side_effect=AssertionError('no real model')))
        self.tmp = tempfile.TemporaryDirectory(prefix='sync-offline-'); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = 'postgresql://synthetic:PRIVATE@127.0.0.1:5432/synthetic'
        self.options = dict(source='reviews_csv', dataset_version='reviews-csv', product_ids=['100013588053'],
            date_from='2021-09-11', date_to='2022-08-26', as_of=None,
            model='SYNTHETIC_MODEL', base_url='https://synthetic.invalid', prompt_version='SYNTHETIC_PROMPT')
        profile = content_sha256([self.options[k] for k in ('model','base_url','prompt_version')])
        stack.enter_context(patch.object(guard, 'FROZEN_PROFILE', profile))
        stack.enter_context(patch.object(guard, 'FROZEN_055_SHA', 'e'*64))
        template = synthetic_corpus(self.options, profile)
        counts = [2]*86+[1]*28+[3]*221+[2]*(1699-335)
        docs = []; index = 0
        for rid, n in enumerate(counts, 1):
            for j in range(n):
                docs.append(replace(template.documents[0], review_id=rid, annotation_id=rid,
                    model_opinion_id=index+1, stable_versioned_opinion_key='synthetic:'+str(index),
                    quote_start=0, quote_end=4, quote='合成观点', embedding_input='合成观点', line_number=rid))
                index += 1
        reviews = [replace(r, opinion_count=counts[r.review_id-1]) for r in template.reviews]
        self.corpus = replace(template, documents=tuple(docs), reviews=tuple(reviews))
        self.selected = store._select(self.corpus.documents, 200)
        count = Counter(d.review_id for d in self.selected)
        scope55 = {k:self.options[k] for k in store.RANGE_FIELDS}; scope55['product_ids']=['100013588055']
        self.plan = dict(budget=dict(guard.EXPECTED), result_status='budget_ready_pending_separate_authorization',
            owner_implementation_sha256={'synthetic-owner.py':sha256(b'unchanged-owner').hexdigest()},
            manifest_sha256=content_sha256({'synthetic':True}), profile_sha256=profile, space=guard.SPACE, builder=guard.BUILDER,
            scope={k:v for k,v in self.corpus.scope.items() if k!='base_url'},
            source_corpus_sha256=content_sha256(asdict(self.corpus)),
            selected_documents=guard.selected_metadata(self.selected),
            selected_review_groups=[dict(review_id=rid, opinion_count=n, effective_snapshot_sha256='d'*64) for rid,n in count.items()],
            complete_valid_keys=[d.stable_versioned_opinion_key for d in docs],
            complete_review_states=[asdict(r) for r in reviews], existing_selected_rows=[], invalidation_rows=[],
            **{'055_baseline':dict(row_count=200,rowset_sha256='e'*64,query_sha256='38cd06302c694a50dbddcc11e15fa80c03173cba95aef3d74066f4441e568ddd',scope=scope55)})
        self.raw = json.dumps(self.plan).encode()
        stack.enter_context(patch.object(guard, 'FROZEN_PLAN_SHA', sha256(self.raw).hexdigest()))
        self.budget = guard.Frozen053Budget(self.raw, self.root)
        (self.root/'synthetic-owner.py').write_bytes(b'unchanged-owner')
        (self.root/'.env').write_text('DATABASE_URL='+self.db+'\n', encoding='utf-8')
        base = self.root/'outputs/rag-semantic-retrieval/mvp-first-20261107-v1'; base.mkdir(parents=True)
        (base/'preflight.json').write_text(json.dumps(dict(manifest_sha256=self.plan['manifest_sha256'],
            protected_sha256={'.env':sha256((self.root/'.env').read_bytes()).hexdigest()})), encoding='utf-8')
        manifest = self.root/'tests/fixtures/rag_mvp_acceptance_v1.json'; manifest.parent.mkdir(parents=True)
        manifest.write_text('{"synthetic":true}',encoding='utf-8')
        self.source = stack.enter_context(patch.object(store, 'read_effective_corpus', return_value=self.corpus))
        self.connect = stack.enter_context(patch.object(store, '_connect', side_effect=AssertionError('unconfigured fake')))
        self.run = stack.enter_context(patch.object(store, '_run', return_value=7))
        self.finish = stack.enter_context(patch.object(store, '_finish'))
        self.cached = stack.enter_context(patch.object(store, '_cached', side_effect=AssertionError('no cached coordinates')))

    def execute(self, encoder=None, pre=None, publish=None):
        pre, publish = pre or FakeConnection(), publish or FakeConnection()
        self.connect.side_effect = [pre, publish]
        encoder = encoder or Mock(encode=Mock(return_value=dict(space_id=store.SPACE_ID, vectors=[UNIT]*200)))
        result = store.sync_effective_opinions(self.db, options=self.options, encoder=encoder,
            budget=self.budget, execute=True, encoding_authorized=True)
        return result, encoder, pre, publish

    def test_default_dry_run_and_missing_separate_authorization_do_no_io(self):
        result = store.sync_effective_opinions(self.db, options=self.options, budget=self.budget)
        self.assertEqual(result['status'], 'dry_run')
        with self.assertRaisesRegex(store.EmbeddingStoreError, 'authorization_required'):
            store.sync_effective_opinions(self.db, options=self.options, budget=self.budget, execute=True)
        self.connect.assert_not_called(); self.source.assert_not_called(); self.run.assert_not_called()
        self.pg.assert_not_called(); self.sqlite.assert_not_called()

    def test_exact_budget_all_200_publish_only_after_locks_with_055_unchanged(self):
        result, encoder, pre, pub = self.execute()
        self.assertEqual(result['metrics']['published_count'],200)
        self.assertEqual(result['metrics']['locked_budget'],dict(S=200,N=200,U=0,I=0,P=200))
        self.assertEqual(result['metrics']['not_indexed_count'],3391)
        self.assertEqual(result['status'],'partial')  # Finite coverage, not partial batch publication.
        self.assertEqual(len(pub.committed),200); self.assertEqual(len(result['metrics']['published_rows']),200)
        self.assertEqual((pre.rollback_count,pub.commit_count,pub.baseline_calls),(1,1,2))
        self.assertTrue(pre.closed and pub.closed)
        self.assertEqual(encoder.encode.call_count,1)
        self.assertEqual(len(encoder.encode.call_args.args[0]),200)
        self.assertEqual(len({d.review_id for d in self.selected}),114)
        locks=[i for i,(s,_) in enumerate(pub.commands) if s.startswith('LOCK TABLE')]
        check=next(i for i,(s,_) in enumerate(pub.commands) if 'stable_versioned_opinion_key=ANY' in s)
        insert=next(i for i,(s,_) in enumerate(pub.commands) if s.startswith('INSERT INTO'))
        self.assertTrue(max(locks)<check<insert)
        sql,params=next((s,p) for s,p in pub.commands if 'AND NOT (stable_versioned_opinion_key=ANY' in s)
        self.assertNotIn('text_builder_version=?',sql); self.assertEqual(len(params[-1]),3591)
        self.assertFalse(any(s.startswith('UPDATE review_opinion_embeddings') for s,_ in pub.commands))
        self.cached.assert_not_called()

    def test_plan_file_digest_tampering_and_fixed_scope_drift(self):
        with self.assertRaisesRegex(guard.SyncBudgetError,'digest'):
            guard.Frozen053Budget(self.raw+b' ',self.root)
        for change in [dict(product_ids=['100013588055']),dict(date_to='2022-08-25'),dict(source='other'),
                       dict(dataset_version='other'),dict(model='other'),dict(base_url='https://other.invalid'),
                       dict(prompt_version='other'),dict(timezone='local')]:
            with self.subTest(change=list(change)), self.assertRaises(guard.SyncBudgetError):
                self.budget.check_options(self.options|change,200)
        with self.assertRaises(guard.SyncBudgetError): self.budget.check_options(self.options,199)

    def test_database_identity_config_manifest_and_environment_drift(self):
        self.budget.check_target(self.db)
        with self.assertRaises(guard.SyncBudgetError): self.budget.check_target(self.db+'other')
        with patch.dict(os.environ,{'PGOPTIONS':'synthetic'}), self.assertRaises(guard.SyncBudgetError):
            self.budget.check_target(self.db)
        (self.root/'.env').write_text('changed',encoding='utf-8')
        with self.assertRaises(guard.SyncBudgetError): self.budget.check_target(self.db)
        (self.root/'.env').write_text('DATABASE_URL='+self.db+'\n',encoding='utf-8')
        (self.root/'tests/fixtures/rag_mvp_acceptance_v1.json').write_text('{"synthetic":false}',encoding='utf-8')
        with self.assertRaisesRegex(guard.SyncBudgetError,'manifest'): self.budget.check_target(self.db)
        (self.root/'tests/fixtures/rag_mvp_acceptance_v1.json').write_text('{"synthetic":true}',encoding='utf-8')
        (self.root/'synthetic-owner.py').write_bytes(b'changed-owner')
        with self.assertRaisesRegex(guard.SyncBudgetError,'owner'): self.budget.check_target(self.db)

    def test_changed_source_selected_key_input_and_incomplete_group_fail_closed(self):
        for corpus in [replace(self.corpus, label_profile_key='changed'),
                       replace(self.corpus, documents=self.corpus.documents[:-1])]:
            with self.assertRaises(guard.SyncBudgetError): self.budget.check_corpus(corpus,self.selected)
        for selected in [self.selected[:-1],self.selected[1:]+[self.corpus.documents[201]],
                         [replace(self.selected[0],embedding_input='replaced')]+self.selected[1:]]:
            with self.assertRaises(guard.SyncBudgetError): self.budget.check_corpus(self.corpus,selected)

    def test_U_I_and_cross_product_drift_before_encoding(self):
        for conn in [FakeConnection(existing=[dict(product_id='100013588053')]),
                     FakeConnection(existing=[dict(product_id='100013588055')]),
                     FakeConnection(invalidated=[dict(id=1)]), FakeConnection(baseline='f'*64)]:
            with self.subTest(conn=conn), self.assertRaises(guard.SyncBudgetError): self.execute(pre=conn)
            self.assertTrue(conn.closed); self.assertEqual(conn.rollback_count,1)
        self.run.assert_not_called()

    def test_U_I_owner_and_055_drift_after_locks_before_vector_DML(self):
        for pub in [FakeConnection(existing=[dict(product_id='100013588053')]),
                    FakeConnection(existing=[dict(product_id='100013588055')]),
                    FakeConnection(existing=[dict(product_id='other')]),
                    FakeConnection(invalidated=[dict(id=1)]), FakeConnection(baseline='f'*64)]:
            with self.subTest(pub=pub), self.assertRaises(store.EmbeddingStoreError): self.execute(publish=pub)
            self.assertEqual(pub.committed,[]); self.assertEqual(pub.rollback_count,1)
            self.assertFalse(any(s.startswith('INSERT INTO') for s,_ in pub.commands))

    def test_one_none_overlong_bad_shape_value_norm_count_space_or_exception_stops_whole_batch(self):
        outputs=[dict(space_id=store.SPACE_ID,vectors=[UNIT]*199+[None]),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*199+[[0.0]*1024]),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*199+[[float('nan')]*1024]),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*199+[[1.0,0.0]]),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*199),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*201),
                 dict(space_id=store.SPACE_ID,vectors=[UNIT]*200,error='PRIVATE partial error'),
                 dict(space_id='wrong',vectors=[UNIT]*200)]
        for output in outputs+[None]:
            encoder=Mock()
            if output is None: encoder.encode.side_effect=RuntimeError('PRIVATE model failure')
            else: encoder.encode.return_value=output
            with self.subTest(output='exception' if output is None else len(output['vectors'])), \
                 self.assertRaises(store.EmbeddingStoreError) as caught:
                self.execute(encoder=encoder)
            self.assertNotIn('PRIVATE',str(caught.exception)); self.assertEqual(self.connect.call_count,1)
            self.connect.reset_mock(); self.assertEqual(encoder.encode.call_count,1)
            self.assertEqual(self.finish.call_args.args[3]['published_count'],0)

    def test_overlength_worker_marker_cannot_publish_remaining_199(self):
        encoder=Mock(encode=Mock(return_value=dict(space_id=store.SPACE_ID,
            vectors=[UNIT]*199+[None],metrics={'token_counts':[10]*199+[513]})))
        with self.assertRaises(store.EmbeddingStoreError): self.execute(encoder=encoder)
        self.assertEqual(self.connect.call_count,1)
        self.assertEqual(self.finish.call_args.args[3]['overlength_count'],1)
        self.assertEqual(self.finish.call_args.args[3]['published_count'],0)

    def test_source_snapshot_change_during_encoding_rolls_back(self):
        self.source.side_effect=[self.corpus,replace(self.corpus,reviews=self.corpus.reviews[:-1])]
        pub=FakeConnection()
        with self.assertRaises(store.SyncRetryRequired): self.execute(publish=pub)
        self.assertEqual(pub.rollback_count,1); self.assertEqual(pub.committed,[])

    def test_insert_failure_conflict_and_055_final_guard_roll_back_all_rows(self):
        for pub in [FakeConnection(fail_insert=99),FakeConnection(conflict=True),
                    FakeConnection(final_baseline='f'*64),FakeConnection(commit_failure=True)]:
            with self.subTest(pub=pub), self.assertRaises(store.EmbeddingStoreError): self.execute(publish=pub)
            self.assertEqual(pub.committed,[]); self.assertEqual(pub.staged,[])
            self.assertEqual(pub.rollback_count,1); self.assertTrue(pub.closed)
            self.assertEqual(self.finish.call_args.args[3]['published_count'],0)
            self.assertNotIn('published_rows',self.finish.call_args.args[3])

    def test_direct_publish_requires_complete_vector_key_set(self):
        for vectors in [{},{d.stable_versioned_opinion_key:UNIT for d in self.selected[1:]}]:
            with self.assertRaises(store.EmbeddingStoreError):
                store._publish(self.db,self.options,self.corpus,self.selected,vectors,7,{},0,budget=self.budget)
        self.connect.assert_not_called()

    def test_naked_053_sync_cannot_bypass_frozen_plan(self):
        with self.assertRaisesRegex(store.EmbeddingStoreError,'controlled_053_budget_required'):
            store.sync_effective_opinions(self.db,options=self.options,encoder=Mock(),execute=True,encoding_authorized=True)
        self.source.assert_not_called(); self.connect.assert_not_called()

    def test_cli_default_dry_missing_encoding_claim_and_private_receipt(self):
        args=['sync','--source','reviews_csv','--dataset-version','reviews-csv','--product-id','100013588053',
              '--date-from','2021-09-11','--date-to','2022-08-26','--model','SYNTHETIC_MODEL',
              '--prompt-version','SYNTHETIC_PROMPT','--base-url-env','SYNTHETIC_BASE']
        out=io.StringIO();err=io.StringIO()
        with patch.object(cli.Frozen053Budget,'load',return_value=self.budget), \
             patch.object(cli,'database_argument',return_value=self.db), \
             patch.object(cli,'read_settings',return_value={'SYNTHETIC_BASE':self.options['base_url']}), \
             patch.object(cli,'LocalEmbeddingProcess') as encoder, \
             patch.object(cli,'ROOT',self.root), \
             patch.object(cli.sys,'executable',str(self.root/'.venv/Scripts/python.exe')), \
             patch.object(cli.Path,'resolve',autospec=True,side_effect=lambda p: self.root/p if not p.is_absolute() else p), \
             redirect_stdout(out),redirect_stderr(err):
            self.assertEqual(cli.main(args),0); encoder.assert_not_called()
            self.assertEqual(cli.main(args+['--execute']),1); encoder.assert_not_called()
            self.assertFalse((self.root/cli.EXECUTION).exists())
            with patch.object(cli,'sync_effective_opinions',return_value=dict(status='partial',metrics={
                'published_count':200,'published_rows':[dict(id=1,key='PRIVATE_KEY')]})) as sync:
                self.assertEqual(cli.main(args+['--execute','--authorize-local-encoding']),0)
                self.assertEqual(cli.main(args+['--execute','--authorize-local-encoding']),1)
                self.assertEqual(sync.call_count,1)
        self.assertNotIn('PRIVATE',out.getvalue()+err.getvalue())
        self.assertIn('PRIVATE_KEY',(self.root/cli.EXECUTION/'result.json').read_text(encoding='utf-8'))
        self.connect.assert_not_called()


if __name__ == '__main__': unittest.main()
