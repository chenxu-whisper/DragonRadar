import type { Page, Route } from '@playwright/test';
import type { Detail, Envelope, Interval, Overview, Security, Snapshot } from '../../src/types';

// Deliberately synthetic test-only data; no production module imports this file.
export const ID = '00000000-0000-4000-8000-000000000001';
export const OLD = '00000000-0000-4000-8000-000000000002';
export const sessions = Array.from({ length: 21 }, (_, i) => {
  const date = new Date(Date.UTC(2026, 7, 13));
  let count = 0;
  while (count < i) { date.setUTCDate(date.getUTCDate() + 1); if (![0, 6].includes(date.getUTCDay())) count++; }
  return date.toISOString().slice(0, 10);
});
export const snapshot: Snapshot = {
  snapshot_id: ID, trade_date: '2026-09-10', as_of: '2026-09-10T08:00:00Z',
  source_time: '2026-09-10T07:00:00Z', source: { id: '合成测试来源', version: '测试版一' },
  coverage: { expected: 65, valid: 63, legal_excluded: 1, missing: 1 },
  coverage_scope: 'snapshot_current', analysis_coverage: null,
  units: { currency: ['CNY'], price_unit: ['currency/share'], volume_unit: ['share'], amount_unit: ['currency'] },
  quality_flags: [], rule_version: '测试规则一', basis: 'snapshot_summary',
  manifest: { rule_version: '测试规则一' }, provenance: 'contemporaneous',
  result: { revision: 1, supersedes: null, published_at: '2026-09-10T08:01:00Z', security_count: 65, calendar_sessions: sessions, earliest_date: sessions[0] },
};
export const oldSnapshot: Snapshot = {
  ...snapshot, snapshot_id: OLD, trade_date: '2026-09-09', provenance: 'retrospective',
  result: { ...snapshot.result, revision: 2, supersedes: ID },
};
export const securities: Security[] = Array.from({ length: 65 }, (_, index) => ({
  security_id: `TEST${String(index + 1).padStart(3, '0')}`, name: `合成测试证券${index + 1}`,
  exchange: 'SSE', board: 'main', close: index === 64 ? null : `${10 + index}.1234`,
  change: index === 64 ? null : index % 2 ? '-0.0123' : '0.0234', amount: index === 64 ? null : '9007199254740993.12',
  suspended: index === 63, missing_reasons: index === 64 ? ['missing_price'] : [],
  currency: 'CNY', price_unit: 'currency/share', volume_unit: 'share', amount_unit: 'currency', change_unit: 'fraction',
}));
export function envelope<T>(result: T, id = ID): Envelope<T> {
  return { ...(id === OLD ? oldSnapshot : snapshot), result };
}
export function detail(id: string): Detail {
  const security = securities.find(s => s.security_id === id) || securities[0];
  return {
    security, chart_type: id === 'TEST002' ? 'line' : 'candlestick', price_basis: 'raw_close', basis: 'raw_close',
    history: sessions.map((trade_date, i) => ({
      trade_date, open: `${10 + i}`, high: `${12 + i}`, low: `${9 + i}`,
      close: id === 'TEST002' && i === 8 ? null : `${11 + i}`, volume: '10000', amount: '120000',
      suspended: false, missing_reasons: id === 'TEST002' && i === 8 ? ['missing_price'] : [],
      currency: 'CNY', price_unit: 'currency/share', volume_unit: 'share', amount_unit: 'currency',
    })),
  };
}
export const overview: Overview = {
  advancing: 32, declining: 31, unchanged: 0, coverage: snapshot.coverage,
  returns: securities.slice(0, 63).map(s => ({ security_id: s.security_id, value: s.change!, basis: 'explicit_previous_close', warnings: [] })),
  exclusions: [{ security_id: 'TEST065', reasons: ['missing_price'] }],
};
export function interval(ids?: string[]): Interval {
  return {
    sessions, items: securities.slice(0, 63).filter(s => !ids || ids.includes(s.security_id)).map((s, index) => ({
      security_id: s.security_id, interval_return: index % 2 ? '-0.01' : '0.02',
      maximum_drawdown: '0.034', normalized_curve: sessions.map((_, i) => `${100 + i * (index % 2 ? -0.05 : 0.1)}`),
    })), exclusions: ids ? [] : [{ security_id: 'TEST064', reasons: ['suspension'] }, { security_id: 'TEST065', reasons: ['missing_price'] }],
  };
}
export type MockState = { denied?: boolean; offline?: boolean; empty?: boolean; delayOld?: boolean };
export async function mockMarket(page: Page, state: MockState = {}) {
  await page.route('**/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (state.denied) return respond({ error: 'Source access denied' }, 403);
    if (state.offline) return route.abort('failed');
    if (path === '/api/v1/snapshots') {
      const date = url.searchParams.get('trade_date');
      return respond(state.empty || (date && !['2026-09-10', '2026-09-09'].includes(date)) ? [] : date === '2026-09-09' ? [oldSnapshot] : [snapshot]);
    }
    const id = path.split('/')[4];
    if (id !== ID && id !== OLD) return respond({}, 404);
    if (state.delayOld && id === OLD) await new Promise(resolve => setTimeout(resolve, 600));
    if (path.endsWith('/overview')) return respond(envelope(overview, id));
    if (path.endsWith('/interval')) {
      if (url.searchParams.get('n') === '250') return respond({}, 400);
      const result = interval(url.searchParams.get('ids')?.split(','));
      return respond({ ...envelope(result, id), basis: 'adjusted_close', analysis_coverage: { requested: result.items.length + result.exclusions.length, eligible: result.items.length, excluded: result.exclusions.length } });
    }
    if (path.endsWith('/securities')) {
      const q = url.searchParams.get('q') || '';
      let items = securities.filter(s => `${s.name} ${s.security_id}`.includes(q));
      if (url.searchParams.get('order') === 'desc') items = [...items].reverse();
      const size = +(url.searchParams.get('page_size') || 50), current = +(url.searchParams.get('page') || 1);
      return respond(envelope({ items: items.slice((current - 1) * size, current * size), total: items.length, page: current, page_size: size, basis: 'raw_close' }, id));
    }
    if (path.includes('/securities/')) return respond(envelope(detail(decodeURIComponent(path.split('/').at(-1)!)), id));
    return respond(id === OLD ? oldSnapshot : snapshot);
  });
}
