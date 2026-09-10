import { expect, test } from '@playwright/test';
import { ID, detail, envelope, mockMarket } from '../fixtures/market';
import type { Units } from '../../src/types';

const inconsistentUnits: { name: string; change: Partial<Units> }[] = [
  { name: 'mixed currencies', change: { currency: 'HKD' } },
  { name: 'mixed price units', change: { price_unit: 'currency/lot' } },
  { name: 'unknown currency', change: { currency: null } },
  { name: 'unknown price unit', change: { price_unit: null } },
];

for (const id of ['TEST001', 'TEST002']) {
  for (const { name, change } of inconsistentUnits) {
    test(`${id} suppresses history with ${name} while preserving exact table data`, async ({ page }) => {
      await mockMarket(page);
      const data = detail(id);
      Object.assign(data.history[1], change, { close: '9007199254740993.1234' });
      await page.route(`**/securities/${id}`, route => route.fulfill({ json: envelope(data) }));
      await page.goto(`/?snapshot=${ID}&view=stock&security=${id}`);
      await expect(page.getByRole('heading', { name: data.security.name!, exact: true })).toBeVisible();
      await expect(page.getByRole('alert').filter({ hasText: '数据口径不一致' })).toBeVisible();
      await expect(page.locator('.chart')).toHaveCount(0);
      await page.getByText('查看完整历史数据（21 个交易日）', { exact: true }).click();
      const table = page.getByRole('table', { name: '完整历史数据' });
      await expect(table.getByRole('row')).toHaveCount(22);
      await expect(table.getByRole('cell', { name: '9,007,199,254,740,993.1234', exact: true })).toBeVisible();
      const changedRow = table.getByRole('row').filter({ hasText: data.history[1].trade_date });
      await expect(changedRow).toContainText(change.currency === null || change.price_unit === null
        ? '未知' : change.currency || change.price_unit!);
    });
  }

  test(`${id} retains its normal single-unit history chart`, async ({ page }) => {
    await mockMarket(page);
    const data = detail(id);
    data.history[1].volume_unit = null;
    data.history[1].amount_unit = null;
    for (const index of [0, 8]) {
      Object.assign(data.history[index], { open: null, high: null, low: null, close: null, currency: null, price_unit: null });
    }
    await page.route(`**/securities/${id}`, route => route.fulfill({ json: envelope(data) }));
    await page.goto(`/?snapshot=${ID}&view=stock&security=${id}`);
    await expect(page.locator('.chart canvas')).toBeVisible();
    await expect(page.getByRole('alert').filter({ hasText: '数据口径不一致' })).toHaveCount(0);
    const chartType = await page.locator('.chart').evaluate(async element => {
      const url = performance.getEntriesByType('resource').map(entry => entry.name).find(name => name.includes('/echarts_core.js'));
      if (!url) throw new Error('ECharts module was not loaded');
      const echarts = await import(url);
      return echarts.getInstanceByDom(element).getOption().series[0].type;
    });
    expect(chartType).toBe(data.chart_type);
  });
}
