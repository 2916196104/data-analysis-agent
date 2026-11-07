"""Offline preparation only; real connections, encoders and Agent tasks are blocked."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('manual_retest',ROOT/'scripts/run_rag_mvp_retest.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
MANIFEST=entry.load(ROOT/'tests/fixtures/rag_mvp_acceptance_v1.json')


def synthetic_receipt():
    keys=[dict(id=i+1,key='synthetic-'+str(i)) for i in range(200)]
    m=dict(scope_opinion_count=3591,selected_opinion_count=200,attempted_count=200,successful_count=200,
        published_count=200,reused_count=0,failed_count=0,overlength_count=0,invalidated_count=0,
        indexed_current_after=200,not_indexed_count=3391,locked_budget=dict(S=200,N=200,U=0,I=0,P=200),published_rows=keys)
    return dict(run_id=4,status='partial',metrics=m)


def synthetic_action(identifier='M01', *, refs=True):
    text='😀配送很快。后来更正，实际说的是安装。'
    row=dict(key='synthetic-key',rank=1,cosine_distance=0.12,source='reviews_csv',dataset_version='reviews-csv',
        product_id='100013588053',review_id=1,published_at='2022-01-01',opinion_source='model',
        file_sha256='a'*64,line_number=1,quote='配送很快',quote_start=1,quote_end=5,review_text=text,
        aspect='logistics',sentiment='positive',annotation_id=2,effective_snapshot_sha256='b'*64)
    raw=dict(evidence=[row]); data=dict(scope={'base_url':'[PRIVATE_ENDPOINT]'},retrieval=raw,
        assessment=dict(policy='reported_experience_v2',permitted_keys=['synthetic-key'] if refs else [],candidate_judgments=[]))
    reference={k:row[k] for k in ('review_id','product_id','published_at','opinion_source')}
    reference.update(source_result_id='r1',evidence_ref='sha256:'+'a'*64+'#line=1',opinions=[dict(
        **{k:row[k] for k in ('quote','quote_start','quote_end','aspect','sentiment','annotation_id','effective_snapshot_sha256')},opinion_key=row['key'])])
    result=dict(status='ok',validation={'status':'passed'},results={'r1':dict(kind='semantic_evidence',data=data)},
        public_answer={'summary':'SYNTHETIC ANSWER','evidence_refs':[reference] if refs else []},total_model_calls=0)
    return dict(id=identifier,question=next(c['question'] for c in MANIFEST['cases'] if c['id']==identifier[:3]),
        product_ids=['100013588053'],p0_validated=True,query_encodings=1,pgvector_queries=1,
        retrieval=raw,task={'result':result})


class RetestPreparationTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch('psycopg.connect',side_effect=AssertionError('no real PG')))
        stack.enter_context(patch('sqlite3.connect',side_effect=AssertionError('no actual queue')))
        stack.enter_context(patch('socket.socket.connect',side_effect=AssertionError('no network')))
        stack.enter_context(patch('data_analysis_agent.reviews.embedding_process.PersistentEmbeddingProcess.embed_texts',side_effect=AssertionError('no BGE')))
        stack.enter_context(patch('data_analysis_agent.jobs.worker.TaskWorker._execute',side_effect=AssertionError('no Agent tasks')))
        self.tmp=tempfile.TemporaryDirectory(prefix='retest-offline-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        stack.enter_context(patch.object(entry,'ROOT',self.root))
        stack.enter_context(patch.object(sys,'executable',str(self.root/'.venv/Scripts/python.exe')))
        self.runner=stack.enter_context(patch.object(entry,'load_first_runner',side_effect=AssertionError('execution not authorized')))
        self.git=stack.enter_context(patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0)))
        self.budget=Mock();self.budget.plan.return_value={'selected_documents':[
            {'stable_versioned_opinion_key':'synthetic-'+str(i)} for i in range(200)]}
        stack.enter_context(patch.object(entry.Frozen053Budget,'load',return_value=self.budget))
        stack.enter_context(patch.object(entry,'read_settings',return_value={'DATABASE_URL':'postgresql://synthetic'}))
        fake_settings=SimpleNamespace(rag_python=str(self.root/'model-python.exe'),rag_model_cache=str(self.root/'cache'))
        (self.root/'model-python.exe').touch();(self.root/'cache').mkdir()
        stack.enter_context(patch('data_analysis_agent.jobs.submission.configured_identity',return_value=(None,{
            'model':MANIFEST['annotation_model'],'base_url':'https://synthetic.invalid','prompt_version':MANIFEST['annotation_prompt_version']})))
        stack.enter_context(patch('data_analysis_agent.jobs.submission.configured_agent_identity',return_value=(fake_settings,{})))
        # Fixture profile is synthetic; the production manifest/pins are unchanged.
        self.manifest=deepcopy(MANIFEST)
        self.manifest['annotation_profile_sha256']=entry.content_sha256([
            MANIFEST['annotation_model'],'https://synthetic.invalid',MANIFEST['annotation_prompt_version']])
        stack.enter_context(patch.object(entry,'MANIFEST_SHA',entry.content_sha256(self.manifest)))
        mf=self.root/'tests/fixtures/rag_mvp_acceptance_v1.json';mf.parent.mkdir(parents=True)
        mf.write_text(json.dumps(self.manifest),encoding='utf-8')
        base=self.root/entry.FIRST;base.mkdir(parents=True)
        source=b'not an executable real runner'
        (base/'run_once.py').write_bytes(source)
        stack.enter_context(patch.object(entry,'RUNNER_SHA',sha256(source).hexdigest()))
        (self.root/'.env').write_bytes(b'SYNTHETIC CONFIG')
        (base/'preflight.json').write_text(json.dumps({'protected_sha256':{'.env':sha256(b'SYNTHETIC CONFIG').hexdigest()}}),encoding='utf-8')
        receipt=self.root/entry.SYNC;receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps(synthetic_receipt()),encoding='utf-8')
        stack.enter_context(patch.object(entry,'SYNC_SHA',sha256(receipt.read_bytes()).hexdigest()))

    def test_default_only_frozen_file_checks_no_runner_or_claim(self):
        output=io.StringIO()
        with redirect_stdout(output): result=entry.main([])
        self.assertEqual(result,0);self.runner.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'],'READY_FOR_RETEST_AUTHORIZATION')
        self.assertFalse((self.root/entry.OUTPUT).exists())

    def test_both_separate_authorizations_required(self):
        for args in [['--execute-once'],['--execute-once','--authorize-postgres-readonly-acceptance'],
                     ['--execute-once','--authorize-local-query-encoding']]:
            with self.subTest(args=args),redirect_stdout(io.StringIO()): self.assertEqual(entry.main(args),1)
        self.runner.assert_not_called();self.assertFalse((self.root/entry.OUTPUT).exists())

    def test_frozen_runner_manifest_sync_and_configuration_drift_block(self):
        for path in [self.root/entry.FIRST/'run_once.py',self.root/'tests/fixtures/rag_mvp_acceptance_v1.json',
                     self.root/entry.SYNC,self.root/'.env']:
            before=path.read_bytes()
            path.write_bytes(before.replace(b'"top_k": 5',b'"top_k": 6') if path.name=='rag_mvp_acceptance_v1.json' else before+b' ')
            with self.subTest(path=path.name),self.assertRaises(Exception): entry.preflight(self.root)
            path.write_bytes(before)
        self.git.return_value=SimpleNamespace(returncode=1)
        with self.assertRaises(Exception): entry.preflight(self.root)
        self.runner.assert_not_called()

    def test_sync_receipt_exact_keys_counts_and_full_groups_are_bound(self):
        result=entry.sync_receipt(self.root,self.budget.plan())
        self.assertEqual(result['metrics']['locked_budget'],dict(S=200,N=200,U=0,I=0,P=200))
        self.budget.plan.return_value['selected_documents'][0]['stable_versioned_opinion_key']='other'
        with self.assertRaises(Exception): entry.sync_receipt(self.root,self.budget.plan())

    def test_consumed_directory_blocks_even_default_preflight(self):
        (self.root/entry.OUTPUT).mkdir()
        with redirect_stdout(io.StringIO()): self.assertEqual(entry.main([]),1)
        self.runner.assert_not_called()

    def test_claim_preserved_if_runner_loading_fails_no_retry(self):
        checked=entry.preflight(self.root)
        result=entry.execute_once(self.root,checked)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(self.runner.call_count,1)
        self.assertTrue((self.root/entry.OUTPUT/'attempt.json').is_file())
        with self.assertRaises(FileExistsError): entry.execute_once(self.root,checked)
        self.assertEqual(self.runner.call_count,1)

    def fake_finished_runner(self, **changes):
        receipt=dict(api_submissions=13,agent_tasks=13,query_encodings=11,pgvector_queries=11,
            external_attempts=0,business_write_attempts=0,execution_finished=True,protected_files_unchanged=True,
            embedding_workers_closed=True,business_connections_closed=True,business_connections_readonly=True,
            cases=[{'id':identifier} for identifier in [f'M{i:02d}' for i in range(1,10)]+['M10A','M10B','M10C','M10D']])
        receipt.update(changes)
        def main():
            (self.root/entry.OUTPUT/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        class Client:
            def post(self,*args,**kwargs): raise AssertionError('no actual HTTP/Agent tasks')
        runner=SimpleNamespace(_processes={},db='postgresql://synthetic',manifest=self.manifest,
            TestClient=Client,main=Mock(side_effect=main),label={'base_url':'https://synthetic.invalid'})
        self.runner.side_effect=None;self.runner.return_value=runner
        return runner

    def test_completed_fake_pipeline_stays_pending_semantics_and_independent(self):
        runner=self.fake_finished_runner()
        review={'actions':[{'structural_errors':[]}], 'semantic_verified':False}
        with patch.object(entry,'build_review',return_value=review),redirect_stdout(io.StringIO()):
            result=entry.main(['--execute-once','--authorize-postgres-readonly-acceptance','--authorize-local-query-encoding'])
        self.assertEqual(result,0);runner.main.assert_called_once()
        summary=entry.load(self.root/entry.OUTPUT/'summary.json')
        self.assertEqual(summary['status'],'PENDING_SEMANTIC_REVIEW')
        self.assertIsNone(summary['actual_case_passes']);self.assertIsNone(summary['validated_primary_supported_answers'])
        self.assertEqual(runner.OUT,self.root/entry.OUTPUT)
        self.assertNotEqual(runner.OUT,self.root/entry.FIRST)

    def test_over_budget_or_cleanup_failure_never_reports_completion(self):
        runner=self.fake_finished_runner(query_encodings=12,business_connections_closed=False)
        result=entry.execute_once(self.root,entry.preflight(self.root))
        self.assertEqual(result['status'],'BLOCKED');runner.main.assert_called_once()
        self.assertTrue((self.root/entry.OUTPUT/'receipt.json').is_file())

    def test_optimized_python_cannot_disable_original_quota_assertions(self):
        with patch.object(entry.sys,'flags',SimpleNamespace(optimize=1)), self.assertRaises(Exception):
            entry.preflight(self.root)
        self.runner.assert_not_called()

    def review(self, action):
        with patch('data_analysis_agent.agents.review_retrieval.validate_retrieval',return_value=True):
            return entry.build_review({'cases':[action]},MANIFEST,'https://synthetic.invalid')

    def test_unique_unicode_and_legal_citation_do_not_become_semantic_pass(self):
        action=synthetic_action();before=deepcopy(action);review=self.review(action)
        self.assertEqual(action,before)
        self.assertEqual(review['actions'][0]['structural_errors'],[])
        self.assertTrue(all(v is None for v in review['actions'][0]['semantic_review'].values()))
        self.assertTrue(all(v is None for v in review['case_passes'].values()))
        self.assertIsNone(review['validated_primary_supported_answers'])
        self.assertFalse(review['publication_allowed'] or review['semantic_verified'])
        self.assertEqual(action['retrieval']['evidence'][0]['review_text'],before['retrieval']['evidence'][0]['review_text'])
        self.assertEqual(review['case_M10_requires_all'],['M10A','M10B','M10C','M10D'])

    def test_invalid_unicode_unpermitted_source_and_scope_citations_fail(self):
        mutations=[lambda a:a['retrieval']['evidence'][0].update(quote_start=0),
            lambda a:a['retrieval']['evidence'][0].update(product_id='100013588055'),
            lambda a:a['task']['result']['public_answer']['evidence_refs'][0]['opinions'][0].update(opinion_key='unknown'),
            lambda a:a['task']['result']['public_answer']['evidence_refs'][0].update(evidence_ref='forged'),
            lambda a:a.update(p0_validated=False),lambda a:a['task']['result'].update(total_model_calls=1)]
        for mutation in mutations:
            action=synthetic_action();mutation(action)
            with self.subTest(mutation=mutation): self.assertTrue(self.review(action)['actions'][0]['structural_errors'])

    def test_retrieval_only_candidates_require_review_and_never_get_permission(self):
        action=synthetic_action('M04',refs=False);review=self.review(action)
        self.assertEqual(review['actions'][0]['structural_errors'],[])
        self.assertIsNone(review['actions'][0]['semantic_review']['capability_gap'])
        self.assertIsNone(review['actions'][0]['semantic_review']['pass_result'])
        action=synthetic_action('M04',refs=True)
        self.assertIn('retrieval_only_permission_bypassed',self.review(action)['actions'][0]['structural_errors'])

    def test_M10_B_C_require_zero_queries_and_no_answer(self):
        for identifier,status in [('M10B','unsupported'),('M10C','error')]:
            action=synthetic_action(identifier,refs=False)
            action.update(query_encodings=0,pgvector_queries=0,retrieval={},p0_validated=False)
            action['task']['result']=dict(status=status,public_answer={'evidence_refs':[]})
            self.assertEqual(self.review(action)['actions'][0]['structural_errors'],[])
            action['query_encodings']=1
            self.assertIn('isolation_or_failure_behavior_invalid',self.review(action)['actions'][0]['structural_errors'])


if __name__=='__main__': unittest.main()
