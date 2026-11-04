"""Synthetic DOM event tests; not a claim of actual browser layout verification."""
import json
from pathlib import Path
import re
import subprocess
import unittest

from test_human_adjudication import bundle_fixture

ROOT=Path(__file__).resolve().parents[1]
ENGINE=r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),data=JSON.parse(fs.readFileSync(0,'utf8'));
class Element{
 constructor(tag){this.tag=tag;this.children=[];this._text='';this.value='';this.checked=false;this.disabled=false;this.classList={add(){},remove(){},toggle(){}};}
 set textContent(t){this._text=String(t);this.children=[];}get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 set innerHTML(v){throw Error('unsafe HTML assignment');}
 append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}click(){return this.onclick?.();}
}
const elements=Object.fromEntries(data.ids.map(id=>[id,new Element('div')]));elements.payload.textContent=JSON.stringify(data.bundle);
const context=vm.createContext({structuredClone,Blob,TextEncoder,crypto:require('node:crypto').webcrypto,Date,JSON,console,module:{exports:{}},
 document:{body:new Element('body'),getElementById:id=>elements[id],createElement:tag=>new Element(tag),createTextNode:text=>{const e=new Element('text');e.textContent=text;return e;}},
 window:{addEventListener(){},scrollTo(){}},URL:{createObjectURL:b=>{context.savedBlob=b;return 'blob:synthetic';},revokeObjectURL(){}},setTimeout:fn=>fn()});
const tree=(e,tag)=>[...(e.tag===tag?[e]:[]),...e.children.flatMap(c=>tree(c,tag))];
const button=text=>tree(elements.content,'button').find(e=>e.textContent===text);
const input=label=>{const node=tree(elements.content,'label').find(e=>e._text===label);return node?.children.find(e=>e.tag==='textarea'||e.tag==='input');};
(async()=>{await vm.runInContext(data.script,context);const fn=new (Object.getPrototypeOf(async function(){}).constructor)('assert','elements','context','tree','button','input','data',data.assertions);await fn(assert,elements,context,tree,button,input,data);console.log('synthetic DOM actions passed');})().catch(e=>{console.error(e);process.exitCode=1;});
"""


class AdjudicationPageTests(unittest.TestCase):
    def run_ui(self,assertions,extra_fact=False):
        html=(ROOT/'scripts/templates/e1_adjudication.html').read_text(encoding='utf-8')
        bundle=bundle_fixture()
        if extra_fact:
            for variant in ('before','proposed'):
                bundle['queries'][0][variant]['contract']['required_facts']['items'].append(dict(id='f1',description='第二合成目标',sentiment='positive'))
            for case in bundle['cases']:
                case['original']['fact_findings'].append(dict(fact_id='f1',support='not_supported',rationale='合成无支持',evidence_refs=[]))
                case['original'].update(reference_state='insufficient',supported_target_fact_ids=[],missing_required_fact_ids=['f1'])
        data=dict(bundle=bundle,ids=re.findall(r'id="([A-Za-z]+)"',html),script=(ROOT/'scripts/templates/e1_adjudication.js').read_text(encoding='utf-8'),assertions=assertions)
        result=subprocess.run(['node','-e',ENGINE],cwd=ROOT,input=json.dumps(data,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_initial_view_preserves_no_confirmation_and_has_no_external_resources(self):
        html=(ROOT/'scripts/templates/e1_adjudication.html').read_text(encoding='utf-8')
        self.assertIn("connect-src 'none'",html);self.assertNotRegex(html,r'https?://|<script[^>]+src=|<link[^>]+href=')
        self.run_ui(r"assert.match(elements.progress.textContent,/契约 0\/1/);assert.equal(elements.complete.disabled,true);await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());assert.equal(saved.events.length,0);assert.equal(saved.completion_event_id,null);")

    def test_actual_contract_button_resets_both_case_forms_and_exports_records(self):
        self.run_ui(r"""
elements.exposure.value='unsure';elements.exposure.onchange();elements.contextAck.checked=true;elements.contextAck.onchange();
button('载入建议契约').click();const reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成接受变更，重新评审';reason.oninput();
await button('确认本查询契约').click();assert.match(elements.message.textContent,/重置为空白/);
await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());assert.equal(saved.events.length,1);
for(const row of Object.values(saved.cases)){assert.equal(row.review.reference_state,null);assert.equal(row.review.fact_findings[0].support,'unknown');assert.equal(row.review.fact_findings[0].evidence_refs.length,0);}
elements.casesTab.click();assert.match(elements.content.textContent,/原判断/);assert.match(elements.content.textContent,/实际人工判断/);assert.match(elements.content.textContent,/完整评论/);
""")

    def test_original_confirmation_export_restore_and_early_completion_block(self):
        self.run_ui(r"""
elements.exposure.value='seen';elements.exposure.onchange();elements.contextAck.checked=true;elements.contextAck.onchange();
button('载入原契约').click();const reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成保留原契约';reason.oninput();await button('确认本查询契约').click();
await elements.save.onclick();const text=await context.savedBlob.text();elements.restore.files=[{text:async()=>text}];await elements.restore.onchange();
assert.match(elements.message.textContent,/恢复/);assert.equal(elements.complete.disabled,true);await elements.complete.onclick();assert.match(elements.message.textContent,/未完成/);
""")

    def test_full_synthetic_ui_sequence_records_each_human_control_separately(self):
        self.run_ui(r"""
elements.exposure.value='unsure';elements.exposure.onchange();elements.contextAck.checked=true;elements.contextAck.onchange();
button('载入原契约').click();let reason=input('本人确认理由 / 如何解决原歧义');reason.value='合成确认原契约';reason.oninput();await button('确认本查询契约').click();
elements.casesTab.click();reason=input('本次实际决定：保留或修改原判断的理由');reason.value='合成逐项复核第一份';reason.oninput();await button('校验并确认本CASE').click();
const second=data.bundle.cases[1].case_id;tree(elements.navList,'button').find(n=>n.textContent.startsWith(second)).click();
reason=input('本次实际决定：保留或修改原判断的理由');reason.value='合成逐项复核第二份';reason.oninput();await button('校验并确认本CASE').click();
elements.pairsTab.click();reason=input('统一裁决理由（不自动复制某一侧答案）');reason.value='合成配对统一';reason.oninput();await button('确认本配对统一裁决').click();
assert.equal(elements.complete.disabled,false);await elements.complete.onclick();assert.match(elements.ready.textContent,/本轮裁决已提交/);
await elements.save.onclick();const saved=JSON.parse(await context.savedBlob.text());assert.equal(saved.events.length,5);assert.equal(saved.events.at(-1).kind,'completion');
assert.ok(Object.values(saved.queries).every(q=>q.event_id));assert.ok(Object.values(saved.cases).every(c=>c.event_id));
""")

    def test_selected_fact_for_new_citation_survives_render(self):
        self.run_ui(r"""
elements.casesTab.click();const target=()=>tree(elements.content,'label').find(e=>e._text==='点击引用时对应的目标事实').children.find(e=>e.tag==='select');
target().value='f1';target().onchange();await button('引用到所选事实（不自动判direct）').click();
assert.equal(target().value,'f1');await elements.save.onclick();const s=JSON.parse(await context.savedBlob.text());
const review=s.cases[data.bundle.cases[0].case_id].review;assert.equal(review.fact_findings[1].evidence_refs.length,1);assert.equal(review.fact_findings[1].support,'not_supported');
""",extra_fact=True)


if __name__=='__main__':unittest.main()
