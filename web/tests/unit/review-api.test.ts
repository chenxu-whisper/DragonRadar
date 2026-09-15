import { afterEach, describe, expect, it, vi } from 'vitest';
import { privateRequest, PrivateError } from '../../src/features/review/api';
import { command } from '../../src/features/review/draft';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('私人请求契约', () => {
  it('原命令重试保留方法、正文、键和带引号版本，只读 Cookie 构建 CSRF', async () => {
    vi.stubGlobal('document', { cookie: '__Host-dragon-private-csrf=test-csrf' });
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(new Response('{}'));
    vi.stubGlobal('fetch', fetcher);
    const saved = command('PATCH', '/notes/00000000-0000-4000-8000-000000000001', { content: { title: '原创' } }, 2);
    await expect(privateRequest(saved.path, saved)).rejects.toMatchObject({ status: 0 });
    await privateRequest(saved.path, saved);
    const first = fetcher.mock.calls[0][1], retry = fetcher.mock.calls[1][1];
    expect(first.body).toBe(retry.body);
    expect(first.method).toBe('PATCH');
    expect(first.headers).toEqual(retry.headers);
    expect(first.headers['If-Match']).toBe('"2"');
    expect(first.headers['Idempotency-Key']).toBe(saved.key);
    expect(first.headers['X-CSRF-Token']).toBe('test-csrf');
    expect(first.cache).toBe('no-store');
    expect(first.credentials).toBe('same-origin');
  });
  it.each(['source_forbidden', 'csrf_forbidden', 'origin_forbidden'])('403保留独立错误码 %s', async code => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, message: 'do not echo' }), { status: 403 })));
    await expect(privateRequest('/session')).rejects.toMatchObject({ code, status: 403 });
  });
  it('拒绝任意URL、路径穿越以及写入查询串', async () => {
    for (const path of ['https://evil.test', '/notes/../session', '/notes?text=secret', '/notes\\bad']) {
      await expect(privateRequest(path, command('POST', path, {}))).rejects.toBeInstanceOf(PrivateError);
    }
  });
  it('15秒超时取消网络且保留可重试状态', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn((_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));
    vi.stubGlobal('fetch', fetcher);
    const pending = expect(privateRequest('/session')).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(15000);
    await pending;
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  });
  it('503映射未知结果；错误正文不回显服务器message或输入', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'storage_unavailable', message: 'secret-content',
    }), { status: 503 })));
    const failure = await privateRequest('/session').catch(error => error as PrivateError);
    expect(failure).toBeInstanceOf(PrivateError);
    if (!(failure instanceof PrivateError)) throw new Error('Expected private error');
    expect(failure.uncertain).toBe(true);
    expect(failure.message).not.toContain('secret-content');
  });
});
