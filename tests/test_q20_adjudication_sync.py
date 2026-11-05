"""Synthetic two-fact -> one-fact revision; no private reviews or decisions."""
from copy import deepcopy
import json
import unittest

from data_analysis_agent.core.serialization import content_sha256
from data_analysis_agent.reviews.e1_review_protocol import review_context_view
from data_analysis_agent.reviews.evidence_judge import EvidenceBatch
from data_analysis_agent.reviews.human_adjudication import validate_session
import test_adjudication_page as page_tests
from test_human_adjudication import bundle_fixture, node_run


def revision_fixture():
    b=bundle_fixture(quotes=('[合成] 测试设备未能降低温度',))
    q=b['queries'][0];q.update(query_id='Q20',question='[合成] 不是速度慢，而是无法降温，有相应证据吗？')
    c=q['before']['contract'];c.update(target_entity='device',subtopic='synthetic_negated_claim',sentiment_intent='negative')
    c['required_facts']=dict(operator='all_of',items=[dict(id='f0',description='合成设备速度慢',sentiment='negative'),dict(id='f1',description='合成设备无法降温',sentiment='negative')],relations=[dict(kind='same_review',fact_ids=['f0','f1'])])
    c['polarity_structure']=dict(negations=[dict(fact_id='f0',cue='不是',scope='合成速度')],contrast_fact_ids=['f0','f1'])
    q['proposed']=deepcopy(q['before']);new=q['proposed']['contract'];new['subtopic']='synthetic_cooling_failure'
    new['required_facts']['items']=new['required_facts']['items'][1:];new['required_facts']['relations']=[]
    new['polarity_structure']=dict(negations=[dict(fact_id='f1',cue='未能',scope='合成降温')],contrast_fact_ids=[])
    for case,cid in zip(b['cases'],('CASE-004','CASE-009')):
        case.update(case_id=cid,query_id='Q20',question=q['question']);case['batch']['query_id']='Q20'
        for h in case['candidates']:h['query_id']='Q20'
        batch=EvidenceBatch.model_validate_json(json.dumps(case['batch']))
        case['batch_sha256']=content_sha256(case['batch']);case['context_input_sha256']=content_sha256(review_context_view(batch,'quote_full_review_veto'))
        case['base_reference'].update(query_id='Q20',batch_sha256=case['batch_sha256'],context_input_sha256=case['context_input_sha256'])
        old=case['original'];old['fact_findings'].append(dict(fact_id='f1',support='not_supported',rationale='合成旧判断',evidence_refs=[]))
        old['relation_findings']=[dict(relation_id=rid,support='unknown',rationale='',evidence_keys=[]) for rid in ('relation_0','contrast')]
    q['case_ids']=[c['case_id'] for c in b['cases']];b['pairs'][0].update(query_id='Q20',case_ids=q['case_ids'])
    return b


SETUP="s.prior_relevance_exposure='seen';s.context_ack=true;const q=b.queries[0];"
CONFIRM="await A.confirmContract(b,s,q.query_id,q.proposed,'proposed','合成接受仅f1的新契约');"
FILL="""
for(const c of b.cases){const r=s.cases[c.case_id].review,h=c.candidates[0];
r.fact_findings[0]={fact_id:'f1',support:'direct',rationale:'合成人工测试：quote明确给出目标',evidence_refs:[{key:h.key,quote_start:h.quote_start,quote_end:h.quote_end}]};
r.reference_state='sufficient';r.supported_target_fact_ids=['f1'];r.missing_required_fact_ids=[];r.decision_rationale='合成逐项复核';
await A.confirmCase(b,s,c.case_id);}
"""


class QueryRevisionTests(unittest.TestCase):
    def assert_gate_parity(self,b,value):
        audit=validate_session(b,value['session']);ui=value['progress']
        for cid in value['session']['cases']:
            self.assertEqual(bool(audit['case_errors'][cid]),bool(ui['case_errors'][cid]),cid)
        self.assertEqual(audit['counts'],ui['counts'])
        return audit

    def test_selected_unconfirmed_draft_cannot_confirm_cases(self):
        b=revision_fixture();v=node_run(b,SETUP+"s.queries.Q20.draft={text:JSON.stringify(q.proposed),choice:'proposed',rationale:'合成草稿'};A.changeDraft(b,s,'Q20');let blocked=false;try{await A.confirmCase(b,s,'CASE-004')}catch{blocked=true}console.log(JSON.stringify({session:s,progress:A.progress(b,s),blocked}));")
        self.assertTrue(v['blocked']);self.assertEqual(v['session']['events'],[]);self.assert_gate_parity(b,v)

    def test_revision_removes_old_facts_and_relations_and_archives_work(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+"console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        for row in v['session']['cases'].values():
            self.assertEqual([f['fact_id'] for f in row['review']['fact_findings']],['f1']);self.assertEqual(row['review']['relation_findings'],[])
            self.assertIsNone(row['review']['reference_state']);self.assertIsNone(row['event_id'])
            self.assertEqual([f['fact_id'] for f in row['review_history'][0]['review']['fact_findings']],['f0','f1'])
            self.assertEqual(row['review_history'][0]['review_sha256'],content_sha256(row['review_history'][0]['review']))
        self.assertFalse(v['session']['queries']['Q20']['payload']['contract']['polarity_structure']['contrast_fact_ids']);self.assert_gate_parity(b,v)

    def test_both_modes_can_pass_only_after_explicit_synthetic_case_confirmation(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        audit=self.assert_gate_parity(b,v);self.assertEqual(audit['counts']['cases_reviewed'],2)
        for cid in ('CASE-004','CASE-009'):self.assertEqual(audit['case_errors'][cid],[])
        self.assertEqual([c['mode'] for c in b['cases']],['unfiltered','filtered'])

    def test_sufficient_without_direct_f1_is_rejected_in_both_validators(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"for(const row of Object.values(s.cases))row.review.fact_findings[0].support='partial';console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        audit=self.assert_gate_parity(b,v);self.assertEqual(audit['counts']['cases_reviewed'],0)
        self.assertTrue(all(any('sufficient' in x for x in es) for es in audit['case_errors'].values()))

    def test_changed_draft_invalidates_shared_confirmation_even_with_stale_event_pointer(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"s.queries.Q20.draft={text:JSON.stringify(q.before),choice:'original',rationale:'合成另一次修改'};console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        audit=self.assert_gate_parity(b,v);self.assertEqual(audit['counts']['queries_reviewed'],0)
        self.assertIn('adjudication_contract_draft_unconfirmed',audit['query_errors']['Q20'])

    def test_confirming_later_semantic_change_blanks_cases_and_preserves_history(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"await A.confirmContract(b,s,'Q20',q.before,'original','合成恢复两个必要目标');console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        self.assert_gate_parity(b,v)
        for row in v['session']['cases'].values():
            self.assertIsNone(row['event_id']);self.assertIsNone(row['review']['reference_state']);self.assertEqual(len(row['review']['relation_findings']),2)
            self.assertEqual(row['review_history'][-1]['review']['reference_state'],'sufficient');self.assertIsNotNone(row['review_history'][-1]['event_id'])

    def test_repeated_identical_confirmation_preserves_revision_and_case_records(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"const before=JSON.stringify(s);"+CONFIRM+"console.log(JSON.stringify({same:before===JSON.stringify(s),session:s,progress:A.progress(b,s)}));")
        self.assertTrue(v['same']);self.assertEqual(v['session']['queries']['Q20']['revision'],1);self.assert_gate_parity(b,v)

    def test_export_restore_preserves_revision_events_and_source_bindings(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"const exported=JSON.stringify(s);s=await A.restore(b,JSON.parse(exported),await A.digest(b));console.log(JSON.stringify({same:exported===JSON.stringify(s),session:s,progress:A.progress(b,s)}));")
        self.assertTrue(v['same']);audit=self.assert_gate_parity(b,v);self.assertEqual(audit['counts']['cases_reviewed'],2)

    def test_old_fact_licenses_cannot_pass_new_contract(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+FILL+"for(const row of Object.values(s.cases))row.review.supported_target_fact_ids=['f0'];console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        audit=self.assert_gate_parity(b,v);self.assertEqual(audit['counts']['cases_reviewed'],0)

    def test_explicit_reconfirmation_repairs_mismatched_case_shape_without_inventing_support(self):
        b=revision_fixture();v=node_run(b,SETUP+CONFIRM+"s.cases['CASE-004'].review=A.copy(b.cases[0].original);"+CONFIRM+"console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        self.assert_gate_parity(b,v);row=v['session']['cases']['CASE-004']
        self.assertEqual([f['fact_id'] for f in row['review']['fact_findings']],['f1']);self.assertEqual(row['review']['relation_findings'],[])
        self.assertEqual(row['review']['fact_findings'][0]['support'],'unknown');self.assertIsNone(row['event_id'])
        self.assertEqual(row['review_history'][-1]['reason'],'case_contract_mismatch')

    def test_failed_record_creation_does_not_half_switch_contract_or_cases(self):
        b=revision_fixture();v=node_run(b,SETUP+"const before=JSON.stringify(s);Object.defineProperty(globalThis,'crypto',{value:{subtle:{digest:async()=>{throw Error('synthetic hash failure')}}}});let failed=false;try{"+CONFIRM+"}catch{failed=true}console.log(JSON.stringify({failed,unchanged:before===JSON.stringify(s)}));")
        self.assertTrue(v['failed']);self.assertTrue(v['unchanged'])


class RevisionPageTests(unittest.TestCase):
    run_ui=page_tests.AdjudicationPageTests.run_ui

    def test_failed_confirmation_has_inline_reason_and_cases_stay_read_only(self):
        self.run_ui(r"""
button('载入建议契约').click();const reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成拟确认';reason.oninput();
await button('确认本查询契约').click();
assert.match(context.document.getElementById('actionFeedback').textContent,/评分接触声明/);
elements.casesTab.click();assert.match(elements.content.textContent,/当前CASE不可编辑或确认/);assert.equal(button('校验并确认本CASE'),undefined);
await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());assert.equal(saved.events.length,0);
assert.equal(saved.queries.Q20.payload.contract.required_facts.items.length,2);
""",bundle=revision_fixture())

    def test_real_controls_show_only_new_f1_and_repeated_confirm_is_noop(self):
        self.run_ui(r"""
elements.exposure.value='seen';elements.exposure.onchange();elements.contextAck.checked=true;elements.contextAck.onchange();
button('载入建议契约').click();const reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成仅f1';reason.oninput();await button('确认本查询契约').click();
await button('确认本查询契约').click();assert.match(elements.message.textContent,/未增加版本/);
elements.casesTab.click();assert.match(elements.content.textContent,/活动共享契约 · 已确认/);assert.match(elements.content.textContent,/必要关系：无/);
assert.ok(tree(elements.content,'legend').some(n=>n.textContent.startsWith('f1 ·')));
assert.ok(tree(elements.content,'legend').every(n=>!n.textContent.startsWith('f0 ·')&&!n.textContent.startsWith('relation_0')&&!n.textContent.startsWith('contrast')));
await button('引用到所选事实（不自动判direct）').click();await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());
assert.equal(saved.events.length,1);assert.equal(saved.cases['CASE-004'].review.fact_findings[0].support,'unknown');
const text=JSON.stringify(saved);elements.restore.files=[{text:async()=>text}];await elements.restore.onchange();
await elements.save.onclick();assert.deepEqual(JSON.parse(await context.savedBlob.text()).queries.Q20,saved.queries.Q20);
""",bundle=revision_fixture())


if __name__=='__main__':unittest.main()
