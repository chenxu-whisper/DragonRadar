import { expect, test, type Page, type Route } from '@playwright/test';
import { CLOCK_NAMES } from '../../src/features/lineage/types';

const path = '/tests/fixtures/parent-source-clocks.html';
const at = '2026-09-10T01:30:00Z';
function metadata(url: URL) {
  const lhb = url.pathname.startsWith('/api/lhb/');
  const batch = url.searchParams.get('batch_id');
  return {
    schema: 'dragon-radar.source-clocks/v1',
    identity: lhb ? {
      source_id: 'vendor', source_version: 'v3', disclosure_id: 'disclosure-1', revision: 3,
    } : {
      source_id: url.searchParams.get('source_id'), source_version: batch === 'batch-2' ? 'v2' : 'v1',
      kind: url.searchParams.get('kind'), batch_id: batch,
    },
    as_of: url.searchParams.get('as_of'),
    clocks: Object.fromEntries(CLOCK_NAMES.map(name => [name,
      name === 'snapshot_published_at' || name === 'page_updated_at'
        ? { state: 'not_applicable', at: null, first_at: null, last_at: null,
          reason: name === 'page_updated_at' ? 'browser_clock' : 'domain_clock_not_applicable' }
        : { state: 'known', at, first_at: at, last_at: at, reason: null },
    ])),
  };
}
async function intercept(page: Page) {
  const reads: URL[] = [];
  let behavior: (route: Route, url: URL) => Promise<void> = (route, url) =>
    route.fulfill({ json: metadata(url) });
  await page.route(/\/api\/(?:realtime|lhb)\/lineage\?/, async route => {
    const url = new URL(route.request().url());
    reads.push(url);
    await behavior(route, url);
  });
  return { reads, setBehavior(next: typeof behavior) { behavior = next; } };
}

async function realtimeData(page: Page) {
  const date = '2026-09-10';
  const available = `${date}T01:30:05Z`;
  const base = {
    trade_date: date, as_of: `${date}T02:00:00Z`, snapshot_id: null,
    source: 'vendor', source_version: 'v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [], rule_version: null,
    basis: { unit: null, currency: null, price_type: 'raw', benchmark: null },
  };
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/lineage')) return route.fallback();
    const batch = { source_id: 'vendor', source_version: 'v1', event_at: at };
    if (url.pathname === '/api/realtime/quotes') return route.fulfill({ json: {
      ...base, trade_date: null, data: {
        quote_batch_id: 'quote-1', source_event_at: at, received_at: available, delay_ms: 5000,
        status: 'complete', quotes: [{
          security_id: 'TEST001', last: '10', bid: null, ask: null, bid_size: null, ask_size: null,
          volume: '1', amount: '10', order_book_status: 'disabled', order_book_unavailable_reason: 'disabled',
        }],
      },
    } });
    if (url.pathname === '/api/realtime/minutes') return route.fulfill({ json: {
      ...base, data: { status: 'complete', bars: [{
        ...batch, batch_id: 'minute-1', security_id: 'TEST001', received_at: available,
        available_at: available, open: '10', high: '10', low: '10', close: '10',
        volume: '1', amount: '10', revision: 1, final: true,
      }] },
    } });
    if (url.pathname === '/api/market/sector-flow/intraday') return route.fulfill({ json: {
      ...base, data: { status: 'complete', points: [{
        ...batch, batch_id: 'flow-1', sector: 'SEC001', available_at: available,
        net_inflow: '10', unit: 'CNY', unavailable_reason: null,
      }] },
    } });
    if (url.pathname === '/api/realtime/anomalies') return route.fulfill({ json: {
      ...base, data: { status: 'complete', events: [{
        event_id: 'event-1', batch_id: 'anomaly-1', security_id: 'TEST001', rule_version: 'v1',
        event_at: at, available_at: available, metric: 'window_return', window: 2,
        value: '0.1', threshold: '0.05', quality: 'complete',
      }], next_cursor: null },
    } });
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
}

test('cold details do not read metadata; expansion and LHB use exact source identities', async ({ page }, info) => {
  test.skip(info.project.name !== 'owner-source-clocks');
  const transport = await intercept(page);
  await page.goto(path);
  const details = page.locator('details').first();
  await expect(details).toBeVisible();
  expect(transport.reads).toHaveLength(0);
  await details.locator('summary').click();
  await expect(page.getByRole('region', { name: 'Source clocks', exact: true })).toBeVisible();
  expect(transport.reads).toHaveLength(1);
  expect(Object.fromEntries(transport.reads[0].searchParams)).toEqual({
    source_id: 'vendor', kind: 'minutes', batch_id: 'batch-1', as_of: '2026-09-10T02:00:00Z',
  });
  await details.locator('summary').click();
  await page.getByRole('button', { name: 'Refresh context' }).click();
  expect(transport.reads).toHaveLength(1);
  await page.getByRole('button', { name: 'Show LHB' }).click();
  await details.locator('summary').click();
  await expect(page.getByRole('region', { name: 'Source clocks', exact: true })).toContainText('v3');
  expect(Object.fromEntries(transport.reads.at(-1)!.searchParams)).toEqual({
    source_id: 'vendor', disclosure_id: 'disclosure-1', revision: '3', as_of: '2026-09-10T02:00:00Z',
  });
});

test('403, malformed refresh and rights events clear accepted clocks', async ({ page }, info) => {
  test.skip(info.project.name !== 'owner-source-clocks');
  const transport = await intercept(page);
  await page.goto(path);
  await page.locator('summary').click();
  const clocks = page.getByRole('region', { name: 'Source clocks', exact: true });
  await expect(clocks).toBeVisible();
  transport.setBehavior((route) => route.fulfill({ status: 403, body: 'denied' }));
  await page.getByRole('button', { name: 'Refresh context' }).click();
  await expect(clocks).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('unavailable');
  transport.setBehavior((route, url) => route.fulfill({
    json: { ...metadata(url), identity: { ...metadata(url).identity, source_version: 'wrong' } },
  }));
  await page.getByRole('button', { name: 'Refresh clock details' }).click();
  await expect(clocks).toHaveCount(0);
  transport.setBehavior((route, url) => route.fulfill({ json: metadata(url) }));
  await page.getByRole('button', { name: 'Refresh clock details' }).click();
  await expect(clocks).toBeVisible();
  await page.getByRole('button', { name: 'Revoke source' }).click();
  await expect(clocks).toHaveCount(0);
});

test('selection switches and unmounts discard late reads', async ({ page }, info) => {
  test.skip(info.project.name !== 'owner-source-clocks');
  const transport = await intercept(page);
  let release: (() => void) | undefined;
  const pending = new Promise<void>(resolve => { release = resolve; });
  transport.setBehavior(async (route, url) => {
    if (url.searchParams.get('batch_id') === 'batch-1') await pending;
    await route.fulfill({ json: metadata(url) }).catch(() => {});
  });
  await page.goto(path);
  await page.locator('summary').click();
  await expect.poll(() => transport.reads.length).toBe(1);
  await page.getByRole('button', { name: 'Switch batch' }).click();
  const clocks = page.getByRole('region', { name: 'Source clocks', exact: true });
  await expect(clocks).toContainText('v2');
  release!();
  await expect(clocks).not.toContainText('v1');
  await page.getByRole('button', { name: 'Toggle mount' }).click();
  await expect(page.locator('details')).toHaveCount(0);
  await page.getByRole('button', { name: 'Refresh context' }).click();
  expect(transport.reads).toHaveLength(2);
});

test('realtime shared mounts remain read-free until one exact batch is expanded', async ({ page }, info) => {
  test.skip(info.project.name !== 'owner-source-clocks');
  const transport = await intercept(page);
  await realtimeData(page);
  await page.goto(`${path}?surface=realtime`);
  await expect(page.locator('details')).toHaveCount(4);
  expect(transport.reads).toHaveLength(0);
  await page.getByText('minutes source clocks · minute-1', { exact: true }).click();
  await expect(page.getByRole('region', { name: 'Source clocks', exact: true })).toContainText('vendor / v1');
  expect(transport.reads).toHaveLength(1);
  expect(Object.fromEntries(transport.reads[0].searchParams)).toEqual({
    source_id: 'vendor', kind: 'minutes', batch_id: 'minute-1', as_of: '2026-09-10T02:00:00Z',
  });
});

test('public sharing mounts no specialized clock UI and sends no lineage requests', async ({ page }, info) => {
  test.skip(info.project.name !== 'public-no-source-clocks');
  const transport = await intercept(page);
  await page.goto(path);
  await expect(page.getByRole('heading', { name: 'Source clock integration fixture' })).toBeVisible();
  await expect(page.locator('details')).toHaveCount(0);
  await page.getByRole('button', { name: 'Show LHB' }).click();
  await page.getByRole('button', { name: 'Refresh context' }).click();
  expect(transport.reads).toHaveLength(0);
  await realtimeData(page);
  await page.goto(`${path}?surface=realtime`);
  await expect(page.getByRole('table', { name: '实时报价', exact: true })).toBeVisible();
  await expect(page.locator('details')).toHaveCount(0);
  expect(transport.reads).toHaveLength(0);
});
