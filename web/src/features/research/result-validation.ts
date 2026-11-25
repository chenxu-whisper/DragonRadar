/** Fail-closed EX15 mirror. No current PUB, LOCAL portfolio or read-time statistics. */
import Decimal from 'decimal.js';
import { asciiCanonical, configHash, normalizedConfig } from './config-canonical';
import type { ResearchResult } from './result-types';

const D = Decimal.clone({ precision: 50, rounding: Decimal.ROUND_HALF_EVEN });
const V1 = 'dragon-radar.research-backtest/v1';
const V2 = 'dragon-radar.research-backtest-result/v2';
const phases = ['ice', 'ebb', 'divergent', 'repair', 'climax'];
const money = ['initial_equity', 'final_equity', 'total_commission', 'total_stamp_tax', 'total_cost'];
const metricKeys = [...money, 'total_return', 'benchmark_return', 'excess_return', 'max_drawdown',
  'turnover_rate', 'unfillable_count_ratio', 'unfillable_amount_ratio'];
const reasons = ['suspended', 'limit_up_blocked_buy', 'limit_down_blocked_sell', 't1_sell_blocked',
  'insufficient_cash', 'insufficient_position', 'delisted', 'no_tradable_price', 'unknown_limit_untradable'];
const qualityCodes = ['factor_value_missing', 'factor_value_excluded', 'factor_section_below_top_n',
  'factor_section_empty', 'mark_price_carried', 'decision_without_next_session', 'topic_domain_unavailable'];
const methods = {
  sizing_method: 'equal-weight-cost-aware-board-lot-v1',
  execution_method: 'next-tradable-session-raw-close-v1', ledger_method: 'decimal-t1-settlement-v1',
  valuation_method: 'session-close-last-visible-carry-v1', cost_method: 'explicit-fixed-rate-three-tier-v1',
};
const healthMethod = {
  method_version: 'factor-health-decimal34-v1', min_sample_size: 5,
  quantile_points: ['0', '0.25', '0.5', '0.75', '1'], rate_quantum: '0.000001',
  correlation_method: 'equal-session-cross-sectional-spearman-v1',
};

export function ensure(condition: unknown, code = 'malformed_response'): asserts condition {
  if (!condition) throw new Error(code);
}
export function object(value: unknown, keys?: string[]): Record<string, unknown> {
  ensure(value !== null && typeof value === 'object' && !Array.isArray(value));
  const data = value as Record<string, unknown>;
  if (keys) ensure(Object.keys(data).length === keys.length && keys.every(key => Object.hasOwn(data, key)));
  return data;
}
function array(value: unknown): unknown[] { ensure(Array.isArray(value)); return value; }
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  ensure(typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max);
  return value;
}
function text(value: unknown): string {
  ensure(typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,100}$/.test(value)); return value;
}
function uuid(value: unknown): string {
  ensure(typeof value === 'string' && /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)); return value;
}
function hash(value: unknown): string {
  ensure(typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)); return value;
}
function day(value: unknown): string {
  ensure(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value));
  ensure(new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value);
  return value;
}
function utc(value: unknown): number {
  ensure(typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(Z|\+00:00)$/.test(value));
  const time = Date.parse(value); ensure(Number.isFinite(time)); return time;
}
function dec(value: unknown, places?: number): Decimal {
  ensure(typeof value === 'string' && (places === undefined
    ? /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)
    : new RegExp(`^-?\\d+\\.\\d{${places}}$`).test(value)));
  const result = new D(value); ensure(result.isFinite()); return result;
}
function equal(a: unknown, b: unknown) { ensure(asciiCanonical(a) === asciiCanonical(b), 'selection_mismatch'); }
function manifest(value: unknown): Record<string, unknown> {
  const result = object(value, ['domains', 'versions']);
  const domains = array(result.domains);
  ensure(domains.length >= 1 && domains.length <= 3);
  const names = domains.map(item => {
    const entry = object(item, ['domain', 'snapshot_id']);
    ensure(['market', 'topic', 'role'].includes(text(entry.domain))); uuid(entry.snapshot_id); return entry.domain;
  });
  ensure(new Set(names).size === names.length && names.includes('market'));
  const versions = object(result.versions, ['adjustment', 'calendar', 'pool']);
  Object.values(versions).forEach(text);
  return { domains: [...domains].sort((a, b) => String(object(a).domain).localeCompare(String(object(b).domain))), versions };
}
export function validateConfig(value: Record<string, unknown>): Record<string, unknown> {
  const cfg = normalizedConfig(value);
  object(cfg, ['schema_version', 'snapshot_id', 'strategy_id', 'parameters', 'period', 'cost_model',
    'execution_model', 'walk_forward', 'experiment', 'input_manifests']);
  ensure(cfg.schema_version === V1 && cfg.strategy_id === 'factor-cross-sectional-topn-v1');
  uuid(cfg.snapshot_id); manifest(cfg.input_manifests);
  const p = object(cfg.parameters, ['factor_id', 'top_n', 'rebalance_period', 'weighting']);
  ensure(['FAC-R01', 'FAC-R02', 'FAC-R03', 'FAC-C01', 'FAC-C02'].includes(text(p.factor_id)) && p.weighting === 'equal_weight');
  integer(p.top_n, 1, 100); integer(p.rebalance_period, 1, 60);
  const period = object(cfg.period, ['start', 'end']);
  const span = (Date.parse(day(period.end)) - Date.parse(day(period.start))) / 86400000;
  ensure(span >= 2 && span <= 400);
  const cost = object(cfg.cost_model, ['commission_rate', 'stamp_tax_rate', 'slippage_model', 'slippage_rate', 'stamp_tax_side', 'min_commission']);
  ensure(cost.slippage_model === 'fixed_rate' && cost.stamp_tax_side === 'sell');
  const execution = object(cfg.execution_model, ['fill_timing', 'unknown_limit_policy']);
  ensure(execution.fill_timing === 'next_tradable_point' && execution.unknown_limit_policy === 'reject_untradable');
  const experiment = object(cfg.experiment, ['random_seed', 'legs']);
  integer(experiment.random_seed, 0, 4294967295);
  const legs = array(experiment.legs);
  ensure(legs.includes('baseline') && new Set(legs).size === legs.length && legs.every(x => ['baseline', 'ai_variant'].includes(String(x))));
  if (cfg.walk_forward !== null) {
    const wf = object(cfg.walk_forward, ['folds', 'train_sessions', 'test_sessions']);
    const required = integer(wf.train_sessions, 1) + integer(wf.folds, 2, 10) * integer(wf.test_sessions, 1);
    ensure(required <= 250 && span >= required);
  }
  return cfg;
}
function metrics(value: unknown) {
  const data = object(value, metricKeys);
  const numbers = Object.fromEntries(metricKeys.map(key => [key, dec(data[key], money.includes(key) ? 2 : 6)]));
  ensure(numbers.initial_equity.gt(0) && numbers.final_equity.gte(0));
  for (const key of ['max_drawdown', 'unfillable_count_ratio', 'unfillable_amount_ratio']) ensure(numbers[key].gte(0) && numbers[key].lte(1));
  for (const key of ['turnover_rate', 'total_commission', 'total_stamp_tax', 'total_cost']) ensure(numbers[key].gte(0));
  ensure(numbers.total_return.gte(-1) && numbers.benchmark_return.gte(-1));
  ensure(numbers.final_equity.div(numbers.initial_equity).minus(1).toDecimalPlaces(6).eq(numbers.total_return));
  ensure(numbers.total_return.minus(numbers.benchmark_return).minus(numbers.excess_return).abs().lte('0.000001'));
  ensure(numbers.total_commission.plus(numbers.total_stamp_tax).eq(numbers.total_cost));
  return numbers;
}
function equity(value: unknown, m: ReturnType<typeof metrics>, start: string, end: string) {
  const points = array(value); ensure(points.length > 0 && points.length <= 250);
  let previous = '';
  let peak = new D(0), drawdown = new D(0);
  const days = points.map(raw => {
    const point = object(raw, ['trade_date', 'equity']); const date = day(point.trade_date);
    const amount = dec(point.equity, 2); ensure(amount.gte(0) && date > previous && date >= start && date <= end);
    previous = date; peak = D.max(peak, amount);
    if (peak.gt(0)) drawdown = D.max(drawdown, peak.minus(amount).div(peak));
    return date;
  });
  ensure(dec(object(points[0]).equity).eq(m.initial_equity) && dec(object(points.at(-1)).equity).eq(m.final_equity));
  ensure(drawdown.toDecimalPlaces(6).eq(m.max_drawdown));
  return days;
}
function artifacts(raw: Record<string, unknown>, start: string, end: string) {
  const m = metrics(raw.metrics); const days = equity(raw.equity, m, start, end);
  let commission = new D(0), tax = new D(0);
  for (const rawFill of array(raw.trades)) {
    const fill = object(rawFill, ['security_id', 'side', 'quantity', 'signal_date', 'trade_date',
      'raw_close', 'fill_price', 'gross_amount', 'commission', 'stamp_tax', 'net_cash_delta']);
    text(fill.security_id); ensure(fill.side === 'buy' || fill.side === 'sell');
    const quantity = integer(fill.quantity, 1);
    ensure(start <= day(fill.signal_date) && String(fill.signal_date) < day(fill.trade_date) && String(fill.trade_date) <= end);
    ensure(dec(fill.raw_close).gt(0)); const price = dec(fill.fill_price, 4); ensure(price.gt(0));
    const gross = dec(fill.gross_amount, 2), fee = dec(fill.commission, 2), stamp = dec(fill.stamp_tax, 2);
    ensure(gross.eq(price.times(quantity).toDecimalPlaces(2)) && fee.gte(0) && stamp.gte(0));
    const cash = fill.side === 'buy' ? gross.neg().minus(fee).minus(stamp) : gross.minus(fee).minus(stamp);
    ensure(dec(fill.net_cash_delta, 2).eq(cash));
    if (fill.side === 'buy') ensure(stamp.eq(0) && quantity % 100 === 0);
    commission = commission.plus(fee); tax = tax.plus(stamp);
  }
  ensure(commission.eq(m.total_commission) && tax.eq(m.total_stamp_tax));
  for (const rawRejection of array(raw.rejections)) {
    const rejection = object(rawRejection, ['security_id', 'side', 'quantity', 'signal_date', 'trade_date', 'reason']);
    text(rejection.security_id); integer(rejection.quantity, 1);
    ensure(['buy', 'sell'].includes(String(rejection.side)) && reasons.includes(String(rejection.reason)));
    ensure(start <= day(rejection.signal_date) && String(rejection.signal_date) < day(rejection.trade_date) && String(rejection.trade_date) <= end);
  }
  return days;
}
function partition(value: unknown) {
  const coverage = object(value, ['expected', 'valid', 'excluded', 'missing']);
  Object.values(coverage).forEach(value => integer(value));
  ensure(coverage.expected === Number(coverage.valid) + Number(coverage.excluded) + Number(coverage.missing));
  return coverage;
}
function walkForward(raw: Record<string, unknown>, cfg: Record<string, unknown>, days: string[]) {
  ensure((cfg.walk_forward === null) === (raw.walk_forward === null) && (cfg.walk_forward === null) === (raw.folds === null));
  if (cfg.walk_forward === null) return;
  const spec = object(cfg.walk_forward), p = object(cfg.parameters);
  const report = object(raw.walk_forward, ['schema_version', 'research_only', 'layout', 'folds_count', 'train_sessions',
    'test_sessions', 'regime_map', 'folds', 'aggregate_equity', 'aggregate_metrics']);
  ensure(report.schema_version === 'dragon-radar.research-walk-forward/v1' && report.research_only === true && report.layout === 'walk-forward-anchored-v1');
  for (const [key, expected] of [['folds_count', spec.folds], ['train_sessions', spec.train_sessions], ['test_sessions', spec.test_sessions]]) ensure(report[String(key)] === expected);
  equal(raw.folds, report.folds);
  const train = integer(spec.train_sessions, 1), test = integer(spec.test_sessions, 1), count = integer(spec.folds, 2, 10);
  ensure(days.length >= train + count * test);
  const regime = object(report.regime_map, ['layout', 'fit_method', 'train_start', 'train_end', 'train_sessions',
    'rebalance_period', 'missing_phase_decision_sessions', 'weights']);
  ensure(regime.layout === report.layout && regime.fit_method === 'equal-pool-mean-next-session-return-v1'
    && regime.train_start === days[0] && regime.train_end === days[train - 1] && regime.train_sessions === train
    && regime.rebalance_period === p.rebalance_period);
  const missing = array(regime.missing_phase_decision_sessions);
  ensure(new Set(missing).size === missing.length);
  for (const date of missing) ensure(days.indexOf(day(date)) >= 0 && days.indexOf(String(date)) < train - 1 && days.indexOf(String(date)) % Number(p.rebalance_period) === 0);
  const weights = array(regime.weights); ensure(weights.length === 5);
  let observations = 0;
  weights.forEach((value, index) => {
    const fit = object(value, ['phase', 'status', 'observations', 'mean_next_session_return', 'weight']);
    ensure(fit.phase === phases[index]); const weight = dec(fit.weight); ensure(weight.gte('0.5') && weight.lte(1));
    observations += integer(fit.observations);
    if (fit.status === 'fitted') {
      ensure(Number(fit.observations) > 0 && weight.eq(D.min(1, D.max('0.5', dec(fit.mean_next_session_return).plus(1)))));
    } else ensure(fit.status === 'unobserved_in_training' && fit.observations === 0 && fit.mean_next_session_return === null && weight.eq(1));
  });
  ensure(observations <= Math.ceil((train - 1) / Number(p.rebalance_period)));
  const folds = array(report.folds); ensure(folds.length === count);
  folds.forEach((value, index) => {
    const fold = object(value, ['fold_index', 'train_start', 'train_end', 'test_start', 'test_end', 'metrics', 'equity', 'trades', 'rejections', 'quality']);
    const block = days.slice(train + index * test, train + (index + 1) * test);
    ensure(fold.fold_index === index && fold.train_start === regime.train_start && fold.train_end === regime.train_end
      && fold.test_start === block[0] && fold.test_end === block.at(-1) && String(fold.train_end) < String(fold.test_start));
    equal(artifacts(fold, block[0], block.at(-1)!), block);
    for (const event of array(fold.quality)) {
      const quality = object(event, ['trade_date', 'code', 'phase']); ensure(block.includes(day(quality.trade_date)));
      ensure((quality.code === 'regime_phase_missing' && quality.phase === null)
        || (quality.code === 'regime_unobserved_in_training' && phases.includes(String(quality.phase))));
    }
  });
  const aggregate = metrics(report.aggregate_metrics);
  equal(equity(report.aggregate_equity, aggregate, days[train], days[train + count * test - 1]), days.slice(train, train + count * test));
}
function v2(raw: Record<string, unknown>, cfg: Record<string, unknown>, days: string[]) {
  const line = object(raw.lineage, ['anchor_sequence', 'requested_period', 'actual_period', 'sessions', 'frames', 'benchmark', 'health_method', 'computed_at']);
  equal(line.requested_period, cfg.period); equal(line.actual_period, { start: days[0], end: days.at(-1) }); equal(line.sessions, days);
  equal(line.health_method, healthMethod); const completed = utc(line.computed_at);
  const anchor = integer(line.anchor_sequence, 1), frames = array(line.frames); ensure(frames.length === days.length);
  let sequence = 0, expected = 0;
  const pubs = new Set<string>(), visible = new Map<string, string[]>();
  frames.forEach((value, index) => {
    const frame = object(value, ['trade_date', 'publication_id', 'publication_sequence', 'market_snapshot_id', 'topic_snapshot_id',
      'role_snapshot_id', 'frame_sha256', 'visible_security_ids', 'as_of']);
    ensure(frame.trade_date === days[index]); const pub = uuid(frame.publication_id); ensure(!pubs.has(pub)); pubs.add(pub);
    uuid(frame.market_snapshot_id); if (frame.topic_snapshot_id !== null) uuid(frame.topic_snapshot_id); if (frame.role_snapshot_id !== null) uuid(frame.role_snapshot_id);
    const current = integer(frame.publication_sequence, 1, anchor); ensure(current > sequence); sequence = current;
    hash(frame.frame_sha256); ensure(utc(frame.as_of) <= completed);
    const ids = array(frame.visible_security_ids).map(text); ensure(ids.length <= 5000 && new Set(ids).size === ids.length); equal(ids, [...ids].sort());
    expected += ids.length; visible.set(days[index], ids);
  });
  const last = object(frames.at(-1)); ensure(last.publication_id === cfg.snapshot_id && last.publication_sequence === anchor);
  for (const entry of array(object(cfg.input_manifests).domains)) {
    const domain = object(entry); ensure(last[`${domain.domain}_snapshot_id`] === domain.snapshot_id);
  }
  const artifactGroups = [raw, ...(raw.folds === null ? [] : array(raw.folds).map(value => object(value)))];
  for (const group of artifactGroups) for (const field of ['trades', 'rejections']) {
    for (const item of array(group[field])) {
      const artifact = object(item); ensure(visible.get(String(artifact.signal_date))?.includes(String(artifact.security_id)));
    }
  }
  const benchmark = object(line.benchmark, ['status', 'reason', 'definition_sha256', 'source_id', 'source_version', 'input_sha256']);
  if (benchmark.status === 'ready') {
    ensure(benchmark.reason === null); hash(benchmark.definition_sha256); hash(benchmark.input_sha256); text(benchmark.source_id); text(benchmark.source_version);
  } else ensure(benchmark.status === 'unavailable' && benchmark.reason === 'benchmark_inputs_unavailable'
    && ['definition_sha256', 'source_id', 'source_version', 'input_sha256'].every(key => benchmark[key] === null));
  const health = object(raw.health, ['status', 'reason', 'report']);
  if (health.status === 'unavailable') { ensure(health.reason === 'factor_health_inputs_unavailable' && health.report === null); return; }
  ensure(health.status === 'ready' && health.reason === null);
  const report = object(health.report, ['schema_version', 'research_only', 'anchor_sequence', 'window_start', 'window_end', 'sessions',
    'primary_factor_id', 'manifests', 'factors', 'rank_correlations']);
  ensure(report.schema_version === 'dragon-radar.research-health/v1' && report.research_only === true
    && report.anchor_sequence === anchor && report.window_start === days[0] && report.window_end === days.at(-1)
    && report.sessions === days.length && report.primary_factor_id === object(cfg.parameters).factor_id);
  equal(manifest(report.manifests), manifest(cfg.input_manifests));
  const factors = array(report.factors), ids: string[] = [];
  factors.forEach(value => {
    const factor = object(value, ['factor_id', 'metric', 'window', 'unit', 'sessions', 'coverage', 'sample_size', 'valid_rate',
      'excluded_rate', 'missing_rate', 'quantiles', 'status', 'reasons']);
    const id = text(factor.factor_id); ensure(!ids.includes(id)); ids.push(id);
    text(factor.metric); text(factor.window); text(factor.unit); ensure(factor.sessions === days.length);
    const coverage = partition(factor.coverage); ensure(coverage.expected === expected && factor.sample_size === coverage.valid);
    for (const key of ['valid', 'excluded', 'missing']) ensure(dec(factor[key + '_rate'], 6).eq(expected ? new D(Number(coverage[key])).div(expected).toDecimalPlaces(6) : 0));
    const reasonList = array(factor.reasons); reasonList.forEach(x => ensure(['insufficient_sample_size', 'insufficient_paired_sample', 'no_eligible_sessions'].includes(String(x))));
    if (factor.status === 'ok') {
      ensure(Number(factor.sample_size) >= 5 && reasonList.length === 0);
      const quantile = object(factor.quantiles, ['points', 'values']); equal(quantile.points, healthMethod.quantile_points);
      const values = array(quantile.values).map(x => dec(x, 6)); ensure(values.length === 5);
      values.forEach((x, i) => ensure(i === 0 || x.gte(values[i - 1])));
    } else ensure(factor.status === 'insufficient_sample' && Number(factor.sample_size) < 5 && factor.quantiles === null && reasonList.length > 0);
  });
  ensure(ids.includes(String(report.primary_factor_id))); equal(ids, [...ids].sort());
  const pairs: string[] = [];
  for (const value of array(report.rank_correlations)) {
    const correlation = object(value, ['left', 'right', 'paired_sample_size', 'eligible_sessions', 'value', 'status', 'reasons']);
    ensure(ids.includes(String(correlation.left)) && ids.includes(String(correlation.right)) && String(correlation.left) < String(correlation.right));
    const pair = `${correlation.left}/${correlation.right}`; ensure(!pairs.includes(pair)); pairs.push(pair);
    integer(correlation.paired_sample_size, 0, expected); integer(correlation.eligible_sessions, 0, days.length);
    const why = array(correlation.reasons);
    if (correlation.status === 'ok') ensure(Number(correlation.paired_sample_size) >= 5 && Number(correlation.eligible_sessions) > 0 && dec(correlation.value, 6).abs().lte(1) && why.length === 0);
    else ensure(correlation.status === 'insufficient_sample' && correlation.value === null && why.length > 0);
  }
  equal(pairs, ids.flatMap((id, i) => ids.slice(i + 1).map(other => `${id}/${other}`)));
}

export function validateResearchResult(value: unknown, context: Record<string, unknown> | null, expectedHash: string): ResearchResult {
  ensure(context, 'config_context_unavailable');
  const cfg = validateConfig(context), raw = object(value);
  const version = raw.schema_version; ensure(version === V1 || version === V2);
  object(raw, ['schema_version', 'research_only', 'config_hash', 'snapshot_id', 'manifests', 'versions', 'metrics', 'equity',
    'trades', 'rejections', 'quality', 'folds', 'walk_forward', 'baseline', 'ablation', ...(version === V2 ? ['health', 'lineage'] : [])]);
  ensure(raw.research_only === true && hash(raw.config_hash) === expectedHash && uuid(raw.snapshot_id) === cfg.snapshot_id, 'selection_mismatch');
  equal(manifest(raw.manifests), manifest(cfg.input_manifests));
  const versions = object(raw.versions, ['strategy_id', 'strategy_method', 'factor_id', 'factor_metric', 'factor_window', 'factor_unit',
    ...Object.keys(methods), 'cost_sensitivity_multipliers', 'adjustment', 'calendar', 'pool']);
  ensure(versions.strategy_id === cfg.strategy_id && versions.strategy_method === cfg.strategy_id && versions.factor_id === object(cfg.parameters).factor_id);
  for (const [key, method] of Object.entries(methods)) ensure(versions[key] === method);
  ensure(['20d', '60d', '120d'].includes(String(versions.factor_window)) && versions.factor_unit === 'ratio'
    && versions.factor_metric === `relative_strength_${String(versions.factor_window).slice(0, -1)}`);
  equal(versions.cost_sensitivity_multipliers, { low: '0.5', base: '1', high: '2' });
  for (const key of ['adjustment', 'calendar', 'pool']) ensure(versions[key] === object(object(cfg.input_manifests).versions)[key]);
  const period = object(cfg.period), days = artifacts(raw, String(period.start), String(period.end));
  ensure(dec(object(raw.metrics).initial_equity).eq('100000'));
  const quality = array(raw.quality); ensure(quality.length === days.length);
  quality.forEach((value, index) => {
    const entry = object(value, ['trade_date', 'factor_coverage', 'events']); ensure(entry.trade_date === days[index]);
    if (entry.factor_coverage !== null) partition(entry.factor_coverage);
    for (const value of array(entry.events)) {
      const event = object(value, ['trade_date', 'code', 'security_id']); ensure(event.trade_date === entry.trade_date && qualityCodes.includes(String(event.code)));
      if (event.security_id !== null) text(event.security_id);
    }
  });
  const baseline = object(raw.baseline, ['status', 'metrics', 'cost_sensitivity']); ensure(baseline.status === 'completed'); equal(baseline.metrics, raw.metrics);
  const tiers = array(baseline.cost_sensitivity); ensure(tiers.length === 3);
  tiers.forEach((value, index) => {
    const tier = object(value, ['tier', 'cost_multiplier', 'metrics']); ensure(tier.tier === ['low', 'base', 'high'][index] && tier.cost_multiplier === ['0.5', '1', '2'][index]);
    metrics(tier.metrics); if (index === 1) equal(tier.metrics, raw.metrics);
  });
  walkForward(raw, cfg, days);
  const aiRequested = array(object(cfg.experiment).legs).includes('ai_variant'); ensure((raw.ablation !== null) === aiRequested);
  if (aiRequested) {
    const ablation = object(raw.ablation, ['schema_version', 'research_only', 'baseline', 'ai_variant']);
    ensure(ablation.schema_version === 'dragon-radar.research-ablation/v1' && ablation.research_only === true);
    for (const name of ['baseline', 'ai_variant']) {
      const leg = object(ablation[name], ['leg', 'status', 'inputs', 'metrics', 'reason']); ensure(leg.leg === name);
      const inputs = object(leg.inputs, ['snapshot_id', 'input_manifests', 'period', 'cost_model', 'random_seed']);
      ensure(inputs.snapshot_id === cfg.snapshot_id && inputs.random_seed === object(cfg.experiment).random_seed);
      equal(manifest(inputs.input_manifests), manifest(cfg.input_manifests)); equal(inputs.period, cfg.period); equal(inputs.cost_model, cfg.cost_model);
      if (name === 'baseline') { ensure(leg.status === 'completed' && leg.reason === null); equal(leg.metrics, raw.metrics); }
      else ensure(leg.status === 'disabled' && leg.metrics === null && leg.reason === 'real_evaluation_inputs_unavailable');
    }
  }
  if (version === V2) v2(raw, cfg, days);
  return raw as unknown as ResearchResult;
}

export async function validateResearchEnvelope(value: unknown, config: Record<string, unknown> | null, runId: string) {
  ensure(config, 'config_context_unavailable');
  const raw = object(value, ['run_id', 'status', 'config_hash', 'research_only', 'result', 'failure']);
  ensure(uuid(raw.run_id) === runId && raw.research_only === true && hash(raw.config_hash) === await configHash(validateConfig(config)), 'selection_mismatch');
  ensure(['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(String(raw.status)));
  if (raw.status === 'succeeded') {
    ensure(raw.failure === null); validateResearchResult(raw.result, config, String(raw.config_hash));
  } else {
    ensure(raw.result === null);
    if (raw.status === 'failed') text(object(raw.failure).code);
    else ensure(raw.failure === null || (raw.status === 'cancelled' && object(raw.failure).code === 'cancelled'));
  }
  return raw;
}
