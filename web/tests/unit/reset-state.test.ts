import { describe, expect, it } from 'vitest';
import { parseState, stateSearch } from '../../src/state';

const snapshot = '00000000-0000-4000-8000-000000000001';

describe('explicit reset URL state', () => {
  it.each(['news', 'leaders', 'regulation', 'review'])('round trips %s without owner mode', view => {
    const query = `?view=${view}&snapshot=${snapshot}&date=2026-09-10&q=TEST&sort=amount&order=desc&page=3`;
    const parsed = parseState(query, false, true);
    expect(parsed.state.view).toBe(view);
    expect(parsed.notices).toEqual([]);
    expect(parseState(stateSearch(parsed.state, false, true), false, true).state).toEqual(parsed.state);
  });

  it('keeps public defaults and strips reset/private views without opt-in', () => {
    for (const view of ['news', 'leaders', 'regulation', 'review']) {
      expect(parseState(`?view=${view}`).state.view).toBe('overview');
      const reset = parseState(`?view=${view}`, false, true).state;
      expect(new URLSearchParams(stateSearch(reset)).get('view')).toBe('overview');
    }
  });

  it('keeps existing private review URLs and note IDs', () => {
    const state = parseState(`?view=review&note=${snapshot}`, true).state;
    expect(state.view).toBe('review');
    expect(parseState(stateSearch(state, true), true).state).toEqual(state);
    expect(parseState(stateSearch(state, false, true), false, true).state.note).toBe('');
  });

  it('accepts a reset standalone stock path without losing fixed query context', () => {
    const { state } = parseState(`?snapshot=${snapshot}&date=2026-09-10&q=TEST&sort=amount&page=2`, false, true, '/stock/TEST%20001');
    expect(state).toMatchObject({ view: 'stock', security: 'TEST 001', snapshot, date: '2026-09-10', q: 'TEST', sort: 'amount', page: 2 });
  });

  it('does not interpret paths in legacy mode', () => {
    expect(parseState('?view=analysis', false, false, '/stock/TEST001').state.view).toBe('analysis');
  });

  it.each(['/stock/%E0%A4%A', '/stock/%2E%2E', '/stock/' + 'A'.repeat(201)])('rejects invalid standalone ID: %s', path => {
    const parsed = parseState('', false, true, path);
    expect(parsed.state.security).toBe('');
    expect(parsed.notices.length).toBeGreaterThan(0);
  });
});
