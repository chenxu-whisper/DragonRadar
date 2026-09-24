import type { WorkbenchCoverage, WorkbenchEnvelope } from '../../types';

export type CompletenessState = 'complete' | 'partial' | 'missing';
export type SourceCheck = 'pass' | 'unknown';
export type CalculationState =
  | 'pending'
  | 'idle'
  | 'approaching_alert'
  | 'triggered';
export type ReviewSwitch = 'pending' | 'approved' | 'rejected';
export type EventType =
  | 'abnormal'
  | 'serious_abnormal'
  | 'monitoring'
  | 'restriction';
export type EventDisplayState = 'disclosed' | 'monitoring' | 'restricted';

export interface RegulationCompleteness extends WorkbenchCoverage {
  status: CompletenessState;
  coverage_ratio: number | null;
  missing_count: number | null;
  source_check: SourceCheck;
}

export interface RegulationCalculation {
  rule_id: string;
  rule_version: string;
  security_id: string;
  trade_date: string;
  window_days: number;
  direction: 'upper' | 'lower';
  threshold: number;
  warning_threshold: number;
  review_switch: ReviewSwitch;
  benchmark_index: string | null;
  recompute_condition: string | null;
  computed_value: number | null;
  state: CalculationState;
  reasons: string[];
  official_source_url: string | null;
  effective_from: string;
  effective_to: string | null;
  production_enabled: boolean;
  production_gate_reasons: string[];
}

export interface RegulationEvent {
  event_id: string;
  rule_id: string;
  rule_version: string;
  security_id: string;
  event_type: EventType;
  display_state: EventDisplayState;
  announcement_id: string | null;
  announcement_url: string | null;
  effective_from: string;
  effective_to: string | null;
}

export interface RegulationStatus {
  region1_completeness: RegulationCompleteness;
  region2_calc_state: RegulationCalculation[];
  region3_events: RegulationEvent[] | null;
}

export interface RegulationEvents {
  events: RegulationEvent[] | null;
  total: number | null;
  source_check: SourceCheck;
}

export type RegulationStatusResponse = Omit<
  WorkbenchEnvelope<RegulationStatus | null>,
  'rule_version' | 'coverage'
> & {
  rule_version: string | null;
  coverage: WorkbenchCoverage | null;
};

export type RegulationEventsResponse = Omit<
  WorkbenchEnvelope<RegulationEvents | null>,
  'rule_version' | 'coverage'
> & {
  rule_version: string | null;
  coverage: WorkbenchCoverage | null;
};
