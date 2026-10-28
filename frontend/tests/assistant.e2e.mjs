// 隔离数据库、本机模拟模型与真实 Vite 页面；不读取生产数据或调用付费模型。
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
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
const children = [], logs = [], errors = [], screenshots = [], requests = [];
let browser, modelServer, activePage;
let basicCalls = 0, analysisCalls = 0;
const report = { source: 'isolated SQLite + simulated local model + real Vite/Chromium', artifacts, screenshots, errors };
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
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(artifacts, `${name}.log`));
  logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', LABEL_EXECUTION_MODE: 'local', ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
  return child;
}
async function capture(page, name) {
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
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
        primary: getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim() };
    });
    assert.ok(measure.documentWidth <= width + 1, `${name}: horizontal overflow ${width}`);
    assert.ok(!measure.messagesOverflow && !measure.outerOverflow, `${name}: overflow ${width}`);
    assert.ok(measure.documentHeight <= measure.height + 1, `${name}: document scroll ${width}`);
    assert.ok(measure.input.y + measure.input.height <= measure.height + 1, 'composer inside viewport');
    assert.ok(Math.abs(measure.stack.x + measure.stack.width / 2 - measure.box.x - measure.box.width / 2) <= 1, 'composer alignment');
    assert.equal(measure.dialogs, 0);
    screenshots.push({ name, filename: `${name}-${width}.png`, ...measure });
  }
}

try {
  const apiPort = await port(), webPort = await port();
  const apiUrl = `http://127.0.0.1:${apiPort}/api/v1`, web = `http://127.0.0.1:${webPort}`;
  const fixture = launch(python, ['scripts/frontend_fixture.py', '--output', artifacts], 'fixture');
  await new Promise((resolve, reject) => { fixture.once('error', reject); fixture.once('exit', code => code === 0 ? resolve() : reject(new Error('Fixture failed'))); });
  modelServer = createServer(async (request, response) => {
    let raw = ''; for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    assert.equal(body.model, 'SIMULATED-CHAT');
    let message;
    if (body.tools) {
      analysisCalls++;
      const ids = body.messages.filter(item => item.role === 'tool').map(item => JSON.parse(item.content).data?.result_id).filter(Boolean);
      message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
        : { role: 'assistant', content: null, tool_calls: [
          ['get_trend', { period: 'month' }], ['get_evidence', { aspect: 'packaging', sentiment: 'negative' }],
        ].map(([name, parameters], i) => ({ id: `tool-${i}`, type: 'function', function: { name, arguments: JSON.stringify(parameters) } })) };
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
  launch(python, ['-m', 'data_analysis_agent.cli.server', 'serve', '--db', path.join(artifacts, 'reviews.sqlite3'),
    '--port', String(apiPort), '--env-file', path.join(artifacts, 'unused.env'), '--import-root', artifacts, '--allow-origin', web], 'api', {
    LLM_MODEL_ID: 'SIMULATED-CHAT', LLM_API_KEY: 'SIMULATED-key',
    LLM_BASE_URL: `http://127.0.0.1:${modelServer.address().port}/v1`, LLM_MAX_RETRIES: '0',
  });
  launch(process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), frontend, '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: apiUrl });
  await until(async () => (await fetch(`${apiUrl}/health`)).ok, 'API ready');
  await until(async () => (await fetch(web)).ok, 'Vite ready');
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
  }, { key: `review-workbench:${apiUrl}`, profileId: options.profiles[0].profile_id });
  const analysis = await analysisContext.newPage();
  activePage = analysis;
  analysis.on('pageerror', error => errors.push(error.message));
  await analysis.goto(`${web}/assistant`);
  await analysis.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await analysis.getByRole('textbox', { name: '评论分析问题' }).fill('你能做什么？（明确选择评论分析，不自动切换基础问答）');
  await analysis.getByRole('button', { name: '提交问题', exact: true }).click();
  await analysis.locator('.chat-report > summary').waitFor();
  await analysis.locator('.chat-report > summary').click();
  await analysis.locator('.agent-evidence-list blockquote').first().waitFor();
  assert.equal(analysisCalls, 2);
  assert.equal(basicCalls, 2);
  assert.ok(await analysis.locator('.chat-report table').count() > 0);
  await capture(analysis, 'analysis-result');
  await analysis.reload();
  await analysis.locator('.chat-report').waitFor();
  assert.equal(await analysis.getByRole('radio', { name: '基础问答', exact: true }).isChecked(), true);
  assert.equal(analysisCalls, 2, 'restoring analysis must not invoke model again');
  await analysisContext.close();
  assert.deepEqual(errors, []);
  report.status = 'passed';
  report.checks = ['basic without scope', 'no keyword routing', 'Markdown answer', 'no basic task polling',
    'basic failure and resend', 'memory-only messages and draft', 'analysis scope requirement',
    'existing analysis tools, tables and evidence', 'analysis refresh restore without model call', 'responsive screenshots'];
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
  console.log(JSON.stringify(report, null, 2));
}
