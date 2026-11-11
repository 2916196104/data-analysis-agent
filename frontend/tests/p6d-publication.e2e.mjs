// 真 API / Worker / Vite / Chromium；临时 SQLite 与 P6-C 的固定供应商响应。
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createWriteStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(import.meta.dirname, '../..');
const out = path.join(root, 'output/playwright/p6d');
await mkdir(out, { recursive: true });
const children = [], logs = [], errors = [];
const report = { scenarios: [], layouts: [], errors };
let browser;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label) {
  const end = Date.now() + 45000;
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
  child.stdout.pipe(log); child.stderr.pipe(log); children.push(child);
  child.on('error', error => errors.push(`${name}: ${error.message}`));
  child.on('exit', code => { if (code && report.status === undefined) errors.push(`${name} exited ${code}`); });
}
try {
  const apiPort = await port(), webPort = await port();
  const origin = `http://127.0.0.1:${apiPort}`, api = origin + '/api/v1', web = `http://127.0.0.1:${webPort}`;
  launch(path.join(root, '.venv/Scripts/python.exe'), ['tests/p6d_browser_fixture.py', '--port', String(apiPort), '--origin', web], 'api');
  launch(process.execPath, ['frontend/node_modules/vite/bin/vite.js', 'frontend', '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], 'vite', { VITE_API_BASE_URL: api });
  console.log('Checking isolated services', api, web);
  await until(async () => (await fetch(api + '/health', { signal: AbortSignal.timeout(2000) })).ok
    && (await fetch(web, { signal: AbortSignal.timeout(2000) })).ok, 'services');
  console.log('Services ready; launching Chromium');
  const options = (await (await fetch(api + '/options')).json()).result;
  const profile = options.profiles.find(row => row.requested_model === 'independent-labels'); assert.ok(profile, JSON.stringify(options));
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true,
    permissions: ['clipboard-read', 'clipboard-write'] });
  await context.addInitScript(({ api, profile }) => {
    const key = `review-workbench:${api}`;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
      dataset: JSON.stringify(['amazon_reviews_2023', 'independent-state']), productIds: [], profileId: profile.profile_id,
      current: ['2023-02-01', '2023-02-28'], baseline: ['2023-01-01', '2023-01-31'], note: '', rangeConfirmed: true,
    } }));
  }, { api, profile });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  await page.goto(web + '/assistant');
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  async function ask(question, p6 = true) {
    console.log('Asking', question);
    await page.getByRole('textbox', { name: '评论分析问题' }).fill(question);
    const submitted = page.waitForResponse(r => r.url() === api + '/tasks' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    const body = await (await submitted).json(); assert.equal(body.status, 'ok', JSON.stringify(body));
    const id = body.result.task.task_id; let task;
    await until(async () => { task = (await (await fetch(api + '/tasks/' + id)).json()).result;
      return !['queued', 'running'].includes(task.status); }, 'task');
    await until(async () => await page.locator('.chat-turn').last().locator(p6 ? '.p6-results' : '.public-answer').count() > 0, 'rendered answer');
    if (p6) {
      const view = (await (await fetch(api + '/public/tasks/' + id)).json()).result;
      assert.deepEqual(view.p6_publication, task.result.p6_publication);
      report.scenarios.push({ task_id: id, task_status: task.status, agent_status: task.result.status,
        parent_status: view.p6_publication.parent_status, complete: task.result.task_acceptance.complete,
        required: view.p6_publication.required_aspects, published: view.p6_publication.publishable_aspects });
    }
    return task;
  }
  await ask('100013588053的差评主要集中在哪些方面？', false);
  const complete = await ask('具体体现在哪里？');
  assert.equal(complete.status, 'succeeded'); assert.equal(complete.result.task_acceptance.complete, true);
  let section = page.locator('.chat-turn').last().locator('.p6-results');
  assert.equal(await section.locator('.p6-dimension').count(), 3);
  assert.equal(await section.locator('.p6-topics > li').count(), 9);
  assert.equal(await section.locator('.p6-reference').count(), 9);
  await section.locator('.p6-reference summary').first().click();
  assert.ok((await section.locator('mark').first().innerText()).length > 0);
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 1000 }); await delay(250);
    await page.locator('.chat-messages').evaluate(scroller => {
      const target = scroller.querySelector('.chat-turn:last-child .p6-results');
      scroller.scrollTop += target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12;
    });
    const measurement = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      overflowing: [...document.querySelectorAll('.p6-results *')].filter(e => e.clientWidth && e.scrollWidth > e.clientWidth + 2).map(e => e.className),
      cards: [...document.querySelectorAll('.chat-turn:last-child .p6-dimension')].map(e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }) }));
    assert.ok(measurement.documentWidth <= width + 1, JSON.stringify(measurement));
    assert.equal(measurement.overflowing.length, 0, JSON.stringify(measurement));
    report.layouts.push(measurement);
    // 聊天内容在独立滚动容器里；截取真实视口，避免 element screenshot 产生裁切与空白。
    await page.screenshot({ path: path.join(out, `complete-${width}.png`) });
    const lastDimension = section.locator('.p6-dimension').last();
    for (const topic of await lastDimension.locator('.p6-topics > li').all()) {
      await topic.scrollIntoViewIfNeeded(); assert.ok(await topic.isVisible());
    }
    await lastDimension.locator('.p6-reference summary').last().click();
    await lastDimension.locator('.p6-reference').last().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, `third-dimension-${width}.png`) });
    await lastDimension.locator('.p6-reference summary').last().click();
  }
  async function copyAndExport(task, name) {
    const section = page.locator('.chat-turn').last().locator('.p6-results');
    await section.getByRole('button', { name: '复制回答' }).click();
    // Windows 剪贴板把 LF 转成 CRLF；只归一化换行，事实、状态与引用逐字一致。
    assert.equal((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n'), task.result.p6_publication.answer_text);
    const downloaded = page.waitForEvent('download');
    await section.getByRole('button', { name: '导出分析报告' }).click();
    const file = path.join(out, name + '.md'); await (await downloaded).saveAs(file);
    assert.equal(await readFile(file, 'utf8'), task.result.p6_publication.answer_text);
  }
  await copyAndExport(complete, 'complete-export');
  for (const mode of ['fail_service', 'fail_effectiveness', 'fail_logistics', 'empty_effectiveness', 'empty', 'budget']) {
    await fetch(origin + '/offline-scenario/' + mode, { method: 'POST' });
    // 每个场景使用同会话新验证排名，随后走完整 Worker 路径。
    await ask('100013588053的差评主要集中在哪些方面？', false);
    const task = await ask('具体体现在哪里？');
    section = page.locator('.chat-turn').last().locator('.p6-results');
    const partial = !['empty', 'budget'].includes(mode);
    assert.equal(await section.locator('.p6-dimension').count(), partial ? 2 : 0);
    assert.equal(task.status, partial ? 'partial' : mode === 'empty' ? 'succeeded' : 'failed');
    assert.equal(task.result.task_acceptance.complete, false);
    await copyAndExport(task, mode + '-export');
    if (mode === 'fail_effectiveness' || mode === 'empty') {
      await page.locator('.chat-messages').evaluate(scroller => {
        const target = scroller.querySelector('.chat-turn:last-child .p6-results');
        scroller.scrollTop += target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12;
      });
      await page.screenshot({ path: path.join(out, mode + '-768.png') });
    }
  }
  const metrics = await (await fetch(origin + '/offline-metrics')).json(); report.metrics = metrics;
  assert.equal(metrics.paid_model_calls, 0); assert.equal(metrics.embedding_calls, 0); assert.equal(metrics.external_network_attempts, 0);
  assert.deepEqual(errors, []);
  report.status = 'PASS';
} catch (error) { report.status = 'FAIL'; report.failure = String(error.stack || error); process.exitCode = 1; }
finally {
  await browser?.close();
  for (const child of children.reverse()) {
    if (process.platform === 'win32') await promisify(execFile)('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }).catch(() => {});
    else child.kill('SIGTERM');
  }
  for (const log of logs) log.end();
  await writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}
