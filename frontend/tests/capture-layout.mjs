import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

const viewports = [
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
          main: rect(document.querySelector('.main-content')),
          header: rect(document.querySelector('.topbar-inner')),
          sidebar: rect(document.querySelector('.workspace-sidebar')),
          footer: rect(document.querySelector('.workspace-footer')),
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
      assert.ok(measurement.documentHeight <= measurement.viewportHeight + 1, `${name}: document must not scroll`);
      assert.ok(measurement.main.x >= measurement.header.x, `${name}: main must be inside frame`);
      assert.equal(measurement.footer.x, measurement.main.x, `${name}: footer left alignment`);
      assert.equal(measurement.footer.width, measurement.main.width, `${name}: footer right alignment`);
      if (viewport.width > 700) {
        assert.ok(measurement.sidebar.width >= 160 && measurement.sidebar.width <= 230, 'sidebar width');
        assert.ok(measurement.main.x >= measurement.sidebar.x + measurement.sidebar.width + 20, 'sidebar content gap');
      } else assert.equal(measurement.sidebar.width, 0, 'mobile navigation collapses');
      assert.deepEqual(measurement.clippedButtons, [], `${name}: clipped navigation text`);
      assert.equal(measurement.primaryColor, 'rgb(78, 116, 104)', 'reference green token');
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
export async function checkShellScrolling(page, artifacts) {
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
      const mobile = viewport.width <= 700;
      if (mobile) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
      await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
      const before = await snapshot();
      assert.ok(before.scrollHeight > before.clientHeight, 'real analysis result must overflow its container');
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
      await page.screenshot({ path: path.join(artifacts, `scroll-${viewport.width}x${viewport.height}-bottom.png`), animations: 'disabled' });
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
      await page.screenshot({ path: path.join(artifacts, `scroll-${viewport.width}x${viewport.height}-top.png`), animations: 'disabled' });
      checks.push({ viewport, before, after, status: 'passed' });
      if (mobile) await page.getByRole('button', { name: '工作空间导航', exact: true }).click();
    }
    await writeFile(path.join(artifacts, 'scroll-measurements.json'), JSON.stringify(checks, null, 2));
  } finally {
    await page.setViewportSize(previous);
    await page.locator('.workspace-content').evaluate(element => element.scrollTo(0, 0));
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
