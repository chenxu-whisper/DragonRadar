import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../src/api';
import { fetchIndexHistory, indexHistoryWindow, validateIndexHistory } from '../../src/features/workbench/index-history';
import { DAY, history, PUB, selection, SESSIONS } from '../fixtures/index-history';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('PUB calendar windows', () => {
  for (const size of [20, 60, 120, 250] as const) {
    it(`keeps an honestly short ${size} session window`, () => {
      expect(indexHistoryWindow(SESSIONS, DAY, size)).toEqual({ start: SESSIONS[0], end: DAY });
    });
  }
  it('uses calendar positions, not natural days or future sessions', () => {
    const days = Array.from({ length: 90 }, (_, i) => new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10));
    expect(indexHistoryWindow(days, days[70], 60)).toEqual({ start: days[11], end: days[70] });
  });
  for (const sessions of [[], [...SESSIONS].reverse(), [...SESSIONS, DAY], ['2026-02-30', DAY]]) {
    it(`rejects noncanonical calendar ${JSON.stringify(sessions)}`, () => {
      expect(indexHistoryWindow(sessions, DAY, 60)).toBeNull();
    });
  }
  it('rejects unavailable PUB cutoff instead of snapping to today', () => {
    expect(indexHistoryWindow(SESSIONS, '2026-09-06', 60)).toBeNull();
  });
});
describe('strict index history response', () => {
  it('keeps exact decimals, gap and retrospective labels', () => {
    expect(validateIndexHistory(history(), selection, SESSIONS)).toBeUndefined();
  });
  it('accepts complete candle and all-gap windows without fabricating prices', () => {
    const candles = history();
    candles.data.points.forEach(p => Object.assign(p, { open: '1234', high: '1235', low: '1233',
      close: '1234', reason: null, chart_type: 'candle', available_at: `${p.trade_date}T09:00:00Z` }));
    candles.data.chart_type = 'candle';
    candles.data.coverage = candles.coverage = { expected: 6, valid: 6, excluded: 0, missing: 0 };
    expect(validateIndexHistory(candles, selection, SESSIONS)).toBeUndefined();
    Object.assign(candles.data.points[0], { low: '1235' });
    expect(validateIndexHistory(candles, selection, SESSIONS)).toBeTruthy();
    const gaps = history();
    gaps.data.points.forEach(p => Object.assign(p, { close: null, available_at: null,
      reason: 'observation_missing', chart_type: 'gap' }));
    gaps.data.chart_type = 'gap';
    gaps.data.coverage = gaps.coverage = { expected: 6, valid: 0, excluded: 0, missing: 6 };
    expect(validateIndexHistory(gaps, selection, SESSIONS)).toBeUndefined();
  });
  const corruptions: [string, (value: any) => void][] = [
    ['PUB', v => { v.snapshot_id = PUB.replace('091', '090'); }],
    ['date', v => { v.trade_date = '2026-09-09'; }],
    ['identity', v => { v.data.identity.security_type = 'stock'; }],
    ['security', v => { v.data.security_id = 'OTHER'; }],
    ['float', v => { v.data.points[0].close = 1234; }],
    ['NaN', v => { v.data.points[0].close = 'NaN'; }],
    ['negative', v => { v.data.points[0].close = '-1'; }],
    ['zero', v => { v.data.points[0].close = '0'; }],
    ['partial candle', v => { v.data.points[0].open = '1234'; }],
    ['volume', v => { v.data.points[0].volume = '1'; }],
    ['currency', v => { v.data.points[0].amount = '1'; }],
    ['basis', v => { v.data.index_basis.series_type = 'guessed'; }],
    ['source', v => { v.data.points[0].source_version = 'different'; }],
    ['late', v => { v.data.points[0].available_at = '2026-09-11T09:00:00Z'; }],
    ['missing timestamp zone', v => { v.data.points[0].available_at = '2026-09-03T09:00:00'; }],
    ['coverage', v => { v.data.coverage.valid = 6; }],
    ['duplicate', v => { v.data.points[1].trade_date = SESSIONS[0]; }],
    ['omitted session', v => { v.data.points.splice(2, 1); }],
    ['request', v => { v.data.requested.start = '2026-09-04'; }],
    ['available', v => { v.data.available.start = '2026-09-04'; }],
    ['archive', v => { v.data.archive.end = '2026-09-09'; }],
    ['chart', v => { v.data.chart_type = 'candle'; }],
    ['gap', v => { v.data.points[2].reason = null; }],
    ['digest', v => { v.data.source.source_archive_digest = 'not-a-digest'; }],
    ['source PUB', v => { v.data.source.market_snapshot_id = 'other'; }],
    ['provenance', v => { v.data.points[0].provenance = 'contemporaneous'; }],
  ];
  for (const [name, mutate] of corruptions) it(`rejects ${name}`, () => {
    const value = history(); mutate(value);
    expect(validateIndexHistory(value, selection, SESSIONS)).toBeTruthy();
  });
  for (const reason of ['materialization_unavailable', 'index_history_unavailable', 'no_sessions']) {
    it(`accepts explicit unavailable ${reason}`, () => {
      const value = { ...history(), data: null, coverage: null, reason, quality_flags: [reason] };
      expect(validateIndexHistory(value, selection, SESSIONS)).toBeUndefined();
      expect(validateIndexHistory({ ...value, reason: 'unknown' }, selection, SESSIONS)).toBeTruthy();
    });
  }
});
describe('bounded same-origin history fetch', () => {
  it('reads only the index endpoint with raw PUB selectors and no cache', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => history() });
    vi.stubGlobal('fetch', fetch);
    const result = await fetchIndexHistory(selection, SESSIONS);
    expect((result.data as any).points[0].close).toBe('1234.123456789012345678901');
    expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/index\/IDX\/history\?/),
      expect.objectContaining({ method: 'GET', credentials: 'same-origin', cache: 'no-store' }));
    const url = new URL(fetch.mock.calls[0][0], 'https://local.test');
    expect(url.searchParams.get('snapshot_id')).toBe(PUB);
    expect(url.searchParams.get('adj')).toBe('raw');
  });
  it('cancels a fetch even when a transport ignores AbortSignal', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const controller = new AbortController();
    const request = fetchIndexHistory(selection, SESSIONS, controller.signal);
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('cancels stalled body parsing and refuses pre-aborted I/O', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: () => new Promise(() => {}) });
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    const request = fetchIndexHistory(selection, SESSIONS, controller.signal);
    await Promise.resolve();
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: 'AbortError' });
    fetch.mockClear();
    await expect(fetchIndexHistory(selection, SESSIONS, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetch).not.toHaveBeenCalled();
  });
  it('bounds body parsing at 15 seconds', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => new Promise(() => {}) }));
    const request = fetchIndexHistory(selection, SESSIONS);
    const assertion = expect(request).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(15000);
    await assertion;
  });
  it('sanitizes server errors and preserves authorization code', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403,
      json: async () => ({ code: 'source_forbidden', detail: 'private token' }) }));
    await expect(fetchIndexHistory(selection, SESSIONS)).rejects.toEqual(new ApiError(403, undefined, 'source_forbidden'));
  });
  for (const securityId of ['..', '.', 'IDX/stock', 'IDX\\stock', 'IDX?x', '']) {
    it(`rejects unsafe selection ${securityId} before I/O`, async () => {
      const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
      await expect(fetchIndexHistory({ ...selection, securityId }, SESSIONS)).rejects.toMatchObject({ status: 400 });
      expect(fetch).not.toHaveBeenCalled();
    });
  }
});
