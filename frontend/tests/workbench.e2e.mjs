import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import { createServer } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

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
let concurrentCalls = 0;
let maxConcurrentCalls = 0;
const browserErrors = [];
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const artifacts = await mkdtemp(path.join(root, 'output/playwright/frontend-'));
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
    '--port', String(apiPort), '--import-root', artifacts, '--env-file', path.join(artifacts, 'unused.env'), '--allow-origin', webUrl], 'api', {
    LLM_MODEL_ID: 'SIMULATED-LABEL-MODEL', LLM_API_KEY: 'local-test-key',
    LLM_BASE_URL: `http://127.0.0.1:${modelServer.address().port}/v1`, LLM_MAX_RETRIES: '0',
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
  await page.locator('.context-summary').filter({ hasText: 'SIMULATED-frontend' }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: '分析问题备注' }).isVisible(), false);
  assert.equal(await page.getByRole('combobox', { name: '标注版本', exact: true }).isVisible(), false);
  const startBox = await page.getByRole('button', { name: '开始分析', exact: true }).boundingBox();
  assert.ok(startBox && startBox.y + startBox.height <= 768, 'main action must fit above the fold');
  // History lives in a dedicated drawer; the workspace always gets the full width.
  assert.equal(await page.locator('.desktop-sidebar').count(), 0);
  assert.equal((await page.locator('.workspace-main').boundingBox()).width, 1366);
  await page.getByRole('button', { name: '任务记录', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('heading', { name: '还没有任务记录', exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'history-empty.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '任务记录', exact: true }).waitFor({ state: 'hidden' });
  for (const width of [1024, 768]) {
    await page.setViewportSize({ width, height: 768 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'workspace overflows');
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await writeFile(path.join(artifacts, 'initial-ui.txt'), await page.locator('body').innerText());
  await page.screenshot({ path: path.join(artifacts, '01-workbench.png'), fullPage: true });

  // Select a real product and preview its actual persisted reviews.
  await page.getByRole('combobox', { name: '分析商品' }).click();
  await page.getByTitle('DEMO-A（48 条）').click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '预览数据', exact: true }).click();
  await page.getByText('使用顺手，效果不错。', { exact: true }).first().waitFor();
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

  // A real corrupt gzip file reaches the worker and produces a persisted failure.
  await page.getByRole('button', { name: '从服务端目录导入', exact: true }).click();
  assert.equal(await page.getByRole('textbox', { name: '数据集版本', exact: true }).isVisible(), false);
  await page.getByRole('textbox', { name: '评论文件相对路径', exact: true }).fill('broken.jsonl.gz');
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

  // A valid import also uses the actual server-side file contract.
  const extra = JSON.parse(await readFile(path.join(artifacts, 'extra.jsonl'), 'utf8'));
  await writeFile(path.join(artifacts, 'limited.jsonl'), Array.from({ length: 3 }, (_, index) =>
    JSON.stringify({ ...extra, user_id: `limited-demo-${index}` })).join('\n') + '\n');
  await page.getByRole('button', { name: '从服务端目录导入', exact: true }).click();
  await page.getByRole('textbox', { name: '评论文件相对路径', exact: true }).fill('limited.jsonl');
  await page.getByRole('spinbutton', { name: '本次最多新增评论数（可选）' }).fill('2');
  const importResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const importId = (await (await importResponse).json()).result.task.task_id;
  await until(async () => (await get(`/tasks/${importId}`)).status === 'succeeded', 'successful import');
  const limitedImport = await get(`/tasks/${importId}`);
  assert.equal(limitedImport.parameters.max_reviews, 2);
  assert.equal(limitedImport.result.imports[0].inserted_records, 2);
  assert.equal(limitedImport.result.imports[0].limit_reached, true);
  assert.equal(limitedImport.result.imports[0].scan_complete, false);
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
  await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).fill('2.5');
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  await page.getByText('请输入 1–1000 的整数', { exact: true }).waitFor();
  assert.equal(modelCalls, 0);
  await page.getByRole('spinbutton', { name: '每批标注数量', exact: true }).fill('2');
  assert.equal(await page.getByRole('spinbutton', { name: '并发数', exact: true }).isVisible(), false);
  await page.locator('summary').filter({ hasText: '处理设置' }).click();
  await page.getByRole('spinbutton', { name: '并发数', exact: true }).fill('2');
  await page.getByRole('checkbox', { name: '同时重试此前标注失败的评论' }).check();
  const labelResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交标注任务', exact: true }).click();
  const labeled = (await (await labelResponse).json()).result.task;
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
  await page.reload();
  await page.getByText('标注进度 2/2', { exact: true }).waitFor();
  await until(() => page.getByRole('button', { name: '分析这些评论', exact: true }).isEnabled(), 'prepared analysis ready');
  assert.equal(await page.locator('.json-view').isVisible(), false);
  await page.screenshot({ path: path.join(artifacts, '05-label-result.png'), fullPage: true });
  await page.getByRole('button', { name: '继续标注下一批', exact: true }).click();
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
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  await page.locator('.history-item').filter({ hasText: '准备数据 · 最多 2 条' }).last().click();
  await page.getByRole('dialog', { name: '任务记录', exact: true }).waitFor({ state: 'hidden' });
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
  await writeFile(path.join(artifacts, 'duplicate.jsonl'), (await readFile(path.join(artifacts, 'limited.jsonl'), 'utf8')).trim().split('\n').slice(0, 2).join('\n') + '\n');
  await page.getByRole('button', { name: '从服务端目录导入', exact: true }).click();
  await page.getByRole('textbox', { name: '评论文件相对路径', exact: true }).fill('duplicate.jsonl');
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
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  await page.locator('.history-item').filter({ hasText: '导入 limited.jsonl' }).first().click();
  await page.getByRole('dialog', { name: '任务记录', exact: true }).waitFor({ state: 'hidden' });
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

  // History preserves pagination and restores a real result; test rows only replace the list API.
  const historyRoute = '**/api/v1/tasks?*';
  const historyItem = { ...completed, task_id: taskId };
  const historyRows = Array.from({ length: 15 }, (_, index) => ({ ...historyItem, task_id: `history-fixture-${index}` }));
  await page.route(historyRoute, async route => {
    const currentPage = Number(new URL(route.request().url()).searchParams.get('page'));
    await route.fulfill({ json: { status: 'ok', result: { total: 16, page: currentPage, page_size: 15, items: currentPage === 2 ? [historyItem] : historyRows } } });
  });
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  const historyDrawer = page.getByRole('dialog', { name: '任务记录', exact: true });
  await until(async () => await historyDrawer.locator('.history-item').count() === 15, 'history first page');
  await page.screenshot({ path: path.join(artifacts, 'history-pagination.png'), animations: 'disabled' });
  await historyDrawer.getByTitle('下一页').click();
  await until(async () => await historyDrawer.locator('.history-item').count() === 1, 'history second page');
  await historyDrawer.locator('.history-item').click();
  await historyDrawer.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { name: question, exact: true }).waitFor();
  await page.getByRole('heading', { name: '从评论中，看到变化' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  await historyDrawer.locator('.history-item').first().waitFor();
  await until(async () => {
    const box = await historyDrawer.boundingBox();
    return box && box.width <= 391 && box.x >= -1 && box.x + box.width <= 391;
  }, 'history fits mobile viewport after opening');
  assert.ok(await historyDrawer.evaluate(element => element.scrollWidth <= element.clientWidth), 'history content overflows');
  assert.ok(await historyDrawer.locator('.history-title').first().evaluate(element => element.scrollWidth <= element.clientWidth), 'long history title overflows');
  await page.screenshot({ path: path.join(artifacts, 'history-mobile.png'), animations: 'disabled' });
  await historyDrawer.getByTitle('上一页').click();
  await until(async () => await historyDrawer.locator('.history-item').count() === 15, 'return to first history page');
  await historyDrawer.getByRole('button', { name: '关闭任务记录', exact: true }).click();
  await historyDrawer.waitFor({ state: 'hidden' });
  // A history request error has a retry action and recovers without losing the workspace.
  await page.unroute(historyRoute);
  await page.route(historyRoute, route => route.abort());
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  await historyDrawer.getByText('任务记录暂不可用', { exact: true }).waitFor();
  const historyRetry = page.waitForRequest(request => request.url().includes('/tasks?'));
  await historyDrawer.getByRole('button', { name: '重试任务记录', exact: true }).click();
  await historyRetry;
  await page.unroute(historyRoute);
  await historyDrawer.getByRole('button', { name: '刷新任务记录', exact: true }).click();
  await historyDrawer.getByText('任务记录暂不可用', { exact: true }).waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({ path: path.join(artifacts, 'history-desktop.png'), animations: 'disabled' });
  await historyDrawer.getByRole('button', { name: '关闭任务记录', exact: true }).click();
  await historyDrawer.waitFor({ state: 'hidden' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '新建分析', exact: true }).click();
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, '04-mobile.png'), fullPage: true, animations: 'disabled' });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'mobile body overflows');
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
    failed_task_id: failedId, retry_task_id: retried.task_id, imported_task_id: importId,
    checks: ['product selection', 'data preview', 'question note', 'real task and worker', 'snapshot statistics', 'chart',
      'JSON download equality', 'reload recovery', 'network reconnect', 'worker failure', 'explicit retry', 'limited server file import',
      'label parameter validation', 'label product scope', 'label live progress', 'label concurrency', 'label reload recovery', 'label skip completed',
      'database import rows', 'database annotations and opinions', 'duplicate import', 'delete imported data', 'delete task history', 'label to analysis scope', 'mobile workspace', 'backend operation logs',
      'advanced parameters collapsed', 'main action above fold at 1366x768', 'draft reload recovery', 'one click prepared analysis',
      'full width workspace at 1366, 1024 and 768', 'keyboard history drawer and empty state', 'history pagination and result restore', 'mobile history drawer', 'history error recovery'],
    browser_errors: browserErrors, artifacts };
  await writeFile(path.join(artifacts, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  if (page) {
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
