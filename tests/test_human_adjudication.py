"""Only synthetic queries, reviews and simulated UI actions. No real gold labels."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.e1_review_protocol import review_context_view
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
from data_analysis_agent.reviews.human_adjudication import (
    new_session, metadata, case_payload, pair_payload, completion_payload, validate_session, review_errors,
)
from test_e1_readiness import review_fixture

ROOT=Path(__file__).resolve().parents[1]


def bundle_fixture(quotes=('[合成] 客服回复及时',)):
    reference,contract,prepared,_=review_fixture(quotes=quotes)
    c=contract.model_dump(mode='json');before=dict(contract=c,independent_target_fact_ids=[])
    proposed=deepcopy(before);proposed['contract']['required_facts']['items'][0]['description']+='且满足合成使用条件'
    cases=[]
    for mode in ('unfiltered','filtered'):
        batch=prepared.trusted_batch.model_dump(mode='json');batch['mode']=mode
        for h in batch['candidates']:h['mode']=mode
        batch_model=EvidenceBatch.model_validate_json(json.dumps(batch));cid='SYNTH-'+mode
        h=batch['candidates'][0]
        original=dict(reference_state='sufficient',supported_target_fact_ids=['f0'],missing_required_fact_ids=[],
            fact_findings=[dict(fact_id='f0',support='direct',evidence_refs=[dict(key=h['key'],quote_start=h['quote_start'],quote_end=h['quote_end'])],rationale='合成事实直接支持')],
            relation_findings=[],review_notes='合成原评审')
        base=deepcopy(reference);base.update(mode=mode,batch_sha256=content_sha256(batch),context_input_sha256=content_sha256(review_context_view(batch_model,'quote_full_review_veto')))
        cases.append(dict(case_id=cid,query_id=batch['query_id'],question='[合成] 客服回复及时吗？',mode=mode,severity='P1',
            candidates=batch['candidates'],batch=batch,batch_sha256=base['batch_sha256'],context_input_sha256=base['context_input_sha256'],
            original=original,base_reference={k:v for k,v in base.items() if k not in ('version','origin')},issues=[],field_proposals=[],original_contract_notes='合成备注'))
    qid=cases[0]['query_id']
    return dict(version='e1-human-adjudication-bundle-v1',data_kind='synthetic',experiment_kind='exploratory_current_observation_history_unproven',
        history_status='history_unproven',context_policy='quote_full_review_veto',context_version='1',scope_ref=c['scope_ref'],reviewer_id='SYNTHETIC-REVIEWER',
        queries=[dict(query_id=qid,question=cases[0]['question'],before=before,proposed=proposed,case_ids=[c['case_id'] for c in cases],interpretation_notes=['合成建议'],alternatives=[],diff=[])],
        cases=cases,pairs=[dict(query_id=qid,case_ids=[c['case_id'] for c in cases],required_decision=True,identical_inputs=True)])


def event(s,kind,subject,payload):
    n=len(s['events'])+1
    e=dict(event_id=f'E{n:04d}',previous_event_sha256=content_sha256(s['events'][-1]) if s['events'] else None,
        bundle_sha256=s['bundle_sha256'],metadata=metadata(s),metadata_sha256=content_sha256(metadata(s)),origin='human_ui_action',kind=kind,
        subject_id=subject,reviewer_id=s['reviewer_id'],recorded_at=f'2031-01-01T00:00:{n:02d}.000Z',payload=deepcopy(payload),payload_sha256=content_sha256(payload))
    s['events'].append(e);return e['event_id']


def completed_fixture():
    b=bundle_fixture();s=new_session(b);s.update(prior_relevance_exposure='unsure',context_ack=True)
    qid=b['queries'][0]['query_id'];q=s['queries'][qid]
    q.update(revision=1,choice='original',rationale='合成确认原契约')
    q['event_id']=event(s,'contract',qid,{k:q[k] for k in ('payload','revision','choice','rationale')})
    for cid,row in s['cases'].items():
        row['contract_revision']=1;row['review']['decision_rationale']='合成本人复核理由'
        row['event_id']=event(s,'case',cid,case_payload(s,cid))
    s['pairs'][qid]['rationale']='合成统一配对结论';s['pairs'][qid]['event_id']=event(s,'pair',qid,pair_payload(b,s,qid))
    s['completion_event_id']=event(s,'completion','session',completion_payload(s))
    return b,s


def node_run(bundle,body):
    script="const fs=require('node:fs');globalThis.crypto=require('node:crypto').webcrypto;const A=require('./scripts/templates/e1_adjudication.js');const b=JSON.parse(fs.readFileSync(0,'utf8'));(async()=>{let s=A.init(b,await A.digest(b));"+body+"})().catch(e=>{console.error(e);process.exitCode=1});"
    r=subprocess.run(['node','-e',script],cwd=ROOT,input=json.dumps(bundle,ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,timeout=20)
    if r.returncode:raise AssertionError(r.stdout+r.stderr)
    return json.loads(r.stdout)


class HumanAdjudicationTests(unittest.TestCase):
    def test_initial_state_never_confirms_existing_human_choices(self):
        b=bundle_fixture();s=new_session(b);r=validate_session(b,s)
        self.assertFalse(r['ready_for_formal_reference_freeze']);self.assertEqual(r['counts']['cases_reviewed'],0)
        self.assertIsNone(r['pending_references']);self.assertEqual(s['events'],[])

    def test_complete_synthetic_review_prepares_only_unconfirmed_references(self):
        b,s=completed_fixture();r=validate_session(b,s)
        self.assertTrue(r['ready_for_formal_reference_freeze']);self.assertEqual(len(r['pending_references']),2)
        for p in r['pending_references']:
            self.assertFalse(p['prepared_fields']['human_confirmed']);self.assertFalse(p['prepared_fields']['contract_confirmed'])
            self.assertIsNone(p['formal_confirmation_record']);self.assertFalse(p['usable_for_evaluation'])
            self.assertEqual(p['contract_confirmation_record']['kind'],'contract');self.assertEqual(p['input_bundle_sha256'],content_sha256(b))
            self.assertEqual(p['prior_relevance_exposure'],'unsure');self.assertEqual(p['reviewer_count'],1)

    def test_switching_flags_or_inventing_event_ids_does_not_complete_review(self):
        b=bundle_fixture();s=new_session(b);s.update(prior_relevance_exposure='seen',context_ack=True,completion_event_id='E9999')
        for q in s['queries'].values():q.update(event_id='E9999',revision=1,choice='original',rationale='合成')
        for c in s['cases'].values():c.update(event_id='E9999',contract_revision=1);c['review']['decision_rationale']='合成'
        self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_edit_after_confirmation_invalidates_record(self):
        b,s=completed_fixture();next(iter(s['cases'].values()))['review']['fact_findings'][0]['rationale']='合成更改'
        self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_source_scope_and_filter_changes_are_rejected(self):
        for mutation in (lambda q:q['payload']['contract']['scope_ref'].update(run_id='0'*64),lambda q:q['payload']['contract']['proposed_filters'].update(aspect='service')):
            b,s=completed_fixture();mutation(next(iter(s['queries'].values())))
            self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_foreign_source_and_outside_quote_span_are_rejected(self):
        for change in (dict(key='outside-list'),dict(quote_start=9999)):
            b,s=completed_fixture();next(iter(s['cases'].values()))['review']['fact_findings'][0]['evidence_refs'][0].update(change)
            self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_insufficient_cannot_keep_permission(self):
        b,s=completed_fixture();next(iter(s['cases'].values()))['review']['reference_state']='insufficient'
        r=validate_session(b,s);self.assertFalse(r['ready_for_formal_reference_freeze'])
        self.assertTrue(any('insufficient' in x for xs in r['case_errors'].values() for x in xs))

    def test_undeclared_exposure_or_boundary_blocks_finalization(self):
        for changes in (dict(prior_relevance_exposure=None),dict(context_ack=False),dict(reviewer_id=''),dict(reviewer_id=None)):
            b,s=completed_fixture();s.update(changes)
            self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_forged_old_pair_id_cannot_bypass_pair_payload_binding(self):
        b,s=completed_fixture();qid=b['queries'][0]['query_id'];s['pairs'][qid]['rationale']='更改后的合成理由'
        self.assertFalse(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_all_of_any_of_and_independent_limited_boundaries(self):
        b=bundle_fixture();case=b['cases'][0];c=deepcopy(b['queries'][0]['before']['contract']);r=deepcopy(case['original'])
        r.update(resolved_issue_ids=[],decision_rationale='合成理由');c['required_facts']['items'].append(dict(id='f1',description='第二合成条件',sentiment='positive'))
        r['fact_findings'].append(dict(fact_id='f1',support='not_supported',evidence_refs=[],rationale='第二条件无证据'));r['reference_state']='limited';r['missing_required_fact_ids']=['f1']
        self.assertFalse(review_errors(r,c,['f0'],case));self.assertTrue(review_errors(r,c,[],case))
        c['required_facts']['operator']='any_of';r['reference_state']='sufficient';r['missing_required_fact_ids']=[]
        self.assertFalse(review_errors(r,c,[],case))
        c['required_facts']['operator']='all_of';c['required_facts']['relations']=[dict(kind='same_review',fact_ids=['f0','f1'])]
        r.update(reference_state='limited',missing_required_fact_ids=['f1'],relation_findings=[dict(relation_id='relation_0',support='not_supported',evidence_keys=[],rationale='必要关系没有支持')])
        self.assertTrue(any('limited' in e for e in review_errors(r,c,['f0'],case)))

    def test_metadata_change_invalidates_all_previous_javascript_records(self):
        b=bundle_fixture();s=node_run(b,"for(const q of Object.values(s.queries))q.event_id='old';for(const c of Object.values(s.cases))c.event_id='old';s.completion_event_id='old';A.invalidateMetadata(s);console.log(JSON.stringify(s));")
        self.assertTrue(all(q['event_id'] is None for q in s['queries'].values()));self.assertTrue(all(c['event_id'] is None for c in s['cases'].values()))

    def test_javascript_rejects_flags_without_matching_actual_events(self):
        b=bundle_fixture();r=node_run(b,"s.context_ack=true;s.prior_relevance_exposure='seen';for(const q of Object.values(s.queries)){q.event_id='invented';q.revision=1;}for(const c of Object.values(s.cases)){c.event_id='invented';c.contract_revision=1;c.review.decision_rationale='合成';}s.completion_event_id='invented';console.log(JSON.stringify(A.progress(b,s)));")
        self.assertFalse(r['ready_for_formal_reference_freeze']);self.assertEqual(r['counts']['cases_reviewed'],0)

    def test_complete_cli_generates_pending_material_without_freezing(self):
        b,s=completed_fixture()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'bundle.json').write_text(json.dumps(b),encoding='utf-8');(p/'session.json').write_text(json.dumps(s),encoding='utf-8')
            r=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-B','-m','data_analysis_agent.reviews.human_adjudication','--bundle',str(p/'bundle.json'),'--session',str(p/'session.json'),'--expected-bundle-sha256',content_sha256(b),'--output',str(p/'result')],cwd=ROOT,capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,0,r.stderr)
            refs=json.loads((p/'result/references.pending.json').read_text(encoding='utf-8'))
            self.assertTrue(all(not x['prepared_fields']['human_confirmed'] and x['formal_confirmation_record'] is None for x in refs))

    def test_event_chain_and_payload_hash_conflicts_fail_verification(self):
        for change in (dict(previous_event_sha256='0'*64),dict(payload_sha256='0'*64),dict(origin='generated_by_agent')):
            b,s=completed_fixture();s['events'][1].update(change)
            with self.assertRaises(ValueError):validate_session(b,s)

    def test_shared_contract_revision_must_match_each_case(self):
        b,s=completed_fixture();next(iter(s['queries'].values()))['revision']=2
        r=validate_session(b,s);self.assertFalse(r['ready_for_formal_reference_freeze'])
        self.assertTrue(all(any('版本' in x for x in xs) for xs in r['case_errors'].values()))

    def test_pending_audit_issue_cannot_be_ignored(self):
        b,s=completed_fixture();b['cases'][0]['issues']=[dict(issue_id='SYNTH-I',code='synthetic')]
        # Rebind only the synthetic bundle; no real source artifact is used.
        s['bundle_sha256']=content_sha256(b)
        for e in s['events']:e['bundle_sha256']=s['bundle_sha256']
        # Direct consistency check avoids manufacturing a production confirmation.
        errors=review_errors(s['cases'][b['cases'][0]['case_id']]['review'],b['queries'][0]['before']['contract'],[],b['cases'][0])
        self.assertIn('请逐项处理审计问题',errors)

    def test_javascript_and_python_completion_gate_agree(self):
        b=bundle_fixture()
        s=node_run(b,"s.prior_relevance_exposure='unsure';s.context_ack=true;const q=b.queries[0];await A.confirmContract(b,s,q.query_id,q.before,'original','合成确认');for(const c of b.cases){s.cases[c.case_id].review.decision_rationale='合成复核';await A.confirmCase(b,s,c.case_id);}s.pairs[q.query_id].rationale='合成统一';await A.confirmPair(b,s,q.query_id);await A.complete(b,s);console.log(JSON.stringify(s));")
        self.assertTrue(validate_session(b,s)['ready_for_formal_reference_freeze'])

    def test_accepting_changed_contract_resets_both_modes_without_carry_forward(self):
        b=bundle_fixture();value=node_run(b,"s.prior_relevance_exposure='unsure';s.context_ack=true;const q=b.queries[0];await A.confirmContract(b,s,q.query_id,q.proposed,'proposed','合成变更');console.log(JSON.stringify(s));")
        for row in value['cases'].values():
            self.assertIsNone(row['review']['reference_state']);self.assertEqual(row['review']['supported_target_fact_ids'],[])
            self.assertEqual(row['review']['fact_findings'][0]['support'],'unknown');self.assertEqual(row['review']['fact_findings'][0]['evidence_refs'],[])
        self.assertFalse(validate_session(b,value)['ready_for_formal_reference_freeze'])

    def test_editing_case_invalidates_pair_and_completion_in_javascript(self):
        b=bundle_fixture();r=node_run(b,"const id=b.cases[0].case_id;s.cases[id].event_id='old';s.pairs[b.queries[0].query_id].event_id='old';s.completion_event_id='old';A.invalidateCase(b,s,id);console.log(JSON.stringify(s));")
        self.assertIsNone(r['cases'][b['cases'][0]['case_id']]['event_id']);self.assertIsNone(r['pairs'][b['queries'][0]['query_id']]['event_id']);self.assertIsNone(r['completion_event_id'])

    def test_restore_rejects_other_bundle_and_modified_event(self):
        b=bundle_fixture();v=node_run(b,"const hash=await A.digest(b);s.bundle_sha256='0'.repeat(64);let rejected=false;try{await A.restore(b,s,hash)}catch{rejected=true}s.bundle_sha256=hash;s.context_ack=true;s.prior_relevance_exposure='unsure';await A.confirmContract(b,s,b.queries[0].query_id,b.queries[0].before,'original','合成确认');s.events[0].payload_sha256='0'.repeat(64);let eventRejected=false;try{await A.restore(b,s,hash)}catch{eventRejected=true}console.log(JSON.stringify({rejected,eventRejected}));")
        self.assertTrue(v['rejected']);self.assertTrue(v['eventRejected'])

    def test_incomplete_cli_emits_audit_but_no_pending_reference_file(self):
        b=bundle_fixture();s=new_session(b)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'bundle.json').write_text(json.dumps(b),encoding='utf-8');(p/'session.json').write_text(json.dumps(s),encoding='utf-8')
            r=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),'-B','-m','data_analysis_agent.reviews.human_adjudication','--bundle',str(p/'bundle.json'),'--session',str(p/'session.json'),'--expected-bundle-sha256',content_sha256(b),'--output',str(p/'result')],cwd=ROOT,capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,2,r.stderr);self.assertTrue((p/'result/consistency-audit.json').exists());self.assertFalse((p/'result/references.pending.json').exists())


if __name__=='__main__':unittest.main()
