export const PUB = '00000000-0000-4000-8000-000000000091';
export const DAY = '2026-09-10';
export const SESSIONS = ['2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09', DAY];
export const selection = { securityId: 'IDX', snapshot: PUB, date: DAY, start: SESSIONS[0], end: DAY };
export const coverage = { expected: 6, valid: 5, excluded: 0, missing: 1 };
export function envelope<T>(data: T) {
  return { snapshot_id: PUB, trade_date: DAY, as_of: `${DAY}T10:00:00Z`, source: 'synthetic',
    source_version: 's1', rule_version: 'r1', quality_flags: [],
    basis: { unit: 'point', currency: null, price_type: 'raw', benchmark: null }, coverage: { ...coverage }, data };
}
export function history() {
  const source = { source_id: 'synthetic', source_version: 's1', market_snapshot_id: '00000000-0000-4000-8000-000000000001',
    source_archive_id: '00000000-0000-4000-8000-000000000090', source_archive_digest: 'a'.repeat(64),
    calendar_version: 'c1', as_of: `${DAY}T10:00:00Z`, provenance: 'retrospective' };
  return envelope({
    security_id: 'IDX', identity: { security_id: 'IDX', security_type: 'index', name: '合成指数',
      code: '000300', source_id: 'synthetic', source_version: 's1', pool_version: 'p1',
      exchange: 'SSE', listing_date: '2000-01-01', suspended: false, effective_from: '2000-01-01',
      effective_to: null, available_at: '2026-09-03T09:00:00Z' },
    index_basis: { series_type: 'price', series_reference: 'synthetic-price-v1', price_unit: 'point',
      amount_currency: null, volume_unit: 'not_applicable' },
    price_type: 'raw', adjustment_version: 'raw-index-v1',
    requested: { start: SESSIONS[0], end: DAY }, adjusted: { start: SESSIONS[0], end: DAY },
    available: { start: SESSIONS[0], end: DAY }, archive: { start: SESSIONS[0], end: DAY },
    chart_type: 'line', coverage: { ...coverage }, source,
    points: SESSIONS.map((day, index) => ({
      trade_date: day, open: null, high: null, low: null,
      close: index === 2 ? null : '1234.123456789012345678901',
      volume: null, amount: null, available_at: index === 2 ? null : `${day}T09:00:00Z`,
      source_id: 'synthetic', source_version: 's1', reason: index === 2 ? 'observation_missing' : null,
      chart_type: index === 2 ? 'gap' : 'line', provenance: 'retrospective',
    })),
  });
}
