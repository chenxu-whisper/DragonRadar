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
