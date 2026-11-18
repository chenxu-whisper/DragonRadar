import { expect, test, type Page } from '@playwright/test';
import { ID, mockMarket, resetSummary } from '../fixtures/market';

async function emptySnapshots(page: Page) {
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/snapshots/list') {
      return route.fulfill({ json: { ...resetSummary(), data: { snapshots: [] } } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
}

for (const [view, title, modules] of [
  ['overview', '市场总览', ['指数行情', '涨停生态', '涨跌分布', '情绪原始观测', '题材热度 TOP', '板块资金流', '涨停情绪热力图', 'AI 每日速览']],
  ['news', '市场消息', ['消息列表', '消息详情与 AI 摘要', '新闻词云', 'AI 题材建议']],
  ['leaders', '龙头梯队', ['连板矩阵', '晋级与昨日反馈', '六类角色候选', '题材轮动']],
  ['analysis', '涨幅分析', ['区间排行', '多标的比较']],
  ['regulation', '监管观察', ['数据完整性', '数值计算状态', '正式事件与限制']],
] as const) {
  test(`empty ${view} retains the PRD page structure without snapshot analysis requests`, async ({ page }) => {
    await emptySnapshots(page);
    const analysisRequests: string[] = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (/^\/api\/(market|news|role|limit-up|regulation|rank|compare|realtime)/.test(url.pathname)) {
        analysisRequests.push(url.pathname);
      }
    });
    await page.goto(`/?view=${view}`);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    for (const name of modules) {
      await expect(page.getByRole('region', { name, exact: true })).toContainText('未选择可读快照');
    }
    await expect(page.getByRole('navigation', { name: '主导航' }).getByRole('link')).toHaveText([
      '市场总览', '市场消息', '龙头梯队', '涨幅分析', '监管观察', '我的复盘',
    ]);
    await expect(page.getByLabel('工作台状态')).toContainText('股票池');
    await expect(page.getByLabel('工作台状态')).toContainText('数据质量');
    expect(analysisRequests).toEqual([]);
  });
}

test('module entries preserve snapshot context and open query and local simulation', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ status: 404, json: {} }));
  await mockMarket(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10`);
  const capabilities = page.getByRole('navigation', { name: '研究工具' });
  await capabilities.getByRole('link', { name: '自然语言查询', exact: true }).click();
  await expect(page).toHaveURL(/view=analysis/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${ID}`));
  await expect(page).toHaveURL(/section=query/);
  await expect(page.getByRole('heading', { name: '白名单查询', exact: true })).toBeVisible();
  await capabilities.getByRole('link', { name: '模拟组合', exact: true }).click();
  await expect(page).toHaveURL(/view=review/);
  await expect(page).toHaveURL(/section=simulation/);
  await expect(page.getByRole('button', { name: '模拟账本', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('标题', { exact: true })).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/section=query/);
});

test('published overview restores the ecology and three-column summary before detailed research', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  await mockMarket(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=2026-09-10`);
  await expect(page.getByRole('region', { name: '涨停生态', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'AI 每日速览', exact: true })).toContainText('快照事实模板');
  for (const id of ['indexes', 'ecology', 'breadth', 'sentiment', 'topics', 'sector-flow', 'heatmap']) {
    await expect(page.locator(`#workbench-${id}`)).toBeVisible();
  }
  await expect(page.getByRole('navigation', { name: '页面模块' }).getByRole('link', { name: '实时看盘', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('overview-desktop.png'), fullPage: true });
});

test('empty workbench fills the viewport and fits a narrow screen', async ({ page }, testInfo) => {
  await emptySnapshots(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '市场总览', exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
    footer: document.querySelector('footer')!.getBoundingClientRect().bottom,
  }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  expect(dimensions.footer).toBeGreaterThanOrEqual(812);
  await page.screenshot({ path: testInfo.outputPath('empty-mobile.png'), fullPage: true });
});

test('simulation account draft is protected across module and page navigation', async ({ page }) => {
  await mockMarket(page);
  await page.goto(`/?view=review&section=simulation&snapshot=${ID}&date=2026-09-10`);
  await page.getByLabel('初始资金', { exact: true }).fill('250000');
  const dialogs: string[] = [];
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.dismiss(); });
  await page.getByRole('button', { name: '我的笔记', exact: true }).click();
  await expect(page).toHaveURL(/section=simulation/);
  await expect(page.getByLabel('初始资金', { exact: true })).toHaveValue('250000');
  await page.getByRole('navigation', { name: '主导航' }).getByRole('link', { name: '市场总览', exact: true }).click();
  await expect(page).toHaveURL(/view=review/);
  expect(dialogs).toHaveLength(2);
});

for (const [view, ids] of [
  ['overview', ['indexes', 'ecology', 'breadth', 'sentiment', 'topics', 'sector-flow', 'heatmap', 'daily-review', 'factors']],
  ['news', ['news-list', 'news-detail', 'wordcloud', 'topic-suggest']],
  ['leaders', ['ladder', 'continuity', 'candidates', 'rotation']],
  ['analysis', ['ranking', 'comparison', 'query', 'backtest']],
  ['regulation', ['completeness', 'calculation', 'events']],
] as const) {
  test(`unavailable ${view} domain retains module targets for a published snapshot`, async ({ page }) => {
    await mockMarket(page);
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      if (path.startsWith('/api/snapshots/')) return route.fallback();
      return route.fulfill({ status: 503, json: { error: { code: 'DATA_UNAVAILABLE', message: '物化数据不可用' } } });
    });
    await page.goto(`/?view=${view}&snapshot=${ID}&date=2026-09-10`);
    for (const id of ids) await expect(page.locator(`#workbench-${id}`)).toHaveCount(1);
    const moduleLink = page.getByRole('navigation', { name: '页面模块' }).getByRole('link').first();
    await moduleLink.click();
    await expect(page.locator(`#workbench-${ids[0]}`)).toBeFocused();
  });
}

test('module focus follows the final content after a delayed domain replaces its placeholder', async ({ page }) => {
  await mockMarket(page);
  let release!: () => void;
  const responseGate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/market/overview?*', async route => {
    await responseGate;
    await route.fallback();
  });
  await page.goto(`/?view=overview&section=breadth&snapshot=${ID}&date=2026-09-10`);
  await expect(page.locator('#workbench-breadth')).toBeFocused();
  await expect(page.locator('#workbench-breadth')).toHaveAttribute('aria-busy', 'true');
  release();
  await expect(page.locator('#workbench-breadth')).toContainText('上涨 32');
  await expect(page.locator('#workbench-breadth')).toBeFocused();
});
