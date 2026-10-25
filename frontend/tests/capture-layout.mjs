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
      await page.evaluate(() => window.scrollTo(0, 0));
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
