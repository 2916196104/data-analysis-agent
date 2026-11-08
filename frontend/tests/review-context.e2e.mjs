// 真 API/Worker/Vite/Chromium，临时 SQLite 与进程内合成分类器；禁止真实模型和 Embedding。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '../..');
const out = path.join(root, 'output/playwright/review-context-repair-20261108');
await mkdir(out, { recursive: true });
const children = [], logs = [], errors = [], submitted = [];
const report = { source: 'isolated SQLite + real API/Worker/Vite/Chromium + in-process synthetic classifier',
  production_access: false, scenarios: [], measurements: [], errors };
let browser;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label) {
  const end = Date.now() + 40000;
  while (Date.now() < end) { try { if (await check()) return; } catch {} await delay(150); }
  throw new Error('Timeout: ' + label);
}
async function port() {
  const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port; await new Promise(resolve => server.close(resolve)); return value;
}
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(out, name + '.log')); logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', PYTHONPATH: ['.', 'tests', 'src'].join(path.delimiter), ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child); return child;
}
try {
  const apiPort = await port(), webPort = await port();
  const api = `http://127.0.0.1:${apiPort}/api/v1`, web = `http://127.0.0.1:${webPort}`;
  const python = path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  launch(python, ['tests/review_context_browser_fixture.py', '--port', String(apiPort), '--origin', web], 'api');
  launch(process.execPath, ['frontend/node_modules/vite/bin/vite.js', 'frontend', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: api });
  await until(async () => (await fetch(api + '/health')).ok && (await fetch(web)).ok, 'services');
  const options = (await (await fetch(api + '/options')).json()).result;
  const profile = options.profiles.find(row => row.requested_model === 'SIMULATED-model-A');
  assert.ok(profile);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  async function context() {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
    await ctx.addInitScript(({ api, profile }) => {
      const key = `review-workbench:${api}`;
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
        dataset: JSON.stringify(['reviews_csv', 'completion-fixture']), productIds: [], profileId: profile.profile_id,
        current: ['2023-02-01', '2023-02-28'], baseline: ['2023-01-01', '2023-01-31'], note: '', rangeConfirmed: true,
      } }));
    }, { api, profile });
    const page = await ctx.newPage(); page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => { if (req.url() === api + '/tasks' && req.method() === 'POST') submitted.push(req.postDataJSON()); });
    return { ctx, page };
  }
  const first = await context(), page = first.page;
  await page.goto(web + '/assistant');
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  async function ask(page, question) {
    await page.getByRole('textbox', { name: '评论分析问题' }).fill(question);
    const response = page.waitForResponse(r => r.url() === api + '/tasks' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    const body = await (await response).json(); assert.equal(body.status, 'ok', JSON.stringify(body));
    const id = body.result.task.task_id; let task;
    await until(async () => { task = (await (await fetch(api + '/tasks/' + id)).json()).result; return !['queued', 'running'].includes(task.status); }, question);
    assert.notEqual(task.status, 'failed', JSON.stringify(task.error));
    await until(async () => (await page.locator('.chat-turn').last().getByRole('region', { name: '分析回答' }).innerText()).includes(task.result.public_answer.summary), 'current turn answer rendering');
    await delay(150);
    report.scenarios.push({ question, task_id: id, status: task.result.status, products: task.result.scope.product_ids,
      acceptance: task.result.task_acceptance?.status, tools: task.result.result_ids?.map(id => task.result.results[id].kind) });
    return task;
  }
  const brand = await ask(page, '小米空调的物流差评主要体现在哪里？');
  assert.equal(brand.result.status, 'unsupported'); assert.ok(brand.result.answer.includes('共 5 款'));
  const sku = await ask(page, '100010219881物流差评主要体现在哪里？');
  assert.deepEqual(sku.result.scope.product_ids, ['100010219881']); assert.equal(sku.result.task_acceptance.status, 'passed');
  const topic = sku.result.results.r1.data.topics.find(row => row.name === '模拟配送迟延');
  assert.equal(topic.review_count, 2); assert.equal(topic.opinion_count, 3);
  const followup = await ask(page, '给出负面的物流观点的体现在哪里');
  assert.deepEqual(followup.result.scope.product_ids, ['100010219881']);
  assert.equal(submitted.at(-1).parameters.context_task_id, sku.task_id);
  const switched = await ask(page, '100018015886物流差评主要体现在哪里？');
  assert.deepEqual(switched.result.scope.product_ids, ['100018015886']);
  const empty = await ask(page, '100021755350物流差评主要体现在哪里？');
  assert.equal(empty.result.task_acceptance.status, 'insufficient_evidence'); assert.equal(empty.result.task_acceptance.complete, false);
  await page.getByRole('button', { name: /新建会话/ }).click();
  const independent = await ask(page, '物流差评都在说什么？');
  assert.equal(independent.result.scope.product_ids.length, 5);
  assert.equal(submitted.at(-1).parameters.context_task_id, undefined);
  assert.notEqual(independent.parameters.conversation_id, sku.parameters.conversation_id);
  const restored = await context(), restoredPage = restored.page;
  await restoredPage.goto(web + '/assistant/tasks/' + sku.task_id);
  await restoredPage.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await restoredPage.getByRole('region', { name: '分析回答' }).last().waitFor();
  const resumed = await ask(restoredPage, '具体体现在哪里？');
  assert.deepEqual(resumed.result.scope.product_ids, ['100010219881']);
  assert.equal(submitted.at(-1).parameters.context_task_id, sku.task_id);
  const short = await ask(restoredPage, '那物流方面呢');
  assert.deepEqual(short.result.scope.product_ids, ['100010219881']);
  assert.equal(short.result.task_acceptance.status, 'passed');
  assert.equal(submitted.at(-1).parameters.context_task_id, resumed.task_id);
  assert.ok(submitted.at(-1).parameters.question.startsWith('执行本轮已确认的只读查询'));
  const turn = restoredPage.locator('.chat-turn').last();
  assert.ok((await turn.getByLabel('本次问题的简要范围').innerText()).includes('100010219881'));
  await restoredPage.locator('details.chat-report > summary').last().click();
  await turn.locator('details.agent-audit > summary').click();
  await until(() => turn.getByText('本轮任务验收：通过', { exact: true }).isVisible(), 'visible task acceptance');
  for (const width of [1440, 1024, 768]) {
    await restoredPage.setViewportSize({ width, height: 1000 });
    await restoredPage.getByRole('region', { name: '分析回答' }).last().scrollIntoViewIfNeeded();
    await restoredPage.screenshot({ path: path.join(out, `actual-${width}.png`), animations: 'disabled' });
    const measure = await restoredPage.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      inputBottom: document.querySelector('.chat-input-area').getBoundingClientRect().bottom }));
    assert.ok(measure.documentWidth <= width + 1, JSON.stringify(measure)); assert.ok(measure.inputBottom <= 1001);
    report.measurements.push(measure);
  }
  await turn.locator('.ant-table-wrapper').first().scrollIntoViewIfNeeded();
  await restoredPage.screenshot({ path: path.join(out, 'topics-768.png'), animations: 'disabled' });
  await turn.getByText('本轮任务验收：通过', { exact: true }).scrollIntoViewIfNeeded();
  await restoredPage.screenshot({ path: path.join(out, 'task-acceptance-768.png'), animations: 'disabled' });
  const download = restoredPage.waitForEvent('download');
  await restoredPage.getByRole('button', { name: '导出分析报告', exact: true }).last().click();
  await (await download).saveAs(path.join(out, 'candidate-report.md'));
  const text = await readFile(path.join(out, 'candidate-report.md'), 'utf8');
  assert.ok(text.includes('模拟配送迟延') && text.includes('## 任务级验收'));
  report.metrics = await (await fetch(`http://127.0.0.1:${apiPort}/offline-metrics`)).json();
  assert.equal(report.metrics.sdk_attempts, 0); assert.equal(report.metrics.embedding_attempts, 0);
  assert.deepEqual(errors, []); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = String(error); process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  for (const child of children.reverse()) {
    if (process.platform === 'win32') await promisify(execFile)('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true }).catch(() => {});
    else child.kill('SIGTERM');
  }
  for (const log of logs) log.end();
  await writeFile(path.join(out, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
