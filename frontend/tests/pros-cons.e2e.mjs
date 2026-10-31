// 隔离 SQLite、真实任务队列和 Vite/Chromium；模型端点只计数，不提供回答。
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
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const out = await mkdtemp(path.join(root, 'output/playwright/pros-cons-'));
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const children = [], logs = [], errors = [], screenshots = [];
const report = { source: 'isolated SQLite + real API/worker + Vite/Chromium; simulated product reviews', artifacts: out };
let browser, page, modelCalls = 0;
const model = createServer((request, response) => { modelCalls++; response.writeHead(503); response.end(); });
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
  launch(python, ['-m', 'data_analysis_agent.cli.server', 'serve', '--db', path.join(out, 'reviews.sqlite3'),
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
  async function send(question) {
    await input.fill(question);
    const submitted = page.waitForResponse(response => response.url() === api + '/tasks' && response.request().method() === 'POST');
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    const id = (await (await submitted).json()).result.task.task_id;
    let task;
    await until(async () => { task = (await (await fetch(api + '/tasks/' + id)).json()).result; return ['succeeded', 'failed', 'partial'].includes(task.status); }, 'analysis');
    assert.equal(task.status, 'succeeded', JSON.stringify(task.error));
    await until(async () => await input.isEnabled(), 'answer shown');
    return task;
  }
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
  assert.equal(modelCalls, 0); assert.deepEqual(errors, []);
  await run(python, ['-c', `import hashlib,sys;from pathlib import Path;p=Path(sys.argv[1]);assert hashlib.sha256((p/'reviews.sqlite3').read_bytes()).hexdigest()==(p/'business-before.sha256').read_text()`, out], { cwd: root, windowsHide: true });
  report.status = 'passed'; report.modelCalls = modelCalls; report.screenshots = screenshots; report.errors = errors;
  report.checks = ['two named brands/four models from one page selection', 'four sentiment/group queries', 'zero model requests', 'public history read', 'copy answer', 'download snapshot preserves original selection and resolved groups', 'refresh restores answer', 'specific missing product explanation', 'business database unchanged'];
  await writeFile(path.join(out, 'task.json'), JSON.stringify(task, null, 2));
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
