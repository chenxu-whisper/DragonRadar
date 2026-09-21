import { expect, test } from '@playwright/test';
import { ID, OLD, mockMarket } from '../fixtures/market';

test.beforeEach(async ({}, testInfo) => {
  test.skip(!testInfo.project.use.baseURL?.endsWith(':5174'), 'Requires the explicit reset server.');
});

test('six reset destinations retain a fixed historical snapshot', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?snapshot=${OLD}&date=2026-09-09`);
  const nav = page.getByRole('navigation', { name: '主导航' });
  await expect(nav.getByRole('link')).toHaveText(['总览', '消息', '梯队', '涨幅', '监管', '我的复盘']);
  for (const [name, view, reason] of [
    ['消息', 'news', '消息来源'],
    ['梯队', 'leaders', '角色计算'],
    ['监管', 'regulation', '正式规则'],
  ]) {
    await nav.getByRole('link', { name, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`view=${view}`));
    await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: '未就绪' })).toContainText(reason);
    await expect(page.getByRole('table', { name: '证券列表' })).toHaveCount(0);
  }
});

test('per-page filter and sort survive navigation, drawer and browser back', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10&q=TEST002&sort=amount&order=desc`);
  await expect(page.getByRole('button', { name: '合成测试证券2', exact: true })).toBeVisible();
  const original = new URL(page.url()).search;
  await page.getByRole('link', { name: '涨幅', exact: true }).click();
  await page.getByLabel('搜索证券', { exact: true }).fill('TEST003');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('button', { name: '最大回撤幅度', exact: true }).click();
  const analysis = new URL(page.url()).search;
  await page.getByRole('link', { name: '总览', exact: true }).click();
  await expect(page).toHaveURL(url => url.search === original);
  await page.getByRole('button', { name: '合成测试证券2', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('link', { name: '打开完整详情' }).click();
  await expect(page).toHaveURL(/view=stock/);
  await page.getByRole('link', { name: '返回研究列表' }).click();
  await expect(page).toHaveURL(url => url.search === original);
  await page.getByRole('link', { name: '涨幅', exact: true }).click();
  await expect(page).toHaveURL(url => url.search === analysis);
  await page.goBack();
  await expect(page).toHaveURL(url => url.search === original);
});

test('review is available without snapshots or owner calls, including denied sources', async ({ page }) => {
  const ownerRequests: string[] = [];
  page.on('request', request => {
    if (/\/api\/(owner|v1\/(notes|watchlist|session))/.test(request.url())) ownerRequests.push(request.url());
  });
  await mockMarket(page, { denied: true });
  await page.goto('/?view=review&security=TEST001');
  await expect(page.getByRole('region', { name: '本地复盘' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
  await expect(page.getByLabel('证券代码', { exact: true })).toHaveValue('TEST001');
  await expect(page.getByText('来源内容已清除', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/view=review/);
  await expect(page).toHaveURL(/security=TEST001/);
  await expect(page.getByRole('navigation').getByRole('link', { name: '我的复盘' })).toHaveAttribute('aria-current', 'page');
  expect(ownerRequests).toEqual([]);
});

test('standalone stock path returns to a usable root list', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/stock/TEST002?snapshot=${ID}&date=2026-09-10&q=TEST002&sort=amount&order=desc`);
  await expect(page.getByRole('heading', { name: '合成测试证券2', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '返回研究列表' }).click();
  await expect(page).toHaveURL(url => url.pathname === '/' && url.searchParams.get('q') === 'TEST002');
  await page.reload();
  await expect(page.getByRole('table', { name: '证券列表' })).toBeVisible();
});

test('empty source still loads local review after reload', async ({ page }) => {
  await mockMarket(page, { empty: true });
  await page.goto('/?view=review');
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '添加自选', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '保存笔记', exact: true })).toBeDisabled();
  await expect(page.getByText('选择快照后才能新建笔记', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/view=review/);
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
});

test('late historical responses cannot replace a newly selected snapshot', async ({ page }) => {
  await mockMarket(page, { delayOld: true });
  await page.goto(`/?snapshot=${ID}&date=2026-09-10`);
  await expect(page.getByRole('button', { name: '合成测试证券1', exact: true })).toBeVisible();
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-09');
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await page.getByLabel('交易日期', { exact: true }).fill('2026-09-10');
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await page.getByRole('link', { name: '消息', exact: true }).click();
  await page.waitForTimeout(750);
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page.getByLabel('交易日期', { exact: true })).toHaveValue('2026-09-10');
  await expect(page.getByRole('heading', { name: '消息', exact: true })).toBeVisible();
});

test('mobile navigation and local editor fit a 375px viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockMarket(page, { empty: true });
  await page.goto('/?view=review');
  await page.getByRole('button', { name: '展开导航' }).click();
  await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link')).toHaveCount(6);
  await expect(page.getByRole('heading', { name: '我的复盘', exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
});
