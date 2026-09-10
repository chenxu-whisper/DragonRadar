import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { ID, OLD, envelope, mockMarket, overview, snapshot } from '../fixtures/market';

test('detail-only 403 clears overview, versions and open drawer, then reauthorizes without cached content', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  await page.route('**/securities/TEST001', route => route.fulfill({ status: 403, json: { error: 'Source access denied' } }));
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  await expect(page.getByRole('heading', { name: '来源访问已被拒绝' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.locator('option').filter({ hasText: '首版' })).toHaveCount(0);
  await page.getByRole('button', { name: '重新检查访问权限' }).click();
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
});

test('404 direct link keeps snapshot and date; never asks latest', async ({ page }) => {
  const urls: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/')) urls.push(request.url()); });
  await mockMarket(page);
  const missing = '00000000-0000-4000-8000-000000000099';
  await page.goto(`/?snapshot=${missing}&date=2020-01-01`);
  await expect(page.getByRole('alert').filter({ hasText: '未找到该快照' })).toBeVisible();
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2020-01-01');
  await expect(page).toHaveURL(new RegExp(missing));
  expect(urls.some(url => /snapshots\?limit/.test(url))).toBe(false);
});

test('a mismatching overview envelope is discarded', async ({ page }) => {
  await mockMarket(page);
  await page.route('**/overview', route => route.fulfill({ json: envelope(overview, OLD) }));
  await page.goto(`/?snapshot=${ID}`);
  await expect(page.getByRole('alert').filter({ hasText: '响应快照与当前选择不匹配' })).toBeVisible();
  await expect(page.getByRole('table', { name: '涨跌分布数据' })).toHaveCount(0);
});

test('server sort and page size are explicit and checkbox does not open details', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await page.getByRole('button', { name: '收盘价', exact: true }).click();
  await expect(page).toHaveURL(/sort=close/);
  const request = page.waitForRequest(req => req.url().includes('/securities?') && req.url().includes('page_size=20'));
  await page.getByRole('combobox', { name: '每页条数', exact: true }).selectOption('20');
  expect((await request).url()).toContain('sort=close');
  await page.getByLabel('比较 TEST001', { exact: true }).check();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('custom interval validation, reset, exact values and line gaps', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/?view=analysis');
  await page.getByLabel('区间方式').selectOption('custom');
  await page.getByRole('button', { name: '应用区间' }).click();
  await expect(page.getByRole('alert')).toContainText('完整的起止日期');
  await page.getByLabel('起始日期').fill('2026-08-13');
  await page.getByLabel('结束日期').fill('2026-09-10');
  await page.getByRole('button', { name: '应用区间' }).click();
  await expect(page).toHaveURL(/start=2026-08-13/);
  await page.getByRole('button', { name: '重置区间' }).click();
  await expect(page.getByLabel('交易日间隔', { exact: true })).toHaveValue('20');
  await page.getByRole('button', { name: 'TEST002', exact: true }).click();
  await page.getByText('查看完整历史数据（21 个交易日）', { exact: true }).click();
  await expect(page.getByRole('table', { name: '完整历史数据' })).toContainText('未知');
  const curve = await page.getByRole('dialog').locator('.chart').evaluate(async el => {
    const url = performance.getEntriesByType('resource').map(entry => entry.name).find(name => name.includes('/echarts_core.js'));
    if (!url) throw new Error('ECharts module was not loaded');
    const echarts = await import(url);
    const option = echarts.getInstanceByDom(el).getOption();
    return { connectNulls: option.series[0].connectNulls, gap: option.series[0].data[8], type: option.series[0].type };
  });
  await expect(page.getByRole('dialog').locator('canvas')).toBeVisible();
  expect(curve).toEqual({ connectNulls: false, gap: null, type: 'line' });
});

test('keyboard, reduced motion, 200 percent CSS scale and accessibility scans', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockMarket(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
  const drawing = await page.locator('.chart').first().evaluate(async el => {
    const canvas = el.querySelector('canvas')!;
    const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0, colored = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3]) painted++;
      if (pixels[i + 3] && Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 40) colored++;
    }
    const url = performance.getEntriesByType('resource').map(entry => entry.name).find(name => name.includes('/echarts_core.js'));
    if (!url) throw new Error('ECharts module was not loaded');
    const echarts = await import(url);
    return { painted, colored, animation: echarts.getInstanceByDom(el).getOption().animation };
  });
  expect(drawing.painted).toBeGreaterThan(500);
  expect(drawing.colored).toBeGreaterThan(100);
  expect(drawing.animation).toBe(false);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳至研究内容' })).toBeFocused();
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(result.violations).toEqual([]);
  await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '.temp/playwright/overview-scale200.png' });
  await page.evaluate(() => { document.documentElement.style.zoom = ''; });
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  await expect(page.getByRole('button', { name: '关闭详情' })).toBeFocused();
  const drawer = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(drawer.violations).toEqual([]);
  expect(errors).toEqual([]);
});

test('new date removes old rows until its matching summary arrives', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/snapshots/${OLD}`, async route => {
    await gate;
    await route.fulfill({ json: { ...snapshot, snapshot_id: OLD, trade_date: '2026-09-09' } });
  });
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09');
  await expect(page.getByRole('table', { name: '证券列表' })).toHaveCount(0);
  await expect(page.getByText('尚未载入匹配快照，来源与覆盖未知')).toBeVisible();
  release();
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
});
