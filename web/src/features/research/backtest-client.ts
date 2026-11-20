/**
 * Owner-only research backtest HTTP client (EX14 submit / EX15 read / cancel).
 *
 * Mirrors features/ai/client.ts and features/query/client.ts: an exact path
 * whitelist, same-origin credentials, cookie CSRF on writes, a 15s timeout,
 * caller-supplied idempotency keys, a single private-session-lock broadcast
 * on 401 and fail-closed response validation. No request outside the research
 * backtest namespace is ever issued by this module.
 *
 * Error codes follow the research route family: each error status owns a
 * small allowed-code family plus a default; an unexpected or missing body
 * code falls back to the status default. 401 deliberately has no default —
 * the gateway code passes through untouched.
 */

const UUID_PATTERN = '[\\da-f]{8}-[\\da-f]{4}-[1-5][\\da-f]{3}-[89ab][\\da-f]{3}-[\\da-f]{12}';
const UUID = new RegExp(`^${UUID_PATTERN}$`, 'i');
/**
 * Closed research namespace: the EX14/EX15 backtest collection, run and cancel
 * routes plus the Task 17 owner input-manifest read. No other path under
 * /api/research is reachable through this client.
 */
const RESEARCH_PATH = new RegExp(
  `^/api/research/(?:backtests(?:/${UUID_PATTERN}(?:/cancel)?)?|manifests/${UUID_PATTERN})$`,
  'i',
);
const DEFAULT_TIMEOUT_MS = 15_000;

export type BacktestRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

const RUN_STATUSES = new Set<BacktestRunStatus>([
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]);

export interface BacktestRunRecord {
  run_id: string;
  status: BacktestRunStatus;
  config_hash: string;
  research_only: true;
  result: Record<string, unknown> | null;
  failure: Record<string, unknown> | null;
}

export interface BacktestCommand {
  path: string;
  body: Record<string, unknown> & { idempotency_key: string };
}

/** Owner input-manifest envelope; aligns exactly with the panel materialisation context. */
export interface BacktestManifestContext {
  snapshot_id: string;
  domains: Array<{ domain: 'market' | 'topic' | 'role'; snapshot_id: string }>;
  versions: { adjustment: string; calendar: string; pool: string };
}

const MANIFEST_DOMAINS = new Set(['market', 'topic', 'role']);
const VERSION_TOKEN = /^[A-Za-z0-9_.:-]+$/;
const MAX_VERSION_TOKEN_LENGTH = 100;

export class BacktestClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string = '',
    message = '',
  ) {
    super(message || `研究回测请求未完成（${status}）`);
    this.name = 'BacktestClientError';
  }

  /** Timeouts, transport failures and unexpected 5xx are unknown outcomes. */
  get uncertain(): boolean {
    return this.status === 0
      || this.status === 408
      || (this.status >= 500 && this.status !== 503);
  }
}

/** Research route status -> code accepted verbatim from the {code,message} envelope. */
const ALLOWED_CODES_BY_STATUS: Record<number, readonly string[]> = {
  400: ['INVALID_PARAM'],
  403: ['FORBIDDEN'],
  404: ['NOT_FOUND'],
  409: ['VERSION_CONFLICT'],
  503: ['STORAGE_UNAVAILABLE'],
};

/** Research route status -> fallback code when the body omits a code or lies. */
const DEFAULT_CODE_BY_STATUS: Record<number, string> = {
  400: 'INVALID_PARAM',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'VERSION_CONFLICT',
  503: 'STORAGE_UNAVAILABLE',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

function exactKeys(value: Record<string, unknown>, required: string[]): boolean {
  return Object.keys(value).length === required.length
    && required.every(key => Object.hasOwn(value, key));
}

function csrfToken(): string {
  if (typeof document === 'undefined') return '';
  return document.cookie.split(';')
    .map(value => value.trim())
    .find(value => value.startsWith('__Host-dragon-private-csrf='))
    ?.slice('__Host-dragon-private-csrf='.length) || '';
}

/**
 * Maps a non-OK response onto the research code family. 401 additionally
 * broadcasts the existing private-session-lock event (exactly once). A 503 is
 * a definitive storage-side refusal, so it stays a certain (non-uncertain)
 * outcome.
 */
function normalizeError(status: number, data: unknown): BacktestClientError {
  const bodyCode = isRecord(data) && typeof data.code === 'string' ? data.code : '';
  const allowed = ALLOWED_CODES_BY_STATUS[status];
  // 401 deliberately has no default: the gateway code passes through.
  const code = allowed && allowed.includes(bodyCode)
    ? bodyCode
    : DEFAULT_CODE_BY_STATUS[status] ?? bodyCode;
  if (status === 401 && typeof window !== 'undefined') {
    window.dispatchEvent(new Event('private-session-lock'));
  }
  return new BacktestClientError(status, code);
}

export interface ResearchRequestOptions {
  method?: 'GET' | 'POST';
  body?: Record<string, unknown>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

/**
 * Low-level whitelisted research request. Exported for the closed
 * path/body/timeout contract tests; UI code should call the dedicated
 * submit/get/cancel helpers below.
 */
export async function researchRequest(
  path: string,
  options: ResearchRequestOptions = {},
): Promise<unknown> {
  const method = options.method ?? 'GET';
  if (typeof path !== 'string'
    || !RESEARCH_PATH.test(path)
    || path.includes('\\')
    || path.includes('?')
    || (method === 'GET' && options.body !== undefined)) {
    throw new BacktestClientError(400, 'INVALID_PARAM');
  }
  const controller = new AbortController();
  let rejectInterrupted: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectInterrupted = reject; });
  const abort = () => {
    controller.abort();
    rejectInterrupted(new DOMException('回测请求已取消', 'AbortError'));
  };
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  const timer = setTimeout(() => {
    controller.abort();
    rejectInterrupted(new BacktestClientError(408, 'timeout'));
  }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (method === 'POST') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = csrfToken();
  }
  try {
    const response = await Promise.race([
      fetch(path, {
        method,
        body: method === 'POST' ? JSON.stringify(options.body ?? {}) : undefined,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
        headers,
      }),
      interrupted,
    ]);
    let data: unknown;
    try {
      data = await Promise.race([response.json(), interrupted]);
    } catch (error) {
      // Caller aborts and the 15s timeout must pass through untouched; every
      // other parse failure keeps the response status family (401 still locks
      // the session) instead of being mislabelled as a protocol error.
      if (error instanceof BacktestClientError
        || (error instanceof DOMException && error.name === 'AbortError')) {
        throw error;
      }
      if (!response.ok) throw normalizeError(response.status, null);
      throw new BacktestClientError(502, 'malformed_response');
    }
    if (!response.ok) throw normalizeError(response.status, data);
    if (!isRecord(data)) throw new BacktestClientError(502, 'malformed_response');
    return data;
  } catch (error) {
    if (error instanceof BacktestClientError
      || (error instanceof DOMException && error.name === 'AbortError')) throw error;
    throw new BacktestClientError(0, 'network_error');
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abort);
  }
}

/** Wrap an EX14 body with a caller-owned UUID idempotency key. */
export function createBacktestCommand(
  body: Record<string, unknown>,
  key: string = crypto.randomUUID(),
): BacktestCommand {
  if (!isUuid(key)) throw new BacktestClientError(400, 'INVALID_PARAM');
  return { path: '/api/research/backtests', body: { ...body, idempotency_key: key } };
}

function validateAccepted(data: unknown): { run_id: string; status: BacktestRunStatus } {
  if (!isRecord(data)
    || !exactKeys(data, ['run_id', 'status'])
    || !isUuid(data.run_id)
    || !RUN_STATUSES.has(data.status as BacktestRunStatus)) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  return { run_id: data.run_id, status: data.status as BacktestRunStatus };
}

function validateRunRecord(data: unknown): BacktestRunRecord {
  if (!isRecord(data)
    || !exactKeys(data, ['run_id', 'status', 'config_hash', 'research_only', 'result', 'failure'])
    || !isUuid(data.run_id)
    || !RUN_STATUSES.has(data.status as BacktestRunStatus)
    || typeof data.config_hash !== 'string'
    || data.research_only !== true
    || (data.result !== null && !isRecord(data.result))
    || (data.failure !== null && !isRecord(data.failure))) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  return data as unknown as BacktestRunRecord;
}

/** EX14 submit: the queued envelope must carry exactly run_id + status. */
export async function submitBacktest(
  command: BacktestCommand,
  signal?: AbortSignal,
): Promise<{ run_id: string; status: BacktestRunStatus }> {
  const data = await researchRequest(command.path, {
    method: 'POST',
    body: command.body,
    signal,
  });
  return validateAccepted(data);
}

/** EX15 read: validate the full materialised envelope; never recompute. */
export async function getBacktestRun(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestRunRecord> {
  if (!isUuid(runId)) throw new BacktestClientError(400, 'INVALID_PARAM');
  return validateRunRecord(
    await researchRequest(`/api/research/backtests/${runId}`, { signal }),
  );
}

/**
 * Fail-closed transport validation for the owner manifest envelope: exactly
 * three top-level keys, 1-3 unique domains that must include market, UUID
 * snapshot ids on every entry and three bounded version tokens.
 */
function validateManifestResponse(data: unknown): BacktestManifestContext {
  if (!isRecord(data) || !exactKeys(data, ['snapshot_id', 'domains', 'versions'])) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  if (!isUuid(data.snapshot_id)) throw new BacktestClientError(502, 'malformed_response');
  const domains = data.domains;
  if (!Array.isArray(domains) || domains.length < 1 || domains.length > 3) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  const names: string[] = [];
  for (const entry of domains) {
    if (!isRecord(entry)
      || !exactKeys(entry, ['domain', 'snapshot_id'])
      || typeof entry.domain !== 'string'
      || !MANIFEST_DOMAINS.has(entry.domain)
      || !isUuid(entry.snapshot_id)) {
      throw new BacktestClientError(502, 'malformed_response');
    }
    names.push(entry.domain);
  }
  if (new Set(names).size !== names.length || !names.includes('market')) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  const versions = data.versions;
  const tokens = isRecord(versions)
    ? [versions.adjustment, versions.calendar, versions.pool]
    : [];
  if (!isRecord(versions)
    || !exactKeys(versions, ['adjustment', 'calendar', 'pool'])
    || tokens.some(token => typeof token !== 'string'
      || token.length < 1
      || token.length > MAX_VERSION_TOKEN_LENGTH
      || !VERSION_TOKEN.test(token))) {
    throw new BacktestClientError(502, 'malformed_response');
  }
  return data as unknown as BacktestManifestContext;
}

/** Task 17 owner read: fetch the materialised input manifest for one publication. */
export async function getBacktestManifest(
  snapshotId: string,
  signal?: AbortSignal,
): Promise<BacktestManifestContext> {
  if (!isUuid(snapshotId)) throw new BacktestClientError(400, 'INVALID_PARAM');
  return validateManifestResponse(
    await researchRequest(`/api/research/manifests/${snapshotId}`, { signal }),
  );
}

/** Cooperative cancel: posts an idempotency key and returns the terminal record. */
export async function cancelBacktestRun(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestRunRecord> {
  if (!isUuid(runId)) throw new BacktestClientError(400, 'INVALID_PARAM');
  const command = createBacktestCommand({}, crypto.randomUUID());
  return validateRunRecord(
    await researchRequest(`/api/research/backtests/${runId}/cancel`, {
      method: 'POST',
      body: command.body,
      signal,
    }),
  );
}

/** One-second polling cadence while the run is queued or running. */
export function pollBacktestDecision(
  record: BacktestRunRecord,
): { poll: boolean; delayMs: number } {
  if (record.status === 'queued' || record.status === 'running') {
    return { poll: true, delayMs: 1000 };
  }
  return { poll: false, delayMs: 0 };
}
