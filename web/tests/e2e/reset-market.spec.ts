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
  });
}

async function mockResetMarket(page: Page) {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown) => route.fulfill({ json: body });
    if (url.pathname === '/api/snapshots/list') {
      const day = url.searchParams.get('date');
      const items = day === '2026-09-09'
        ? [summary(PRIOR, '2026-09-09')]
        : [summary(CURRENT, '2026-09-10')];
      return respond(envelope(items[0].snapshot_id, items[0].trade_date, { snapshots: items }));
    }
    const selected = url.searchParams.get('snapshot_id') === PRIOR
      ? [PRIOR, '2026-09-09'] : [CURRENT, '2026-09-10'];
    if (url.pathname === '/api/market/overview') {
      return respond(envelope(selected[0], selected[1], {
        indexes: [],
        breadth: {
          up: 3, down: 1, flat: 1, valid_total: 5,
          coverage, seal_rate: { numerator: 2, denominator: 3, value: 0.6667, reason: null },
        },
        sentiment: {
          limit_up_count: 2, limit_down_count: 0, max_boards: 2,
          promote_rate: { yesterday_ge2: 2, numerator: 1, denominator: 2, value: 0.5, excluded: 0, missing: 0, reason: null },
          yesterday_limit_up_median_return: 0.012,
        },
        hot_sectors: [{
          sector_id: 'SEC001', name: '合成题材甲', member_count: 3,
          valid_returns: 3, median_return: 0.024, change_ratio: 0.024,
          limit_up_count: 1, quality_flags: ['sample_insufficient'],
          net_inflow: null, net_inflow_reason: 'source_field_unavailable',
          coverage: { expected: 3, valid: 3, excluded: 0, missing: 0 },
        }],
        factors: {},
        historical_topic_analysis: { enabled: false, reason: 'historical_topic_membership_unavailable' },
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

test('reset overview uses versioned APIs and persists drill-down context in URL', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/api/')) requests.push(new URL(request.url()).pathname);
  });
  await mockResetMarket(page);
  await page.goto(`/?view=overview&snapshot=${CURRENT}&date=2026-09-10`);

  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByText('来源 synthetic-market / market-v1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '上涨 3' }).click();
  await expect(page).toHaveURL(/market_filter=up/);
  await expect(page.getByText('当前筛选上下文：上涨')).toBeVisible();

  await page.getByRole('button', { name: /合成题材甲/ }).click();
  await expect(page).toHaveURL(/view=leaders/);
  await expect(page).toHaveURL(/sector=SEC001/);
  await expect(page.getByText('题材上下文：SEC001')).toBeVisible();
  expect(requests.some(path => path.startsWith('/api/v1/'))).toBe(false);
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
