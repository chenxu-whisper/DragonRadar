import { expect, test, type Page, type Route } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import { ID, mockMarket } from '../fixtures/market';

const DATE = '2026-09-10';
const SECURITY = 'TEST001';
const SECTOR = 'SEC001';
const SOURCE = 'licensed-feed';
const BATCH = 'batch-1';
type RealtimeResourceName = 'quotes' | 'minutes' | 'anomalies' | 'flow';

type MockOptions = {
  failFirstQuote?: boolean;
  revokeFirstQuote?: boolean;
  flowGap?: boolean;
  flowMixedUnits?: boolean;
  minuteMultiSource?: boolean;
  nullResources?: RealtimeResourceName[];
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
  const isNull = (name: RealtimeResourceName) => options.nullResources?.includes(name);
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
        return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
      }
      if (isNull('quotes')) {
        return route.fulfill({ json: envelope(null, {
          trade_date: null,
          source: null,
          source_version: null,
          coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
          quality_flags: ['no_data'],
        }) });
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
      if (isNull('minutes')) {
        return route.fulfill({ json: envelope(null, {
          source: null,
          source_version: null,
          coverage: { expected: 3, valid: 0, excluded: 0, missing: 3 },
          quality_flags: ['no_data'],
        }) });
      }
      if (options.minuteMultiSource) {
        return route.fulfill({ json: envelope({
          status: 'incomplete',
          bars: [
            minute('2026-09-10T01:30:00Z', 10, 'alpha-feed', 'alpha-batch'),
            minute('2026-09-10T01:32:00Z', 12, 'alpha-feed', 'alpha-batch'),
            minute('2026-09-10T01:30:00Z', 20, 'beta-feed', 'beta-batch'),
            minute('2026-09-10T01:31:00Z', 21, 'beta-feed', 'beta-batch'),
          ],
        }, {
          source: null,
          source_version: null,
          coverage: { expected: 6, valid: 4, excluded: 0, missing: 2 },
          quality_flags: ['stream_gap'],
        }) });
      }
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
      if (isNull('anomalies')) {
        return route.fulfill({ json: envelope(null, {
          source: null,
          source_version: null,
          coverage: null,
          quality_flags: ['no_data'],
        }) });
      }
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
      if (isNull('flow')) {
        return route.fulfill({ json: envelope(null, {
          source: null,
          source_version: null,
          coverage: { expected: 1, valid: 0, excluded: 0, missing: 1 },
          quality_flags: ['no_data'],
        }) });
      }
      const points = options.flowMixedUnits
        ? [
          flowPoint('2026-09-10T01:30:00Z', 0, null, 'CNY'),
          flowPoint('2026-09-10T01:31:00Z', 10, null, 'USD'),
          flowPoint('2026-09-10T01:32:00Z', null, 'field_not_authorized', 'CNY'),
        ]
        : options.flowGap ? [
          flowPoint('2026-09-10T01:30:00Z', 1, null),
          flowPoint('2026-09-10T01:31:00Z', null, 'field_not_authorized'),
          flowPoint('2026-09-10T01:32:00Z', 2, null),
        ]
        : [flowPoint(
          '2026-09-10T01:30:00Z',
          null,
          'field_not_authorized',
        )];
      const unavailableReason = points.find(point => point.net_inflow === null)
        ?.unavailable_reason;
      return route.fulfill({ json: envelope({
        status: unavailableReason ? 'unavailable' : 'complete',
        points,
      }, {
        coverage: {
          expected: points.length,
          valid: points.length,
          excluded: 0,
          missing: 0,
        },
        quality_flags: unavailableReason ? [unavailableReason] : [],
        basis: {
          unit: options.flowMixedUnits ? null : 'CNY',
          currency: options.flowMixedUnits ? null : 'CNY',
          price_type: 'raw',
          benchmark: null,
        },
      }) });
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  }

  return requests;
}

function flowPoint(
  eventAt: string,
  netInflow: number | null,
  unavailableReason: string | null,
  unit = 'CNY',
) {
  return {
    batch_id: BATCH,
    source_id: SOURCE,
    source_version: 'v1',
    sector: SECTOR,
    event_at: eventAt,
    available_at: eventAt,
    net_inflow: netInflow,
    unit,
    unavailable_reason: unavailableReason,
  };
}

function minute(
  eventAt: string,
  close: number,
  source = SOURCE,
  batch = BATCH,
) {
  return {
    batch_id: batch,
    source_id: source,
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

test('browser JSON.parse exposes lexical source for Decimal decoding', async ({ page }) => {
  expect(await page.evaluate(() => {
    let source: string | undefined;
    type Context = { source?: string };
    type Reviver = (
      this: unknown,
      key: string,
      value: unknown,
      context: Context,
    ) => unknown;
    (JSON.parse as (text: string, reviver: Reviver) => unknown)(
      '{"value":1.2300}',
      (key, value, context) => {
        if (key === 'value') source = context?.source;
        return value;
      },
    );
    return source;
  })).toBe('1.2300');
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
  const flowSection = panel.getByRole('heading', {
    name: `题材分时资金流 · ${SECTOR}`,
  }).locator('..').locator('..');
  await expect(flowSection.getByText('不可用', { exact: true }).first()).toBeVisible();
  await expect(panel.getByText('资金流不可用：field_not_authorized')).toBeVisible();
  await expect(panel.getByRole('img', { name: /资金流/ })).toHaveCount(0);
  await expect(panel.getByRole('row', {
    name: /return-v1.*0\.025.*0\.02.*batch-1.*incomplete/,
  })).toBeVisible();
  expect(requests.every(item => item.method === 'GET')).toBe(true);
});

test('keeps an unavailable funding point as a disconnected chart gap', async ({ page }) => {
  await mockRealtime(page, { flowGap: true });
  await openSelected(page);

  const chart = page.getByRole('img', { name: /题材分时净流入/ });
  await expect(chart.locator('canvas')).toBeVisible();
  const series = await chart.evaluate(async element => {
    const url = performance.getEntriesByType('resource')
      .map(entry => entry.name)
      .find(name => name.includes('/echarts_core.js'));
    if (!url) throw new Error('ECharts module was not loaded');
    const echarts = await import(url);
    const option = echarts.getInstanceByDom(element).getOption();
    return {
      connectNulls: option.series[0].connectNulls,
      data: option.series[0].data,
      labels: option.xAxis[0].data,
    };
  });
  expect(series).toEqual({
    connectNulls: false,
    data: [1, null, 2],
    labels: ['09:30', '09:31', '09:32'],
  });
});

test('keeps same-minute sources as separate minute series with independent gaps', async ({ page }) => {
  await mockRealtime(page, { minuteMultiSource: true });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  const table = panel.getByRole('table', { name: '分钟记录' });
  await expect(table.getByRole('row', {
    name: /09:30.*10.*alpha-batch.*alpha-feed/,
  })).toBeVisible();
  await expect(table.getByRole('row', {
    name: /09:30.*20.*beta-batch.*beta-feed/,
  })).toBeVisible();

  const chart = panel.getByRole('img', { name: /分钟收盘价.*按来源拆分/ });
  const series = await chart.evaluate(async element => {
    const url = performance.getEntriesByType('resource')
      .map(entry => entry.name)
      .find(name => name.includes('/echarts_core.js'));
    if (!url) throw new Error('ECharts module was not loaded');
    const echarts = await import(url);
    const option = echarts.getInstanceByDom(element).getOption();
    return {
      labels: option.xAxis[0].data,
      series: option.series.map((item: {
        id: unknown;
        name: unknown;
        connectNulls: unknown;
        data: unknown;
        lineStyle: { color: unknown };
      }) => ({
        id: item.id,
        name: item.name,
        connectNulls: item.connectNulls,
        data: item.data,
        color: item.lineStyle.color,
      })),
    };
  });
  expect(series.labels).toEqual(['09:30', '09:31', '09:32']);
  expect(series.series).toMatchObject([
    {
      id: 'minute-source:alpha-feed',
      name: '分钟收盘 · alpha-feed',
      connectNulls: false,
      data: [10, null, 12],
    },
    {
      id: 'minute-source:beta-feed',
      name: '分钟收盘 · beta-feed',
      connectNulls: false,
      data: [20, 21, null],
    },
  ]);
  expect(new Set(series.series.map((item: { color: unknown }) => item.color)).size).toBe(2);
});

test('splits mixed-unit flow series while retaining zero, gaps, and audit units', async ({ page }) => {
  await mockRealtime(page, { flowMixedUnits: true });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  await expect(panel.getByText(/混合单位.*按单位拆分/)).toBeVisible();
  const table = panel.getByRole('table', { name: '题材分时资金流审计记录' });
  await expect(table.getByRole('row', { name: /09:30.*0.*CNY/ })).toBeVisible();
  await expect(table.getByRole('row', { name: /09:31.*10.*USD/ })).toBeVisible();
  await expect(table.getByRole('row', {
    name: /09:32.*不可用.*CNY.*field_not_authorized/,
  })).toBeVisible();

  const chart = panel.getByRole('img', { name: /题材分时净流入.*混合单位/ });
  const series = await chart.evaluate(async element => {
    const url = performance.getEntriesByType('resource')
      .map(entry => entry.name)
      .find(name => name.includes('/echarts_core.js'));
    if (!url) throw new Error('ECharts module was not loaded');
    const echarts = await import(url);
    return echarts.getInstanceByDom(element).getOption().series.map((item: {
      name: unknown;
      connectNulls: unknown;
      data: unknown;
    }) => ({
      name: item.name,
      connectNulls: item.connectNulls,
      data: item.data,
    }));
  });
  expect(series).toEqual([
    { name: '净流入 (CNY)', connectNulls: false, data: [0, null, null] },
    { name: '净流入 (USD)', connectNulls: false, data: [null, 10, null] },
  ]);
});

test('keeps per-resource unavailable evidence when another resource has data', async ({ page }) => {
  await mockRealtime(page, { nullResources: ['flow'] });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  await expect(panel.getByRole('table', { name: '实时报价' })).toBeVisible();
  const flowState = panel.getByLabel('分时资金流状态');
  await expect(flowState.getByText('不可用', { exact: true })).toBeVisible();
  await expect(flowState.getByText(/有效 0\/1.*缺失 1/)).toBeVisible();
  await expect(flowState.getByText('质量标记：no_data')).toBeVisible();
  await expect(flowState.getByText('无可用数据')).toBeVisible();
  await expect(panel.getByText('当前选择没有可审计的实时证据', {
    exact: false,
  })).toHaveCount(0);
});

test('shows all resource evidence and the global empty state when all data is null', async ({ page }) => {
  await mockRealtime(page, {
    nullResources: ['quotes', 'minutes', 'anomalies', 'flow'],
  });
  await openSelected(page);

  const panel = page.getByRole('region', { name: '实时证据账本' });
  for (const name of ['实时报价', '分钟记录', '异动证据', '分时资金流']) {
    const state = panel.getByLabel(`${name}状态`);
    await expect(state.getByText('不可用', { exact: true })).toBeVisible();
    await expect(state.getByText('质量标记：no_data')).toBeVisible();
    await expect(state.getByText('无可用数据')).toBeVisible();
  }
  await expect(panel.getByText(
    '当前选择没有可审计的实时证据；不会用日线或其他批次替代。',
  )).toBeVisible();
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
  await expect(page.getByRole('heading', { name: '市场总览' })).toBeVisible();
  await expect(page.getByText('合成测试来源', { exact: false }).first()).toBeVisible();
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
