import { expect, test } from 'vitest';
import { AnomalyPages } from '../../src/features/realtime/anomaly-pages';
import type { AnomalyResponse } from '../../src/features/realtime/types';

const selection = { date: '2026-09-10', securityId: 'TEST001', sourceId: 'feed', limit: 50 };
function page(ids: number[], cursor: string | null, asOf = '2026-09-10T02:00:00Z'): AnomalyResponse {
  return {
    trade_date: selection.date, as_of: asOf, snapshot_id: null, source: 'feed',
    source_version: null, coverage: { expected: ids.length, valid: ids.length, excluded: 0, missing: 0 },
    quality_flags: [], rule_version: null,
    basis: { unit: null, currency: null, price_type: 'raw', benchmark: null },
    data: { status: 'complete', next_cursor: cursor, events: ids.map(id => ({
      event_id: `event-${String(id).padStart(3, '0')}`, batch_id: 'batch',
      security_id: 'TEST001', rule_version: 'return-v1', metric: 'window_return',
      event_at: '2026-09-10T01:32:00Z', available_at: '2026-09-10T01:32:02Z',
      window: 3, value: '0.03', threshold: '0.02', quality: 'complete',
    })) },
  };
}
const descending = (top: number, size: number) => Array.from({ length: size }, (_, n) => top - n);

test('traverses 111 tied-time events in three pages with frozen PIT and terminal null', async () => {
  const calls: URL[] = [];
  const replies = [page(descending(111, 50), 'opaque-1'), page(descending(61, 50), 'opaque-2'),
    page(descending(11, 11), null)];
  const chain = new AnomalyPages(async path => {
    calls.push(new URL(path, 'http://localhost'));
    return replies.shift()!;
  });
  chain.reset('source:both-batches:access:session:refresh', selection);
  await chain.load();
  await chain.load();
  await chain.load();
  await chain.load();
  expect(calls).toHaveLength(3);
  expect(calls[1].searchParams.get('as_of')).toBe('2026-09-10T02:00:00Z');
  expect(calls[2].searchParams.get('cursor')).toBe('opaque-2');
  expect(calls.every(url => !url.searchParams.has('batch_id'))).toBe(true);
  expect(chain.state.data?.data?.events.map(event => event.event_id))
    .toEqual(descending(111, 111).map(id => `event-${String(id).padStart(3, '0')}`));
  expect(chain.state.hasMore).toBe(false);
});

test.each(['cursor', 'duplicate', 'order', 'pit', 'security'])('rejects corrupt continuation: %s', async kind => {
  const next = page([1], null);
  if (kind === 'cursor') next.data!.next_cursor = 'opaque-1';
  if (kind === 'duplicate') next.data!.events[0].event_id = 'event-002';
  if (kind === 'order') next.data!.events[0].available_at = '2026-09-10T01:32:03Z';
  if (kind === 'pit') next.as_of = '2026-09-10T02:00:01Z';
  if (kind === 'security') next.data!.events[0].security_id = 'OTHER';
  const replies = [page([2], 'opaque-1'), next];
  const chain = new AnomalyPages(async () => replies.shift()!);
  chain.reset('k', selection);
  await chain.load();
  await chain.load();
  expect(chain.state.error?.code).toBe('malformed_response');
  expect(chain.state.data).toBeUndefined();
  expect(chain.state.hasMore).toBe(false);
});

test('one inflight only; reset/revocation aborts and rejects a late old page', async () => {
  let release!: (value: AnomalyResponse) => void;
  let signal: AbortSignal | undefined;
  let calls = 0;
  const chain = new AnomalyPages(async (_path, options) => {
    calls += 1;
    signal = options.signal;
    return new Promise(resolve => { release = resolve; });
  });
  chain.reset('k', selection);
  const old = chain.load();
  await chain.load();
  expect(calls).toBe(1);
  chain.reset(null, null);
  expect(signal?.aborted).toBe(true);
  release(page([2], 'opaque-1'));
  await old;
  expect(chain.state.data).toBeUndefined();
  expect(chain.state.loading).toBe(false);
});

test('explicit requested PIT is retained and batch-context refresh starts a new chain', async () => {
  const paths: string[] = [];
  const pit = '2026-09-10T02:00:00Z';
  const chain = new AnomalyPages(async path => { paths.push(path); return page([2], null); });
  chain.reset('quote1:minute1', { ...selection, asOf: pit });
  await chain.load();
  chain.reset('quote2:minute1', { ...selection, asOf: pit });
  expect(chain.state.data).toBeUndefined();
  await chain.load();
  expect(paths).toHaveLength(2);
  expect(new URL(paths[0], 'http://localhost').searchParams.get('as_of')).toBe(pit);
});
