import { expect, test } from 'vitest';
import { realtimeRequestPaths } from '../../src/features/realtime/client';

test('mounted selection uses independent quote/minute/flow pins and never daily identity', () => {
  const paths = realtimeRequestPaths('2026-09-10', ['TEST001'], 'SEC001', {
    sourceId: 'feed', quoteBatchId: 'quote-old', minuteBatchId: 'minute-old',
    flowBatchId: 'flow-old', asOf: '2026-09-10T02:00:00Z',
  });
  const parsed = Object.fromEntries(Object.entries(paths).map(([key, path]) =>
    [key, new URL(path!, 'http://localhost').searchParams]));
  expect(parsed.quote.get('source_id')).toBe('feed');
  expect(parsed.quote.get('quote_batch_id')).toBe('quote-old');
  expect(parsed.minute.get('batch_id')).toBe('minute-old');
  expect(parsed.flow.get('batch_id')).toBe('flow-old');
  for (const query of Object.values(parsed)) {
    expect(query.get('as_of')).toBe('2026-09-10T02:00:00Z');
    expect(query.has('snapshot_id')).toBe(false);
    expect(query.has('rule_version')).toBe(false);
  }
  expect(parsed.anomaly.has('batch_id')).toBe(false);
  expect(parsed.anomaly.has('quote_batch_id')).toBe(false);
});

test('batch without source fails closed, preserving no-pin defaults', () => {
  expect(realtimeRequestPaths('2026-09-10', ['TEST001'], 'SEC001', {
    minuteBatchId: 'explicit',
  })).toEqual({ quote: null, minute: null, anomaly: null, flow: null });
  expect(realtimeRequestPaths('2026-09-10', ['TEST001']).quote)
    .toBe('/api/realtime/quotes?security_ids=TEST001');
});
