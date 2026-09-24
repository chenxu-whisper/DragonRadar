import { afterEach, describe, expect, it, vi } from 'vitest';
import { request, ApiError, requestGeneration } from '../../src/api';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('read-only request boundary', () => {
  const resetEnvelope = (data: unknown) => ({
    trade_date: '2026-09-10',
    as_of: '2026-09-10T10:00:00Z',
    snapshot_id: 'snapshot-a',
    source: 'analysis',
    source_version: 'v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: 'analysis-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    data,
  });

  it('only allows same-origin API paths', async () => {
    await expect(request('https://example.com/api')).rejects.toThrow('同源');
    await expect(request('//example.com/api')).rejects.toThrow('同源');
  });
  it.each([403, 404, 422, 503])('retains HTTP %s error identity', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status })));
    await expect(request('/api/v1/snapshots')).rejects.toMatchObject({ status });
  });
  it('rejects mismatching snapshot envelopes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ snapshot_id: 'old' }))));
    await expect(request('/api/v1/snapshots/new', { snapshot: 'new' })).rejects.toThrow('快照');
  });
  it('maps offline failures and malformed JSON to user-facing errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(request('/api/v1/snapshots')).rejects.toBeInstanceOf(ApiError);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('invalid')));
    await expect(request('/api/v1/snapshots')).rejects.toThrow('响应');
  });
  it('times out even if a transport ignores abort', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const promise = request('/api/v1/snapshots');
    const assertion = expect(promise).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(15000);
    await assertion;
  });
  it('honors external abort even if transport ignores it', async () => {
    const controller = new AbortController();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const promise = request('/api/v1/snapshots', { signal: controller.signal });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('sends read-only, noncached requests and accepts a matching envelope', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ snapshot_id: 'a', result: {} })));
    vi.stubGlobal('fetch', fetcher);
    expect(await request('/api/v1/snapshots/a', { snapshot: 'a' })).toMatchObject({ snapshot_id: 'a' });
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store', credentials: 'same-origin' });
  });
  it('invalidates old generations after new requests and permission changes', () => {
    const gate = requestGeneration();
    const first = gate.next(), second = gate.next();
    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
    gate.next();
    expect(gate.isCurrent(second)).toBe(false);
  });

  it.each([
    ['/api/rank/range?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&benchmark=B000300&adj=raw&sort=return', {
      window_days: 20,
      adjusted_start: '2026-08-13',
      adjusted_end: '2026-09-10',
      benchmark_return: 0.02,
      ranking: [{
        rank: 1, security_id: 'TEST001', name: '合成甲',
        topics: [{ topic_id: 'SEC001', name: '机器人' }],
        topic_status: 'available', topic_reasons: [], reasons: [],
        return_ratio: 0.1, excess_return_ratio: 0.08,
        max_drawdown_ratio: 0.25, amount_change_ratio: 0.2,
      }],
      exclusions: [],
    }],
    ['/api/compare/curves?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&symbols=TEST001&benchmark=B000300&adj=raw', {
      adjusted_start: '2026-08-13',
      adjusted_end: '2026-09-10',
      benchmark: 'B000300',
      curves: [{
        security_id: 'TEST001',
        reasons: ['missing_price'],
        points: [
          { trade_date: '2026-08-13', normalized: 100, drawdown: 0 },
          { trade_date: '2026-08-14', normalized: null, drawdown: null },
        ],
      }],
      exclusions: [],
    }],
    ['/api/stock/TEST001/kline?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw', {
      security_id: 'TEST001',
      adjustment: 'raw',
      adjusted_start: '2026-08-13',
      adjusted_end: '2026-09-10',
      chart_type: 'candlestick',
      points: [{
        trade_date: '2026-08-13', open: 10, high: 12, low: 9,
        close: 11, volume: 100, amount: 1100,
      }],
    }],
  ])('allows and validates the published analysis response %s', async (path, data) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(resetEnvelope(data)))));
    await expect(request(path, { snapshot: 'snapshot-a' })).resolves.toMatchObject({ data });
  });

  it.each([
    ['rank snapshot', '/api/rank/range?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw&sort=return', {
      ...resetEnvelope({
        window_days: 20, adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
        benchmark_return: 0.02, ranking: [], exclusions: [],
      }),
      snapshot_id: 'snapshot-b',
    }, 409],
    ['rank shape', '/api/rank/range?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw&sort=return', resetEnvelope({
      window_days: 20, adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
      benchmark_return: 0.02, ranking: 'private dirty payload', exclusions: [],
    }), 502],
    ['curve sessions', '/api/compare/curves?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&symbols=TEST001&adj=raw', resetEnvelope({
      adjusted_start: '2026-08-13', adjusted_end: '2026-09-10', benchmark: 'B000300',
      curves: [{
        security_id: 'TEST001', reasons: [],
        points: [{ trade_date: '2026-09-11', normalized: 100, drawdown: 0 }],
      }],
      exclusions: [],
    }), 409],
    ['kline security', '/api/stock/TEST001/kline?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw', resetEnvelope({
      security_id: 'TEST999', adjustment: 'raw',
      adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
      chart_type: 'line', points: [],
    }), 409],
    ['rank price basis', '/api/rank/range?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&benchmark=B000300&adj=qfq&sort=return', resetEnvelope({
      window_days: 20, adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
      benchmark_return: 0.02, ranking: [], exclusions: [],
    }), 502],
    ['curve symbols', '/api/compare/curves?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&symbols=TEST001&adj=raw', resetEnvelope({
      adjusted_start: '2026-08-13', adjusted_end: '2026-09-10', benchmark: 'B000300',
      curves: [{ security_id: 'TEST999', reasons: [], points: [] }],
      exclusions: [],
    }), 502],
    ['rank invariant', '/api/rank/range?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw&sort=return', resetEnvelope({
      window_days: 20, adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
      benchmark_return: 0.02,
      ranking: [{
        rank: 1, security_id: 'TEST001', name: null,
        topics: [], topic_status: 'available', topic_reasons: [], reasons: [],
        return_ratio: -1, excess_return_ratio: 0,
        max_drawdown_ratio: 0, amount_change_ratio: 0,
      }],
      exclusions: [],
    }), 502],
    ['curve invariant', '/api/compare/curves?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&symbols=TEST001&adj=raw', resetEnvelope({
      adjusted_start: '2026-08-13', adjusted_end: '2026-09-10', benchmark: 'B000300',
      curves: [{
        security_id: 'TEST001', reasons: [],
        points: [{ trade_date: '2026-08-13', normalized: 99, drawdown: 0 }],
      }],
      exclusions: [],
    }), 502],
    ['kline invariant', '/api/stock/TEST001/kline?snapshot_id=snapshot-a&start=2026-08-13&end=2026-09-10&adj=raw', resetEnvelope({
      security_id: 'TEST001', adjustment: 'raw',
      adjusted_start: '2026-08-13', adjusted_end: '2026-09-10',
      chart_type: 'line',
      points: [{
        trade_date: '2026-08-13',
        open: null, high: null, low: null, close: 11, volume: -1, amount: 1100,
      }],
    }), 502],
  ])('rejects mismatched or malformed %s without echoing payloads', async (_name, path, body, status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
    const failure = request(path, { snapshot: 'snapshot-a' });
    await expect(failure).rejects.toMatchObject({ status });
    await expect(failure).rejects.not.toThrow('private dirty payload');
  });

  it.each([
    ['envelope', { source_version: 3 }],
    ['quote', { data: { quote: { last: '11.2', change_ratio: 0.1, amount: 100 } } }],
    ['roles', { data: { roles: {} } }],
    ['seal', { data: { seal: { first_seal_time: null, last_seal_time: null, break_count: -1, seal_amount: null } } }],
    ['seal conflict', { data: { seal: {
      is_limit_up: true, touched_limit: null,
      first_seal_time: null, last_seal_time: null, break_count: null,
      seal_amount: null, seal_amount_unit: null, continuous_boards: null,
      reason: 'seal_event_missing',
    } } }],
    ['regulation', { data: { regulation: { active_events: '0' } } }],
  ])('rejects a malformed reset stock %s with a sanitized 502', async (_field, patch) => {
    const valid = {
      trade_date: '2026-09-10',
      as_of: '2026-09-10T10:00:00Z',
      snapshot_id: 'snapshot-a',
      source: 'market',
      source_version: 'v1',
      coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
      quality_flags: [],
      rule_version: 'role-v1',
      basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
      data: {
        security_id: 'TEST001',
        name: '合成测试证券',
        quote: { last: 11.2, change_ratio: 0.1, amount: 100 },
        roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
        seal: {
          is_limit_up: null, touched_limit: null,
          first_seal_time: null, last_seal_time: null, break_count: 0,
          seal_amount: null, seal_amount_unit: null, continuous_boards: null, reason: null,
        },
        regulation: { active_events: 0, reason: null },
      },
    };
    const malformed = {
      ...valid,
      ...patch,
      data: { ...valid.data, ...(('data' in patch ? patch.data : {}) as object) },
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(malformed))));

    await expect(request('/api/stock/TEST001?date=2026-09-10&snapshot_id=snapshot-a', {
      snapshot: 'snapshot-a',
    })).rejects.toMatchObject({
      status: 502,
      code: 'malformed_response',
      message: '证券详情加载失败：数据格式不完整。',
    });
  });

  it('accepts nullable regulation events and complete seal fields', async () => {
    const valid = {
      ...resetEnvelope({
        security_id: 'TEST001',
        name: '合成测试证券',
        quote: { last: 11.2, change_ratio: 0.1, amount: 100 },
        roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
        role_reason: 'role_partial',
        seal: {
          is_limit_up: true, touched_limit: true,
          first_seal_time: '09:35:00', last_seal_time: '14:55:00',
          break_count: 0, seal_amount: 200, seal_amount_unit: 'currency',
          continuous_boards: 2, reason: null,
        },
        regulation: { active_events: null, reason: 'regulation_not_implemented' },
      }),
      basis: { unit: 'currency/share', currency: 'CNY', price_type: 'raw', benchmark: null },
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(valid))));

    await expect(request('/api/stock/TEST001?date=2026-09-10&snapshot_id=snapshot-a', {
      snapshot: 'snapshot-a',
    })).resolves.toMatchObject({ data: { regulation: { active_events: null } } });
  });

  it('accepts a published stock unavailable envelope', async () => {
    const unavailable = {
      ...resetEnvelope(null),
      reason: 'materialization_unavailable',
      quality_flags: ['materialization_unavailable'],
      basis: { unit: null, currency: null, price_type: null, benchmark: null },
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(unavailable))));

    await expect(request('/api/stock/TEST001?date=2026-09-10&snapshot_id=snapshot-a', {
      snapshot: 'snapshot-a',
    })).resolves.toMatchObject({
      data: null,
      reason: 'materialization_unavailable',
    });
  });

  it('rejects malformed legacy stock history before SecurityDetail reads it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      snapshot_id: 'snapshot-a',
      result: {
        security: {
          security_id: 'TEST001', name: '合成测试证券', exchange: 'SSE', board: 'main',
          close: '11.2', change: '0.1', amount: '100', suspended: false, missing_reasons: [],
          currency: 'CNY', price_unit: 'currency/share', volume_unit: 'share', amount_unit: 'currency',
          change_unit: 'fraction',
        },
        history: [{ trade_date: '2026-09-10', close: 11.2 }],
        chart_type: 'line', price_basis: 'raw_close', basis: 'raw_close',
      },
    }))));

    await expect(request('/api/v1/snapshots/snapshot-a/securities/TEST001', {
      snapshot: 'snapshot-a',
    })).rejects.toMatchObject({ status: 502 });
  });
});
