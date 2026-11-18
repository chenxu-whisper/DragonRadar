import type { NewsListArticle } from '../news/types';

export const SEARCH_GROUPS = ['securities', 'topics', 'messages'] as const;
export type SearchGroup = typeof SEARCH_GROUPS[number];
export interface GlobalSearchRequest {
  snapshot: string; date: string; query: string; page: number; size: number;
}
export interface SearchSecurity {
  security_id: string; code: string | null; name: string | null; exchange: string;
  match_field: 'security_id' | 'code' | 'name';
}
export interface SearchTopic { sector_id: string; name: string; match_field: 'sector_id' | 'name' }
export type SearchMessage = NewsListArticle & { match_field: 'article_id' | 'title' };
export interface GlobalSearchData {
  query: string; page: number; size: number; total: number;
  groups: { securities: SearchSecurity[]; topics: SearchTopic[]; messages: SearchMessage[] };
  group_status: Record<SearchGroup,
    { status: 'ready'; total: number; reason: null } |
    { status: 'unavailable'; total: null; reason: string }>;
}
const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string' && Boolean(value.trim());
const identity = (value: unknown): value is string =>
  text(value) && value.length <= 200 && value !== '.' && value !== '..' && !/[/\\?#\u0000-\u001f]/.test(value);
const nullableText = (value: unknown) => value === null || typeof value === 'string';
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const keys = (value: Record<string, unknown>, expected: readonly string[]) =>
  Object.keys(value).length === expected.length && expected.every(key => key in value);
const timestamp = (value: unknown) => text(value) && Number.isFinite(Date.parse(value));

function itemValid(group: SearchGroup, value: unknown): value is Record<string, unknown> {
  if (!record(value)) return false;
  if (group === 'securities') return keys(value, ['security_id', 'code', 'name', 'exchange', 'match_field']) &&
    identity(value.security_id) && nullableText(value.code) && nullableText(value.name) && text(value.exchange) &&
    ['security_id', 'code', 'name'].includes(String(value.match_field));
  if (group === 'topics') return keys(value, ['sector_id', 'name', 'match_field']) &&
    identity(value.sector_id) && text(value.name) && ['sector_id', 'name'].includes(String(value.match_field));
  return keys(value, ['article_id', 'title', 'source', 'source_version', 'published_at', 'available_at',
    'article_type', 'summary', 'event_id', 'revision', 'match_field']) &&
    typeof value.article_id === 'string' && UUID.test(value.article_id) &&
    typeof value.event_id === 'string' && UUID.test(value.event_id) &&
    text(value.title) && text(value.source) && text(value.source_version) &&
    timestamp(value.published_at) && timestamp(value.available_at) &&
    ['news', 'announcement', 'research'].includes(String(value.article_type)) &&
    nullableText(value.summary) && count(value.revision) && value.revision >= 1 &&
    ['article_id', 'title'].includes(String(value.match_field));
}

export function validateGlobalSearch(value: unknown, expected: GlobalSearchRequest):
  'selection_mismatch' | 'malformed_response' | undefined {
  if (!record(value)) return 'malformed_response';
  if (value.snapshot_id !== expected.snapshot || value.trade_date !== expected.date) return 'selection_mismatch';
  if (value.data === null) return Array.isArray(value.quality_flags) &&
    value.quality_flags.includes('materialization_unavailable') ? undefined : 'malformed_response';
  const data = value.data;
  if (!record(data)) return 'malformed_response';
  if (data.query !== expected.query || data.page !== expected.page || data.size !== expected.size) return 'selection_mismatch';
  if (!keys(data, ['query', 'page', 'size', 'total', 'groups', 'group_status']) ||
    !count(data.total) || !count(data.page) || data.page < 1 || data.page > 100000 ||
    ![20, 50, 100].includes(Number(data.size)) || !record(data.groups) || !record(data.group_status) ||
    !keys(data.groups, SEARCH_GROUPS) || !keys(data.group_status, SEARCH_GROUPS)) return 'malformed_response';
  let offset = 0;
  const start = (expected.page - 1) * expected.size, end = start + expected.size;
  for (const group of SEARCH_GROUPS) {
    const items = data.groups[group], status = data.group_status[group];
    if (!Array.isArray(items) || !record(status) || !keys(status, ['status', 'total', 'reason'])) return 'malformed_response';
    if (status.status === 'unavailable') {
      if (status.total !== null || !text(status.reason) || items.length !== 0) return 'malformed_response';
      continue;
    }
    if (status.status !== 'ready' || !count(status.total) || status.reason !== null) return 'malformed_response';
    const expectedCount = Math.max(0, Math.min(end, offset + status.total) - Math.max(start, offset));
    if (items.length !== expectedCount) return 'malformed_response';
    let previous: string | undefined;
    const field = group === 'securities' ? 'security_id' : group === 'topics' ? 'sector_id' : 'article_id';
    for (const item of items) {
      if (!itemValid(group, item)) return 'malformed_response';
      const id = String(item[field]);
      if (previous !== undefined && id <= previous) return 'malformed_response';
      previous = id;
    }
    offset += status.total;
    if (!Number.isSafeInteger(offset)) return 'malformed_response';
  }
  if (offset !== data.total) return 'malformed_response';
}
