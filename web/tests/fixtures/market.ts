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
export function resetSummary(value: Snapshot = snapshot) {
  return {
    trade_date: value.trade_date,
    as_of: value.as_of,
    snapshot_id: value.snapshot_id,
    source: value.source.id,
    source_version: value.source.version,
    coverage: { ...value.coverage, excluded: value.coverage.legal_excluded },
    quality_flags: value.quality_flags,
    rule_version: value.rule_version,
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null },
    data: {
      revision: value.result.revision,
      supersedes: value.result.supersedes,
      published_at: value.result.published_at,
      security_count: value.result.security_count,
    },
  };
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
export type MockState = { denied?: boolean; offline?: boolean; empty?: boolean; delayOld?: boolean; securityId?: string };
export async function mockMarket(page: Page, state: MockState = {}) {
  const resetCoverage = { expected: 65, valid: 63, excluded: 1, missing: 1 };
  const resetEnvelope = (data: unknown) => ({
    ...resetSummary(snapshot),
    coverage: resetCoverage,
    data,
  });
  await page.route('**/api/market/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (data: unknown) => route.fulfill({ json: resetEnvelope(data) });
    if (state.denied) return route.fulfill({ status: 403, json: { error: 'Source access denied' } });
    if (state.offline) return route.abort('failed');
    if (url.pathname === '/api/market/overview') {
      const members = {
        up: securities.slice(0, 63).filter(item => Number(item.change) > 0).map(item => ({ security_id: item.security_id, code: item.security_id, name: item.name! })),
        down: securities.slice(0, 63).filter(item => Number(item.change) < 0).map(item => ({ security_id: item.security_id, code: item.security_id, name: item.name! })),
        flat: [],
      };
      return respond({
        topic_basis: { source: null, source_version: null, as_of: null, cutoff: snapshot.as_of },
        indexes: [],
        breadth: {
          up: 32, down: 31, flat: 0, valid_total: 63, coverage: resetCoverage, members,
          seal_rate: { numerator: 0, denominator: 63, value: 0, reason: null, coverage: resetCoverage },
        },
        sentiment: {
          limit_up_count: 0, limit_down_count: 0, max_boards: 0,
          promote_rate: { yesterday_ge2: 0, numerator: 0, denominator: 0, value: null, excluded: 0, missing: 0, reason: 'no_yesterday_ge2_members' },
          yesterday_limit_up_median_return: null,
          yesterday_limit_up_return_coverage: { expected: 0, valid: 0, excluded: 0, missing: 0 },
          yesterday_limit_up_return_reason: 'no_yesterday_limit_up_members',
        },
        hot_sectors: [],
        factors: {
          'FAC-M01': {
            advancing: { numerator: 32, denominator: 63, value: 32 / 63, reason: null, coverage: resetCoverage },
            above_ma20: { numerator: 0, denominator: 0, value: null, reason: 'not_available', coverage: resetCoverage },
            method: 'synthetic',
          },
          'FAC-M02': { value: null, today_total: null, baseline_mean: null, baseline_days: 20, baseline_excludes_current: true, coverage: resetCoverage, reason: 'not_available', method: 'synthetic' },
          'FAC-M03': { indexes: [], method: 'synthetic' },
        },
        historical_topic_analysis: { enabled: false, reason: 'historical_topic_coverage_unavailable' },
      });
    }
    if (url.pathname === '/api/market/heatmap') return respond({ columns: [], cells: [] });
    if (url.pathname === '/api/market/sector-flow') return respond({ sectors: [] });
    if (url.pathname === '/api/market/sentiment') return respond({ series: [] });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route('**/api/snapshots/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (state.denied) return respond({ error: 'Source access denied' }, 403);
    if (state.offline) return route.abort('failed');
    if (url.pathname === '/api/snapshots/list') {
      const date = url.searchParams.get('date');
      const items = state.empty || (date && !['2026-09-10', '2026-09-09'].includes(date))
        ? [] : [resetSummary(date === '2026-09-09' ? oldSnapshot : snapshot)];
      return respond({ ...resetSummary(items[0]?.snapshot_id === OLD ? oldSnapshot : snapshot), data: { snapshots: items } });
    }
    if (url.pathname === `/api/snapshots/${ID}`) return respond(resetSummary(snapshot));
    if (url.pathname === `/api/snapshots/${OLD}`) return respond(resetSummary(oldSnapshot));
    return respond({}, 404);
  });
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
      const available = state.securityId ? [{ ...securities[0], security_id: state.securityId }] : securities;
      let items = available.filter(s => `${s.name} ${s.security_id}`.includes(q));
      if (url.searchParams.get('order') === 'desc') items = [...items].reverse();
      const size = +(url.searchParams.get('page_size') || 50), current = +(url.searchParams.get('page') || 1);
      return respond(envelope({ items: items.slice((current - 1) * size, current * size), total: items.length, page: current, page_size: size, basis: 'raw_close' }, id));
    }
    if (path.includes('/securities/')) return respond(envelope(detail(decodeURIComponent(path.split('/').at(-1)!)), id));
    return respond(id === OLD ? oldSnapshot : snapshot);
  });
}

export type LeaderMockState = {
  denied?: boolean;
  offline?: boolean;
  domainUnavailable?: boolean;
  noCandidate?: boolean;
  insufficient?: boolean;
  unapproved?: boolean;
};

const roleCoverage = { expected: 2, valid: 2, excluded: 0, missing: 0 };
const roleEnvelope = (data: unknown, quality_flags: string[] = []) => ({
  trade_date: '2026-09-10',
  as_of: '2026-09-10T10:00:00Z',
  snapshot_id: ID,
  source: 'role_materialization',
  source_version: 'role-source-v1',
  coverage: roleCoverage,
  quality_flags,
  rule_version: 'role-v1',
  basis: { unit: 'ratio', currency: 'CNY', price_type: 'adjusted', benchmark: 'CSI300' },
  data,
});

export async function mockLeaders(page: Page, state: LeaderMockState = {}) {
  await mockMarket(page);
  await page.route('**/api/market/heatmap?**', async route => {
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    if (state.offline) return route.abort('failed');
    return route.fulfill({ json: {
      ...resetSummary(snapshot),
      data: {
        columns: [1, 2],
        cells: [
          { trade_date: '2026-09-09', boards: 2, count: 1 },
          { trade_date: '2026-09-10', boards: 1, count: 1 },
          { trade_date: '2026-09-10', boards: 2, count: 1 },
        ],
      },
    } });
  });
  await page.route('**/api/limit-up/**', async route => {
    const url = new URL(route.request().url());
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    if (state.offline) return route.abort('failed');
    if (state.domainUnavailable) return route.fulfill({ json: roleEnvelope(null, ['role_missing']) });
    if (url.pathname === '/api/limit-up/ladder') {
      return route.fulfill({ json: roleEnvelope({
        columns: [1, 2],
        total: 2,
        cells: [
          { boards: 1, count: 1, stocks: [{ security_id: 'TEST002', name: '合成测试证券2', roles: [], sector_id: 'SEC001' }] },
          { boards: 2, count: 1, stocks: [{ security_id: 'TEST001', name: '合成测试证券1', roles: ['trend_leader'], sector_id: 'SEC001' }] },
        ],
        promote_rate: { yesterday_ge2: 3, today_sealed: 1, tradable: 2, rate: 0.5 },
      }) });
    }
    if (url.pathname === '/api/limit-up/continuity') {
      return route.fulfill({ json: roleEnvelope({
        promote_rate: { yesterday_ge2: 3, today_sealed: 1, tradable: 2, rate: 0.5 },
        promote_rate_quality: { coverage: { expected: 3, valid: 2, excluded: 0, missing: 1 }, reasons: ['promotion_observation_missing'] },
        yesterday_limit_up_median_return: 0.012,
        yesterday_limit_up_feedback: {
          status: 'available', coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 }, reasons: [],
        },
        tradable_count: 2,
      }) });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route('**/api/role/**', async route => {
    const url = new URL(route.request().url());
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    if (state.offline) return route.abort('failed');
    if (state.domainUnavailable) return route.fulfill({ json: roleEnvelope(null, ['role_disabled']) });
    if (url.pathname === '/api/role/candidates') {
      const role = url.searchParams.get('role') || 'trend_leader';
      const status = state.insufficient ? 'insufficient' : state.noCandidate ? 'no_candidate' : 'ready';
      const reasons = state.insufficient
        ? ['required_inputs_missing']
        : state.noCandidate ? ['no_ranked_candidate'] : [];
      const candidates = state.noCandidate || state.insufficient || state.unapproved ? [] : [
        { security_id: 'TEST001', name: '合成测试证券1', rank: 1, evidence_status: 'candidate', sector_id: 'SEC001', boards: 2 },
        { security_id: 'TEST002', name: '合成测试证券2', rank: 1, evidence_status: 'candidate', sector_id: 'SEC001', boards: 1 },
      ];
      return route.fulfill({ json: roleEnvelope({
        role,
        statuses: [{ sector_id: 'SEC001', status, capability: role === 'genfeng' ? 'watch_only' : 'ready', reasons }],
        candidates,
        change_summaries: [{
          sector_id: 'SEC001', role: 'trend', previous_role_run_id: 'previous-run',
          comparison_status: 'not_comparable', membership_changed: true,
          reasons: ['rule_version_changed', 'topic_membership_changed'],
        }],
        role_changes: [],
        total: candidates.length,
      }, state.unapproved ? ['rule_version_not_approved'] : []) });
    }
    if (url.pathname === '/api/role/evidence') {
      const security = url.searchParams.get('security_id') || '';
      const roleNames = ['emotion_leader', 'trend_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli'];
      return route.fulfill({ json: roleEnvelope({
        security_id: security,
        sector_id: 'SEC001',
        roles: roleNames.map((role, index) => ({
          role_type: role,
          evidence_status: index === 1 ? 'candidate' : index === 3 ? 'watch' : 'insufficient',
          factors: [{
            factor_id: index === 1 ? 'FAC-R01' : `FAC-${index + 1}`,
            metric: index === 1 ? 'relative_strength_20' : 'observed_factor',
            value: index === 1 ? 0.12 : null,
            status: index === 1 ? 'computed' : 'unavailable',
            unit: 'ratio',
            window: index === 1 ? '20d' : null,
            coverage: index === 1 ? { expected: 21, valid: 21, excluded: 0, missing: 0 } : { expected: 1, valid: 0, excluded: 0, missing: 1 },
            reasons: index === 1 ? [] : ['required_input_unavailable'],
            source_time: '2026-09-10T07:30:00Z',
            knowledge_cutoff: '2026-09-10T08:00:00Z',
          }],
          supporting_evidence: index === 1 ? ['relative_strength_positive'] : [],
          opposing_evidence: index === 3 ? ['lags_unique_core'] : [],
          missing_evidence: index === 1 ? ['intraday_seal_detail_unavailable'] : ['required_input_unavailable'],
        })),
      }) });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route('**/api/stock/**', async route => {
    const url = new URL(route.request().url());
    if (state.denied) return route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } });
    if (state.offline) return route.abort('failed');
    const securityId = decodeURIComponent(url.pathname.split('/').at(-1)!);
    const security = securities.find(item => item.security_id === securityId) ?? securities[0];
    return route.fulfill({ json: roleEnvelope({
      security_id: securityId,
      name: security.name,
      quote: { last: Number(security.close), change_ratio: Number(security.change), amount: Number(security.amount) },
      roles: [{ role_type: 'trend_leader', evidence_status: 'candidate', sector_id: 'SEC001' }],
      seal: {
        first_seal_time: null, last_seal_time: null, break_count: null, seal_amount: null,
        reason: 'intraday_seal_fields_unavailable',
      },
      regulation: { active_events: 0 },
    }) });
  });
}
