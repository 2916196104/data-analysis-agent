// 阶段 5 前端验收：同一 Vite/Chromium 进程使用受控接口响应，验证 UI 状态和响应式布局。
// 统计数字来自本脚本的受控回答，不代表生产数据。
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(frontend);
const output = path.join(root, 'output', 'playwright', 'phase5-ui');
await mkdir(output, { recursive: true });
const pageUrl = 'http://phase5.local/assistant';
const apiBase = 'http://127.0.0.1:8000/api/v1';
const dist = path.join(frontend, 'dist');
const scope = { source: 'fixture', dataset_version: 'reviews-csv', product_ids: ['P-1'], model: 'fixture-model',
  base_url: 'https://model.invalid/v1', prompt_version: 'fixture-v1', date_from: '2023-02-01', date_to: '2023-02-28',
  baseline_from: '2023-01-01', baseline_to: '2023-01-31' };
const summary = { review_count: 24, eligible_text_reviews: 24, successful_reviews: 20, failed_reviews: 1,
  unprocessed_reviews: 3, negative_review_count: 5, labeling_coverage: 20 / 24, negative_rate: 0.25 };
const positive = { result_id: 'r1', parameters: {}, kind: 'positive_aspects', data: {
  analysis_type: 'positive_aspects', sentiment: 'positive', data_status: 'ok', scope,
  summary: { ...summary, positive_review_count: 12 }, aspects: [
    { rank: 1, aspect: 'effectiveness', aspect_label: '使用效果', positive_review_count: 8, positive_rate: 8 / 12,
      positive_success_rate: 8 / 20, positive_rate_denominator: 12, positive_success_rate_denominator: 20 },
    { rank: 2, aspect: 'logistics', aspect_label: '物流', positive_review_count: 5, positive_rate: 5 / 12,
      positive_success_rate: 5 / 20, positive_rate_denominator: 12, positive_success_rate_denominator: 20 },
  ],
} };
const trend = { result_id: 'r2', parameters: { period: 'month' }, kind: 'trend', data: { period: 'month', summary,
  buckets: [{ ...summary, bucket: '2023-02' }, { ...summary, bucket: '2023-03', negative_rate: 0.2 }] } };
const evidence = { result_id: 'r3', parameters: { page: 1 }, kind: 'evidence', data: {
  total: 2, page: 1, items: [{ review_id: 1, product_id: 'P-1', text: '包装完整，物流很快。', published_at: '2023-02-01',
    evidence_ref: 'fixture:1', opinion_source: 'model', opinions: [{ aspect: 'logistics', sentiment: 'positive', quote: '物流很快', quote_start: 6, quote_end: 10 }] }],
} };
let taskNumber = 0;
const tasks = new Map();
const isTrend = question => question.includes('趋势') || question.includes('变化');
const answer = (question, generationStatus = 'template') => ({ schema_version: '1.0', status: 'ok',
  summary: isTrend(question) ? '包装和物流的负面占比均基于已有标注样本，变化需要结合各时段样本量判断。'
    : question.includes('原文') ? '已找到当前筛选条件下的评论证据。' : '根据当前评论数据，好评主要集中在使用效果和物流。',
  highlights: isTrend(question) ? [
    { finding_ref: 'finding:packaging', aspect_key: 'packaging', scope_ref: 'scope:current', source_result_ids: ['r2'], text: '包装在已有标注样本中的负面占比有升有降、呈现波动。' },
    { finding_ref: 'finding:logistics', aspect_key: 'logistics', scope_ref: 'scope:current', source_result_ids: ['r2'], text: '物流在已有标注样本中的负面占比呈下降变化。' },
  ] : question.includes('原文') ? [{ finding_ref: 'finding:evidence', aspect_key: 'logistics', scope_ref: 'scope:current', source_result_ids: ['r3'], text: '物流相关原文：“物流很快”。' }]
    : [{ finding_ref: 'finding:effectiveness', aspect_key: 'effectiveness', scope_ref: 'scope:current', source_result_ids: ['r1'], text: '使用效果：8 条，占正向观点评论的 66.7%。' },
      { finding_ref: 'finding:logistics', aspect_key: 'logistics', scope_ref: 'scope:current', source_result_ids: ['r1'], text: '物流：5 条，占正向观点评论的 41.7%。' }],
  metrics: [], sample_note: '本次分析范围共 24 条评论，基于 20 条成功标注评论。', evidence_refs: [], caveats: [],
  generation_status: generationStatus, source_result_ids: question.includes('趋势') ? ['r2'] : question.includes('原文') ? ['r3'] : ['r1'],
  scope_summary: [], details_available: true });
function taskFor(id, question, status = 'succeeded', generationStatus = 'template') {
  const resultIds = isTrend(question) ? ['r2'] : question.includes('原文') ? ['r3'] : ['r1'];
  const resultMap = isTrend(question) ? { r2: trend } : question.includes('原文') ? { r3: evidence } : { r1: positive };
  return { task_id: id, kind: 'analysis', status, parameters: { ...scope, question }, result: {
    agent: true, status: status === 'succeeded' ? 'ok' : 'error', question, answer: '[r1] internal answer', public_answer: status === 'succeeded' ? answer(question, generationStatus) : undefined,
    scope, snapshot_at: '2026-10-30T00:00:00Z', result_ids: resultIds, results: resultMap, validation: { status: 'passed', checks: [] },
    model_calls: 1, answer_model_calls: generationStatus === 'llm' ? 1 : 0, total_model_calls: generationStatus === 'llm' ? 2 : 1,
    tool_calls: 1, elapsed_seconds: 0.2, trace: [],
  }, error: status === 'succeeded' ? null : { code: 'timeout', message: '模拟任务失败' }, parent_task_id: null,
  created_at: '2026-10-30T00:00:00Z', started_at: '2026-10-30T00:00:00Z', finished_at: '2026-10-30T00:00:01Z', updated_at: '2026-10-30T00:00:01Z',
  progress: { stage: status === 'succeeded' ? 'agent_completed' : 'agent_tools', completed: 1, total: 1, unit: 'tools' } };
}

const browser = await chromium.launch({ headless: true, channel: 'chrome', args: ['--no-proxy-server', '--proxy-bypass-list=*', '--allow-file-access-from-files'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.addInitScript(({ key }) => {
  if (!crypto.randomUUID) Object.defineProperty(crypto, 'randomUUID', { value: () => `phase5-${Date.now()}-${Math.random().toString(16).slice(2)}` });
  localStorage.setItem(key, JSON.stringify({ selected: null, notes: {}, draft: {
    dataset: JSON.stringify(['fixture', 'reviews-csv']), productIds: ['P-1'], profileId: 1, note: '',
    current: ['2023-02-01', '2023-02-28'], baseline: ['2023-01-01', '2023-01-31'],
  } }));
}, { key: `review-workbench:${apiBase}` });
await page.route('http://phase5.local/**', async route => {
  const pathname = new URL(route.request().url()).pathname;
  const relative = pathname === '/' || pathname === '/assistant' ? 'index.html' : pathname.replace(/^\//, '');
  const file = path.join(dist, relative);
  try {
    const body = await readFile(file);
    const type = relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ body, contentType: type });
  } catch { return route.fulfill({ status: 404, body: 'not found' }); }
});
await page.route(`${apiBase}/**`, async route => {
  const request = route.request();
  const url = new URL(request.url());
  const id = url.pathname.match(/\/tasks\/([^/]+)/)?.[1];
  if (request.method() === 'POST' && url.pathname === '/api/v1/tasks') {
    const body = JSON.parse(request.postData() || '{}');
    const question = body.parameters?.question || '';
    const taskId = `phase5-${++taskNumber}`;
    const item = taskFor(taskId, question, question.includes('失败') ? 'failed' : 'succeeded', question.includes('超时') ? 'fallback' : isTrend(question) ? 'llm' : 'template');
    tasks.set(taskId, item);
    return route.fulfill({ json: { status: 'ok', result: { task: item, reused: false } } });
  }
  if (request.method() === 'POST' && id && url.pathname.endsWith('/retry')) {
    const original = tasks.get(id); const next = taskFor(`${id}-retry`, original?.parameters.question || '重试', 'succeeded', 'fallback');
    tasks.set(next.task_id, next); return route.fulfill({ json: { status: 'ok', result: { task: next, reused: false } } });
  }
  if (request.method() === 'GET' && id && tasks.has(id)) return route.fulfill({ json: { status: 'ok', result: tasks.get(id) } });
  if (url.pathname === '/api/v1/options') return route.fulfill({ json: { status: 'ok', result: {
    profiles: [{ profile_id: 1, requested_model: 'fixture-model', base_url: 'https://model.invalid/v1', prompt_version: 'fixture-v1' }], selected_profile_id: 1,
    products: [{ key: 'P-1', source: 'fixture', dataset_version: 'reviews-csv', product_id: 'P-1', product_title: '美的空调', review_count: 24, success_count: 20, pending_count: 3 }],
    datasets: [{ source: 'fixture', dataset_version: 'reviews-csv', review_count: 24, product_count: 1, eligible_text_reviews: 24, published_at_start: '2023-01-01', published_at_end: '2023-02-28', profiles: [{ profile_id: 1, successful_reviews: 20, labeling_coverage: 20 / 24 }] }],
    aspects: {}, sentiments: {}, processing_statuses: {}, human_statuses: {},
  } } });
  if (url.pathname === '/api/v1/health') return route.fulfill({ json: { status: 'ok', result: { worker_available: true, business_database: 'ok' } } });
  if (url.pathname === '/api/v1/quality') return route.fulfill({ json: { status: 'ok', result: { review_count: 24, product_count: 1, published_at_start: '2023-01-01', published_at_end: '2023-02-28' } } });
  if (url.pathname.startsWith('/api/v1/analytics/trend')) return route.fulfill({ json: { status: 'ok', result: { summary, buckets: [] } } });
  if (url.pathname === '/api/v1/tasks') return route.fulfill({ json: { status: 'ok', result: { total: tasks.size, page: 1, page_size: 15, items: [...tasks.values()] } } });
  return route.fulfill({ json: { status: 'ok', result: {} } });
});

const screenshots = [];
async function capture(name, target) {
  for (const width of [1440, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    if (target) await target.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, `${name}-${width}.png`), animations: 'disabled' });
    const measure = await page.evaluate(() => ({ width: innerWidth, documentWidth: document.documentElement.scrollWidth,
      messagesOverflow: document.querySelector('.chat-messages')?.scrollWidth > document.querySelector('.chat-messages')?.clientWidth + 1,
      outerOverflow: document.querySelector('.workspace-content')?.scrollHeight > document.querySelector('.workspace-content')?.clientHeight + 1 }));
    assert.ok(measure.documentWidth <= width + 1, `${name}: page overflow at ${width}`);
    assert.equal(measure.messagesOverflow, false, `${name}: chat overflow at ${width}`);
    assert.equal(measure.outerOverflow, false, `${name}: content overflow at ${width}`);
    screenshots.push({ name, width, ...measure });
  }
}
async function send(question) {
  const turns = await page.locator('.chat-turn').count();
  await page.getByRole('textbox', { name: '评论分析问题' }).fill(question);
  await page.getByRole('button', { name: '提交问题', exact: true }).click();
  await page.locator('.chat-turn').nth(turns).waitFor();
  await page.locator('.chat-turn').nth(turns).locator('.public-answer').waitFor();
}

try {
  await page.goto(pageUrl);
  await page.getByLabel('问答模式').getByText('评论分析', { exact: true }).click();
  await page.getByText('当前数据集：reviews-csv', { exact: false }).waitFor();
  await send('好评主要集中在哪些方面？');
  const positiveReply = page.locator('.public-answer').last();
  assert.doesNotMatch(await positiveReply.innerText(), /\[r\d+\]|base_url|fixture-model|https:\/\//);
  assert.equal(await page.locator('.chat-turn-scope').last().evaluate(element => element.open), false);
  await positiveReply.getByRole('button', { name: '复制回答', exact: true }).click();
  await capture('positive-answer', positiveReply);

  await send('按月查看包装和物流问题变化');
  const trendReply = page.locator('.public-answer').last();
  assert.match(await trendReply.innerText(), /包装/); assert.match(await trendReply.innerText(), /物流/);
  await capture('trend-answer', trendReply);

  await send('按月查看包装和物流问题变化，模拟解释超时');
  const fallbackReply = page.locator('.public-answer').last();
  await fallbackReply.getByText('解释生成未完成，已根据核验统计生成可靠摘要。').waitFor();
  await capture('fallback-answer', fallbackReply);

  await send('查询物流负面评论的原文');
  const evidenceTurn = page.locator('.chat-turn').last();
  await evidenceTurn.locator('.chat-report > summary').click();
  await evidenceTurn.getByText('继续查看下一页').waitFor();
  await evidenceTurn.getByText('继续查看下一页').click();
  assert.equal(await page.locator('.chat-turn').count() >= 5, true);
  const messages = page.locator('.chat-messages');
  await messages.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll', { bubbles: true })); });
  await page.getByRole('button', { name: '回到最新消息', exact: true }).waitFor();
  const report = { status: 'passed', source: 'controlled mock API + real Vite/Chromium page', screenshots, checks: ['public answer', 'copy', 'collapsed scope', 'trend and fallback', 'evidence paging', 'responsive overflow', 'scroll position preservation'] };
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
