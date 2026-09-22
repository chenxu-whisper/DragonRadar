type ObjectValue = Record<string, unknown>;
export type AnalysisValidationError = 'malformed' | 'date_mismatch' | 'security_mismatch';

function record(value: unknown): value is ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function dateValue(value: unknown): value is string {
  return typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value));
}

function finiteOrNull(value: unknown): boolean {
  return value === null || (typeof value === 'number' && Number.isFinite(value));
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string');
}

function coverage(value: unknown): boolean {
  if (!record(value)) return false;
  const fields = ['expected', 'valid', 'excluded', 'missing'] as const;
  return fields.every(key => Number.isSafeInteger(value[key]) && (value[key] as number) >= 0) &&
    value.expected === (value.valid as number) + (value.excluded as number) + (value.missing as number);
}

function envelope(value: unknown): value is ObjectValue {
  if (!record(value) || !record(value.basis)) return false;
  const basis = value.basis;
  return dateValue(value.trade_date) &&
    typeof value.as_of === 'string' && Number.isFinite(Date.parse(value.as_of)) &&
    typeof value.snapshot_id === 'string' &&
    typeof value.source === 'string' &&
    typeof value.source_version === 'string' &&
    coverage(value.coverage) &&
    strings(value.quality_flags) &&
    typeof value.rule_version === 'string' &&
    ['unit', 'currency', 'price_type', 'benchmark'].every(
      key => basis[key] === null || typeof basis[key] === 'string',
    );
}

function unavailable(value: ObjectValue): boolean {
  return value.data === null && typeof value.reason === 'string' && value.reason.length > 0;
}

function basisMatches(value: ObjectValue, query: URLSearchParams, benchmark: boolean): boolean {
  if (!record(value.basis)) return false;
  const adjustment = query.get('adj') || 'qfq';
  const requestedBenchmark = query.get('benchmark') || 'B000300';
  return value.basis.price_type === adjustment &&
    (!benchmark || value.basis.benchmark === requestedBenchmark);
}

function requestedWindow(query: URLSearchParams, start: string, end: string): AnalysisValidationError | null {
  const requestedStart = query.get('start');
  const requestedEnd = query.get('end');
  if (!dateValue(start) || !dateValue(end) || start > end) return 'malformed';
  if (!requestedStart || !requestedEnd || start < requestedStart || end > requestedEnd) return 'date_mismatch';
  return null;
}

function exclusions(value: unknown): boolean {
  return Array.isArray(value) && value.every(item =>
    record(item) && typeof item.security_id === 'string' && strings(item.reasons),
  );
}

export function validateRankResponse(value: unknown, query: URLSearchParams): AnalysisValidationError | null {
  if (!envelope(value)) return 'malformed';
  if (!basisMatches(value, query, true)) return 'malformed';
  if (unavailable(value)) return null;
  if (!record(value.data)) return 'malformed';
  const data = value.data;
  if (!dateValue(data.adjusted_start) || !dateValue(data.adjusted_end)) return 'malformed';
  const adjustedStart = data.adjusted_start;
  const adjustedEnd = data.adjusted_end;
  const mismatch = requestedWindow(query, adjustedStart, adjustedEnd);
  if (mismatch) return mismatch;
  if (value.trade_date !== adjustedEnd) return 'date_mismatch';
  if (!Number.isSafeInteger(data.window_days) || (data.window_days as number) < 0 ||
    !finiteOrNull(data.benchmark_return) ||
    !Array.isArray(data.ranking) ||
    !exclusions(data.exclusions)) return 'malformed';
  const ids = new Set<string>();
  for (const item of data.ranking) {
    if (!record(item) ||
      !(item.rank === null || (Number.isSafeInteger(item.rank) && (item.rank as number) >= 1)) ||
      typeof item.security_id !== 'string' || !item.security_id ||
      (item.name !== null && typeof item.name !== 'string') ||
      !Array.isArray(item.topics) ||
      !item.topics.every(topic => record(topic) && typeof topic.topic_id === 'string' && typeof topic.name === 'string') ||
      typeof item.topic_status !== 'string' || !strings(item.topic_reasons) || !strings(item.reasons) ||
      !['return_ratio', 'excess_return_ratio', 'max_drawdown_ratio', 'amount_change_ratio']
        .every(key => finiteOrNull(item[key])) ||
      ids.has(item.security_id)) return 'malformed';
    ids.add(item.security_id);
  }
  return null;
}

export function validateCurvesResponse(value: unknown, query: URLSearchParams): AnalysisValidationError | null {
  if (!envelope(value)) return 'malformed';
  if (!basisMatches(value, query, true)) return 'malformed';
  if (unavailable(value)) return null;
  if (!record(value.data)) return 'malformed';
  const data = value.data;
  if (!dateValue(data.adjusted_start) || !dateValue(data.adjusted_end)) return 'malformed';
  const adjustedStart = data.adjusted_start;
  const adjustedEnd = data.adjusted_end;
  const mismatch = requestedWindow(query, adjustedStart, adjustedEnd);
  if (mismatch) return mismatch;
  if (value.trade_date !== adjustedEnd ||
    typeof data.benchmark !== 'string' ||
    !Array.isArray(data.curves) ||
    !exclusions(data.exclusions)) return 'malformed';
  let expectedDates: string[] | undefined;
  const ids = new Set<string>();
  for (const curve of data.curves) {
    if (!record(curve) || typeof curve.security_id !== 'string' || ids.has(curve.security_id) ||
      !strings(curve.reasons) || !Array.isArray(curve.points)) return 'malformed';
    ids.add(curve.security_id);
    const dates: string[] = [];
    for (const point of curve.points) {
      if (!record(point) || !dateValue(point.trade_date) ||
        point.trade_date < adjustedStart || point.trade_date > adjustedEnd ||
        !finiteOrNull(point.normalized) || !finiteOrNull(point.drawdown)) return 'date_mismatch';
      dates.push(point.trade_date);
    }
    if (dates.some((day, index) => index > 0 && day <= dates[index - 1])) return 'date_mismatch';
    if (expectedDates && dates.join(',') !== expectedDates.join(',')) return 'date_mismatch';
    expectedDates = dates;
  }
  const requestedIds = new Set((query.get('symbols') || '').split(',').filter(Boolean));
  const returnedIds = new Set(ids);
  for (const item of data.exclusions as ObjectValue[]) returnedIds.add(item.security_id as string);
  if (requestedIds.size !== returnedIds.size ||
    [...requestedIds].some(securityId => !returnedIds.has(securityId))) return 'malformed';
  return null;
}

export function validateKlineResponse(
  value: unknown,
  securityId: string,
  query: URLSearchParams,
): AnalysisValidationError | null {
  if (!envelope(value)) return 'malformed';
  if (!basisMatches(value, query, false)) return 'malformed';
  if (unavailable(value)) return null;
  if (!record(value.data)) return 'malformed';
  const data = value.data;
  if (data.security_id !== securityId) return 'security_mismatch';
  if (!dateValue(data.adjusted_start) || !dateValue(data.adjusted_end)) return 'malformed';
  const adjustedStart = data.adjusted_start;
  const adjustedEnd = data.adjusted_end;
  const mismatch = requestedWindow(query, adjustedStart, adjustedEnd);
  if (mismatch) return mismatch;
  if (value.trade_date !== adjustedEnd ||
    !['raw', 'qfq', 'hfq'].includes(data.adjustment as string) ||
    data.adjustment !== (query.get('adj') || 'qfq') ||
    !['candlestick', 'line'].includes(data.chart_type as string) ||
    !Array.isArray(data.points)) return 'malformed';
  let previous = '';
  for (const point of data.points) {
    if (!record(point) || !dateValue(point.trade_date) ||
      point.trade_date <= previous ||
      point.trade_date < adjustedStart || point.trade_date > adjustedEnd ||
      !['open', 'high', 'low', 'close', 'volume', 'amount'].every(key => finiteOrNull(point[key]))) {
      return point && record(point) && dateValue(point.trade_date) ? 'date_mismatch' : 'malformed';
    }
    previous = point.trade_date;
  }
  return null;
}
