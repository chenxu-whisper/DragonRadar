import { createElement } from 'react';
import { renderToReadableStream } from 'react-dom/server';
import { beforeEach, expect, it, vi } from 'vitest';
import RealtimePanel from '../../src/features/realtime/RealtimePanel';
import type { LineageSelection } from '../../src/features/lineage/types';

const probes = vi.hoisted(() => ({ resource: vi.fn(), anomalies: vi.fn(), clocks: vi.fn() }));
vi.mock('../../src/features/realtime/useRealtimeResource', () => ({ useRealtimeResource: probes.resource }));
vi.mock('../../src/features/realtime/useAnomalyPages', () => ({ useAnomalyPages: probes.anomalies }));
vi.mock('../../src/features/lineage/SourceClocks', () => ({
  default: (props: { selection: LineageSelection }) => {
    probes.clocks(props);
    return createElement('details', { 'aria-label': 'source-clock-wiring-probe' });
  },
}));

const date = '2026-09-10';
const asOf = `${date}T02:00:00Z`;
function envelope(data: unknown, extra = {}) {
  return {
    trade_date: date, as_of: asOf, snapshot_id: null, source: 'vendor', source_version: 'v1',
    coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 }, quality_flags: [],
    rule_version: null, basis: { unit: null, currency: null, price_type: 'raw', benchmark: null },
    data, ...extra,
  };
}
const minute = {
  batch_id: 'minute-1', source_id: 'vendor', source_version: 'v1', security_id: 'TEST001',
  event_at: `${date}T01:30:00Z`, received_at: `${date}T01:30:05Z`, available_at: `${date}T01:30:05Z`,
  open: '10', high: '10', low: '10', close: '10', volume: '1', amount: '10', revision: 1, final: true,
};
const point = {
  batch_id: 'flow-1', source_id: 'vendor', source_version: 'v1', sector: 'SEC001',
  event_at: `${date}T01:30:00Z`, available_at: `${date}T01:30:05Z`,
  net_inflow: '10', unit: 'CNY', unavailable_reason: null,
};
const anomaly = {
  event_id: 'event-1', batch_id: 'anomaly-1', security_id: 'TEST001', rule_version: 'v1',
  event_at: `${date}T01:30:00Z`, available_at: `${date}T01:30:05Z`,
  metric: 'window_return', window: 2, value: '0.1', threshold: '0.05', quality: 'complete',
};
beforeEach(() => {
  probes.clocks.mockClear();
  probes.resource.mockImplementation((path: string | null) => ({
    loading: false, refresh: vi.fn(),
    data: path?.startsWith('/api/realtime/quotes') ? envelope({
      quote_batch_id: 'quote-1', source_event_at: minute.event_at, received_at: minute.received_at,
      delay_ms: 5000, status: 'delayed', quotes: [{
        security_id: 'TEST001', last: '10', bid: null, ask: null, bid_size: null,
        ask_size: null, volume: '1', amount: '10', order_book_status: 'disabled',
        order_book_unavailable_reason: 'disabled',
      }],
    }, { trade_date: null, quality_flags: ['source_delayed'],
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 } })
      : path?.startsWith('/api/realtime/minutes') ? envelope({
        status: 'complete', bars: [minute, { ...minute, event_at: `${date}T01:31:00Z`,
          received_at: `${date}T01:31:05Z`, available_at: `${date}T01:31:05Z` }],
      })
        : path?.startsWith('/api/market/sector-flow/intraday') ? envelope({
          status: 'complete', points: [point, { ...point, event_at: `${date}T01:31:00Z`,
            available_at: `${date}T01:31:05Z` }],
        }) : undefined,
  }));
  probes.anomalies.mockReturnValue({
    loading: false, refresh: vi.fn(), hasMore: false, loadMore: vi.fn(),
    data: envelope({ status: 'complete', events: [anomaly, {
      ...anomaly, event_id: 'event-2', batch_id: 'anomaly-2', event_at: `${date}T01:31:00Z`,
    }], next_cursor: null }),
  });
});
async function render() {
  const stream = await renderToReadableStream(createElement(RealtimePanel, {
    date, securityIds: ['TEST001'], sectorId: 'SEC001', refreshToken: 7,
  }));
  await stream.allReady;
  return new Response(stream).text();
}

it('mounts finite exact source/batch/PIT selections once per batch, not once per row', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch');
  const markup = await render();
  expect(markup.match(/source-clock-wiring-probe/g)).toHaveLength(5);
  const selections = probes.clocks.mock.calls.map(([props]) => props.selection);
  expect(selections).toEqual(expect.arrayContaining([
    ...(['quotes', 'minutes', 'sector_flow', 'anomalies', 'anomalies'] as const).map((kind, index) => ({
      type: 'realtime', source_id: 'vendor', source_version: 'v1', kind,
      batch_id: ['quote-1', 'minute-1', 'flow-1', 'anomaly-1', 'anomaly-2'][index], as_of: asOf,
    })),
  ]));
  expect(probes.clocks.mock.calls.every(([props]) => props.refreshToken === 7)).toBe(true);
  expect(fetcher).not.toHaveBeenCalled();
});

it('does not invent source identities for anomaly events with a mixed/unknown envelope', async () => {
  const previous = probes.anomalies();
  probes.anomalies.mockReturnValue({
    ...previous, data: { ...previous.data, source: null, source_version: null },
  });
  await render();
  expect(probes.clocks.mock.calls.some(([props]) => props.selection.kind === 'anomalies')).toBe(false);
});

it('removes all source-clock mounts when realtime authorization is revoked', async () => {
  probes.resource.mockReturnValue({
    loading: false, refresh: vi.fn(), error: { status: 403, code: 'FORBIDDEN' },
  });
  expect(await render()).toContain('实时来源授权已撤销');
  expect(probes.clocks).not.toHaveBeenCalled();
});
