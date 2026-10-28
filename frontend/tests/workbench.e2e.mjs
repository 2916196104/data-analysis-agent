import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { chromium } from 'playwright';
import { captureLayout, checkShellScrolling, checkShortContent, checkPreparationPage, checkTaskReportsPage, checkAssistantChat } from './capture-layout.mjs';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(frontend);
const apiPort = Number(process.env.E2E_API_PORT || 18765);
const webPort = Number(process.env.E2E_WEB_PORT || 15173);
const apiUrl = `http://127.0.0.1:${apiPort}/api/v1`;
const webUrl = `http://127.0.0.1:${webPort}`;
const python = process.env.E2E_PYTHON || path.join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const database = process.env.E2E_DATABASE_URL;
if (database && !new URL(database).pathname.startsWith('/agent_test_')) throw new Error('Use an isolated agent_test_ database');
const children = [];
const logs = [];
let browser;
let page;
let modelServer;
let modelCalls = 0;
let agentModelCalls = 0;
let concurrentCalls = 0;
let maxConcurrentCalls = 0;
const browserErrors = [];
const layoutMeasurements = [];
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const artifacts = await mkdtemp(path.join(root, 'output/playwright/frontend-'));
const importRoot = path.join(artifacts, 'server-imports');
const localFiles = path.join(artifacts, '电脑文件 中文目录');
await mkdir(importRoot);
await mkdir(localFiles);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function freePort(port) {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  await new Promise(resolve => server.close(resolve));
}
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(artifacts, `${name}.log`));
  logs.push(log);
  const child = spawn(command, args, { cwd: root, env: { ...process.env, PYTHONUTF8: '1', ...env }, windowsHide: true });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
  return child;
}
async function until(check, label, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { const value = await check(); if (value) return value; } catch { /* Server may still be starting. */ }
    await delay(250);
  }
  throw new Error(`Timed out: ${label}`);
}
async function clickNavigation(name) {
  const target = page.getByRole('button', { name, exact: true });
  if (!await target.isVisible()) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
  await target.click();
}
async function chooseImportFile(filePath, index = 0) {
  const fileChooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: index === 0 ? '选择评论文件' : '选择元数据文件', exact: true }).click();
  await (await fileChooser).setFiles(filePath);
}
async function get(endpoint) {
  const response = await fetch(`${apiUrl}${endpoint}`);
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  return body.result;
}
async function databaseEvidence() {
  const { stdout } = await promisify(execFile)(python, ['-c', `
import json, os, sys
from contextlib import closing
from data_analysis_agent.reviews.store import connect_review_database
with closing(connect_review_database(os.environ.get('E2E_DATABASE_URL') or sys.argv[1], readonly=True)) as db:
    rows = [dict(r) for r in db.execute("SELECT id, source_record_id, user_id, text, product_id FROM reviews WHERE product_id='DEMO-C' ORDER BY id")]
    labels = [dict(r) for r in db.execute("SELECT a.id, a.review_id, a.status, o.aspect, o.sentiment, o.quote FROM review_annotations a JOIN reviews r ON r.id=a.review_id LEFT JOIN review_opinions o ON o.annotation_id=a.id WHERE r.product_id='DEMO-C' ORDER BY a.id")]
    print(json.dumps({'reviews': rows, 'annotations': labels, 'total_reviews': db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]}))
`, path.join(artifacts, 'reviews.sqlite3')], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } });
  return JSON.parse(stdout);
}

try {
  await freePort(apiPort); await freePort(webPort);
  const fixture = launch(python, ['scripts/frontend_fixture.py', '--output', artifacts,
    ...(database ? ['--database-env', 'E2E_DATABASE_URL'] : [])], 'fixture');
  await new Promise((resolve, reject) => {
    fixture.once('error', reject);
    fixture.once('exit', code => code === 0 ? resolve() : reject(new Error(`Fixture exited ${code}; see ${artifacts}`)));
  });
  // Exercise the real label worker/SDK against a local model, never a paid provider.
  modelServer = createServer(async (request, response) => {
    let raw = '';
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    assert.equal(request.url, '/v1/chat/completions');
    assert.equal(body.model, 'SIMULATED-LABEL-MODEL');
    if (body.tools) {
      agentModelCalls++;
      const ids = body.messages.filter(message => message.role === 'tool')
        .map(message => JSON.parse(message.content).data?.result_id).filter(Boolean);
      const message = ids.length ? { role: 'assistant', content: JSON.stringify({ status: 'ok', result_ids: ids }) }
        : { role: 'assistant', content: null, tool_calls: [
          ['get_trend', { period: 'month' }], ['compare_products', {}],
          ['get_evidence', { aspect: 'packaging', sentiment: 'negative' }],
        ].map(([name, parameters], index) => ({ id: `agent-${index}`, type: 'function',
          function: { name, arguments: JSON.stringify(parameters) } })) };
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ id: `agent-local-${agentModelCalls}`, object: 'chat.completion', created: 0,
        model: body.model, choices: [{ index: 0, finish_reason: message.tool_calls ? 'tool_calls' : 'stop', message }],
        usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } }));
      return;
    }
    modelCalls++; concurrentCalls++;
    maxConcurrentCalls = Math.max(maxConcurrentCalls, concurrentCalls);
    await delay(2200);
    concurrentCalls--;
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ id: `local-${modelCalls}`, object: 'chat.completion', created: 0,
      model: 'SIMULATED-LABEL-MODEL', choices: [{ index: 0, finish_reason: 'stop',
        message: { role: 'assistant', content: JSON.stringify({ opinions: [{ aspect: 'packaging', sentiment: 'negative', quote: '包装破损' }] }) } }],
      usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } }));
  });
  await new Promise(resolve => modelServer.listen(0, '127.0.0.1', resolve));
  launch(python, ['-m', 'data_analysis_agent.cli.server', 'serve', '--db', database ? 'env:E2E_DATABASE_URL' : path.join(artifacts, 'reviews.sqlite3'),
    '--port', String(apiPort), '--import-root', importRoot, '--env-file', path.join(artifacts, 'unused.env'), '--allow-origin', webUrl], 'api', {
    LLM_MODEL_ID: 'SIMULATED-LABEL-MODEL', LLM_API_KEY: 'local-test-key',
    LLM_BASE_URL: `http://127.0.0.1:${modelServer.address().port}/v1`, LLM_MAX_RETRIES: '0',
    // 本脚本只启动本地 Worker；避免项目 .env 的 Redis 模式影响隔离验收。
    LABEL_EXECUTION_MODE: 'local',
  });
  launch(process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), frontend, '--host', '127.0.0.1', '--port', String(webPort)], 'vite', { VITE_API_BASE_URL: apiUrl });
  await until(async () => (await fetch(`${apiUrl}/health`)).ok, 'API ready');
  assert.equal((await get('/quality')).review_count, 96);
  assert.equal((await get('/options')).products.length, 2);
  await until(async () => (await fetch(webUrl)).ok, 'Vite ready');
  browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: 'zh-CN', acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.goto(webUrl);
  await writeFile(path.join(artifacts, 'page-title.txt'), await page.title());
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '导入新数据', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '导入数据文件', exact: true }).count(), 1);
  await page.locator('.context-summary').filter({ hasText: 'SIMULATED-frontend' }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: '分析问题备注' }).isVisible(), false);
  assert.equal(await page.getByRole('combobox', { name: '标注版本', exact: true }).isVisible(), false);
  const startBox = await page.getByRole('button', { name: '开始分析', exact: true }).boundingBox();
  assert.ok(startBox && startBox.y + startBox.height <= 768, 'main action must fit above the fold');
  // 桌面侧栏固定，主内容随可用宽度伸缩。
  assert.equal((await page.locator('.workspace-sidebar').boundingBox()).width, 200);
  assert.ok((await page.locator('.main-content').boundingBox()).width > 650);
  await page.getByRole('button', { name: '任务记录', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: '还没有任务记录', exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'history-empty.png'), animations: 'disabled' });
  await clickNavigation('新建分析');
  await page.locator('.task-reports').waitFor({ state: 'hidden' });
  for (const width of [1024, 768]) {
    await page.setViewportSize({ width, height: 768 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'workspace overflows');
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await writeFile(path.join(artifacts, 'initial-ui.txt'), await page.locator('body').innerText());
  await page.screenshot({ path: path.join(artifacts, '01-workbench.png'), fullPage: true });
  await captureLayout(page, artifacts, 'workbench', layoutMeasurements);
  await checkShortContent(page, artifacts);
  await checkPreparationPage(page, artifacts);
  assert.equal(modelCalls, 0, 'opening preparation must not submit model work');

  // Select a real product and preview its actual persisted reviews.
  await page.getByRole('combobox', { name: '分析商品' }).click();
  await page.getByTitle('DEMO-A（48 条）').click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '数据详情', exact: true }).click();
  await page.getByText('使用顺手，效果不错。', { exact: true }).first().waitFor();
  await captureLayout(page, artifacts, 'data-preview', layoutMeasurements);
  await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: '数据预览', exact: true }).waitFor({ state: 'hidden' });
  const analysisEnd = page.locator('.scope-card .ant-picker').first().locator('input').last();
  await analysisEnd.fill('2023-02-28');
  await analysisEnd.press('Enter');
  const question = '验收：DEMO-A 的负面问题有哪些变化？';
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  await page.getByRole('textbox', { name: '分析问题备注' }).fill(question);
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  await page.reload();
  await page.locator('.ant-select-selection-item').filter({ hasText: 'DEMO-A（48 条）' }).waitFor();
  assert.equal(await page.locator('.scope-card .ant-picker').first().locator('input').last().inputValue(), '2023-02-28');
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  assert.equal(await page.getByRole('textbox', { name: '分析问题备注' }).inputValue(), question);
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  const submitted = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  const submission = (await (await submitted).json()).result;
  const taskId = submission.task.task_id;
  assert.deepEqual(submission.task.parameters.product_ids, ['DEMO-A']);
  assert.equal(submission.task.parameters.date_to, '2023-02-28');
  assert.equal(submission.task.parameters.question, undefined);
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor({ timeout: 30_000 });
  await page.getByRole('tabpanel', { name: '趋势图', exact: true }).locator('canvas').waitFor();
  await until(async () => await page.locator('canvas').count(), 'chart canvas rendered');
  const completed = await get(`/tasks/${taskId}`);
  assert.equal(completed.status, 'succeeded');
  const run = await get(`/analysis-runs/${completed.result.run_id}`);
  assert.equal(run.results.trend.summary.review_count, 24);
  assert.equal(run.results.trend.summary.negative_review_count, 12);
  assert.equal(run.results.trend.summary.negative_rate, 0.5);
  assert.equal(run.results.summary.triggered, 1);
  await page.screenshot({ path: path.join(artifacts, '02-analysis-result.png'), fullPage: true });
  await captureLayout(page, artifacts, 'analysis-result', layoutMeasurements);
  await checkShellScrolling(page, artifacts);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 JSON', exact: true }).click();
  const download = await downloadPromise;
  const downloadPath = path.join(artifacts, download.suggestedFilename());
  await download.saveAs(downloadPath);
  const exported = JSON.parse(await readFile(downloadPath, 'utf8'));
  assert.equal(exported.analysis.run_id, run.run_id);
  assert.equal(exported.question_note, question);
  assert.deepEqual(exported.analysis.results, run.results);

  // Reload resumes a backend task and preserves the local-only question association.
  await page.reload();
  await page.getByRole('heading', { name: question, exact: true }).waitFor();
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor();
  // Network failure leaves the last known successful state visible; reconnect recovers it.
  await context.setOffline(true);
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await page.getByText('任务连接异常', { exact: true }).waitFor();
  await context.setOffline(false);
  await page.getByRole('button', { name: '重新连接', exact: true }).click();
  await page.getByRole('button', { name: '立即重连', exact: true }).click();
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor();

  // A real corrupt gzip selected from the browser reaches the worker and produces a persisted failure.
  await page.getByRole('button', { name: '导入数据文件', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '选择评论文件', exact: true }).isVisible(), true);
  await chooseImportFile(path.join(artifacts, 'broken.jsonl.gz'));
  const failedSubmission = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const failedId = (await (await failedSubmission).json()).result.task.task_id;
  await page.getByRole('button', { name: '重试任务', exact: true }).waitFor({ timeout: 30_000 });
  const failed = await get(`/tasks/${failedId}`);
  assert.equal(failed.status, 'failed');
  assert.ok(failed.error.message);
  await page.screenshot({ path: path.join(artifacts, '03-failure.png'), fullPage: true });
  const retryResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks/${failedId}/retry`);
  await page.getByRole('button', { name: '重试任务', exact: true }).click();
  const retried = (await (await retryResponse).json()).result.task;
  assert.notEqual(retried.task_id, failedId);
  assert.equal(retried.parent_task_id, failedId);

  // Choose a file outside import-root using the native file chooser, then check its bytes and database rows.
  const extra = JSON.parse(await readFile(path.join(artifacts, 'extra.jsonl'), 'utf8'));
  const localReview = path.join(localFiles, '评论 样本.jsonl');
  const localMeta = path.join(localFiles, '商品 信息.jsonl.gz');
  await writeFile(localReview, Array.from({ length: 3 }, (_, index) =>
    JSON.stringify({ ...extra, user_id: `limited-demo-${index}`, upload_padding: 'x'.repeat(1024 * 1024) })).join('\n') + '\n');
  await writeFile(localMeta, gzipSync(JSON.stringify({ parent_asin: 'DEMO-C', title: 'DEMO-C', store: '本机测试商店' }) + '\n'));
  assert.ok(!path.relative(importRoot, localReview).startsWith(`.${path.sep}`) && path.relative(importRoot, localReview).startsWith('..'));
  await page.getByRole('button', { name: '导入数据文件', exact: true }).click();
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  await page.getByText('请先选择评论文件或商品元数据文件。', { exact: true }).waitFor();
  await chooseImportFile(localReview);
  await page.getByRole('dialog', { name: '导入数据', exact: true }).locator('summary').filter({ hasText: '高级设置' }).click();
  await chooseImportFile(localMeta, 1);
  await page.screenshot({ path: path.join(artifacts, '06-file-import.png'), fullPage: true });
  await captureLayout(page, artifacts, 'import-dialog', layoutMeasurements);
  await page.getByRole('spinbutton', { name: '本次最多新增评论数（可选）' }).fill('2');
  // Losing the task response must preserve both selected files, uploaded paths and the idempotency key.
  const uploadRequests = [];
  page.on('request', request => { if (request.url().includes('/import-files?')) uploadRequests.push(request.url()); });
  let lostSubmission;
  const lostResponseRoute = async route => {
    if (route.request().method() !== 'POST') return route.continue();
    lostSubmission = (await (await route.fetch()).json()).result.task;
    await route.abort();
  };
  const network = await context.newCDPSession(page);
  await network.send('Network.enable');
  await network.send('Network.emulateNetworkConditions', { offline: false, latency: 20, downloadThroughput: -1, uploadThroughput: 256 * 1024 });
  await page.evaluate(() => {
    window.uploadEvents = [];
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (body) {
      this.upload.addEventListener('progress', event => window.uploadEvents.push({ loaded: event.loaded, total: event.total }));
      return send.call(this, body);
    };
  });
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const uploadBar = page.getByRole('dialog', { name: '导入数据', exact: true }).getByRole('progressbar', { name: '文件上传进度', exact: true });
  await until(async () => {
    const percent = Number(await uploadBar.getAttribute('aria-valuenow'));
    return percent > 0 && percent < 100;
  }, 'real intermediate upload percentage');
  assert.equal(await page.getByText('等待上传并提交任务', { exact: true }).isVisible(), true);
  const uploadPercent = Number(await uploadBar.getAttribute('aria-valuenow'));
  await page.route(`${apiUrl}/tasks`, lostResponseRoute);
  await page.screenshot({ path: path.join(artifacts, 'upload-progress.png'), fullPage: true });
  await page.getByText(/无法连接后端.*提交结果暂未确认/).waitFor();
  assert.equal(await page.getByText('上传完成', { exact: true }).isVisible(), true);
  await network.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await network.detach();
  assert.equal(await page.getByText(path.basename(localReview), { exact: true }).isVisible(), true);
  assert.equal(await page.getByText(path.basename(localMeta), { exact: true }).isVisible(), true);
  assert.equal(uploadRequests.length, 2);
  await page.unroute(`${apiUrl}/tasks`, lostResponseRoute);
  const importResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const importedSubmission = (await (await importResponse).json()).result;
  const importId = importedSubmission.task.task_id;
  assert.equal(importId, lostSubmission.task_id);
  assert.equal(importedSubmission.reused, true);
  assert.equal(uploadRequests.length, 2, 'retry must reuse the files that were already uploaded');
  await until(async () => (await get(`/tasks/${importId}`)).status === 'succeeded', 'successful import');
  const limitedImport = await get(`/tasks/${importId}`);
  await page.getByText('处理完成', { exact: true }).waitFor();
  assert.equal(limitedImport.progress.stage, 'imported');
  assert.equal(limitedImport.progress.total_lines, 3);
  assert.equal(limitedImport.progress.inserted_records, 3);
  await page.getByRole('dialog', { name: '导入数据', exact: true }).waitFor({ state: 'hidden' });
  assert.match(await page.getByLabel('数据处理阶段', { exact: true }).innerText(), /已处理 3 行.*已新增 3 条/);
  const uploadEvents = await page.evaluate(() => window.uploadEvents);
  await page.reload();
  await page.getByText('处理完成', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'processing-complete.png'), fullPage: true });
  assert.equal(limitedImport.parameters.max_reviews, 2);
  const reviewImport = limitedImport.result.imports.find(item => item.kind === 'reviews');
  assert.equal(reviewImport.inserted_records, 2);
  assert.equal(reviewImport.limit_reached, true);
  assert.equal(reviewImport.scan_complete, false);
  assert.equal(limitedImport.result.imports.find(item => item.kind === 'meta').inserted_records, 1);
  assert.deepEqual(await readFile(limitedImport.parameters.reviews_path), await readFile(localReview));
  assert.deepEqual(await readFile(limitedImport.parameters.meta_path), await readFile(localMeta));
  assert.ok(path.relative(importRoot, limitedImport.parameters.reviews_path).startsWith(`.uploads${path.sep}`));
  await writeFile(path.join(artifacts, 'upload-evidence.json'), JSON.stringify({
    local_review_file: localReview, local_meta_file: localMeta, server_import_root: importRoot,
    same_review_bytes: true, same_compressed_meta_bytes: true, tasks_created_after_response_retry: 1,
    uploaded_files_after_response_retry: uploadRequests.length, task_id: importId,
    observed_upload_percent: uploadPercent, processing_progress: limitedImport.progress,
    upload_events: uploadEvents,
  }, null, 2));
  assert.equal((await get('/quality')).review_count, 98);
  const importedRows = await databaseEvidence();
  assert.equal(importedRows.total_reviews, 98);
  assert.equal(importedRows.reviews.length, 2);
  assert.equal(new Set(importedRows.reviews.map(r => r.source_record_id)).size, 2);
  assert.deepEqual(importedRows.reviews.map(r => r.user_id), ['limited-demo-0', 'limited-demo-1']);
  assert.ok(importedRows.reviews.every(r => r.text === extra.text));
  assert.equal(importedRows.annotations.length, 0);

  // Import -> product-scoped label batch -> live progress -> persisted results.
  await page.getByRole('button', { name: '标注已导入的数据', exact: true }).click();
  await page.getByRole('combobox', { name: '标注商品', exact: true }).click();
  await page.getByTitle('DEMO-C · DEMO-C（2 条）').click();
  await page.keyboard.press('Escape');
  await captureLayout(page, artifacts, 'annotation-page', layoutMeasurements);
  await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).fill('2.5');
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  await page.getByText('请输入 1–1000 的整数', { exact: true }).waitFor();
  assert.equal(modelCalls, 0);
  await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).fill('2');
  assert.equal(await page.getByRole('spinbutton', { name: '并发数', exact: true }).isVisible(), false);
  await page.locator('summary').filter({ hasText: '处理设置' }).click();
  await page.getByRole('spinbutton', { name: '并发数', exact: true }).fill('2');
  await page.getByRole('checkbox', { name: '同时重试此前标注失败的评论' }).check();
  let failedLabelKey;
  const failedLabelRoute = async route => {
    if (route.request().method() !== 'POST' || route.request().postDataJSON()?.kind !== 'label') return route.continue();
    failedLabelKey = route.request().headers()['idempotency-key'];
    await route.fulfill({ status: 503, json: { status: 'error', error: { code: 'temporarily_unavailable', message: '验收：标注提交暂不可用，请重试。' } } });
  };
  await page.route(`${apiUrl}/tasks`, failedLabelRoute);
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  await page.getByText('验收：标注提交暂不可用，请重试。', { exact: true }).waitFor();
  assert.equal(await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).inputValue(), '2');
  assert.equal(await page.getByRole('checkbox', { name: '同时重试此前标注失败的评论' }).isChecked(), true);
  assert.equal(modelCalls, 0, 'failed submission must stay on form without model work');
  await page.unroute(`${apiUrl}/tasks`, failedLabelRoute);
  const labelResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  const labelSubmissionResponse = await labelResponse;
  assert.equal(labelSubmissionResponse.request().headers()['idempotency-key'], failedLabelKey, 'label retry must retain idempotency key');
  const labeled = (await labelSubmissionResponse.json()).result.task;
  assert.equal(labeled.kind, 'label');
  assert.deepEqual(labeled.parameters.product_ids, ['DEMO-C']);
  assert.equal(labeled.parameters.dataset_version, 'amazon-reviews-2023');
  assert.equal(labeled.parameters.limit, 2);
  assert.equal(labeled.parameters.workers, 2);
  assert.equal(labeled.parameters.retry_failed, true);
  assert.equal(labeled.parameters.date_from, null);
  await page.getByText('标注进度 0/2', { exact: true }).waitFor();
  await page.getByText('标注进度 2/2', { exact: true }).waitFor();
  await page.getByText('成功 2', { exact: true }).waitFor();
  await page.getByText('失败 0', { exact: true }).waitFor();
  await page.getByText('模型调用 2', { exact: true }).waitFor();
  const labelTask = await get(`/tasks/${labeled.task_id}`);
  assert.equal(labelTask.status, 'succeeded');
  assert.equal(labelTask.result.successful_reviews, 2);
  assert.equal(modelCalls, 2);
  assert.equal(maxConcurrentCalls, 2);
  const persisted = await databaseEvidence();
  assert.equal(persisted.annotations.length, 2);
  assert.deepEqual(persisted.annotations.map(a => a.review_id).sort((a, b) => a - b), importedRows.reviews.map(r => r.id));
  assert.ok(persisted.annotations.every(a => a.status === 'success' && a.aspect === 'packaging' && a.sentiment === 'negative' && a.quote === '包装破损'));
  assert.equal(new URL(page.url()).pathname, `/annotation/tasks/${labeled.task_id}`);
  assert.equal(await page.getByRole('button', { name: '评论标注', exact: true }).getAttribute('aria-current'), 'page');
  assert.equal(await page.locator('.sidebar-nav [aria-current="page"]').count(), 1);
  await page.goBack();
  await page.getByRole('heading', { name: '评论标注', exact: true }).waitFor();
  assert.equal(await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).inputValue(), '2');
  assert.equal(modelCalls, 2, 'back must restore form without submitting again');
  await page.goForward();
  await page.getByText('标注进度 2/2', { exact: true }).waitFor();
  await page.reload();
  await page.getByText('标注进度 2/2', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '评论标注', exact: true }).getAttribute('aria-current'), 'page');
  const directLabelTask = await page.context().newPage();
  try {
    directLabelTask.on('pageerror', error => browserErrors.push(error.message));
    // Historical generic task links resolve to the actual annotation module after loading the task kind.
    await directLabelTask.goto(`${webUrl}/tasks/${labeled.task_id}`);
    await directLabelTask.waitForURL(`${webUrl}/annotation/tasks/${labeled.task_id}`);
    await directLabelTask.getByText('标注进度 2/2', { exact: true }).waitFor();
    assert.equal(await directLabelTask.getByRole('button', { name: '评论标注', exact: true }).getAttribute('aria-current'), 'page');
    assert.equal(modelCalls, 2, 'direct task URL must only read saved task');
  } finally { await directLabelTask.close(); }
  await until(() => page.getByRole('button', { name: '分析这些评论', exact: true }).isEnabled(), 'prepared analysis ready');
  assert.equal(await page.locator('.json-view').isVisible(), false);
  await page.screenshot({ path: path.join(artifacts, '05-label-result.png'), fullPage: true });
  await page.getByRole('button', { name: '继续标注下一批', exact: true }).click();
  assert.equal(new URL(page.url()).pathname, '/annotation');
  assert.equal(await page.getByRole('button', { name: '评论标注', exact: true }).getAttribute('aria-current'), 'page');
  assert.equal(await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).inputValue(), '2');
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  await page.getByText('所选范围内没有需要标注的评论。可更换商品，或勾选重试失败评论。', { exact: true }).waitFor();
  assert.equal(modelCalls, 2, 'completed reviews must not call the model again');
  assert.deepEqual(await databaseEvidence(), persisted);
  await page.getByRole('button', { name: '调整分析范围', exact: true }).click();
  await page.getByRole('combobox', { name: '分析商品', exact: true }).waitFor();
  await page.locator('.ant-select-selection-item').filter({ hasText: 'DEMO-C（2 条）' }).waitFor();
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  await page.getByText(/SIMULATED-LABEL-MODEL · review-opinions/).first().waitFor();
  await page.locator('summary').filter({ hasText: '高级设置' }).click();
  // Return to the completed batch and start analysis without selecting the scope again.
  await clickNavigation('任务记录');
  await page.locator('.history-item').filter({ hasText: '准备数据 · 最多 2 条' }).last().click();
  await page.locator('.task-reports').waitFor({ state: 'hidden' });
  await page.reload();
  await page.getByText('标注进度 2/2', { exact: true }).waitFor();
  await until(() => page.getByRole('button', { name: '分析这些评论', exact: true }).isEnabled(), 'restored label ready for analysis');
  const quickResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '分析这些评论', exact: true }).click();
  const quickAnalysis = (await (await quickResponse).json()).result.task;
  assert.equal(quickAnalysis.kind, 'analysis');
  assert.deepEqual(quickAnalysis.parameters.product_ids, ['DEMO-C']);
  assert.equal(quickAnalysis.parameters.model, 'SIMULATED-LABEL-MODEL');
  assert.equal(quickAnalysis.parameters.date_from, submission.task.parameters.date_from);
  assert.equal(quickAnalysis.parameters.date_to, '2023-02-28');
  assert.equal(quickAnalysis.parameters.baseline_from, submission.task.parameters.baseline_from);
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor();
  assert.equal(modelCalls, 2, 'analysis must reuse labels without model calls');

  // Reimport the same two records through the UI, then read database rows independently.
  await writeFile(path.join(artifacts, 'duplicate.jsonl'), (await readFile(localReview, 'utf8')).trim().split('\n').slice(0, 2).join('\n') + '\n');
  await page.getByRole('button', { name: '导入数据文件', exact: true }).click();
  await chooseImportFile(path.join(artifacts, 'duplicate.jsonl'));
  const duplicateResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const duplicateId = (await (await duplicateResponse).json()).result.task.task_id;
  await until(async () => (await get(`/tasks/${duplicateId}`)).status === 'succeeded', 'duplicate import');
  const duplicateTask = await get(`/tasks/${duplicateId}`);
  assert.equal(duplicateTask.result.imports[0].inserted_records, 0);
  assert.equal(duplicateTask.result.imports[0].duplicate_records, 2);
  assert.deepEqual(await databaseEvidence(), persisted);
  await writeFile(path.join(artifacts, 'database-evidence.json'), JSON.stringify({ database_kind: database ? 'postgresql' : 'sqlite',
    file_rows: 3, imported: importedRows, labeled: persisted, duplicate_import: { inserted: 0, duplicates: 2 }, model_calls: modelCalls, max_concurrency: maxConcurrentCalls }, null, 2));

  // Delete the real limited import through the UI, then remove its task history.
  await clickNavigation('任务记录');
  await page.locator('.history-item').filter({ hasText: `导入 ${path.basename(localReview)}` }).first().click();
  await page.locator('.task-reports').waitFor({ state: 'hidden' });
  await page.getByText('删除导入数据', { exact: true }).click();
  await page.getByText('会删除本次导入产生的评论、错误记录和关联标注；其他导入批次共用的商品信息会保留。此操作不可恢复。', { exact: true }).waitFor();
  await page.getByRole('button', { name: '删除导入数据', exact: true }).click();
  await page.getByText('导入数据已删除', { exact: true }).waitFor();
  assert.equal((await get('/quality')).review_count, 96);
  const deletedImportTask = await get(`/tasks/${importId}`);
  assert.equal(deletedImportTask.result.import_data_deleted, true);
  await page.getByText('删除任务记录', { exact: true }).click();
  await page.getByRole('button', { name: '删除记录', exact: true }).click();
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  assert.equal((await fetch(`${apiUrl}/tasks/${importId}`)).status, 404);

  // Use enough real database work to observe processing without generating timer-based progress.
  const progressRows = database ? 3000 : 30000;
  const processingFile = path.join(localFiles, '处理进度.jsonl');
  await writeFile(processingFile, Array.from({ length: progressRows }, (_, index) =>
    JSON.stringify({ ...extra, parent_asin: 'DEMO-PROGRESS', user_id: `progress-${index}` })).join('\n') + '\n');
  await page.getByRole('button', { name: '导入数据文件', exact: true }).click();
  await chooseImportFile(processingFile);
  await page.getByRole('spinbutton', { name: '本次最多新增评论数（可选）' }).fill('');
  const processingSubmission = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const processingId = (await (await processingSubmission).json()).result.task.task_id;
  let observedProcessing;
  await until(async () => {
    const current = await get(`/tasks/${processingId}`);
    if (current.progress.stage === 'importing_reviews' && current.progress.total_lines > 0 && current.progress.total_lines < progressRows) observedProcessing = current.progress;
    return observedProcessing && /新增待提交 [1-9]/.test(await page.getByLabel('数据处理阶段', { exact: true }).innerText());
  }, 'actual processing bytes and row count', 90_000);
  assert.equal(observedProcessing.unit, 'bytes');
  assert.ok(observedProcessing.completed > 0 && observedProcessing.completed < observedProcessing.total);
  await page.screenshot({ path: path.join(artifacts, 'processing-progress.png'), fullPage: true });
  await page.reload();
  await page.getByLabel('数据处理阶段', { exact: true }).waitFor();
  await until(async () => (await get(`/tasks/${processingId}`)).status === 'succeeded', 'processing import commits', 90_000);
  await page.getByText('处理完成', { exact: true }).waitFor();
  const processed = await get(`/tasks/${processingId}`);
  assert.equal(processed.progress.total_lines, progressRows);
  assert.equal(processed.progress.inserted_records, progressRows);
  assert.equal((await get('/quality')).review_count, 96 + progressRows);
  await writeFile(path.join(artifacts, 'processing-evidence.json'), JSON.stringify({ observed: observedProcessing, completed: processed.progress, rows: progressRows }, null, 2));
  await page.getByText('删除导入数据', { exact: true }).click();
  await page.getByRole('button', { name: '删除导入数据', exact: true }).click();
  await page.getByText('导入数据已删除', { exact: true }).waitFor();
  assert.equal((await get('/quality')).review_count, 96);
  await page.getByText('删除任务记录', { exact: true }).click();
  await page.getByRole('button', { name: '删除记录', exact: true }).click();
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();

  // History preserves pagination and restores a real result; test rows only replace the list API.
  const historyRoute = '**/api/v1/tasks?*';
  const historyItem = { ...completed, task_id: taskId };
  const historyRows = Array.from({ length: 15 }, (_, index) => ({ ...historyItem, task_id: `history-fixture-${index}` }));
  await page.route(historyRoute, async route => {
    const currentPage = Number(new URL(route.request().url()).searchParams.get('page'));
    await route.fulfill({ json: { status: 'ok', result: { total: 16, page: currentPage, page_size: 15, items: currentPage === 2 ? [historyItem] : historyRows } } });
  });
  await clickNavigation('任务记录');
  const historyPage = page.locator('main .task-reports');
  await until(async () => await historyPage.locator('.history-item').count() === 15, 'history first page');
  await checkTaskReportsPage(page, artifacts);
  await page.screenshot({ path: path.join(artifacts, 'history-pagination.png'), animations: 'disabled' });
  await historyPage.getByTitle('下一页').click();
  await until(async () => await historyPage.locator('.history-item').count() === 1, 'history second page');
  await historyPage.locator('.history-item').click();
  await historyPage.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { name: question, exact: true }).waitFor();
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await clickNavigation('任务记录');
  await historyPage.locator('.history-item').first().waitFor();
  await until(async () => {
    const box = await historyPage.boundingBox();
    return box && box.width <= 391 && box.x >= -1 && box.x + box.width <= 391;
  }, 'history page fits mobile viewport');
  assert.ok(await historyPage.evaluate(element => element.scrollWidth <= element.clientWidth), 'history content overflows');
  assert.ok(await historyPage.locator('.history-title').first().evaluate(element => element.scrollWidth <= element.clientWidth), 'long history title overflows');
  await page.screenshot({ path: path.join(artifacts, 'history-mobile.png'), animations: 'disabled' });
  await historyPage.getByTitle('上一页').click();
  await until(async () => await historyPage.locator('.history-item').count() === 15, 'return to first history page');
  await clickNavigation('新建分析');
  await historyPage.waitFor({ state: 'hidden' });
  // A history request error has a retry action and recovers without losing the workspace.
  await page.unroute(historyRoute);
  await page.route(historyRoute, route => route.abort());
  await clickNavigation('任务记录');
  await historyPage.getByText('任务记录暂不可用', { exact: true }).waitFor();
  const historyRetry = page.waitForRequest(request => request.url().includes('/tasks?'));
  await historyPage.getByRole('button', { name: '重试任务记录', exact: true }).click();
  await historyRetry;
  await page.unroute(historyRoute);
  await historyPage.getByRole('button', { name: '刷新任务记录', exact: true }).click();
  await historyPage.getByText('任务记录暂不可用', { exact: true }).waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({ path: path.join(artifacts, 'history-desktop.png'), animations: 'disabled' });
  await captureLayout(page, artifacts, 'task-history', layoutMeasurements);
  // Deleting the currently selected real task stays on this page and needs explicit inline confirmation.
  await historyPage.locator(`[data-task-id="${failedId}"]`).click();
  await page.getByRole('button', { name: '重试任务', exact: true }).waitFor();
  await clickNavigation('任务记录');
  const deleteRow = historyPage.locator(`[data-task-id="${failedId}"]`);
  await deleteRow.waitFor();
  assert.equal(await deleteRow.getAttribute('aria-current'), 'true');
  await deleteRow.locator('.history-delete').click();
  assert.equal(await page.locator('.ant-modal-mask:visible, .ant-drawer-mask:visible').count(), 0);
  await historyPage.getByRole('button', { name: '取消删除任务', exact: true }).click();
  assert.equal((await fetch(`${apiUrl}/tasks/${failedId}`)).status, 200);
  await deleteRow.locator('.history-delete').click();
  const taskDeleted = page.waitForResponse(response => response.url() === `${apiUrl}/tasks/${failedId}` && response.request().method() === 'DELETE');
  await historyPage.getByRole('button', { name: '确认删除任务', exact: true }).click();
  assert.equal((await taskDeleted).status(), 200);
  await deleteRow.waitFor({ state: 'hidden' });
  assert.equal((await fetch(`${apiUrl}/tasks/${failedId}`)).status, 404);
  await page.getByRole('heading', { name: '任务与报告', exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await clickNavigation('新建分析');
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, '04-mobile.png'), fullPage: true, animations: 'disabled' });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'mobile body overflows');
  // CSV uses the same native picker and a separate source through preparation.
  await page.setViewportSize({ width: 1366, height: 900 });
  const localCsv = path.join(localFiles, '京东 评论.CSV');
  await writeFile(localCsv, '\uFEFF商品ID,用户ID,打分,商品名称,评论内容,评论时间\n000123,00042,5,模拟CSV商品,包装完整,2024/8/21 17:30\n');
  await page.getByRole('button', { name: '导入数据文件', exact: true }).click();
  assert.equal(await page.locator('input[aria-label="评论文件"]').getAttribute('accept'), '.csv,.jsonl,.jsonl.gz');
  await chooseImportFile(localCsv);
  await page.locator('details').filter({ hasText: '数据集版本' }).locator('summary').click();
  await page.getByRole('textbox', { name: '数据集版本', exact: true }).fill('reviews-csv');
  assert.equal(await page.getByRole('button', { name: '选择元数据文件', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  await page.getByRole('button', { name: '标注已导入的数据', exact: true }).waitFor();
  const csvTask = (await get('/tasks?kind=import')).items[0];
  const csvResult = await get(`/tasks/${csvTask.task_id}`);
  assert.equal(csvResult.result.imports[0].inserted_records, 1);
  assert.equal(csvResult.result.imports[0].source, 'reviews_csv');
  const csvProducts = (await get('/options')).products.filter(item => item.source === 'reviews_csv');
  assert.equal(csvProducts[0].product_id, '000123');
  await page.getByRole('button', { name: '标注已导入的数据', exact: true }).click();
  await page.getByText('reviews_csv / reviews-csv', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'csv-preparation.png'), fullPage: true });
  await page.keyboard.press('Escape');
  // Agent uses the actual queue, HelloAgents, SDK and read-only business tools.
  const agentContext = await browser.newContext({ viewport: { width: 1366, height: 900 }, acceptDownloads: true });
  const fixtureProfile = (await get('/options')).profiles.find(profile => profile.requested_model === 'SIMULATED-ANNOTATIONS');
  await agentContext.addInitScript(({ storageKey, profileId }) => {
    if (!localStorage.getItem(storageKey)) localStorage.setItem(storageKey, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify(['amazon_reviews_2023', 'SIMULATED-frontend']), productIds: ['DEMO-A', 'DEMO-B'],
      profileId, current: ['2023-02-01', '2023-02-24'], baseline: ['2023-01-01', '2023-01-31'], note: '',
    } }));
  }, { storageKey: `review-workbench:${apiUrl}`, profileId: fixtureProfile.profile_id });
  const agentPage = await agentContext.newPage();
  const mainPage = page;
  page = agentPage;
  agentPage.on('pageerror', error => browserErrors.push(error.message));
  await agentPage.goto(webUrl);
  await agentPage.getByRole('button', { name: 'Agent 问答', exact: true }).click();
  await agentPage.getByRole('heading', { name: 'AI 评论分析助手', exact: true }).waitFor();
  assert.equal(new URL(agentPage.url()).pathname, '/assistant');
  assert.equal(await agentPage.getByRole('dialog').count(), 0);
  await agentPage.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await agentPage.getByRole('button', { name: '按日查看评论趋势', exact: true }).click();
  assert.equal(await agentPage.getByRole('textbox', { name: '评论分析问题' }).inputValue(), '按日查看评论趋势');
  await captureLayout(agentPage, artifacts, 'agent-question', layoutMeasurements);
  const agentQuestion = '按月查看评论趋势，对比所选商品，并查询包装负面评论的原文证据';
  await agentPage.getByRole('textbox', { name: '评论分析问题' }).fill(agentQuestion);
  const agentSubmissionKeys = [];
  const agentFailure = async route => {
    if (route.request().method() !== 'POST') return route.continue();
    agentSubmissionKeys.push(route.request().headers()['idempotency-key']);
    await route.fulfill({ status: 503, json: { status: 'error', error: { code: 'temporary_failure', message: '验收：AI 提交暂不可用，请重试。' } } });
  };
  await agentPage.route(`${apiUrl}/tasks`, agentFailure);
  const chatInput = agentPage.getByRole('textbox', { name: '评论分析问题' });
  await chatInput.press('Shift+Enter');
  assert.equal(await chatInput.inputValue(), `${agentQuestion}\n`);
  assert.equal(agentSubmissionKeys.length, 0, 'Shift+Enter must not submit');
  await chatInput.press('Enter');
  await agentPage.getByText('验收：AI 提交暂不可用，请重试。', { exact: true }).waitFor();
  assert.equal(await agentPage.locator('.chat-turn').count(), 1);
  assert.equal(agentModelCalls, 0);
  await agentPage.unroute(`${apiUrl}/tasks`, agentFailure);
  const acceptedAgent = agentPage.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await agentPage.getByRole('button', { name: '重试提交', exact: true }).click();
  const agentAcceptedResponse = await acceptedAgent;
  assert.equal(agentAcceptedResponse.request().headers()['idempotency-key'], agentSubmissionKeys[0]);
  await agentPage.locator('.chat-report').waitFor();
  await agentPage.locator('.chat-report > summary').click();
  await agentPage.getByRole('heading', { name: 'Agent 分析结果', exact: true }).waitFor();
  const agentId = (await get('/tasks?kind=analysis')).items[0].task_id;
  const agentTask = await get(`/tasks/${agentId}`);
  assert.equal(agentTask.status, 'succeeded', JSON.stringify(agentTask));
  assert.equal(agentTask.parameters.question, agentQuestion);
  assert.equal(agentTask.result.results.r1.data.summary.review_count, 48);
  assert.equal(agentTask.result.results.r2.data.products.length, 2);
  assert.ok(agentTask.result.results.r3.data.items.every(item => item.opinions[0].quote === '包装破损'));
  assert.equal(agentModelCalls, 2);
  await agentPage.getByRole('button', { name: /查看原文 #/ }).first().click();
  await agentPage.locator('.chat-inline-detail').getByText(/sha256:/).waitFor();
  assert.equal(await agentPage.getByRole('dialog').count(), 0);
  await agentPage.getByRole('button', { name: '关闭数据详情', exact: true }).click();
  await agentPage.locator('.chat-inline-detail').waitFor({ state: 'hidden' });
  const [agentDownload] = await Promise.all([
    agentPage.waitForEvent('download'),
    agentPage.getByRole('button', { name: '下载 Agent JSON', exact: true }).click({ timeout: 5000 }),
  ]);
  assert.deepEqual(JSON.parse(await readFile(await agentDownload.path(), 'utf8')), agentTask.result);
  await agentPage.locator('.chat-report > summary').click();
  const nextQuestion = `第二次查询：请解释当前范围的包装问题。\n${'请结合真实评论原文分析。'.repeat(65)}`;
  await chatInput.fill(nextQuestion);
  const nextSubmission = agentPage.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await agentPage.getByRole('button', { name: '提交问题', exact: true }).click();
  const nextRequest = (await nextSubmission).request().postDataJSON();
  assert.deepEqual(nextRequest.parameters.product_ids, ['DEMO-A', 'DEMO-B']);
  assert.equal(nextRequest.parameters.date_from, '2023-02-01');
  assert.equal(nextRequest.parameters.question, nextQuestion);
  assert.equal('messages' in nextRequest || 'messages' in nextRequest.parameters, false, 'single-turn API must not receive chat history');
  await until(async () => await agentPage.locator('.chat-report').count() === 2, 'second real Agent reply');
  assert.equal(agentModelCalls, 4);
  assert.deepEqual(await agentPage.locator('.chat-user > p').allTextContents(), [agentQuestion, nextQuestion]);
  await agentPage.getByRole('button', { name: '对比所选商品的负面评论占比', exact: true }).click();
  assert.equal(await chatInput.inputValue(), '对比所选商品的负面评论占比');
  await chatInput.fill('');
  await agentPage.waitForFunction(() => { const m = document.querySelector('.chat-messages'); return Math.abs(m.scrollTop + m.clientHeight - m.scrollHeight) <= 1; });
  await checkAssistantChat(agentPage, artifacts);
  await clickNavigation('数据管理');
  assert.equal(await agentPage.locator('.ai-assistant').count(), 0);
  await agentPage.getByRole('heading', { name: '数据管理', exact: true }).waitFor();
  await clickNavigation('AI 助手');
  await until(async () => await agentPage.locator('.chat-report').count() === 2, 'conversation retained after navigation');
  await agentPage.reload();
  await until(async () => await agentPage.locator('.chat-report').count() === 2, 'saved conversation restored');
  assert.equal(agentModelCalls, 4, 'reload must only read saved tasks');
  await agentPage.goto(`${webUrl}/tasks/${agentId}`);
  await agentPage.waitForURL(`${webUrl}/assistant/tasks/${agentId}`);
  await until(async () => await agentPage.locator('.chat-report').count() === 2, 'legacy Agent link restored in chat');
  await agentPage.route(`${apiUrl}/tasks/${agentId}`, route => route.abort());
  await agentPage.reload();
  await agentPage.getByText('任务连接异常', { exact: true }).waitFor();
  await agentPage.getByRole('heading', { name: 'AI 评论分析助手', exact: true }).waitFor();
  await agentPage.unroute(`${apiUrl}/tasks/${agentId}`);
  await agentPage.getByRole('button', { name: '重新连接', exact: true }).click();
  await agentPage.getByText('任务连接异常', { exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await agentPage.getByText('正在读取分析快照…').count(), 0);
  await agentPage.setViewportSize({ width: 390, height: 844 });
  assert.ok(await agentPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Agent mobile overflows');
  await agentPage.screenshot({ path: path.join(artifacts, 'review-agent-mobile.png'), fullPage: true });
  await agentPage.setViewportSize({ width: 1366, height: 900 });
  await agentPage.screenshot({ path: path.join(artifacts, 'review-agent.png'), fullPage: true });
  await captureLayout(agentPage, artifacts, 'agent-result', layoutMeasurements);
  assert.equal(agentModelCalls, 4, 'restoring a saved Agent result must not invoke the model');
  await agentContext.close();
  page = mainPage;
  const emptyContext = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const emptyPage = await emptyContext.newPage();
  const emptyOptions = { ...(await get('/options')), products: [], profiles: [], selected_profile_id: null };
  await emptyPage.route(`${apiUrl}/options`, route => route.fulfill({ json: { status: 'ok', result: emptyOptions } }));
  await emptyPage.goto(webUrl);
  await emptyPage.getByText('从数据管理或侧栏导入评论后开始分析', { exact: true }).waitFor();
  assert.equal(await emptyPage.getByRole('button', { name: '导入数据文件', exact: true }).count(), 1);
  assert.equal(await emptyPage.getByRole('button', { name: '导入新数据', exact: true }).count(), 0);
  await emptyContext.close();
  assert.deepEqual(browserErrors, []);
  const serverLog = await readFile(path.join(artifacts, 'api.log'), 'utf8');
  assert.match(serverLog, /POST \/api\/v1\/tasks HTTP\/1\.1.*202/);
  assert.match(serverLog, /GET \/api\/v1\/tasks\//);
  for (const message of ['任务提交', '任务重试', '任务开始', '标注进度', '导入结果', '任务结束', '任务失败']) {
    assert.ok(serverLog.includes(message), `Missing backend log: ${message}`);
  }
  assert.ok(serverLog.includes(`task_id=${labeled.task_id} 2/2 成功=2 失败=0 模型调用=2`));
  assert.ok(serverLog.includes('新增=0 重复=2 无效=0'));
  assert.ok(!serverLog.includes('local-test-key'));
  assert.ok(!serverLog.includes(extra.text));
  const report = { status: 'passed', simulated_data: true, api: apiUrl, frontend: webUrl, task_id: taskId, run_id: run.run_id,
    failed_task_id: failedId, retry_task_id: retried.task_id, imported_task_id: importId, agent_task_id: agentId,
    checks: ['single import entry', 'CSV native picker and upload', 'CSV source and preparation scope', 'CSV textual IDs', 'product selection', 'data preview', 'question note', 'real task and worker', 'snapshot statistics', 'chart',
      'JSON download equality', 'reload recovery', 'network reconnect', 'worker failure', 'explicit retry', 'native file chooser outside import root',
      'Chinese and spaced file paths', 'JSONL and compressed metadata upload', 'uploaded bytes match selected files', 'upload missing file validation', 'lost response import retry uses one task',
      'real byte upload progress', 'upload and processing stages', 'processing committed counts', 'import progress reload recovery',
      'real processing byte and row progress', 'processing reload while active', 'large import database counts', 'large import cleanup',
      'inline preparation without overlay', 'preparation return and sidebar navigation', 'preparation responsive independent scrolling', 'label submission error and idempotent retry',
      'independent dataset and annotation routes', 'direct annotation URL and reload', 'browser back and forward module ownership',
      'annotation task URL reload and legacy links', 'annotation batch form history without model calls',
      'label parameter validation', 'label product scope', 'label live progress', 'label concurrency', 'label reload recovery', 'label skip completed',
      'database import rows', 'database annotations and opinions', 'duplicate import', 'delete imported data', 'delete task history', 'label to analysis scope', 'mobile workspace', 'backend operation logs',
      'advanced parameters collapsed', 'main action above fold at 1366x768', 'draft reload recovery', 'one click prepared analysis',
      'reference sidebar and responsive workspace', 'keyboard task page and empty state', 'history pagination and result restore', 'mobile task page', 'history error recovery',
      'task page without overlay and active navigation', 'task page responsive independent scrolling', 'inline delete cancellation and selected task deletion',
      'Agent real tools and queue', 'Agent trend comparison and original evidence', 'Agent JSON download', 'Agent reload without model call', 'Agent mobile layout',
      'assistant route without overlays', 'chat keyboard send and multiline input', 'chat submission failure and idempotent retry',
      'ordered local conversation with single-turn requests', 'fixed chat input and independently scrolling messages',
      'chat presets remain available after replies', 'inline Agent evidence without dialog', 'legacy Agent URL and connection recovery'],
    browser_errors: browserErrors, layout_screenshots: layoutMeasurements.length, artifacts };
  await writeFile(path.join(artifacts, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  if (page) {
    await writeFile(path.join(artifacts, 'upload-events.json'), JSON.stringify(await page.evaluate(() => window.uploadEvents ?? []).catch(() => []), null, 2));
    await page.screenshot({ path: path.join(artifacts, 'failure-state.png'), fullPage: true }).catch(() => {});
    await writeFile(path.join(artifacts, 'failure-ui.txt'), await page.locator('body').innerText().catch(() => 'unavailable'));
  }
  await writeFile(path.join(artifacts, 'browser-errors.json'), JSON.stringify(browserErrors, null, 2));
  console.error(`Acceptance failed. Artifacts: ${artifacts}`);
  throw error;
} finally {
  await browser?.close();
  for (const child of children) if (child.exitCode === null) child.kill();
  await Promise.all(children.filter(child => child.exitCode === null).map(child => new Promise(resolve => child.once('exit', resolve))));
  if (modelServer) await new Promise(resolve => modelServer.close(resolve));
  logs.forEach(log => log.end());
}
