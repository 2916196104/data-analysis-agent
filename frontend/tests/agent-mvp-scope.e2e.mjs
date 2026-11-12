// Offline UI boundary checks. Every API request is intercepted; no backend or Agent executes.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const web = process.env.P1_WEB_URL || 'http://127.0.0.1:5175';
const output = path.resolve('../outputs/p1-browser');
await mkdir(output, { recursive: true });
const csv = { source: 'reviews_csv', dataset_version: 'reviews-csv', product_ids: ['100013588055'],
  model: 'offline-model', base_url: 'offline', prompt_version: 'offline', date_from: '2026-10-01', date_to: '2026-10-31',
  baseline_from: '2026-09-01', baseline_to: '2026-09-30' };
const amazon = { ...csv, source: 'amazon_reviews_2023', dataset_version: 'amazon-reviews-2023', product_ids: ['B0ZZZZZZZZ'] };
const product = scope => ({ key: scope.source, source: scope.source, dataset_version: scope.dataset_version,
  product_id: scope.product_ids[0], product_title: scope.source === 'reviews_csv' ? '055 合成空调' : 'Amazon 合成商品',
  review_count: 5, success_count: 5, pending_count: 0 });
const dataset = scope => ({ ...scope, review_count: 5, product_count: 1, eligible_text_reviews: 5,
  published_at_start: '2026-10-01T00:00:00Z', published_at_end: '2026-10-31T00:00:00Z', profiles: [] });
function savedTask(id, parameters) {
  return { task_id: id, kind: 'analysis', status: 'succeeded', parameters, parent_task_id: null, error: null,
    created_at: '2026-11-12T00:00:00Z', updated_at: '2026-11-12T00:00:00Z', started_at: null, finished_at: null,
    progress: { stage: 'agent_completed', completed: 0, total: 0, unit: 'tools' },
    result: { agent: true, status: 'unsupported', question: parameters.question, scope: parameters,
      answer: '离线界面检查：未执行任何业务分析。', results: {}, result_ids: [], query_executions: 0, total_model_calls: 0 } };
}
const legacy = savedTask('legacy-amazon', { ...amazon, question: '历史 Amazon 商品差评', conversation_id: 'legacy-conversation' });
const originalLegacy = structuredClone(legacy);
const tasks = new Map([[legacy.task_id, legacy]]);
const submissions = [], errors = [], unexpected = [], layouts = [], checks = [];
let available = true;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname.replace('/api/v1', '');
    let result;
    if (endpoint === '/options') result = { profiles: [{ profile_id: 1, requested_model: csv.model, base_url: csv.base_url, prompt_version: csv.prompt_version }],
      selected_profile_id: 1, products: [product(amazon), ...(available ? [product(csv)] : [])],
      datasets: [dataset(amazon), ...(available ? [dataset(csv)] : [])],
      aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {} };
    else if (endpoint === '/quality') result = { review_count: 10, product_count: 2,
      published_at_start: '2026-10-01', published_at_end: '2026-10-31' };
    else if (endpoint === '/health') result = { worker_available: true, business_database: 'offline-mock' };
    else if (endpoint === '/analytics/trend') result = { summary: { review_count: 5, eligible_text_reviews: 5,
      successful_reviews: 5, failed_reviews: 0, unprocessed_reviews: 0, labeling_coverage: 1,
      negative_review_count: 0, negative_rate: 0 }, buckets: [] };
    else if (endpoint === '/tasks' && request.method() === 'POST') {
      const input = request.postDataJSON(); submissions.push(input);
      assert.equal(input.parameters.source, csv.source); assert.equal(input.parameters.dataset_version, csv.dataset_version);
      const item = savedTask(`offline-${submissions.length}`, input.parameters); tasks.set(item.task_id, item);
      result = { task: item, reused: false };
    } else if (endpoint === '/tasks') result = { total: 1, page: 1, page_size: 20, items: [legacy] };
    else if (endpoint.startsWith('/public/tasks/')) result = { public_answer: { status: 'unsupported',
      summary: '离线界面检查：未执行任何业务分析。', highlights: [], caveats: [] } };
    else if (endpoint.startsWith('/tasks/')) result = tasks.get(decodeURIComponent(endpoint.slice(7)));
    if (result === undefined) { unexpected.push(`${request.method()} ${endpoint}`); return route.abort(); }
    await route.fulfill({ json: { status: 'ok', result } });
  });
  await page.goto(`${web}/assistant`);
  await page.getByRole('heading', { name: 'AI 评论分析助手' }).waitFor();
  await page.locator('.chat-input-toolbar').getByText('评论分析', { exact: true }).click();
  await page.getByLabel('评论分析问题', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('[aria-label="全局数据集"]')?.closest('.ant-select')?.textContent.includes('reviews-csv'));
  checks.push('MVP preferred when Amazon is first in catalog');
  await page.getByRole('button', { name: '修改范围', exact: true }).click();
  await page.getByRole('combobox', { name: '助手分析商品', exact: true }).click();
  await page.locator('.ant-select-item-option').filter({ hasText: '055 合成空调（5 条）' }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '应用范围', exact: true }).click();
  async function send(question) {
    const previous = submissions.length;
    await page.getByLabel('评论分析问题', { exact: true }).fill(question);
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('[aria-label="提交问题"]')?.classList.contains('ant-btn-loading'));
    assert.equal(submissions.length, previous + 1);
  }
  await send('商品 100013588055 有哪些差评？');
  assert.deepEqual(submissions[0].parameters.product_ids, csv.product_ids);
  assert.deepEqual(submissions[0].parameters.selection_scope.product_ids, csv.product_ids);
  await send('那好评呢？');
  await send('商品 XYZ-ABC123 有哪些差评？');
  await send('商品 B0ZZZZZZZZ 有哪些差评？');
  checks.push('A-D submit frozen CSV source, version and selected 055; mocked replies execute no Agent');
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: width === 768 ? 1024 : 900 });
    await page.getByRole('button', { name: '修改范围', exact: true }).click();
    await page.locator('.assistant-scope-editor').waitFor({ state: 'visible' });
    const screenshot = `scope-${width}.png`;
    await page.screenshot({ path: path.join(output, screenshot), fullPage: true, animations: 'disabled' });
    const layout = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      popovers: [...document.querySelectorAll('.ant-popover:not(.ant-popover-hidden)')].map(element => {
        const r = element.getBoundingClientRect(); return { x: r.x, right: r.right, y: r.y, bottom: r.bottom };
      }) }));
    layouts.push({ ...layout, screenshot });
    assert.ok(layout.scrollWidth <= width + 1, `horizontal overflow at ${width}`);
    for (const box of layout.popovers) assert.ok(box.x >= -1 && box.right <= width + 1, `popover overflow at ${width}`);
    await page.getByRole('button', { name: '取消', exact: true }).click();
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  async function selectDataset(name) {
    await page.locator('.header-actions .ant-select-selector').click();
    await page.locator('.ant-select-item-option').filter({ hasText: name }).click();
  }
  await selectDataset('amazon-reviews-2023');
  await page.getByText('AI 助手目前仅支持 reviews_csv / reviews-csv，请切换到该数据集；历史任务仍可按原范围查看。').waitFor();
  assert.ok(await page.getByLabel('评论分析问题', { exact: true }).isDisabled());
  const localDraft = await page.evaluate(() => JSON.parse(localStorage.getItem(
    Object.keys(localStorage).find(key => key.startsWith('review-workbench:')))).draft);
  assert.deepEqual(localDraft.productIds, []);
  await selectDataset('reviews-csv');
  await send('商品 100013588055 有哪些差评？');
  assert.equal(submissions.at(-1).parameters.context_task_id, undefined);
  checks.push('F switch clears products and old task references, including switching back');
  available = false;
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await page.getByText('AI 助手数据集 reviews_csv / reviews-csv 当前不可用，请先确认数据集；不会自动切换到其他来源。').waitFor();
  assert.ok(await page.getByLabel('评论分析问题', { exact: true }).isDisabled());
  await page.screenshot({ path: path.join(output, 'missing-dataset.png'), fullPage: true, animations: 'disabled' });
  checks.push('E removed MVP dataset leaves input disabled and does not select Amazon');
  available = true;
  const postsBeforeHistory = submissions.length;
  await page.goto(`${web}/assistant/tasks/legacy-amazon`);
  await page.locator('.chat-scope-summary').filter({ hasText: 'amazon-reviews-2023' }).waitFor();
  await page.getByText('查看完整查询范围', { exact: true }).click();
  await page.getByText('amazon_reviews_2023 / amazon-reviews-2023', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(output, 'legacy-amazon.png'), fullPage: true, animations: 'disabled' });
  assert.equal(submissions.length, postsBeforeHistory); assert.deepEqual(legacy, originalLegacy);
  checks.push('G historical Amazon source and product remain visible without resubmission');
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  await writeFile(path.join(output, 'report.json'), JSON.stringify({ mode: 'offline mocked API; no real backend or Agent execution',
    checks, layouts, submissions, errors, unexpected }, null, 2));
  console.log(JSON.stringify({ checks: checks.length, layouts: layouts.length, submissions: submissions.length, errors, output }));
} finally { await browser.close(); }
