import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { ID, mockMarket } from '../fixtures/market';

const DATE = '2026-09-10';
const SECURITY = 'TEST001';
const SECTOR = 'SEC001';
const SOURCE = 'licensed-feed';
const BATCH = 'batch-1';

type MockOptions = {
  failFirstQuote?: boolean;
  revokeFirstQuote?: boolean;
};

function envelope(
  data: unknown,
  changes: Record<string, unknown> = {},
) {
  return {
    trade_date: DATE,
    as_of: '2026-09-10T02:00:00Z',
    snapshot_id: null,
    source: SOURCE,
    source_version: 'v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: null,
    basis: {
      unit: null,
      currency: null,
      price_type: 'raw',
      benchmark: null,
    },
    data,
    ...changes,
  };
}

async function mockRealtime(page: Page, options: MockOptions = {}) {
  let quoteAttempts = 0;
  const requests: { method: string; path: string }[] = [];
  await page.route('**/api/realtime/**', route => respond(route));
  await page.route('**/api/market/sector-flow/intraday?**', route => respond(route));

  async function respond(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({ method: request.method(), path: url.pathname });
    if (request.method() !== 'GET') {
      return route.fulfill({ status: 405, json: { code: 'METHOD_NOT_ALLOWED' } });
    }
    if (url.pathname === '/api/realtime/quotes') {
      quoteAttempts += 1;
      if (options.failFirstQuote && quoteAttempts === 1) {
        return route.fulfill({ status: 503, json: { code: 'STORAGE_UNAVAILABLE' } });
      }
      if (options.revokeFirstQuote && quoteAttempts === 1) {
        return route.fulfill({ status: 403, json: { code: 'source_forbidden' } });
      }
      return route.fulfill({ json: envelope({
        quote_batch_id: BATCH,
        source_event_at: '2026-09-10T01:30:00Z',
        received_at: '2026-09-10T01:30:05Z',
        delay_ms: 5000,
        status: 'delayed',
        quotes: [{
          security_id: SECURITY,
          last: 10.25,
          bid: null,
          ask: null,
          bid_size: null,
          ask_size: null,
          volume: 0,
          amount: 0,
          order_book_status: 'unavailable',
          order_book_unavailable_reason: 'field_not_authorized',
        }],
      }, {
        trade_date: null,
        quality_flags: ['delayed'],
      }) });
    }
    if (url.pathname === '/api/realtime/minutes') {
      return route.fulfill({ json: envelope({
        status: 'incomplete',
        bars: [
          minute('2026-09-10T01:30:00Z', 10),
          minute('2026-09-10T01:32:00Z', 10.25),
        ],
      }, {
        coverage: { expected: 3, valid: 2, excluded: 0, missing: 1 },
        quality_flags: ['stream_gap'],
      }) });
    }
    if (url.pathname === '/api/realtime/anomalies') {
      return route.fulfill({ json: envelope({
        status: 'complete',
        events: [{
          event_id: 'event-1',
          batch_id: BATCH,
          security_id: SECURITY,
          rule_version: 'return-v1',
          event_at: '2026-09-10T01:32:00Z',
          available_at: '2026-09-10T01:32:02Z',
          metric: 'window_return',
          window: 2,
          value: 0.025,
          threshold: 0.02,
          quality: 'incomplete',
        }],
        next_cursor: null,
      }, {
        source_version: null,
      }) });
    }
    if (url.pathname === '/api/market/sector-flow/intraday') {
      return route.fulfill({ json: envelope({
        status: 'complete',
        points: [{
          batch_id: BATCH,
          source_id: SOURCE,
          source_version: 'v1',
          sector: SECTOR,
          event_at: '2026-09-10T01:30:00Z',
          available_at: '2026-09-10T01:30:02Z',
          net_inflow: null,
          unit: 'CNY',
          unavailable_reason: 'field_not_authorized',
        }],
      }, {
        basis: {
          unit: 'CNY',
          currency: 'CNY',
          price_type: 'raw',
          benchmark: null,
        },
      }) });
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  }

  return requests;
}

function minute(eventAt: string, close: number) {
  return {
    batch_id: BATCH,
    source_id: SOURCE,
    source_version: 'v1',
    security_id: SECURITY,
    event_at: eventAt,
    received_at: eventAt,
    available_at: eventAt,
    open: close,
    high: close,
    low: close,
    close,
    volume: 0,
    amount: 0,
    revision: 1,
    final: true,
  };
}

async function openSelected(page: Page) {
  await page.goto(
    `/?view=overview&snapshot=${ID}&date=${DATE}&security=${SECURITY}&sector=${SECTOR}`,
  );
  await expect(page.getByRole('region', { name: '实时证据账本' })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await mockMarket(page);
});

test('shows delayed source time, stream gap, unavailable flow, and auditable anomaly', async ({ page }) => {
  const requests = await mockRealtime(page);
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  await expect(panel.getByText('延迟 5,000 ms')).toBeVisible();
  await expect(panel.getByText('来源事件时间').locator('..').getByRole('time'))
    .toHaveAttribute('datetime', '2026-09-10T01:30:00Z');
  await expect(panel.getByText('缺失 1 分钟')).toBeVisible();
  const minuteTable = panel.getByRole('table', { name: '分钟记录' });
  await expect(minuteTable.getByRole('row', { name: /09:30/ })).toBeVisible();
  await expect(minuteTable.getByRole('row', { name: /09:32/ })).toBeVisible();
  await expect(minuteTable.getByRole('row', { name: /09:31/ })).toHaveCount(0);
  await expect(panel.getByText('资金流不可用：field_not_authorized')).toBeVisible();
  await expect(panel.getByRole('img', { name: /资金流/ })).toHaveCount(0);
  await expect(panel.getByRole('row', {
    name: /return-v1.*0\.025.*0\.02.*batch-1.*incomplete/,
  })).toBeVisible();
  expect(requests.every(item => item.method === 'GET')).toBe(true);
});

test('retry is keyboard reachable and restores the quote ledger without layout shift', async ({ page }) => {
  await mockRealtime(page, { failFirstQuote: true });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  const retry = panel.getByRole('button', { name: '重试实时报价' });
  await expect(retry).toBeVisible();
  await retry.focus();
  await expect(retry).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByText('延迟 5,000 ms')).toBeVisible();
  await expect(retry).toHaveCount(0);
});

test('shows revoked state and keyboard-reachable recovery without retaining evidence', async ({ page }) => {
  await mockRealtime(page, { revokeFirstQuote: true });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  await expect(panel.getByText('实时来源授权已撤销')).toBeVisible();
  await expect(panel.getByText(BATCH, { exact: true })).toHaveCount(0);
  const retry = panel.getByRole('button', { name: '重新检查实时来源' });
  await retry.focus();
  await expect(retry).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByText('延迟 5,000 ms')).toBeVisible();
  await expect(panel.getByText('实时来源授权已撤销')).toHaveCount(0);
});

for (const target of [
  { name: '375px', width: 375, height: 900 },
  { name: '1440px', width: 1440, height: 1000 },
  { name: '200% equivalent', width: 720, height: 900 },
]) {
  test(`has no overlap or page overflow at ${target.name}`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: target.width, height: target.height });
    await mockRealtime(page);
    await openSelected(page);

    const metrics = await page.evaluate(() => {
      const panel = document.querySelector('[aria-label="实时证据账本"]');
      const tables = [...document.querySelectorAll('[aria-label="实时证据账本"] table')];
      const rects = tables.map(table => table.getBoundingClientRect());
      return {
        bodyOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        panelOverflow: panel ? panel.scrollWidth > panel.clientWidth : true,
        tableOverlap: rects.some((rect, index) =>
          rects.slice(index + 1).some(other =>
            rect.top < other.bottom && rect.bottom > other.top
            && rect.left < other.right && rect.right > other.left)),
      };
    });
    expect(metrics).toEqual({
      bodyOverflow: false,
      panelOverflow: false,
      tableOverlap: false,
    });
    expect(await page.evaluate(
      () => matchMedia('(prefers-reduced-motion: reduce)').matches,
    )).toBe(true);
    expect(await new AxeBuilder({ page }).include('[aria-label="实时证据账本"]').analyze())
      .toMatchObject({ violations: [] });
    expect(consoleErrors).toEqual([]);
  });
}

test('does not request realtime endpoints without a selected security or sector', async ({ page }) => {
  const requests = await mockRealtime(page);
  await page.goto(`/?view=overview&snapshot=${ID}&date=${DATE}`);
  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByRole('region', { name: '实时证据账本' })).toHaveCount(0);
  expect(requests).toEqual([]);
});
