/**
 * Task 27 — Task 16 owner-only research backtest workbench panel.
 *
 * Research-only, materialisation-bound surface. The panel never fabricates
 * manifest UUIDs: a missing or invalid BacktestManifestContext fails closed
 * and keeps submission disabled until the owning publication supplies real
 * manifests. Pure validation / mapping helpers and the SSR-safe subcomponents
 * are exported independently so vitest can pin them headlessly in node; the
 * interactive submit / poll / cancel orchestration lives in the default
 * container and is exercised by the Task 17 Playwright probes.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BacktestClientError,
  cancelBacktestRun,
  createBacktestCommand,
  getBacktestRun,
  getResearchCapabilities,
  pollBacktestDecision,
  submitBacktest,
  type BacktestManifestContext,
  type BacktestRunRecord,
  type BacktestRunStatus,
  type ResearchCapabilities,
} from './backtest-client';
import './backtest-panel.css';
import { ResearchEvidence } from './ResearchEvidence';
import { validateResearchResult } from './result-validation';
import { asciiCanonical, normalizedConfig } from './config-canonical';

export const STRATEGY_ID = 'factor-cross-sectional-topn-v1';
export const RESEARCH_BACKTEST_SCHEMA_VERSION = 'dragon-radar.research-backtest/v1';
export const FACTOR_IDS = ['FAC-R01', 'FAC-R02', 'FAC-R03', 'FAC-C01', 'FAC-C02'] as const;
export const COST_SENSITIVITY_TIERS = [
  { tier: 'low', cost_multiplier: '0.5' },
  { tier: 'base', cost_multiplier: '1' },
  { tier: 'high', cost_multiplier: '2' },
] as const;

export const ABLATION_DISABLED_REASON = 'real_evaluation_inputs_unavailable';
export const ABLATION_DISABLED_LABEL = 'AI 对照腿已停用：真实评估样本尚未通过独立评估闸门，暂不产出任何指标。';

/** FR-16 disclosure: every panel state carries the research/materialisation scope. */
export const BACKTEST_DISCLOSURE = '研究回测：结果基于历史题材与行情快照物化生成，仅用于研究复盘，不代表未来表现，不构成投资建议。';

/** Closed Chinese explanation set; every backend rejection reason is covered. */
export const BACKTEST_REJECTION_LABELS = {
  suspended: '证券停牌，当前交易日不可撮合',
  limit_up_blocked_buy: '涨停封板，买入委托无法成交',
  limit_down_blocked_sell: '跌停封板，卖出委托无法成交',
  t1_sell_blocked: 'T+1 冻结：当日买入份额须次一交易日解冻后才可卖出',
  insufficient_cash: '回测资金不足，无法完成本次买入',
  insufficient_position: '持仓数量不足，无法卖出对应份额',
  delisted: '证券处于退市或摘牌状态，不可交易',
  no_tradable_price: '无有效成交价格：收盘价缺失或非正',
  unknown_limit_untradable: '涨跌停元数据不完整，按保守规则拒单',
} as const;

export type BacktestRejectionReason = keyof typeof BACKTEST_REJECTION_LABELS;

export interface BacktestDraft {
  start_date: string;
  end_date: string;
  top_n: string;
  rebalance_period: string;
  factor_id: string;
  weighting: 'equal_weight';
  commission_rate: string;
  stamp_tax_rate: string;
  slippage_rate: string;
  min_commission: string;
  slippage_model: 'fixed_rate';
  stamp_tax_side: 'sell';
  random_seed: string;
  include_ai_variant: boolean;
  walk_forward_enabled: boolean;
  folds: string;
  train_sessions: string;
  test_sessions: string;
}

// The manifest context type is owned by the transport client (single source of
// truth for the endpoint envelope) and re-exported to preserve the panel's
// existing import surface.
export type { BacktestManifestContext };

export type BacktestErrors = Partial<Record<string, string>>;

const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
const VERSION_TOKEN = /^[A-Za-z0-9_.:-]+$/;
const MAX_PERIOD_CALENDAR_DAYS = 400;
const MIN_PERIOD_SPAN_DAYS = 2;
const MAX_PERIOD_SESSIONS = 250;
const MAX_FEE = 0.01;
const MAX_MIN_COMMISSION = 100;
const UINT32_MAX = 4_294_967_295;

/** Conservative golden draft; mirrors the backend frozen cost assumptions. */
export function emptyBacktestDraft(): BacktestDraft {
  return {
    start_date: '2025-01-06',
    end_date: '2025-06-27',
    top_n: '10',
    rebalance_period: '5',
    factor_id: 'FAC-R01',
    weighting: 'equal_weight',
    commission_rate: '0.0003',
    stamp_tax_rate: '0.001',
    slippage_rate: '0.0005',
    min_commission: '5',
    slippage_model: 'fixed_rate',
    stamp_tax_side: 'sell',
    random_seed: '20250106',
    include_ai_variant: false,
    walk_forward_enabled: false,
    folds: '',
    train_sessions: '',
    test_sessions: '',
  };
}

export function hasBacktestErrors(errors: BacktestErrors): boolean {
  return Object.keys(errors).length > 0;
}

function isUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID.test(value);
}

function isCalendarDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function boundedInteger(value: string, min: number, max: number): boolean {
  if (!/^\d+$/.test(value.trim())) return false;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= min && parsed <= max;
}

function boundedRate(value: string): boolean {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 && parsed <= MAX_FEE;
}

function boundedFee(value: string): boolean {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 && parsed <= MAX_MIN_COMMISSION;
}

/** Bounded, conservative validation; every message carries a Chinese field fragment. */
export function validateBacktestDraft(draft: BacktestDraft): BacktestErrors {
  const errors: BacktestErrors = {};

  if (!isCalendarDate(draft.start_date) || !isCalendarDate(draft.end_date)) {
    errors.start_date = '回测周期须为有效日历日期';
    errors.end_date = '回测周期须为有效日历日期';
  } else {
    const spanDays = (
      Date.parse(`${draft.end_date}T00:00:00Z`)
      - Date.parse(`${draft.start_date}T00:00:00Z`)
    ) / 86_400_000;
    if (spanDays < MIN_PERIOD_SPAN_DAYS || spanDays > MAX_PERIOD_CALENDAR_DAYS) {
      errors.end_date = `回测周期跨度须为 ${MIN_PERIOD_SPAN_DAYS} 至 ${MAX_PERIOD_CALENDAR_DAYS} 个日历日`;
    }
  }

  if (!boundedInteger(draft.top_n, 1, 100)) {
    errors.top_n = '持仓数量须为 1 至 100 的整数';
  }
  if (!boundedInteger(draft.rebalance_period, 1, 60)) {
    errors.rebalance_period = '调仓周期须为 1 至 60 的整数';
  }
  if (!FACTOR_IDS.includes(draft.factor_id as (typeof FACTOR_IDS)[number])) {
    errors.factor_id = '因子须取自冻结的研究因子集合';
  }
  if (!boundedRate(draft.commission_rate)) {
    errors.commission_rate = '佣金费率须为大于 0 且不超过 0.01 的数字';
  }
  if (!boundedRate(draft.stamp_tax_rate)) {
    errors.stamp_tax_rate = '印花税费率须为大于 0 且不超过 0.01 的数字';
  }
  if (!boundedRate(draft.slippage_rate)) {
    errors.slippage_rate = '滑点费率须为大于 0 且不超过 0.01 的数字';
  }
  if (!boundedFee(draft.min_commission)) {
    errors.min_commission = '最低佣金须为大于 0 且不超过 100 的数字';
  }
  if (!boundedInteger(draft.random_seed, 0, UINT32_MAX)) {
    errors.random_seed = '随机种子须为 0 至 4294967295 的整数';
  }

  if (draft.walk_forward_enabled) {
    if (!boundedInteger(draft.folds, 2, 10)) {
      errors.folds = '滚动折数须为 2 至 10 的整数';
    }
    if (!boundedInteger(draft.train_sessions, 1, MAX_PERIOD_SESSIONS)) {
      errors.train_sessions = '训练窗口须为不小于 1 的整数';
    }
    if (!boundedInteger(draft.test_sessions, 1, MAX_PERIOD_SESSIONS)) {
      errors.test_sessions = '测试窗口须为不小于 1 的整数';
    }
    if (!errors.folds && !errors.train_sessions && !errors.test_sessions) {
      const required = Number(draft.train_sessions)
        + Number(draft.folds) * Number(draft.test_sessions);
      if (required > MAX_PERIOD_SESSIONS) {
        errors.folds = `滚动折数组合超出 ${MAX_PERIOD_SESSIONS} 个交易日上限（当前需要 ${required} 个交易日）`;
      }
    }
  }

  return errors;
}

/** Fail-closed manifest guard; the frontend never invents manifest UUIDs. */
export function validateManifestContext(
  context: BacktestManifestContext | null,
): string | null {
  const prefix = '研究清单校验失败：';
  if (!context || !isUuid(context.snapshot_id)) {
    return `${prefix}快照标识缺失或不是有效 UUID`;
  }
  const domains = Array.isArray(context.domains) ? context.domains : [];
  if (domains.length < 1 || domains.length > 3) {
    return `${prefix}领域清单须包含 1 至 3 个领域`;
  }
  const names = domains.map(entry => entry?.domain);
  if (new Set(names).size !== names.length) {
    return `${prefix}领域清单存在重复领域`;
  }
  if (!names.includes('market')) {
    return `${prefix}领域清单必须包含 market 行情领域`;
  }
  if (domains.some(entry => !isUuid(entry?.snapshot_id))) {
    return `${prefix}领域快照标识不是有效 UUID`;
  }
  const versions = context.versions;
  const tokens = versions
    ? [versions.adjustment, versions.calendar, versions.pool]
    : [];
  const tokenValid = tokens.every(token => typeof token === 'string'
    && token.length >= 1
    && token.length <= 100
    && VERSION_TOKEN.test(token));
  if (!versions || !tokenValid) {
    return `${prefix}版本令牌缺失、超长或含有非法字符`;
  }
  return null;
}

type Ex14Payload = {
  schema_version: string;
  snapshot_id: string;
  strategy_id: string;
  parameters: {
    factor_id: string;
    top_n: number;
    rebalance_period: number;
    weighting: 'equal_weight';
  };
  period: { start: string; end: string };
  cost_model: {
    commission_rate: string;
    stamp_tax_rate: string;
    slippage_model: 'fixed_rate';
    slippage_rate: string;
    stamp_tax_side: 'sell';
    min_commission: string;
  };
  execution_model: {
    fill_timing: 'next_tradable_point';
    unknown_limit_policy: 'reject_untradable';
  };
  walk_forward: { folds: number; train_sessions: number; test_sessions: number } | null;
  experiment: { random_seed: number; legs: Array<'baseline' | 'ai_variant'> };
  input_manifests: {
    domains: BacktestManifestContext['domains'];
    versions: BacktestManifestContext['versions'];
  };
  idempotency_key: string;
};

/** Map a validated golden draft + manifest onto the exact eleven-key EX14 body. */
export function buildBacktestPayload(
  draft: BacktestDraft,
  manifest: BacktestManifestContext | null,
  key: string,
): Ex14Payload {
  const draftErrors = validateBacktestDraft(draft);
  if (hasBacktestErrors(draftErrors)) {
    throw new Error(Object.values(draftErrors).join(' '));
  }
  const manifestError = validateManifestContext(manifest);
  if (manifestError || !manifest) throw new Error(manifestError ?? '研究清单不可用');
  return {
    schema_version: RESEARCH_BACKTEST_SCHEMA_VERSION,
    snapshot_id: manifest.snapshot_id,
    strategy_id: STRATEGY_ID,
    parameters: {
      factor_id: draft.factor_id,
      top_n: Number(draft.top_n),
      rebalance_period: Number(draft.rebalance_period),
      weighting: 'equal_weight',
    },
    period: { start: draft.start_date, end: draft.end_date },
    cost_model: {
      commission_rate: draft.commission_rate,
      stamp_tax_rate: draft.stamp_tax_rate,
      slippage_model: 'fixed_rate',
      slippage_rate: draft.slippage_rate,
      stamp_tax_side: 'sell',
      min_commission: draft.min_commission,
    },
    execution_model: {
      fill_timing: 'next_tradable_point',
      unknown_limit_policy: 'reject_untradable',
    },
    walk_forward: draft.walk_forward_enabled
      ? {
          folds: Number(draft.folds),
          train_sessions: Number(draft.train_sessions),
          test_sessions: Number(draft.test_sessions),
        }
      : null,
    experiment: {
      random_seed: Number(draft.random_seed),
      legs: draft.include_ai_variant ? ['baseline', 'ai_variant'] : ['baseline'],
    },
    input_manifests: {
      domains: manifest.domains,
      versions: manifest.versions,
    },
    idempotency_key: key,
  };
}

const STATUS_LABELS: Record<BacktestRunStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '已失败',
  cancelled: '已取消',
};

export function backtestStatusLabel(status: BacktestRunStatus): string {
  return STATUS_LABELS[status];
}

function errorAttributes(field: string, message?: string) {
  return message
    ? { 'aria-invalid': true as const, 'aria-describedby': `bt-${field}-error` }
    : {};
}

function FieldError({ field, message }: { field: string; message?: string }) {
  if (!message) return null;
  return <span className="field-error" id={`bt-${field}-error`} role="alert">{message}</span>;
}

export function BacktestDisclosure() {
  return <p className="backtest-disclosure" data-testid="backtest-disclosure">
    {BACKTEST_DISCLOSURE}
    <span className="backtest-schema">物化口径版本：{RESEARCH_BACKTEST_SCHEMA_VERSION}</span>
  </p>;
}

interface BacktestConfigFormProps {
  draft: BacktestDraft;
  errors: BacktestErrors;
  busy: boolean;
  manifestReady: boolean;
  onDraft: (draft: BacktestDraft) => void;
  onSubmit: () => void;
  onCancel: () => void;
  capabilities?: ResearchCapabilities | null;
}

export function BacktestConfigForm({
  draft,
  errors,
  busy,
  manifestReady,
  onDraft,
  onSubmit,
  onCancel,
  capabilities,
}: BacktestConfigFormProps) {
  const factorReady = capabilities?.factors.some(item => item.id === draft.factor_id && item.status === 'ready') === true;
  const submitDisabled = busy || !manifestReady || !factorReady || hasBacktestErrors(errors);
  const patch = (partial: Partial<BacktestDraft>) => onDraft({ ...draft, ...partial });
  return <form
    className="backtest-form"
    aria-label="研究回测配置"
    onSubmit={event => { event.preventDefault(); onSubmit(); }}
  >
    <label>回测周期（开始）
      <input
        type="date"
        name="start_date"
        value={draft.start_date}
        onChange={event => patch({ start_date: event.target.value })}
        {...errorAttributes('start_date', errors.start_date)}
      />
    </label>
    <FieldError field="start_date" message={errors.start_date} />
    <label>回测周期（结束）
      <input
        type="date"
        name="end_date"
        value={draft.end_date}
        onChange={event => patch({ end_date: event.target.value })}
        {...errorAttributes('end_date', errors.end_date)}
      />
    </label>
    <FieldError field="end_date" message={errors.end_date} />
    <label>持仓数量
      <input
        name="top_n"
        inputMode="numeric"
        value={draft.top_n}
        onChange={event => patch({ top_n: event.target.value })}
        {...errorAttributes('top_n', errors.top_n)}
      />
    </label>
    <FieldError field="top_n" message={errors.top_n} />
    <label>调仓周期
      <input
        name="rebalance_period"
        inputMode="numeric"
        value={draft.rebalance_period}
        onChange={event => patch({ rebalance_period: event.target.value })}
        {...errorAttributes('rebalance_period', errors.rebalance_period)}
      />
    </label>
    <FieldError field="rebalance_period" message={errors.rebalance_period} />
    <label>因子
      <select
        name="factor_id"
        value={draft.factor_id}
        onChange={event => patch({ factor_id: event.target.value })}
      >
        {FACTOR_IDS.map(factor => <option key={factor}
          disabled={!capabilities?.factors.some(item => item.id === factor && item.status === 'ready')}
          value={factor}>{factor}</option>)}
      </select>
    </label>
    {!factorReady && <p>approved_method_unavailable：当前窗口没有批准且可用的冻结因子输入。</p>}
    <label>佣金费率
      <input
        name="commission_rate"
        inputMode="decimal"
        value={draft.commission_rate}
        onChange={event => patch({ commission_rate: event.target.value })}
        {...errorAttributes('commission_rate', errors.commission_rate)}
      />
    </label>
    <FieldError field="commission_rate" message={errors.commission_rate} />
    <label>印花税费率
      <input
        name="stamp_tax_rate"
        inputMode="decimal"
        value={draft.stamp_tax_rate}
        onChange={event => patch({ stamp_tax_rate: event.target.value })}
        {...errorAttributes('stamp_tax_rate', errors.stamp_tax_rate)}
      />
    </label>
    <FieldError field="stamp_tax_rate" message={errors.stamp_tax_rate} />
    <label>滑点费率
      <input
        name="slippage_rate"
        inputMode="decimal"
        value={draft.slippage_rate}
        onChange={event => patch({ slippage_rate: event.target.value })}
        {...errorAttributes('slippage_rate', errors.slippage_rate)}
      />
    </label>
    <FieldError field="slippage_rate" message={errors.slippage_rate} />
    <label>最低佣金
      <input
        name="min_commission"
        inputMode="decimal"
        value={draft.min_commission}
        onChange={event => patch({ min_commission: event.target.value })}
        {...errorAttributes('min_commission', errors.min_commission)}
      />
    </label>
    <FieldError field="min_commission" message={errors.min_commission} />
    <label>随机种子
      <input
        name="random_seed"
        inputMode="numeric"
        value={draft.random_seed}
        onChange={event => patch({ random_seed: event.target.value })}
        {...errorAttributes('random_seed', errors.random_seed)}
      />
    </label>
    <FieldError field="random_seed" message={errors.random_seed} />
    <label className="backtest-check">
      <input
        type="checkbox"
        disabled={capabilities?.ai_variant.status !== 'ready'}
        checked={draft.include_ai_variant}
        onChange={event => patch({ include_ai_variant: event.target.checked })}
      />纳入 AI 对照腿（消融对照）
    </label>
    <label className="backtest-check">
      <input
        type="checkbox"
        disabled={capabilities?.walk_forward.status !== 'ready'}
        checked={draft.walk_forward_enabled}
        onChange={event => patch({ walk_forward_enabled: event.target.checked })}
      />启用滚动折数（Walk-Forward）
    </label>
    {draft.walk_forward_enabled && <>
      <label>滚动折数
        <input
          name="folds"
          inputMode="numeric"
          value={draft.folds}
          onChange={event => patch({ folds: event.target.value })}
          {...errorAttributes('folds', errors.folds)}
        />
      </label>
      <FieldError field="folds" message={errors.folds} />
      <label>训练窗口（交易日）
        <input
          name="train_sessions"
          inputMode="numeric"
          value={draft.train_sessions}
          onChange={event => patch({ train_sessions: event.target.value })}
          {...errorAttributes('train_sessions', errors.train_sessions)}
        />
      </label>
      <FieldError field="train_sessions" message={errors.train_sessions} />
      <label>测试窗口（交易日）
        <input
          name="test_sessions"
          inputMode="numeric"
          value={draft.test_sessions}
          onChange={event => patch({ test_sessions: event.target.value })}
          {...errorAttributes('test_sessions', errors.test_sessions)}
        />
      </label>
      <FieldError field="test_sessions" message={errors.test_sessions} />
    </>}
    {!manifestReady && <p className="muted backtest-manifest-notice">研究清单未就绪，提交回测已暂停。</p>}
    <div className="backtest-actions">
      <button type="submit" className="primary" disabled={submitDisabled}>提交回测</button>
      <button type="button" onClick={onCancel} disabled={busy}>取消运行</button>
    </div>
  </form>;
}

interface AblationLegView {
  leg: string;
  status: string;
  metrics: Record<string, unknown> | null;
  reason: string | null;
}

interface BacktestAblationView {
  schema_version: string;
  research_only: boolean;
  baseline: AblationLegView;
  ai_variant: AblationLegView;
}

export function AblationSection({ ablation }: { ablation: BacktestAblationView | null }) {
  if (!ablation) return null;
  return <section className="backtest-ablation" aria-label="消融对照">
    <div data-testid="ablation-baseline" className="ablation-leg">
      <h4>baseline 基线腿</h4>
      <p className="muted">状态：{ablation.baseline.status === 'completed' ? '已完成' : ablation.baseline.status}</p>
      <p>config_context_unavailable：完整结果验证后才可展示基线指标。</p>
    </div>
    <div data-testid="ablation-ai-variant" className="ablation-leg ablation-disabled">
      <h4>ai_variant AI 对照腿</h4>
      <p className="muted">状态：已停用</p>
      <p className="ablation-reason" data-testid="ablation-disabled-reason">{ABLATION_DISABLED_REASON}</p>
      <p>{ABLATION_DISABLED_LABEL}</p>
    </div>
  </section>;
}

export function BacktestResultView({ record }: { record: BacktestRunRecord }) {
  if (!record.result) {
    return <section className="backtest-result" aria-label="回测结果">
      <h3>回测结果</h3>
      <p className="muted">暂无物化结果</p>
    </section>;
  }
  try {
    const result = validateResearchResult(record.result, record.config_context ?? null, record.config_hash);
    if (record.status !== 'succeeded' || record.failure !== null) throw new Error('malformed_response');
    return <ResearchEvidence result={result} runId={record.run_id}
      rejectionLabels={BACKTEST_REJECTION_LABELS} />;
  } catch (error) {
    return <p role="alert">{error instanceof Error ? error.message : 'malformed_response'}</p>;
  }
}

function isAbortError(failure: unknown): boolean {
  return failure instanceof DOMException && failure.name === 'AbortError';
}

function failureMessage(failure: unknown): string {
  if (failure instanceof BacktestClientError) return failure.message;
  if (failure instanceof Error) return failure.message;
  return '研究回测请求失败，当前表单输入已保留。';
}

export default function BacktestPanel({ manifest }: { manifest: BacktestManifestContext | null }) {
  const [draft, setDraft] = useState<BacktestDraft>(emptyBacktestDraft);
  const [record, setRecord] = useState<BacktestRunRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [capabilities, setCapabilities] = useState<ResearchCapabilities | null>(null);
  const [pollPaused, setPollPaused] = useState(false);
  const mounted = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const generation = useRef(0);
  const frozenConfig = useRef<Record<string, unknown> | undefined>(undefined);
  const pending = useRef<{ fingerprint: string; command: ReturnType<typeof createBacktestCommand> } | null>(null);

  const errors = validateBacktestDraft(draft);
  const manifestError = validateManifestContext(manifest);
  const manifestReady = manifestError === null;

  const refreshRecord = useCallback(async (runId: string, explicitRetry = false) => {
    if (explicitRetry) generation.current += 1;
    const epoch = generation.current;
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    try {
      const current = await getBacktestRun(runId, controller.signal, frozenConfig.current);
      if (mounted.current && epoch === generation.current && !controller.signal.aborted) {
        frozenConfig.current = current.config_context;
        setPollPaused(false);
        setRecord(current);
      }
    } catch (failure) {
      if (mounted.current && epoch === generation.current && !controller.signal.aborted && !isAbortError(failure)) {
        setPollPaused(true);
        setError(failureMessage(failure));
        if (failure instanceof BacktestClientError && [401, 403, 404].includes(failure.status)) {
          frozenConfig.current = undefined; pending.current = null; setRecord(null);
        }
      }
    }
  }, []);

  const manifestFingerprint = manifest ? asciiCanonical(manifest) : '';
  useEffect(() => {
    generation.current += 1;
    abortRef.current?.abort();
    frozenConfig.current = undefined;
    pending.current = null;
    setRecord(null); setPollPaused(false); setBusy(false);
    const clear = () => {
      generation.current += 1; abortRef.current?.abort();
      frozenConfig.current = undefined; pending.current = null;
      setRecord(null); setCapabilities(null); setBusy(false); setPollPaused(true);
    };
    window.addEventListener('private-session-lock', clear);
    return () => window.removeEventListener('private-session-lock', clear);
  }, [manifestFingerprint]);

  useEffect(() => {
    setCapabilities(null);
    if (!manifestReady || !manifest || errors.start_date || errors.end_date) return;
    const controller = new AbortController();
    const epoch = generation.current;
    void getResearchCapabilities(manifest.snapshot_id, {
      start: draft.start_date, end: draft.end_date,
    }, controller.signal).then(value => {
      if (!controller.signal.aborted && epoch === generation.current) setCapabilities(value);
    }).catch(failure => {
      if (!controller.signal.aborted && epoch === generation.current) setError(failureMessage(failure));
    });
    return () => controller.abort();
  }, [manifestFingerprint, manifestReady, draft.start_date, draft.end_date, errors.start_date, errors.end_date]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!record || busy || pollPaused || !pollBacktestDecision(record).poll) return;
    const delayMs = pollBacktestDecision(record).delayMs;
    pollTimer.current = setTimeout(() => { void refreshRecord(record.run_id); }, delayMs);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [record, busy, pollPaused, refreshRecord]);

  const handleSubmit = async () => {
    if (busy || !manifestReady || hasBacktestErrors(errors)
      || !capabilities?.factors.some(item => item.id === draft.factor_id && item.status === 'ready')) return;
    generation.current += 1;
    const epoch = generation.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError('');
    try {
      const payload = buildBacktestPayload(draft, manifest, crypto.randomUUID());
      const { idempotency_key: unusedKey, ...input } = payload;
      void unusedKey;
      const config = normalizedConfig(input);
      const fingerprint = asciiCanonical(config);
      const command = pending.current?.fingerprint === fingerprint
        ? pending.current.command : createBacktestCommand(config);
      pending.current = { fingerprint, command };
      frozenConfig.current = config;
      setRecord(null); setPollPaused(false);
      const accepted = await submitBacktest(command, controller.signal);
      if (!mounted.current || epoch !== generation.current || controller.signal.aborted) return;
      pending.current = null;
      setBusy(false);
      await refreshRecord(accepted.run_id);
    } catch (failure) {
      if (mounted.current && epoch === generation.current && !controller.signal.aborted && !isAbortError(failure)) {
        setError(failureMessage(failure));
        if (failure instanceof BacktestClientError && !failure.uncertain) pending.current = null;
        if (failure instanceof BacktestClientError && [401, 403].includes(failure.status)) {
          frozenConfig.current = undefined; setRecord(null);
        }
      }
    } finally {
      if (mounted.current && epoch === generation.current && !controller.signal.aborted) setBusy(false);
    }
  };

  const handleCancel = async () => {
    // Cancellation targets the cooperative queued/running window; a terminal
    // run or the pre-submit state is a silent no-op.
    if (!record || !pollBacktestDecision(record).poll) return;
    generation.current += 1;
    const epoch = generation.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError('');
    try {
      const cancelled = await cancelBacktestRun(record.run_id, controller.signal);
      if (mounted.current && epoch === generation.current && !controller.signal.aborted) setRecord(cancelled);
    } catch (failure) {
      if (mounted.current && epoch === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(failureMessage(failure));
    } finally {
      if (mounted.current && epoch === generation.current && !controller.signal.aborted) setBusy(false);
    }
  };

  return <section className="backtest-panel" aria-label="研究回测">
    <div className="section-heading backtest-heading">
      <div>
        <p className="eyebrow">本机资料 · 研究用途</p>
        <h2>研究回测</h2>
      </div>
    </div>
    <BacktestDisclosure />
    {error && <p className="notice error" role="alert">{error}</p>}
    <p className="status" role="status" aria-live="polite">
      {record ? `运行状态：${backtestStatusLabel(record.status)}` : '尚未提交回测任务'}
    </p>
    <BacktestConfigForm
      draft={draft}
      errors={errors}
      busy={busy}
      manifestReady={manifestReady}
      capabilities={capabilities}
      onDraft={setDraft}
      onSubmit={() => void handleSubmit()}
      onCancel={() => void handleCancel()}
    />
    {record && <BacktestResultView record={record} />}
    {record && pollPaused && <button type="button" disabled={busy} onClick={() => { void refreshRecord(record.run_id, true); }}>重试读取原始运行</button>}
  </section>;
}
