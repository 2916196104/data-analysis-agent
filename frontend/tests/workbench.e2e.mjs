import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
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
const children = [];
const logs = [];
let browser;
let page;
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

try {
  await freePort(apiPort); await freePort(webPort);
  const fixture = launch(python, ['scripts/frontend_fixture.py', '--output', artifacts], 'fixture');
  await new Promise((resolve, reject) => {
    fixture.once('error', reject);
    fixture.once('exit', code => code === 0 ? resolve() : reject(new Error(`Fixture exited ${code}; see ${artifacts}`)));
  });
  launch(python, ['-m', 'data_analysis_agent.review_api_cli', 'serve', '--db', path.join(artifacts, 'reviews.sqlite3'),
    '--port', String(apiPort), '--import-root', artifacts, '--env-file', path.join(artifacts, 'unused.env'), '--allow-origin', webUrl], 'api');
  launch(process.execPath, [path.join(frontend, 'node_modules/vite/bin/vite.js'), frontend, '--host', '127.0.0.1', '--port', String(webPort)], 'vite', { VITE_API_BASE_URL: apiUrl });
  await until(async () => (await fetch(`${apiUrl}/health`)).ok, 'API ready');
  assert.equal((await get('/quality')).review_count, 96);
  assert.equal((await get('/options')).products.length, 2);
  await until(async () => (await fetch(webUrl)).ok, 'Vite ready');
  browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN', acceptDownloads: true });
  page = await context.newPage();
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.goto(webUrl);
  await writeFile(path.join(artifacts, 'page-title.txt'), await page.title());
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  await page.getByText('SIMULATED-frontend', { exact: true }).first().waitFor();
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
  const question = '验收：DEMO-A 的负面问题有哪些变化？';
  await page.getByRole('textbox', { name: '分析问题备注' }).fill(question);
  const submitted = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  const submission = (await (await submitted).json()).result;
  const taskId = submission.task.task_id;
  assert.deepEqual(submission.task.parameters.product_ids, ['DEMO-A']);
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
  await page.getByRole('spinbutton', { name: '本次最多新增评论数（可选）' }).fill('1');
  const importResponse = page.waitForResponse(response => response.url() === `${apiUrl}/tasks` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交导入任务', exact: true }).click();
  const importId = (await (await importResponse).json()).result.task.task_id;
  await until(async () => (await get(`/tasks/${importId}`)).status === 'succeeded', 'successful import');
  const limitedImport = await get(`/tasks/${importId}`);
  assert.equal(limitedImport.parameters.max_reviews, 1);
  assert.equal(limitedImport.result.imports[0].inserted_records, 1);
  assert.equal(limitedImport.result.imports[0].limit_reached, true);
  assert.equal(limitedImport.result.imports[0].scan_complete, false);
  assert.equal((await get('/quality')).review_count, 97);

  // Narrow layout has an accessible collapsible sidebar and no body overflow.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '展开侧栏', exact: true }).click();
  await page.getByRole('button', { name: '新建分析', exact: true }).click();
  await page.getByRole('button', { name: '新建分析', exact: true }).waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: '开始分析', exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, '04-mobile.png'), fullPage: true, animations: 'disabled' });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'mobile body overflows');
  assert.deepEqual(browserErrors, []);
  const report = { status: 'passed', simulated_data: true, api: apiUrl, frontend: webUrl, task_id: taskId, run_id: run.run_id,
    failed_task_id: failedId, retry_task_id: retried.task_id, imported_task_id: importId,
    checks: ['product selection', 'data preview', 'question note', 'real task and worker', 'snapshot statistics', 'chart',
      'JSON download equality', 'reload recovery', 'network reconnect', 'worker failure', 'explicit retry', 'limited server file import', 'mobile sidebar'],
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
  logs.forEach(log => log.end());
}
