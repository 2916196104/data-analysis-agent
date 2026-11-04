"""Synthetic offline-page checks in Node VM; these are not browser layout tests."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from data_analysis_agent.reviews.exploratory_e1 import blind_review_case
from test_exploratory_e1 import inputs, prepare, draft

TEMPLATE=Path(__file__).resolve().parents[1]/'scripts/templates/e1_review.html'
ENGINE=r"""
const {readFileSync}=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const data=JSON.parse(readFileSync(0,'utf8'));
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.value='';this._text='';}
 set textContent(v){this._text=String(v);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 set innerHTML(v){throw Error('unsafe HTML rendering');}
 append(...items){this.children.push(...items);if(this.tag==='select'&&!this.value&&items.length)this.value=items[0].value;}
 replaceChildren(...items){this.children=[];this._text='';this.value='';this.append(...items);}
 addEventListener(name,fn){(this.handlers[name]||=[]).push(fn);}
 click(){for(const fn of this.handlers.click||[])fn();}
}
const elements={};for(const id of data.ids)elements[id]=new Element(id==='activeFact'?'select':'div');
elements.payload.textContent=JSON.stringify(data.payload);
const context=vm.createContext({structuredClone,Blob,Date,JSON,console,
 document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),createTextNode:text=>{const e=new Element('text');e.textContent=text;return e;}},
 window:{addEventListener:()=>{}},URL:{createObjectURL:b=>{context.savedBlob=b;return 'blob:synthetic';},revokeObjectURL:()=>{}},setTimeout:fn=>fn()});
vm.runInContext(data.script,context);
const get=expression=>vm.runInContext(expression,context),run=code=>vm.runInContext(code,context);
const tree=(node,tag)=>[...(node.tag===tag?[node]:[]),...node.children.flatMap(c=>tree(c,tag))];
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
(async()=>{await new AsyncFunction('assert','get','run','tree','elements','data','context',data.assertions)(assert,get,run,tree,elements,data,context);console.log('synthetic page events and assertions passed');})().catch(e=>{console.error(e);process.exitCode=1;});
"""


class OfflineReviewTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template=TEMPLATE.read_text(encoding='utf-8')

    def test_no_external_resources_and_inert_payload_encoding(self):
        self.assertIn("connect-src 'none'",self.template)
        self.assertNotRegex(self.template,r'https?://|<script[^>]+src=|<link[^>]+href=')
        self.assertNotIn('innerHTML',self.template)
        payload=json.dumps({'synthetic':'</script><script>throw Error()</script>'}).replace('<','\\u003c')
        self.assertNotIn('</script>',payload)
        self.assertEqual(json.loads(payload)['synthetic'],'</script><script>throw Error()</script>')

    def run_js(self,assertions):
        if not shutil.which('node'):
            self.skipTest('Node not installed; no browser verification is implied')
        run,sources,scope=inputs();p=prepare(run,sources,scope);q=run['manifest']['queries'][0]
        c=blind_review_case(p,draft(scope,q['id'],q['text']),question=q['text'],case_id='CASE-SYNTH',expected_scope=scope)
        # Code point offsets must survive a supplementary character in full context.
        c['candidates'][0].update(review_text='😀合成证据后文',quote='合成证据',quote_start=1,quote_end=5)
        c['relation_findings']=[dict(relation_id='relation_synthetic',support='unknown',evidence_keys=[],rationale='待人工判断')]
        script=re.findall(r'<script>(.*?)</script>',self.template,re.S)[0]
        payload=dict(script=script,ids=re.findall(r'id="([A-Za-z]+)"',self.template),assertions=assertions,
            payload=dict(version='local-evidence-review-view-v1',source_input_sha256='a'*64,cases=[c]))
        result=subprocess.run(['node','-e',ENGINE],input=json.dumps(payload,ensure_ascii=False),text=True,encoding='utf-8',
            capture_output=True,timeout=15,check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_render_unicode_quote_and_explicit_unconfirmed_export(self):
        self.run_js(r"""
assert.equal(elements.question.textContent,'CASE-SYNTH · '+data.payload.cases[0].question);
const pre=tree(elements.evidence,'pre')[0];
assert.equal(pre.textContent,'😀合成证据后文');assert.equal(tree(pre,'mark')[0].textContent,'合成证据');
elements.state.value='limited';elements.contractDecision.value='accept_draft';elements.independentProposal.value='f0';
tree(elements.evidence,'button')[0].click();
assert.equal(get('selected().fact_findings[0].support'),'unknown');
elements.save.click();const saved=JSON.parse(await context.savedBlob.text());
assert.equal(saved.human_confirmed,false);assert.equal(saved.cases[0].contract_confirmed,false);
assert.equal(saved.cases[0].contract_review_decision,'accept_draft');
assert.equal(saved.cases[0].fact_findings[0].evidence_refs[0].quote_start,1);
assert.equal(saved.cases[0].reference_state,'limited');assert.equal(saved.reviewer_count,1);
assert.equal(saved.prior_relevance_exposure,null);
assert.equal(get('restoreDraft('+JSON.stringify(saved)+').length'),1);
""")

    def test_restore_rejects_source_identity_unknown_facts_and_outside_spans(self):
        self.run_js(r"""
elements.save.click();const saved=JSON.parse(await context.savedBlob.text());
const mutations=[v=>v.source_input_sha256='b'.repeat(64),v=>v.human_confirmed=true,
 v=>v.cases[0].contract_confirmed=true,v=>v.cases[0].context_policy='quote_only',
 v=>v.cases[0].candidates[0].quote='篡改',v=>v.cases[0].supported_target_fact_ids=['invented'],
 v=>v.cases[0].fact_findings[0].support='made_up',v=>v.cases[0].fact_findings.pop(),
 v=>v.cases[0].fact_findings[0].evidence_refs=[{key:v.cases[0].candidates[0].key,quote_start:0,quote_end:5}],
 v=>v.cases[0].relation_findings[0].evidence_keys=['outside-list']];
for(const mutate of mutations){const changed=structuredClone(saved);mutate(changed);assert.throws(()=>get('restoreDraft('+JSON.stringify(changed)+')'));}
assert.equal(get('cases[0].human_confirmed'),false);
""")

    def test_empty_list_and_restore_event_preserve_pending_state(self):
        self.run_js(r"""
run('cases[0].candidates=[];render()');assert.match(elements.evidence.textContent,/没有候选/);
run('cases=structuredClone(INITIAL.cases);render()');elements.save.click();
const saved=JSON.parse(await context.savedBlob.text());saved.cases[0].review_notes='合成评审备注';
elements.restore.files=[{text:async()=>JSON.stringify(saved)}];await elements.restore.handlers.change[0]();
assert.equal(elements.notes.value,'合成评审备注');assert.equal(get('selected().human_confirmed'),false);
assert.match(elements.message.textContent,/不代表正式/);
""")


if __name__=='__main__':
    unittest.main()
