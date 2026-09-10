import { expect, test } from '@playwright/test';
import { ID, envelope, mockMarket, overview, snapshot } from '../fixtures/market';

for (const reducedMotion of ['reduce', 'no-preference'] as const) {
  test(`initial resource loading has a static hidden skeleton with ${reducedMotion}`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 900 });
    await page.emulateMedia({ reducedMotion });
    await mockMarket(page);
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/snapshots/${ID}`, async route => {
      await gate;
      await route.fulfill({ json: snapshot });
    });
    await page.goto(`/?snapshot=${ID}`);
    await expect(page.getByRole('status').filter({ hasText: '正在加载快照…' })).toBeVisible();
    const skeleton = page.locator('main .resource-skeleton');
    try {
      await expect(skeleton).toBeVisible();
      await expect(skeleton).toHaveAttribute('aria-hidden', 'true');
      await expect(skeleton).toHaveText('');
      const geometry = await skeleton.evaluate(element => [...element.children].map(child => {
        const rect = child.getBoundingClientRect();
        const style = getComputedStyle(child);
        return { width: rect.width, height: rect.height, animation: style.animationName, transition: style.transitionDuration };
      }));
      expect(geometry.length).toBeGreaterThanOrEqual(3);
      for (const row of geometry) {
        expect(row.width).toBeGreaterThan(0);
        expect(row.height).toBeGreaterThan(0);
        expect(row.animation).toBe('none');
        expect(row.transition).toBe('0s');
      }
      await expect(page.getByRole('table')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `.temp/playwright/loading-${reducedMotion}.png` });
    } finally {
      release();
    }
    await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
    await expect(skeleton).toHaveCount(0);
  });
}

test('refresh retains successful resource data without replacing it with a skeleton', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?snapshot=${ID}`);
  const section = page.getByRole('region', { name: '市场涨跌分布', exact: true });
  const table = page.getByRole('table', { name: '涨跌分布数据' });
  await expect(table).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/overview', async route => {
    await gate;
    await route.fulfill({ json: envelope(overview) });
  });
  await page.getByRole('button', { name: '刷新快照' }).click();
  try {
    await expect(section.getByRole('status')).toContainText('正在加载涨跌分布');
    await expect(table).toBeVisible();
    await expect(section.locator('.resource-skeleton')).toHaveCount(0);
  } finally {
    release();
  }
  await expect(section.getByRole('status')).toHaveCount(0);
});
