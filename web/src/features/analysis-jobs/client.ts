/** Owner-only closed transport; never imported into PUBLIC builds. */
import type { AnalysisCommand, AnalysisJob, AnalysisSelection } from './types';

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const TOKEN = /^[A-Za-z0-9_.:-]{1,100}$/;
const ALGORITHM = 'analysis-custom-v1-decimal34';
const PATH = /^\/api\/analysis\/jobs(?:\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})?$/i;
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => key in value);
const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(item => typeof item === 'string' && item.length > 0);
const unique = (value: string[]) => new Set(value).size === value.length;
const finite = (value: unknown) => value === null || typeof value === 'number' && Number.isFinite(value);
const date = (value: unknown): value is string =>
  typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(value))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

export class AnalysisJobError extends Error {
  constructor(readonly status: number, readonly code = 'analysis_request_failed') {
    super(status === 409 ? '任务输入冲突，请明确选择后重试。' :
      status === 403 ? '来源授权已撤销，任务与受保护结果已清除。' :
        status === 401 ? '请先验证所有者会话。' :
          status === 404 ? '任务或冻结输入已不可用。' :
            '请求未完成，请显式重试；提交结果未知时保留原命令。');
  }
}

export function createAnalysisCommand(
  selection: AnalysisSelection, idempotencyKey: string,
): Readonly<AnalysisCommand> | null {
  if (!UUID.test(selection.snapshot_id) || !date(selection.start) || !date(selection.end)
    || selection.start > selection.end || !TOKEN.test(selection.benchmark)
    || !['raw', 'adjusted'].includes(selection.adj)
    || !Array.isArray(selection.symbols) || selection.symbols.length > 5
    || !selection.symbols.every(symbol => TOKEN.test(symbol)) || !unique([...selection.symbols])
    || !Array.isArray(selection.sessions) || selection.sessions.length < 1
    || selection.sessions.length > 250 || !selection.sessions.every(day => date(day)
      && day >= selection.start && day <= selection.end)
    || selection.sessions.some((day, index) => index > 0 && day <= selection.sessions[index - 1])
    || !/^[A-Za-z0-9_.:-]{1,200}$/.test(idempotencyKey)) return null;
  return Object.freeze({
    snapshot_id: selection.snapshot_id, start: selection.start, end: selection.end,
    benchmark: selection.benchmark, adj: selection.adj as 'raw' | 'adjusted',
    symbols: Object.freeze([...selection.symbols]), idempotency_key: idempotencyKey,
  });
}

function resultMatches(result: unknown, selection: AnalysisSelection): boolean {
  if (!object(result) || !exact(result, [
    'schema', 'schema_version', 'algorithm_version', 'snapshot_id',
    'requested_start', 'requested_end', 'adjusted_start', 'adjusted_end',
    'benchmark', 'adjustment', 'eligible_security_ids', 'available', 'reason',
    'ranking', 'exclusions', 'benchmark_return_ratio', 'benchmark_reasons', 'curves',
  ]) || result.schema !== 'dragon-radar.custom-analysis' || result.schema_version !== 1
    || result.algorithm_version !== ALGORITHM || result.snapshot_id !== selection.snapshot_id
    || result.requested_start !== selection.start || result.requested_end !== selection.end
    || result.adjusted_start !== selection.sessions[0]
    || result.adjusted_end !== selection.sessions.at(-1)
    || result.benchmark !== selection.benchmark || result.adjustment !== selection.adj
    || result.available !== true || result.reason !== null
    || !strings(result.eligible_security_ids) || !unique(result.eligible_security_ids)
    || result.eligible_security_ids.length > 5000
    || !finite(result.benchmark_return_ratio) || !Array.isArray(result.benchmark_reasons)
    || !result.benchmark_reasons.every(item => typeof item === 'string')
    || !Array.isArray(result.ranking) || !Array.isArray(result.exclusions)
    || !Array.isArray(result.curves) || result.curves.length > 5) return false;
  const eligible = new Set(result.eligible_security_ids);
  if (!selection.symbols.every(id => eligible.has(id))) return false;
  const ranked = new Set<string>();
  let previousReturn: number | undefined;
  let expectedRank = 0;
  for (const [position, row] of result.ranking.entries()) {
    if (!object(row) || !exact(row, [
      'rank', 'security_id', 'name', 'topics', 'topic_status', 'topic_reasons',
      'return_ratio', 'excess_return_ratio', 'max_drawdown_ratio', 'amount_change_ratio', 'reasons',
    ]) || typeof row.security_id !== 'string' || !eligible.has(row.security_id)
      || ranked.has(row.security_id) || !Number.isSafeInteger(row.rank) || Number(row.rank) < 1
      || typeof row.return_ratio !== 'number' || !Number.isFinite(row.return_ratio)
      || !finite(row.excess_return_ratio) || !finite(row.max_drawdown_ratio)
      || !finite(row.amount_change_ratio)
      || row.name !== null && typeof row.name !== 'string'
      || !Array.isArray(row.topics) || !row.topics.every(topic =>
        object(topic) && exact(topic, ['topic_id', 'name'])
        && typeof topic.topic_id === 'string' && typeof topic.name === 'string')
      || !['available', 'unavailable'].includes(String(row.topic_status))
      || !Array.isArray(row.topic_reasons) || !row.topic_reasons.every(reason => typeof reason === 'string')
      || !Array.isArray(row.reasons) || !row.reasons.every(reason => typeof reason === 'string')
      || selection.symbols.length > 0 && !selection.symbols.includes(row.security_id)) return false;
    ranked.add(row.security_id);
    if (previousReturn !== undefined && row.return_ratio > previousReturn) return false;
    if (previousReturn === undefined || row.return_ratio !== previousReturn) expectedRank = position + 1;
    if (row.rank !== expectedRank) return false;
    previousReturn = row.return_ratio;
  }
  const excluded = new Set<string>();
  for (const row of result.exclusions) {
    if (!object(row) || !exact(row, ['security_id', 'reasons'])
      || typeof row.security_id !== 'string' || !eligible.has(row.security_id)
      || excluded.has(row.security_id) || ranked.has(row.security_id) || !strings(row.reasons)
      || selection.symbols.length > 0 && !selection.symbols.includes(row.security_id)) return false;
    excluded.add(row.security_id);
  }
  const curves = new Set<string>();
  for (const curve of result.curves) {
    if (!object(curve) || !exact(curve, ['security_id', 'points', 'reasons'])
      || typeof curve.security_id !== 'string' || !selection.symbols.includes(curve.security_id)
      || curves.has(curve.security_id) || excluded.has(curve.security_id)
      || !Array.isArray(curve.reasons) || !curve.reasons.every(reason => typeof reason === 'string')
      || !Array.isArray(curve.points) || curve.points.length !== selection.sessions.length
      || !curve.points.every((point, index) => object(point)
        && exact(point, ['trade_date', 'normalized', 'drawdown'])
        && point.trade_date === selection.sessions[index]
        && finite(point.normalized) && finite(point.drawdown))
      || !curve.points.some(point => object(point) && point.normalized !== null)) return false;
    curves.add(curve.security_id);
  }
  return selection.symbols.length > 0
    ? selection.symbols.every(id => curves.has(id) !== excluded.has(id))
    : result.curves.length === 0 && [...ranked].every(id => !excluded.has(id))
      && ranked.size + excluded.size === eligible.size;
}

export function validateAnalysisJob(
  value: unknown, selection: AnalysisSelection, jobId: string,
): value is AnalysisJob {
  if (!object(value) || !exact(value, ['job_id', 'status', 'result', 'failure', 'input'])
    || value.job_id !== jobId || !UUID.test(jobId)
    || !['queued', 'running', 'succeeded', 'failed'].includes(String(value.status))
    || !object(value.input) || !exact(value.input, [
      'snapshot_id', 'start', 'end', 'benchmark', 'adj', 'symbols', 'algorithm_version', 'adjustment_version',
    ])) return false;
  const input = value.input;
  if (input.snapshot_id !== selection.snapshot_id || input.start !== selection.start
    || input.end !== selection.end || input.benchmark !== selection.benchmark
    || input.adj !== selection.adj || input.algorithm_version !== ALGORITHM
    || typeof input.adjustment_version !== 'string' || !TOKEN.test(input.adjustment_version)
    || selection.adjustment_version !== undefined && input.adjustment_version !== selection.adjustment_version
    || JSON.stringify(input.symbols) !== JSON.stringify(selection.symbols)) return false;
  if (value.status === 'succeeded') return value.failure === null && resultMatches(value.result, selection);
  if (value.status === 'failed') return value.result === null && object(value.failure)
    && ['analysis_failed', 'analysis_capacity_exceeded', 'analysis_budget_exceeded',
      'input_forbidden', 'input_unavailable', 'attempts_exhausted'].includes(String(value.failure.code))
    && Object.keys(value.failure).every(key => ['code', 'error_type'].includes(key))
    && (value.failure.error_type === undefined || typeof value.failure.error_type === 'string'
      && TOKEN.test(value.failure.error_type));
  return value.result === null && value.failure === null;
}

export async function analysisJobRequest(
  path: string, options: {
    method?: 'GET' | 'POST'; body?: Readonly<AnalysisCommand>; signal?: AbortSignal;
  } = {},
): Promise<unknown> {
  const method = options.method || 'GET';
  if (!PATH.test(path) || !['GET', 'POST'].includes(method)
    || method === 'POST' && path !== '/api/analysis/jobs'
    || method === 'GET' && (path === '/api/analysis/jobs' || options.body !== undefined)
    || method === 'POST' && (!object(options.body) || !exact(options.body, [
      'snapshot_id', 'start', 'end', 'benchmark', 'adj', 'symbols', 'idempotency_key',
    ]) || typeof options.body.snapshot_id !== 'string' || !UUID.test(options.body.snapshot_id)
      || !date(options.body.start) || !date(options.body.end) || options.body.start > options.body.end
      || !TOKEN.test(String(options.body.benchmark))
      || !['raw', 'adjusted'].includes(String(options.body.adj))
      || !Array.isArray(options.body.symbols) || options.body.symbols.length > 5
      || !options.body.symbols.every(symbol => typeof symbol === 'string' && TOKEN.test(symbol))
      || !unique(options.body.symbols) || typeof options.body.idempotency_key !== 'string'
      || !/^[A-Za-z0-9_.:-]{1,200}$/.test(options.body.idempotency_key))) throw new AnalysisJobError(400);
  const controller = new AbortController();
  let interrupt: (reason: unknown) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { interrupt = reject; });
  const abort = () => {
    controller.abort(); interrupt(new DOMException('请求已取消', 'AbortError'));
  };
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  const timer = setTimeout(() => {
    controller.abort(); interrupt(new AnalysisJobError(408, 'timeout'));
  }, 15000);
  try {
    const response = await Promise.race([fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: method === 'POST' ? {
        Accept: 'application/json', 'Content-Type': 'application/json',
        'X-CSRF-Token': document.cookie.split(';').map(value => value.trim())
          .find(value => value.startsWith('__Host-dragon-private-csrf='))
          ?.slice('__Host-dragon-private-csrf='.length) || '',
      } : { Accept: 'application/json' },
      body: options.body ? JSON.stringify(options.body) : undefined,
    }), interrupted]);
    if (!response.ok) {
      if (response.status === 401) window.dispatchEvent(new Event('private-session-lock'));
      if (response.status === 403) window.dispatchEvent(new Event('private-source-revoked'));
      if ([401, 403].includes(response.status)) throw new AnalysisJobError(response.status);
      const value: unknown = await Promise.race([response.json(), interrupted]).catch(() => null);
      throw new AnalysisJobError(response.status,
        object(value) && typeof value.code === 'string' ? value.code : undefined);
    }
    const value: unknown = await Promise.race([response.json(), interrupted]);
    if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
    return value;
  } catch (error) {
    if (error instanceof AnalysisJobError || error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new AnalysisJobError(0);
  } finally {
    clearTimeout(timer); options.signal?.removeEventListener('abort', abort);
  }
}

export async function submitAnalysisJob(
  command: Readonly<AnalysisCommand>, signal: AbortSignal,
): Promise<{ job_id: string; status: AnalysisJob['status'] }> {
  const value = await analysisJobRequest('/api/analysis/jobs', { method: 'POST', body: command, signal });
  if (!object(value) || !exact(value, ['job_id', 'status'])
    || typeof value.job_id !== 'string' || !UUID.test(value.job_id)
    || !['queued', 'running', 'succeeded', 'failed'].includes(String(value.status))) throw new AnalysisJobError(502);
  return value as unknown as { job_id: string; status: AnalysisJob['status'] };
}

export async function getAnalysisJob(
  jobId: string, selection: AnalysisSelection, signal: AbortSignal,
): Promise<AnalysisJob> {
  const value = await analysisJobRequest(`/api/analysis/jobs/${jobId}`, { signal });
  if (!validateAnalysisJob(value, selection, jobId)) throw new AnalysisJobError(502, 'malformed_response');
  return value;
}
