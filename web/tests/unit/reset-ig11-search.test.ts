import { describe, expect, it } from 'vitest';
import { validateGlobalSearch } from '../../src/features/workbench/search-validation';
import { sortOverviewMembers } from '../../src/features/workbench/overview-members';

const expected = { snapshot: '11111111-1111-4111-8111-111111111111', date: '2026-09-10', query: 'A', page: 1, size: 50 };
function response() {
  return {
    snapshot_id: expected.snapshot, trade_date: expected.date, quality_flags: [],
    data: {
      query: 'A', page: 1, size: 50, total: 1,
      groups: {
        securities: [{ security_id: 'A', code: '000001', name: null, exchange: 'SSE', match_field: 'security_id' }],
        topics: [], messages: [],
      },
      group_status: {
        securities: { status: 'ready', total: 1, reason: null },
        topics: { status: 'ready', total: 0, reason: null },
        messages: { status: 'ready', total: 0, reason: null },
      },
    },
  };
}
describe('global archive search validation', () => {
  it('accepts exact publication selection and nullable historical names', () => {
    expect(validateGlobalSearch(response(), expected)).toBeUndefined();
  });
  it.each(['snapshot', 'date', 'query', 'page', 'size'] as const)('rejects mismatched %s', field => {
    const value = response();
    if (field === 'snapshot') value.snapshot_id = 'other';
    else if (field === 'date') value.trade_date = '2026-09-09';
    else if (field === 'query') value.data.query = 'B';
    else value.data[field] += 1;
    expect(validateGlobalSearch(value, expected)).toBe('selection_mismatch');
  });
  it.each(['total', 'group_total', 'missing_group', 'extra_group', 'identity', 'match', 'duplicate', 'extra_field', 'wrong_type', 'short_page'] as const)(
    'fails closed on malformed %s', field => {
      const value = response();
      if (field === 'total') value.data.total = 2;
      if (field === 'group_total') value.data.group_status.securities.total = 2;
      if (field === 'missing_group') Reflect.deleteProperty(value.data.groups, 'topics');
      if (field === 'extra_group') Reflect.set(value.data.groups, 'private', []);
      if (field === 'identity') value.data.groups.securities[0].security_id = '..';
      if (field === 'match') value.data.groups.securities[0].match_field = 'invented';
      if (field === 'duplicate') value.data.groups.securities.push(value.data.groups.securities[0]);
      if (field === 'extra_field') Reflect.set(value.data.groups.securities[0], 'content', 'private');
      if (field === 'wrong_type') Reflect.set(value.data.groups.securities[0], 'name', 42);
      if (field === 'short_page') value.data.groups.securities = [];
      expect(validateGlobalSearch(value, expected)).toBe('malformed_response');
    },
  );
  it('distinguishes an unavailable group from ready zero', () => {
    const value = response();
    Reflect.set(value.data.group_status, 'topics', { status: 'unavailable', total: null, reason: 'archive_missing' });
    expect(validateGlobalSearch(value, expected)).toBeUndefined();
    Reflect.set(value.data.group_status, 'topics', { status: 'unavailable', total: 0, reason: null });
    expect(validateGlobalSearch(value, expected)).toBe('malformed_response');
  });
  it('validates message types and suppresses unknown fields', () => {
    const value = response();
    value.data.groups.securities = [];
    value.data.group_status.securities.total = 0;
    value.data.group_status.messages.total = 1;
    Reflect.set(value.data.groups, 'messages', [{
      article_id: '22222222-2222-4222-8222-222222222222', title: 'A',
      source: 'synthetic', source_version: 'v1', published_at: '2026-09-10T08:00:00Z',
      available_at: '2026-09-10T08:00:00Z', article_type: 'announcement',
      summary: null, event_id: '33333333-3333-4333-8333-333333333333',
      revision: 1, match_field: 'title',
    }]);
    expect(validateGlobalSearch(value, expected)).toBeUndefined();
    Reflect.set((value.data.groups.messages as Record<string, unknown>[])[0], 'article_type', 'private');
    expect(validateGlobalSearch(value, expected)).toBe('malformed_response');
  });
});
describe('overview archived member sorting', () => {
  const rows = [
    { security_id: 'B', code: '01', name: 'Alpha' },
    { security_id: 'A', code: '02', name: 'Zulu' },
    { security_id: 'C', code: null, name: null },
    { security_id: 'D', code: '', name: '  ' },
  ];
  it.each(['asc', 'desc'] as const)('sorts real code and ID distinctly in %s order', order => {
    expect(sortOverviewMembers(rows, 'security_id', order)[0].security_id).toBe(order === 'asc' ? 'A' : 'D');
    expect(sortOverviewMembers(rows, 'code', order)[0].security_id).toBe(order === 'asc' ? 'B' : 'A');
  });
  it.each(['asc', 'desc'] as const)('places null and blank names last in %s order', order => {
    const sorted = sortOverviewMembers(rows, 'name', order);
    expect(sorted[0].security_id).toBe(order === 'asc' ? 'B' : 'A');
    expect(sorted.slice(-2).map(row => row.security_id)).toEqual(['C', 'D']);
    expect(rows[0].security_id).toBe('B');
  });
});
