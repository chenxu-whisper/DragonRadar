import type {
  RegulationCalculation,
  RegulationEvent,
} from './types';

type Failure = 'malformed_response' | 'selection_mismatch' | null;
type Selection = { date: string; snapshot: string };
type ObjectValue = Record<string, unknown>;

const CALCULATION_STATES = ['pending', 'idle', 'approaching_alert', 'triggered'];
const EVENT_TYPES = ['abnormal', 'serious_abnormal', 'monitoring', 'restriction'];
const EVENT_STATES = ['disclosed', 'monitoring', 'restricted'];

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
  return ['event_id', 'rule_id', 'rule_version', 'security_id'].every(
    key => typeof value[key] === 'string' && Boolean((value[key] as string).trim()),
  ) &&
    EVENT_TYPES.includes(String(value.event_type)) &&
    EVENT_STATES.includes(String(value.display_state)) &&
    nullableText(value.announcement_id) &&
    (value.announcement_url === null ||
      (typeof value.announcement_url === 'string' &&
        value.announcement_url.startsWith('https://'))) &&
    typeof value.effective_from === 'string' &&
    nullableText(value.effective_to);
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
    strings(value.production_gate_reasons);
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
    return data.events === null && data.total === null
      ? null
      : 'malformed_response';
  }
  return Array.isArray(data.events) &&
    data.events.every(event) &&
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
