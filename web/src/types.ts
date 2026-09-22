export interface Coverage { expected: number; valid: number; legal_excluded: number; missing: number }
export interface Envelope<T> {
  snapshot_id: string; trade_date: string; as_of: string; source_time: string | null;
  source: { id: string; version: string }; coverage: Coverage; coverage_scope: 'snapshot_current';
  analysis_coverage: Coverage | { requested: number; eligible: number; excluded: number } | null;
  units: { currency: string[]; price_unit: string[]; volume_unit: string[]; amount_unit: string[] };
  quality_flags: unknown[]; rule_version: string; basis: string;
  manifest: Record<string, unknown>; provenance: 'contemporaneous' | 'retrospective'; result: T;
}
export interface Summary {
  revision: number; supersedes: string | null; published_at: string; security_count: number;
  calendar_sessions: string[]; earliest_date: string | null;
}
export type Snapshot = Envelope<Summary>;
export interface Units {
  currency: string | null; price_unit: string | null; volume_unit: string | null; amount_unit: string | null;
}
export interface Security extends Units {
  security_id: string; name: string | null; exchange: string | null; board: string | null;
  close: string | null; change: string | null; amount: string | null;
  suspended: boolean | null; missing_reasons: string[]; change_unit: 'fraction';
}
export interface Securities { items: Security[]; total: number; page: number; page_size: number; basis: string }
export interface HistoryPoint extends Units {
  trade_date: string; open: string | null; high: string | null; low: string | null;
  close: string | null; volume: string | null; amount: string | null;
  suspended: boolean | null; missing_reasons: string[];
}
export interface Detail { security: Security; history: HistoryPoint[]; chart_type: 'candlestick' | 'line'; price_basis: string; basis: string }
export interface Exclusion { security_id: string; reasons: string[] }
export interface Overview {
  advancing: number; declining: number; unchanged: number; coverage: Coverage;
  returns: { security_id: string; value: string; basis: string; warnings: string[] }[]; exclusions: Exclusion[];
}
export interface IntervalItem {
  security_id: string; interval_return: string | null; maximum_drawdown: string | null;
  normalized_curve: (string | null)[];
}
export interface Interval { sessions: string[]; items: IntervalItem[]; exclusions: Exclusion[] }

export interface WorkbenchCoverage { expected: number; valid: number; excluded: number; missing: number }
export interface WorkbenchEnvelope<T> {
  trade_date: string; as_of: string; snapshot_id: string; source: string; source_version: string;
  coverage: WorkbenchCoverage; quality_flags: string[]; rule_version: string;
  basis: { unit: string | null; currency: string | null; price_type: string | null; benchmark: string | null };
  data: T; reason?: string | null;
}
export interface ResetSnapshotData {
  revision: number; supersedes: string | null; published_at: string; security_count: number;
  calendar_sessions: string[]; earliest_date: string | null;
}
export interface ResetExclusion { security_id: string; reasons: string[] }
export interface ResetRankItem {
  rank: number | null; security_id: string; name: string | null;
  topics: { topic_id: string; name: string }[];
  topic_status: string; topic_reasons: string[]; reasons: string[];
  return_ratio: number | null; excess_return_ratio: number | null;
  max_drawdown_ratio: number | null; amount_change_ratio: number | null;
}
export interface ResetRankData {
  window_days: number; adjusted_start: string; adjusted_end: string;
  benchmark_return: number | null; ranking: ResetRankItem[]; exclusions: ResetExclusion[];
}
export interface ResetCurvePoint {
  trade_date: string; normalized: number | null; drawdown: number | null;
}
export interface ResetCurve {
  security_id: string; points: ResetCurvePoint[]; reasons: string[];
}
export interface ResetCurveData {
  adjusted_start: string; adjusted_end: string; benchmark: string;
  curves: ResetCurve[]; exclusions: ResetExclusion[];
}
export interface ResetKlinePoint {
  trade_date: string; open: number | null; high: number | null; low: number | null;
  close: number | null; volume: number | null; amount: number | null;
}
export interface ResetKlineData {
  security_id: string; adjustment: 'raw' | 'qfq' | 'hfq';
  adjusted_start: string; adjusted_end: string; chart_type: 'candlestick' | 'line';
  points: ResetKlinePoint[];
}
export interface ResetStock {
  security_id: string;
  name: string | null;
  quote: { last: number | null; change_ratio: number | null; amount: number | null };
  roles: { role_type: string; evidence_status: string; sector_id: string }[];
  role_reason?: string;
  seal: {
    is_limit_up: boolean | null; touched_limit: boolean | null;
    first_seal_time: string | null; last_seal_time: string | null;
    break_count: number | null; seal_amount: number | null; seal_amount_unit: string | null;
    continuous_boards: number | null; reason: string | null;
  };
  regulation: { active_events: number | null; reason: string | null };
}
export interface ResetRatio {
  numerator: number; denominator: number; value: number | null; reason: string | null;
}
export interface ResetSecurityIdentity {
  security_id: string; code: string | null; name: string;
}
export interface ResetSector {
  sector_id: string; name: string; member_count: number; valid_returns: number;
  median_return: number | null; change_ratio: number | null; limit_up_count: number | null;
  up_ratio: ResetRatio; limit_up_ratio: ResetRatio;
  net_inflow: number | null; net_inflow_reason: string | null; quality_flags: string[];
  coverage: WorkbenchCoverage;
}
export interface ResetFactors {
  'FAC-M01': {
    advancing: ResetRatio & { coverage: WorkbenchCoverage };
    above_ma20: ResetRatio & { coverage: WorkbenchCoverage };
    method: string;
  };
  'FAC-M02': {
    value: number | null; today_total: number | null; baseline_mean: number | null;
    baseline_days: number; baseline_excludes_current: boolean;
    coverage: WorkbenchCoverage; reason: string | null; method: string;
  };
  'FAC-M03': {
    indexes: {
      security_id: string; code: string | null; name: string; close: number | null;
      close_over_ma20: number | null; close_over_ma60: number | null;
      volatility_20: number | null; volatility_method: string;
      coverage: { ma20: WorkbenchCoverage; ma60: WorkbenchCoverage; return20: WorkbenchCoverage };
      close_over_ma20_reason: string | null; close_over_ma60_reason: string | null;
      volatility_20_reason: string | null; reason: string | null;
    }[];
    method: string;
  };
}
export interface ResetOverview {
  topic_basis: { source: string | null; source_version: string | null; as_of: string | null; cutoff: string };
  indexes: { security_id: string; code: string | null; name: string; last: number | null; change_ratio: number | null; amount: number | null; reason: string | null }[];
  breadth: {
    up: number; down: number; flat: number; valid_total: number;
    coverage: WorkbenchCoverage;
    members: Record<'up' | 'down' | 'flat', ResetSecurityIdentity[]>;
    seal_rate: ResetRatio & { coverage: WorkbenchCoverage };
  };
  sentiment: {
    limit_up_count: number; limit_down_count: number; max_boards: number;
    promote_rate: ResetRatio & { yesterday_ge2: number; excluded: number; missing: number };
    yesterday_limit_up_median_return: number | null;
    yesterday_limit_up_return_coverage: WorkbenchCoverage;
    yesterday_limit_up_return_reason: string | null;
  };
  hot_sectors: ResetSector[];
  factors: ResetFactors;
  historical_topic_analysis: { enabled: boolean; reason: string | null };
}
export interface ResetHeatmap {
  columns: number[]; cells: { trade_date: string; boards: number; count: number }[];
}
