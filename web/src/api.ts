export class ApiError extends Error {
  status: number;
  constructor(status: number, message?: string) {
    const messages: Record<number, string> = {
      0: '网络连接失败，请检查连接后重试。',
      400: '研究区间不可用，请检查日期、交易日间隔和历史样本。',
      403: '来源访问已被拒绝，已清除所有受保护内容。',
      404: '未找到该快照或证券，当前日期与输入已保留。',
      408: '请求超过 15 秒，请重试。',
      422: '样本不足或研究条件无效，请检查区间；不会自动缩短区间。',
      503: '数据服务暂不可用，请稍后重试。',
    };
    super(message || messages[status] || '请求失败，请重试或检查本地数据服务。');
    this.name = 'ApiError';
    this.status = status;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nullableStrings(value: Record<string, unknown>, keys: string[]): boolean {
  return keys.every(key => value[key] === null || typeof value[key] === 'string');
}

function quoteFields(value: Record<string, unknown>): boolean {
  return nullableStrings(value, ['currency', 'price_unit', 'volume_unit', 'amount_unit', 'close', 'amount']) &&
    (value.suspended === null || typeof value.suspended === 'boolean') &&
    Array.isArray(value.missing_reasons) && value.missing_reasons.every(reason => typeof reason === 'string');
}

function validateSecurityResponse(data: unknown, id: string): void {
  const result = record(data) ? data.result : undefined;
  if (!record(result) || !record(result.security) ||
    typeof result.security.security_id !== 'string' || !quoteFields(result.security) ||
    !nullableStrings(result.security, ['name', 'exchange', 'board', 'change']) ||
    result.security.change_unit !== 'fraction' ||
    !Array.isArray(result.history) || !result.history.every(point =>
      record(point) && typeof point.trade_date === 'string' && quoteFields(point) &&
      nullableStrings(point, ['open', 'high', 'low', 'volume'])) ||
    (result.chart_type !== 'candlestick' && result.chart_type !== 'line') ||
    typeof result.price_basis !== 'string' || typeof result.basis !== 'string') {
    throw new ApiError(502, '证券详情响应格式无效，已丢弃该响应，请重试。');
  }
  if (result.security.security_id !== id) {
    throw new ApiError(409, '响应证券与当前请求不匹配，已丢弃该响应，请重试。');
  }
}

export async function request<T>(path: string, options: { signal?: AbortSignal; snapshot?: string } = {}): Promise<T> {
  if (!path.startsWith('/api/v1/') || path.includes('\\')) throw new ApiError(400, '仅允许同源只读接口。');
  // Inspect the raw segment before URL/fetch can normalize "." and ".." away.
  const securityPath = path.match(/^\/api\/v1\/snapshots\/[^/?#]+\/securities\/([^/?#]+)\/?(?:[?#]|$)/);
  let securityId: string | undefined;
  if (securityPath) {
    try { securityId = decodeURIComponent(securityPath[1]); }
    catch { throw new ApiError(400, '证券标识无效，请返回研究列表重新选择。'); }
    if (securityId === '.' || securityId === '..') throw new ApiError(400, '证券标识无效，请返回研究列表重新选择。');
  }
  const controller = new AbortController();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = () => { controller.abort(); rejectAbort(new DOMException('请求已取消', 'AbortError')); };
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  timeout = setTimeout(() => {
    rejectAbort(new ApiError(408));
    controller.abort();
  }, 15000);
  try {
    const response = await Promise.race([
      fetch(path, { method: 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } }),
      interrupted,
    ]);
    if (!response.ok) throw new ApiError(response.status);
    let data: unknown;
    try { data = await Promise.race([response.json(), interrupted]); }
    catch (error) { if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error; throw new ApiError(502, '接口响应格式无效，请重试。'); }
    if (options.snapshot && (!data || typeof data !== 'object' || !('snapshot_id' in data) || data.snapshot_id !== options.snapshot)) {
      throw new ApiError(409, '响应快照与当前选择不匹配，已丢弃该响应。');
    }
    if (securityId !== undefined) validateSecurityResponse(data, securityId);
    return data as T;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new ApiError(0);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}

export function requestGeneration() {
  let generation = 0;
  return { next: () => ++generation, isCurrent: (value: number) => value === generation };
}
