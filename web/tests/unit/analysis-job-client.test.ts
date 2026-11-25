import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  analysisJobRequest, createAnalysisCommand, validateAnalysisJob,
} from '../../src/features/analysis-jobs/client';

const snapshot = '11111111-1111-4111-8111-111111111111';
const job = '22222222-2222-4222-8222-222222222222';
const selection = {
  snapshot_id: snapshot, start: '2026-09-03', end: '2026-09-08',
  benchmark: 'B000300', adj: 'raw' as const, symbols: [] as string[],
  sessions: ['2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08'],
};
const input = {
  snapshot_id: snapshot, start: selection.start, end: selection.end,
  benchmark: selection.benchmark, adj: 'raw', symbols: [],
  algorithm_version: 'analysis-custom-v1-decimal34', adjustment_version: 'a1',
};
const pending = { job_id: job, status: 'queued', result: null, failure: null, input };

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const rankingRow = {
  rank: 1, security_id: 'A', name: 'Synthetic A', topics: [],
  topic_status: 'unavailable', topic_reasons: ['topic_batch_unavailable'],
  return_ratio: 0.1, excess_return_ratio: null, max_drawdown_ratio: 0,
  amount_change_ratio: null, reasons: ['benchmark_identity_invalid'],
};
const complete = {
  schema: 'dragon-radar.custom-analysis', schema_version: 1,
  algorithm_version: input.algorithm_version, snapshot_id: snapshot,
  requested_start: selection.start, requested_end: selection.end,
  adjusted_start: selection.start, adjusted_end: selection.end,
  benchmark: selection.benchmark, adjustment: 'raw',
  eligible_security_ids: ['A'], available: true, reason: null,
  ranking: [rankingRow], exclusions: [], benchmark_return_ratio: null,
  benchmark_reasons: ['benchmark_identity_invalid'], curves: [],
};
describe('analysis job immutable private client', () => {
  it('rejects changed version, ranks and overlapping partitions on completed jobs', () => {
    const result = (value: unknown) => validateAnalysisJob({
      ...pending, status: 'succeeded', result: value,
    }, selection, job);
    expect(result(complete)).toBe(true);
    expect(result({ ...complete, schema_version: 2 })).toBe(false);
    expect(result({ ...complete, algorithm_version: 'future-version' })).toBe(false);
    expect(result({ ...complete, ranking: [{ ...rankingRow, rank: 2 }] })).toBe(false);
    expect(result({ ...complete, exclusions: [{ security_id: 'A', reasons: ['missing'] }] })).toBe(false);
    expect(validateAnalysisJob({ ...pending, input: { ...input, adjustment_version: 'a2' } },
      { ...selection, adjustment_version: 'a1' }, job)).toBe(false);
  });
  it('validates exact comparison dates and one curve or exclusion per requested symbol', () => {
    const requested = { ...selection, symbols: ['A'] };
    const curve = {
      security_id: 'A', reasons: [],
      points: selection.sessions.map(trade_date => ({ trade_date, normalized: 1, drawdown: 0 })),
    };
    const result = (value: unknown) => validateAnalysisJob({
      ...pending, input: { ...input, symbols: ['A'] }, status: 'succeeded', result: value,
    }, requested, job);
    expect(result({ ...complete, curves: [curve] })).toBe(true);
    expect(result(complete)).toBe(false);
    expect(result({ ...complete, curves: [curve, curve] })).toBe(false);
    expect(result({ ...complete, curves: [{ ...curve, points: [...curve.points].reverse() }] })).toBe(false);
    expect(result({ ...complete, curves: [{ ...curve, points: curve.points.map(point => ({ ...point, normalized: null })) }] })).toBe(false);
    expect(result({ ...complete, ranking: [], exclusions: [{ security_id: 'A', reasons: ['missing'] }] })).toBe(true);
    expect(result({ ...complete, exclusions: [{ security_id: 'A', reasons: ['missing'] }] })).toBe(false);
  });
  it('rejects path aliases, query additions and mismatched HTTP methods before fetch', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const command = createAnalysisCommand(selection, 'fixed-key')!;
    for (const path of [
      `https://example.test/api/analysis/jobs/${job}`,
      `/api/analysis/jobs/${job}?snapshot_id=${snapshot}`,
      `/api/analysis/jobs/${job}/cancel`, '/api/analysis/jobs/',
    ]) await expect(analysisJobRequest(path)).rejects.toMatchObject({ status: 400 });
    await expect(analysisJobRequest('/api/analysis/jobs')).rejects.toMatchObject({ status: 400 });
    await expect(analysisJobRequest(`/api/analysis/jobs/${job}`, { method: 'POST', body: command }))
      .rejects.toMatchObject({ status: 400 });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([401, 403])('dispatches denial %s and does not parse potentially protected response bodies', async status => {
    const dispatchEvent = vi.fn();
    const json = vi.fn();
    vi.stubGlobal('window', { dispatchEvent });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status, json })));
    await expect(analysisJobRequest(`/api/analysis/jobs/${job}`)).rejects.toMatchObject({ status });
    expect(json).not.toHaveBeenCalled();
    expect(dispatchEvent.mock.calls[0][0].type).toBe(status === 401 ? 'private-session-lock' : 'private-source-revoked');
  });
  it('freezes exact compute fields and key, excluding sorting and sessions', () => {
    const command = createAnalysisCommand(selection, 'fixed-key')!;
    expect(Object.keys(command).sort()).toEqual(
      ['snapshot_id', 'start', 'end', 'benchmark', 'adj', 'symbols', 'idempotency_key'].sort(),
    );
    expect(Object.isFrozen(command)).toBe(true);
    expect(Object.isFrozen(command.symbols)).toBe(true);
    expect(command.idempotency_key).toBe('fixed-key');
    expect(createAnalysisCommand({ ...selection, symbols: ['A', 'A'] }, 'fixed-key')).toBeNull();
    expect(createAnalysisCommand({ ...selection, adj: 'qfq' }, 'fixed-key')).toBeNull();
  });
  it('accepts queued identity and rejects malformed or changed inputs', () => {
    expect(validateAnalysisJob(pending, selection, job)).toBe(true);
    expect(validateAnalysisJob({ ...pending, input: { ...input, start: '2026-09-04' } }, selection, job)).toBe(false);
    expect(validateAnalysisJob({ ...pending, result: {} }, selection, job)).toBe(false);
    expect(validateAnalysisJob({ ...pending, private_path: 'hidden' }, selection, job)).toBe(false);
    expect(validateAnalysisJob(pending, selection, snapshot)).toBe(false);
  });
  it('requires exact failed state with structured safe failure', () => {
    expect(validateAnalysisJob({ ...pending, status: 'failed', failure: { code: 'analysis_budget_exceeded' } }, selection, job)).toBe(true);
    expect(validateAnalysisJob({ ...pending, status: 'failed', failure: null }, selection, job)).toBe(false);
    expect(validateAnalysisJob({ ...pending, status: 'failed', failure: { code: 'analysis_failed', credentials: 'hidden' } }, selection, job)).toBe(false);
  });
  it('rejects succeeded results with missing deep curve/partition identity', () => {
    expect(validateAnalysisJob({ ...pending, status: 'succeeded', result: {} }, selection, job)).toBe(false);
    const result = {
      schema: 'dragon-radar.custom-analysis', schema_version: 1,
      algorithm_version: input.algorithm_version, snapshot_id: snapshot,
      requested_start: selection.start, requested_end: selection.end,
      adjusted_start: selection.start, adjusted_end: selection.end,
      benchmark: selection.benchmark, adjustment: 'raw',
      eligible_security_ids: [], available: true, reason: null,
      ranking: [], exclusions: [], benchmark_return_ratio: null,
      benchmark_reasons: ['benchmark_identity_invalid'], curves: [],
    };
    expect(validateAnalysisJob({ ...pending, status: 'succeeded', result }, selection, job)).toBe(true);
    expect(validateAnalysisJob({ ...pending, status: 'succeeded', result: { ...result, eligible_security_ids: ['A'] } }, selection, job)).toBe(false);
    const row = {
      rank: 1, security_id: 'A', name: 'Synthetic A', topics: [],
      topic_status: 'unavailable', topic_reasons: ['topic_batch_unavailable'],
      return_ratio: 0.1, excess_return_ratio: null, max_drawdown_ratio: 0,
      amount_change_ratio: null, reasons: ['benchmark_identity_invalid'],
    };
    expect(validateAnalysisJob({ ...pending, status: 'succeeded',
      result: { ...result, eligible_security_ids: ['A'], ranking: [row] },
    }, selection, job)).toBe(true);
  });
  it('closes paths before network and uses no-store same-origin CSRF POST', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ job_id: job, status: 'queued' })));
    vi.stubGlobal('fetch', fetcher);
    vi.stubGlobal('document', { cookie: '__Host-dragon-private-csrf=csrf-value' });
    await expect(analysisJobRequest('/api/ai/task')).rejects.toMatchObject({ status: 400 });
    expect(fetcher).not.toHaveBeenCalled();
    const command = createAnalysisCommand(selection, 'same-retry-key')!;
    await analysisJobRequest('/api/analysis/jobs', { method: 'POST', body: command });
    expect(fetcher).toHaveBeenCalledWith('/api/analysis/jobs', expect.objectContaining({
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-value' }),
      body: JSON.stringify(command),
    }));
  });
  it('times out JSON body at 15000ms even if fetch resolves first', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: () => new Promise(() => {}),
    })));
    const promise = analysisJobRequest(`/api/analysis/jobs/${job}`);
    const assertion = expect(promise).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(15000);
    await assertion;
  });
});
