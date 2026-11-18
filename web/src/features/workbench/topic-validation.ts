const object = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown) => typeof value === 'string' && value.trim().length > 0;
const nullableText = (value: unknown) => value === null || text(value);
const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const nullableNumber = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value));
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const coverage = (value: unknown) => object(value) && ['expected', 'valid', 'excluded', 'missing'].every(key => count(value[key])) &&
  value.expected === Number(value.valid) + Number(value.excluded) + Number(value.missing);
const ratio = (value: unknown) => object(value) && count(value.numerator) && count(value.denominator) &&
  Number(value.numerator) <= Number(value.denominator) && nullableNumber(value.value) &&
  (value.value === null || (Number(value.value) >= 0 && Number(value.value) <= 1)) && nullableText(value.reason);

export function validateIndexOverview(value: unknown, date: string): string | undefined {
  if (!object(value)) return 'malformed_response';
  if (value.trade_date !== date) return 'date_mismatch';
  if (value.data === null) return strings(value.quality_flags) && value.quality_flags.includes('materialization_unavailable') ? undefined : 'malformed_response';
  const data = value.data;
  if (!object(data) || !Array.isArray(data.indexes) || !object(data.factors) || !object(data.factors['FAC-M03']) ||
    !Array.isArray(data.factors['FAC-M03'].indexes)) return 'malformed_response';
  for (const index of data.indexes) {
    if (!object(index) || !text(index.security_id) || !text(index.name) || !nullableText(index.code) ||
      !['last', 'change_ratio', 'amount'].every(key => nullableNumber(index[key])) || !nullableText(index.reason)) return 'malformed_response';
  }
  for (const factor of data.factors['FAC-M03'].indexes) {
    if (!object(factor) || !text(factor.security_id) || !text(factor.name) || !text(factor.volatility_method) ||
      !['close_over_ma20', 'close_over_ma60', 'volatility_20'].every(key => nullableNumber(factor[key])) ||
      !['close_over_ma20_reason', 'close_over_ma60_reason', 'volatility_20_reason'].every(key => nullableText(factor[key])) ||
      !object(factor.coverage) || !['ma20', 'ma60', 'return20'].every(key => coverage((factor.coverage as Record<string, unknown>)[key]))) return 'malformed_response';
  }
  return undefined;
}

export function validateTopicDetail(value: unknown, sectorId: string, query: URLSearchParams): string | undefined {
  if (!object(value)) return 'malformed_response';
  if (value.trade_date !== query.get('date')) return 'selection_mismatch';
  if (value.data === null) return Array.isArray(value.quality_flags) && value.quality_flags.includes('materialization_unavailable') ? undefined : 'malformed_response';
  const data = value.data;
  if (!object(data) || !object(data.sector)) return 'malformed_response';
  if (data.sector.sector_id !== sectorId) return 'selection_mismatch';
  if (data.page !== Number(query.get('page') || 1) || data.size !== Number(query.get('size') || 50)) return 'selection_mismatch';
  const sector = data.sector, basis = data.topic_basis, definition = data.definition, membersCoverage = data.membership_coverage;
  if (!text(sector.name) || !count(sector.member_count) || !count(sector.valid_returns) ||
    !['median_return', 'change_ratio', 'limit_up_count', 'net_inflow'].every(key => nullableNumber(sector[key])) ||
    !nullableText(sector.net_inflow_reason) || !strings(sector.quality_flags) || !coverage(sector.coverage) ||
    !ratio(sector.up_ratio) || !ratio(sector.limit_up_ratio) ||
    !object(basis) || !['source', 'source_version', 'as_of', 'source_time'].every(key => nullableText(basis[key])) ||
    !['cutoff', 'market_snapshot_id', 'topic_snapshot_id', 'taxonomy_version', 'membership_version'].every(key => text(basis[key])) ||
    !object(definition) || !text(definition.effective_from) || !text(definition.available_at) ||
    !nullableText(definition.effective_to) || !nullableText(definition.source_reference) ||
    !object(membersCoverage) || !['expected', 'received'].every(key => membersCoverage[key] === null || count(membersCoverage[key])) || !count(membersCoverage.known) ||
    !['complete', 'unavailable'].includes(String(membersCoverage.status)) || !nullableText(membersCoverage.reason) || !nullableText(membersCoverage.complete_as_of) ||
    !count(data.total) || !Array.isArray(data.members)) return 'malformed_response';
  if (membersCoverage.known !== data.total ||
    (membersCoverage.expected !== null && (Number(membersCoverage.received) > Number(membersCoverage.expected) || Number(membersCoverage.known) > Number(membersCoverage.expected))) ||
    (membersCoverage.complete_as_of === null && (membersCoverage.expected !== null || membersCoverage.received !== null)) ||
    (membersCoverage.complete_as_of !== null && (membersCoverage.expected === null || membersCoverage.received === null)) ||
    (membersCoverage.status === 'complete' && (membersCoverage.complete_as_of === null || membersCoverage.reason !== null ||
      membersCoverage.expected !== membersCoverage.received || membersCoverage.expected !== membersCoverage.known))) return 'malformed_response';
  if (data.members.length !== Math.min(Number(data.size), Math.max(0, Number(data.total) - (Number(data.page) - 1) * Number(data.size)))) return 'malformed_response';
  const ids = new Set<string>();
  for (const member of data.members) {
    if (!object(member) || !text(member.security_id) || !nullableText(member.code) || !nullableText(member.name) ||
      !text(member.effective_from) || !nullableText(member.effective_to) || !text(member.available_at) || !strings(member.evidence_refs)) return 'malformed_response';
    if (ids.has(member.security_id as string)) return 'malformed_response';
    ids.add(member.security_id as string);
  }
  return undefined;
}
