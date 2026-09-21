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
  calendar_sessions: string[]; earliest_date: string;
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
  data: T;
}
export interface ResetSnapshotData {
  revision: number; supersedes: string | null; published_at: string; security_count: number;
}
export interface ResetRatio {
  numerator: number; denominator: number; value: number | null; reason: string | null;
}
export interface ResetSector {
  sector_id: string; name: string; member_count: number; valid_returns: number;
  median_return: number | null; change_ratio: number | null; limit_up_count: number | null;
  net_inflow: number | null; net_inflow_reason: string | null; quality_flags: string[];
  coverage: WorkbenchCoverage;
}
export interface ResetOverview {
  indexes: { security_id: string; code: string | null; name: string; last: number | null; change_ratio: number | null; amount: number | null; reason: string | null }[];
  breadth: { up: number; down: number; flat: number; valid_total: number; coverage: WorkbenchCoverage; seal_rate: ResetRatio };
  sentiment: {
    limit_up_count: number; limit_down_count: number; max_boards: number;
    promote_rate: ResetRatio & { yesterday_ge2: number; excluded: number; missing: number };
    yesterday_limit_up_median_return: number | null;
  };
  hot_sectors: ResetSector[];
  factors: Record<string, unknown>;
  historical_topic_analysis: { enabled: boolean; reason: string | null };
}
export interface ResetHeatmap {
  columns: number[]; cells: { trade_date: string; boards: number; count: number }[];
}
