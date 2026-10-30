// 隔离数据库、本机模拟模型与真实 Vite 页面；不读取生产数据或调用付费模型。
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(frontend);
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const artifacts = await mkdtemp(path.join(root, 'output/playwright/assistant-'));
const python = process.env.E2E_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const database = process.env.E2E_DATABASE_URL;
if (database && !new URL(database).pathname.startsWith('/agent_test_')) throw new Error('Use an isolated agent_test_ database');
const children = [], logs = [], errors = [], screenshots = [], requests = [];
let browser, modelServer, activePage;
let basicCalls = 0, analysisCalls = 0;
const report = { source: `isolated ${database ? 'PostgreSQL' : 'SQLite'} + simulated local model + real Vite/Chromium`, artifacts, screenshots, errors };
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 30_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { if (await check()) return; } catch {} await delay(150); }
  throw new Error(`Timed out: ${label}`);
}
async function port() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}
async function choose(page, name, label) {
  const input = page.getByRole('combobox', { name, exact: true });
  await page.locator('.ant-select').filter({ has: input }).click();
  await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option').filter({ hasText: label }).click();
  await input.press('Escape');
}
async function dates(page, name, from, to) {
  const inputs = page.locator('.ant-picker').filter({ has: page.getByRole('textbox', { name, exact: true }) }).locator('input');
  await inputs.nth(0).fill(from); await inputs.nth(0).press('Enter');
  await inputs.nth(1).fill(to); await inputs.nth(1).press('Enter');
  await inputs.nth(1).press('Escape');
}
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(artifacts, `${name}.log`));
  logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', LABEL_EXECUTION_MODE: 'local', ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
  return child;
}
async function capture(page, name, target) {
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    if (target) await target.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifacts, `${name}-${width}.png`), animations: 'disabled' });
    const measure = await page.evaluate(() => {
      const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
      const messages = document.querySelector('.chat-messages');
      const outer = document.querySelector('.workspace-content');
      return { width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth,
        documentHeight: document.scrollingElement.scrollHeight, input: rect('.chat-input-area'),
        box: rect('.chat-input-box'), toolbar: rect('.chat-input-toolbar'), stack: rect('.chat-message-stack'),
        messagesOverflow: messages.scrollWidth > messages.clientWidth + 1,
        outerOverflow: outer.scrollHeight > outer.clientHeight + 1,
        dialogs: document.querySelectorAll('[role="dialog"]').length,
        scope: document.querySelector('.assistant-scope-bar') ? rect('.assistant-scope-bar') : null,
        primary: getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim() };
    });
    assert.ok(measure.documentWidth <= width + 1, `${name}: horizontal overflow ${width}`);
    assert.ok(!measure.messagesOverflow && !measure.outerOverflow, `${name}: overflow ${width}`);
    assert.ok(measure.documentHeight <= measure.height + 1, `${name}: document scroll ${width}`);
    assert.ok(measure.input.y + measure.input.height <= measure.height + 1, 'composer inside viewport');
    if (measure.scope) {
      assert.ok(measure.scope.y + measure.scope.height <= measure.input.y + 1, 'scope above composer');
      assert.ok(measure.scope.y >= 0 && measure.scope.x >= 0, 'scope inside viewport');
    }
    assert.ok(Math.abs(measure.stack.x + measure.stack.width / 2 - measure.box.x - measure.box.width / 2) <= 1, 'composer alignment');
    assert.equal(measure.dialogs, 0);
    screenshots.push({ name, filename: `${name}-${width}.png`, ...measure });
  }
}

try {
  const apiPort = await port(), webPort = await port();
  const apiUrl = `http://127.0.0.1:${apiPort}/api/v1`, web = `http://127.0.0.1:${webPort}`;
  const fixture = launch(python, ['scripts/frontend_fixture.py', '--output', artifacts,
    ...(database ? ['--database-env', 'E2E_DATABASE_URL'] : [])], 'fixture');
  await new Promise((resolve, reject) => { fixture.once('error', reject); fixture.once('exit', code => code === 0 ? resolve() : reject(new Error('Fixture failed'))); });
  await promisify(execFile)(python, ['-c', `
import json, os, sys
from pathlib import Path
from data_analysis_agent.reviews.store import import_amazon_jsonl
from data_analysis_agent.reviews.label_store import pending_reviews, save_annotation
root = Path(sys.argv[1]); db = os.environ.get('E2E_DATABASE_URL') or root/'reviews.sqlite3'
rows = [json.loads(line) for line in (root/'simulated.jsonl').read_text(encoding='utf-8').splitlines()]
path = root/'scope-b.jsonl'; path.write_text('\\n'.join(json.dumps(row) for row in rows[24:27]), encoding='utf-8')
import_amazon_jsonl(db, reviews_path=path, dataset_version='SIMULATED-scope-b')
identity = dict(model='SIMULATED-ANNOTATIONS', base_url='https://offline.invalid/v1', prompt_version='fixture-v1')
for review in pending_reviews(db, **identity, limit=1000):
    save_annotation(db, review, **identity, status='success', opinions=[])
for review in pending_reviews(db, **dict(identity, model='SIMULATED-SECOND'), product_ids=['DEMO-A'], date_from='2023-02-01', date_to='2023-02-02', limit=1000):
    save_annotation(db, review, **dict(identity, model='SIMULATED-SECOND'), status='success', opinions=[])
`, artifacts], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } });
  modelServer = createServer(async (request, response) => {
    let raw = ''; for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    assert.equal(body.model, 'SIMULATED-CHAT');
    let message;
    if (body.tools) {
      analysisCalls++;
      const question = body.messages.find(item => item.role === 'user').content.split('\n\n<bound_context>')[0];
      const plan = JSON.parse(body.messages.find(item => item.role === 'user').content.split('<query_plan>\n')[1].split('\n</query_plan>')[0]);
      const selectedTools = plan.requirements.map(query => [query.tool, query.parameters]);
      const ids = body.messages.filter(item => item.role === 'tool').map(item => JSON.parse(item.content).data?.result_id).filter(Boolean);
      message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
        : { role: 'assistant', content: null, tool_calls: selectedTools.map(([name, parameters], i) => ({ id: `tool-${i}`, type: 'function', function: { name, arguments: JSON.stringify(parameters) } })) };
    } else {
      basicCalls++;
      assert.equal(body.messages.length, 2);
      assert.equal(body.messages[0].role, 'system');
      assert.equal(body.messages[1].role, 'user');
      message = { role: 'assistant', content: '**基础模式测试回答**：不读取业务数据。\n\n- 本机模拟模型，仅用于界面验收。' };
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ id: `fixture-${basicCalls}-${analysisCalls}`, object: 'chat.completion', created: 0,
      model: body.model, choices: [{ index: 0, finish_reason: message.tool_calls ? 'tool_calls' : 'stop', message }],
      usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } }));
  });
  await new Promise(resolve => modelServer.listen(0, '127.0.0.1', resolve));
  launch(python, ['-m', 'data_analysis_agent.cli.server', 'serve', '--db', database ? 'env:E2E_DATABASE_URL' : path.join(artifacts, 'reviews.sqlite3'),
    '--port', String(apiPort), '--env-file', path.join(artifacts, 'unused.env'), '--import-root', artifacts, '--allow-origin', web], 'api', {
    LLM_MODEL_ID: 'SIMULATED-CHAT', LLM_API_KEY: 'SIMULATED-key',
    LLM_BASE_URL: `http://127.0.0.1:${modelServer.address().port}/v1`, LLM_MAX_RETRIES: '0',
  });
  launch(process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), frontend, '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: apiUrl });
  await until(async () => (await fetch(`${apiUrl}/health`, { signal: AbortSignal.timeout(1000) })).ok, 'API ready');
  await until(async () => (await fetch(web, { signal: AbortSignal.timeout(1000) })).ok, 'Vite ready');
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const basicContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await basicContext.newPage();
  activePage = page;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().startsWith(apiUrl)) requests.push({ method: request.method(), path: new URL(request.url()).pathname, body: request.postData() }); });
  // 只在测试浏览器里构造没有分析范围的边界状态，API 和模型仍执行真实链路。
  await page.route(`${apiUrl}/options`, route => route.fulfill({ json: { status: 'ok', result: {
    profiles: [], selected_profile_id: null, products: [], aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {},
  } } }));
  await page.goto(`${web}/assistant`);
  await page.getByLabel('问答模式').getByText('基础问答', { exact: true }).waitFor();
  assert.equal(await page.getByRole('radio', { name: '基础问答', exact: true }).isChecked(), true);
  const question = '差评率是多少？（测试基础模式不查询业务）';
  await page.getByRole('textbox', { name: '基础问答问题' }).fill(question);
  await page.getByRole('textbox', { name: '基础问答问题' }).press('Enter');
  await page.getByText('基础模式测试回答', { exact: true }).waitFor();
  assert.equal(basicCalls, 1);
  assert.deepEqual(JSON.parse(requests.find(item => item.path.endsWith('/chat') && item.method === 'POST').body), { message: question });
  assert.ok(!requests.some(item => (item.method === 'POST' && item.path.endsWith('/tasks'))
    || /\/tasks\//.test(item.path)), 'basic question must not submit or poll tasks');
  assert.equal(await page.locator('.chat-report, .chat-turn-scope').count(), 0);
  assert.ok(!(await page.evaluate(() => Object.values(localStorage).join(''))).includes(question), 'basic draft is memory only');
  await capture(page, 'basic-answer');
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await page.getByText('请先选择可用标注版本与分析范围', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '提交问题', exact: true }).isDisabled(), true);
  await capture(page, 'analysis-missing-scope');
  await page.getByLabel('问答模式').getByText('基础问答', { exact: true }).click();
  await page.route(`${apiUrl}/chat`, route => route.abort());
  await page.getByRole('textbox', { name: '基础问答问题' }).fill('失败后重发');
  await page.getByRole('button', { name: '提交问题', exact: true }).click();
  await page.getByText('回答未完成', { exact: true }).waitFor();
  await page.unroute(`${apiUrl}/chat`);
  await page.getByRole('button', { name: '重新发送', exact: true }).click();
  await until(async () => await page.locator('.chat-markdown strong').count() === 2, 'basic resend');
  assert.equal(basicCalls, 2);
  await page.reload();
  await page.getByRole('textbox', { name: '基础问答问题' }).waitFor();
  assert.equal(await page.locator('.chat-turn').count(), 0);
  assert.equal(await page.getByRole('textbox', { name: '基础问答问题' }).inputValue(), '');
  assert.equal(basicCalls, 2);
  await basicContext.close();

  const options = (await (await fetch(`${apiUrl}/options`)).json()).result;
  const analysisContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await analysisContext.addInitScript(({ key, profileId }) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify(['amazon_reviews_2023', 'SIMULATED-frontend']), productIds: ['DEMO-A'], profileId,
      current: ['2023-02-01', '2023-02-24'], baseline: ['2023-01-01', '2023-01-31'], note: '',
    } }));
  }, { key: `review-workbench:${apiUrl}`, profileId: options.profiles.find(item => item.requested_model === 'SIMULATED-ANNOTATIONS').profile_id });
  const analysis = await analysisContext.newPage();
  activePage = analysis;
  analysis.on('pageerror', error => errors.push(error.message));
  await analysis.goto(`${web}/assistant`);
  await analysis.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await analysis.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手标注版本', 'SIMULATED-ANNOTATIONS');
  await analysis.getByRole('button', { name: '应用范围', exact: true }).click();
  await analysis.getByText(/覆盖率 100.0%/).waitFor();
  const catalogSummary = analysis.getByRole('region', { name: '数据集摘要', exact: true });
  assert.match(await catalogSummary.innerText(), /评论数\s+96/);
  assert.match(await catalogSummary.innerText(), /商品数\s+2/);
  await catalogSummary.locator('summary').click();
  assert.match(await catalogSummary.getByRole('row').filter({hasText:'SIMULATED-SECOND'}).innerText(), /2.1%/);
  await capture(analysis, 'dataset-profile-summary');
  await catalogSummary.locator('summary').click();
  await analysis.getByRole('textbox', { name: '评论分析问题' }).fill('数据质量、包装按月趋势、星级分布、比较当前窗口与基线、包装负面原文');
  await analysis.getByRole('button', { name: '提交问题', exact: true }).click();
  await analysis.locator('.chat-report > summary').waitFor();
  await analysis.locator('.chat-report > summary').click();
  await analysis.locator('.agent-evidence-list blockquote').first().waitFor();
  assert.equal(analysisCalls, 2);
  assert.equal(basicCalls, 2);
  assert.ok(await analysis.locator('.chat-report table').count() > 0);
  await capture(analysis, 'analysis-result');
  for (const [title, name] of [['数据质量', 'quality'], ['维度趋势', 'aspect-trend'], ['星级分布', 'ratings'], ['时间窗口对比', 'windows']]) {
    const card = analysis.locator('.chat-report .ant-card').filter({ has: analysis.locator('.ant-card-head-title').filter({ hasText: title }) });
    assert.equal(await card.count(), 1);
    assert.ok(await card.locator('table').count() > 0);
    await capture(analysis, `capability-${name}`, card);
  }
  const download = analysis.waitForEvent('download');
  await analysis.getByRole('button', { name: '导出分析报告', exact: true }).first().click();
  const downloaded = await download;
  assert.ok(downloaded.suggestedFilename().endsWith('.md'));
  const reportPath = path.join(artifacts, 'analysis-report.md');
  await downloaded.saveAs(reportPath);
  const exported = await readFile(reportPath, 'utf8');
  for (const text of ['SIMULATED-frontend', '2023-02-01 — 2023-02-24', '2023-01-01 — 2023-01-31',
    'review-agent-v5', 'file_sha256', 'sha256:', 'rating_rate_denominator', '原始星级']) assert.ok(exported.includes(text), text);
  await analysis.setViewportSize({ width: 1440, height: 900 });
  const firstRange = await analysis.locator('.chat-turn-scope').first().innerText();
  const firstTurn = await analysis.evaluate(() => JSON.parse(localStorage[Object.keys(localStorage).find(key => key.startsWith('review-agent-chat:'))]).turns[0]);
  const scope = analysis.getByRole('region', { name: '助手分析范围', exact: true });
  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  assert.equal(await analysis.getByRole('button', { name: '提交问题', exact: true }).isDisabled(), true);
  await capture(analysis, 'scope-editor');
  for (const width of [1440, 1024, 768, 390]) {
    await analysis.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await analysis.getByRole('textbox', { name: '助手当前窗口', exact: true }).first().click();
    const calendar = analysis.locator('.assistant-scope-calendar:not(.ant-picker-dropdown-hidden)');
    await calendar.waitFor();
    const bounds = await calendar.boundingBox();
    assert.ok(bounds.x >= -1 && bounds.x + bounds.width <= width + 1, 'calendar inside viewport');
    await analysis.screenshot({ path: path.join(artifacts, `scope-calendar-${width}.png`), animations: 'disabled' });
    screenshots.push({ name: 'scope-calendar', filename: `scope-calendar-${width}.png`, width, bounds });
    await analysis.getByRole('textbox', { name: '助手当前窗口', exact: true }).first().press('Escape');
  }
  await analysis.setViewportSize({ width: 1440, height: 900 });
  await choose(analysis, '助手数据集', 'SIMULATED-scope-b');
  await scope.getByRole('button', { name: '取消', exact: true }).click();
  assert.match(await scope.innerText(), /SIMULATED-frontend/);
  assert.equal(await analysis.locator('.chat-turn-scope').first().innerText(), firstRange);

  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手标注版本', 'SIMULATED-SECOND');
  await choose(analysis, '助手分析商品', 'DEMO-B');
  await dates(analysis, '助手当前窗口', '2023-02-02', '2023-02-20');
  await dates(analysis, '助手基线窗口', '2023-02-01', '2023-02-10');
  assert.equal(await scope.getByRole('button', { name: '应用范围', exact: true }).isDisabled(), true);
  await dates(analysis, '助手基线窗口', '2023-01-05', '2023-01-20');
  await scope.getByRole('button', { name: '应用范围', exact: true }).click();
  await scope.getByText(/覆盖率 2.6%/).waitFor();
  assert.equal(await analysis.locator('.chat-turn-scope').first().innerText(), firstRange);
  async function send(question) {
    const response = analysis.waitForResponse(item => item.url() === `${apiUrl}/tasks` && item.request().method() === 'POST');
    await analysis.getByRole('textbox', { name: '评论分析问题' }).fill(question);
    await analysis.getByRole('button', { name: '提交问题', exact: true }).click();
    const task = (await (await response).json()).result.task;
    await until(async () => {
      const saved = (await (await fetch(`${apiUrl}/tasks/${task.task_id}`)).json()).result;
      assert.notEqual(saved.status, 'failed', JSON.stringify(saved.error));
      return saved.status === 'succeeded';
    }, 'scope task finished');
    await until(async () => !await analysis.getByRole('button', { name: '切换范围', exact: true }).isDisabled(), 'scope task rendered');
    return task;
  }
  const secondTask = await send('按月查看新范围趋势和证据');
  assert.deepEqual(secondTask.parameters.product_ids, ['DEMO-A', 'DEMO-B']);
  assert.equal(secondTask.parameters.model, 'SIMULATED-SECOND');
  assert.equal(secondTask.parameters.date_from, '2023-02-02');
  assert.equal(secondTask.parameters.baseline_to, '2023-01-20');
  await capture(analysis, 'scope-limited-sample');

  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手数据集', 'SIMULATED-scope-b');
  await choose(analysis, '助手标注版本', 'SIMULATED-ANNOTATIONS');
  await scope.getByRole('button', { name: '应用范围', exact: true }).click();
  await scope.getByText(/覆盖率 100.0%/).waitFor();
  assert.equal(await analysis.locator('.header-actions .ant-select-selection-item').textContent(), 'SIMULATED-scope-b');
  const thirdTask = await send('按月查看另一数据集趋势');
  assert.equal(thirdTask.parameters.dataset_version, 'SIMULATED-scope-b');
  assert.equal(thirdTask.parameters.date_from, '2023-02-01');
  assert.equal(thirdTask.parameters.date_to, '2023-02-03');
  assert.deepEqual(thirdTask.parameters.product_ids, []);
  assert.equal(await analysis.locator('.chat-turn-scope').first().innerText(), firstRange);
  await capture(analysis, 'scope-dataset-b');
  const historyDownload = analysis.waitForEvent('download');
  await analysis.getByRole('button', { name: '导出分析报告', exact: true }).first().click();
  const historyPath = path.join(artifacts, 'analysis-report-after-switch.md');
  await (await historyDownload).saveAs(historyPath);
  assert.equal(await readFile(historyPath, 'utf8'), exported, 'historical report must retain its original scope and results');

  await choose(analysis, '全局数据集', 'SIMULATED-frontend');
  await scope.getByText(/覆盖率 100.0%/).waitFor();
  await analysis.route(`${apiUrl}/analytics/trend?*`, route => route.abort());
  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手分析商品', 'DEMO-B');
  await scope.getByRole('button', { name: '应用范围', exact: true }).click();
  await scope.getByText(/覆盖率读取失败/).waitFor();
  await capture(analysis, 'scope-coverage-error');
  await analysis.unroute(`${apiUrl}/analytics/trend?*`);
  await scope.getByRole('button', { name: '重试覆盖率', exact: true }).click();
  await scope.getByText(/覆盖率 100.0%/).waitFor();

  await analysis.reload();
  await analysis.locator('.chat-report').first().waitFor();
  assert.equal(await analysis.getByRole('radio', { name: '基础问答', exact: true }).isChecked(), true);
  assert.equal(analysisCalls, 6, 'restoring analysis must not invoke model again');
  assert.equal(await analysis.locator('.chat-turn-scope').first().innerText(), firstRange);
  await analysis.goto(`${web}/assistant/tasks/${firstTurn.taskId}`);
  await until(async () => await analysis.evaluate(id => JSON.parse(localStorage[Object.keys(localStorage).find(key => key.startsWith('review-agent-chat:'))]).activeId === id, firstTurn.id), 'historical task restored');
  assert.equal(await analysis.locator('.chat-turn-scope').first().innerText(), firstRange);
  assert.equal(analysisCalls, 6);
  await capture(analysis, 'scope-history-restored');
  await analysis.goto(`${web}/assistant`);
  await analysis.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await analysis.setViewportSize({ width: 1440, height: 900 });
  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手标注版本', 'SIMULATED-ANNOTATIONS');
  await dates(analysis, '助手当前窗口', '2023-02-01', '2023-02-24');
  await dates(analysis, '助手基线窗口', '2023-01-01', '2023-01-31');
  await choose(analysis, '助手分析商品', 'DEMO-A');
  await scope.getByRole('button', { name: '应用范围', exact: true }).click();
  await scope.getByText(/覆盖率 100.0%/).waitFor();
  const daily = await send('按日查看包装问题趋势');
  const logistics = await send('只看物流');
  const monthly = await send('换成按月');
  const originals = await send('继续查原文');
  for (const [savedTask, expected] of [[logistics, { aspect: 'logistics', period: 'day' }], [monthly, { aspect: 'logistics', period: 'month' }],
    [originals, { aspect: 'logistics', page: 1 }]]) {
    const saved = (await (await fetch(`${apiUrl}/tasks/${savedTask.task_id}`)).json()).result;
    const queries = Object.values(saved.result.results).filter(item => item.kind !== 'context');
    assert.equal(queries.length, 1);
    for (const [key, value] of Object.entries(expected)) assert.equal(queries[0].parameters[key], value);
  }
  const snapshotsBefore = await analysis.evaluate(() => JSON.parse(localStorage[Object.keys(localStorage).find(key => key.startsWith('review-agent-chat:'))]).turns);
  const monthlyTurn = snapshotsBefore.find(turn => turn.taskId === monthly.task_id);
  assert.equal(monthlyTurn.query.parentTaskId, logistics.task_id);
  const lastTurn = analysis.locator('.chat-turn').last();
  await lastTurn.locator('.chat-query-snapshot summary').click();
  await capture(analysis, 'followup-snapshot', lastTurn.locator('.chat-query-snapshot'));
  const evidenceFirst = await send('查询包装负面评论的原文');
  const evidenceNext = await send('继续查原文');
  const firstResult = (await (await fetch(`${apiUrl}/tasks/${evidenceFirst.task_id}`)).json()).result.result;
  const nextResult = (await (await fetch(`${apiUrl}/tasks/${evidenceNext.task_id}`)).json()).result.result;
  const firstEvidence = Object.values(firstResult.results).find(item => item.kind === 'evidence');
  const nextEvidence = Object.values(nextResult.results).find(item => item.kind === 'evidence');
  assert.equal(nextEvidence.data.page, 2);
  assert.ok(!nextEvidence.data.items.some(item => firstEvidence.data.items.some(previous => previous.review_id === item.review_id)));
  // 改变范围后必须先澄清，刷新也不能自动提交。
  await scope.getByRole('button', { name: '切换范围', exact: true }).click();
  await choose(analysis, '助手数据集', 'SIMULATED-scope-b');
  await scope.getByRole('button', { name: '应用范围', exact: true }).click();
  const beforeClarification = analysisCalls;
  await analysis.getByRole('textbox', { name: '评论分析问题' }).fill('只看物流');
  await analysis.getByRole('button', { name: '提交问题', exact: true }).click();
  await analysis.getByText('需要澄清', { exact: true }).last().waitFor();
  assert.equal(analysisCalls, beforeClarification);
  await capture(analysis, 'followup-clarification', analysis.locator('.chat-turn').last().locator('.chat-clarification-choices'));
  for (const width of [1440, 1024, 768, 390]) {
    await analysis.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const choice = analysis.getByRole('button', { name: /使用当前范围 · 原文证据/ });
    await choice.scrollIntoViewIfNeeded();
    assert.ok(await choice.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    }), `clarification choice unobstructed at ${width}`);
  }
  await analysis.reload();
  await analysis.getByText('需要澄清', { exact: true }).last().waitFor();
  assert.equal(analysisCalls, beforeClarification);
  const restoredTurns = await analysis.evaluate(() => JSON.parse(localStorage[Object.keys(localStorage).find(key => key.startsWith('review-agent-chat:'))]).turns);
  assert.deepEqual(restoredTurns.find(turn => turn.taskId === monthly.task_id), monthlyTurn);
  await analysis.getByRole('button', { name: /使用当前范围 · 原文证据/ }).click();
  await until(() => analysisCalls === beforeClarification + 2, 'clarified task executed');
  await until(async () => await analysis.locator('.chat-turn').last().locator('.chat-report').count() === 1, 'clarified result rendered');
  const confirmed = await analysis.evaluate(() => JSON.parse(localStorage[Object.keys(localStorage).find(key => key.startsWith('review-agent-chat:'))]).turns.at(-1));
  assert.equal(confirmed.parameters.dataset_version, 'SIMULATED-scope-b');
  assert.equal(confirmed.query.queries[0].parameters.page, 1);
  report.followups = { daily: daily.task_id, logistics: logistics.task_id, monthly: monthly.task_id, originals: originals.task_id,
    evidenceFirst: evidenceFirst.task_id, evidenceNext: evidenceNext.task_id, clarified: confirmed.taskId };
  report.scopeTasks = [firstTurn, secondTask, thirdTask].map(task => ({ id: task.task_id ?? task.taskId, parameters: task.parameters }));
  // 阶段 2：真实本机 API/Worker/模拟模型生成正向结果，受控任务响应验证保守终态展示。
  await analysis.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  const positiveTask = await send('好评主要集中在哪些方面？');
  const savedPositive = (await (await fetch(`${apiUrl}/tasks/${positiveTask.task_id}`)).json()).result;
  assert.equal(savedPositive.status, 'succeeded');
  assert.equal(savedPositive.result.model_tool_attempts, 1);
  assert.equal(savedPositive.result.query_executions, 2);
  assert.equal(savedPositive.result.results.r1.kind, 'positive_aspects');
  assert.ok(!Object.values(savedPositive.result.results).some(item => item.kind === 'evidence'));
  const last = analysis.locator('.chat-turn').last();
  await last.locator('.chat-report > summary').click();
  await last.getByRole('columnheader', { name: '占正向观点评论比例', exact: true }).waitFor();
  await capture(analysis, 'phase2-positive', last.getByRole('columnheader', { name: '占正向观点评论比例', exact: true }));
  for (const status of ['failed', 'partial']) {
    await analysis.route(`${apiUrl}/tasks/${positiveTask.task_id}`, route => route.fulfill({ json: { status: 'ok', result: {
      ...savedPositive, status, error: { code: 'timeout', message: '模拟工具完成后超时' },
    } } }));
    await analysis.reload();
    await analysis.locator('.chat-turn').last().getByRole('button', { name: '重试任务', exact: true }).waitFor();
    assert.equal(await analysis.locator('.chat-turn').last().locator('.chat-report').count(), 0);
    assert.equal(await analysis.locator('.chat-turn').last().getByRole('button', { name: '导出分析报告' }).count(), 0);
    await capture(analysis, `phase2-${status}`, analysis.locator('.chat-turn').last());
    await analysis.unroute(`${apiUrl}/tasks/${positiveTask.task_id}`);
  }
  report.phase2 = { positiveTask: savedPositive.task_id, modelToolAttempts: savedPositive.result.model_tool_attempts,
    queryExecutions: savedPositive.result.query_executions, statusViews: 'controlled failed/partial API responses; actual backend failure verified by Python tests' };
  await analysisContext.close();
  assert.deepEqual(errors, []);
  report.status = 'passed';
  report.checks = ['basic without scope', 'no keyword routing', 'Markdown answer', 'no basic task polling',
    'basic failure and resend', 'memory-only messages and draft', 'analysis scope requirement',
    'existing analysis tools, tables and evidence', 'data quality, aspect trends, ratings, window comparisons',
    'Markdown download with scope, model identities, source hashes and frozen historical results',
    'analysis refresh restore without model call', 'responsive screenshots',
    'staged scope editing and cancellation', 'overlapping baseline blocked', 'product multi-selection and profile-specific coverage',
    'dataset and global selection synchronization', 'scope snapshots unchanged across changes and URL restore', 'coverage error and retry',
    'followup aspect/month/evidence chain', 'evidence next page without duplicate reviews', 'clarification before scope change',
    'clarification and query snapshots survive refresh without submitting', 'confirmed scope applies to task'];
} catch (error) {
  report.status = 'failed'; report.error = String(error); process.exitCode = 1;
  if (activePage && !activePage.isClosed()) await activePage.screenshot({ path: path.join(artifacts, 'failure.png') }).catch(() => {});
} finally {
  if (browser) await browser.close();
  for (const child of children.filter(child => child.pid && child.exitCode === null)) {
    if (process.platform === 'win32') await promisify(execFile)('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).catch(() => {});
    else child.kill();
  }
  if (modelServer) { modelServer.closeAllConnections(); await new Promise(resolve => modelServer.close(resolve)); }
  for (const log of logs) log.end();
  report.basicModelCalls = basicCalls; report.analysisModelCalls = analysisCalls;
  await writeFile(path.join(artifacts, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, artifacts, screenshots: screenshots.length, checks: report.checks,
    basicModelCalls: basicCalls, analysisModelCalls: analysisCalls, errors, error: report.error }, null, 2));
}
