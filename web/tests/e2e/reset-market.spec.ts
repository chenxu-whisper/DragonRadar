import { expect, test, type Page, type Route } from '@playwright/test';

const CURRENT = '00000000-0000-4000-8000-000000000091';
const PRIOR = '00000000-0000-4000-8000-000000000090';

const coverage = { expected: 5, valid: 5, excluded: 0, missing: 0 };
const basis = { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null };

function envelope(snapshotId: string, tradeDate: string, data: unknown) {
  return {
    trade_date: tradeDate,
    as_of: `${tradeDate}T10:00:00Z`,
    snapshot_id: snapshotId,
    source: 'synthetic-market',
    source_version: 'market-v1',
    coverage,
    quality_flags: [],
    rule_version: 'market-v1',
    basis,
    data,
  };
}

function summary(snapshotId: string, tradeDate: string) {
  return envelope(snapshotId, tradeDate, {
    revision: 1,
    supersedes: null,
    published_at: `${tradeDate}T10:05:00Z`,
    security_count: 5,
    calendar_sessions: [tradeDate],
    earliest_date: tradeDate,
  });
}

async function mockResetMarket(page: Page, listIncludesSelected = true) {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === '/api/snapshots/list') {
      const day = url.searchParams.get('date');
      const items = day === '2026-09-09'
        ? [summary(PRIOR, '2026-09-09')]
        : listIncludesSelected ? [summary(CURRENT, '2026-09-10')] : [];
      if (!items.length) return respond(envelope(CURRENT, '2026-09-10', { snapshots: [] }));
      return respond(envelope(items[0].snapshot_id, items[0].trade_date, { snapshots: items }));
    }
    if (url.pathname === `/api/snapshots/${CURRENT}`) {
      return respond(summary(CURRENT, '2026-09-10'));
    }
    if (url.pathname === `/api/snapshots/${PRIOR}`) {
      return respond(summary(PRIOR, '2026-09-09'));
    }
    const selected = url.searchParams.get('snapshot_id') === PRIOR
      ? [PRIOR, '2026-09-09'] : [CURRENT, '2026-09-10'];
    if (url.pathname === '/api/market/overview') {
      return respond(envelope(selected[0], selected[1], {
        indexes: [],
        breadth: {
          up: 3, down: 1, flat: 1, valid_total: 5,
          coverage,
          members: {
            up: [
              { security_id: 'E', code: '000005', name: '合成戊' },
              { security_id: 'A', code: '000001', name: '合成甲' },
              { security_id: 'C', code: '000003', name: '合成丙' },
              { security_id: 'A', code: '000001', name: '合成甲' },
            ],
            down: [{ security_id: 'B', code: '000002', name: '合成乙' }],
            flat: [{ security_id: 'D', code: '000004', name: '合成丁' }],
          },
          seal_rate: {
            numerator: 2, denominator: 3, value: null,
            reason: 'incomplete_touched_limit_coverage',
            coverage: { expected: 5, valid: 4, excluded: 0, missing: 1 },
          },
        },
        sentiment: {
          limit_up_count: 2, limit_down_count: 0, max_boards: 2,
          promote_rate: { yesterday_ge2: 3, numerator: 1, denominator: 2, value: 0.5, excluded: 0, missing: 1, reason: null },
          yesterday_limit_up_median_return: null,
          yesterday_limit_up_return_coverage: { expected: 2, valid: 1, excluded: 0, missing: 1 },
          yesterday_limit_up_return_reason: 'partial_yesterday_limit_up_return_coverage',
        },
        hot_sectors: [{
          sector_id: 'SEC001', name: '合成题材甲', member_count: 3,
          valid_returns: 3, median_return: 0.024, change_ratio: 0.024,
          limit_up_count: 1, quality_flags: ['sample_insufficient'],
          up_ratio: { numerator: 2, denominator: 3, value: 0.6667, reason: null },
          limit_up_ratio: { numerator: 1, denominator: 3, value: 0.3333, reason: null },
          net_inflow: null, net_inflow_reason: 'source_field_unavailable',
          coverage: { expected: 3, valid: 3, excluded: 0, missing: 0 },
        }],
        factors: {
          'FAC-M01': {
            advancing: { numerator: 3, denominator: 5, value: 0.6, reason: null, coverage },
            above_ma20: {
              numerator: 2, denominator: 4, value: 0.5, reason: null,
              coverage: { expected: 5, valid: 4, excluded: 0, missing: 1 },
            },
            method: 'adjusted_close_ma20_including_current',
          },
          'FAC-M02': {
            value: null, today_total: null, baseline_mean: null,
            baseline_days: 20, baseline_excludes_current: true,
            coverage: { expected: 21, valid: 19, excluded: 0, missing: 2 },
            reason: 'requires_complete_current_and_prior_20_sessions',
            method: 'same_pool_amount_vs_prior_20_session_mean',
          },
          'FAC-M03': {
            indexes: [{
              security_id: 'IDX', code: '000300', name: '合成指数', close: 1234,
              close_over_ma20: 0.031, close_over_ma60: null, volatility_20: 0.018,
              volatility_method: 'sample_stddev_non_annualized',
              coverage: {
                ma20: { expected: 20, valid: 20, excluded: 0, missing: 0 },
                ma60: { expected: 60, valid: 59, excluded: 0, missing: 1 },
                return20: { expected: 20, valid: 20, excluded: 0, missing: 0 },
              },
              close_over_ma20_reason: null,
              close_over_ma60_reason: 'requires_60_consecutive_index_closes',
              volatility_20_reason: null,
              reason: null,
            }],
            method: 'close_over_ma20_ma60_and_20_return_sample_stddev_non_annualized',
          },
        },
        historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
      }));
    }
    if (url.pathname === '/api/market/heatmap') {
      return respond(envelope(selected[0], selected[1], {
        columns: [1, 2],
        cells: [
          { trade_date: '2026-09-09', boards: 2, count: 1 },
          { trade_date: '2026-09-10', boards: 1, count: 2 },
        ],
      }));
    }
    if (url.pathname === '/api/market/sector-flow') return respond(envelope(selected[0], selected[1], { sectors: [] }));
    if (url.pathname === '/api/market/sentiment') return respond(envelope(selected[0], selected[1], { series: [] }));
    return route.fulfill({ status: 404, json: {} });
  });
}

test('explicit snapshot deep link does not depend on the first list page', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => requests.push(new URL(request.url()).pathname));
  await mockResetMarket(page, false);

  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  expect(requests).toContain(`/api/snapshots/${CURRENT}`);
});

test('reset overview uses versioned APIs and persists drill-down context in URL', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/')) requests.push(new URL(request.url()).pathname);
  });
  await mockResetMarket(page);
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByText('来源 synthetic-market / market-v1', { exact: true })).toBeVisible();
  const members = page.getByRole('region', { name: '证券筛选结果' });
  await expect(members.getByText('全部证券 · 共 5 家', { exact: true })).toBeVisible();
  await expect(members.getByRole('button')).toHaveText(['合成甲', '合成乙', '合成丙', '合成丁', '合成戊']);
  await members.getByRole('button', { name: '合成甲', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByPlaceholder('输入证券代码或名称').fill('000002');
  await page.getByRole('button', { name: '搜索', exact: true }).click();
  await expect(page).toHaveURL(/q=000002/);
  await expect(members.getByText('全部证券 · 搜索“000002” · 共 1 家', { exact: true })).toBeVisible();
  await expect(members.getByRole('button')).toHaveText(['合成乙']);
  await members.getByRole('button', { name: '合成乙', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await page.getByRole('button', { name: '上涨 3' }).click();
  await expect(page).toHaveURL(/market_filter=up/);
  const upMembers = page.getByRole('region', { name: '证券筛选结果' });
  await expect(upMembers.getByText('共 3 家')).toBeVisible();
  await expect(upMembers.getByRole('button')).toHaveText(['合成甲', '合成丙', '合成戊']);
  await expect(upMembers.getByText('000003')).toBeVisible();
  await upMembers.getByRole('button', { name: '合成丙', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭详情' }).click();

  await page.getByRole('button', { name: '下跌 1' }).click();
  await expect(page).toHaveURL(/market_filter=down/);
  const downMembers = page.getByRole('region', { name: '证券筛选结果' });
  await expect(downMembers.getByText('共 1 家')).toBeVisible();
  await expect(downMembers.getByRole('button', { name: '合成乙', exact: true })).toBeVisible();
  await expect(downMembers.getByRole('button', { name: '合成甲', exact: true })).toHaveCount(0);

  await expect(page.getByText(/FAC-M01/)).toBeVisible();
  await expect(page.getByText('requires_complete_current_and_prior_20_sessions')).toBeVisible();
  await expect(page.getByText('requires_60_consecutive_index_closes')).toBeVisible();
  await expect(page.getByText('incomplete_touched_limit_coverage')).toBeVisible();
  await expect(page.getByText('partial_yesterday_limit_up_return_coverage')).toBeVisible();
  await expect(page.getByText('source_field_unavailable')).toBeVisible();
  await expect(page.getByText('涨停占比 1/3 · 33.33%')).toBeVisible();

  await page.getByRole('button', { name: /合成题材甲/ }).click();
  await expect(page).toHaveURL(/view=leaders/);
  await expect(page).toHaveURL(/sector=SEC001/);
  await expect(page.getByLabel('题材筛选')).toHaveValue('SEC001');
  expect(requests.some(path => path.startsWith('/api/v1/') && (path.endsWith('/overview') || path.endsWith('/securities')))).toBe(false);
});

test('heatmap date clears stale snapshot and historical topic control is disabled', async ({ page }) => {
  await mockResetMarket(page);
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('button', { name: '历史题材分析' })).toBeDisabled();
  await expect(page.getByText('缺少历史题材成员')).toBeVisible();
  await page.getByRole('button', { name: '2026-09-09 2板 1家' }).click();
  await expect(page).toHaveURL(/date=2026-09-09/);
  await expect(page).toHaveURL(new RegExp(`snapshot=${PRIOR}`));
  await expect(page).not.toHaveURL(new RegExp(`snapshot=${CURRENT}`));
});
