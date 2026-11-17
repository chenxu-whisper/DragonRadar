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

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
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

function exclusionIds(value: unknown): Set<string> | null {
  if (!Array.isArray(value)) return null;
  const ids = new Set<string>();
  for (const item of value) {
    if (!record(item) || typeof item.security_id !== 'string' || !item.security_id ||
      !Array.isArray(item.reasons) || item.reasons.length === 0 ||
      !item.reasons.every(reason => typeof reason === 'string' && reason.length > 0) ||
      ids.has(item.security_id)) return null;
    ids.add(item.security_id);
  }
  return ids;
}

const rankFields = {
  return: ['return_ratio', true],
  excess_return: ['excess_return_ratio', true],
  drawdown: ['max_drawdown_ratio', false],
  amount_change: ['amount_change_ratio', true],
} as const;

function nearlyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= Number.EPSILON *
    Math.max(1, Math.abs(left), Math.abs(right)) * 8;
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
    !(data.benchmark_return === null || (finite(data.benchmark_return) && data.benchmark_return > -1)) ||
    !Array.isArray(data.ranking) ||
    !Array.isArray(data.exclusions)) return 'malformed';
  const excludedIds = exclusionIds(data.exclusions);
  const sort = query.get('sort') || 'return';
  const rankField = rankFields[sort as keyof typeof rankFields];
  if (!excludedIds || !rankField || !record(value.coverage) ||
    data.ranking.length + data.exclusions.length !== value.coverage.expected) {
    return 'malformed';
  }
  const ids = new Set<string>();
  for (const item of data.ranking) {
    if (!record(item) ||
      !(item.rank === null || (Number.isSafeInteger(item.rank) && (item.rank as number) >= 1)) ||
      typeof item.security_id !== 'string' || !item.security_id ||
      (item.name !== null && typeof item.name !== 'string') ||
      !Array.isArray(item.topics) ||
      !item.topics.every(topic => record(topic) && typeof topic.topic_id === 'string' && typeof topic.name === 'string') ||
      typeof item.topic_status !== 'string' || !strings(item.topic_reasons) || !strings(item.reasons) ||
      !finite(item.return_ratio) || item.return_ratio <= -1 ||
      !finiteOrNull(item.excess_return_ratio) ||
      !(item.max_drawdown_ratio === null ||
        (finite(item.max_drawdown_ratio) && item.max_drawdown_ratio >= 0 && item.max_drawdown_ratio < 1)) ||
      !(item.amount_change_ratio === null ||
        (finite(item.amount_change_ratio) && item.amount_change_ratio >= -1)) ||
      ids.has(item.security_id) || excludedIds.has(item.security_id)) return 'malformed';
    if (data.benchmark_return === null
      ? item.excess_return_ratio !== null
      : !finite(item.excess_return_ratio) ||
        !nearlyEqual(item.excess_return_ratio, item.return_ratio - data.benchmark_return)) {
      return 'malformed';
    }
    ids.add(item.security_id);
  }
  const [field, descending] = rankField;
  let previousValue: number | undefined;
  let reachedNull = false;
  let valid = 0;
  for (const [index, item] of (data.ranking as ObjectValue[]).entries()) {
    const current = item[field];
    if (current === null) {
      if (item.rank !== null) return 'malformed';
      reachedNull = true;
      continue;
    }
    if (reachedNull || !finite(current) || item.rank === null) return 'malformed';
    valid += 1;
    if (previousValue !== undefined) {
      if ((descending && current > previousValue) || (!descending && current < previousValue)) {
        return 'malformed';
      }
    }
    const expectedRank = previousValue === current
      ? (data.ranking[index - 1] as ObjectValue).rank
      : index + 1;
    if (item.rank !== expectedRank) return 'malformed';
    previousValue = current;
  }
  if (valid !== value.coverage.valid ||
    data.ranking.length - valid + data.exclusions.length !==
      (value.coverage.excluded as number) + (value.coverage.missing as number)) {
    return 'malformed';
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
    !Array.isArray(data.exclusions)) return 'malformed';
  const excludedIds = exclusionIds(data.exclusions);
  if (!excludedIds) return 'malformed';
  let expectedDates: string[] | undefined;
  const ids = new Set<string>();
  for (const curve of data.curves) {
    if (!record(curve) || typeof curve.security_id !== 'string' || !curve.security_id ||
      ids.has(curve.security_id) || excludedIds.has(curve.security_id) ||
      !strings(curve.reasons) || !Array.isArray(curve.points) || curve.points.length === 0) return 'malformed';
    ids.add(curve.security_id);
    const dates: string[] = [];
    for (const point of curve.points) {
      if (!record(point) || !dateValue(point.trade_date)) return 'malformed';
      if (point.trade_date < adjustedStart || point.trade_date > adjustedEnd) return 'date_mismatch';
      if ((point.normalized === null) !== (point.drawdown === null) ||
        !(point.normalized === null ||
          (finite(point.normalized) && point.normalized > 0 &&
            finite(point.drawdown) && point.drawdown >= 0 && point.drawdown < 1))) {
        return 'malformed';
      }
      dates.push(point.trade_date);
    }
    const first = curve.points[0] as ObjectValue;
    if (first.normalized !== 100 || first.drawdown !== 0) return 'malformed';
    if (dates.some((day, index) => index > 0 && day <= dates[index - 1])) return 'date_mismatch';
    if (dates[0] !== adjustedStart || dates[dates.length - 1] !== adjustedEnd) return 'date_mismatch';
    if (expectedDates && dates.join(',') !== expectedDates.join(',')) return 'date_mismatch';
    expectedDates = dates;
  }
  const requestedIds = new Set((query.get('symbols') || '').split(',').filter(Boolean));
  const returnedIds = new Set(ids);
  for (const securityId of excludedIds) returnedIds.add(securityId);
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
    if (!record(point) || !dateValue(point.trade_date)) return 'malformed';
    if (point.trade_date <= previous ||
      point.trade_date < adjustedStart || point.trade_date > adjustedEnd) return 'date_mismatch';
    if (!['volume', 'amount'].every(key =>
      point[key] === null || (finite(point[key]) && point[key] >= 0))) return 'malformed';
    const openHighLow = ['open', 'high', 'low'] as const;
    const gap = [...openHighLow, 'close'].every(key => point[key] === null);
    if (!gap) {
      const closeOnly = openHighLow.every(key => point[key] === null);
      const complete = openHighLow.every(key => finite(point[key]));
      if (!finite(point.close) || point.close <= 0 ||
        (!closeOnly && !complete) ||
        (data.chart_type === 'candlestick' && !complete)) return 'malformed';
      if (complete) {
        const open = point.open as number;
        const high = point.high as number;
        const low = point.low as number;
        if (open <= 0 || high <= 0 || low <= 0 ||
          low > Math.min(open, point.close) ||
          Math.max(open, point.close) > high) return 'malformed';
      }
    }
    previous = point.trade_date;
  }
  return null;
}
