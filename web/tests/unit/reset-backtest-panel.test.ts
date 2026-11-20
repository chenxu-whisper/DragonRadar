/**
 * Task 27 — Task 16 BacktestPanel + owner research API client contract (RED).
 *
 * Pins the owner-only research backtest workbench before any production code
 * exists:
 *
 *   TR-16.1  submit / poll / cancel / error-normalisation mock cases pass; an
 *            invalid configuration disables submission and exposes accessible
 *            error descriptions; the ablation region shows only the fixed
 *            disabled reason and never an AI-leg return number.
 *   TR-16.2  research-only + materialisation/version disclosure is always
 *            visible, every rejection has a Chinese explanation, and no
 *            promise/odds wording (FR-16) can appear in the panel sources.
 *
 * The vitest environment is node: interactive browser behaviour is owned by
 * the Task 17 Playwright probes; React components are exercised through
 * renderToStaticMarkup while the client/orchestrators run against fetch stubs.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  BacktestClientError,
  cancelBacktestRun,
  createBacktestCommand,
  getBacktestManifest,
  getBacktestRun,
  pollBacktestDecision,
  researchRequest,
  submitBacktest,
} from '../../src/features/research/backtest-client';
import type { BacktestRunRecord } from '../../src/features/research/backtest-client';
import {
  EMPTY_MANIFEST_RESOURCE,
  subscribeBacktestManifest,
  type BacktestManifestResource,
} from '../../src/features/research/manifest-resource';
import BacktestPanelDefault, {
  ABLATION_DISABLED_LABEL,
  ABLATION_DISABLED_REASON,
  BACKTEST_DISCLOSURE,
  BACKTEST_REJECTION_LABELS,
  COST_SENSITIVITY_TIERS,
  FACTOR_IDS,
  RESEARCH_BACKTEST_SCHEMA_VERSION,
  STRATEGY_ID,
  BacktestConfigForm,
  BacktestDisclosure,
  BacktestResultView,
  AblationSection,
  buildBacktestPayload,
  emptyBacktestDraft,
  hasBacktestErrors,
  backtestStatusLabel,
  validateBacktestDraft,
  validateManifestContext,
  type BacktestDraft,
  type BacktestManifestContext,
} from '../../src/features/research/BacktestPanel';

const h = createElement;
const read = (file: string): string =>
  readFileSync(resolve(process.cwd(), file), 'utf8');

const RUN_ID = '00000000-0000-4000-8000-0000000000aa';
const SNAPSHOT_ID = '11111111-1111-4111-8111-111111111111';
const TOPIC_ID = '22222222-2222-4222-8222-222222222222';
const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;

const manifest = (overrides: Partial<BacktestManifestContext> = {}): BacktestManifestContext => ({
  snapshot_id: SNAPSHOT_ID,
  domains: [
    { domain: 'market', snapshot_id: SNAPSHOT_ID },
    { domain: 'topic', snapshot_id: TOPIC_ID },
  ],
  versions: {
    adjustment: 'adjustment-v1',
    calendar: 'calendar-cn-v1',
    pool: 'topic-pool-v1',
  },
  ...overrides,
});

const recordFixture = (overrides: Partial<BacktestRunRecord> = {}): BacktestRunRecord => ({
  run_id: RUN_ID,
  status: 'queued',
  config_hash: 'a'.repeat(64),
  research_only: true,
  result: null,
  failure: null,
  ...overrides,
});

const jsonResponse = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Dedicated research client — transport, CSRF and path whitelist
// ---------------------------------------------------------------------------

describe('Task 16 research client transport contract', () => {
  it('uses same-origin cookies, no-store and CSRF only for writes', async () => {
    vi.stubGlobal('document', { cookie: '__Host-dragon-private-csrf=csrf-token' });
    const fetcher = vi.fn().mockImplementation(async () =>
      jsonResponse(recordFixture()));
    vi.stubGlobal('fetch', fetcher);

    await researchRequest(`/api/research/backtests/${RUN_ID}`);
    await researchRequest('/api/research/backtests', {
      method: 'POST',
      body: { idempotency_key: 'k' },
    });

    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
    });
    expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty('X-CSRF-Token');
    expect(fetcher.mock.calls[1][1].headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-CSRF-Token': 'csrf-token',
    });
  });

  it('rejects paths outside the research backtest namespace', async () => {
    vi.stubGlobal('fetch', vi.fn());
    for (const bad of [
      '/api/ai/daily-review',
      '/api/query/execute',
      '/api/research/backtests/../secrets',
      '/api/research/backtests?x=1',
      '/api/research/other',
    ]) {
      await expect(researchRequest(bad)).rejects.toBeInstanceOf(BacktestClientError);
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('times out an ignored transport and surfaces an uncertain 408', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const pending = researchRequest(`/api/research/backtests/${RUN_ID}`, {
      timeoutMs: 50,
    });
    const assertion = expect(pending).rejects.toMatchObject({
      status: 408,
      uncertain: true,
    });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;
  });

  it('passes a caller abort through as an AbortError without rewrapping', async () => {
    const controller = new AbortController();
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('回测请求已取消', 'AbortError')));
      })));
    const pending = researchRequest(`/api/research/backtests/${RUN_ID}`, {
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('maps a network failure to status 0 network_error', async () => {
    vi.stubGlobal('fetch', vi.fn(() => { throw new TypeError('offline'); }));
    await expect(researchRequest(`/api/research/backtests/${RUN_ID}`))
      .rejects.toMatchObject({ status: 0, code: 'network_error', uncertain: true });
  });

  it('rejects a malformed success body with a 502 protocol error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(['not', 'object'])));
    await expect(researchRequest(`/api/research/backtests/${RUN_ID}`))
      .rejects.toMatchObject({ status: 502, code: 'malformed_response' });
  });
});

describe('Task 16 research client error normalisation', () => {
  it.each([
    [400, 'INVALID_PARAM'],
    [403, 'FORBIDDEN'],
    [404, 'NOT_FOUND'],
    [409, 'VERSION_CONFLICT'],
    [503, 'STORAGE_UNAVAILABLE'],
  ])('maps %s onto the research route code family %s', async (status, code) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ code, message: 'x' }, { status })));
    await expect(researchRequest(`/api/research/backtests/${RUN_ID}`))
      .rejects.toMatchObject({ status, code, uncertain: false });
  });

  it('falls back to the status default code when the body code is missing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ message: 'no code' }, { status: 409 })));
    await expect(researchRequest(`/api/research/backtests/${RUN_ID}`))
      .rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT' });
  });

  it('locks the private session once before exposing a 401', async () => {
    const order: string[] = [];
    const dispatchEvent = vi.fn((event: Event) => { order.push(event.type); return true; });
    vi.stubGlobal('window', { dispatchEvent });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ code: 'SESSION_REVOKED' }, { status: 401 })));
    await researchRequest(`/api/research/backtests/${RUN_ID}`).catch(error => {
      order.push('rejected');
      expect(error).toMatchObject({ status: 401, code: 'SESSION_REVOKED' });
    });
    expect(order).toEqual(['private-session-lock', 'rejected']);
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
  });
});

describe('Task 16 submit / get / cancel validated calls', () => {
  it('submit posts the EX14 command body and validates the queued response', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse({ run_id: RUN_ID, status: 'queued' }));
    vi.stubGlobal('fetch', fetcher);
    const command = createBacktestCommand({ snapshot_id: SNAPSHOT_ID });
    expect(UUID.test(command.body.idempotency_key)).toBe(true);

    const response = await submitBacktest(command);
    expect(fetcher.mock.calls[0][0]).toBe('/api/research/backtests');
    expect(fetcher.mock.calls[0][1].method).toBe('POST');
    expect(response).toEqual({ run_id: RUN_ID, status: 'queued' });
  });

  it('rejects a submit response with unexpected envelope keys', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ run_id: RUN_ID, status: 'queued', extra: true })));
    const command = createBacktestCommand({ snapshot_id: SNAPSHOT_ID });
    await expect(submitBacktest(command)).rejects.toMatchObject({
      status: 502,
      code: 'malformed_response',
    });
  });

  it('get validates the full EX15 materialised envelope and never recomputes', async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(recordFixture()));
    vi.stubGlobal('fetch', fetcher);
    const record = await getBacktestRun(RUN_ID);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/research/backtests/${RUN_ID}`);
    expect(record).toMatchObject({ run_id: RUN_ID, status: 'queued', research_only: true });
    expect(record.result).toBeNull();
  });

  it('rejects an unknown run status or missing research_only flag', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ ...recordFixture(), status: 'exploding' })));
    await expect(getBacktestRun(RUN_ID)).rejects.toMatchObject({ status: 502 });
    vi.unstubAllGlobals();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ ...recordFixture(), research_only: false })));
    await expect(getBacktestRun(RUN_ID)).rejects.toMatchObject({ status: 502 });
  });

  it('cancel posts the idempotency key and returns the cancelled record envelope', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse(recordFixture({ status: 'cancelled' })));
    vi.stubGlobal('fetch', fetcher);
    const record = await cancelBacktestRun(RUN_ID);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/research/backtests/${RUN_ID}/cancel`);
    expect(fetcher.mock.calls[0][1].method).toBe('POST');
    expect(fetcher.mock.calls[0][1].body).toContain('idempotency_key');
    expect(record.status).toBe('cancelled');
    expect(record.result).toBeNull();
  });

  it('rejects a non-UUID run id for every run-scoped call', async () => {
    vi.stubGlobal('fetch', vi.fn());
    await expect(getBacktestRun('not-a-uuid')).rejects.toBeInstanceOf(BacktestClientError);
    await expect(cancelBacktestRun('not-a-uuid')).rejects.toBeInstanceOf(BacktestClientError);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('Task 16 polling decision', () => {
  it('polls queued and running runs on a one-second cadence', () => {
    expect(pollBacktestDecision(recordFixture({ status: 'queued' })))
      .toEqual({ poll: true, delayMs: 1000 });
    expect(pollBacktestDecision(recordFixture({ status: 'running' })))
      .toEqual({ poll: true, delayMs: 1000 });
  });

  it.each(['succeeded', 'failed', 'cancelled'] as const)(
    'stops polling at the terminal %s state',
    status => {
      expect(pollBacktestDecision(recordFixture({ status })))
        .toEqual({ poll: false, delayMs: 0 });
    },
  );
});

// ---------------------------------------------------------------------------
// Pure bounded configuration validation (TR-16.1 / TR-16.2)
// ---------------------------------------------------------------------------

describe('Task 16 draft validation bounds', () => {
  it('accepts the conservative golden draft and reports no errors', () => {
    expect(validateBacktestDraft(emptyBacktestDraft())).toEqual({});
    expect(hasBacktestErrors({})).toBe(false);
  });

  it.each([
    ['blank start date', { start_date: '' }, '回测周期'],
    ['impossible calendar start', { start_date: '2025-02-31' }, '回测周期'],
    ['span shorter than two days', { start_date: '2025-01-06', end_date: '2025-01-06' }, '回测周期'],
    ['span beyond 400 calendar days', { start_date: '2025-01-06', end_date: '2026-12-31' }, '回测周期'],
    ['blank top_n', { top_n: '' }, '持仓数量'],
    ['zero top_n', { top_n: '0' }, '持仓数量'],
    ['top_n above 100', { top_n: '101' }, '持仓数量'],
    ['decimal top_n', { top_n: '1.5' }, '持仓数量'],
    ['rebalance below 1', { rebalance_period: '0' }, '调仓周期'],
    ['rebalance above 60', { rebalance_period: '61' }, '调仓周期'],
    ['unknown factor id', { factor_id: 'FAC-X99' as BacktestDraft['factor_id'] }, '因子'],
    ['zero commission rate', { commission_rate: '0' }, '费率'],
    ['rate above 0.01', { stamp_tax_rate: '0.02' }, '费率'],
    ['non-numeric slippage', { slippage_rate: 'NaN' }, '费率'],
    ['zero min commission', { min_commission: '0' }, '最低佣金'],
    ['min commission above 100', { min_commission: '100.01' }, '最低佣金'],
    ['negative seed', { random_seed: '-1' }, '随机种子'],
    ['seed above uint32', { random_seed: '4294967296' }, '随机种子'],
  ])('rejects %s', (_name, patch: Partial<BacktestDraft>, fragment) => {
    const errors = validateBacktestDraft({ ...emptyBacktestDraft(), ...patch });
    expect(hasBacktestErrors(errors)).toBe(true);
    expect(Object.values(errors).join(' ')).toContain(fragment);
  });

  it('validates walk-forward fields only when the toggle is enabled', () => {
    const disabled = emptyBacktestDraft();
    disabled.walk_forward_enabled = false;
    disabled.folds = '';
    expect(validateBacktestDraft(disabled)).toEqual({});

    const enabled: BacktestDraft = { ...emptyBacktestDraft(), walk_forward_enabled: true };
    expect(validateBacktestDraft({ ...enabled, folds: '1' })).toHaveProperty('folds');
    expect(validateBacktestDraft({ ...enabled, folds: '11' })).toHaveProperty('folds');
    expect(validateBacktestDraft({ ...enabled, train_sessions: '0' })).toHaveProperty('train_sessions');
    expect(validateBacktestDraft({ ...enabled, test_sessions: '0' })).toHaveProperty('test_sessions');
    // train + folds * test must stay within the 250-session cap.
    const overCap = validateBacktestDraft({
      ...enabled,
      folds: '10',
      train_sessions: '100',
      test_sessions: '100',
    });
    expect(Object.values(overCap).join(' ')).toContain('折数');
  });

  it('accepts a walk-forward configuration inside the session cap', () => {
    const draft: BacktestDraft = {
      ...emptyBacktestDraft(),
      walk_forward_enabled: true,
      folds: '3',
      train_sessions: '40',
      test_sessions: '10',
    };
    expect(validateBacktestDraft(draft)).toEqual({});
  });
});

describe('Task 16 manifest context validation', () => {
  it('accepts a well-formed market-required manifest', () => {
    expect(validateManifestContext(manifest())).toBeNull();
  });

  it.each([
    ['missing snapshot id', manifest({ snapshot_id: '' })],
    ['non-uuid snapshot', manifest({ snapshot_id: 'nope' })],
    ['market domain absent', manifest({ domains: [{ domain: 'topic', snapshot_id: TOPIC_ID }] })],
    ['duplicate domains', manifest({
      domains: [
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
      ],
    })],
    ['more than three domains', manifest({
      domains: [
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
        { domain: 'topic', snapshot_id: TOPIC_ID },
        { domain: 'role', snapshot_id: '33333333-3333-4333-8333-333333333333' },
        { domain: 'role', snapshot_id: '44444444-4444-4444-8444-444444444444' },
      ],
    })],
    ['illegal version token', manifest({ versions: { adjustment: 'bad token!', calendar: 'c', pool: 'p' } })],
  ])('rejects %s', (_name, ctx) => {
    expect(validateManifestContext(ctx)).toContain('研究清单');
  });

  it('fail-closes on a null manifest', () => {
    expect(validateManifestContext(null)).toContain('研究清单');
  });
});

describe('Task 16 EX14 payload mapping', () => {
  it('maps the golden draft and context to the exact eleven-key EX14 body', () => {
    const payload = buildBacktestPayload(emptyBacktestDraft(), manifest(), 'submit-key-1');
    expect(Object.keys(payload).sort()).toEqual([
      'cost_model',
      'execution_model',
      'experiment',
      'idempotency_key',
      'input_manifests',
      'parameters',
      'period',
      'schema_version',
      'snapshot_id',
      'strategy_id',
      'walk_forward',
    ]);
    expect(payload.schema_version).toBe(RESEARCH_BACKTEST_SCHEMA_VERSION);
    expect(payload.strategy_id).toBe(STRATEGY_ID);
    expect(payload.snapshot_id).toBe(SNAPSHOT_ID);
    expect(payload.idempotency_key).toBe('submit-key-1');
    expect(payload.parameters).toEqual({
      factor_id: 'FAC-R01',
      top_n: 10,
      rebalance_period: 5,
      weighting: 'equal_weight',
    });
    expect(payload.period).toEqual({ start: '2025-01-06', end: '2025-06-27' });
    expect(payload.cost_model).toEqual({
      commission_rate: '0.0003',
      stamp_tax_rate: '0.001',
      slippage_model: 'fixed_rate',
      slippage_rate: '0.0005',
      stamp_tax_side: 'sell',
      min_commission: '5',
    });
    expect(payload.execution_model).toEqual({
      fill_timing: 'next_tradable_point',
      unknown_limit_policy: 'reject_untradable',
    });
    expect(payload.walk_forward).toBeNull();
    expect(payload.experiment).toEqual({ random_seed: 20250106, legs: ['baseline', 'ai_variant'] });
    expect(payload.input_manifests.domains).toHaveLength(2);
    expect(payload.input_manifests.versions).toEqual(manifest().versions);
  });

  it('emits a walk-forward spec and baseline-only legs when configured', () => {
    const draft: BacktestDraft = {
      ...emptyBacktestDraft(),
      walk_forward_enabled: true,
      folds: '3',
      train_sessions: '40',
      test_sessions: '10',
      include_ai_variant: false,
    };
    const payload = buildBacktestPayload(draft, manifest(), 'k');
    expect(payload.walk_forward).toEqual({ folds: 3, train_sessions: 40, test_sessions: 10 });
    expect(payload.experiment.legs).toEqual(['baseline']);
  });

  it('refuses to build a payload from an invalid draft or manifest', () => {
    expect(() => buildBacktestPayload({ ...emptyBacktestDraft(), top_n: '0' }, manifest(), 'k'))
      .toThrow('持仓数量');
    expect(() => buildBacktestPayload(emptyBacktestDraft(), null, 'k')).toThrow('研究清单');
  });

  it('freezes the strategy, factor set, cost tiers and schema constants', () => {
    expect(STRATEGY_ID).toBe('factor-cross-sectional-topn-v1');
    expect(FACTOR_IDS).toEqual(['FAC-R01', 'FAC-R02', 'FAC-R03', 'FAC-C01', 'FAC-C02']);
    expect(COST_SENSITIVITY_TIERS).toEqual([
      { tier: 'low', cost_multiplier: '0.5' },
      { tier: 'base', cost_multiplier: '1' },
      { tier: 'high', cost_multiplier: '2' },
    ]);
    expect(RESEARCH_BACKTEST_SCHEMA_VERSION).toBe('dragon-radar.research-backtest/v1');
  });
});

// ---------------------------------------------------------------------------
// SSR markup: disclosure, accessibility and the disabled-submit contract
// ---------------------------------------------------------------------------

describe('Task 16 SSR form and accessibility contract', () => {
  it('renders the research-only materialisation disclosure persistently', () => {
    const markup = renderToStaticMarkup(h(BacktestDisclosure));
    expect(markup).toContain(BACKTEST_DISCLOSURE);
    expect(markup).toContain('data-testid="backtest-disclosure"');
    expect(markup).toContain(RESEARCH_BACKTEST_SCHEMA_VERSION);
  });

  it('wires aria-invalid and aria-describedby onto every errored field', () => {
    const draft: BacktestDraft = {
      ...emptyBacktestDraft(),
      top_n: '0',
      end_date: '2025-01-06',
      commission_rate: '0',
    };
    const errors = validateBacktestDraft(draft);
    const markup = renderToStaticMarkup(h(BacktestConfigForm, {
      draft,
      errors,
      busy: false,
      manifestReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    for (const field of ['top_n', 'end_date', 'commission_rate']) {
      expect(markup).toContain(`aria-describedby="bt-${field}-error"`);
      expect(markup).toContain(`id="bt-${field}-error"`);
    }
    expect(markup).toContain('aria-invalid="true"');
  });

  it('keeps clean fields free of aria-invalid and enables submit for a valid draft', () => {
    const markup = renderToStaticMarkup(h(BacktestConfigForm, {
      draft: emptyBacktestDraft(),
      errors: {},
      busy: false,
      manifestReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    expect(markup).not.toContain('aria-invalid');
    expect(markup).not.toMatch(/<button[^>]*disabled[^>]*>提交回测<\/button>/);
  });

  it('disables submission for an invalid draft and announces the accessible reason', () => {
    const draft = { ...emptyBacktestDraft(), top_n: '0' };
    const errors = validateBacktestDraft(draft);
    const markup = renderToStaticMarkup(h(BacktestConfigForm, {
      draft,
      errors,
      busy: false,
      manifestReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    expect(markup).toMatch(/<button[^>]*disabled[^>]*>提交回测<\/button>/);
    expect(markup).toContain('持仓数量');
  });

  it('disables submission while busy or when the manifest context is missing', () => {
    const busy = renderToStaticMarkup(h(BacktestConfigForm, {
      draft: emptyBacktestDraft(),
      errors: {},
      busy: true,
      manifestReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    expect(busy).toMatch(/<button[^>]*disabled[^>]*>提交回测<\/button>/);

    const noManifest = renderToStaticMarkup(h(BacktestConfigForm, {
      draft: emptyBacktestDraft(),
      errors: {},
      busy: false,
      manifestReady: false,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    expect(noManifest).toMatch(/<button[^>]*disabled[^>]*>提交回测<\/button>/);
    expect(noManifest).toContain('研究清单');
  });

  it('exposes the bounded fields with Chinese labels including three cost tiers', () => {
    const markup = renderToStaticMarkup(h(BacktestConfigForm, {
      draft: emptyBacktestDraft(),
      errors: {},
      busy: false,
      manifestReady: true,
      onDraft: () => undefined,
      onSubmit: () => undefined,
      onCancel: () => undefined,
    }));
    for (const label of ['回测周期', '持仓数量', '调仓周期', '佣金费率', '印花税费率', '滑点费率', '最低佣金', '滚动折数', '随机种子']) {
      expect(markup).toContain(label);
    }
  });
});

// ---------------------------------------------------------------------------
// SSR result rendering and the ablation disabled-only contract (TR-16.1)
// ---------------------------------------------------------------------------

const metricsFixture = {
  initial_equity: '1000000.00',
  final_equity: '1125000.00',
  total_return: '0.1250',
  benchmark_return: '0.0500',
  excess_return: '0.0750',
  max_drawdown: '0.0820',
  turnover_rate: '3.4000',
  total_commission: '1020.00',
  total_stamp_tax: '2040.00',
  total_cost: '3060.00',
  unfillable_count_ratio: '0.0100',
  unfillable_amount_ratio: '0.0200',
};

const resultFixture = {
  schema_version: RESEARCH_BACKTEST_SCHEMA_VERSION,
  research_only: true,
  config_hash: 'a'.repeat(64),
  snapshot_id: SNAPSHOT_ID,
  manifests: manifest(),
  versions: { strategy_id: STRATEGY_ID },
  metrics: metricsFixture,
  equity: [
    { trade_date: '2025-01-06', equity: '1000000.00' },
    { trade_date: '2025-01-07', equity: '1001000.00' },
  ],
  trades: [{
    security_id: '000001',
    side: 'buy',
    quantity: 100,
    signal_date: '2025-01-06',
    trade_date: '2025-01-07',
    raw_close: '10.00',
    fill_price: '10.0050',
    gross_amount: '1000.50',
    commission: '5.00',
    stamp_tax: '0.00',
    net_cash_delta: '-1005.50',
  }],
  rejections: [{
    security_id: '000002',
    side: 'sell',
    quantity: 200,
    signal_date: '2025-01-06',
    trade_date: '2025-01-07',
    reason: 't1_sell_blocked',
  }],
  quality: [{
    trade_date: '2025-01-07',
    factor_coverage: { expected: 10, valid: 8, excluded: 1, missing: 1 },
    events: [],
  }],
  folds: null,
  walk_forward: null,
  baseline: {
    status: 'completed',
    metrics: metricsFixture,
    cost_sensitivity: [
      { tier: 'low', cost_multiplier: '0.5', metrics: { total_return: '0.1300' } },
      { tier: 'base', cost_multiplier: '1', metrics: { total_return: '0.1250' } },
      { tier: 'high', cost_multiplier: '2', metrics: { total_return: '0.1100' } },
    ],
  },
  ablation: {
    schema_version: 'dragon-radar.research-ablation/v1',
    research_only: true,
    baseline: { leg: 'baseline', status: 'completed', metrics: metricsFixture, reason: null },
    ai_variant: {
      leg: 'ai_variant',
      status: 'disabled',
      metrics: null,
      reason: ABLATION_DISABLED_REASON,
    },
  },
};

const succeededRecord = recordFixture({
  status: 'succeeded',
  result: resultFixture as unknown as BacktestRunRecord['result'],
});

describe('Task 16 SSR result view contract', () => {
  it('renders metrics, equity, trades, rejections, factor health and cost sensitivity', () => {
    const markup = renderToStaticMarkup(h(BacktestResultView, { record: succeededRecord }));
    expect(markup).toContain('回测结果');
    expect(markup).toContain('1125000.00');
    expect(markup).toContain('0.1250');
    expect(markup).toContain('净值曲线');
    expect(markup).toContain('成交流水');
    expect(markup).toContain('10.0050');
    expect(markup).toContain(BACKTEST_REJECTION_LABELS.t1_sell_blocked);
    expect(markup).not.toContain('t1_sell_blocked');
    expect(markup).toContain('因子体检');
    expect(markup).toContain('成本敏感性');
    expect(markup).toContain('low');
    expect(markup).toContain('base');
    expect(markup).toContain('high');
    expect(markup).toContain('0.1300');
  });

  it('renders walk-forward folds when the run carries them', () => {
    const withFolds = {
      ...resultFixture,
      folds: [{
        fold_index: 0,
        train_start: '2025-01-06',
        train_end: '2025-03-06',
        test_start: '2025-03-07',
        test_end: '2025-03-27',
        metrics: metricsFixture,
        equity: [],
        trades: [],
        rejections: [],
        quality: [],
      }],
    };
    const markup = renderToStaticMarkup(h(BacktestResultView, {
      record: recordFixture({ status: 'succeeded', result: withFolds as never }),
    }));
    expect(markup).toContain('滚动折数');
    expect(markup).toContain('0');
  });

  it('shows an empty-state message when a terminal run has no materialised result', () => {
    const markup = renderToStaticMarkup(h(BacktestResultView, {
      record: recordFixture({ status: 'failed', failure: { code: 'engine_failed' } }),
    }));
    expect(markup).toContain('暂无物化结果');
  });

  it('maps every run status to a Chinese label', () => {
    expect(backtestStatusLabel('queued')).toBe('排队中');
    expect(backtestStatusLabel('running')).toBe('运行中');
    expect(backtestStatusLabel('succeeded')).toBe('已完成');
    expect(backtestStatusLabel('failed')).toBe('已失败');
    expect(backtestStatusLabel('cancelled')).toBe('已取消');
  });

  it('provides an exhaustive Chinese label for every rejection reason', () => {
    const reasons = [
      'suspended',
      'limit_up_blocked_buy',
      'limit_down_blocked_sell',
      't1_sell_blocked',
      'insufficient_cash',
      'insufficient_position',
      'delisted',
      'no_tradable_price',
      'unknown_limit_untradable',
    ] as const;
    for (const reason of reasons) {
      const label = BACKTEST_REJECTION_LABELS[reason];
      expect(/[一-鿿]/.test(label)).toBe(true);
      expect(label).not.toBe(reason);
    }
  });
});

describe('Task 16 ablation disabled-only contract', () => {
  it('renders the fixed disabled reason on the AI leg and no AI metrics', () => {
    const markup = renderToStaticMarkup(h(AblationSection, { ablation: resultFixture.ablation }));
    expect(markup).toContain(ABLATION_DISABLED_REASON);
    expect(markup).toContain(ABLATION_DISABLED_LABEL);
    const aiRegion = markup.split('data-testid="ablation-ai-variant"')[1] ?? '';
    expect(aiRegion).not.toContain('0.1250');
    expect(aiRegion).not.toContain('1125000.00');
    expect(aiRegion).not.toContain('total_return');
  });

  it('still renders the completed baseline leg metrics in the ablation frame', () => {
    const markup = renderToStaticMarkup(h(AblationSection, { ablation: resultFixture.ablation }));
    const baselineRegion = markup.split('data-testid="ablation-baseline"')[1] ?? '';
    expect(baselineRegion).toContain('0.1250');
    expect(markup).toContain('baseline');
  });

  it('renders nothing when the run did not produce an ablation frame', () => {
    expect(renderToStaticMarkup(h(AblationSection, { ablation: null }))).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Mount, isolation and copy-compliance source contracts
// ---------------------------------------------------------------------------

describe('Task 16 mount and isolation contract', () => {
  it('env-folds the panel as a lazy reset-only export in the Analysis page module', () => {
    const source = read('src/pages/Analysis.tsx');
    expect(source).toContain("import('../features/research/OwnerBacktestWorkbench')");
    expect(source).not.toContain("from '../research/BacktestPanel'");
    expect(source).toContain("VITE_RESET_WORKBENCH === '1'");
    expect(source).toContain("VITE_PUBLIC_SHARING !== '1'");
    expect(source).toContain('ResetBacktestPanel');
  });

  it('mounts the panel in the App workbench only under the reset analysis view', () => {
    const source = read('src/App.tsx');
    expect(source).toContain('ResetBacktestPanel');
    expect(source).toContain("state.view === 'analysis'");
  });

  it('keeps the client on the research whitelist and the owner CSRF cookie', () => {
    const source = read('src/features/research/backtest-client.ts');
    expect(source).toContain('/api/research/backtests');
    expect(source).toContain('__Host-dragon-private-csrf');
    expect(source).not.toContain('/api/ai/');
    expect(source).not.toContain('/api/query/');
  });

  it('carries the disclosure in the default container render path', () => {
    const markup = renderToStaticMarkup(h(BacktestPanelDefault, { manifest: manifest() }));
    expect(markup).toContain('研究回测');
    expect(markup).toContain(BACKTEST_DISCLOSURE);
    expect(markup).toContain('role="status"');
  });

  it.each([
    'src/features/research/BacktestPanel.tsx',
    'src/features/research/backtest-panel.css',
  ])('bans promise/odds wording from %s', file => {
    const source = read(file);
    for (const banned of ['安全再涨', '无风险', '预期胜率', '上涨概率', '胜率', '必涨', '稳赚', '预测', '包赚']) {
      expect(source, `${file} must not contain ${banned}`).not.toContain(banned);
    }
  });
});

// ---------------------------------------------------------------------------
// Task 17 — owner input-manifest endpoint client (GET /manifests/{snapshot})
// ---------------------------------------------------------------------------

describe('Task 17 manifest client transport contract', () => {
  it('GETs the owner manifest with read-only transport and validates the three-key envelope', async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(manifest()));
    vi.stubGlobal('fetch', fetcher);

    const context = await getBacktestManifest(SNAPSHOT_ID);

    expect(fetcher.mock.calls[0][0]).toBe(`/api/research/manifests/${SNAPSHOT_ID}`);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
    });
    expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty('X-CSRF-Token');
    expect(context).toEqual(manifest());
  });

  it('accepts a market-only manifest domain list', async () => {
    const marketOnly = manifest({
      domains: [{ domain: 'market', snapshot_id: SNAPSHOT_ID }],
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(marketOnly)));
    await expect(getBacktestManifest(SNAPSHOT_ID)).resolves.toEqual(marketOnly);
  });

  it('rejects a non-UUID snapshot id locally without issuing a request', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(getBacktestManifest('not-a-uuid')).rejects.toMatchObject({
      status: 400,
      code: 'INVALID_PARAM',
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('keeps manifest reads inside the research whitelist and refuses query strings or siblings', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    for (const bad of [
      `/api/research/manifests/${SNAPSHOT_ID}?expand=1`,
      '/api/research/manifests',
      '/api/research/manifests/../secrets',
      '/api/research/other/1',
      '/api/research/backtests/../manifests/x',
    ]) {
      await expect(researchRequest(bad)).rejects.toBeInstanceOf(BacktestClientError);
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([
    ['extra top-level key', { ...manifest(), stale: true }],
    ['missing versions', { snapshot_id: SNAPSHOT_ID, domains: manifest().domains }],
    ['missing market domain', { ...manifest(), domains: [{ domain: 'topic', snapshot_id: TOPIC_ID }] }],
    ['duplicate domains', {
      ...manifest(),
      domains: [
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
      ],
    }],
    ['four domains', {
      ...manifest(),
      domains: [
        { domain: 'market', snapshot_id: SNAPSHOT_ID },
        { domain: 'topic', snapshot_id: TOPIC_ID },
        { domain: 'role', snapshot_id: '33333333-3333-4333-8333-333333333333' },
        { domain: 'role', snapshot_id: '44444444-4444-4444-8444-444444444444' },
      ],
    }],
    ['unknown domain name', { ...manifest(), domains: [{ domain: 'news', snapshot_id: SNAPSHOT_ID }] }],
    ['non-uuid domain snapshot', { ...manifest(), domains: [{ domain: 'market', snapshot_id: 'nope' }] }],
    ['domain entry with an extra key', {
      ...manifest(),
      domains: [{ domain: 'market', snapshot_id: SNAPSHOT_ID, extra: 1 }],
    }],
    ['illegal version token', {
      ...manifest(),
      versions: { adjustment: 'bad token!', calendar: 'c', pool: 'p' },
    }],
    ['overlong version token', {
      ...manifest(),
      versions: { adjustment: 'a'.repeat(101), calendar: 'c', pool: 'p' },
    }],
    ['missing version key', {
      ...manifest(),
      versions: { adjustment: 'a', calendar: 'c' } as BacktestManifestContext['versions'],
    }],
    ['domains not an array', { ...manifest(), domains: {} }],
    ['non-uuid top-level snapshot id', { ...manifest(), snapshot_id: 'nope' }],
  ])('rejects %s with a 502 malformed_response', async (_name, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(body)));
    await expect(getBacktestManifest(SNAPSHOT_ID)).rejects.toMatchObject({
      status: 502,
      code: 'malformed_response',
    });
  });

  it('locks the private session once on a 401 from the manifest endpoint', async () => {
    const dispatchEvent = vi.fn(() => true);
    vi.stubGlobal('window', { dispatchEvent });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ code: 'SESSION_REVOKED' }, { status: 401 })));
    await expect(getBacktestManifest(SNAPSHOT_ID))
      .rejects.toMatchObject({ status: 401, code: 'SESSION_REVOKED' });
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
  });

  it('maps a 404 manifest response onto the research route code family', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ code: 'NOT_FOUND', message: 'Resource not found' }, { status: 404 })));
    await expect(getBacktestManifest(SNAPSHOT_ID))
      .rejects.toMatchObject({ status: 404, code: 'NOT_FOUND' });
  });
});

describe('Task 17 manifest resource fail-closed lifecycle', () => {
  it('pushes a loading resource first and then the validated manifest', async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(manifest()));
    vi.stubGlobal('fetch', fetcher);
    const seen: BacktestManifestResource[] = [];

    subscribeBacktestManifest(SNAPSHOT_ID, next => { seen.push(next); });

    await vi.waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[0]).toEqual({ manifest: null, loading: true });
    expect(seen[1]).toEqual({ manifest: manifest(), loading: false });
    expect(fetcher.mock.calls[0][0]).toBe(`/api/research/manifests/${SNAPSHOT_ID}`);
  });

  it('fails closed to the empty resource when the endpoint refuses storage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ code: 'STORAGE_UNAVAILABLE' }, { status: 503 })));
    const seen: BacktestManifestResource[] = [];

    subscribeBacktestManifest(SNAPSHOT_ID, next => { seen.push(next); });

    await vi.waitFor(() => expect(seen.at(-1)).toEqual(EMPTY_MANIFEST_RESOURCE));
    expect(seen[0]).toEqual({ manifest: null, loading: true });
  });

  it('fails closed on a malformed manifest body instead of trusting partial data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ snapshot_id: SNAPSHOT_ID, domains: [], versions: {} })));
    const seen: BacktestManifestResource[] = [];

    subscribeBacktestManifest(SNAPSHOT_ID, next => { seen.push(next); });

    await vi.waitFor(() => expect(seen.at(-1)).toEqual(EMPTY_MANIFEST_RESOURCE));
  });

  it('cancels an in-flight load and never delivers a late manifest', async () => {
    const fetcher = vi.fn((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('回测请求已取消', 'AbortError')));
      }));
    vi.stubGlobal('fetch', fetcher);
    const seen: BacktestManifestResource[] = [];

    const unsubscribe = subscribeBacktestManifest(SNAPSHOT_ID, next => { seen.push(next); });
    unsubscribe();
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(seen).toEqual([{ manifest: null, loading: true }]);
    const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(true);
  });
});

describe('Task 17 App manifest wiring and fold isolation contract', () => {
  it('replaces the hardcoded null manifest with a dynamically imported manifest subscription', () => {
    const source = read('src/App.tsx');
    expect(source).not.toContain('manifest={null}');
    expect(source).not.toContain('subscribeBacktestManifest');
    expect(source).not.toContain('manifestResource');
    expect(source).toContain("snapshotId={snapshot?.snapshot_id || ''}");
    expect(source).toContain('ResetBacktestPanel && ownerResearchEnabled');
    const ownerSource = read('src/features/research/OwnerBacktestWorkbench.tsx');
    expect(ownerSource).toContain('subscribeBacktestManifest');
    expect(ownerSource).toContain('manifest={manifestResource.manifest}');
    expect(source).toContain('ResetBacktestPanel');
    expect(source).toContain("state.view === 'analysis'");
    // Runtime research dependencies must stay dynamically imported so the
    // default build folds them away; only type-only static imports are allowed.
    expect(source).not.toMatch(/^import (?!type ).*from '\.\/features\/research/m);
  });

  it('re-exports the manifest context type from the panel module surface', () => {
    const source = read('src/features/research/BacktestPanel.tsx');
    expect(source).toContain("from './backtest-client'");
    expect(source).toContain('export type { BacktestManifestContext };');
  });

  it('keeps the research client whitelist limited to backtests and manifests', () => {
    const source = read('src/features/research/backtest-client.ts');
    expect(source).toContain('/api/research/backtests');
    expect(source).toContain('/api/research/manifests/');
    expect(source).not.toContain('/api/ai/');
    expect(source).not.toContain('/api/query/');
  });
});
