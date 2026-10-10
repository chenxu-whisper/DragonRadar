import { expect, test } from '@playwright/test';
import { ID, OLD, mockMarket, type MockState } from '../fixtures/market';

test('realtime feature flag disabled sends no realtime requests with active selections', async ({ page }) => {
  const realtimeRequests: string[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/realtime/')
      || url.pathname === '/api/market/sector-flow/intraday') {
      realtimeRequests.push(url.pathname);
    }
  });
  await mockMarket(page);
  await page.goto(
    `/?view=overview&snapshot=${ID}&date=2026-09-10&security=TEST001&sector=SEC001`,
  );
  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByRole('region', { name: '实时证据账本' })).toHaveCount(0);
  await page.waitForTimeout(100);
  expect(realtimeRequests).toEqual([]);
});

test('overview, server pagination, drawer focus and fullpage back preserve context', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByText('合成测试来源', { exact: false }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  const trigger = page.getByRole('button', { name: '合成测试证券51', exact: true });
  await trigger.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog').locator('canvas')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.getByRole('link', { name: '打开完整详情' }).click();
  await expect(page).toHaveURL(/view=stock/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('link', { name: '返回研究列表' }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(trigger).toBeVisible();
});

test('comparison maximum, common sessions and exact equivalent table', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/?view=analysis');
  for (let i = 1; i <= 5; i++) await page.getByLabel(`比较 TEST00${i}`, { exact: true }).check();
  await page.getByLabel('比较 TEST006', { exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('最多比较 5');
  await expect(page.getByLabel('比较 TEST006', { exact: true })).not.toBeChecked();
  await expect(page.getByRole('button', { name: '前往比较' })).toBeInViewport();
  await page.getByRole('button', { name: '前往比较' }).click();
  await expect(page.getByRole('heading', { name: '同区间比较' })).toBeInViewport();
  await expect(page.getByRole('heading', { name: '同区间比较' })).toBeVisible();
  await expect(page.getByRole('img', { name: /基准值为 100/ }).locator('canvas')).toBeVisible();
  await page.getByText('查看比较数据表', { exact: true }).click();
  await expect(page.getByRole('table', { name: '比较曲线数据' })).toBeVisible();
  await page.getByRole('button', { name: '清空比较' }).click();
  await expect(page.getByRole('heading', { name: '同区间比较' })).toHaveCount(0);
});

test('missing date has no latest fallback and clearing date is supported', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/');
  await page.getByLabel('交易日期', { exact: true }).fill('2020-01-01');
  await expect(page.getByText('该日期没有可用快照')).toBeVisible();
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2020-01-01');
  await expect(page.getByRole('table', { name: '证券列表' })).toHaveCount(0);
  await page.getByLabel('交易日期', { exact: true }).fill('');
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
});

test('empty store is usable without synthetic production content', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await page.goto('/');
  await expect(page.getByText('暂无可用快照')).toBeVisible();
  await expect(page.getByRole('button', { name: '刷新快照' })).toBeEnabled();
});

test('same-condition network failure retains timestamp with degraded notice then 403 clears all panes', async ({ page }) => {
  const state: MockState = {};
  await mockMarket(page, state);
  await page.goto('/');
  await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  state.offline = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '保留上次' }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  state.offline = false; state.denied = true;
  await page.getByRole('button', { name: '刷新快照' }).click();
  await expect(page.getByText(/来源访问已被拒绝/).first()).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('合成测试来源')).toHaveCount(0);
  await page.getByRole('link', { name: '区间分析', exact: true }).click();
  await expect(page.getByRole('table')).toHaveCount(0);
});

test('older direct snapshot and late results never relabel old data', async ({ page }) => {
  await mockMarket(page, { delayOld: true });
  await page.goto(`/?snapshot=${OLD}`);
  await expect(page.getByText(/版本性质.*回溯记录/)).toBeVisible();
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-10');
  await expect(page).toHaveURL(new RegExp(ID));
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09');
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-10');
  await expect(page).toHaveURL(new RegExp(ID));
  await page.waitForTimeout(750);
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2026-09-10');
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
});

test('history back restores search and invalid large comparison gets a notice', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/?compare=A,B,C,D,E,F');
  await expect(page.getByRole('alert').first()).toContainText('最多比较');
  await page.getByLabel('搜索证券', { exact: true }).fill('TEST002');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page.getByRole('button', { name: '合成测试证券2', exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel('搜索证券', { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
});

test('insufficient interval remains explicit, with no automatic reduction', async ({ page }) => {
  await mockMarket(page);
  await page.goto('/?view=analysis');
  await page.getByLabel('交易日间隔', { exact: true }).fill('250');
  await page.getByRole('button', { name: '应用区间' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '研究区间不可用' })).toBeVisible();
  await expect(page.getByLabel('交易日间隔', { exact: true })).toHaveValue('250');
});

for (const width of [375, 768, 1024, 1440]) {
  test(`responsive ${width}, table-only overflow and canvas`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockMarket(page);
    await page.goto('/');
    await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `.temp/playwright/overview-${width}.png` });
    await page.getByRole('button', { name: '合成测试证券1', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await page.getByRole('dialog').evaluate(el => el.getBoundingClientRect().width <= innerWidth)).toBe(true);
  });
}
