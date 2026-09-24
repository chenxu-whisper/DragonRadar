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
    const { state } = parseState(`?snapshot=${snapshot}&date=2026-09-10&q=TEST&sort=amount&page=2&sector=SEC001&role=trend_leader&return_view=leaders`, false, true, '/stock/TEST%20001');
    expect(state).toMatchObject({
      view: 'stock', security: 'TEST 001', snapshot, date: '2026-09-10',
      q: 'TEST', sort: 'amount', page: 2, sector: 'SEC001',
      role: 'trend_leader', return_view: 'leaders',
    });
  });

  it('round trips news article and filters and allows returning from stock to news', () => {
    const query = `?view=stock&snapshot=${snapshot}&date=2026-09-10&return_view=news&article=${snapshot}&news_type=announcement&news_source=synthetic-wire&q=robot&page=3`;
    const parsed = parseState(query, false, true, '/stock/TEST001');
    expect(parsed.notices).toEqual([]);
    expect(parsed.state).toMatchObject({
      view: 'stock',
      security: 'TEST001',
      return_view: 'news',
      article: snapshot,
      news_type: 'announcement',
      news_source: 'synthetic-wire',
      q: 'robot',
      page: 3,
    });
    expect(parseState(stateSearch(parsed.state, false, true), false, true).state).toEqual(parsed.state);
  });

  it.each([
    'article=not-a-uuid',
    `article=${snapshot}&news_type=unknown`,
    `article=${snapshot}&news_type=company`,
    `article=${snapshot}&news_source=${'s'.repeat(201)}`,
  ])('rejects invalid news state without retaining it: %s', invalid => {
    const parsed = parseState(`?view=news&${invalid}`, false, true);
    expect(parsed.notices).toHaveLength(1);
    expect(parsed.state).toMatchObject({ article: '', news_type: '', news_source: '' });
  });

  it.each([
    'emotion_leader', 'trend_leader', 'zhongjun', 'genfeng', 'buZhang', 'jieli',
  ])('round trips the allowed role %s with leaders filters', role => {
    const parsed = parseState(
      `?view=leaders&snapshot=${snapshot}&date=2026-09-10&sector=SEC001&role=${role}`,
      false,
      true,
    );
    expect(parsed.notices).toEqual([]);
    expect(parsed.state).toMatchObject({ view: 'leaders', snapshot, date: '2026-09-10', sector: 'SEC001', role });
    expect(parseState(stateSearch(parsed.state, false, true), false, true).state).toEqual(parsed.state);
  });

  it('rejects non-whitelisted roles without losing other leaders filters', () => {
    const parsed = parseState(
      `?view=leaders&snapshot=${snapshot}&date=2026-09-10&sector=SEC001&role=Trend_leader`,
      false,
      true,
    );
    expect(parsed.state).toMatchObject({
      view: 'leaders', snapshot, date: '2026-09-10', sector: 'SEC001',
      role: 'trend_leader',
    });
    expect(parsed.notices).toHaveLength(1);
  });

  it('does not interpret paths in legacy mode', () => {
    expect(parseState('?view=analysis', false, false, '/stock/TEST001').state.view).toBe('analysis');
  });

  it('defaults and round trips strict benchmark and adjustment parameters', () => {
    const defaults = parseState('?view=analysis', false, true);
    expect(defaults.state).toMatchObject({ benchmark: 'B000300', adj: 'raw' });

    const parsed = parseState('?view=analysis&benchmark=B000905&adj=hfq', false, true);
    expect(parsed.notices).toEqual([]);
    expect(parsed.state).toMatchObject({ benchmark: 'B000905', adj: 'hfq' });
    const query = new URLSearchParams(stateSearch(parsed.state, false, true));
    expect(query.get('benchmark')).toBe('B000905');
    expect(query.get('adj')).toBe('hfq');
  });

  it.each(['forward', 'RAW', '', 'null'])('rejects invalid adjustment %s without retaining it', adj => {
    const parsed = parseState(`?view=analysis&adj=${encodeURIComponent(adj)}`, false, true);
    expect(parsed.state.adj).toBe('raw');
    expect(parsed.notices).toHaveLength(1);
  });

  it.each(['/stock/%E0%A4%A', '/stock/%2E%2E', '/stock/' + 'A'.repeat(201)])('rejects invalid standalone ID: %s', path => {
    const parsed = parseState('', false, true, path);
    expect(parsed.state.security).toBe('');
    expect(parsed.notices.length).toBeGreaterThan(0);
  });
});
