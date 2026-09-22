import { afterEach, describe, expect, it, vi } from 'vitest';
import { request, ApiError, requestGeneration } from '../../src/api';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('read-only request boundary', () => {
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
    ['envelope', { source_version: 3 }],
    ['quote', { data: { quote: { last: '11.2', change_ratio: 0.1, amount: 100 } } }],
    ['roles', { data: { roles: {} } }],
    ['seal', { data: { seal: { first_seal_time: null, last_seal_time: null, break_count: -1, seal_amount: null } } }],
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
        seal: { first_seal_time: null, last_seal_time: null, break_count: 0, seal_amount: null, reason: null },
        regulation: { active_events: 0 },
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
