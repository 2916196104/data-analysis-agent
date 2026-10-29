import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(frontend);
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const artifacts = await mkdtemp(path.join(root, 'output/playwright/dataset-switch-'));
const python = path.join(root, '.venv/Scripts/python.exe');
const apiUrl = 'http://127.0.0.1:18768/api/v1';
const webUrl = 'http://127.0.0.1:15178';
const children = [];
const logs = [];
const evidence = { requests: [], viewports: [], checks: [] };
let browser, page, model;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label) {
  for (let i = 0; i < 120; i++) { if (await fn()) return; await sleep(250); }
  throw new Error(`Timed out: ${label}`);
}
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(artifacts, `${name}.log`)); logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
}
async function get(endpoint) {
  const response = await fetch(`${apiUrl}${endpoint}`);
  assert.equal(response.status, 200);
  return (await response.json()).result;
}
async function choose(name, label) {
  const input = page.getByRole('combobox', { name, exact: true });
  await page.locator('.ant-select').filter({ has: input }).click();
  const listId = await input.getAttribute('aria-controls');
  await page.locator('.ant-select-dropdown').filter({ has: page.locator(`[id="${listId}"]`) }).locator('.ant-select-item-option').filter({ hasText: label }).click();
}
async function nav(label) {
  const button = page.getByRole('button', { name: label, exact: true });
  if (!await button.isVisible()) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
  await button.click();
}
try {
  // Isolated SQLite only; never touch the user's business database or model provider.
  await promisify(execFile)(python, ['-c', `
import json, sys
from datetime import datetime, timezone
from pathlib import Path
from scripts.frontend_fixture import create_fixture
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
root = Path(sys.argv[1]); create_fixture(root)
rows = [json.loads(line) for line in (root/'simulated.jsonl').read_text(encoding='utf-8').splitlines()]
rows = [dict(row, user_id='dataset-b-'+str(i), timestamp=int(datetime(2018,7,10+i,tzinfo=timezone.utc).timestamp()*1000)) for i,row in enumerate(rows[24:27])]
file = root/'dataset-b.jsonl'; file.write_text('\\n'.join(json.dumps(row) for row in rows),encoding='utf-8')
result=import_amazon_jsonl(root/'reviews.sqlite3',reviews_path=file,dataset_version='SIMULATED-B')
assert result['imports'][0]['inserted_records']==3, result
identity=dict(model='SIMULATED-ANNOTATIONS',base_url='https://offline.invalid/v1',prompt_version='fixture-v1')
for review in pending_reviews(root/'reviews.sqlite3',**identity,limit=1000):
    save_annotation(root/'reviews.sqlite3',review,**identity,status='success',opinions=[])
second = dict(identity,model='SIMULATED-SECOND')
for review in pending_reviews(root/'reviews.sqlite3',**second,dataset_version='SIMULATED-frontend',limit=2):
    save_annotation(root/'reviews.sqlite3',review,**second,status='success',opinions=[])
`, artifacts], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } });
  model = createServer(async (request, response) => {
    let raw=''; for await (const chunk of request) raw+=chunk;
    const body = JSON.parse(raw);
    const ids = body.messages.filter(m => m.role === 'tool').map(m => JSON.parse(m.content).data?.result_id).filter(Boolean);
    const message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
      : { role: 'assistant', content: null, tool_calls: [{ id: 'trend', type: 'function', function: { name: 'get_trend', arguments: '{"period":"month"}' } }] };
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ id:'local',object:'chat.completion',created:0, model:body.model,
      choices:[{index:0,finish_reason:message.tool_calls?'tool_calls':'stop',message}], usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30} }));
  });
  await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
  launch(python, ['-m','data_analysis_agent.cli.server','serve','--db',path.join(artifacts,'reviews.sqlite3'),
    '--port','18768','--import-root',artifacts,'--env-file',path.join(artifacts,'unused.env'),'--allow-origin',webUrl], 'api', {
    LLM_MODEL_ID:'SIMULATED-ANNOTATIONS', LLM_API_KEY:'local-test-key', LLM_BASE_URL:`http://127.0.0.1:${model.address().port}/v1`, LLM_MAX_RETRIES:'0', LABEL_EXECUTION_MODE:'local',
  });
  launch(process.execPath,[path.join(frontend,'node_modules/vite/bin/vite.js'),frontend,'--host','127.0.0.1','--port','15178'],'vite',{ VITE_API_BASE_URL:apiUrl });
  await until(async () => { try { return (await fetch(`${apiUrl}/health`)).ok && (await fetch(webUrl)).ok; } catch { return false; } }, 'servers');
  browser = await chromium.launch({ headless:true, channel:'chrome' });
  page = await browser.newPage({ viewport:{width:1440,height:900} });
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  page.on('request', r => { if (r.url().includes('/analytics/') || r.url().endsWith('/tasks') && r.method()==='POST' || r.url().includes('/reviews?')) evidence.requests.push({url:r.url(),method:r.method(),body:r.postDataJSON()}); });
  await page.goto(`${webUrl}/data-management`);
  await page.locator('.data-management > .ant-table-wrapper tbody tr.ant-table-row').first().waitFor();
  await choose('全局数据集','SIMULATED-frontend');
  assert.match(await page.locator('.data-management').innerText(), /DEMO-B/);
  await nav('新建分析');
  await until(async()=> (await page.locator('.review-metric strong').first().textContent())==='96','A statistics');
  await choose('分析商品','DEMO-B');
  await page.getByRole('combobox', {name:'分析商品',exact:true}).press('Escape');
  await until(async()=> (await page.locator('.review-metric strong').first().textContent())==='48','A product filter');
  await choose('全局数据集','SIMULATED-B');
  await until(async()=> (await page.locator('.review-metric strong').first().textContent())==='3','B statistics');
  assert.deepEqual(await page.getByRole('textbox',{name:'当前窗口',exact:true}).evaluateAll(inputs=>inputs.map(input=>input.value)),['2018-07-10','2018-07-12']);
  assert.ok(evidence.requests.some(r=>r.url.includes('/analytics/trend') && new URL(r.url).searchParams.get('dataset_version')==='SIMULATED-B'
    && new URL(r.url).searchParams.get('date_from')==='2018-07-10' && new URL(r.url).searchParams.get('date_to')==='2018-07-12'));
  assert.ok(evidence.requests.some(r=>r.url.includes('/analytics/trend') && new URL(r.url).searchParams.get('dataset_version')==='SIMULATED-B'));
  evidence.checks.push('A to B: actual trend counts 96 to 3; B default range 2018-07-10 to 2018-07-12, isolated from global 2023 data');
  await nav('数据管理');
  assert.equal(await page.locator('.data-management-controls .ant-select-selection-item').textContent(),'SIMULATED-B');
  assert.doesNotMatch(await page.locator('.data-management > .ant-table-wrapper tbody').innerText(), /DEMO-B/);
  const datasetSummary = page.getByRole('region',{name:'数据集摘要',exact:true});
  assert.match(await datasetSummary.innerText(), /评论数\s+3/);
  assert.match(await datasetSummary.innerText(), /商品数\s+1/);
  assert.match(await datasetSummary.innerText(), /2018-07-10 — 2018-07-12/);
  assert.match(await datasetSummary.getByRole('row').filter({hasText:'SIMULATED-ANNOTATIONS'}).innerText(),/100.0%/);
  assert.match(await datasetSummary.getByRole('row').filter({hasText:'SIMULATED-SECOND'}).innerText(),/0.0%/);
  evidence.datasetSummaries=(await get('/options')).datasets;
  evidence.checks.push('dataset summary: real review/product counts, dates, and both profile coverages with zero preserved');
  await choose('数据集','SIMULATED-frontend');
  assert.equal(await page.locator('.header-actions .ant-select-selection-item').textContent(),'SIMULATED-frontend');
  await choose('全局数据集','SIMULATED-B');
  await nav('AI 助手');
  await page.locator('.chat-input-toolbar .ant-radio-button-wrapper').filter({hasText:'评论分析'}).click();
  async function sendAnalysis(question, version) {
    const response = page.waitForResponse(r=>r.url()===`${apiUrl}/tasks` && r.request().method()==='POST');
    await page.getByRole('textbox',{name:'评论分析问题'}).fill(question);
    await page.getByRole('button',{name:'提交问题',exact:true}).click();
    const task = (await (await response).json()).result.task;
    assert.equal(task.parameters.dataset_version,version); assert.deepEqual(task.parameters.product_ids,[]);
    await until(async()=>['succeeded','failed','partial'].includes((await get(`/tasks/${task.task_id}`)).status),'AI finished');
    await until(async()=>!(await page.locator('.chat-status').count()),'chat finished');
    assert.equal((await get(`/tasks/${task.task_id}`)).status,'succeeded');
    return task.task_id;
  }
  const bTask = await sendAnalysis('按月查看评论趋势','SIMULATED-B');
  await choose('全局数据集','SIMULATED-frontend');
  await sendAnalysis('查询当前数据集的趋势','SIMULATED-frontend');
  assert.equal((await get(`/tasks/${bTask}`)).parameters.dataset_version,'SIMULATED-B');
  assert.ok(await page.locator('.chat-turn-scope').filter({hasText:'SIMULATED-B'}).count());
  evidence.checks.push('AI B then A: real POST parameters and persisted task association');
  await page.reload();
  await page.getByRole('combobox',{name:'全局数据集'}).waitFor();
  await until(async()=>await page.locator('.header-actions .ant-select-selection-item').textContent()==='SIMULATED-frontend','restore');
  evidence.checks.push('refresh restores validated dataset');
  await nav('评论标注');
  await choose('全局数据集','SIMULATED-B');
  assert.match(await page.locator('.annotation-page').innerText(),/SIMULATED-B/);
  await choose('标注数据集','SIMULATED-frontend');
  assert.equal(await page.locator('.header-actions .ant-select-selection-item').textContent(),'SIMULATED-frontend');
  await choose('全局数据集','SIMULATED-B');
  await page.getByRole('button',{name:'进入人工复核'}).click();
  await page.getByText('请选择当前数据集的商品并应用筛选',{exact:true}).waitFor();
  assert.doesNotMatch(await page.locator('.review-workbench-progress').innerText(),/DEMO-B/);
  await page.locator('.review-workbench-filter-grid .ant-select').nth(1).click();
  await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').filter({hasText:'DEMO-A'}).click();
  await page.getByRole('button',{name:'应用筛选',exact:true}).click();
  await page.locator('.review-workbench-card').first().waitFor();
  assert.equal(await page.locator('.review-workbench-card').count(),3);
  const reviewRequest=evidence.requests.filter(r=>r.url.includes('/reviews?')).at(-1);
  assert.equal(JSON.parse(new URL(reviewRequest.url).searchParams.get('product_key'))[1],'SIMULATED-B');
  await choose('全局数据集','SIMULATED-frontend');
  await page.getByText('请选择当前数据集的商品并应用筛选',{exact:true}).waitFor();
  assert.equal(await page.locator('.review-workbench-card').count(),0);
  evidence.checks.push('annotation synchronization; review product scope and selection cleared');
  for (const [route,name] of [['/analysis/new','analysis'],['/data-management','data'],['/annotation','annotation'],['/annotation/review','review'],['/assistant','assistant'],['/tasks','tasks']]) {
    await page.goto(webUrl+route); await page.getByRole('combobox',{name:'全局数据集'}).waitFor();
    if (name==='analysis') await page.locator('.review-trend canvas').waitFor();
    if (name==='data') await page.locator('.data-management > .ant-table-wrapper .ant-table-row').first().waitFor();
    if (name==='annotation') await page.getByRole('combobox',{name:'标注数据集',exact:true}).waitFor();
    if (name==='review') await page.locator('.review-workbench-filter').waitFor();
    if (name==='assistant') await page.locator('.chat-turn').nth(1).waitFor();
    if (name==='tasks') await page.locator('.history-item').nth(1).waitFor();
    for (const width of [1440,1024,768,390]) {
      await page.setViewportSize({width,height:900}); await sleep(200);
      const measurement=await page.evaluate(()=>{
        const rect=element=>{const r=element.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};};
        return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,selector:rect(document.querySelector('.header-actions .ant-select')),brand:rect(document.querySelector('.brand')),header:rect(document.querySelector('.header-actions'))};
      });
      assert.ok(measurement.scrollWidth<=width,`${name} ${width} horizontal overflow`);
      assert.ok(measurement.selector.left>=measurement.brand.right-1,`${name} ${width} selector overlap`);
      assert.ok(measurement.header.right<=width,`${name} ${width} header clipped`);
      evidence.viewports.push({page:name,...measurement});
      await page.screenshot({path:path.join(artifacts,`${name}-${width}.png`)});
    }
    if (name==='analysis') {
      await choose('全局数据集','SIMULATED-B');
      await until(async()=> (await page.locator('.review-metric strong').first().textContent())==='3','390px switch');
      await choose('全局数据集','SIMULATED-frontend');
      await until(async()=> (await page.locator('.review-metric strong').first().textContent())==='96','390px switch back');
      evidence.checks.push('390px selector interaction and statistics refresh');
    }
  }
  // Invalid persisted IDs must never be used for a query.
  await page.evaluate(()=>{ const key=Object.keys(localStorage).find(k=>k.startsWith('review-workbench:'));const saved=JSON.parse(localStorage[key]); saved.draft.dataset='["deleted-source","deleted-version"]'; saved.draft.productIds=['stale-product'];localStorage[key]=JSON.stringify(saved); });
  await page.reload();
  await page.getByText('原数据集已不可用，已切换到第一个可用数据集并清空筛选。').waitFor();
  const saved=await page.evaluate(()=>JSON.parse(localStorage[Object.keys(localStorage).find(k=>k.startsWith('review-workbench:'))]));
  assert.notEqual(saved.draft.dataset,'["deleted-source","deleted-version"]'); assert.deepEqual(saved.draft.productIds,[]);
  evidence.checks.push('invalid persisted dataset falls back and clears product filters');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(artifacts,'evidence.json'),JSON.stringify(evidence,null,2));
  console.log(`PASS global dataset switching. Artifacts: ${artifacts}`);
} catch(error) {
  if(page) { await page.screenshot({path:path.join(artifacts,'failure.png')}).catch(()=>{}); await writeFile(path.join(artifacts,'failure.txt'),await page.locator('body').innerText().catch(()=>'')); }
  await writeFile(path.join(artifacts,'evidence.json'),JSON.stringify(evidence,null,2));
  console.error(`FAIL artifacts: ${artifacts}`); throw error;
} finally {
  await browser?.close();
  for(const child of children) child.kill();
  for(const log of logs) log.end();
  model?.close();
}

