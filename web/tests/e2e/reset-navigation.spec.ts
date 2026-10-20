import { expect, test, type Page } from '@playwright/test';
import {
  ID,
  OLD,
  mockLeaders,
  mockMarket,
  resetSummary,
  snapshot,
} from '../fixtures/market';

async function mockHistoricalRegulation(page: Page) {
  const response = (data: unknown) => ({
    ...resetSummary(),
    trade_date: '2026-09-09',
    snapshot_id: OLD,
    source: 'synthetic-navigation',
    source_version: 'navigation-v1',
    coverage: null,
    quality_flags: ['regulation_missing'],
    rule_version: null,
    data,
  });
  await page.route('**/api/regulation/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/regulation/status') {
      return route.fulfill({ json: response({
        region1_completeness: {
          status: 'missing',
          coverage_ratio: null,
          missing_count: null,
          source_check: 'unknown',
          expected: null,
          valid: null,
          excluded: null,
          missing: null,
        },
        region2_calc_state: [],
        region3_events: null,
      }) });
    }
    if (url.pathname === '/api/regulation/events') {
      return route.fulfill({ json: response({
        events: null,
        listed_count: null,
        total: null,
        source_check: 'unknown',
      }) });
    }
    return route.fulfill({ status: 404, json: {} });
  });
}

async function mockNavigationAnalysis(page: Page) {
  await page.route('**/api/rank/range?**', async route => {
    const url = new URL(route.request().url());
    const start = url.searchParams.get('start')!;
    const end = url.searchParams.get('end')!;
    return route.fulfill({ json: {
      ...resetSummary(snapshot),
      source: 'synthetic-navigation',
      source_version: 'navigation-v1',
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
      rule_version: 'navigation-v1',
      basis: {
        unit: 'ratio',
        currency: 'CNY',
        price_type: url.searchParams.get('adj'),
        benchmark: url.searchParams.get('benchmark'),
      },
      data: {
        window_days: 20,
        adjusted_start: start,
        adjusted_end: end,
        benchmark_return: 0.02,
        ranking: [{
          rank: 1,
          security_id: 'TEST003',
          name: '合成测试证券3',
          topics: [],
          topic_status: 'available',
          topic_reasons: [],
          reasons: [],
          return_ratio: 0.1,
          excess_return_ratio: 0.08,
          max_drawdown_ratio: 0.03,
          amount_change_ratio: 0.2,
        }],
        exclusions: [],
      },
    } });
  });
}

test.beforeEach(async ({}, testInfo) => {
  test.skip(!testInfo.project.use.baseURL?.endsWith(':5174'), 'Requires the explicit reset server.');
});

test('implemented reset destinations retain a fixed historical snapshot and honest evidence states', async ({ page }) => {
  await mockLeaders(page);
  await mockHistoricalRegulation(page);
  await page.goto(`/?snapshot=${OLD}&date=2026-09-09`);
  const nav = page.getByRole('navigation', { name: '主导航' });
  await expect(nav.getByRole('link')).toHaveText(['总览', '消息', '梯队', '涨幅', '监管', '我的复盘']);

  await nav.getByRole('link', { name: '消息', exact: true }).click();
  await expect(page).toHaveURL(/view=news/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await expect(page.getByRole('heading', { name: '消息', exact: true })).toBeVisible();
  // 词云与消息列表各自渲染一个 404 告警，断言必须收敛到列表面板自己的 alert。
  const listAlert = page.getByRole('alert')
    .filter({ has: page.getByRole('button', { name: '重试消息列表' }) });
  await expect(listAlert).toContainText('未找到该快照或证券，当前日期与输入已保留。');
  await expect(page.getByRole('button', { name: '重试消息列表' })).toBeVisible();

  await nav.getByRole('link', { name: '监管', exact: true }).click();
  await expect(page).toHaveURL(/view=regulation/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await expect(page.getByRole('heading', { name: '监管', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '数据完整性' })).toContainText('来源校验未知');
  await expect(page.getByRole('region', { name: '数值计算状态' })).toContainText('当前快照没有已发布的数值计算记录');
  const events = page.getByRole('region', { name: '已披露正式事件与限制' });
  await expect(events).toContainText('事件来源完整性未知，不能将缺失解释为零');
  await expect(events).not.toContainText('0 条正式记录');

  await nav.getByRole('link', { name: '梯队', exact: true }).click();
  await expect(page).toHaveURL(/view=leaders/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${OLD}`));
  await expect(page.getByRole('heading', { name: '梯队', exact: true })).toBeVisible();
});

test('per-page filter and sort survive navigation, drawer and browser back', async ({ page }) => {
  await mockMarket(page);
  await mockNavigationAnalysis(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10&q=TEST002&sort=amount&order=desc`);
  await expect(page.getByRole('button', { name: '合成测试证券2', exact: true })).toBeVisible();
  const original = new URL(page.url()).search;
  await page.getByRole('link', { name: '涨幅', exact: true }).click();
  await page.getByLabel('搜索证券', { exact: true }).fill('TEST003');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByRole('button', { name: '最大回撤', exact: true }).click();
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
  await expect(page.getByRole('button', { name: '合成测试证券2', exact: true })).toBeVisible();
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
