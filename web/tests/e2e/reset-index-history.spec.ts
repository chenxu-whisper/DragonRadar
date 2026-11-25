import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { coverage, DAY, envelope, history, PUB, SESSIONS } from '../fixtures/index-history';

async function mock(page: Page, historyRoute?: (route: Route) => Promise<void>) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const respond = (data: unknown) => route.fulfill({ json: envelope(data) });
    const summary = { revision: 1, supersedes: null, published_at: `${DAY}T10:05:00Z`,
      security_count: 1, calendar_sessions: SESSIONS, earliest_date: SESSIONS[0] };
    if (path === '/api/snapshots/list') return respond({ snapshots: [envelope(summary)] });
    if (path === `/api/snapshots/${PUB}`) return respond(summary);
    if (path === '/api/index/IDX/history') return historyRoute ? historyRoute(route) : route.fulfill({ json: history() });
    const ratio = { numerator: 1, denominator: 1, value: 1, reason: null, coverage };
    if (path === '/api/market/overview') return respond({
      indexes: [{ security_id: 'IDX', name: '合成指数', code: '000300', last: 1234,
        change_ratio: 0.01, amount: null, reason: null }],
      topic_basis: { source: null, source_version: null, as_of: null, cutoff: `${DAY}T10:00:00Z` },
      breadth: { up: 1, down: 0, flat: 0, valid_total: 1, coverage,
        members: { up: [], down: [], flat: [] }, seal_rate: ratio },
      sentiment: { limit_up_count: 0, limit_down_count: 0, max_boards: 0,
        promote_rate: { ...ratio, yesterday_ge2: 0, excluded: 0, missing: 0 },
        yesterday_limit_up_median_return: null, yesterday_limit_up_return_coverage: coverage,
        yesterday_limit_up_return_reason: null },
      hot_sectors: [], historical_topic_analysis: { enabled: false, reason: 'unavailable' },
      factors: { 'FAC-M01': { advancing: ratio, above_ma20: ratio, method: 'synthetic' },
        'FAC-M02': { value: null, today_total: null, baseline_mean: null, baseline_days: 20,
          baseline_excludes_current: true, coverage, reason: 'unavailable', method: 'synthetic' },
        'FAC-M03': { indexes: [], method: 'synthetic' } },
    });
    if (path === '/api/market/heatmap') return respond({ columns: [], cells: [] });
    if (path === '/api/market/sector-flow') return respond({ sectors: [] });
    if (path === '/api/market/sentiment') return respond({ series: [] });
    if (path === '/api/market/sentiment/research') return respond(null);
    return route.fulfill({ status: 404, json: {} });
  });
}
async function open(page: Page) {
  await page.goto(`/?view=overview&snapshot=${PUB}&date=${DAY}`);
  const opener = page.getByRole('button', { name: /查看指数详情.*合成指数/ });
  await opener.click();
  return { opener, drawer: page.getByRole('dialog', { name: '指数详情 · 只读' }) };
}
test('calendar windows, exact values, gap segments, mobile focus and no stock fallback', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const stock: string[] = [], reads: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/stock/')) stock.push(request.url()); });
  await mock(page, async route => { reads.push(route.request().url()); await route.fulfill({ json: history() }); });
  const { opener, drawer } = await open(page);
  const window = drawer.getByRole('combobox', { name: '指数历史窗口' });
  await expect(window).toHaveValue('60');
  await expect(drawer.getByRole('img', { name: '指数历史曲线，缺口不连线' })).toBeVisible();
  await expect(drawer.locator('[data-history-segment]')).toHaveCount(2);
  await expect(drawer).toContainText('1234.123456789012345678901');
  await expect(drawer).toContainText('observation_missing');
  await expect(drawer).toContainText('retrospective');
  for (const size of ['20', '120', '250', '60']) {
    const prior = reads.length;
    await window.selectOption(size);
    await expect.poll(() => reads.length).toBeGreaterThan(prior);
    await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toBeVisible();
    const query = new URL(reads.at(-1)!).searchParams;
    expect(query.get('start')).toBe(SESSIONS[0]); expect(query.get('end')).toBe(DAY);
  }
  expect((await new AxeBuilder({ page }).include('dialog[open]').analyze()).violations).toEqual([]);
  expect(await drawer.evaluate(el => el.getBoundingClientRect().width)).toBeLessThanOrEqual(375);
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await drawer.getByRole('button', { name: '关闭详情' }).focus();
  await page.keyboard.press('Shift+Tab');
  await expect(drawer.getByRole('region', { name: '指数历史精确数值横向滚动' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(drawer.getByRole('button', { name: '关闭详情' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused(); expect(stock).toEqual([]);
});
test('late superseded window cannot replace the reopened drawer', async ({ page }) => {
  let count = 0;
  let pending: Route | undefined;
  await mock(page, async route => {
    if (++count === 2) pending = route;
    else await route.fulfill({ json: history() });
  });
  const { opener, drawer } = await open(page);
  await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toBeVisible();
  await drawer.getByRole('combobox', { name: '指数历史窗口' }).selectOption('20');
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.keyboard.press('Escape');
  await opener.click();
  await expect(drawer.getByRole('combobox', { name: '指数历史窗口' })).toHaveValue('60');
  await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toBeVisible();
  const stale = history(); stale.data.points[0].close = '9999';
  await pending!.fulfill({ json: stale }).catch(() => {});
  await expect(drawer).not.toContainText('9999');
  await expect(page.locator('dialog[open]')).toHaveCount(1);
});
test('revoked source clears an already rendered history and quote', async ({ page }) => {
  await mock(page);
  const { drawer } = await open(page);
  await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toBeVisible();
  await page.evaluate(() => window.dispatchEvent(new Event('private-source-revoked')));
  await expect(page.getByText(/来源访问已被拒绝/).first()).toBeVisible();
  await expect(page.getByRole('table', { name: '指数历史精确数值' })).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: '指数详情 · 只读' })).toHaveCount(0);
  await expect(page.getByText('1234.123456789012345678901', { exact: true })).toHaveCount(0);
});
test('clears the old graph during a pending window, discards a late response after close', async ({ page }) => {
  let count = 0;
  let pending: Route | undefined;
  await mock(page, async route => { if (++count === 1) await route.fulfill({ json: history() }); else pending = route; });
  const { opener, drawer } = await open(page);
  await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toBeVisible();
  await drawer.getByRole('combobox', { name: '指数历史窗口' }).selectOption('20');
  await expect.poll(() => Boolean(pending)).toBe(true);
  await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toHaveCount(0);
  await page.keyboard.press('Escape'); await expect(opener).toBeFocused();
  await pending!.fulfill({ json: history() }).catch(() => {});
  await expect(page.locator('dialog[open]')).toHaveCount(0);
});
for (const failure of ['403', 'malformed', 'legacy', 'wrong-PUB'] as const) {
  test(`fails closed for ${failure}`, async ({ page }) => {
    await mock(page, async route => {
      const value = history();
      if (failure === '403') { await route.fulfill({ status: 403, json: { code: 'source_forbidden' } }); return; }
      if (failure === 'malformed') (value.data.points[0] as any).close = 9999;
      if (failure === 'wrong-PUB') value.snapshot_id = PUB.replace('091', '090');
      await route.fulfill({ json: failure === 'legacy'
        ? { ...value, data: null, coverage: null, reason: 'materialization_unavailable', quality_flags: ['materialization_unavailable'] } : value });
    });
    const { drawer } = await open(page);
    await expect(drawer.getByRole('table', { name: '指数历史精确数值' })).toHaveCount(0);
    await expect(page.getByText(failure === '403' ? /来源访问已被拒绝/ : failure === 'legacy'
      ? /当前发布未归档指数历史行情/ : failure === 'wrong-PUB' ? /响应.*不匹配/ : /历史响应格式无效/).first()).toBeVisible();
  });
}
