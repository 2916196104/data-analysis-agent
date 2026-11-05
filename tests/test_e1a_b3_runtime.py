"""B3-A uses only synthetic quotes, sources, references and execution permissions."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import shutil
import unittest
from unittest.mock import patch

from data_analysis_agent.core.serialization import canonical_json
from data_analysis_agent.reviews.e1_preparation import load
from data_analysis_agent.reviews.e1_review_protocol import review_context_view
from data_analysis_agent.reviews.e1a_b3_worker import WorkerRequest, execute_request
from data_analysis_agent.reviews.e1a_b3_runtime import (
    FIXED_CASES, EXPERIMENT_ID, RunConfig, ExecutionPermit, freeze_requests, project_request,
    validate_config, validate_request, seal_attempt, audit_sealed_run, run_debug,
    permission_probe, read, write_new, file_sha,
)
from data_analysis_agent.reviews.e1a_protocol import PredictionInput, digest
from data_analysis_agent.reviews.e1a_output_audit import audit_output
from data_analysis_agent.reviews.e1a_evaluation import evaluate_e1a
from data_analysis_agent.reviews.e1a_windows_worker import AppContainer, IsolationError, stage_python
from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
from test_e1a_b1 import fixture, predict, audit


def eight_fixture(quotes=('客服回复及时',)):
    """Real CASE labels test selection only. All payload content is synthetic."""
    f = fixture(quotes,descriptions=(quotes[0],)); requests = []; refs = []
    for cid, qid, mode in FIXED_CASES:
        p = f['inputs'][mode].model_dump(mode='json')
        p['experiment_id'] = EXPERIMENT_ID
        batch = p['prepared']['candidate_batch']; batch['query_id'] = qid
        for h in batch['candidates']: h['query_id'] = qid
        ref = next(r for r in f['release'].references if r.reference.mode == mode).model_dump(mode='json')
        ref['case_id'] = cid; human = ref['reference']; human['query_id'] = qid; human['review_id'] = 'SYNTHETIC-' + cid
        for finding in human['fact_findings']:
            for w in finding['evidence_refs']: w['query_id'] = qid
        if cid == 'CASE-005':
            batch['candidates'] = []
            human.update(reference_state='insufficient', supported_target_fact_ids=[], missing_required_fact_ids=['f0'])
            human['fact_findings'][0].update(support='not_supported', evidence_refs=[])
            human['reviewer_decisions'][0].update(reference_state='insufficient', supported_target_fact_ids=[])
        p['prepared']['batch_sha256'] = digest(batch)
        from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
        p['prepared']['context_input_sha256'] = digest(review_context_view(load(batch,EvidenceBatch),'quote_full_review_veto'))
        human['batch_sha256'] = p['prepared']['batch_sha256']; human['context_input_sha256'] = p['prepared']['context_input_sha256']
        requests.append(project_request(cid, load(p,PredictionInput), f['sources']))
        refs.append(ref)
    release = f['release'].model_dump(mode='json'); release['references'] = refs
    return requests, load(release,ReferenceRelease), f['sources']


def result(raw=b'', code=0, timeout=False):
    return dict(stdout=raw, stderr=b'SYNTHETIC STDERR', exit_code=code, timed_out=timeout,
                elapsed_seconds=0.01, token_is_appcontainer=True)


def synthetic_manifest(prepared, run, config, outputs):
    run.mkdir(); (run/'raw').mkdir()
    receipts = []
    for pin, out in zip(config.cases, outputs):
        receipts.append(seal_attempt(run/'raw'/pin.case_id,case_id=pin.case_id,input_sha256=pin.input_sha256,process_result=out))
    manifest = dict(version='e1a-b3-raw-manifest-v1', config_sha256=digest(config), code_sha256=config.code_sha256,
        results=receipts, files={p.relative_to(run).as_posix():file_sha(p) for p in run.rglob('*') if p.is_file()})
    write_new(run/'raw-manifest.json',manifest)
    return file_sha(run/'raw-manifest.json')


class RuntimeTests(unittest.TestCase):
    def test_fixed_eight_order_modes_and_one_attempt_are_frozen(self):
        requests, release, sources = eight_fixture()
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t)/'prepared'; c = freeze_requests(requests, release_sha256=digest(release), destination=dest)
            self.assertEqual(tuple((p.case_id,p.query_id,p.mode) for p in c.cases),FIXED_CASES)
            self.assertEqual((c.engine,c.engine_version,c.attempts_per_case,c.automatic_retries),('R0','explicit-clauses-zh-v1',1,0))
            self.assertEqual(validate_config(read(dest/'config.json'),expected_sha256=digest(c)),c)
            for pin in c.cases: validate_request(c,pin,read(dest/'runtime'/pin.case_id/'request.json'))
            self.assertFalse(read(dest/'freeze-receipt.json')['prediction_execution_authorized'])
            with self.assertRaises(ValueError): freeze_requests(requests,release_sha256=digest(release),destination=dest)

    def test_cannot_drop_add_duplicate_reorder_or_change_mode(self):
        requests, release, _ = eight_fixture()
        variations = (requests[:-1],requests+[requests[0]],list(reversed(requests)),[requests[1],*requests[1:]])
        for rows in variations:
            with self.subTest(count=len(rows)), tempfile.TemporaryDirectory() as t, self.assertRaises(ValueError):
                freeze_requests(rows,release_sha256=digest(release),destination=Path(t)/'new')
        raw = requests[0].model_dump(mode='json');raw['prediction']['prepared']['candidate_batch']['mode']='filtered'
        with tempfile.TemporaryDirectory() as t, self.assertRaises(ValueError):
            freeze_requests([raw,*requests[1:]],release_sha256=digest(release),destination=Path(t)/'new')

    def test_input_allowlist_and_no_reference_source_set_to_worker(self):
        requests, release, _ = eight_fixture()
        for request in requests:
            self.assertEqual(set(h.key for h in request.comparison_batch.candidates),
                             set(h.key for h in request.prediction.prepared.candidate_batch.candidates))
            wire = request.model_dump_json()
            for forbidden in ('fact_findings','relation_findings','reference_state','reviewer_decisions','decision_rationale',
                              'supported_target_fact_ids','missing_required_fact_ids','cosine_distance','grade','review_notes'):
                self.assertNotIn('"'+forbidden+'"',wire)
        for field in ('reference','metrics','human_confirmed','source_collection'):
            raw = requests[0].model_dump(mode='json');raw[field] = 'SYNTHETIC GOLD'
            with self.subTest(field=field),self.assertRaises(ValueError):load(raw,WorkerRequest)

    def test_projection_omits_unselected_sources_and_quotes(self):
        f = fixture(('客服回复及时','客服态度友好'),descriptions=('客服回复及时',))
        raw = f['inputs']['unfiltered'].model_dump(mode='json');b = raw['prepared']['candidate_batch'];b['candidates']=b['candidates'][:1]
        from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
        raw['prepared']['batch_sha256']=digest(b)
        raw['prepared']['context_input_sha256']=digest(review_context_view(load(b,EvidenceBatch),'quote_full_review_veto'))
        p=load(raw,PredictionInput);r=project_request('SYNTHETIC',p,f['sources'])
        self.assertEqual(len(r.comparison_batch.candidates),1)
        self.assertNotIn('客服态度友好',r.model_dump_json())

    def test_worker_matches_existing_r0_output_without_changing_semantics(self):
        f=fixture();p=f['inputs']['unfiltered'];r=project_request('SYNTHETIC',p,f['sources'])
        self.assertEqual(execute_request(r,expected_request_sha256=digest(r)),predict(f))
        self.assertEqual(r.prediction.prepared.history_status,'history_unproven')
        self.assertFalse(r.independent_seal)

    def test_worker_cli_synthetic_request_has_no_reference_argument(self):
        r=eight_fixture()[0][0]
        with tempfile.TemporaryDirectory() as t:
            request=Path(t)/'request.json';write_new(request,r.model_dump(mode='json'))
            proc=subprocess.run([sys.executable,'-I','-B','-m','data_analysis_agent.reviews.e1a_b3_worker',str(request),digest(r)],
                capture_output=True,timeout=15)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            self.assertEqual(json.loads(proc.stdout),execute_request(r,expected_request_sha256=digest(r)).model_dump(mode='json'))

    @unittest.skipUnless(os.name=='nt','staged Windows CPython distribution')
    def test_staged_interpreter_imports_only_staged_packages(self):
        with tempfile.TemporaryDirectory() as t:
            exe=stage_python(Path(t));r=eight_fixture()[0][0];request=exe.parent/'request.json'
            write_new(request,r.model_dump(mode='json'))
            proc=subprocess.run([str(exe),'-I','-B','-m','data_analysis_agent.reviews.e1a_b3_worker',str(request),digest(r)],
                capture_output=True,timeout=15)
            self.assertEqual(proc.returncode,0,proc.stderr.decode(errors='replace'))
            self.assertEqual(json.loads(proc.stdout)['input_sha256'],r.input_sha256)

    def test_empty_unknown_and_compound_relation_outputs_are_traceable(self):
        cases = [fixture((),ref_direct=()), fixture(('合成未知断言',),descriptions=('合成未知断言',))]
        ds=('设备已到货','安装人员仍未上门')
        facts=dict(operator='all_of',items=[dict(id='f0',description=ds[0],sentiment='neutral'),dict(id='f1',description=ds[1],sentiment='negative')],
                   relations=[dict(kind='same_review',fact_ids=['f0','f1']),dict(kind='before',fact_ids=['f0','f1'])])
        cases.append(fixture(('设备已到货，然后安装人员仍未上门',),descriptions=ds,required_facts=facts,sentiment_intent='negative',target_entity='multiple',subtopic='multiple'))
        for f in cases:
            p=f['inputs']['unfiltered'];r=project_request('SYNTHETIC',p,f['sources']);out=execute_request(r,expected_request_sha256=digest(r))
            self.assertEqual(out,predict(f));self.assertEqual(out.execution_status,'completed')
            self.assertEqual(audit(f,out).execution,'completed')
            for w in out.evidence_refs:self.assertIn(w.key,[h.key for h in p.prepared.candidate_batch.candidates])
        self.assertEqual(len(out.relation_findings),2)
        self.assertTrue(all(x.support=='supported' for x in out.relation_findings))

    def test_request_hash_source_and_quote_conflicts_fail_before_rules(self):
        r=eight_fixture()[0][0]
        for field,value in (('request_hash','0'*64),('source','0'*64),('span',100000)):
            raw=r.model_dump(mode='json');pin=digest(r)
            if field=='request_hash':pin=value
            elif field=='source':raw['comparison_batch']['candidates'][0]['embedding_input_sha256']=value;raw['comparison_sha256']=digest(raw['comparison_batch']);pin=digest(raw)
            else:raw['prediction']['prepared']['candidate_batch']['candidates'][0]['quote_end']=value;pin=digest(raw)
            with self.subTest(field=field),patch('data_analysis_agent.reviews.e1a_b3_worker._predict_r0_validated',side_effect=AssertionError('must not run')),self.assertRaises(ValueError):
                execute_request(raw,expected_request_sha256=pin)

    def test_code_or_candidate_order_changed_after_freeze_is_rejected(self):
        requests,release,_=eight_fixture(('客服回复及时','客服态度友好'))
        with tempfile.TemporaryDirectory() as t:
            c=freeze_requests(requests,release_sha256=digest(release),destination=Path(t)/'p')
            with patch('data_analysis_agent.reviews.e1a_b3_runtime.source_files',return_value={}):
                with self.assertRaises(ValueError):validate_config(c,expected_sha256=digest(c))
            raw=requests[0].model_dump(mode='json');raw['prediction']['contract_revision']+=1
            with self.assertRaises(ValueError):validate_request(c,c.cases[0],raw)
            raw=requests[0].model_dump(mode='json')
            raw['prediction']['prepared']['candidate_batch']['candidates'].reverse()
            with self.assertRaises(ValueError):validate_request(c,c.cases[0],raw)

    def test_permission_or_authorization_failure_cannot_launch_predictions(self):
        requests,release,_=eight_fixture();basis=b'SYNTHETIC AUTHORIZATION';bpin=sha256(basis).hexdigest()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);c=freeze_requests(requests,release_sha256=digest(release),destination=root/'p')
            permit=ExecutionPermit(action='b3_eight_case_r0_debug',data_kind='synthetic',config_sha256=digest(c),
                approval_ref='SYNTHETIC',authorization_basis_sha256=bpin)
            with patch('data_analysis_agent.reviews.e1a_b3_runtime.stage_python',side_effect=AssertionError('no launch')):
                with self.assertRaises(ValueError):run_debug(root/'p',expected_config_sha256=digest(c),permit=permit,
                    expected_permit_sha256='0'*64,authorization_basis=basis,expected_basis_sha256=bpin,destination=root/'run',protected_paths=[])
            def synthetic_stage(dest):
                shutil.copytree(Path(__file__).resolve().parents[1]/'src',dest/'python'/'src',ignore=shutil.ignore_patterns('__pycache__'))
                return dest/'python'/'python.exe'
            with patch('data_analysis_agent.reviews.e1a_b3_runtime.stage_python',side_effect=synthetic_stage), \
                 patch('data_analysis_agent.reviews.e1a_b3_runtime.AppContainer',side_effect=IsolationError('SYNTHETIC OS BLOCK')):
                manifest=run_debug(root/'p',expected_config_sha256=digest(c),permit=permit,expected_permit_sha256=digest(permit),
                    authorization_basis=basis,expected_basis_sha256=bpin,destination=root/'blocked',protected_paths=[])
            self.assertEqual(manifest['results'][0]['status'],'isolation_or_launch_failed')
            self.assertEqual([r['status'] for r in manifest['results'][1:]],['not_attempted_after_stop']*7)

    def test_exact_raw_bytes_timeout_missing_invalid_and_failure_are_preserved(self):
        scenarios=[(b'{invalid',0,False,'invalid_output'),(b'',0,False,'missing_output'),
                   (b'{"partial":true}',0,True,'timeout'),(b'\x00\xff',1,False,'execution_failed')]
        with tempfile.TemporaryDirectory() as t:
            for n,(raw,code,timeout,status) in enumerate(scenarios):
                dest=Path(t)/str(n);receipt=seal_attempt(dest,case_id='SYNTHETIC',input_sha256='1'*64,process_result=result(raw,code,timeout))
                self.assertEqual((dest/'output.raw').read_bytes(),raw);self.assertEqual(receipt['status'],status)
                self.assertEqual(receipt['raw_sha256'],sha256(raw).hexdigest());self.assertFalse(receipt['automatic_retry'])
                with self.assertRaises(FileExistsError):seal_attempt(dest,case_id='SYNTHETIC',input_sha256='1'*64,process_result=result())

    def test_post_seal_independent_audit_keeps_all_eight_and_pairs(self):
        requests,release,sources=eight_fixture()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config=freeze_requests(requests,release_sha256=digest(release),destination=root/'p')
            outs=[result(canonical_json(execute_request(r,expected_request_sha256=digest(r)).model_dump(mode='json')).encode()) for r in requests]
            outs[2]=result(b'not-json');outs[5]=result(b'',1,True)
            pin=synthetic_manifest(root/'p',root/'run',config,outs)
            before={str(p):p.read_bytes() for p in (root/'run').rglob('*') if p.is_file()}
            with patch('data_analysis_agent.reviews.e1a_prediction._judge',side_effect=AssertionError('audit cannot predict')):
                metrics=audit_sealed_run(root/'p',root/'run',expected_config_sha256=digest(config),expected_manifest_sha256=pin,
                    release=release,sources=sources,destination=root/'audit')
            self.assertEqual(metrics['overall']['expected'],8);self.assertEqual(len(metrics['case_ledger']),8)
            self.assertEqual(metrics['overall']['valid_comparisons'],6)
            self.assertEqual(metrics['overall']['execution_failures'],1)
            self.assertEqual(metrics['overall']['unparseable_outputs'],1)
            self.assertEqual(metrics['paired']['state_agreement']['paired_query_denominator'],2)
            self.assertIsNone(metrics['overall']['citation_validity_rate']['value'])
            self.assertEqual({str(p):p.read_bytes() for p in (root/'run').rglob('*') if p.is_file()},before)

    def test_no_citation_illegal_span_cross_review_and_all_refusal_are_not_hidden(self):
        f=fixture();out=predict(f).model_dump(mode='json')
        out['fact_findings'][0]['evidence_refs']=[]
        self.assertIn('direct_fact_without_citation',audit(f,out).errors)
        out=predict(f).model_dump(mode='json');out['evidence_refs'][0]['quote_end']+=999
        self.assertIn('invalid_citation_identity_or_span',audit(f,out).errors)
        ds=('设备制冷良好','设备制热良好')
        facts=dict(operator='all_of',items=[dict(id=f'f{i}',description=d,sentiment='positive') for i,d in enumerate(ds)],
                   relations=[dict(kind='same_review',fact_ids=['f0','f1'])])
        f=fixture(ds,descriptions=ds,target_entity='device',subtopic='multiple',required_facts=facts,ref_relations='not_supported',ref_state='insufficient')
        out=predict(f).model_dump(mode='json')
        out['relation_findings'][0].update(support='supported',evidence_keys=[h.key for h in f['inputs']['unfiltered'].prepared.candidate_batch.candidates])
        self.assertIn('cross_review_relation',audit(f,out).errors)
        f=fixture(('合成未覆盖句子',),descriptions=('合成未覆盖句子',))
        rows=[audit(f,predict(f,m),m) for m in ('unfiltered','filtered')]
        metrics=evaluate_e1a(rows,expected_pairs=[(r.query_id,r.mode) for r in rows],expected_audit_sha256={(r.query_id,r.mode):digest(r) for r in rows})
        self.assertEqual(metrics['overall']['over_refusal_rate']['value'],1)
        self.assertEqual(metrics['overall']['evidence_permission_rate']['value'],0)
        self.assertEqual(metrics['overall']['coverage_unknown_over_refusals'],2)

    def test_real_kind_cannot_be_enabled_by_synthetic_permit_or_local_release_hash(self):
        requests,release,_=eight_fixture()
        with tempfile.TemporaryDirectory() as t:
            c=freeze_requests(requests,release_sha256=digest(release),destination=Path(t)/'p')
            raw=c.model_dump(mode='json');raw['data_kind']='frozen_query'
            with self.assertRaises(ValueError):validate_config(raw,expected_sha256=digest(raw))

    def test_direct_worker_frozen_query_requires_separate_launch_grant(self):
        raw=eight_fixture()[0][0].model_dump(mode='json');raw['prediction']['data_kind']='frozen_query'
        raw['input_sha256']=digest(raw['prediction'])
        with patch('data_analysis_agent.reviews.e1a_b3_worker._predict_r0_validated',side_effect=AssertionError('no unauthorized evaluation')):
            with self.assertRaisesRegex(ValueError,'b3_worker_execution_grant_required'):
                execute_request(raw,expected_request_sha256=digest(raw))


@unittest.skipUnless(os.name=='nt' and os.environ.get('E1A_RUN_OS_TEST')=='1',
                     'actual AppContainer probe runs separately with explicit synthetic test switch')
class WindowsIsolationTests(unittest.TestCase):
    def test_actual_appcontainer_cannot_read_synthetic_reference(self):
        # A failed launch is a real failed test, not a pass or a skipped permission check.
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);ref=root/'reference';ref.mkdir();sentinel=ref/'answer.json'
            sentinel.write_text('{"SYNTHETIC_SECRET":"NEVER SEND TO PREDICTOR"}',encoding='utf-8')
            exe=stage_python(root)
            runtime=root/'runtime';runtime.mkdir();request=runtime/'request.json'
            request.write_text('{"SYNTHETIC_INPUT":true}',encoding='utf-8')
            with AppContainer(root) as box:
                box.grant(exe.parent);box.grant(runtime)
                proof=permission_probe(box,exe,protected_paths=[ref,sentinel],readable_paths=[request])
            print('ACTUAL_WINDOWS_PROOF',json.dumps(proof))
            self.assertTrue(proof['passed'],proof)


if __name__=='__main__':unittest.main()
