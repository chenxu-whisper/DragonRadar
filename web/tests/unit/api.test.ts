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
});
