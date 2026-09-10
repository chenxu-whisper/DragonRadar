import { describe, expect, it } from 'vitest';
import { parseState, stateSearch, toggleCompare, intervalQuery } from '../../src/state';

describe('URL research state', () => {
  it('defaults to overview, fifty rows and twenty return offsets', () => {
    expect(parseState('').state).toMatchObject({ view: 'overview', page: 1, page_size: 50, n: 20, compare: [] });
  });
  it('round trips an older snapshot and exact context', () => {
    const query = '?view=analysis&snapshot=00000000-0000-4000-8000-000000000001&date=2024-02-29&q=测试&sort=maximum_drawdown&order=desc&page=3&page_size=20&start=2024-02-01&end=2024-02-29&compare=A,B';
    const { state } = parseState(query);
    expect(parseState(stateSearch(state)).state).toEqual(state);
    expect(state.start).toBe('2024-02-01');
  });
  it('sanitizes malformed selectors and reports the changes', () => {
    const result = parseState('?view=bad&snapshot=oops&date=2024-02-30&page=-1&page_size=7&n=251&sort=bad');
    expect(result.state).toMatchObject({ view: 'overview', snapshot: '', date: '', page: 1, page_size: 50, n: 20 });
    expect(result.notices.length).toBeGreaterThan(0);
  });
  it('rejects all oversized comparison URL input with an explicit notice', () => {
    const result = parseState('?compare=A,B,C,D,E,F');
    expect(result.state.compare).toEqual([]);
    expect(result.notices.join('')).toContain('最多');
  });
  it('does not silently truncate the sixth selection', () => {
    const ids = ['A', 'B', 'C', 'D', 'E'];
    expect(toggleCompare(ids, 'F')).toEqual({ ids, error: '最多比较 5 个标的，请先移除一个。' });
    expect(toggleCompare(ids, 'C').ids).toEqual(['A', 'B', 'D', 'E']);
  });
  it('rejects empty, reversed or partial custom intervals', () => {
    expect(() => intervalQuery({ n: 0, start: '', end: '' })).toThrow();
    expect(() => intervalQuery({ n: 20, start: '2026-09-10', end: '' })).toThrow();
    expect(() => intervalQuery({ n: 20, start: '2026-09-10', end: '2026-09-01' })).toThrow();
  });
  it('preserves exact common endpoints for comparison', () => {
    expect(intervalQuery({ n: 20, start: '2026-09-01', end: '2026-09-10' }, ['A', 'B'])).toBe('start=2026-09-01&end=2026-09-10&ids=A%2CB');
  });
});
