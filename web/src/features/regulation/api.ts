import Decimal from 'decimal.js';

import type {
  RegulationCalculation,
  RegulationEvent,
} from './types';

type Failure = 'malformed_response' | 'selection_mismatch' | null;
type Selection = { date: string; snapshot: string };
type ObjectValue = Record<string, unknown>;

const CALCULATION_STATES = ['pending', 'idle', 'approaching_alert', 'triggered'];
const EVENT_TYPES = ['abnormal', 'serious_abnormal', 'monitoring', 'restriction'];
const EVENT_DISPLAY_STATES: Record<string, string> = {
  abnormal: 'disclosed',
  serious_abnormal: 'disclosed',
  monitoring: 'monitoring',
  restriction: 'restricted',
};

function record(value: unknown): value is ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string');
}

function integer(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function nullableText(value: unknown): boolean {
  return value === null || typeof value === 'string';
}

function coverage(value: unknown): value is ObjectValue {
  if (!record(value)) return false;
  const fields = ['expected', 'valid', 'excluded', 'missing'] as const;
  return fields.every(key => integer(value[key])) &&
    value.expected === (value.valid as number) +
      (value.excluded as number) + (value.missing as number);
}

function envelope(value: unknown, selection: Selection): Failure {
  if (!record(value) ||
    value.snapshot_id !== selection.snapshot ||
    value.trade_date !== selection.date) return 'selection_mismatch';
  if (
    typeof value.as_of !== 'string' || !Number.isFinite(Date.parse(value.as_of)) ||
    typeof value.source !== 'string' ||
    typeof value.source_version !== 'string' ||
    !strings(value.quality_flags) ||
    (value.rule_version !== null && typeof value.rule_version !== 'string') ||
    !record(value.basis) ||
    (value.coverage !== null && !coverage(value.coverage))
  ) return 'malformed_response';
  return null;
}

function event(value: unknown): value is RegulationEvent {
  if (!record(value)) return false;
  const hasLegacySource = (
    (value.source_id === undefined || value.source_id === null) &&
    (value.source_version === undefined || value.source_version === null)
  );
  const hasVersionedSource = (
    typeof value.source_id === 'string' && Boolean(value.source_id.trim()) &&
    typeof value.source_version === 'string' && Boolean(value.source_version.trim())
  );
  return ['event_id', 'rule_id', 'rule_version', 'security_id'].every(
    key => typeof value[key] === 'string' && Boolean((value[key] as string).trim()),
  ) &&
    EVENT_TYPES.includes(String(value.event_type)) &&
    value.display_state === EVENT_DISPLAY_STATES[String(value.event_type)] &&
    nullableText(value.announcement_id) &&
    (value.announcement_url === null ||
      (typeof value.announcement_url === 'string' &&
        value.announcement_url.startsWith('https://'))) &&
    typeof value.effective_from === 'string' &&
    nullableText(value.effective_to) &&
    (hasLegacySource || hasVersionedSource);
}

function calculationState(value: ObjectValue): boolean {
  const direction = String(value.direction);
  const threshold = new Decimal(value.threshold as number);
  const warning = new Decimal(value.warning_threshold as number);
  if (
    (direction === 'upper' && !warning.lessThan(threshold)) ||
    (direction === 'lower' && !warning.greaterThan(threshold))
  ) return false;

  const reasons = value.reasons as string[];
  const gateReasons = value.production_gate_reasons as string[];
  if (value.state === 'pending') {
    return reasons.length > 0 && (
      value.computed_value === null ||
      value.review_switch !== 'approved' ||
      value.production_enabled !== true ||
      gateReasons.length > 0
    );
  }
  if (
    value.review_switch !== 'approved' ||
    value.production_enabled !== true ||
    gateReasons.length > 0 ||
    reasons.length > 0 ||
    value.computed_value === null
  ) return false;

  const computed = new Decimal(value.computed_value as number);
  const expected = direction === 'upper'
    ? computed.greaterThanOrEqualTo(threshold)
      ? 'triggered'
      : computed.greaterThanOrEqualTo(warning)
        ? 'approaching_alert'
        : 'idle'
    : computed.lessThanOrEqualTo(threshold)
      ? 'triggered'
      : computed.lessThanOrEqualTo(warning)
        ? 'approaching_alert'
        : 'idle';
  return value.state === expected;
}

function calculation(value: unknown, selection: Selection): value is RegulationCalculation {
  if (!record(value)) return false;
  return ['rule_id', 'rule_version', 'security_id'].every(
    key => typeof value[key] === 'string' && Boolean((value[key] as string).trim()),
  ) &&
    value.trade_date === selection.date &&
    integer(value.window_days) && value.window_days > 0 &&
    ['upper', 'lower'].includes(String(value.direction)) &&
    finite(value.threshold) &&
    finite(value.warning_threshold) &&
    ['pending', 'approved', 'rejected'].includes(String(value.review_switch)) &&
    nullableText(value.benchmark_index) &&
    nullableText(value.recompute_condition) &&
    (value.computed_value === null || finite(value.computed_value)) &&
    CALCULATION_STATES.includes(String(value.state)) &&
    strings(value.reasons) &&
    (value.official_source_url === null ||
      (typeof value.official_source_url === 'string' &&
        value.official_source_url.startsWith('https://'))) &&
    typeof value.effective_from === 'string' &&
    nullableText(value.effective_to) &&
    typeof value.production_enabled === 'boolean' &&
    strings(value.production_gate_reasons) &&
    calculationState(value);
}

function completeness(value: unknown): boolean {
  if (!record(value) ||
    !['complete', 'partial', 'missing'].includes(String(value.status)) ||
    !['pass', 'unknown'].includes(String(value.source_check))) return false;
  if (value.source_check === 'unknown') {
    return value.status === 'missing' &&
      ['coverage_ratio', 'missing_count', 'expected', 'valid', 'excluded', 'missing']
        .every(key => value[key] === null);
  }
  if (!coverage(value) ||
    !finite(value.coverage_ratio) ||
    value.coverage_ratio < 0 ||
    value.coverage_ratio > 1 ||
    !integer(value.missing_count) ||
    value.missing_count !== value.missing) return false;
  const expectedStatus = value.missing === 0
    ? 'complete'
    : (value.valid as number) + (value.excluded as number) === 0
      ? 'missing'
      : 'partial';
  return value.status === expectedStatus;
}

export function regulationStatusPath(date: string, snapshot: string): string {
  return `/api/regulation/status?${new URLSearchParams({
    date,
    snapshot_id: snapshot,
  })}`;
}

export function regulationEventsPath(date: string, snapshot: string): string {
  return `/api/regulation/events?${new URLSearchParams({
    date,
    snapshot_id: snapshot,
  })}`;
}

export function validateRegulationStatusResponse(
  value: unknown,
  selection: Selection,
): Failure {
  const failure = envelope(value, selection);
  if (failure) return failure;
  const data = (value as ObjectValue).data;
  if (data === null) return null;
  if (!record(data) ||
    !completeness(data.region1_completeness) ||
    !Array.isArray(data.region2_calc_state) ||
    !data.region2_calc_state.every(item => calculation(item, selection)) ||
    !(data.region3_events === null ||
      (Array.isArray(data.region3_events) && data.region3_events.every(event)))) {
    return 'malformed_response';
  }
  return null;
}

export function validateRegulationEventsResponse(
  value: unknown,
  selection: Selection,
): Failure {
  const failure = envelope(value, selection);
  if (failure) return failure;
  const data = (value as ObjectValue).data;
  if (data === null) return null;
  if (!record(data) || !['pass', 'unknown'].includes(String(data.source_check))) {
    return 'malformed_response';
  }
  if (data.source_check === 'unknown') {
    if (data.events === null) {
      return data.listed_count === null && data.total === null
        ? null
        : 'malformed_response';
    }
    return Array.isArray(data.events) &&
      data.events.every(event) &&
      integer(data.listed_count) &&
      data.listed_count === data.events.length &&
      data.total === null
      ? null
      : 'malformed_response';
  }
  return Array.isArray(data.events) &&
    data.events.every(event) &&
    integer(data.listed_count) &&
    data.listed_count === data.events.length &&
    integer(data.total) &&
    data.total === data.events.length
    ? null
    : 'malformed_response';
}

export function validateRegulationResponse(
  value: unknown,
  resource: string,
  selection: Selection,
): Failure {
  return resource === 'status'
    ? validateRegulationStatusResponse(value, selection)
    : validateRegulationEventsResponse(value, selection);
}
