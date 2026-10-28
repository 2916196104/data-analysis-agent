import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

const viewports = [
  { width: 1920, height: 900 },
  { width: 1440, height: 900 }, { width: 1024, height: 768 },
  { width: 768, height: 1024 }, { width: 390, height: 844 },
];

// 在原有业务流程中采集截图；针对新 Header + Sidebar 布局检查实际边界。
export async function captureLayout(page, artifacts, name, measurements) {
  const previous = page.viewportSize();
  try {
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
      const screenshot = `layout-${name}-${viewport.width}.png`;
      await page.screenshot({ path: path.join(artifacts, screenshot), fullPage: true, animations: 'disabled' });
      const measurement = await page.evaluate(() => {
        const rect = element => {
          const box = element.getBoundingClientRect();
          return { x: box.x, y: box.y, width: box.width, height: box.height };
        };
        const visible = element => element.getBoundingClientRect().width > 0 && getComputedStyle(element).visibility !== 'hidden';
        return {
          documentWidth: document.documentElement.scrollWidth,
          documentHeight: document.scrollingElement.scrollHeight,
          viewportHeight: innerHeight,
          shell: rect(document.querySelector('.workspace-main')),
          main: rect(document.querySelector('.main-content')),
          fillContent: document.querySelector('.workspace-content').classList.contains('workspace-content-fill'),
          header: rect(document.querySelector('.topbar-inner')),
          sidebar: rect(document.querySelector('.workspace-sidebar')),
          footer: document.querySelector('.workspace-footer') ? rect(document.querySelector('.workspace-footer')) : null,
          dialogs: [...document.querySelectorAll('[role="dialog"]')].filter(visible).map(rect),
          clippedButtons: [...document.querySelectorAll('.header-actions button, .workspace-actions button, .sidebar-nav button')]
            .filter(visible).filter(element => element.scrollWidth > element.clientWidth + 1)
            .map(element => element.getAttribute('aria-label') || element.textContent),
          canvases: [...document.querySelectorAll('canvas')].filter(visible).map(rect),
          tableCount: [...document.querySelectorAll('table')].filter(visible).length,
          primaryColor: getComputedStyle(document.querySelector('.brand-mark')).backgroundColor,
        };
      });
      measurements.push({ name, viewport, screenshot, ...measurement });
      await writeFile(path.join(artifacts, 'layout-measurements.json'), JSON.stringify(measurements, null, 2));
      assert.ok(measurement.documentWidth <= viewport.width + 1, `${name}: page overflows at ${viewport.width}`);
      assert.equal(measurement.shell.x, 0, 'workspace starts at viewport edge');
      assert.equal(measurement.shell.y, 0, 'workspace starts at viewport top');
      assert.equal(measurement.shell.width, viewport.width, 'workspace fills viewport width');
      assert.equal(measurement.shell.height, viewport.height, 'workspace fills viewport height');
      assert.ok(measurement.documentHeight <= measurement.viewportHeight + 1, `${name}: document must not scroll`);
      assert.ok(measurement.main.x >= measurement.header.x, `${name}: main must be inside frame`);
      if (measurement.footer) assert.equal(measurement.footer.x, measurement.main.x, `${name}: footer left alignment`);
      if (measurement.footer) assert.equal(measurement.footer.width, measurement.main.width, `${name}: footer right alignment`);
      if (viewport.width >= 992) {
        assert.equal(measurement.sidebar.width, 200, 'desktop sidebar keeps its fixed width');
        assert.ok(measurement.main.x >= measurement.sidebar.x + measurement.sidebar.width + (measurement.fillContent ? 0 : 16), 'sidebar and main do not overlap; regular pages retain their padding');
      } else assert.equal(measurement.sidebar.width, 0, 'mobile navigation collapses');
      assert.deepEqual(measurement.clippedButtons, [], `${name}: clipped navigation text`);
      assert.equal(measurement.primaryColor, 'rgb(9, 88, 217)', 'brand blue token');
      for (const dialog of measurement.dialogs) {
        assert.ok(dialog.x >= -1 && dialog.x + dialog.width <= viewport.width + 1, `${name}: dialog overflows`);
      }
      if (name === 'analysis-result') {
        assert.ok(measurement.canvases.some(canvas => canvas.width > 0 && canvas.height > 0), 'trend chart must render');
        assert.ok(measurement.tableCount > 0, 'analysis tables must render');
      }
    }
  } finally {
    await page.setViewportSize(previous);
  }
}

// 使用真实长结果和鼠标滚轮检查滚动边界，不增加或隐藏页面内容。
export async function checkShellScrolling(page, artifacts, prefix = 'scroll') {
  const previous = page.viewportSize();
  const checks = [];
  const snapshot = () => page.evaluate(() => {
    const rect = selector => {
      const box = document.querySelector(selector).getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height };
    };
    const content = document.querySelector('.workspace-content');
    return { header: rect('.topbar'), sidebar: rect('.workspace-sidebar'), tools: rect('.sidebar-tools'),
      content: rect('.workspace-content'), top: content.scrollTop, clientHeight: content.clientHeight,
      scrollHeight: content.scrollHeight, clientWidth: content.clientWidth, scrollWidth: content.scrollWidth,
      documentTop: document.scrollingElement.scrollTop, documentHeight: document.scrollingElement.scrollHeight,
      documentWidth: document.documentElement.scrollWidth, width: innerWidth, height: innerHeight };
  });
  try {
    for (const viewport of [...viewports, { width: 1440, height: 480 }, { width: 390, height: 480 }]) {
      await page.setViewportSize(viewport);
      const mobile = viewport.width < 992;
      if (mobile) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
      await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
      const before = await snapshot();
      assert.ok(before.scrollHeight > before.clientHeight, 'long page must overflow its content container');
      assert.ok(before.content.height > 100, 'navigation must leave room to scroll content');
      assert.ok(before.tools.y >= 0 && before.tools.y + before.tools.height <= viewport.height, 'sidebar status and actions stay in viewport');
      await page.mouse.move(before.content.x + before.content.width / 2, before.content.y + before.content.height / 2);
      await page.mouse.wheel(0, 100000);
      await page.waitForFunction(() => {
        const element = document.querySelector('.workspace-content');
        return Math.abs(element.scrollTop + element.clientHeight - element.scrollHeight) <= 1;
      });
      const after = await snapshot();
      assert.deepEqual(after.header, before.header, 'header moved with content');
      assert.deepEqual(after.sidebar, before.sidebar, 'sidebar moved with content');
      assert.deepEqual(after.tools, before.tools, 'sidebar tools moved with content');
      assert.equal(after.documentTop, 0, 'wheel must not scroll the document');
      assert.ok(after.documentHeight <= viewport.height + 1, 'double vertical scrolling');
      assert.ok(after.documentWidth <= viewport.width + 1 && after.scrollWidth <= after.clientWidth + 1, 'horizontal overflow');
      await page.screenshot({ path: path.join(artifacts, `${prefix}-${viewport.width}x${viewport.height}-bottom.png`), animations: 'disabled' });
      await page.mouse.wheel(0, -100000);
      await page.waitForFunction(() => document.querySelector('.workspace-content').scrollTop === 0);
      if (viewport.height === 480) {
        const menu = page.locator('.sidebar-nav');
        assert.ok(await menu.evaluate(element => element.scrollHeight > element.clientHeight), 'short viewport menu should scroll independently');
        const box = await menu.boundingBox();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.wheel(0, 10000);
        await page.waitForFunction(() => document.querySelector('.sidebar-nav').scrollTop > 0);
        const menuScrolled = await snapshot();
        assert.deepEqual(menuScrolled.tools, before.tools, 'menu scrolling moved the bottom status');
        assert.equal(menuScrolled.top, 0, 'menu scrolling moved main content');
        await menu.evaluate(element => element.scrollTo(0, 0));
      }
      await page.screenshot({ path: path.join(artifacts, `${prefix}-${viewport.width}x${viewport.height}-top.png`), animations: 'disabled' });
      checks.push({ viewport, before, after, status: 'passed' });
      if (mobile) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
    }
    await writeFile(path.join(artifacts, `${prefix}-measurements.json`), JSON.stringify(checks, null, 2));
  } finally {
    await page.setViewportSize(previous);
    await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
  }
}

export async function checkTaskReportsPage(page, artifacts) {
  const reports = page.locator('main .task-reports');
  await reports.getByRole('heading', { name: '任务与报告', exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog', { name: '任务记录', exact: true }).count(), 0);
  assert.equal(await page.locator('.ant-modal-mask:visible, .ant-drawer-mask:visible').count(), 0);
  assert.equal(await page.getByRole('button', { name: '任务记录', exact: true }).getAttribute('aria-current'), 'page');
  assert.ok(await reports.locator('.history-delete').evaluateAll(buttons => buttons.length > 0 && buttons.every(button => getComputedStyle(button).opacity === '1')), 'delete actions must remain visible without hover');
  assert.ok(await reports.evaluate(element => {
    const list = element.querySelector('.history-list');
    const pagination = element.querySelector('.task-reports-pagination');
    return pagination.parentElement === element && !!(list.compareDocumentPosition(pagination) & Node.DOCUMENT_POSITION_FOLLOWING)
      && !['fixed', 'sticky', 'absolute'].includes(getComputedStyle(pagination).position);
  }), 'pagination must follow list in normal page flow');
  await checkShellScrolling(page, artifacts, 'task-scroll');
  const previous = page.viewportSize();
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await reports.locator('.history-item').first().evaluate(element => {
      const icon = element.querySelector('.history-icon').getBoundingClientRect();
      const title = element.querySelector('.history-copy').getBoundingClientRect();
      const status = element.querySelector('.task-state').getBoundingClientRect();
      return Math.abs(title.top - icon.top) <= 2 && status.top >= title.bottom;
    }), 'mobile task title must align with icon, with status below');
  } finally {
    await page.setViewportSize(previous);
  }
}

export async function checkShortContent(page, artifacts) {
  const previous = page.viewportSize();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: '数据管理', exact: true }).click();
    const size = await page.locator('.workspace-content').evaluate(element => ({
      clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, overflow: getComputedStyle(element).overflowY,
    }));
    assert.equal(size.overflow, 'auto');
    assert.ok(size.scrollHeight <= size.clientHeight, 'short content should not need a scrollbar');
    await page.screenshot({ path: path.join(artifacts, 'scroll-short-content.png'), animations: 'disabled' });
    await page.getByRole('button', { name: '新建分析', exact: true }).click();
  } finally {
    await page.setViewportSize(previous);
  }
}

export async function checkPreparationPage(page, artifacts) {
  const previous = page.viewportSize();
  const checks = [];
  const navigate = async name => {
    const button = page.getByRole('button', { name, exact: true });
    if (!await button.isVisible()) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
    await button.click();
  };
  try {
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await navigate('数据管理');
      const rows = await page.locator('.data-management tbody').innerText();
      assert.equal(new URL(page.url()).pathname, '/data-management');
      assert.equal(await page.locator('.data-management').getByRole('button', { name: '准备所选数据', exact: true }).count(), 0);
      assert.equal(await page.locator('.data-management a').count(), 0, 'annotation-only review link must not belong to dataset page');
      await navigate('评论标注');
      await page.getByRole('heading', { name: '评论标注', exact: true }).waitFor();
      assert.equal(new URL(page.url()).pathname, '/annotation');
      assert.equal(await page.locator('.sidebar-nav button[aria-label="评论标注"]').getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('.sidebar-nav [aria-current="page"]').count(), 1);
      assert.equal(await page.locator('.sidebar-nav button[aria-label="数据管理"]').getAttribute('aria-current'), null);
      assert.equal(await page.getByRole('button', { name: '返回数据管理', exact: true }).count(), 0);
      await page.locator('.annotation-page summary').click();
      assert.equal(await page.getByRole('dialog', { name: '准备数据', exact: true }).count(), 0);
      assert.equal(await page.locator('.ant-modal-mask:visible, .ant-drawer-mask:visible').count(), 0);
      assert.equal(await page.getByRole('spinbutton', { name: '并发数', exact: true }).isVisible(), true);
      assert.equal(await page.getByRole('checkbox', { name: '同时重试此前标注失败的评论' }).isVisible(), true);
      const snapshot = () => page.evaluate(() => {
        const box = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
        const content = document.querySelector('.workspace-content');
        return { header: box('.topbar'), sidebar: box('.workspace-sidebar'), tools: box('.sidebar-tools'),
          content: box('.workspace-content'), top: content.scrollTop, clientHeight: content.clientHeight,
          scrollHeight: content.scrollHeight, overflow: content.scrollWidth > content.clientWidth + 1,
          documentTop: document.scrollingElement.scrollTop, documentHeight: document.scrollingElement.scrollHeight,
          documentWidth: document.documentElement.scrollWidth, width: innerWidth, height: innerHeight };
      });
      await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
      const before = await snapshot();
      assert.ok(!before.overflow && before.documentWidth <= viewport.width + 1, 'preparation horizontal overflow');
      assert.ok(before.documentHeight <= viewport.height + 1, 'preparation must not scroll document');
      await page.screenshot({ path: path.join(artifacts, `preparation-${viewport.width}-top.png`), animations: 'disabled' });
      await page.mouse.move(before.content.x + before.content.width / 2, before.content.y + before.content.height / 2);
      await page.mouse.wheel(0, 100000);
      await page.waitForFunction(() => { const element = document.querySelector('.workspace-content'); return Math.abs(element.scrollTop + element.clientHeight - element.scrollHeight) <= 1; });
      const after = await snapshot();
      assert.deepEqual(after.header, before.header, 'preparation scroll moved header');
      assert.deepEqual(after.sidebar, before.sidebar, 'preparation scroll moved sidebar');
      assert.deepEqual(after.tools, before.tools, 'preparation scroll moved sidebar status');
      assert.equal(after.documentTop, 0);
      assert.equal(await page.getByRole('button', { name: '提交标注任务', exact: true }).isVisible(), true);
      await page.screenshot({ path: path.join(artifacts, `preparation-${viewport.width}-bottom.png`), animations: 'disabled' });
      await page.goBack();
      await page.getByRole('heading', { name: '数据管理', exact: true }).waitFor();
      assert.equal(await page.locator('.data-management tbody').innerText(), rows, 'back must retain dataset list');
      assert.equal(await page.locator('.sidebar-nav button[aria-label="数据管理"]').getAttribute('aria-current'), 'page');
      await page.goForward();
      await page.getByRole('heading', { name: '评论标注', exact: true }).waitFor();
      await page.reload();
      await page.getByRole('combobox', { name: '标注数据集', exact: true }).waitFor();
      assert.equal(await page.locator('.sidebar-nav button[aria-label="评论标注"]').getAttribute('aria-current'), 'page');
      await navigate('新建分析');
      assert.equal(await page.locator('.annotation-page').count(), 0, 'navigation must leave annotation page');
      await page.getByRole('heading', { name: '评论分析', exact: true }).waitFor();
      checks.push({ viewport, before, after, status: 'passed' });
    }
    const direct = await page.context().newPage();
    try {
      await direct.goto(new URL('/annotation', page.url()).href);
      await direct.getByRole('combobox', { name: '标注数据集', exact: true }).waitFor();
      assert.equal(await direct.getByRole('button', { name: '评论标注', exact: true }).getAttribute('aria-current'), 'page');
      assert.equal(await direct.locator('.sidebar-nav [aria-current="page"]').count(), 1);
    } finally { await direct.close(); }
    await writeFile(path.join(artifacts, 'preparation-measurements.json'), JSON.stringify(checks, null, 2));
  } finally {
    await page.setViewportSize(previous);
  }
}

export async function checkAssistantChat(page, artifacts) {
  const previous = page.viewportSize();
  const checks = [];
  try {
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      const snapshot = () => page.evaluate(() => {
        const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
        const messages = document.querySelector('.chat-messages');
        const outer = document.querySelector('.workspace-content');
        return { header: rect('.topbar'), sidebar: rect('.workspace-sidebar'), tools: rect('.sidebar-tools'),
          heading: rect('.chat-heading'), input: rect('.chat-input-area'), messages: rect('.chat-messages'),
          top: messages.scrollTop, clientHeight: messages.clientHeight, scrollHeight: messages.scrollHeight,
          overflowX: messages.scrollWidth > messages.clientWidth + 1, outerTop: outer.scrollTop,
          outerHeight: outer.scrollHeight, outerClient: outer.clientHeight,
          documentTop: document.scrollingElement.scrollTop, documentHeight: document.scrollingElement.scrollHeight,
          documentWidth: document.documentElement.scrollWidth };
      });
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(await page.locator('.ant-modal-mask:visible, .ant-drawer-mask:visible').count(), 0);
      assert.equal(await page.locator('.sidebar-nav button[aria-label="AI 助手"]').getAttribute('aria-current'), 'page');
      const before = await snapshot();
      assert.ok(before.scrollHeight > before.clientHeight, 'real questions and replies must create a scrolling conversation');
      assert.equal(await page.locator('.workspace-footer, .chat-input-hint, .chat-context').count(), 0);
      const stack = await page.locator('.chat-message-stack').boundingBox();
      const inputBox = await page.locator('.chat-input-box').boundingBox();
      assert.ok(stack.width <= 960 && inputBox.width <= 960, 'conversation reading width');
      assert.ok(Math.abs(stack.x + stack.width / 2 - inputBox.x - inputBox.width / 2) <= 1, 'messages and composer align');
      assert.ok(before.clientHeight > 60 && before.input.y + before.input.height <= viewport.height, 'input stays inside content');
      assert.ok(!before.overflowX && before.documentWidth <= viewport.width + 1, 'chat horizontal overflow');
      assert.ok(before.documentHeight <= viewport.height + 1 && before.outerHeight <= before.outerClient + 1, 'chat must not create outer scrolling');
      await page.mouse.move(before.messages.x + before.messages.width / 2, before.messages.y + before.messages.height / 2);
      await page.mouse.wheel(0, -100000);
      await page.waitForFunction(() => document.querySelector('.chat-messages').scrollTop === 0);
      const top = await snapshot();
      await page.screenshot({ path: path.join(artifacts, `assistant-${viewport.width}-top.png`), animations: 'disabled' });
      await page.mouse.wheel(0, 100000);
      await page.waitForFunction(() => { const m = document.querySelector('.chat-messages'); return Math.abs(m.scrollTop + m.clientHeight - m.scrollHeight) <= 1; });
      const bottom = await snapshot();
      for (const key of ['header', 'sidebar', 'tools', 'heading', 'input']) assert.deepEqual(bottom[key], top[key], `chat scroll moved ${key}`);
      assert.equal(bottom.documentTop, 0);
      assert.equal(bottom.outerTop, 0);
      await page.screenshot({ path: path.join(artifacts, `assistant-${viewport.width}-bottom.png`), animations: 'disabled' });
      checks.push({ viewport, top, bottom, status: 'passed' });
    }
    await writeFile(path.join(artifacts, 'assistant-measurements.json'), JSON.stringify(checks, null, 2));
  } finally { await page.setViewportSize(previous); }
}
