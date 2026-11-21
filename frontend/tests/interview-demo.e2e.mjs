import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const webUrl = 'http://127.0.0.1:15180';
const apiUrl = 'http://127.0.0.1:18080/api/v1';
const output = path.resolve(import.meta.dirname, '../../output/playwright');
await mkdir(output, { recursive: true });
const artifacts = await mkdtemp(path.join(output, 'interview-demo-'));
const browser = await chromium.launch({ headless: true, channel: process.env.E2E_BROWSER_CHANNEL || 'chrome' });
const errors = [];
const externalRequests = [];
const screenshots = [];
const tasks = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => {
  if ([webUrl, new URL(apiUrl).origin].includes(new URL(route.request().url()).origin)) return route.continue();
  externalRequests.push(new URL(route.request().url()).origin);
  return route.abort();
});

async function capture(stage, focus) {
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: width === 1024 ? 768 : 900 });
    await page.waitForFunction(() => {
      const main = document.querySelector('.workspace-content');
      return document.documentElement.scrollWidth <= innerWidth + 1 && main.scrollWidth <= main.clientWidth + 1;
    }, undefined, { timeout: 5000 });
    if (focus) {
      await focus.evaluate(element => element.scrollIntoView({ block: 'center' }));
      assert.ok(await focus.evaluate(element => {
        const box = element.getBoundingClientRect();
        const messages = document.querySelector('.chat-messages').getBoundingClientRect();
        return box.top >= messages.top - 1 && box.bottom <= messages.bottom + 1;
      }), `${stage}: screenshot target is clipped at ${width}px`);
    }
    const filename = `${stage}-${width}.png`;
    await page.screenshot({ path: path.join(artifacts, filename), animations: 'disabled' });
    screenshots.push(filename);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function ask(question) {
  await page.getByRole('textbox', { name: '评论分析问题', exact: true }).fill(question);
  const submitted = page.waitForResponse(response => response.url() === apiUrl + '/tasks'
    && response.request().method() === 'POST');
  await page.getByRole('button', { name: '提交问题', exact: true }).click();
  const response = await submitted;
  const body = await response.json();
  assert.equal(response.status(), 202, JSON.stringify(body));
  const id = body.result.task.task_id;
  const turn = page.locator('.chat-turn').last();
  await turn.getByRole('region', { name: '分析回答', exact: true }).waitFor();
  const taskResponse = await page.request.get(apiUrl + '/tasks/' + id);
  assert.equal(taskResponse.status(), 200);
  const task = (await taskResponse.json()).result;
  assert.equal(task.status, 'succeeded');
  assert.equal(task.result.status, 'ok');
  assert.equal(task.result.validation.status, 'passed');
  assert.equal(task.result.total_model_calls, 0);
  assert.deepEqual(task.result.scope.product_ids, ['100013588055']);
  assert.equal(task.result.scope.dataset_version, 'reviews-csv');
  tasks.push({ question, id, modelCalls: task.result.total_model_calls });
  return { task, turn };
}

try {
  const health = (await (await page.request.get(apiUrl + '/health')).json()).result;
  assert.equal(health.worker_available, true);
  assert.equal(health.redis_worker_required, false);
  await page.goto(webUrl);
  await page.getByRole('heading', { name: '评论分析', exact: true }).waitFor();
  await page.getByRole('combobox', { name: '分析商品', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('正向趋势合成商品（13 条）', { exact: true }).click();
  await page.keyboard.press('Escape');
  await page.locator('canvas').first().waitFor();
  await capture('dashboard');

  await page.getByRole('button', { name: 'Agent 问答', exact: true }).click();
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  const monthly = await ask('按月分析物流好评占比趋势。');
  await monthly.turn.getByText(/2023-01：正向评论 1 条，分母 2 条含正向观点评论，占比 50\.0%/).waitFor();
  const denominator = await ask('按月分析物流好评占成功标注评论的比例趋势。');
  assert.equal(denominator.task.parameters.context_task_id, monthly.task.task_id);
  assert.equal(denominator.task.parameters.conversation_id, monthly.task.parameters.conversation_id);
  assert.deepEqual(denominator.task.result.scope, monthly.task.result.scope);
  const denominatorText = denominator.turn.getByText(/2023-01：正向评论 1 条，分母 3 条成功标注评论，占比 33\.3%/);
  await denominatorText.waitFor();
  await capture('denominator', denominatorText);

  const evidence = await ask('给我看 3 条物流差评原文。');
  assert.equal(evidence.task.result.public_answer.evidence_refs.length, 3);
  await evidence.turn.getByText('查看统计、证据与执行记录', { exact: true }).click();
  await evidence.turn.getByRole('button', { name: /查看原文 #/ }).first().click();
  const detail = evidence.turn.getByRole('region', { name: '数据详情', exact: true });
  await detail.getByText(/物流很慢/).waitFor();
  await capture('evidence', detail.getByRole('heading', { name: /原文证据 #/ }));
  await detail.getByRole('button', { name: '关闭数据详情', exact: true }).click();
  await detail.waitFor({ state: 'hidden' });
  assert.deepEqual(errors, []);
  assert.deepEqual(externalRequests, []);
  await writeFile(path.join(artifacts, 'acceptance.json'), JSON.stringify({
    passed: true, screenshots, tasks, errors, externalRequests,
  }, null, 2));
  console.log(`Interview demo passed: ${artifacts}`);
} catch (error) {
  await page.screenshot({ path: path.join(artifacts, 'failure.png') }).catch(() => {});
  await writeFile(path.join(artifacts, 'failure.txt'), String(error) + '\n' + await page.locator('body').innerText());
  console.error(`Interview demo failed: ${artifacts}`);
  throw error;
} finally {
  await browser.close();
}
