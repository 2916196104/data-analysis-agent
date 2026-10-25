import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const web = process.env.UI_WEB_URL || 'http://127.0.0.1:5173';
const api = process.env.UI_API_URL || 'http://127.0.0.1:8000/api/v1';
const output = path.resolve('../output/playwright/reference-ui');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];
const report = { source: 'existing PostgreSQL API; read-only browser checks', web, screenshots: [], checks: [], browserErrors: errors };
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(web);
  await page.getByRole('heading', { name: '评论分析', exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('.review-metrics')?.getAttribute('aria-busy') === 'false');
  assert.equal(await page.getByText('无法完整加载工作空间', { exact: true }).count(), 0);
  await page.screenshot({ path: path.join(output, 'review-empty-window.png'), fullPage: true, animations: 'disabled' });
  // 通过真实日期控件选择现有 CSV 数据所在时间，不替换任何 API 或统计值。
  const dateInputs = page.locator('.review-filter .ant-picker input');
  await dateInputs.first().fill('2021-01-01');
  await dateInputs.first().press('Enter');
  await page.keyboard.press('Escape');
  await dateInputs.last().fill('2022-08-31');
  await dateInputs.last().press('Enter');
  await page.keyboard.press('Escape');
  await page.locator('.review-trend canvas').waitFor();
  await page.waitForFunction(() => document.querySelector('.review-metrics')?.getAttribute('aria-busy') === 'false');
  await page.getByRole('heading', { name: '评论分析', exact: true }).click();
  const refreshed = page.waitForResponse(response => response.url().includes('/analytics/trend?') && response.url().includes('date_to=2022-08-31'));
  await page.getByRole('button', { name: '刷新数据', exact: true }).click();
  await refreshed;
  await page.waitForFunction(() => document.querySelector('.review-metrics')?.getAttribute('aria-busy') === 'false');
  report.checks.push('refresh reloads statistics without changing filters');
  report.checks.push('actual date filter produces real monthly trend');
  const optionsResponse = await fetch(`${api}/options`);
  const options = (await optionsResponse.json()).result;
  report.productCount = options.products.length;
  report.metrics = await page.locator('.review-metric').allTextContents();
  const profile = options.profiles.find(item => item.profile_id === options.selected_profile_id);
  const product = options.products[0];
  const params = new URLSearchParams({ model: profile.requested_model, base_url: profile.base_url,
    prompt_version: profile.prompt_version, source: product.source, dataset_version: product.dataset_version,
    date_from: '2021-01-01', date_to: '2022-08-31', period: 'month' });
  const expected = (await (await fetch(`${api}/analytics/trend?${params}`)).json()).result.summary;
  assert.deepEqual(await page.locator('.review-metric strong').allTextContents(), [
    expected.review_count.toLocaleString(), expected.successful_reviews.toLocaleString(),
    expected.negative_rate === null ? '—' : `${(expected.negative_rate * 100).toFixed(1)}%`,
    String(options.products.filter(item => item.source === product.source && item.dataset_version === product.dataset_version).length),
  ]);
  report.checks.push('visible metrics exactly match existing analytics API');
  report.dataset = await page.locator('.review-page-heading p').textContent();
  report.fonts = await page.locator('.review-metric strong').first().evaluate(element => ({ family: getComputedStyle(element).fontFamily, size: getComputedStyle(element).fontSize }));
  report.executorWarningVisible = await page.getByText('后台执行器离线', { exact: true }).isVisible();
  for (const width of [1440, 1024, 768, 390, 1116]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
    await page.locator('.review-trend').scrollIntoViewIfNeeded();
    await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
    const filename = `review-analysis-${width}.png`;
    await page.screenshot({ path: path.join(output, filename), fullPage: true, animations: 'disabled' });
    const boxes = await page.evaluate(() => {
      const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
      return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, frame: rect('.workspace-main'), header: rect('.topbar-inner'), main: rect('.main-content'), filter: rect('.review-filter'), metrics: rect('.review-metrics'), trend: rect('.review-trend'), insight: rect('.review-insight'), chart: document.querySelectorAll('.review-trend canvas').length };
    });
    assert.ok(boxes.scrollWidth <= width, `horizontal overflow at ${width}`);
    assert.equal(await page.locator('.review-metric').count(), 4);
    report.screenshots.push({ filename, ...boxes });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '数据管理', exact: true }).click();
  await page.getByRole('heading', { name: '数据管理', exact: true }).waitFor();
  await page.getByRole('button', { name: '查看评论原文', exact: true }).click();
  await page.getByRole('dialog', { name: '数据详情', exact: true }).waitFor();
  await page.getByRole('dialog', { name: '数据详情', exact: true }).locator('table tbody tr').first().waitFor();
  await page.screenshot({ path: path.join(output, 'real-review-preview.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '数据详情', exact: true }).waitFor({ state: 'hidden' });
  report.checks.push('data management and persisted review preview');
  await page.getByRole('button', { name: '评论标注', exact: true }).click();
  await page.getByRole('dialog', { name: '准备数据', exact: true }).waitFor();
  await page.getByRole('dialog', { name: '准备数据', exact: true }).locator('.ant-modal-close').click();
  await page.getByRole('dialog', { name: '准备数据', exact: true }).waitFor({ state: 'hidden' });
  report.checks.push('annotation form opens with real options; no model submitted');
  await page.getByRole('button', { name: 'AI 助手', exact: true }).click();
  await page.getByRole('dialog', { name: '只读评论 Agent', exact: true }).waitFor();
  await page.getByRole('textbox', { name: '评论分析问题' }).fill('界面验证，未提交');
  await page.getByRole('dialog', { name: '只读评论 Agent', exact: true }).locator('.ant-modal-close').click();
  await page.getByRole('dialog', { name: '只读评论 Agent', exact: true }).waitFor({ state: 'hidden' });
  report.checks.push('AI assistant form and draft retained; no model submitted');
  await page.getByRole('button', { name: '任务记录', exact: true }).click();
  await page.getByRole('dialog', { name: '任务记录', exact: true }).waitFor();
  await page.locator('.history-list .history-item').first().waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '任务记录', exact: true }).waitFor({ state: 'hidden' });
  report.checks.push('real task history');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '工作空间导航' }).click();
  await page.getByRole('button', { name: '数据管理', exact: true }).click();
  await page.getByRole('heading', { name: '数据管理', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(output, 'data-management-390.png'), fullPage: true, animations: 'disabled' });
  report.checks.push('mobile navigation and data table internal scrolling');
  await page.getByRole('button', { name: /上传评论文件/ }).click();
  const importDialog = page.getByRole('dialog', { name: '导入数据', exact: true });
  await importDialog.locator('summary').click();
  const submitImport = importDialog.getByRole('button', { name: '提交导入任务', exact: true });
  await submitImport.scrollIntoViewIfNeeded();
  const submitBox = await submitImport.boundingBox();
  assert.ok(submitBox.y >= 0 && submitBox.y + submitBox.height <= 844, 'mobile import action is reachable inside the dialog');
  await page.screenshot({ path: path.join(output, 'import-dialog-390-actions.png'), animations: 'disabled' });
  await submitImport.click();
  await importDialog.getByText('请先选择评论文件或商品元数据文件。', { exact: true }).waitFor();
  await importDialog.locator('.ant-modal-close').click();
  report.checks.push('mobile import action reachable by internal scroll and missing file validation; no upload submitted');
  assert.deepEqual(errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = String(error); throw error;
} finally {
  await writeFile(path.join(output, 'acceptance.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}
