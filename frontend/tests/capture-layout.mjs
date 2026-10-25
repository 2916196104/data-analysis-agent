import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

const viewports = [
  { width: 1440, height: 900 }, { width: 1024, height: 768 },
  { width: 768, height: 1024 }, { width: 390, height: 844 },
];

// 在现有真实业务流程中采集页面，不用替换 API 来构造截图状态。
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
        const main = document.querySelector('.main-content');
        const header = document.querySelector('.topbar-inner');
        const toolbar = document.querySelector('.workspace-toolbar');
        return {
          documentWidth: document.documentElement.scrollWidth,
          main: rect(main), header: rect(header), toolbar: rect(toolbar),
          footer: rect(document.querySelector('.workspace-footer')),
          contentPadding: [main, header, toolbar].map(element => getComputedStyle(element).paddingLeft),
          dialogs: [...document.querySelectorAll('[role="dialog"]')].filter(visible).map(rect),
          clippedButtons: [...document.querySelectorAll('.header-actions button, .workspace-actions button')]
            .filter(visible).filter(element => element.scrollWidth > element.clientWidth + 1)
            .map(element => element.getAttribute('aria-label')),
          canvases: [...document.querySelectorAll('canvas')].filter(visible).map(rect),
          tableCount: [...document.querySelectorAll('table')].filter(visible).length,
          primaryColor: getComputedStyle(document.querySelector('.brand-mark')).backgroundColor,
        };
      });
      measurements.push({ name, viewport, screenshot, ...measurement });
      await writeFile(path.join(artifacts, 'layout-measurements.json'), JSON.stringify(measurements, null, 2));
      assert.ok(measurement.documentWidth <= viewport.width + 1, `${name}: page overflows at ${viewport.width}`);
      assert.equal(measurement.header.x, measurement.main.x, `${name}: header alignment`);
      assert.equal(measurement.toolbar.width, measurement.main.width, `${name}: toolbar alignment`);
      assert.equal(new Set(measurement.contentPadding).size, 1, `${name}: inconsistent horizontal spacing`);
      const padding = parseFloat(measurement.contentPadding[0]);
      assert.equal(measurement.footer.x, measurement.main.x + padding, `${name}: footer left alignment`);
      assert.equal(measurement.footer.width, measurement.main.width - padding * 2, `${name}: footer right alignment`);
      assert.deepEqual(measurement.clippedButtons, [], `${name}: clipped navigation text`);
      assert.equal(measurement.primaryColor, 'rgb(18, 117, 104)', 'brand token must remain green');
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
