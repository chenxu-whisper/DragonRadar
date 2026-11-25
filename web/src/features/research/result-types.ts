/** Archive facts. Strings retain source decimal precision; no JS money numbers. */
export type Metrics = Record<
  'initial_equity' | 'final_equity' | 'total_return' | 'benchmark_return' | 'excess_return'
  | 'max_drawdown' | 'turnover_rate' | 'total_commission' | 'total_stamp_tax' | 'total_cost'
  | 'unfillable_count_ratio' | 'unfillable_amount_ratio', string>;
export interface Partition { expected: number; valid: number; excluded: number; missing: number }
export interface EquityPoint { trade_date: string; equity: string }
export interface Rejection {
  security_id: string; side: 'buy' | 'sell'; quantity: number;
  signal_date: string; trade_date: string; reason: string;
}
export interface Fill extends Omit<Rejection, 'reason'> {
  raw_close: string; fill_price: string; gross_amount: string;
  commission: string; stamp_tax: string; net_cash_delta: string;
}
export interface DayQuality {
  trade_date: string; factor_coverage: Partition | null;
  events: Array<{ trade_date: string; code: string; security_id: string | null }>;
}
export interface Fold {
  fold_index: number; train_start: string; train_end: string; test_start: string; test_end: string;
  metrics: Metrics; equity: EquityPoint[]; trades: Fill[]; rejections: Rejection[];
  quality: Array<{ trade_date: string; code: string; phase: string | null }>;
}
export interface HealthReport {
  schema_version: string; research_only: true; anchor_sequence: number;
  window_start: string; window_end: string; sessions: number; primary_factor_id: string;
  manifests: Record<string, unknown>;
  factors: Array<{
    factor_id: string; metric: string; window: string; unit: string; sessions: number;
    coverage: Partition; sample_size: number; valid_rate: string; excluded_rate: string;
    missing_rate: string; quantiles: { points: string[]; values: string[] } | null;
    status: string; reasons: string[];
  }>;
  rank_correlations: Array<{
    left: string; right: string; paired_sample_size: number; eligible_sessions: number;
    value: string | null; status: string; reasons: string[];
  }>;
}
export interface Lineage {
  anchor_sequence: number; requested_period: { start: string; end: string };
  actual_period: { start: string; end: string }; sessions: string[];
  frames: Array<{
    trade_date: string; publication_id: string; publication_sequence: number;
    market_snapshot_id: string; topic_snapshot_id: string | null; role_snapshot_id: string | null;
    frame_sha256: string; visible_security_ids: string[]; as_of: string;
  }>;
  benchmark: {
    status: 'ready' | 'unavailable'; reason: string | null; definition_sha256: string | null;
    source_id: string | null; source_version: string | null; input_sha256: string | null;
  };
  health_method: Record<string, unknown>; computed_at: string;
}
export interface ResearchResult {
  schema_version: string; research_only: true; config_hash: string; snapshot_id: string;
  manifests: Record<string, unknown>; versions: Record<string, unknown>; metrics: Metrics;
  equity: EquityPoint[]; trades: Fill[]; rejections: Rejection[]; quality: DayQuality[];
  baseline: { status: 'completed'; metrics: Metrics; cost_sensitivity: Array<{
    tier: string; cost_multiplier: string; metrics: Metrics;
  }> };
  folds: Fold[] | null;
  walk_forward: {
    schema_version: string; research_only: true; layout: string; folds_count: number;
    train_sessions: number; test_sessions: number; regime_map: Record<string, unknown>;
    folds: Fold[]; aggregate_equity: EquityPoint[]; aggregate_metrics: Metrics;
  } | null;
  ablation: {
    schema_version: string; research_only: true;
    baseline: { leg: string; status: string; inputs: Record<string, unknown>; metrics: Metrics; reason: null };
    ai_variant: { leg: string; status: 'disabled'; inputs: Record<string, unknown>; metrics: null; reason: string };
  } | null;
  health?: { status: 'ready' | 'unavailable'; reason: string | null; report: HealthReport | null };
  lineage?: Lineage;
}
