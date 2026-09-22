type ObjectValue = Record<string, unknown>;
export type ResetStockValidationError = 'malformed' | 'date_mismatch' | 'security_mismatch';

function record(value: unknown): value is ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function nullableStrings(value: ObjectValue, keys: string[]): boolean {
  return keys.every(key => value[key] === null || typeof value[key] === 'string');
}

function nullableFiniteNumber(value: unknown): boolean {
  return value === null || (typeof value === 'number' && Number.isFinite(value));
}

function nullableNonnegativeInteger(value: unknown): boolean {
  return value === null || (Number.isSafeInteger(value) && (value as number) >= 0);
}

export function validateResetStockResponse(
  data: unknown,
  id: string,
  tradeDate: string | null,
): ResetStockValidationError | null {
  if (!record(data)) return 'malformed';
  if (!record(data.coverage) || !record(data.basis) || !record(data.data)) return 'malformed';
  const coverage = data.coverage;
  const basis = data.basis;
  const payload = data.data;
  const coverageFields = ['expected', 'valid', 'excluded', 'missing'] as const;
  if (
    typeof data.trade_date !== 'string' ||
    typeof data.as_of !== 'string' || !Number.isFinite(Date.parse(data.as_of)) ||
    typeof data.snapshot_id !== 'string' ||
    typeof data.source !== 'string' ||
    typeof data.source_version !== 'string' ||
    !Array.isArray(data.quality_flags) || data.quality_flags.some(value => typeof value !== 'string') ||
    typeof data.rule_version !== 'string' ||
    !coverageFields.every(key => Number.isSafeInteger(coverage[key]) && (coverage[key] as number) >= 0) ||
    coverage.expected !== (coverage.valid as number) + (coverage.excluded as number) + (coverage.missing as number) ||
    !nullableStrings(basis, ['unit', 'currency', 'price_type', 'benchmark'])
  ) return 'malformed';
  if (tradeDate && data.trade_date !== tradeDate) return 'date_mismatch';
  if (!record(payload.quote) || !record(payload.seal) || !record(payload.regulation)) return 'malformed';
  const quote = payload.quote;
  const seal = payload.seal;
  const regulation = payload.regulation;
  if (
    typeof payload.security_id !== 'string' ||
    (payload.name !== null && typeof payload.name !== 'string') ||
    !['last', 'change_ratio', 'amount'].every(key => nullableFiniteNumber(quote[key])) ||
    !Array.isArray(payload.roles) ||
    !payload.roles.every(item => record(item) &&
      typeof item.role_type === 'string' &&
      typeof item.evidence_status === 'string' &&
      typeof item.sector_id === 'string') ||
    !nullableStrings(seal, ['first_seal_time', 'last_seal_time']) ||
    !nullableNonnegativeInteger(seal.break_count) ||
    !nullableFiniteNumber(seal.seal_amount) ||
    (seal.reason !== undefined && seal.reason !== null && typeof seal.reason !== 'string') ||
    !Number.isSafeInteger(regulation.active_events) ||
    (regulation.active_events as number) < 0
  ) return 'malformed';
  return payload.security_id === id ? null : 'security_mismatch';
}
