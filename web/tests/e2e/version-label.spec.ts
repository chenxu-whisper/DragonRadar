import { expect, test } from '@playwright/test';
import { mockMarket } from '../fixtures/market';

const layouts = [
  ...[375, 768, 1024, 1100, 1101, 1440].map(width => ({ width, zoom: 1 })),
  { width: 1440, zoom: 2 },
];

for (const { width, zoom } of layouts) {
  test(`snapshot version label stays on one line at ${width}px and CSS zoom ${zoom}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockMarket(page);
    await page.goto('/');
    const select = page.getByRole('combobox', { name: '快照版本', exact: true });
    await expect(select).toBeEnabled();
    await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
    await page.evaluate(scale => { document.documentElement.style.zoom = String(scale); }, zoom);

    const label = page.locator('.version-control');
    const text = await label.evaluate(element => {
      const node = [...element.childNodes].find(child =>
        child.nodeType === Node.TEXT_NODE && child.textContent?.trim(),
      );
      if (!node) throw new Error('Snapshot version label text is missing');
      const range = document.createRange();
      range.selectNodeContents(node);
      return {
        content: node.textContent?.trim(),
        lines: [...range.getClientRects()].filter(rect => rect.width > 0 && rect.height > 0).length,
      };
    });
    expect(text.content).toBe('快照版本');
    expect(text.lines, 'Snapshot version label must not split across lines').toBe(1);
    await expect(select).toBeInViewport({ ratio: 1 });
    expect(await label.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
