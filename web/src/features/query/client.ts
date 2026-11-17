/**
 * Whitelist query HTTP client (EX12/EX13). Mirrors features/ai/client.ts:
 * exact path whitelist, same-origin credentials, cookie CSRF, 15s timeout,
 * caller-supplied idempotency keys, private-session-lock on 401 and
 * fail-closed response validation. No request other than parse/execute is
 * ever issued by the query feature.
 *
 * Error codes follow the DEC12 family table (workbench-api.md §errors):
 * each error status owns a small allowed-code family plus a default; an
 * unexpected or missing body code falls back to the status default. 401 has
 * no default — the gateway code passes through untouched.
 */

import {
  QueryProtocolError,
  validateExecuteResponse,
  validateParseResponse,
  type ExecuteResponse,
  type ParseResponse,
} from './types';

const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
const HASH_RE = /^[0-9a-f]{64}$/;
const QUERY_PATH = /^\/api\/query\/(?:parse|execute)$/;
const TEXT_MIN = 1;
const TEXT_MAX = 500;
const IDEMPOTENCY_KEY_MAX = 200;
const TIMEOUT_MS = 15_000;

export class QueryClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string = '',
    message = '',
  ) {
    super(message || statusToMessage(status));
    this.name = 'QueryClientError';
  }
}

function statusToMessage(status: number): string {
  switch (status) {
    case 400:
      return '查询请求不合法，请检查输入后重试。';
    case 403:
      return '当前会话无权在此快照上执行查询，请刷新后重试。';
    case 404:
      return '快照或查询记录不存在。';
    case 408:
      return '查询请求超时，请稍后使用原条件重试。';
    case 409:
      return '查询确认已失效（条件或快照不一致），请重新解析后再执行。';
    case 413:
      return '查询请求体积超过上限，请缩短语句后重试。';
    case 502:
      return '查询响应格式不正确。';
    case 503:
      return '快照数据暂不可用或已损坏，无法执行查询。';
    default:
      return status === 0 || status >= 500
        ? '查询服务暂不可用，请稍后使用原条件重试。'
        : '查询未完成，请检查当前会话与输入。';
  }
}

/** DEC12 status -> code accepted verbatim from the {code,message} envelope. */
const ALLOWED_CODES_BY_STATUS: Record<number, readonly string[]> = {
  400: ['INVALID_PARAM'],
  403: ['FORBIDDEN'],
  404: ['NOT_FOUND'],
  409: ['VERSION_CONFLICT'],
  413: ['INVALID_PARAM'],
  503: ['STORAGE_UNAVAILABLE', 'DATA_UNAVAILABLE'],
};

/** DEC12 status -> fallback code when the body omits a code or lies. */
const DEFAULT_CODE_BY_STATUS: Record<number, string> = {
  400: 'INVALID_PARAM',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'VERSION_CONFLICT',
  413: 'INVALID_PARAM',
  503: 'STORAGE_UNAVAILABLE',
};

function csrfToken(): string {
  if (typeof document === 'undefined') return '';
  return document.cookie.split(';')
    .map(value => value.trim())
    .find(value => value.startsWith('__Host-dragon-private-csrf='))
    ?.slice('__Host-dragon-private-csrf='.length) || '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

function validText(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= TEXT_MIN
    && value.length <= TEXT_MAX;
}

function validIdempotencyKey(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= IDEMPOTENCY_KEY_MAX;
}

/**
 * Maps a non-OK response onto the DEC12 family. 401 additionally broadcasts
 * the existing private-session-lock event (exactly once per request).
 */
function normalizeError(status: number, data: unknown): QueryClientError {
  const bodyCode = isRecord(data) && typeof data.code === 'string' ? data.code : '';
  const allowed = ALLOWED_CODES_BY_STATUS[status];
  // 401 deliberately has no default: the gateway code passes through.
  const code = allowed && allowed.includes(bodyCode)
    ? bodyCode
    : DEFAULT_CODE_BY_STATUS[status] ?? bodyCode;
  if (status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('private-session-lock'));
  }
  return new QueryClientError(status, code);
}

/**
 * Low-level whitelisted POST. Exported for the closed path/body/timeout
 * contract tests; UI code should call {@link parseQuery}/{@link executeQuery}.
 */
export async function queryRequest(
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<unknown> {
  if (typeof path !== 'string'
    || !QUERY_PATH.test(path)
    || path.includes('\\')
    || path.includes('?')) {
    throw new QueryClientError(400, 'INVALID_PARAM');
  }
  const controller = new AbortController();
  let rejectInterrupted: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectInterrupted = reject; });
  const abort = () => {
    controller.abort();
    rejectInterrupted(new DOMException('查询请求已取消', 'AbortError'));
  };
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(() => {
    controller.abort();
    rejectInterrupted(new QueryClientError(408, 'timeout'));
  }, TIMEOUT_MS);
  try {
    const response = await Promise.race([
      fetch(path, {
        method: 'POST',
        body: JSON.stringify(body),
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken(),
        },
      }),
      interrupted,
    ]);
    let data: unknown;
    try {
      data = await Promise.race([response.json(), interrupted]);
    } catch (error) {
      // P2-1：取消（外部 AbortController 透传的 DOMException AbortError）与
      // 15s 超时（QueryClientError 408 timeout）必须原样透传，不能在内层被
      // 统一包装成 502；其余解析异常才落 malformed_response。
      if (error instanceof QueryClientError
        || (error instanceof DOMException && error.name === 'AbortError')) {
        throw error;
      }
      // P2-2：响应头已到但体不是 JSON 时，非 2xx（如 401/503 空体）仍按状态
      // 码归族（401 照常派发一次 private-session-lock），不得误判为 502。
      if (!response.ok) throw normalizeError(response.status, null);
      throw new QueryClientError(502, 'malformed_response', '查询响应格式不正确。');
    }
    if (!response.ok) throw normalizeError(response.status, data);
    if (!isRecord(data)) {
      throw new QueryClientError(502, 'malformed_response', '查询响应格式不正确。');
    }
    return data;
  } catch (error) {
    if (error instanceof QueryClientError
      || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new QueryClientError(0, 'network_error');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

function sendValidated<T>(
  path: string,
  body: Record<string, unknown>,
  signal: AbortSignal | undefined,
  validate: (value: unknown) => T,
): Promise<T> {
  return queryRequest(path, body, signal).then(data => {
    try {
      return validate(data);
    } catch (error) {
      if (error instanceof QueryProtocolError) {
        throw new QueryClientError(502, 'malformed_response', '查询响应格式不正确。');
      }
      throw error;
    }
  });
}

/** EX12 parse: exactly three frozen body keys; the caller owns the key. */
export function parseQuery(
  text: string,
  snapshotId: string,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ParseResponse> {
  if (!validText(text) || !isUuid(snapshotId) || !validIdempotencyKey(idempotencyKey)) {
    return Promise.reject(new QueryClientError(400, 'INVALID_PARAM'));
  }
  return sendValidated(
    '/api/query/parse',
    { text, snapshot_id: snapshotId, idempotency_key: idempotencyKey },
    signal,
    validateParseResponse,
  );
}

export interface ExecuteCommand {
  queryId: string;
  confirmedAstHash: string;
  snapshotId: string;
  idempotencyKey: string;
}

/** EX13 execute: exactly four frozen body keys and never a date. */
export function executeQuery(
  command: ExecuteCommand,
  signal?: AbortSignal,
): Promise<ExecuteResponse> {
  if (!isRecord(command)
    || !isUuid(command.queryId)
    || typeof command.confirmedAstHash !== 'string'
    || !HASH_RE.test(command.confirmedAstHash)
    || !isUuid(command.snapshotId)
    || !validIdempotencyKey(command.idempotencyKey)) {
    return Promise.reject(new QueryClientError(400, 'INVALID_PARAM'));
  }
  return sendValidated(
    '/api/query/execute',
    {
      query_id: command.queryId,
      confirmed_ast_hash: command.confirmedAstHash,
      snapshot_id: command.snapshotId,
      idempotency_key: command.idempotencyKey,
    },
    signal,
    validateExecuteResponse,
  );
}
