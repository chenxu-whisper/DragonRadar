import type { Command } from './types';

const messages: Record<string, string> = {
  session_unauthorized: '会话已锁定，请重新配对。', invalid_pairing: '配对码无效或已过期。',
  source_forbidden: '来源不可用，原创内容仍可保存。',
  csrf_forbidden: 'CSRF 校验未通过，请检查会话后重试。', origin_forbidden: '入口校验未通过，请使用已信任的本机 HTTPS 入口。',
  not_found: '记录不存在或当前不可见。', version_conflict: '版本冲突，请对照后手工处理。',
  conflict: '命令冲突，请勿更换键重复提交未知结果。', invalid_request: '请求不符合要求，请检查文字、日期与验证到期条件。',
  storage_unavailable: '服务暂不可用，结果未知，请重试原命令。', pairing_rate_limited: '配对过于频繁，请稍后再试。',
};
export class PrivateError extends Error {
  constructor(readonly status: number, readonly code = '', readonly current?: unknown) {
    super(messages[code] || (status === 0 || status === 408 || status >= 500
      ? '请求结果未知，请恢复连接后重试原命令。' : '请求未完成，请检查输入后重试。'));
  }
  get uncertain() { return this.status === 0 || this.status === 408 || this.status >= 500; }
}
type Options = Partial<Command> & { signal?: AbortSignal; method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'; pair?: boolean };
export async function privateRequest<T>(path: string, options: Omit<Options, 'method'> & { method?: 'GET' | 'POST' | 'PATCH' | 'DELETE' } = {}): Promise<T> {
  const method = options.method || 'GET';
  if (!/^\/(?:session|pair|notes|watchlist|review-templates)(?:[/?]|$)/.test(path) ||
    /[\\#]/.test(path) || /(?:^|\/)\.{1,2}(?:\/|$)/.test(path) ||
    /%/i.test(path) || (method !== 'GET' && path.includes('?')) ||
    (options.path && options.path !== path)) throw new PrivateError(400);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    if (options.pair) headers['X-Dragon-Owner-Pair'] = '1';
    else {
      const cookie = typeof document === 'undefined' ? '' : document.cookie;
      headers['X-CSRF-Token'] = cookie.split(';').map(value => value.trim()).find(value => value.startsWith('__Host-dragon-private-csrf='))?.slice('__Host-dragon-private-csrf='.length) || '';
    }
    if (options.key) headers['Idempotency-Key'] = options.key;
    if (options.expected !== undefined) headers['If-Match'] = `"${options.expected}"`;
  }
  const controller = new AbortController();
  let rejectInterrupted: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectInterrupted = reject; });
  const abort = () => { controller.abort(); rejectInterrupted(new DOMException('请求已取消', 'AbortError')); };
  const timer = setTimeout(() => { rejectInterrupted(new PrivateError(408)); controller.abort(); }, 15000);
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  try {
    const response = await Promise.race([fetch(`/api/owner/v1${path}`, {
      method, body: method === 'GET' ? undefined : options.body || '{}',
      headers, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
    }), interrupted]);
    const data = await Promise.race([response.json(), interrupted]);
    if (!response.ok) throw new PrivateError(response.status, data.code, data.current);
    return data as T;
  } catch (error) {
    if (error instanceof PrivateError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new PrivateError(0);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}
