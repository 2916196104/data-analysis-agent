"""B1 uses entirely synthetic sources, decisions and approval records."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.e1_preparation import load
from data_analysis_agent.reviews.e1_review_protocol import review_context_view
from data_analysis_agent.reviews.exploratory_e1 import ContractDraft, make_review_draft
from data_analysis_agent.reviews.evidence_judge import FactEvidence, judge_evidence
from data_analysis_agent.reviews.query_contract import validate_query_contract
from data_analysis_agent.reviews.human_adjudication import new_session,metadata,case_payload,pair_payload,completion_payload
from data_analysis_agent.reviews.e1a_protocol import OperationAuthorization,PredictionInput,PredictionOutput,digest
from data_analysis_agent.reviews.e1a_reference_release import build_reference_release,publish_reference_release
from data_analysis_agent.reviews.e1a_prediction import build_prediction_input,run_r0
from data_analysis_agent.reviews.e1a_output_audit import audit_output
from data_analysis_agent.reviews.e1a_evaluation import evaluate_e1a
from test_exploratory_e1 import inputs,prepare
from test_evidence_judge import contract
from test_human_adjudication import event


def authorization(action,scope,artifact,kind='synthetic'):
    return OperationAuthorization(action=action,data_kind=kind,scope_ref=scope,
        artifact_sha256=artifact,approval_ref='SYNTHETIC-OPERATOR-AUTHORIZATION')


def fixture(quotes=('客服回复及时',),descriptions=('客服回复及时',),ref_direct=None,ref_state=None,
            ref_relations='supported',independent=(),**updates):
    run,sources,scope=inputs(quotes);question='[合成] 请核对指定目标与关系。'
    raw=contract(descriptions,**updates);raw['scope_ref']=scope.model_dump(mode='json')
    c=validate_query_contract(raw,expected_scope=scope);ind=list(independent)
    fids=[f.id for f in c.required_facts.items];direct=set(fids if ref_direct is None else ref_direct)
    state=ref_state or ('sufficient' if direct else 'insufficient')
    payload=dict(contract=c.model_dump(mode='json'),independent_target_fact_ids=ind)
    qid=run['manifest']['queries'][0]['id'];prepared={};cases=[]
    for mode in ('unfiltered','filtered'):
        p=prepare(run,sources,scope,mode);prepared[mode]=p
        d=ContractDraft(query_id=qid,query_sha256=content_sha256(question),contract=c,independent_target_fact_ids=tuple(ind))
        base=make_review_draft(p,d,question=question,expected_scope=scope)
        findings=[]
        for f in c.required_facts.items:
            refs=[]
            if f.id in direct:
                h=next(h for h in p.candidate_batch.candidates if f.description in h.quote)
                start=h.quote_start+h.quote.index(f.description)
                refs=[dict(key=h.key,quote_start=start,quote_end=start+len(f.description))]
            findings.append(dict(fact_id=f.id,support='direct' if f.id in direct else 'not_supported',
                evidence_refs=refs,rationale='合成独立人工理由 '+f.id))
        rels=[dict(relation_id=r['relation_id'],support=ref_relations,evidence_keys=list(dict.fromkeys(
            w['key'] for f in findings for w in f['evidence_refs'])) if ref_relations=='supported' else [],rationale='合成人工关系说明')
            for r in base['relation_findings']]
        missing=[] if c.required_facts.operator=='any_of' and state=='sufficient' else sorted(set(fids)-direct)
        original=dict(reference_state=state,supported_target_fact_ids=sorted(direct) if state=='sufficient' else sorted(direct&set(ind)) if state=='limited' else [],
            missing_required_fact_ids=missing,fact_findings=findings,relation_findings=rels,review_notes='合成人工参考')
        batch=p.candidate_batch.model_dump(mode='json');cid='SYNTH-'+mode
        cases.append(dict(case_id=cid,query_id=qid,question=question,mode=mode,candidates=batch['candidates'],batch=batch,
            batch_sha256=p.batch_sha256,context_input_sha256=p.context_input_sha256,original=original,
            base_reference={k:v for k,v in base.items() if k not in ('version','origin')},issues=[]))
    b=dict(version='e1-human-adjudication-bundle-v1',data_kind='synthetic',history_status='history_unproven',
        context_policy='quote_full_review_veto',context_version='1',scope_ref=scope.model_dump(mode='json'),
        reviewer_id='SYNTHETIC-HUMAN',observed_sources_sha256=digest(sources),
        queries=[dict(query_id=qid,before=payload,proposed=deepcopy(payload))],cases=cases,
        pairs=[dict(query_id=qid,case_ids=[case['case_id'] for case in cases],required_decision=True)])
    s=new_session(b);s.update(prior_relevance_exposure='seen',context_ack=True)
    q=s['queries'][qid];q.update(revision=1,choice='original',rationale='合成确认原目标')
    q['event_id']=event(s,'contract',qid,{k:q[k] for k in ('payload','revision','choice','rationale')})
    for cid,row in s['cases'].items():
        row['contract_revision']=1;row['review']['decision_rationale']='合成已完成人工复核'
        row['event_id']=event(s,'case',cid,case_payload(s,cid))
    s['pairs'][qid]['rationale']='合成统一';s['pairs'][qid]['event_id']=event(s,'pair',qid,pair_payload(b,s,qid))
    s['completion_event_id']=event(s,'completion','session',completion_payload(s))
    auth=authorization('publish_reference',scope,digest(s))
    release_args=dict(expected_bundle_sha256=digest(b),expected_session_sha256=digest(s),authorization=auth,
        expected_authorization_sha256=digest(auth),release_id='SYNTHETIC-RELEASE-1',published_at='2031-02-01T00:00:00Z')
    release=build_reference_release(b,s,**release_args)
    data={}
    for mode,p in prepared.items():
        data[mode]=build_prediction_input(question=question,contract=c,contract_revision=1,independent_target_fact_ids=ind,
            prepared=p,experiment_id='SYNTHETIC-E1A',data_kind='synthetic',expected_scope=scope,
            expected_question_sha256=content_sha256(question),expected_contract_sha256=digest(c),expected_batch_sha256=p.batch_sha256)
    return dict(bundle=b,session=s,release=release,release_args=release_args,sources=sources,scope=scope,inputs=data)


def predict(f,mode='unfiltered',value=None):
    p=value or f['inputs'][mode];auth=authorization('predict_r0',f['scope'],digest(p))
    return run_r0(p,f['sources'],expected_scope=f['scope'],expected_input_sha256=digest(p),expected_sources_sha256=digest(f['sources']),
        authorization=auth,expected_authorization_sha256=digest(auth))


def audit(f,out,mode='unfiltered',p=None):
    p=p or f['inputs'][mode]
    return audit_output(p,out,f['release'],f['sources'],expected_scope=f['scope'],expected_input_sha256=digest(p),
        expected_sources_sha256=digest(f['sources']),expected_release_sha256=digest(f['release']))


def evaluate(rows,extra=()):
    return evaluate_e1a(rows,expected_pairs=[(r.query_id,r.mode) for r in rows]+list(extra),
        expected_audit_sha256={(r.query_id,r.mode):digest(r) for r in rows})


class ReferenceAndIsolationTests(unittest.TestCase):
    def test_release_is_derived_from_events_without_mutating_human_input(self):
        f=fixture();b,s=f['bundle'],f['session'];before=deepcopy((b,s));release=build_reference_release(b,s,**f['release_args'])
        self.assertEqual((b,s),before);self.assertEqual(len(release.references),2)
        for row in release.references:
            self.assertTrue(row.reference.human_confirmed);self.assertEqual(len(row.reference.reviewer_decisions),1)
            self.assertIsNone(row.reference.adjudication);self.assertEqual(row.reference.reference_state,'sufficient')
        self.assertEqual(release.history_status,'history_unproven');self.assertFalse(release.independent_seal)

    def test_no_completion_tampered_event_and_wrong_approval_are_rejected(self):
        for change in ('completion','event','approval'):
            f=fixture();s=deepcopy(f['session']);args=f['release_args'].copy()
            if change=='completion':s['completion_event_id']=None
            elif change=='event':s['events'][1]['payload_sha256']='0'*64
            else:args['authorization']=authorization('predict_r0',f['scope'],digest(s))
            with self.subTest(change=change),self.assertRaises(ValueError):build_reference_release(f['bundle'],s,**args)

    def test_even_rebound_input_pin_cannot_replace_actual_completion_or_event_chain(self):
        for change in ('completion','event'):
            f=fixture();s=deepcopy(f['session']);args=f['release_args'].copy()
            if change=='completion':s['completion_event_id']=None
            else:s['events'][1]['payload']['review']['decision_rationale']='合成事件被篡改'
            args['expected_session_sha256']=digest(s);args['authorization']=authorization('publish_reference',f['scope'],digest(s))
            args['expected_authorization_sha256']=digest(args['authorization'])
            with self.subTest(change=change),self.assertRaises(ValueError):build_reference_release(f['bundle'],s,**args)

    def test_release_flags_and_scope_cannot_be_upgraded(self):
        f=fixture();raw=f['release'].model_dump(mode='json');raw['independent_seal']=True
        from data_analysis_agent.reviews.e1a_reference_release import ReferenceRelease
        with self.assertRaises(ValueError):load(raw,ReferenceRelease)
        with self.assertRaises(ValueError):audit_output(f['inputs']['unfiltered'],predict(f),f['release'],f['sources'],
            expected_scope=f['scope'],expected_input_sha256=digest(f['inputs']['unfiltered']),
            expected_sources_sha256=digest(f['sources']),expected_release_sha256='0'*64)

    def test_question_and_independent_target_injection_fail_reference_binding(self):
        for change in ('question','independent'):
            f=fixture();raw=f['inputs']['unfiltered'].model_dump(mode='json')
            if change=='question':raw['question']='合成被替换的问题';raw['question_sha256']=content_sha256(raw['question'])
            else:raw['independent_target_fact_ids']=['f0']
            p=load(raw,PredictionInput);out=predict(f,value=p)
            with self.subTest(change=change),self.assertRaises(ValueError):audit(f,out,p=p)

    def test_publish_is_exclusive_and_failure_leaves_no_ready_directory(self):
        f=fixture()
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/'release'
            with patch('pathlib.Path.write_text',side_effect=OSError('synthetic write failure')):
                with self.assertRaises(OSError):publish_reference_release(f['bundle'],f['session'],destination=dest,**f['release_args'])
            self.assertFalse(dest.exists());self.assertEqual(list(Path(root).iterdir()),[])
            publish_reference_release(f['bundle'],f['session'],destination=dest,**f['release_args'])
            before=(dest/'reference-release.json').read_bytes()
            with self.assertRaises(ValueError):publish_reference_release(f['bundle'],f['session'],destination=dest,**f['release_args'])
            self.assertEqual((dest/'reference-release.json').read_bytes(),before)

    def test_prediction_schema_rejects_reference_fields_at_every_layer(self):
        f=fixture();p=f['inputs']['unfiltered'];raw=p.model_dump(mode='json')
        for where in (raw,raw['prepared'],raw['prepared']['candidate_batch'],raw['prepared']['candidate_batch']['candidates'][0]):
            poisoned=deepcopy(raw);target=poisoned
            if where is raw['prepared']:target=poisoned['prepared']
            elif where is raw['prepared']['candidate_batch']:target=poisoned['prepared']['candidate_batch']
            elif where is raw['prepared']['candidate_batch']['candidates'][0]:target=poisoned['prepared']['candidate_batch']['candidates'][0]
            target['reference_state']='sufficient'
            with self.assertRaises(ValueError):load(poisoned,PredictionInput)
        text=p.model_dump_json()
        for forbidden in ('fact_findings','reviewer_decisions','decision_rationale','cosine_distance','review_notes'):
            self.assertNotIn(forbidden,text)
        self.assertNotIn('SYNTHETIC-HUMAN',text)

    def test_predictor_has_no_reference_argument_and_no_filesystem_network_or_strict_calls(self):
        f=fixture()
        with patch('builtins.open',side_effect=AssertionError('no file access')),patch('socket.socket.connect',side_effect=AssertionError('no network')),\
             patch('data_analysis_agent.reviews.e1_preparation.prepare_trusted_batch',side_effect=AssertionError('no strict prepare')),\
             patch('data_analysis_agent.reviews.evidence_judge.judge_evidence',side_effect=AssertionError('no strict judge')):
            self.assertEqual(predict(f).status,'sufficient')
        with self.assertRaises(TypeError):run_r0(reference=f['release'])


class R0AndAuditTests(unittest.TestCase):
    def test_any_of_and_limited_preserve_independent_fact_boundaries(self):
        descriptions=('合成规则不认识的目标','客服回复及时')
        facts=dict(operator='any_of',items=[dict(id=f'f{i}',description=d,sentiment='positive') for i,d in enumerate(descriptions)],relations=[])
        f=fixture(descriptions=descriptions,ref_direct=('f1',),required_facts=facts)
        out=predict(f);self.assertEqual(out.status,'sufficient');self.assertEqual(out.supported_target_fact_ids,('f1',))
        self.assertEqual(out.missing_required_fact_ids,());self.assertEqual(audit(f,out).execution,'completed')
        f=fixture(descriptions=descriptions,ref_direct=('f1',),required_facts=facts,uncertainty=['合成未确定条件'])
        out=predict(f);self.assertEqual(out.status,'insufficient');self.assertEqual(audit(f,out).execution,'completed')
        facts['operator']='all_of'
        f=fixture(descriptions=descriptions,ref_direct=('f1',),ref_state='limited',independent=('f1',),required_facts=facts)
        out=predict(f);self.assertEqual(out.status,'limited');self.assertEqual(out.missing_required_fact_ids,('f0',))
        a=audit(f,out);self.assertEqual(a.execution,'completed');self.assertTrue(a.fact_support[0].supported)

    def test_before_and_mixed_contrast_use_joint_same_review_witnesses(self):
        examples=[(('设备已到货','安装人员仍未上门'),'设备已到货，然后安装人员仍未上门',('neutral','negative'),'negative','before'),
                  (('设备制冷良好','设备噪声很大'),'设备制冷良好，但是设备噪声很大',('positive','negative'),'mixed','contrast')]
        for ds,quote,sentiments,intent,relation in examples:
            facts=dict(operator='all_of',items=[dict(id=f'f{i}',description=d,sentiment=sentiments[i]) for i,d in enumerate(ds)],
                relations=[dict(kind='same_review',fact_ids=['f0','f1'])]+([dict(kind='before',fact_ids=['f0','f1'])] if relation=='before' else []))
            polarity=dict(negations=[],contrast_fact_ids=['f0','f1'] if relation=='contrast' else [])
            f=fixture((quote,),descriptions=ds,target_entity='multiple',subtopic='multiple',sentiment_intent=intent,
                      required_facts=facts,polarity_structure=polarity)
            out=predict(f);a=audit(f,out)
            with self.subTest(relation=relation):
                self.assertEqual(out.status,'sufficient');self.assertEqual(a.execution,'completed');self.assertEqual(a.relation_correct,2)
                self.assertTrue(all(r.supported for r in a.fact_support))

    def test_negation_scope_and_entity_mismatch_stay_unknown(self):
        f=fixture(('客服回复不及时',),descriptions=('客服回复不及时',),sentiment_intent='negative',
                  polarity_structure=dict(negations=[dict(fact_id='f0',cue='不',scope='及时')],contrast_fact_ids=[]))
        self.assertEqual(predict(f).status,'sufficient')
        p=f['inputs']['unfiltered'].model_dump(mode='json');p['contract']['target_entity']='installer';p['contract_sha256']=content_sha256(p['contract'])
        p=load(p,PredictionInput);out=predict(f,value=p)
        self.assertEqual(out.status,'insufficient');self.assertEqual(out.fact_findings[0].support,'unknown')

    def test_wrong_authorization_kind_and_input_digest_cannot_execute(self):
        f=fixture();p=f['inputs']['unfiltered'];auth=authorization('predict_r0',f['scope'],digest(p),kind='frozen_query')
        with self.assertRaises(ValueError):run_r0(p,f['sources'],expected_scope=f['scope'],expected_input_sha256=digest(p),
            expected_sources_sha256=digest(f['sources']),authorization=auth,expected_authorization_sha256=digest(auth))
        auth=authorization('predict_r0',f['scope'],digest(p))
        with self.assertRaises(ValueError):run_r0(p,f['sources'],expected_scope=f['scope'],expected_input_sha256='0'*64,
            expected_sources_sha256=digest(f['sources']),authorization=auth,expected_authorization_sha256=digest(auth))

    def test_r0_preserves_old_rule_result_and_exact_references(self):
        f=fixture();p=f['inputs']['unfiltered'];out=predict(f)
        old=judge_evidence(p.contract,p.prepared.candidate_batch,expected_scope=f['scope'],trusted_batch=p.prepared.candidate_batch)
        self.assertEqual(out.raw_r0,old);self.assertEqual(out.status,old.status);self.assertEqual(out.evidence_refs,old.evidence_refs)
        a=audit(f,out);self.assertEqual(a.execution,'completed');self.assertTrue(a.fact_support[0].supported)

    def test_empty_candidates_keep_no_absence_claim_and_zero_denominator(self):
        f=fixture((),ref_direct=());out=predict(f);a=audit(f,out);metrics=evaluate([a])
        self.assertEqual(out.status,'insufficient');self.assertEqual(out.evidence_refs,())
        self.assertIn('no_candidates_not_proof_of_absence',out.reason_codes)
        self.assertEqual(a.execution,'completed');self.assertIsNone(metrics['overall']['citation_validity_rate']['value'])

    def test_unknown_statement_is_not_promoted_to_not_supported_and_is_over_refusal(self):
        f=fixture(('合成但不在受控语法中的陈述',),descriptions=('合成但不在受控语法中的陈述',))
        out=predict(f);self.assertEqual(out.fact_findings[0].support,'unknown')
        a=audit(f,out);m=evaluate([a])['overall'];self.assertEqual(a.execution,'completed')
        self.assertEqual(m['over_refusal_rate']['value'],1);self.assertEqual(m['coverage_unknown_lists'],1)

    def test_rule_query_veto_is_retained_and_reported_as_normal_refusal(self):
        f=fixture(uncertainty=['合成查询条件尚不明确'])
        out=predict(f);self.assertEqual(out.status,'insufficient');self.assertEqual(out.fact_findings[0].support,'direct')
        a=audit(f,out);self.assertEqual(a.execution,'completed');self.assertEqual(evaluate([a])['overall']['over_refusal_rate']['value'],1)

    def test_execution_failure_and_missing_outputs_are_not_insufficient(self):
        f=fixture()
        with patch('data_analysis_agent.reviews.e1a_prediction._judge',side_effect=RuntimeError('synthetic engine failure')):out=predict(f)
        self.assertIsNone(out.status);a=audit(f,out);self.assertEqual(a.execution,'execution_failed')
        b=audit(f,None,mode='filtered');m=evaluate([a,b])['overall']
        self.assertEqual(m['expected'],2);self.assertEqual(m['valid_comparisons'],0)
        self.assertEqual((m['execution_failures'],m['missing_outputs']),(1,1));self.assertIsNone(m['over_refusal_rate']['value'])
        self.assertEqual(m['answerable_unserved_rate']['value'],1)

    def test_source_conflict_fails_before_rule_engine(self):
        f=fixture();f['sources']['records'][0]['quote']='合成篡改'
        with patch('data_analysis_agent.reviews.e1a_prediction._judge',side_effect=AssertionError('must not run')):
            out=predict(f)
        self.assertEqual(out.execution_status,'validation_failed');self.assertIsNone(out.status)

    def test_rebound_source_pin_still_compares_independent_records_to_candidates(self):
        f=fixture();f['sources']['records'][0]['quote']='合成不同来源'
        raw=f['inputs']['unfiltered'].model_dump(mode='json');raw['prepared']['observed_sources_sha256']=digest(f['sources'])
        p=load(raw,PredictionInput)
        with patch('data_analysis_agent.reviews.e1a_prediction._judge',side_effect=AssertionError('must not execute')):
            out=predict(f,value=p)
        self.assertEqual(out.execution_status,'validation_failed')

    def test_quote_context_span_borrowing_and_unknown_ids_are_rejected(self):
        f=fixture(('客服回复及时。',));raw=predict(f).model_dump(mode='json')
        for w in [raw['fact_findings'][0]['evidence_refs'][0],raw['evidence_refs'][0]]:w['quote_start']=-1
        self.assertEqual(audit(f,raw).execution,'validation_failed')
        raw=predict(f).model_dump(mode='json');raw['supported_target_fact_ids']=['f9']
        self.assertEqual(audit(f,raw).execution,'validation_failed')

    def test_no_reference_license_and_bad_spans_cannot_pass_audit(self):
        for mutation in ('no_refs','outside','foreign_mode','wrong_fact'):
            f=fixture();raw=predict(f).model_dump(mode='json')
            if mutation=='no_refs':raw['fact_findings'][0]['evidence_refs']=[];raw['evidence_refs']=[]
            else:
                for w in [raw['fact_findings'][0]['evidence_refs'][0],raw['evidence_refs'][0]]:
                    w.update({'quote_end':9999} if mutation=='outside' else {'mode':'filtered'} if mutation=='foreign_mode' else {'fact_id':'f9'})
            a=audit(f,raw)
            with self.subTest(mutation=mutation):self.assertEqual(a.execution,'validation_failed');self.assertTrue(a.errors)

    def test_auditor_never_reruns_predictor_and_new_legal_span_remains_unassessed(self):
        f=fixture();raw=predict(f).model_dump(mode='json')
        for w in [raw['fact_findings'][0]['evidence_refs'][0],raw['evidence_refs'][0]]:w['quote_start']+=1
        with patch('data_analysis_agent.reviews.e1a_prediction._judge',side_effect=AssertionError('no prediction during audit')):
            a=audit(f,raw)
        self.assertEqual(a.execution,'completed');self.assertIsNone(a.fact_support[0].supported)
        self.assertIsNone(evaluate([a])['overall']['licensed_fact_support_rate']['value'])

    def test_cross_review_same_review_relation_is_rejected(self):
        ds=('设备制冷良好','设备制热良好')
        facts=dict(operator='all_of',items=[dict(id=f'f{i}',description=d,sentiment='positive') for i,d in enumerate(ds)],relations=[dict(kind='same_review',fact_ids=['f0','f1'])])
        f=fixture(ds,descriptions=ds,target_entity='device',subtopic='multiple',required_facts=facts,ref_relations='not_supported',ref_state='insufficient')
        out=predict(f);self.assertEqual(out.status,'insufficient');self.assertEqual(out.relation_findings[0].support,'unknown')
        raw=out.model_dump(mode='json');raw['relation_findings'][0].update(support='supported',evidence_keys=[h.key for h in f['inputs']['unfiltered'].prepared.candidate_batch.candidates])
        a=audit(f,raw);self.assertIn('cross_review_relation',a.errors)


class MetricsTests(unittest.TestCase):
    def test_nonzero_paired_difference_and_invalid_citations_remain_counted(self):
        f=fixture();u=audit(f,predict(f));raw=predict(f,'filtered').model_dump(mode='json')
        raw.update(status='insufficient',supported_target_fact_ids=[],evidence_refs=[],missing_required_fact_ids=['f0'])
        raw['fact_findings'][0].update(support='unknown',evidence_refs=[],rationale='合成未识别')
        v=audit(f,raw,'filtered');result=evaluate([u,v])
        self.assertEqual(result['paired']['over_refusal_rate']['filtered_minus_unfiltered'],1)
        self.assertEqual(result['paired']['state_agreement']['filtered_minus_unfiltered'],-1)
        bad=predict(f).model_dump(mode='json');bad['execution_status']='execution_failed'
        a=audit(f,bad);self.assertEqual(a.execution,'validation_failed');self.assertEqual(a.citation_count,1)
        bad['version']='invalid';b=audit(f,bad,'filtered')
        self.assertTrue(b.unparseable_output)
        self.assertIsNone(evaluate([a,b])['overall']['citation_validity_rate']['value'])

    def test_all_refusal_exposes_answerable_coverage_and_pairs_not_improvement(self):
        f=fixture(('合成未知句式',),descriptions=('合成未知句式',))
        rows=[audit(f,predict(f,m),m) for m in ('unfiltered','filtered')];result=evaluate(rows);m=result['overall']
        self.assertEqual(m['over_refusal_rate']['value'],1);self.assertEqual(m['evidence_permission_rate']['value'],0)
        self.assertEqual(m['state_agreement']['value'],0);self.assertEqual(m['answerable_unserved_rate']['value'],1)
        self.assertEqual(m['coverage_unknown_over_refusals'],2);self.assertEqual(m['state_agree_but_coverage_unknown'],0)
        self.assertEqual(result['paired']['over_refusal_rate']['paired_query_denominator'],1)

    def test_missing_audit_is_retained_and_paired_metric_is_na(self):
        f=fixture();a=audit(f,predict(f));m=evaluate([a],extra=[(a.query_id,'filtered')])
        self.assertEqual(len(m['case_ledger']),2);self.assertEqual(m['overall']['missing_audits'],1)
        self.assertIsNone(m['paired']['state_agreement']['filtered_minus_unfiltered'])

    def test_pin_tampering_and_duplicate_rows_are_rejected(self):
        f=fixture();a=audit(f,predict(f));key=(a.query_id,a.mode)
        with self.assertRaises(ValueError):evaluate_e1a([a],expected_pairs=[key],expected_audit_sha256={key:'0'*64})
        with self.assertRaises(ValueError):evaluate([a,a])

    def test_semantically_wrong_permission_counts_on_unsupported_human_list(self):
        f=fixture(ref_direct=());a=audit(f,predict(f));m=evaluate([a])['overall']
        self.assertEqual(a.execution,'completed');self.assertFalse(a.fact_support[0].supported)
        self.assertEqual(m['unsupported_permission_rate']['value'],1);self.assertEqual(m['licensed_fact_support_rate']['value'],0)


if __name__=='__main__':unittest.main()
