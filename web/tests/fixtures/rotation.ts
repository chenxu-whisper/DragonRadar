import type { Page, Route } from '@playwright/test';
import { ID, mockLeaders } from './market';

// EX11 rotation timeline E2E fixture (Task 24, slice 4). Synthetic, test-only
// data; no production module imports this file.
export { ID };
// Publication id serving the 2026-09-09 historical run.
export const OLD = '00000000-0000-4000-8000-000000000002';
// Publication ids serving the two earlier runs. Every event snapshot id is a
// canonical lowercase UUID because validateRotation enforces the wire shape.
export const P_0908 = '33333333-0000-4000-8000-000000000008';
export const P_0820 = '44444444-0000-4000-8000-000000000007';

export const ANCHOR_DATE = '2026-09-10';
export const SECTOR_A = 'SEC001';
export const SECTOR_B = 'SEC002';

const MARKET_SNAPSHOT_ID = '55555555-0000-4000-8000-00000000000a';
const TOPIC_SNAPSHOT_ID = '66666666-0000-4000-8000-00000000000b';

// Wire event: strictly the eight keys validateRotation accepts, globally
// ordered by (at, sector) ascending with unique (at, sector) pairs. Anchor-day
// (2026-09-10) events carry the current publication ID; historical events
// carry the publication that served that day.
interface RotationEventWire {
  at: string;
  sector_id: string;
  core_ids: string[];
  change: 'segment_start' | 'unchanged' | 'expanded' | 'contracted' | 'reorganized';
  comparable: boolean;
  reason: string | null;
  snapshot_id: string;
  input_manifest: {
    run_id: string;
    market_snapshot_id: string;
    topic_snapshot_id: string;
    rule_version: string;
    membership_version: string;
  };
}

function manifest(at: string): RotationEventWire['input_manifest'] {
  return {
    run_id: `run-${at}`,
    market_snapshot_id: MARKET_SNAPSHOT_ID,
    topic_snapshot_id: TOPIC_SNAPSHOT_ID,
    rule_version: 'rotation-rule-v1',
    membership_version: 'membership-v1',
  };
}

const ROTATION_EVENTS: readonly RotationEventWire[] = [
  {
    at: '2026-08-20', sector_id: SECTOR_A, core_ids: ['TEST001'],
    change: 'unchanged', comparable: true, reason: null, snapshot_id: P_0820,
    input_manifest: manifest('2026-08-20'),
  },
  {
    at: '2026-09-08', sector_id: SECTOR_A, core_ids: ['TEST001'],
    change: 'segment_start', comparable: false, reason: 'first_observation', snapshot_id: P_0908,
    input_manifest: manifest('2026-09-08'),
  },
  {
    at: '2026-09-08', sector_id: SECTOR_B, core_ids: ['TEST003'],
    change: 'contracted', comparable: true, reason: null, snapshot_id: P_0908,
    input_manifest: manifest('2026-09-08'),
  },
  {
    at: '2026-09-09', sector_id: SECTOR_A, core_ids: ['TEST001', 'TEST002'],
    change: 'expanded', comparable: true, reason: null, snapshot_id: OLD,
    input_manifest: manifest('2026-09-09'),
  },
  {
    at: '2026-09-09', sector_id: SECTOR_B, core_ids: ['TEST003'],
    change: 'segment_start', comparable: false, reason: 'rule_version_changed', snapshot_id: OLD,
    input_manifest: manifest('2026-09-09'),
  },
  {
    at: ANCHOR_DATE, sector_id: SECTOR_A, core_ids: ['TEST002'],
    change: 'reorganized', comparable: true, reason: null, snapshot_id: ID,
    input_manifest: manifest(ANCHOR_DATE),
  },
  {
    at: ANCHOR_DATE, sector_id: SECTOR_B, core_ids: [],
    change: 'unchanged', comparable: true, reason: 'no_core_candidate', snapshot_id: ID,
    input_manifest: manifest(ANCHOR_DATE),
  },
];

export type RotationMockState = { rotation?: 'null' | 'empty' | 'malformed' };

// Mirrors roleEnvelope() in fixtures/market.ts, but the header echoes the
// requested trade date and publication id so historical/anchor swaps validate.
function roleEnvelope(data: unknown, tradeDate: string, snapshotId: string) {
  return {
    trade_date: tradeDate,
    as_of: `${tradeDate}T10:00:00Z`,
    snapshot_id: snapshotId,
    source: 'role_materialization',
    source_version: 'role-source-v1',
    coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: 'role-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'adjusted', benchmark: 'CSI300' },
    data,
  };
}

// Inclusive calendar-day window in UTC: start = end - (days - 1) days.
function windowStart(endDate: string, days: number): string {
  const start = new Date(`${endDate}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return start.toISOString().slice(0, 10);
}

export async function mockRotation(page: Page, state: RotationMockState = {}) {
  await mockLeaders(page);

  // Registered after mockLeaders (Playwright prefers newer routes); the only
  // topics endpoint served here is /api/topics/rotation.
  await page.route('**/api/topics/**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/api/topics/rotation') {
      return route.fulfill({ status: 404, json: {} });
    }

    const date = url.searchParams.get('date') || ANCHOR_DATE;
    const snapshotId = url.searchParams.get('snapshot_id') || ID;
    const days = Number(url.searchParams.get('days') || '30');
    const sector = url.searchParams.get('sector');
    const start = windowStart(date, days);

    const visible = ROTATION_EVENTS.filter(event =>
      event.at >= start
      && event.at <= date
      && (sector === null || event.sector_id === sector));

    let events: RotationEventWire[] = state.rotation === 'empty' ? [] : [...visible];
    if (state.rotation === 'malformed') {
      // Duplicate the final anchor (at, sector) pair; the pair is otherwise a
      // fully legal event, so fail-closure proves the uniqueness/order guard.
      const anchor = events[events.length - 1] ?? ROTATION_EVENTS[ROTATION_EVENTS.length - 1];
      events.push(JSON.parse(JSON.stringify(anchor)) as RotationEventWire);
    }

    const data = state.rotation === 'null'
      ? null
      : { events, days, sector, window: { start, end: date } };

    return route.fulfill({ json: roleEnvelope(data, date, snapshotId) });
  });

  // Historical nodes request evidence with the serving snapshot id of that
  // day, not the current publication: the envelope header must echo the query
  // date/snapshot or validateEvidence rejects the response with 409.
  await page.route('**/api/role/evidence?**', async (route: Route) => {
    const url = new URL(route.request().url());
    const date = url.searchParams.get('date') || ANCHOR_DATE;
    const snapshotId = url.searchParams.get('snapshot_id') || ID;
    const security = url.searchParams.get('security_id') || '';
    const sector = url.searchParams.get('sector') || '';
    const roleNames = ['emotion_leader', 'trend_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli'];
    return route.fulfill({ json: roleEnvelope({
      security_id: security,
      sector_id: sector,
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
    }, date, snapshotId) });
  });
}
