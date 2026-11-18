export class OwnerReadError extends Error {
  constructor(readonly status: number, message = '请求未完成，请检查当前会话与来源授权。') {
    super(message);
  }
  get uncertain() { return this.status === 0 || this.status === 408 || this.status >= 500; }
}

export interface ReadSession {
  owner_id: string;
  session_id: string;
  expires_at: string;
}

export const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
export const isUUID = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);

export async function ownerRequest(path: string, signal: AbortSignal, body?: Record<string, unknown>): Promise<unknown> {
  if (signal.aborted) throw new DOMException('请求已取消', 'AbortError');
  if (!/^\/api\/(?:owner\/v1\/session|news\/[0-9a-f-]{36}\/associations\/(?:review-state\?date=\d{4}-\d{2}-\d{2}&snapshot_id=[0-9a-f-]{36}|review)|ai\/task\/[0-9a-f-]{36}\/evidence\/[A-Za-z0-9_-]{1,100}\?revision=\d+&snapshot_id=[0-9a-f-]{36})$/i.test(path)) {
    throw new OwnerReadError(400);
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener('abort', abort, { once: true });
  if (signal.aborted) abort();
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: controller.signal,
      headers: body ? {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': document.cookie.split(';').map(item => item.trim())
          .find(item => item.startsWith('__Host-dragon-private-csrf='))
          ?.slice('__Host-dragon-private-csrf='.length) || '',
      } : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new Event('private-session-lock'));
      if (response.status === 403) {
        const error: unknown = await response.json().catch(() => null);
        if (isObject(error) && (error.code === 'FORBIDDEN' || error.code === 'source_forbidden')) {
          window.dispatchEvent(new Event('private-source-revoked'));
        }
      }
      throw new OwnerReadError(response.status, response.status === 409
        ? '关联复核发生冲突，请重新读取并显式确认决定。' : undefined);
    }
    const data: unknown = await response.json();
    if (signal.aborted) throw new DOMException('请求已取消', 'AbortError');
    return data;
  } catch (error) {
    if (signal.aborted) throw new DOMException('请求已取消', 'AbortError');
    if (error instanceof OwnerReadError) throw error;
    throw new OwnerReadError(controller.signal.aborted ? 408 : 0, '请求结果未知，请使用原命令重试。');
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', abort);
  }
}

export async function readSession(signal: AbortSignal): Promise<ReadSession> {
  const value = await ownerRequest('/api/owner/v1/session', signal);
  if (!isObject(value) || !isUUID(value.owner_id) || !isUUID(value.session_id)
    || typeof value.expires_at !== 'string' || Date.parse(value.expires_at) <= Date.now()
    || !Number.isFinite(Date.parse(value.expires_at))) {
    throw new OwnerReadError(502, '所有者会话标识不一致。');
  }
  return value as unknown as ReadSession;
}
