import { describe, expect, it } from 'vitest';
import { validateNewsListResponse } from '../../src/features/news/api';
import { article, DATE, PUB, envelope } from '../fixtures/parent-frontend-fixes-data';
import { articles as c2Articles } from '../fixtures/contract-c2-scenes';

const request = { date: DATE, snapshot: PUB, type: '' as const, source: '', q: '',
  securityId: '', page: 1, size: 20 };
const list = (value: unknown) => envelope({ articles: [value], page: 1, size: 20, total: 1,
  sources: ['synthetic'], event_count: 1 });
describe('news list receipt and revision contract', () => {
  it('C2 download fixtures include authoritative new list columns for every page', () => {
    for (const item of c2Articles) {
      expect(item).toHaveProperty('received_at');
      expect(item).toHaveProperty('revision_id');
      expect(validateNewsListResponse(list(item), request)).toBeNull();
    }
  });
  it('preserves new metadata and permits only genuine legacy absence', () => {
    const response = list(article);
    expect(validateNewsListResponse(response, request)).toBeNull();
    expect((response.data as { articles: unknown[] }).articles[0]).toEqual(article);
    const { received_at: _received, revision_id: _revision, ...legacy } = article;
    expect(validateNewsListResponse(list(legacy), request)).toBeNull();
  });
  it.each([null, undefined, '', 1, 'not-a-timestamp', '2026-11-24T01:00:00'])(
    'rejects malformed or zoneless present received_at: %j', received_at => {
      expect(validateNewsListResponse(list({ ...article, received_at }), request)).toBe('malformed_response');
    },
  );
  it.each([null, undefined, '', 1, 'not-a-uuid'])('rejects malformed present revision_id: %j', revision_id => {
    expect(validateNewsListResponse(list({ ...article, revision_id }), request)).toBe('malformed_response');
  });
});
