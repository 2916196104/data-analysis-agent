// 真实 PostgreSQL、独立任务库、配置模型、Vite 与 Chromium；断言计划和数据，不断言模型固定措辞。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

if (!process.argv.includes('--live')) throw new Error('Pass --live to use configured database and model');
const root = path.resolve(import.meta.dirname, '../..');
await mkdir(path.join(root, 'output/playwright'), { recursive: true });
const out = await mkdtemp(path.join(root, 'output/playwright/assistant-semantics-'));
const python = path.join(root, '.venv/Scripts/python.exe');
const children = [], logs = [], errors = [], rounds = [], screenshots = [];
const report = { out, source: 'real PostgreSQL + isolated SQLite task queue + configured model + Vite/Chromium', rounds, screenshots, errors };
let browser, page;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function port() { const s = net.createServer(); await new Promise(r => s.listen(0, '127.0.0.1', r)); const p = s.address().port; await new Promise(r => s.close(r)); return p; }
async function until(check, label, timeout = 180000) { const end = Date.now() + timeout; while (Date.now() < end) { if (await check()) return; await delay(250); } throw new Error(`Timeout: ${label}`); }
function launch(command, args, name, env = {}) {
  const log = createWriteStream(path.join(out, `${name}.log`)); logs.push(log);
  const child = spawn(command, args, { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1', SSLKEYLOGFILE: '', LABEL_EXECUTION_MODE: 'local', ...env } });
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child); return child;
}
async function py(mode) { return promisify(execFile)(python, ['-m', 'scripts.assistant_semantics_live', mode, '--output', out], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' } }); }
try {
  await py('prepare');
  const selected = JSON.parse(await readFile(path.join(out, 'selection.json'), 'utf8')); report.selection = selected;
  const apiPort = await port(), webPort = await port(), api = `http://127.0.0.1:${apiPort}/api/v1`, web = `http://127.0.0.1:${webPort}`;
  launch(python, ['-m', 'scripts.assistant_semantics_live', 'serve', '--output', out, '--port', String(apiPort), '--origin', web], 'api');
  launch(process.execPath, ['frontend/node_modules/vite/bin/vite.js', 'frontend', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: api });
  await until(async () => { try { return (await fetch(api + '/health', { signal: AbortSignal.timeout(1000) })).ok && (await fetch(web, { signal: AbortSignal.timeout(1000) })).ok; } catch { return false; } }, 'services', 60000);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(({ api, selected }) => {
    const key = `review-workbench:${api}`;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify([selected.source, selected.dataset_version]), productIds: selected.product_ids, profileId: selected.profile_id,
      current: ['2016-08-09', '2023-08-28'], baseline: ['2016-07-01', '2016-07-31'], note: '',
    } }));
  }, { api, selected });
  page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
  let posts = 0; page.on('request', r => { if (r.method() === 'POST' && r.url() === api + '/tasks') posts++; });
  await page.goto(web + '/assistant');
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  const input = page.getByRole('textbox', { name: '评论分析问题' });
  async function enter(question) { await input.fill(question); await page.getByRole('button', { name: '提交问题', exact: true }).click(); }
  async function send(question, kind) {
    const response = page.waitForResponse(r => r.url() === api + '/tasks' && r.request().method() === 'POST');
    await enter(question);
    const submitted = (await (await response).json()).result.task;
    let task;
    await until(async () => { task = (await (await fetch(api + '/tasks/' + submitted.task_id)).json()).result; return ['succeeded', 'failed', 'partial'].includes(task.status); }, question);
    await writeFile(path.join(out, `round-${rounds.length + 1}.json`), JSON.stringify(task, null, 2));
    const queries = Object.values(task.result?.results || {}).filter(item => item.kind !== 'context');
    rounds.push({ question, task_id: task.task_id, status: task.status, result_status: task.result?.status, validation: task.result?.validation?.status,
      parameters: { ...task.parameters, base_url: undefined, model_identity: undefined }, tool_kinds: queries.map(item => item.kind),
      model_calls: task.result?.model_calls, answer_model_calls: task.result?.answer_model_calls,
      total: queries[0]?.data.total, total_opinions: queries[0]?.data.total_opinions });
    console.log(JSON.stringify(rounds.at(-1)));
    assert.equal(task.status, 'succeeded', JSON.stringify(task.error)); assert.equal(task.result.status, 'ok', task.result.answer);
    assert.equal(task.result.validation.status, 'passed'); assert.deepEqual(queries.map(item => item.kind), [kind]);
    assert.deepEqual(task.result.scope.product_ids, selected.product_ids); assert.equal(task.result.scope.dataset_version, selected.dataset_version);
    assert.equal(task.parameters.date_from, selected.date_from); assert.equal(task.parameters.date_to, selected.date_to);
    assert.ok(task.parameters.conversation_id); assert.ok(task.result.public_answer.source_result_ids.length);
    for (const query of queries) {
      assert.deepEqual(query.data.scope.product_ids, selected.product_ids); assert.equal(query.data.scope.source, selected.source);
      assert.equal(query.data.scope.date_from, selected.date_from); assert.equal(query.data.scope.date_to, selected.date_to);
    }
    await until(async () => await input.isEnabled(), 'composer ready');
    return task;
  }
  async function capture(name) {
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.locator('.chat-turn').last().scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `${name}-${width}.png`), animations: 'disabled' });
      const measure = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
        messagesOverflow: document.querySelector('.chat-messages').scrollWidth > document.querySelector('.chat-messages').clientWidth + 1,
        inputBottom: document.querySelector('.chat-input-area').getBoundingClientRect().bottom,
        dialogCount: document.querySelectorAll('[role="dialog"]').length }));
      assert.ok(measure.documentWidth <= width + 1); assert.ok(!measure.messagesOverflow); assert.ok(measure.inputBottom <= 1001); assert.equal(measure.dialogCount, 0);
      screenshots.push({ name, ...measure });
    }
  }
  const first = await send('美的空调的使用效果主要集中在哪些方面？', 'opinion_breakdown');
  const second = await send('美的空调的使用效果主要集中在哪些地方？', 'opinion_breakdown');
  assert.deepEqual(first.result.query_plan.requirements, second.result.query_plan.requirements);
  assert.deepEqual(first.result.results.r1.data.groups, second.result.results.r1.data.groups);
  assert.ok(second.parameters.context_task_id);
  await capture('equivalent-question');
  await page.locator('.chat-turn').last().getByText('查看统计、证据与执行记录', { exact: true }).click();
  await page.getByText('观点文本分析 [r1]', { exact: true }).last().waitFor();
  await capture('breakdown-table');
  await send('这款空调用起来怎么样？', 'opinion_breakdown');
  await send('它的制冷效果怎么样？', 'opinion_breakdown');
  await send('消费者主要吐槽什么？', 'issues');
  await send('好评主要集中在哪些方面？', 'positive_aspects');
  await send('使用效果里面，哪些问题被提及最多？', 'opinion_breakdown');
  const comparison = await send('和上一时间段相比有什么变化？', 'window_comparison');
  assert.equal(comparison.result.results.r1.data.baseline.review_count, 0);
  const before = posts;
  await enter('换成另一款商品后，用户的评价有什么不同？');
  await page.locator('.chat-turn').last().getByText('需要澄清', { exact: true }).waitFor(); assert.equal(posts, before);
  await capture('missing-comparison');
  await send('这个商品有什么问题？', 'issues');
  const countBeforeRefresh = posts;
  await page.reload(); await page.locator('.chat-report').last().waitFor(); assert.equal(posts, countBeforeRefresh);
  assert.deepEqual(errors, []); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = String(error); process.exitCode = 1;
  if (page) await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => {});
} finally {
  if (browser) await browser.close();
  for (const child of children.filter(child => child.pid && child.exitCode === null)) await promisify(execFile)('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).catch(() => {});
  await py('verify').catch(error => { report.readonlyError = String(error); process.exitCode = 1; });
  for (const log of logs) log.end();
  await writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, out, rounds: rounds.length, screenshots: screenshots.length, error: report.error, readonlyError: report.readonlyError }));
}
