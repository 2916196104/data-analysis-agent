// 真实 API、Worker、Vite 和 Chromium；隔离数据库与本机模拟模型。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '../..');
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const out = await mkdtemp(path.join(root, 'output/playwright/sessions-'));
const python = path.join(root, '.venv/Scripts/python.exe');
const children = [], logs = [], errors = [], screenshots = [], submissions = [];
const report = { source: 'isolated SQLite + local simulated model + real FastAPI/Worker/Vite/Chromium', out, screenshots, errors };
let browser, page, modelServer, releaseModel;
let holdModel = true;
const modelGate = new Promise(resolve => { releaseModel = resolve; });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, name, timeout = 45000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) { if (await check()) return; await delay(200); }
  throw new Error(`Timeout: ${name}`);
}
async function port() { const s = net.createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p; }
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(out, name + '.log')); logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1', LABEL_EXECUTION_MODE: 'local', ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child); return child;
}
try {
  const fixture = launch(python, ['scripts/frontend_fixture.py', '--output', out], 'fixture');
  await new Promise((resolve, reject) => { fixture.once('error', reject); fixture.once('exit', code => code === 0 ? resolve() : reject(new Error('Fixture failed'))); });
  modelServer = createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    if (holdModel) { holdModel = false; await modelGate; }
    let message;
    if (body.tools) {
      const plan = JSON.parse(body.messages.find(item => item.role === 'user').content.split('<query_plan>\n')[1].split('\n</query_plan>')[0]);
      const ids = body.messages.filter(item => item.role === 'tool').map(item => JSON.parse(item.content).data?.result_id).filter(Boolean);
      message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
        : { role: 'assistant', content: null, tool_calls: plan.requirements.map((query, i) => ({ id: `t-${i}`, type: 'function', function: { name: query.tool, arguments: JSON.stringify(query.parameters) } })) };
    } else if (body.response_format?.type === 'json_object') {
      const payload = JSON.parse(body.messages.at(-1).content);
      message = { role: 'assistant', content: JSON.stringify(payload.stage === 'answer_draft'
        ? { schema_version: '2.0', focus: payload.required_focus, insights: [] }
        : { summary: payload.summary_options[0], items: payload.findings.map(fact => ({ finding_ref: fact.finding_ref, aspect_key: fact.aspect_key, explanation: fact.statements[0] })), caveats: [] }) };
    } else message = { role: 'assistant', content: '隔离测试回答：' + body.messages.at(-1).content };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ id: 'local-test', object: 'chat.completion', created: 0, model: body.model,
      choices: [{ index: 0, finish_reason: message.tool_calls ? 'tool_calls' : 'stop', message }], usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } }));
  });
  await new Promise(resolve => modelServer.listen(0, '127.0.0.1', resolve));
  const apiPort = await port(), webPort = await port(), web = `http://127.0.0.1:${webPort}`, api = `http://127.0.0.1:${apiPort}/api/v1`;
  const chatKey = `review-agent-chat:${api}`, draftKey = `review-workbench:${api}`;
  launch(python, ['-m', 'data_analysis_agent.cli.server', 'serve', '--db', path.join(out, 'reviews.sqlite3'), '--port', String(apiPort),
    '--env-file', path.join(out, 'unused.env'), '--import-root', out, '--allow-origin', web], 'api', {
    LLM_MODEL_ID: 'SIMULATED-CHAT', LLM_API_KEY: 'SIMULATED-key', LLM_BASE_URL: `http://127.0.0.1:${modelServer.address().port}/v1`,
    LLM_MAX_RETRIES: '0', ANSWER_TIMEOUT_SECONDS: '45', ANSWER_MAX_RETRIES: '0', AGENT_SEMANTIC_PARSING: 'rules_only',
  });
  launch(process.execPath, ['frontend/node_modules/vite/bin/vite.js', 'frontend', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: api });
  await until(async () => { try { return (await fetch(api + '/health', { signal: AbortSignal.timeout(1000) })).ok && (await fetch(web, { signal: AbortSignal.timeout(1000) })).ok; } catch { return false; } }, 'services');
  const options = (await (await fetch(api + '/options')).json()).result;
  const profile = options.profiles.find(item => item.requested_model === 'SIMULATED-ANNOTATIONS');
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(({ draftKey, profile }) => {
    if (!localStorage.getItem(draftKey)) localStorage.setItem(draftKey, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify(['amazon_reviews_2023', 'SIMULATED-frontend']), productIds: ['DEMO-A'], profileId: profile.profile_id,
      current: ['2023-02-01', '2023-02-24'], baseline: ['2023-01-01', '2023-01-31'], note: '', rangeConfirmed: true,
    } }));
  }, { draftKey, profile });
  page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
  page.on('request', req => { if (req.method() === 'POST' && req.url() === api + '/tasks') submissions.push(req.postDataJSON()); });
  const store = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), chatKey);
  const selected = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)).draft.productIds, draftKey);
  async function analysisMode() { await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click(); }
  async function enter(question) { await page.getByRole('textbox', { name: '评论分析问题' }).fill(question); await page.getByRole('button', { name: '提交问题', exact: true }).click(); }
  async function switchTo(title) {
    await page.getByRole('button', { name: '历史会话', exact: true }).click();
    await page.getByRole('navigation', { name: '历史会话列表' }).getByRole('button').filter({ hasText: title }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
  }
  async function capture(name, drawer = false) {
    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      if (name === 'analysis-history') await page.locator('.chat-report .ant-table').first().scrollIntoViewIfNeeded();
      if (name === 'scope-isolation') await page.getByText(/当前选择范围与历史分析不同/).scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `${name}-${width}.png`), animations: 'disabled' });
      const measure = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
        messageWidth: document.querySelector('.chat-messages').scrollWidth, clientWidth: document.querySelector('.chat-messages').clientWidth,
        inputBottom: document.querySelector('.chat-input-area').getBoundingClientRect().bottom,
        drawer: [...document.querySelectorAll('[role="dialog"]')].some(node => node.getBoundingClientRect().width > 0),
        titlesFit: [...document.querySelectorAll('.chat-session-item')].every(node => node.scrollWidth <= node.clientWidth + 1) }));
      assert.ok(measure.documentWidth <= width + 1 && measure.messageWidth <= measure.clientWidth + 1, JSON.stringify(measure));
      assert.ok(measure.inputBottom <= 901 && measure.titlesFit); assert.equal(measure.drawer, drawer);
      screenshots.push({ name: `${name}-${width}.png`, ...measure });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page.goto(web + '/assistant'); await analysisMode();
  const questionA = '按月查看包装问题趋势';
  await enter(questionA);
  await until(async () => (await store())?.sessions[0].turns[0]?.taskId, 'A submitted');
  const stateA = await store(), a = stateA.activeSessionId, taskA = stateA.sessions[0].turns[0].taskId;
  const getTask = async id => (await (await fetch(api + '/tasks/' + id)).json()).result;
  assert.ok(['queued', 'running'].includes((await getTask(taskA)).status), 'A still executing');
  await page.getByRole('button', { name: '新建会话', exact: true }).click();
  assert.equal(await page.locator('.chat-turn').count(), 0); assert.deepEqual(await selected(), ['DEMO-A']);
  const b = (await store()).activeSessionId; assert.notEqual(a, b);
  await page.getByRole('button', { name: '新建会话', exact: true }).click(); assert.equal((await store()).sessions.length, 2);
  await enter('那物流呢');
  await page.getByText('需要澄清', { exact: true }).waitFor(); assert.equal(submissions.length, 1);
  releaseModel();
  await until(async () => (await getTask(taskA)).status === 'succeeded', 'A completes in background');
  assert.equal((await store()).activeSessionId, b); assert.equal(await page.locator('.chat-turn').count(), 1);
  assert.equal(await page.getByRole('textbox', { name: '评论分析问题' }).isDisabled(), false);
  await enter('查看星级分布');
  await until(async () => (await store()).sessions.find(s => s.id === b).turns.at(-1)?.taskId, 'B submitted');
  const taskB = (await store()).sessions.find(s => s.id === b).turns.at(-1).taskId;
  await until(async () => (await getTask(taskB)).status === 'succeeded', 'B completed');
  assert.equal(submissions[1].parameters.conversation_id, b); assert.equal(submissions[1].parameters.context_task_id, undefined);
  assert.deepEqual(submissions[1].parameters.product_ids, ['DEMO-A']);
  await switchTo(questionA); await page.locator('.chat-report').waitFor(); assert.equal(await page.locator('.chat-turn').count(), 1);
  await page.locator('.chat-report > summary').click(); await page.locator('.chat-report table').first().waitFor();
  await capture('analysis-history');
  await page.getByRole('button', { name: '历史会话', exact: true }).click(); await page.getByRole('dialog').waitFor();
  await capture('history-drawer', true); await page.keyboard.press('Escape');
  await switchTo('那物流呢'); await page.reload(); await analysisMode();
  await page.locator('.chat-report').waitFor(); assert.equal((await store()).activeSessionId, b); assert.equal(submissions.length, 2);
  // 切换全局商品，回到 A 后历史任务仍显示原始商品。
  await page.getByRole('button', { name: '修改范围', exact: true }).click();
  const products = page.getByRole('combobox', { name: '助手分析商品', exact: true });
  await page.locator('.ant-select').filter({ has: products }).click();
  const dropdown = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  await dropdown.locator('.ant-select-item-option').filter({ hasText: 'DEMO-A' }).click();
  await dropdown.locator('.ant-select-item-option').filter({ hasText: 'DEMO-B' }).click();
  await products.press('Escape'); await page.getByRole('button', { name: '应用范围', exact: true }).click();
  assert.deepEqual(await selected(), ['DEMO-B']);
  await switchTo(questionA); assert.deepEqual(await selected(), ['DEMO-B']);
  assert.deepEqual((await getTask(taskA)).parameters.product_ids, ['DEMO-A']);
  await enter('那物流呢'); await page.getByText(/当前选择范围与历史分析不同/).waitFor(); assert.equal(submissions.length, 2);
  await capture('scope-isolation');
  await page.getByRole('button', { name: '新建会话', exact: true }).click(); await enter('查看数据质量');
  await until(async () => { const state = await store(); return state.sessions.find(s => s.id === state.activeSessionId)?.turns[0]?.taskId; }, 'new scope submitted');
  assert.deepEqual(submissions.at(-1).parameters.product_ids, ['DEMO-B']);
  // 旧版单会话只迁移任务引用，刷新与新建不会重跑。
  const legacy = { turns: stateA.sessions[0].turns, activeId: stateA.sessions[0].activeId, context: { conversation_id: a, product_ids: ['DEMO-A'], previous_result_refs: [] } };
  await page.evaluate(({ chatKey, legacy }) => localStorage.setItem(chatKey, JSON.stringify(legacy)), { chatKey, legacy });
  const before = submissions.length; await page.reload(); await analysisMode(); await page.locator('.chat-report').waitFor();
  await page.getByRole('button', { name: '新建会话', exact: true }).click();
  assert.equal((await store()).sessions[0].turns[0].taskId, taskA); assert.equal(submissions.length, before);
  assert.ok(await page.evaluate(chatKey => Object.keys(localStorage).some(key => key.startsWith(chatKey + ':backup:')), chatKey));
  assert.deepEqual(errors, []);
  report.status = 'passed'; report.checks = ['A/B/C/D/E/F/G/H/I', 'real task API and worker', 'analysis table rendering', '1440/1024/768/390 layout', 'migration backup'];
  report.submissions = submissions;
} catch (error) {
  report.status = 'failed'; report.error = String(error); process.exitCode = 1;
  if (page) { await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {}); await writeFile(path.join(out, 'failure.txt'), await page.locator('body').innerText().catch(() => '')); }
} finally {
  releaseModel?.(); if (browser) await browser.close();
  for (const child of children.filter(child => child.pid && child.exitCode === null)) {
    await promisify(execFile)('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).catch(() => {});
  }
  if (modelServer) { modelServer.closeAllConnections(); await new Promise(resolve => modelServer.close(resolve)); }
  logs.forEach(log => log.end()); await writeFile(path.join(out, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
