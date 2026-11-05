"""Synthetic price alternatives only; no private quotes, scores or confirmations."""
from copy import deepcopy
import json
import unittest

from data_analysis_agent.reviews.human_adjudication import validate_session, review_errors
import test_adjudication_page as page_tests
from test_human_adjudication import bundle_fixture, node_run


def price_bundle():
    b=bundle_fixture(quotes=('[合成] 测试条目购入次日下调售价',))
    q=b['queries'][0];c=q['before']['contract']
    c.update(target_entity='purchase_price',subtopic='synthetic_price_alternatives',sentiment_intent='negative',
             aspect_candidates=[dict(aspect='price',rationale='合成价格测试')])
    c['required_facts']=dict(operator='any_of',items=[dict(id='f0',description='合成条目售价偏高',sentiment='negative'),dict(id='f1',description='合成条目购入次日降价',sentiment='negative')],relations=[])
    q['before']['independent_target_fact_ids']=['f0','f1'];q['proposed']=deepcopy(q['before'])
    for case in b['cases']:
        h=case['candidates'][0];r=case['original']
        r.update(supported_target_fact_ids=['f1'],missing_required_fact_ids=['f0'])
        r['fact_findings'][0].update(evidence_refs=[],rationale='合成原字段待修改')
        r['fact_findings'].append(dict(fact_id='f1',support='direct',rationale='合成独立判断：购后降价',evidence_refs=[dict(key=h['key'],quote_start=h['quote_start'],quote_end=h['quote_end'])]))
    b['cases'].sort(key=lambda case:case['mode']!='filtered')
    return b


OPEN=r"""
elements.exposure.value='seen';elements.exposure.onchange();elements.contextAck.checked=true;elements.contextAck.onchange();
button('载入原契约').click();const reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成确认any_of';reason.oninput();await button('确认本查询契约').click();
elements.casesTab.click();
const fact=id=>tree(elements.content,'fieldset').find(n=>n.children.some(x=>x.tag==='legend'&&x.textContent.startsWith(id+' ·')));
const support=(id,value)=>{const control=tree(fact(id),'select')[0];control.value=value;control.onchange();};
const caseErrors=()=>tree(elements.content,'ul').filter(n=>n.className==='errors').at(-1).textContent;
"""


class LiveValidationPageTests(unittest.TestCase):
    run_ui=page_tests.AdjudicationPageTests.run_ui

    def test_support_change_removes_stale_errors_without_navigation_or_rerender(self):
        self.run_ui(OPEN+r"""
assert.match(caseErrors(),/f0 直接支持缺引用/);assert.match(caseErrors(),/sufficient/);
const control=tree(fact('f0'),'select')[0];support('f0','not_supported');
assert.doesNotMatch(caseErrors(),/f0 直接支持缺引用/);assert.doesNotMatch(caseErrors(),/sufficient/);
assert.match(caseErrors(),/缺失ID/);assert.equal(tree(fact('f0'),'select')[0],control);
await elements.save.onclick();const s=JSON.parse(await context.savedBlob.text());const r=s.cases[data.bundle.cases[0].case_id].review;
assert.equal(r.fact_findings[0].support,'not_supported');assert.deepEqual(r.supported_target_fact_ids,['f1']);assert.deepEqual(r.missing_required_fact_ids,['f0']);
assert.equal(s.events.length,1);assert.equal(s.cases[data.bundle.cases[0].case_id].event_id,null);
""",bundle=price_bundle())

    def test_missing_checkbox_updates_errors_and_read_only_explanation_without_relabeling(self):
        self.run_ui(OPEN+r"""
support('f0','not_supported');
const panel=tree(elements.content,'fieldset').find(n=>n.children.some(x=>x.tag==='legend'&&x.textContent==='缺失的必要事实'));
const f0=tree(panel,'input')[0];f0.checked=false;f0.onchange();
assert.doesNotMatch(caseErrors(),/缺失ID/);assert.match(caseErrors(),/待本人确认CASE/);
const summary=context.document.getElementById('caseFactSummary').textContent;
assert.match(summary,/any_of/);assert.match(summary,/未标为direct：\[f0\]/);assert.match(summary,/协议要求的缺失必要事实：\[\]/);
await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());const r=saved.cases[data.bundle.cases[0].case_id].review;
assert.deepEqual(r.missing_required_fact_ids,[]);assert.equal(r.fact_findings[0].support,'not_supported');assert.equal(r.fact_findings[0].evidence_refs.length,0);
assert.equal(r.fact_findings[1].support,'direct');assert.equal(r.fact_findings[1].evidence_refs.length,1);assert.equal(saved.events.length,1);
""",bundle=price_bundle())

    def test_reason_and_license_edits_refresh_errors_preserving_typed_text(self):
        self.run_ui(OPEN+r"""
support('f0','not_supported');
const textarea=tree(fact('f0'),'textarea')[0];textarea.value='';textarea.oninput();assert.match(caseErrors(),/f0 缺理由/);
textarea.value='合成分别填写的f0理由';textarea.oninput();assert.doesNotMatch(caseErrors(),/f0 缺理由/);assert.equal(tree(fact('f0'),'textarea')[0],textarea);
const panel=tree(elements.content,'fieldset').find(n=>n.children.some(x=>x.tag==='legend'&&x.textContent==='许可的目标事实'));
const license0=tree(panel,'input')[0];license0.checked=true;license0.onchange();assert.match(caseErrors(),/许可事实必须为direct/);
license0.checked=false;license0.onchange();assert.doesNotMatch(caseErrors(),/许可事实必须为direct/);
""",bundle=price_bundle())


class AlternativeProtocolTests(unittest.TestCase):
    def test_coverage_preserves_all_of_any_of_and_required_relation_boundaries(self):
        b=price_bundle()
        for operator,relations,support,expected in (
            ('any_of',False,('not_supported','direct'),[]),
            ('any_of',False,('not_supported','not_supported'),['f0','f1']),
            ('all_of',False,('not_supported','direct'),['f0']),
            ('any_of',True,('not_supported','direct'),['f0']),
        ):
            with self.subTest(operator=operator,relations=relations,support=support):
                data=deepcopy(b);c=data['queries'][0]['before']['contract'];c['required_facts']['operator']=operator
                if relations:c['required_facts']['relations']=[dict(kind='same_review',fact_ids=['f0','f1'])]
                script="const c=b.queries[0].before.contract,r=s.cases[b.cases[0].case_id].review;"
                script+="r.fact_findings.forEach((f,i)=>f.support="+json.dumps(support)+"[i]);r.missing_required_fact_ids="+json.dumps(expected)+";r.decision_rationale='合成边界';"
                if relations:script+="r.relation_findings=[{relation_id:'relation_0',support:'not_supported',evidence_keys:[],rationale:'合成关系未支持'}];"
                script+="console.log(JSON.stringify({coverage:A.requiredCoverage(r,c),errors:A.reviewErrors(r,c,['f0','f1'],b.cases[0]),review:r}));"
                v=node_run(data,script);self.assertEqual(v['coverage']['missing'],expected)
                py=review_errors(v['review'],c,['f0','f1'],data['cases'][0])
                self.assertFalse(any('缺失ID' in e for e in v['errors']));self.assertFalse(any('缺失事实集合' in e for e in py))

    def test_any_of_only_second_fact_direct_passes_after_explicit_synthetic_confirmation(self):
        b=price_bundle();v=node_run(b,"s.prior_relevance_exposure='seen';s.context_ack=true;const q=b.queries[0];await A.confirmContract(b,s,q.query_id,q.before,'original','合成确认');for(const c of b.cases){const r=s.cases[c.case_id].review;r.fact_findings[0].support='not_supported';r.missing_required_fact_ids=[];r.decision_rationale='合成人工独立复核';await A.confirmCase(b,s,c.case_id);}s=await A.restore(b,JSON.parse(JSON.stringify(s)),await A.digest(b));console.log(JSON.stringify({session:s,progress:A.progress(b,s)}));")
        result=validate_session(b,v['session']);self.assertEqual(result['counts'],v['progress']['counts']);self.assertEqual(result['counts']['cases_reviewed'],2)
        self.assertEqual(b['cases'][0]['mode'],'filtered')
        for c in b['cases']:
            r=v['session']['cases'][c['case_id']]['review'];self.assertEqual(r['supported_target_fact_ids'],['f1']);self.assertEqual(r['missing_required_fact_ids'],[])
            self.assertEqual(r['fact_findings'][0]['evidence_refs'],[]);self.assertEqual(r['fact_findings'][1]['evidence_refs'][0]['key'],c['candidates'][0]['key'])

    def test_current_protocol_keeps_unselected_alternatives_separate_from_required_missing(self):
        b=price_bundle();v=node_run(b,"const c=b.queries[0].before.contract,r=s.cases[b.cases[0].case_id].review;r.fact_findings[0].support='not_supported';r.decision_rationale='合成复核';console.log(JSON.stringify({errors:A.reviewErrors(r,c,['f0','f1'],b.cases[0]),review:r}));")
        py=review_errors(v['review'],b['queries'][0]['before']['contract'],['f0','f1'],b['cases'][0])
        self.assertEqual(v['errors'],['缺失ID与逐事实判断不一致']);self.assertEqual(py,['缺失事实集合与逐事实判断不一致'])


if __name__=='__main__':unittest.main()
