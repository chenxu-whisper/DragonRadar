import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { fetchLineage, lineageUrl, LineageHttpError } from '../../src/features/lineage/api';
import { DomainClocks } from '../../src/features/lineage/DomainClocks';
import { LineageLifecycle } from '../../src/features/lineage/lifecycle';
import { CLOCK_NAMES, DAILY_DOMAINS, type ClockCell, type LineageSelection } from '../../src/features/lineage/types';
import { clockDelay, formatClock, parseLineage } from '../../src/features/lineage/validation';

const at = '2026-11-24T16:00:00.123456Z';
const selection: LineageSelection = {
  type: 'daily', snapshot_id: '11111111-1111-4111-8111-111111111111', trade_date: '2026-11-24',
};
const known = (): ClockCell => ({ state: 'known', at, first_at: at, last_at: at, reason: null });
const clocks = (): Record<string, unknown> => Object.fromEntries(CLOCK_NAMES.map(name => [name, name === 'page_updated_at'
  ? { state: 'not_applicable', at: null, first_at: null, last_at: null, reason: 'browser_clock' }
  : known()]));
const response = () => ({
  schema: 'dragon-radar.publication-clocks/v1',
  snapshot_id: selection.snapshot_id, trade_date: selection.trade_date,
  domains: Object.fromEntries(DAILY_DOMAINS.map(domain => [domain, {
    status: 'ready', input_id: selection.snapshot_id, as_of: at, clocks: clocks(),
  }])),
});
const sourceSelection: LineageSelection = {
  type: 'realtime', source_id: 'vendor', source_version: 'v1', kind: 'minutes',
  batch_id: 'batch-1', as_of: at,
};
const sourceResponse = () => ({
  schema: 'dragon-radar.source-clocks/v1',
  identity: { source_id: 'vendor', source_version: 'v1', kind: 'minutes', batch_id: 'batch-1' },
  as_of: at,
  clocks: { ...clocks(), snapshot_published_at: {
    state: 'not_applicable', at: null, first_at: null, last_at: null,
    reason: 'domain_clock_not_applicable',
  } } as Record<string, unknown>,
});

describe('B4 exact lineage contracts', () => {
  it('accepts only the exact daily PUB and five domains', () => {
    expect(parseLineage(response(), selection).schema).toBe('dragon-radar.publication-clocks/v1');
    for (const field of ['snapshot_id', 'trade_date']) {
      expect(() => parseLineage({ ...response(), [field]: 'other' }, selection)).toThrow();
    }
    expect(() => parseLineage({ ...response(), extra: true }, selection)).toThrow();
    const bad = response(); delete bad.domains.role;
    expect(() => parseLineage(bad, selection)).toThrow();
  });
  it('rejects malformed cells, naive dates and computed-after-PUB', () => {
    for (const cell of [
      { ...known(), extra: 1 }, { ...known(), at: null },
      { ...known(), first_at: '2026-11-24T16:00:00' },
      { ...known(), reason: 'input_clock_unavailable' },
      { state: 'mixed', at: null, first_at: at, last_at: at, reason: null },
    ]) {
      const bad = response(); bad.domains.market.clocks.source_event_at = cell;
      expect(() => parseLineage(bad, selection)).toThrow();
    }
    const bad = response();
    bad.domains.market.clocks.computed_at = {
      ...known(), at: '2026-11-25T00:00:00Z',
      first_at: '2026-11-25T00:00:00Z', last_at: '2026-11-25T00:00:00Z',
    };
    expect(() => parseLineage(bad, selection)).toThrow();
  });
  it('validates specialized identity, exact cutoff and source-only publication clock', () => {
    expect(parseLineage(sourceResponse(), sourceSelection).schema).toBe('dragon-radar.source-clocks/v1');
    for (const field of ['source_id', 'source_version', 'kind', 'batch_id']) {
      const bad = sourceResponse(); bad.identity = { ...bad.identity, [field]: 'other' };
      expect(() => parseLineage(bad, sourceSelection)).toThrow();
    }
    expect(() => parseLineage({ ...sourceResponse(), as_of: '2026-11-25T00:00:00Z' }, sourceSelection)).toThrow();
    const bad = sourceResponse(); bad.clocks.snapshot_published_at = known();
    expect(() => parseLineage(bad, sourceSelection)).toThrow();
    const lhb: LineageSelection = { type: 'lhb', source_id: 'vendor', source_version: 'v1',
      disclosure_id: 'd1', revision: 2, as_of: at };
    const body = { ...sourceResponse(), identity: {
      source_id: 'vendor', source_version: 'v1', disclosure_id: 'd1', revision: 2,
    } };
    expect(parseLineage(body, lhb).schema).toBe('dragon-radar.source-clocks/v1');
    expect(() => parseLineage({ ...body, identity: { ...body.identity, revision: 1 } }, lhb)).toThrow();
  });
  it('constructs only closed explicit GET paths without a latest fallback', () => {
    expect(lineageUrl(selection)).toBe('/api/snapshots/lineage?date=2026-11-24&snapshot_id=11111111-1111-4111-8111-111111111111');
    expect(lineageUrl(sourceSelection)).toContain('/api/realtime/lineage?source_id=vendor&kind=minutes&batch_id=batch-1&as_of=');
    expect(() => lineageUrl({ ...sourceSelection, batch_id: '' })).toThrow();
  });
  it('fetches read-only metadata without persistence and rejects denied payloads', async () => {
    const transport = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(response()), { status: 200 }),
    );
    try {
      expect((await fetchLineage(selection)).schema).toBe('dragon-radar.publication-clocks/v1');
      expect(transport.mock.calls[0][1]).toMatchObject({
        method: 'GET', cache: 'no-store', credentials: 'same-origin',
      });
      transport.mockResolvedValue(new Response('protected', { status: 403 }));
      await expect(fetchLineage(selection)).rejects.toBeInstanceOf(LineageHttpError);
    } finally { transport.mockRestore(); }
  });
  it('renders all five daily clock groups, local receipt and separate cutoffs', () => {
    const state = new LineageLifecycle();
    state.accept(state.begin(selection), response(), new Date(at));
    const html = renderToStaticMarkup(createElement(DomainClocks, { value: state.current }));
    for (const domain of DAILY_DOMAINS) expect(html).toContain(`aria-label="${domain} clocks"`);
    expect(html.match(/Page accepted/g)).toHaveLength(5);
    expect(html).toContain('cutoff:');
    expect(html).toContain('UTC+08:00');
    expect(renderToStaticMarkup(createElement(DomainClocks, { value: null }))).toContain('role="status"');
  });
  it('displays Beijing midnight, microseconds, ranges and honest unknown subsets', () => {
    expect(formatClock(known())).toContain('2026-11-25 00:00:00.123456');
    expect(formatClock({ state: 'mixed', at: null, first_at: at,
      last_at: '2026-11-24T17:00:00Z', reason: null })).toContain(' to ');
    expect(formatClock({ state: 'unknown', at: null, first_at: at, last_at: at,
      reason: 'input_clock_unavailable' })).toContain('known subset');
  });
  it('computes exact nonnegative milliseconds without manufacturing mixed supplier delays', () => {
    expect(clockDelay(known(), { ...known(), at: '2026-11-24T16:00:00.124455Z',
      first_at: '2026-11-24T16:00:00.124455Z', last_at: '2026-11-24T16:00:00.124455Z' }).milliseconds).toBe(0);
    expect(clockDelay(known(), { ...known(), at: '2026-11-24T16:00:00.122456Z',
      first_at: '2026-11-24T16:00:00.122456Z', last_at: '2026-11-24T16:00:00.122456Z' }).reason).toBe('negative_delay');
    const mixed = { state: 'mixed' as const, at: null, first_at: at,
      last_at: '2026-11-24T16:00:01Z', reason: null };
    expect(clockDelay(mixed, known()).reason).toBe('mixed_clock');
  });
});

describe('B4 browser acceptance lifecycle', () => {
  it('retains same-key valid data during refresh and rejects late generations', () => {
    const state = new LineageLifecycle();
    const first = state.begin(selection); expect(state.accept(first, response(), new Date(at))).toBe(true);
    const accepted = state.current;
    const second = state.begin(selection); expect(state.current).toBe(accepted);
    expect(state.accept(first, response(), new Date())).toBe(false);
    expect(state.accept(second, response(), new Date('2026-11-25T00:00:00Z'))).toBe(true);
    expect(state.current?.page_updated_at).toBe('2026-11-25T00:00:00.000Z');
  });
  it('rejects invalid data and backward browser clocks without advancing acceptance', () => {
    const state = new LineageLifecycle(); const ticket = state.begin(selection);
    state.accept(ticket, response(), new Date(at)); const accepted = state.current;
    const next = state.begin(selection);
    expect(() => state.accept(next, { ...response(), trade_date: 'other' }, new Date())).toThrow();
    expect(state.current).toBe(accepted);
    expect(state.accept(next, response(), new Date('2026-11-24T00:00:00Z'))).toBe(false);
    expect(state.current).toBe(accepted);
  });
  it('clears on selection/reset/denial and never permits stale response restoration', () => {
    const state = new LineageLifecycle(); const first = state.begin(selection);
    state.accept(first, response(), new Date(at));
    const second = state.begin(sourceSelection); expect(state.current).toBeNull();
    expect(state.accept(first, response(), new Date())).toBe(false);
    state.accept(second, sourceResponse(), new Date(at));
    state.deny(second); expect(state.current).toBeNull();
    expect(state.accept(second, sourceResponse(), new Date())).toBe(false);
    const third = state.begin(selection); state.reset();
    expect(state.accept(third, response(), new Date())).toBe(false);
  });
});
