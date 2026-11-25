import { expect, type Page } from '@playwright/test';
import { mockC2, topicData, searchData, candidates, ID, DATE } from './contract-c2-scenes';
import { resetSummary, snapshot, sessions, OLD } from './market';
import type { ReviewBackup } from '../../src/repositories/local/migrate';
import type { ResetTopicDetail } from '../../src/types';
import type { GlobalSearchData } from '../../src/features/workbench/search-validation';

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
function topicFixtureData(value: unknown): value is ResetTopicDetail {
  return record(value) && Array.isArray(value.members) && value.members.length > 0
    && value.members.every(member => record(member) && typeof member.security_id === 'string')
    && record(value.membership_coverage) && record(value.sector);
}
function searchFixtureData(value: unknown): value is GlobalSearchData {
  return record(value) && record(value.groups) && Array.isArray(value.groups.securities)
    && value.groups.securities.length > 0 && value.groups.securities.every(security =>
      record(security) && typeof security.security_id === 'string')
    && Array.isArray(value.groups.topics) && Array.isArray(value.groups.messages)
    && record(value.group_status);
}

export { ID, DATE, sessions };
export { resetSummary };
export const H = ID;
export const url = (view: string, extra = '') => `/?view=${view}&snapshot=${H}&date=${DATE}${extra}`;
export const backup: ReviewBackup = {
  format_version: 1, exported_at: '2026-09-21T08:30:00+00:00',
  profile_mapping: { owner_id: 'b8-synthetic-owner', local_profile_id: 'b8-synthetic-profile' },
  counts: { notes: 1, revisions: 2, verifications: 1, watchlist: 1 },
  notes: [{ note_id: 'b8-note', snapshot_id: H, trade_date: DATE, security_id: 'TEST001',
    current_revision: 2, created_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T09:00:00.000Z' }],
  revisions: [1, 2].map(revision => ({
    revision_id: `b8-r${revision}`, note_id: 'b8-note', revision,
    state: revision === 1 ? 'draft' as const : 'confirmed' as const,
    content: { title: `B8 revision ${revision}`, judgment: 'Synthetic judgment', evidence: 'Synthetic evidence',
      invalidation: 'Synthetic condition', verification_date: '2026-09-12' },
    saved_at: `2026-09-20T0${7 + revision}:00:00.000Z`,
  })),
  verifications: [{ verification_id: 'b8-verification', note_id: 'b8-note', revision_id: 'b8-r2',
    snapshot_id: H, outcome: 'refuted', evidence: 'Synthetic verification',
    supersedes: null, created_at: '2026-09-21T08:00:00.000Z' }],
  watchlist: [{ watch_id: 'b8-watch', security_id: 'TEST001', snapshot_id: H, active: true,
    revision: 1, added_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T08:00:00.000Z' }],
};
export async function seedLocal(page: Page, historical = H, tradeDate = DATE) {
  const restored = { ...backup,
    notes: backup.notes.map(row => ({ ...row, snapshot_id: historical, trade_date: tradeDate })),
    verifications: backup.verifications.map(row => ({ ...row, snapshot_id: historical })),
    watchlist: backup.watchlist.map(row => ({ ...row, snapshot_id: historical })) };
  await page.goto(url('review').replace(H, historical).replace(DATE, tradeDate));
  await page.getByLabel('选择复盘备份文件').setInputFiles({
    name: 'b8-synthetic.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(restored)),
  });
  await page.getByLabel('导入方式').selectOption('merge');
  await page.getByRole('button', { name: '执行恢复' }).click();
  await expect(page.getByRole('button', { name: 'B8 revision 2', exact: true })).toBeVisible();
}
export async function d04Fixture(page: Page, state: {
  h: { snapshot_id: string; trade_date: string; published_at: string; version: number },
  v: { snapshot_id: string; trade_date: string; published_at: string; version: number },
  f?: { snapshot_id: string; trade_date: string; published_at: string; version: number },
  points: { trade_date: string; open: number; high: number; low: number; close: number; volume: number; amount: number }[],
}) {
  const h = state.h;
  const envelope = { ...resetSummary(snapshot), snapshot_id: h.snapshot_id, trade_date: h.trade_date,
    data: { ...resetSummary(snapshot).data, revision: h.version, published_at: h.published_at,
      calendar_sessions: state.points.map(row => row.trade_date), earliest_date: state.points[0].trade_date } };
  const summaries = [state.f, state.v, h].filter((item): item is typeof h => Boolean(item)).map(item => ({
    ...envelope, snapshot_id: item.snapshot_id,
    data: { ...envelope.data, revision: item.version, published_at: item.published_at,
      supersedes: item.snapshot_id === h.snapshot_id ? null : item.snapshot_id === state.v.snapshot_id ? h.snapshot_id : state.v.snapshot_id },
  }));
  await page.route('**/api/snapshots/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path.endsWith('/list')
      ? { ...envelope, data: { snapshots: summaries } }
      : summaries.find(item => path.endsWith(`/${item.snapshot_id}`)) || envelope });
  });
  await page.route('**/api/stock/**', route => route.fulfill({ json: {
    ...envelope, snapshot_id: new URL(route.request().url()).searchParams.get('snapshot_id') || h.snapshot_id,
    data: new URL(route.request().url()).pathname.endsWith('/kline')
      ? { security_id: 'TEST001', adjustment: 'raw', chart_type: 'candlestick',
        adjusted_start: state.points[0].trade_date, adjusted_end: h.trade_date, points: state.points }
      : { security_id: 'TEST001', name: 'B8 isolated archive',
        quote: { last: state.points.at(-1)!.close, change_ratio: null, amount: state.points.at(-1)!.amount },
        roles: [], seal: { is_limit_up: null, touched_limit: null, first_seal_time: null, last_seal_time: null,
          break_count: null, seal_amount: null, seal_amount_unit: null, continuous_boards: null,
          reason: 'intraday_seal_fields_unavailable' },
        regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null } },
  } }));
  await page.route('**/api/news?**', route => route.fulfill({ json: {
    ...envelope, data: { articles: [], page: 1, size: 20, total: 0, sources: [], event_count: 0 },
  } }));
  await page.route('**/api/lhb?**', route => route.fulfill({ json: {
    date: h.trade_date, as_of: h.published_at, source: null, source_version: null,
    quality_flags: ['no_data'], basis: { currency: null, unit: null },
    coverage: { total_disclosures: 0, visible_disclosures: 0, late_disclosures: 0,
      selected_disclosures: 0, seat_count: 0, complete_seats: 0, unknown_seats: 0 },
    data: { items: [], total: 0, page: 1, size: 20 },
  } }));
}
export async function localRecords(page: Page) {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('dragon-radar-local-v3');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const stores = [...db.objectStoreNames].filter(name =>
      ['notes', 'note_revisions', 'verification_records', 'watchlist'].includes(name));
    const records: Record<string, unknown[]> = {};
    for (const name of stores) records[name] = await new Promise((resolve, reject) => {
      const request = db.transaction(name).objectStore(name).getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    db.close();
    return records;
  });
}
export function roleBody(query: URLSearchParams, insufficient = false) {
  const role = query.get('role') || 'trend_leader';
  const rows = insufficient ? [] : candidates;
  return { ...resetSummary(snapshot), rule_version: 'role-v1',
    coverage: { expected: 25, valid: rows.length, excluded: 0, missing: insufficient ? 25 : 0 },
    data: { role, candidates: rows, total: rows.length, change_summaries: [],
      role_changes: rows.map((row, index) => ({ ...row, id: `b8-change-${index}`, role,
        comparison_status: 'comparable', change_type: 'added', membership_changed: false, reasons: ['entered_rank'] })),
      statuses: [{ sector_id: 'SEC001', status: insufficient ? 'insufficient' : 'ready',
        capability: 'ready', reasons: insufficient ? ['required_inputs_missing'] : [] }] } };
}
export async function fixture(page: Page) {
  await mockC2(page);
  // These synthetic archived responses add page transitions missing from C2.
  await page.route('**/api/role/candidates?**', route =>
    route.fulfill({ json: roleBody(new URL(route.request().url()).searchParams) }));
  await page.route('**/api/topics/SEC001?**', route => {
    const query = new URL(route.request().url()).searchParams;
    const body = topicData(query);
    if (!topicFixtureData(body.data)) throw new Error('Invalid B8 topic seed fixture');
    const data = body.data;
    const size = Number(query.get('size') || 50), page = Number(query.get('page') || 1);
    const members = Array.from({ length: 25 }, (_, index) => ({
      ...data.members[0], security_id: `TEST${String(index + 1).padStart(3, '0')}`,
      code: `TEST${String(index + 1).padStart(3, '0')}`, name: `B8 member ${index + 1}`,
    }));
    data.members = members.slice((page - 1) * size, page * size);
    data.total = 25;
    data.membership_coverage = { ...data.membership_coverage, expected: 25, received: 25, known: 25 };
    data.sector = { ...data.sector, member_count: 25 };
    return route.fulfill({ json: body });
  });
  await page.route('**/api/search?**', route => {
    const query = new URL(route.request().url()).searchParams;
    const body = searchData(query);
    if (!searchFixtureData(body.data)) throw new Error('Invalid B8 search seed fixture');
    const data = body.data;
    if (query.get('q') === 'topic') return route.fulfill({ json: body });
    const all = Array.from({ length: 25 }, (_, index) => ({
      ...data.groups.securities[0], security_id: `TEST${String(index + 1).padStart(3, '0')}`,
      code: `TEST${String(index + 1).padStart(3, '0')}`, name: `B8 search ${index + 1}`,
    }));
    const size = Number(query.get('size') || 50), page = Number(query.get('page') || 1);
    data.groups = { securities: all.slice((page - 1) * size, page * size), topics: [], messages: [] };
    data.total = 25;
    data.group_status = {
      securities: { status: 'ready', total: 25, reason: null },
      topics: { status: 'ready', total: 0, reason: null },
      messages: { status: 'ready', total: 0, reason: null },
    };
    return route.fulfill({ json: body });
  });
}
export async function historicalSelection(page: Page) {
  const historical = resetSummary(snapshot);
  const latest = { ...historical, snapshot_id: '00000000-0000-4000-8000-000000000003',
    data: { ...historical.data, revision: 2, supersedes: H, published_at: '2026-09-10T09:00:00Z' } };
  await page.route('**/api/snapshots/list?**', route => route.fulfill({ json: {
    ...historical, data: { snapshots: [latest, historical] },
  } }));
}
export async function nondefaultUnits(page: Page) {
  const basis = { currency: 'USD', unit: 'USD/share', price_type: 'raw', benchmark: null };
  await page.route('**/api/snapshots/**', route => {
    const body = { ...resetSummary(snapshot), basis };
    return route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/list')
      ? { ...body, data: { snapshots: [body] } } : body });
  });
  await page.route(/\/api\/stock\/TEST001\?/, route => route.fulfill({ json: {
    ...resetSummary(snapshot), basis, data: {
      security_id: 'TEST001', name: 'B8 USD security',
      quote: { last: 10.1234, change_ratio: 0.0234, amount: 13 },
      roles: [],
      seal: { is_limit_up: null, touched_limit: null, first_seal_time: null, last_seal_time: null,
        break_count: null, seal_amount: null, seal_amount_unit: null, continuous_boards: null,
        reason: 'intraday_seal_fields_unavailable' },
      regulation: { formal_event_count: 0, event_source_completeness: 'complete', reason: null },
    },
  } }));
  await page.route('**/api/stock/TEST001/kline?**', route => route.fulfill({ json: {
    ...resetSummary(snapshot), basis: { unit: 'USD/share', currency: 'USD', price_type: 'raw', benchmark: null },
    data: { security_id: 'TEST001', adjustment: 'raw', adjusted_start: sessions[0], adjusted_end: DATE,
      chart_type: 'candlestick', points: sessions.map((trade_date, index) => ({
        trade_date, open: 10 + index, high: 12 + index, low: 9 + index, close: 11 + index,
        volume: index === 0 ? null : index === 1 ? 0 : 7,
        amount: index === 0 ? null : index === 1 ? 0 : 13,
        currency: 'USD', price_unit: 'USD/share', volume_unit: 'lot', amount_unit: 'USD',
      })) },
  } }));
}

export const EARLY = '2026-09-10T07:40:00Z';
export const LATE = '2026-09-10T08:01:00Z';
export { OLD };
export async function mixedLhb(page: Page) {
  await page.route('**/api/snapshots/**', route => {
    const request = new URL(route.request().url());
    const early = request.pathname.endsWith(OLD);
    const body = { ...resetSummary(snapshot), snapshot_id: early ? OLD : ID,
      data: { ...resetSummary(snapshot).data, published_at: early ? EARLY : LATE } };
    if (request.pathname.endsWith('/list')) return route.fulfill({ json: {
      ...body, data: { snapshots: [body] },
    } });
    return route.fulfill({ json: body });
  });
  const envelope = (data: unknown, asOf: string, coverage: unknown) => ({
    date: DATE, as_of: asOf, source: null, source_version: null,
    quality_flags: [], basis: { currency: 'USD', unit: 'dollar' }, coverage, data,
  });
  await page.route('**/api/lhb**', route => {
    const request = new URL(route.request().url());
    const asOf = request.searchParams.get('as_of') || LATE;
    const late = asOf > EARLY;
    const seat = (source: string, index: number) => ({
      source_row_id: `${source}-row-${index}`, seat_id: index < 2 ? 'REUSED' : `B8-${index}`,
      source_seat_name: source === 'feed-a' && late ? 'A corrected' : source === 'feed-a' ? 'A original' : 'B distinct',
      mapping_version: source === 'feed-a' && late ? 'map2' : 'map1',
      side: index === 1 ? null : 'buy', rank: index === 1 ? null : 1,
      buy_amount: index === 1 ? null : late && source === 'feed-a' ? 120 : 100,
      sell_amount: index === 1 ? 0 : 40, net_amount: index === 1 ? null : late && source === 'feed-a' ? 80 : 60,
      currency: 'USD', unit: 'dollar', completeness: index === 1 ? 'buy_unknown' : 'complete',
    });
    if (request.pathname.startsWith('/api/lhb/seats/')) {
      const id = decodeURIComponent(request.pathname.split('/').at(-1)!);
      if (id === 'B8-2') return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
      if (id !== 'REUSED') return route.fulfill({ json: { ...envelope({
        seat: { source_id: id, seat_id: id, source_seat_name: id, mapping_version: 'unknown' },
        identity_state: 'unknown', items: [], total: 0,
      }, asOf, { total_disclosures: 0, visible_disclosures: 0, late_disclosures: 0,
        selected_disclosures: 0, seat_count: 0, complete_seats: 0, unknown_seats: 0 }),
        quality_flags: ['no_data'], basis: { currency: null, unit: null } } });
      const contribution = (source: string, corrected: boolean) => ({
        disclosure_id: corrected ? 'shared-disc' : 'prior-disc', security_id: 'TEST001',
        revision: corrected ? 2 : 1, disclosure_date: corrected ? DATE : '2026-09-09',
        reason: 'B8 synthetic disclosure', source_row_id: `${source}-${corrected ? 'corrected' : 'original'}`,
        source_seat_name: corrected ? 'A corrected' : source === 'feed-a' ? 'A original' : 'B distinct',
        mapping_version: corrected ? 'map2' : 'map1', permission_state: 'authorized',
      });
      const items = ['feed-a', 'feed-b'].map(source => ({
        seat_id: 'REUSED', source_id: source, source_version: source === 'feed-a' ? 'a-v1' : 'b-v9',
        mapping_version: source === 'feed-a' && late ? 'map2' : 'map1',
        source_seat_name: source === 'feed-a' && late ? 'A corrected' : source === 'feed-a' ? 'A original' : 'B distinct',
        currency: 'USD', unit: 'dollar', buy_amount: source === 'feed-a' ? late ? 220 : 100 : null,
        sell_amount: source === 'feed-a' ? late ? 80 : 40 : 0,
        net_amount: source === 'feed-a' ? late ? 140 : 60 : null,
        completeness: source === 'feed-a' ? 'complete' : 'buy_unknown',
        contributions: [contribution(source, false), ...(source === 'feed-a' && late ? [contribution(source, true)] : [])],
      }));
      return route.fulfill({ json: envelope({
        seat: { source_id: 'feed-a', seat_id: 'REUSED', source_seat_name: late ? 'A corrected' : 'A original',
          mapping_version: late ? 'map2' : 'map1' }, identity_state: 'known', items, total: 2,
      }, asOf, { total_disclosures: late ? 3 : 2, visible_disclosures: late ? 3 : 2, late_disclosures: 0,
        selected_disclosures: late ? 3 : 2, seat_count: 2, complete_seats: 1, unknown_seats: 1 }) });
    }
    const pageNumber = Number(request.searchParams.get('page') || 1);
    const size = Number(request.searchParams.get('size') || 20);
    const all = Array.from({ length: 21 }, (_, index) => {
      const source = index === 1 ? 'feed-b' : 'feed-a';
      return { disclosure_id: index < 2 ? 'shared-disc' : `b8-disc-${index}`,
        security_id: 'TEST001', disclosure_date: DATE, reason: `B8 synthetic reason ${index}`,
        revision: source === 'feed-a' && late ? 2 : 1, source_id: source,
        source_version: source === 'feed-a' ? 'a-v1' : 'b-v9',
        source_event_at: '2026-09-10T07:10:00Z', received_at: '2026-09-10T07:20:00Z',
        available_at: source === 'feed-a' && late ? '2026-09-10T07:50:00Z' : '2026-09-10T07:30:00Z',
        seats: [seat(source, index)] };
    });
    const items = all.slice((pageNumber - 1) * size, pageNumber * size);
    return route.fulfill({ json: envelope({ items, total: 21, page: pageNumber, size }, asOf, {
      total_disclosures: 21, visible_disclosures: 21, late_disclosures: 0,
      selected_disclosures: 21, seat_count: 21, complete_seats: 20, unknown_seats: 1,
    }) });
  });
}
