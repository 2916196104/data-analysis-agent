// 隔离 SQLite、真实队列与浏览器；默认要求零模型调用，意图验收模式提供受控模型响应。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const intentMode = process.env.E2E_INTENT_PHASE1 === '1';
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const out = await mkdtemp(path.join(root, `output/playwright/${intentMode ? 'intent-phase1' : 'pros-cons'}-`));
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const children = [], logs = [], errors = [], screenshots = [];
const report = { source: 'isolated SQLite + real API/worker + Vite/Chromium; simulated product reviews', artifacts: out };
let browser, page, modelCalls = 0, semanticCalls = 0;
const model = createServer(async (request, response) => {
  modelCalls++;
  if (!intentMode) { response.writeHead(503); response.end(); return; }
  let raw = ''; for await (const chunk of request) raw += chunk;
  const body = JSON.parse(raw);
  let message;
  if (body.tools) {
    const prompt = body.messages.find(row => row.role === 'user').content;
    const plan = JSON.parse(prompt.split('<query_plan>\n')[1].split('\n</query_plan>')[0]);
    const ids = body.messages.filter(row => row.role === 'tool').map(row => JSON.parse(row.content).data?.result_id).filter(Boolean);
    message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
      : { role: 'assistant', content: null, tool_calls: plan.requirements.map((query, index) => ({
        id: `tool-${index}`, type: 'function', function: { name: query.tool, arguments: JSON.stringify(query.parameters) } })) };
  } else {
    const payload = JSON.parse(body.messages.at(-1).content);
    if (payload.schema) {
      semanticCalls++;
      assert.ok(!raw.includes('file_sha256') && !raw.includes('raw_json'));
      assert.ok(Buffer.byteLength(body.messages.at(-1).content) <= 16000);
      message = { role: 'assistant', content: JSON.stringify({ query_type: 'aspect_summary', sentiment: 'positive', needs_clarification: false }) };
    } else {
      message = { role: 'assistant', content: JSON.stringify({ summary: payload.summary_options[0],
        items: payload.findings.map(fact => ({ finding_ref: fact.finding_ref, aspect_key: fact.aspect_key, explanation: fact.statements[0] })), caveats: [] }) };
    }
  }
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify({ id: `fixture-${modelCalls}`, object: 'chat.completion', created: 0, model: body.model,
    choices: [{ index: 0, finish_reason: message.tool_calls ? 'tool_calls' : 'stop', message }],
    usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } }));
});
const run = promisify(execFile);
async function port() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}
async function until(check, label, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${label}`);
}
function launch(command, args, name, extra = {}) {
  const log = createWriteStream(path.join(out, `${name}.log`)); logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', LABEL_EXECUTION_MODE: 'local', ...extra } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
}
try {
  await run(python, ['-c', `
import hashlib, json, sys
from pathlib import Path
from scripts.frontend_fixture import create_fixture
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
from data_analysis_agent.reviews.opinions import validate_opinions
out=Path(sys.argv[1]); create_fixture(out)
reviews=out/'more-models.jsonl'
reviews.write_text('\\n'.join(json.dumps({'parent_asin': product,'user_id': product,'text': text,'timestamp':1675296000000}) for product,text in [
    ('DEMO-A2','质量稳定'),('DEMO-B2','售后回复慢')]),encoding='utf-8')
import_amazon_jsonl(out/'reviews.sqlite3',reviews_path=reviews,dataset_version='SIMULATED-frontend')
identity=dict(model='SIMULATED-ANNOTATIONS',base_url='https://offline.invalid/v1',prompt_version='fixture-v1')
for review in pending_reviews(out/'reviews.sqlite3',**identity,limit=1000):
    text=review['text']; values=[dict(aspect='quality' if text=='质量稳定' else 'service',sentiment='positive' if text=='质量稳定' else 'negative',quote=text)]
    save_annotation(out/'reviews.sqlite3',review,**identity,status='success',opinions=validate_opinions(json.dumps({'opinions':values}),text))
meta=out/'products.jsonl'
meta.write_text('\\n'.join(json.dumps({'parent_asin': product, 'title': title, 'details':{'Brand':brand}},ensure_ascii=False) for product,title,brand in [
    ('DEMO-A','美的 1.5 匹风酷空调（模拟数据）','美的'), ('DEMO-B','小米 米家新一级能效空调（模拟数据）','小米'),
    ('DEMO-A2','美的 3匹风酷空调（模拟数据）','美的'), ('DEMO-B2','小米 3匹空调（模拟数据）','小米')]),encoding='utf-8')
import_amazon_jsonl(out/'reviews.sqlite3',meta_path=meta,dataset_version='SIMULATED-frontend')
(out/'business-before.sha256').write_text(hashlib.sha256((out/'reviews.sqlite3').read_bytes()).hexdigest())
`, out], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } });
  const apiPort = await port(), webPort = await port();
  const api = `http://127.0.0.1:${apiPort}/api/v1`, web = `http://127.0.0.1:${webPort}`;
  await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
  const entry = intentMode ? ['-c', `
from data_analysis_agent.agents import reviews
original=reviews.run_review_agent
def execute(*args,**kwargs):
    result=original(*args,**kwargs)
    if args[1]=='美的空调与小米空调的优缺点都有哪些？':
        result.update(status='error',error=dict(code='timeout',message='隔离验收注入的失败'))
        result.pop('answer',None);result.pop('public_answer',None)
    return result
reviews.run_review_agent=execute
from data_analysis_agent.cli.server import main
main()
`] : ['-m', 'data_analysis_agent.cli.server'];
  launch(python, [...entry, 'serve', '--db', path.join(out, 'reviews.sqlite3'),
    '--port', String(apiPort), '--env-file', path.join(out, 'unused.env'), '--import-root', out, '--allow-origin', web], 'api', {
    LLM_MODEL_ID: 'SIMULATED-NOT-CALLED', LLM_API_KEY: 'SIMULATED-key', LLM_MAX_RETRIES: '0',
    LLM_BASE_URL: `http://127.0.0.1:${model.address().port}/v1`,
  });
  launch(process.execPath, [path.join(root, 'frontend/node_modules/vite/bin/vite.js'), 'frontend', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: api });
  await until(async () => {
    try { return (await fetch(api + '/health', { signal: AbortSignal.timeout(1000) })).ok && (await fetch(web, { signal: AbortSignal.timeout(1000) })).ok; } catch { return false; }
  }, 'services');
  const options = (await (await fetch(api + '/options')).json()).result;
  const profile = options.profiles.find(row => row.requested_model === 'SIMULATED-ANNOTATIONS');
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
  await context.addInitScript(({ api, profileId }) => {
    const key = `review-workbench:${api}`;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify(['amazon_reviews_2023', 'SIMULATED-frontend']), productIds: ['DEMO-A'], profileId,
      current: ['2023-02-01', '2023-02-24'], baseline: ['2023-01-01', '2023-01-31'], note: '',
    } }));
  }, { api, profileId: profile.profile_id });
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(web + '/assistant');
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  const input = page.getByRole('textbox', { name: '评论分析问题' });
  async function send(question, expectedStatus = 'succeeded') {
    await input.fill(question);
    const submitted = page.waitForResponse(response => response.url() === api + '/tasks' && response.request().method() === 'POST');
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    const id = (await (await submitted).json()).result.task.task_id;
    let task;
    await until(async () => { task = (await (await fetch(api + '/tasks/' + id)).json()).result; return ['succeeded', 'failed', 'partial'].includes(task.status); }, 'analysis');
    assert.equal(task.status, expectedStatus, JSON.stringify(task.error));
    await until(async () => await input.isEnabled(), 'answer shown');
    return task;
  }
  if (intentMode) {
    const rounds = []; report.rounds = rounds;
    const record = task => { rounds.push({ question: task.parameters.question, task_id: task.task_id, context_task_id: task.parameters.context_task_id,
      status: task.status, result_status: task.result.status, user_intent: task.result.user_intent,
      validation: task.result.validation?.status, model_calls: task.result.total_model_calls,
      queries: Object.values(task.result.results).filter(row => row.kind !== 'context').map(row => ({ kind: row.kind, parameters: row.parameters })) }); return task; };
    const first = record(await send('美的空调好评主要集中在哪些方面？'));
    assert.equal(first.result.validation.status, 'passed');
    assert.deepEqual(first.result.query_plan.required_tools, ['get_positive_aspects']);
    const second = record(await send('使用效果主要集中在哪些地方？'));
    assert.equal(second.parameters.context_task_id, first.task_id);
    assert.equal(second.result.results.r1.kind, 'opinion_breakdown');
    assert.equal(second.result.results.r1.parameters.sentiment, 'positive');
    assert.equal(second.result.results.r1.parameters.aspect, 'effectiveness');
    assert.equal(second.result.total_model_calls, 0);
    const third = record(await send('那物流方面呢？'));
    assert.equal(third.result.results.r1.parameters.sentiment, 'positive');
    assert.equal(third.result.results.r1.parameters.aspect, 'logistics');
    assert.equal(third.result.total_model_calls, 0);
    const b = record(await send('美的空调的好评主要是什么？'));
    assert.deepEqual(b.result.query_plan.required_tools, ['get_positive_aspects']);
    const oral = record(await send('这款机器让买家买账的点是啥？'));
    assert.equal(oral.result.intent_model_calls, 1); assert.equal(oral.result.total_model_calls, 1);
    assert.deepEqual(oral.result.query_plan.required_tools, ['get_positive_aspects']);
    const unknown = record(await send('外星牌空调的好评主要是什么？'));
    assert.equal(unknown.result.status, 'unsupported'); assert.equal(unknown.result.query_executions, 0);
    assert.match(unknown.result.public_answer.summary, /未找到/);
    const failed = record(await send('美的空调与小米空调的优缺点都有哪些？', 'failed'));
    await page.reload(); await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
    const comparison = record(await send('对比这两款商品的差别。'));
    assert.equal(comparison.parameters.context_task_id, failed.task_id);
    assert.equal(comparison.result.status, 'unsupported'); assert.equal(comparison.result.query_executions, 0);
    assert.match(comparison.result.public_answer.summary, /美的空调/); assert.match(comparison.result.public_answer.summary, /小米空调/);
    assert.match(comparison.result.public_answer.summary, /商品 ID/);
    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.locator('.chat-turn').last().scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `intent-${width}.png`), animations: 'disabled' });
      const measure = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
        inputBottom: document.querySelector('.chat-input-area').getBoundingClientRect().bottom,
        overflow: document.querySelector('.chat-messages').scrollWidth > document.querySelector('.chat-messages').clientWidth + 1 }));
      assert.ok(measure.documentWidth <= width + 1); assert.equal(measure.overflow, false); assert.ok(measure.inputBottom <= 1001);
      screenshots.push(measure);
    }
    assert.equal(semanticCalls, 1);
    report.checks = ['A positive effectiveness drill-down', 'B positive overview', 'C inherited logistics intent',
      'D failed user entities restored after refresh, concrete model selection', 'E unknown entity rejected',
      'F validation and read-only database', 'one bounded schema call for colloquial fallback'];
    report.semanticCalls = semanticCalls;
  } else {
  const task = await send('美的空调与小米空调的优缺点都有哪些？');
  assert.equal(task.result.status, 'ok'); assert.equal(task.result.validation.status, 'passed');
  assert.equal(task.result.query_executions, 5); assert.equal(task.result.total_model_calls, 0);
  assert.deepEqual(task.parameters.product_ids, ['DEMO-A']);
  assert.deepEqual(task.result.scope.product_ids, ['DEMO-A', 'DEMO-A2', 'DEMO-B', 'DEMO-B2']);
  const publicView = (await (await fetch(api + '/public/tasks/' + task.task_id)).json()).result;
  assert.deepEqual(publicView.public_answer, task.result.public_answer);
  await page.getByText('商品：4 款商品', { exact: true }).waitFor();
  const answer = page.getByRole('region', { name: '分析回答' });
  assert.match(await answer.innerText(), /优点（正面评价）/); assert.match(await answer.innerText(), /缺点（负面评价）/);
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.locator('.chat-turn').last().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, `answer-${width}.png`), animations: 'disabled' });
    const measure = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      messageOverflow: document.querySelector('.chat-messages').scrollWidth > document.querySelector('.chat-messages').clientWidth + 1,
      inputBottom: document.querySelector('.chat-input-area').getBoundingClientRect().bottom }));
    assert.ok(measure.documentWidth <= width + 1); assert.equal(measure.messageOverflow, false); assert.ok(measure.inputBottom <= 1001);
    screenshots.push(measure);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '复制回答', exact: true }).click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  assert.match(copied, /美的/); assert.match(copied, /小米/); assert.match(copied, /优点/); assert.match(copied, /缺点/);
  await page.locator('.chat-query-snapshot > summary').click();
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载查询快照', exact: true }).click();
  await (await downloading).saveAs(path.join(out, 'query-snapshot.json'));
  const snapshot = JSON.parse(await readFile(path.join(out, 'query-snapshot.json'), 'utf8'));
  assert.deepEqual(snapshot.selection.product_ids, ['DEMO-A']);
  assert.deepEqual(snapshot.resolved_scope.product_ids, ['DEMO-A', 'DEMO-A2', 'DEMO-B', 'DEMO-B2']);
  const before = await answer.innerText();
  await page.reload(); await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await until(async () => (await page.getByRole('region', { name: '分析回答' }).count()) === 1, 'restore');
  assert.equal((await page.getByRole('region', { name: '分析回答' }).innerText()).replace('已复制', '复制回答'), before.replace('已复制', '复制回答'));
  const missing = await send('美的空调与未知品牌空调的优缺点');
  assert.equal(missing.result.status, 'unsupported'); assert.equal(missing.result.query_executions, 0);
  await page.getByRole('region', { name: '分析回答' }).last().getByText(/当前数据集未找到/).waitFor();
  await page.screenshot({ path: path.join(out, 'missing-product.png'), animations: 'disabled' });
  assert.equal(modelCalls, 0);
  report.checks = ['two named brands/four models from one page selection', 'four sentiment/group queries', 'zero model requests', 'public history read', 'copy answer', 'download snapshot preserves original selection and resolved groups', 'refresh restores answer', 'specific missing product explanation', 'business database unchanged'];
  await writeFile(path.join(out, 'task.json'), JSON.stringify(task, null, 2));
  }
  assert.deepEqual(errors, []);
  await run(python, ['-c', `import hashlib,sys;from pathlib import Path;p=Path(sys.argv[1]);assert hashlib.sha256((p/'reviews.sqlite3').read_bytes()).hexdigest()==(p/'business-before.sha256').read_text()`, out], { cwd: root, windowsHide: true });
  report.status = 'passed'; report.modelCalls = modelCalls; report.screenshots = screenshots; report.errors = errors;
} catch (error) {
  report.status = 'failed'; report.error = String(error); process.exitCode = 1;
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
} finally {
  if (browser) await browser.close();
  for (const child of children.filter(child => child.pid && child.exitCode === null)) {
    if (process.platform === 'win32') await run('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).catch(() => {});
    else child.kill();
  }
  model.closeAllConnections(); await new Promise(resolve => model.close(resolve));
  for (const log of logs) log.end();
  await writeFile(path.join(out, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
