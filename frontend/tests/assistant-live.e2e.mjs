// 显式运行才调用真实模型，并将当前业务评论作为原文工具上下文发送给供应商。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

assert.ok(process.argv.includes('--live'), 'Explicit --live required');
const root = path.resolve(import.meta.dirname, '../..');
await mkdir(path.join(root, 'output/playwright'), {recursive:true});
const out = await mkdtemp(path.join(root, 'output/playwright/followup-live-'));
const python = path.join(root,'.venv/Scripts/python.exe');
const children=[], errors=[], rounds=[];
let browser,page;
const report={out,errors,rounds,source:'existing PostgreSQL + configured real model + Vite/Chrome'};
async function port() { const s=net.createServer(); await new Promise(r=>s.listen(0,'127.0.0.1',r)); const p=s.address().port; await new Promise(r=>s.close(r)); return p; }
function launch(cmd,args,name,env={}) { const f=createWriteStream(path.join(out,name+'.log')); const p=spawn(cmd,args,{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',LABEL_EXECUTION_MODE:'local',...env}}); p.stdout.pipe(f);p.stderr.pipe(f);children.push(p);return p; }
async function until(fn,label,ms=180000) { const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,500));}throw new Error('Timeout '+label); }
async function choose(page, ariaLabel, label) {
  const input = page.getByRole('combobox', { name: ariaLabel, exact: true });
  await page.locator('.ant-select').filter({ has: input }).click();
  await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').filter({ hasText: label }).click();
  await input.press('Escape');
}
async function py(mode) { await promisify(execFile)(python,['-m','scripts.followup_live_server',mode,'--output',out],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}}); }
try {
  await py('prepare');
  const selected=JSON.parse(await readFile(path.join(out,'selection.json'),'utf8'));
  report.selection=selected;
  const apiPort=await port(),webPort=await port(),web=`http://127.0.0.1:${webPort}`,api=`http://127.0.0.1:${apiPort}/api/v1`;
  launch(python,['-m','scripts.followup_live_server','serve','--output',out,'--port',String(apiPort),'--origin',web],'api');
  launch(process.execPath,['frontend/node_modules/vite/bin/vite.js','frontend','--host','127.0.0.1','--port',String(webPort),'--strictPort'],'vite',{VITE_API_BASE_URL:api});
  await until(async()=>{try{return (await fetch(api+'/health',{signal:AbortSignal.timeout(1000)})).ok&&(await fetch(web,{signal:AbortSignal.timeout(1000)})).ok;}catch{return false;}},'servers',60000);
  const options=(await(await fetch(api+'/options')).json()).result;
  const profile=options.profiles.find(p=>p.requested_model===selected.model);
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(({api,selected,profile})=>{
    if(!localStorage.getItem(`review-workbench:${api}`))localStorage.setItem(`review-workbench:${api}`,JSON.stringify({selected:null,notes:{},draft:{dataset:JSON.stringify([selected.source,selected.dataset_version]),productIds:selected.product_ids,profileId:profile.profile_id,current:selected.current,baseline:selected.baseline,note:''}}));
  },{api,selected,profile});
  page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url()===api+'/tasks')posts++;});
  await page.goto(web+'/assistant');
  await page.getByLabel('问答模式').getByText('评论分析',{exact:true}).click();
  const input=page.getByRole('textbox',{name:'评论分析问题'});
  async function enter(q){await input.fill(q);await page.getByRole('button',{name:'提交问题',exact:true}).click();}
  // 无上文及未知指代必须留在澄清阶段。
  await enter('只看物流');await page.getByText('需要澄清',{exact:true}).last().waitFor();assert.equal(posts,0);
  await enter('那它呢？');await until(async()=>await page.locator('.chat-turn').count()===2,'ambiguous turn');assert.equal(posts,0);
  async function finish(task,label){
    await until(async()=>{const saved=(await(await fetch(api+'/tasks/'+task.task_id)).json()).result;if(['succeeded','failed','partial'].includes(saved.status)){task=saved;return true;}return false;},label);
    await writeFile(path.join(out,`${rounds.length}-${label}.json`),JSON.stringify(task,null,2));
    rounds.push({label,id:task.task_id,status:task.status,resultStatus:task.result?.status,model_calls:task.result?.model_calls,usage:task.result?.usage,queries:Object.values(task.result?.results??{}).filter(q=>q.kind!=='context').map(q=>({kind:q.kind,parameters:q.parameters,total:q.data.total})),error:task.error});
    console.log(JSON.stringify(rounds.at(-1)));
    assert.equal(task.status,'succeeded',JSON.stringify(task.error));assert.equal(task.result.status,'ok');assert.equal(task.result.validation.status,'passed');
    await until(async()=>await page.locator('.chat-report').count()>0 || await page.getByText('查询未完成',{exact:true}).count()>0,'render result');
    return task;
  }
  async function send(q,label){const response=page.waitForResponse(r=>r.url()===api+'/tasks'&&r.request().method()==='POST');await enter(q);return finish((await(await response).json()).result.task,label);}
  function query(task,kind,params){const qs=Object.values(task.result.results).filter(q=>q.kind!=='context');assert.equal(qs.length,1);assert.equal(qs[0].kind,kind);for(const[k,v]of Object.entries(params))assert.deepEqual(qs[0].parameters[k],v);return qs[0];}
  await send('仅查询当前窗口按日的包装负面评论趋势，使用 get_aspect_trend，不需要其他查询。','initial');
  query(await send('只看物流','logistics'),'aspect_trend',{aspect:'logistics',period:'day'});
  query(await send('换成按月','monthly'),'aspect_trend',{aspect:'logistics',period:'month'});
  const first=query(await send('继续查原文','evidence'),'evidence',{aspect:'logistics',page:1});assert.ok(first.data.total>5);
  const next=query(await send('继续查原文','next-page'),'evidence',{aspect:'logistics',page:2});
  assert.ok(next.data.items.length);assert.ok(!next.data.items.some(r=>first.data.items.some(o=>o.review_id===r.review_id)));
  const before=posts;
  await enter('那它呢？');await page.getByText('需要澄清',{exact:true}).last().waitFor();assert.equal(posts,before);
  // 修改商品子集，要求显式选择旧/新范围。
  await page.getByRole('button',{name:'切换范围',exact:true}).click();
  await choose(page, '助手数据集', 'amazon-reviews-2023');
  await page.getByRole('button',{name:'应用范围',exact:true}).click();
  await enter('只看物流');await page.getByRole('button',{name:/使用当前范围 · 原文证据/}).waitFor();assert.equal(posts,before);
  const storage=()=>page.evaluate(()=>JSON.parse(localStorage[Object.keys(localStorage).find(k=>k.startsWith('review-agent-chat:'))]));
  const frozen=await storage();await page.reload();await page.getByRole('button',{name:/使用当前范围 · 原文证据/}).waitFor();assert.deepEqual(await storage(),frozen);assert.equal(posts,before);
  for(const width of [1440,1024,768]){await page.setViewportSize({width,height:900});const choice=page.getByRole('button',{name:/使用当前范围 · 原文证据/});await choice.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,`clarification-${width}.png`)});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
  const resp=page.waitForResponse(r=>r.url()===api+'/tasks'&&r.request().method()==='POST');await page.getByRole('button',{name:/沿用上轮范围 · 原文证据/}).click();
  const confirmed=await finish((await(await resp).json()).result.task,'confirmed');assert.ok(confirmed.parameters.product_ids.length>0);
  const saved=await storage();assert.deepEqual(saved.turns.slice(0,frozen.turns.length-1),frozen.turns.slice(0,-1));
  await writeFile(path.join(out,'conversation.json'),JSON.stringify(saved,null,2));
  assert.deepEqual(errors,[]);report.status='passed';
}catch(e){report.status='failed';report.error=String(e);if(page)await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});process.exitCode=1;}
finally{if(browser)await browser.close();for(const p of children.filter(p=>p.pid&&p.exitCode===null))await promisify(execFile)('taskkill',['/PID',String(p.pid),'/T','/F'],{windowsHide:true}).catch(()=>{});await py('verify').catch(e=>{report.readonlyError=String(e);process.exitCode=1;});await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));}
