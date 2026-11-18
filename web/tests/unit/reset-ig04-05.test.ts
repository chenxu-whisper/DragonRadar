import { afterEach, describe, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { delimiter, resolve } from 'node:path';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseState, stateSearch } from '../../src/state';
import {
  ladderPath, validateCandidates, validateContinuity, validateLadder,
} from '../../src/features/roles/api';
import { ROLE_NAMES } from '../../src/features/roles/types';
import RoleLists from '../../src/features/roles/RoleLists';
import { Leaders } from '../../src/pages/Leaders';

const harness = vi.hoisted(() => ({
  resources: new Map<string, unknown>(),
  states: [] as unknown[],
  cursor: 0,
}));
vi.mock('../../src/hooks/useResource', () => ({
  useResource: (path: string | null) => ({
    data: path ? harness.resources.get(path.split('?')[0]) : undefined, loading: false,
  }),
}));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useState: (initial: unknown) => {
    const index = harness.cursor++;
    if (!(index in harness.states)) harness.states[index] = initial;
    return [harness.states[index], (value: unknown) => { harness.states[index] = value; }];
  },
  useEffect: () => {},
  useMemo: (factory: () => unknown) => factory(),
}));
afterEach(() => {
  harness.resources.clear(); harness.states = []; harness.cursor = 0;
});

const snapshot = '00000000-0000-4000-8000-000000000041';
const historical = '00000000-0000-4000-8000-000000000042';
const date = '2026-09-08';
const selection = { snapshot, date, sector: 'topic-a', days: 2 };
const coverage = { expected: 1, valid: 1, excluded: 0, missing: 0 };
const promotion = { yesterday_ge2: 1, today_sealed: 1, tradable: 1, rate: 1 };
const root = {
  snapshot_id: snapshot, trade_date: date, as_of: '2026-09-09T12:00:00Z',
  source: 'role_materialization', source_version: 's1',
  rule_version: 'role-v1', coverage, quality_flags: [],
  basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'CSI300' },
};

function stock(name = 'Archived A') {
  return {
    security_id: 'A', name, roles: ['trend_leader'], sector_id: 'topic-a',
    change_ratio: 0.12,
    topics: [{ sector_id: 'topic-a', name: 'Archived Topic' }],
    role_evidence: [{
      role_type: 'trend_leader', sector_id: 'topic-a', evidence_status: 'candidate',
    }],
    seal: {
      is_limit_up: true, touched_limit: true, first_seal_time: null,
      last_seal_time: null, break_count: null, seal_amount: null,
      seal_amount_unit: null, continuous_boards: 2,
      reason: 'intraday_seal_fields_unavailable',
    },
  };
}

function ladder() {
  const cells = [{ boards: 2, count: 1, stocks: [stock()] }];
  return {
    ...structuredClone(root),
    data: {
      columns: [2], total: 1, cells, promote_rate: promotion,
      history: {
        days: 2, sector: 'topic-a',
        window: { start: '2026-09-07', end: date },
        rows: [
          {
            trade_date: '2026-09-07', snapshot_id: historical, rule_version: 'role-v1',
            status: 'available', reasons: [], coverage, columns: [2], total: 1,
            cells: [{ boards: 2, count: 1, stocks: [stock('Historical A')] }],
          },
          {
            trade_date: date, snapshot_id: snapshot, rule_version: 'role-v1',
            status: 'available', reasons: [], coverage, columns: [2], total: 1, cells,
          },
        ],
      },
    },
  };
}

function continuity() {
  const member = {
    security_id: 'A', name: 'Archived A', status: 'valid', return: 0.0,
    direction: 'flat', sealed: true, reasons: [],
  };
  return {
    ...structuredClone(root),
    data: {
      promote_rate: promotion, promote_rate_quality: { coverage: { ...coverage }, reasons: [] },
      yesterday_limit_up_median_return: 0,
      yesterday_limit_up_feedback: { status: 'available', coverage: { ...coverage }, reasons: [] },
      tradable_count: 1,
      details: {
        status: 'available', version: 1, reasons: [],
        data: {
          trade_date: date, previous_trade_date: '2026-09-07',
          promotion: { coverage: { ...coverage }, members: [structuredClone(member)] },
          feedback: {
            coverage: { ...coverage }, members: [structuredClone(member)],
            direction_counts: { up: 0, down: 0, flat: 1 },
          },
        },
      },
    },
  };
}

describe('IG04 canonical multi-role URL and IG05 historical origin', () => {
  it('normalizes roles in canonical order without overriding legacy role', () => {
    const parsed = parseState(
      '?view=leaders&role=jieli&roles=zhongjun,trend_leader,zhongjun,emotion_leader',
      false, true,
    );
    expect(parsed.state).toMatchObject({
      role: 'jieli', roles: ['emotion_leader', 'trend_leader', 'zhongjun'],
    });
    expect(parsed.notices).toEqual([]);
    const query = new URLSearchParams(stateSearch(parsed.state, false, true));
    expect(query.get('roles')).toBe('emotion_leader,trend_leader,zhongjun');
    expect(query.get('role')).toBe('jieli');
    expect(parseState(`?${query}`, false, true).state).toEqual(parsed.state);
  });

  it.each(ROLE_NAMES)('legacy-only %s becomes the selected role', role => {
    const parsed = parseState(`?view=leaders&role=${role}`, false, true);
    expect(parsed.state).toMatchObject({ role, roles: [role] });
  });

  it.each([
    'roles=trend_leader,unknown',
    'roles=trend_leader&roles=zhongjun',
    'roles=,',
  ])('invalid multi-role selection resets to legacy role with one notice: %s', query => {
    const parsed = parseState(`?view=leaders&role=zhongjun&${query}`, false, true);
    expect(parsed.state).toMatchObject({ role: 'zhongjun', roles: ['zhongjun'] });
    expect(parsed.notices).toHaveLength(1);
  });

  it.each(['review', 'regulation'])('preserves reset %s return and pinned origin', view => {
    const query = `?view=stock&snapshot=${historical}&date=2026-09-07&return_view=${view}&return_snapshot=${snapshot}&return_date=${date}`;
    const parsed = parseState(query, false, true);
    expect(parsed.notices).toEqual([]);
    expect(parsed.state).toMatchObject({
      return_view: view, return_snapshot: snapshot, return_date: date,
    });
    expect(parseState(stateSearch(parsed.state, false, true), false, true).state)
      .toEqual(parsed.state);
  });

  it('sharing rejects review origin but retains regulation origin', () => {
    expect(parseState('?return_view=review', false, true, '', false).state.return_view)
      .toBe('overview');
    expect(parseState('?return_view=regulation', false, true, '', false).state.return_view)
      .toBe('regulation');
  });

  it.each([
    'return_snapshot=not-a-uuid',
    'return_date=2026-02-30',
    'return_date=2026-9-8',
  ])('rejects malformed historical origin: %s', query => {
    const parsed = parseState(`?view=stock&${query}`, false, true);
    expect(parsed.notices).toHaveLength(1);
    const encoded = new URLSearchParams(stateSearch(parsed.state, false, true));
    expect(encoded.has('return_snapshot')).toBe(false);
    expect(encoded.has('return_date')).toBe(false);
  });

  it('does not serialize reset-only selection and origin in legacy mode', () => {
    const parsed = parseState(`?view=stock&role=zhongjun`, false, false).state;
    const query = stateSearch({
      ...parsed, roles: ['trend_leader'], return_snapshot: snapshot, return_date: date,
    } as typeof parsed, false, false);
    const values = new URLSearchParams(query);
    expect(values.has('roles')).toBe(false);
    expect(values.has('return_snapshot')).toBe(false);
    expect(values.has('return_date')).toBe(false);
    expect(parseState('?return_view=review').state.return_view).toBe('overview');
    expect(parseState('?return_view=regulation').state.return_view).toBe('overview');
  });

  it.each(['', 'topic-origin'])('preserves explicit origin sector including empty filter: %s', return_sector => {
    const state = parseState(`?sector=actual-topic&return_sector=${return_sector}`, false, true).state;
    expect(state.return_sector).toBe(return_sector);
    const encoded = new URLSearchParams(stateSearch(state, false, true));
    expect(encoded.has('return_sector')).toBe(true);
    expect(encoded.get('return_sector')).toBe(return_sector);
    expect(parseState(`?${encoded}`, false, true).state.return_sector).toBe(return_sector);
    expect(new URLSearchParams(stateSearch(state, false, false)).has('return_sector')).toBe(false);
  });

  it.each(['.', '..', 'a'.repeat(201)])('rejects malformed return sector: %s', sector => {
    const parsed = parseState(`?return_sector=${sector}`, false, true);
    expect(parsed.state.return_sector).toBeUndefined();
    expect(parsed.notices).toHaveLength(1);
  });

  it('omits absent origin sector without substituting current sector', () => {
    const parsed = parseState('?sector=actual-topic', false, true).state;
    expect(new URLSearchParams(stateSearch(parsed, false, true)).has('return_sector')).toBe(false);
  });
});

describe('IG05 read-side nested identity guards', () => {
  it.each(['return', 'null_return', 'sealed', 'null_sealed', 'reasons'])(
    'rejects individually consistent pools with contradictory shared %s', fact => {
      const response = continuity();
      const { promotion, feedback } = response.data.details.data;
      if (fact === 'return') Object.assign(promotion.members[0], { return: -0.1, direction: 'down' });
      if (fact === 'null_return') Object.assign(promotion.members[0], { return: null, direction: null });
      if (fact === 'sealed') feedback.members[0].sealed = false;
      if (fact === 'null_sealed') Object.assign(feedback.members[0], { sealed: null });
      if (fact === 'reasons') Object.assign(promotion.members[0], { reasons: ['daily_return_unavailable'] });
      expect(() => validateContinuity(response, selection)).toThrow();
    },
  );
  it('requests one bounded historical ladder with the selected sector', () => {
    const query = new URL(ladderPath(selection), 'https://test.invalid').searchParams;
    expect(query.get('days')).toBe('2');
    expect(query.get('snapshot_id')).toBe(snapshot);
    expect(query.get('sector')).toBe('topic-a');
  });

  it('accepts actual historical lists and legacy summary-only responses', () => {
    expect(validateLadder(ladder(), selection).data?.total).toBe(1);
    expect(validateContinuity(continuity(), selection).data?.tradable_count).toBe(1);
    const legacy = continuity();
    Reflect.deleteProperty(legacy.data, 'details');
    expect(validateContinuity(legacy, selection).data?.tradable_count).toBe(1);
  });

  it.each([
    'anchor_swap', 'bad_uuid', 'wrong_sector', 'future_day', 'duplicate_day',
    'wrong_count', 'wrong_days', 'empty_reason', 'wrong_evidence_sector',
    'nonfinite_change', 'fabricated_seal_time',
  ])('rejects corrupted nested history/card context: %s', corruption => {
    const response = ladder();
    const history = response.data.history;
    const first = history.rows[0];
    if (corruption === 'anchor_swap') history.rows[1].snapshot_id = historical;
    if (corruption === 'bad_uuid') first.snapshot_id = '../not-a-publication';
    if (corruption === 'wrong_sector') first.cells[0].stocks[0].sector_id = 'topic-b';
    if (corruption === 'future_day') first.trade_date = '2026-09-09';
    if (corruption === 'duplicate_day') first.trade_date = date;
    if (corruption === 'wrong_count') first.cells[0].count = 2;
    if (corruption === 'wrong_days') history.days = 3;
    if (corruption === 'empty_reason') Object.assign(first, {
      status: 'unavailable', columns: null, total: null, cells: null, coverage: null,
      reasons: [],
    });
    if (corruption === 'wrong_evidence_sector') {
      first.cells[0].stocks[0].role_evidence[0].sector_id = 'topic-b';
    }
    if (corruption === 'nonfinite_change') first.cells[0].stocks[0].change_ratio = Infinity;
    if (corruption === 'fabricated_seal_time') Object.assign(first.cells[0].stocks[0].seal, {
      first_seal_time: 'not-a-time', reason: null,
    });
    expect(() => validateLadder(response, selection)).toThrow();
  });

  it.each([
    'wrong_date', 'bad_coverage', 'duplicate_member', 'direction', 'counts',
    'return_for_missing', 'promotion_count', 'median', 'boolean_version',
  ])('rejects malformed versioned feedback detail: %s', corruption => {
    const response = continuity();
    const detail = response.data.details.data;
    if (corruption === 'wrong_date') detail.trade_date = '2026-09-07';
    if (corruption === 'bad_coverage') detail.feedback.coverage.expected = 2;
    if (corruption === 'duplicate_member') detail.feedback.members.push(
      structuredClone(detail.feedback.members[0]),
    );
    if (corruption === 'direction') detail.feedback.members[0].direction = 'up';
    if (corruption === 'counts') detail.feedback.direction_counts.up = 1;
    if (corruption === 'return_for_missing') detail.feedback.members[0].status = 'missing';
    if (corruption === 'promotion_count') detail.promotion.members[0].sealed = false;
    if (corruption === 'median') {
      detail.feedback.members[0].return = 0.3;
      detail.feedback.members[0].direction = 'up';
      detail.feedback.direction_counts = { up: 1, down: 0, flat: 0 };
    }
    if (corruption === 'boolean_version') Object.assign(response.data.details, { version: true });
    expect(() => validateContinuity(response, selection)).toThrow();
  });

  it('rejects evidence from another role without throwing away the other role response', () => {
    const response = {
      ...root,
      data: {
        role: 'trend_leader', statuses: [], candidates: [], total: 0,
        change_summaries: [], role_changes: [{
          id: 'wrong-role-change', sector_id: 'topic-a', security_id: 'A',
          role: 'zhongjun', comparison_status: 'comparable', change_type: 'added',
          membership_changed: false, reasons: [],
        }],
      },
    };
    expect(() => validateCandidates(response, { ...selection, role: 'trend_leader' }))
      .toThrow();
    expect(validateCandidates({
      ...response, data: { ...response.data, role: 'zhongjun' },
    }, { ...selection, role: 'zhongjun' }).data?.role).toBe('zhongjun');
  });

  it('rejects duplicate board cells even when totals still reconcile', () => {
    const response = ladder();
    response.data.cells.push({ boards: 2, count: 0, stocks: [] });
    expect(() => validateLadder(response, selection)).toThrow();
  });
});

function renderList() {
  harness.cursor = 0;
  return RoleLists({ selection: { ...selection, role: 'trend_leader' }, refreshToken: 0, open: vi.fn() });
}

function routeOutput(expression: string) {
  const repo = resolve('..');
  return JSON.parse(execFileSync(resolve(repo, process.platform === 'win32'
    ? '.venv/Scripts/python.exe' : '.venv/bin/python'), [
    '-c', `from test_ig04_05_role_routes import *; print(${expression}.body.decode())`,
  ], {
    cwd: repo, encoding: 'utf8', timeout: 30000,
    env: { ...process.env, PYTHONPATH: [resolve(repo, 'src'), resolve(repo, 'tests')].join(delimiter), PYTHONDONTWRITEBYTECODE: '1' },
  }));
}

function findElement(node: unknown, predicate: (element: ReactElement<Record<string, unknown>>) => boolean): ReactElement<Record<string, unknown>> | undefined {
  if (Array.isArray(node)) return node.map(child => findElement(child, predicate)).find(Boolean);
  if (!node || typeof node !== 'object' || !('props' in node)) return undefined;
  const element = node as ReactElement<Record<string, unknown>>;
  return predicate(element) ? element : findElement(element.props.children, predicate);
}

describe('IG04/05 actual projection and matrix rendering regressions', () => {
  it('sorts real route change names both directions with unknown archive identities last', () => {
    const response = routeOutput('roles.candidates(request(Repository(role_change_publication()), role="trend_leader"))');
    expect(response.data.role_changes.map((row: { name: unknown }) => row.name)).toEqual(['Zulu', 'Alpha', null]);
    harness.resources.set('/api/role/candidates', response);
    const sort = () => {
      const tree = renderList();
      const table = findElement(tree, element => element.props['aria-label'] === '角色变化明细')!;
      const button = findElement(table, element => element.props['aria-label'] === '名称排序')!;
      (button.props.onClick as () => void)();
      const updated = findElement(renderList(), element => element.props['aria-label'] === '角色变化明细')!;
      return renderToStaticMarkup(updated);
    };
    const asc = sort();
    expect(asc.indexOf('>B<')).toBeLessThan(asc.indexOf('>A<'));
    expect(asc.indexOf('>A<')).toBeLessThan(asc.indexOf('>UNKNOWN<'));
    const desc = sort();
    expect(desc.indexOf('>A<')).toBeLessThan(desc.indexOf('>B<'));
    expect(desc.indexOf('>B<')).toBeLessThan(desc.indexOf('>UNKNOWN<'));
  }, 30000);

  it('accepts real promotion-only observation when feedback lacks a return reference', () => {
    const response = routeOutput('roles.continuity(request(Repository(continuity_materialized_publication(no_reference=True))))');
    expect(validateContinuity(response, selection).data?.details?.status).toBe('available');
    response.data.details.data.feedback.members[0].reasons = ['security_suspended'];
    expect(() => validateContinuity(response, selection)).toThrow();
  }, 30000);

  it.each(['available', 'unavailable'])('retains anchor-only levels with separate counts for %s history', status => {
    const response = ladder();
    Object.assign(response.data.history, { days: 10, sector: null });
    if (status === 'unavailable') Object.assign(response.data.history.rows[0], {
      status, snapshot_id: null, rule_version: null, reasons: ['publication_unavailable'],
      coverage: null, columns: null, total: null, cells: null,
    });
    harness.resources.set('/api/limit-up/ladder', response);
    harness.resources.set('/api/market/heatmap', {
      ...root, data: { cells: [{ trade_date: '2026-09-07', boards: 7, count: 9 }] },
    });
    const state = parseState(`?view=leaders&snapshot=${snapshot}&date=${date}`, false, true).state;
    const markup = renderToStaticMarkup(createElement(Leaders, { state, patch: vi.fn(), open: vi.fn(), refreshToken: 0 }));
    expect(markup).toContain('7板');
    expect(markup).toContain(`锚点归档计数 9；历史发布列表 ${status === 'available' ? '0' : '不可用'}`);
    expect(markup.match(/class="matrix-security"/g)).toHaveLength(status === 'available' ? 2 : 1);
  });

  it.each(['snapshot', 'date', 'sector'])('does not merge anchor counts with a different %s selection', mismatch => {
    const response = ladder();
    Object.assign(response.data.history, { days: 10, sector: mismatch === 'sector' ? 'topic-a' : null });
    harness.resources.set('/api/limit-up/ladder', response);
    harness.resources.set('/api/market/heatmap', {
      ...root, snapshot_id: mismatch === 'snapshot' ? historical : snapshot,
      trade_date: mismatch === 'date' ? '2026-09-07' : date,
      data: { cells: [{ trade_date: '2026-09-07', boards: 7, count: 9 }] },
    });
    const state = parseState(`?view=leaders&snapshot=${snapshot}&date=${date}${mismatch === 'sector' ? '&sector=topic-a' : ''}`, false, true).state;
    const markup = renderToStaticMarkup(createElement(Leaders, { state, patch: vi.fn(), open: vi.fn(), refreshToken: 0 }));
    expect(markup).not.toContain('7板');
    expect(markup).not.toContain('锚点归档计数 9');
    expect(markup).toContain('Historical A');
  });
});
