import type {
  NewsArticle,
  NewsDetailRequest,
  NewsListArticle,
  NewsListRequest,
  NewsType,
  WordCloudRequest,
} from './types';

type Failure = 'malformed_response' | 'selection_mismatch' | 'article_mismatch' | null;
const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
// Word cloud article_ids are canonical lowercase UUIDs; the generic UUID
// helper above keeps the i flag for legacy news payloads.
const UUID_LOWER = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/;

export const WORDCLOUD_TOKENIZER_V1 = 'wordcloud-tokenizer-v1';
export const WORDCLOUD_GROUPS = ['all', 'announcement', 'news', 'research'] as const;
export const WORDCLOUD_MAX_TOP_N = 100;
export const WORDCLOUD_MIN_TOP_N = 1;

const WORDCLOUD_WORD_KEYS = ['term', 'document_count', 'article_ids', 'sentiment'] as const;
const WORDCLOUD_SCOPE_KEYS = ['titles', 'summaries', 'contents'] as const;
const WORDCLOUD_DATA_KEYS = [
  'type', 'top_n', 'total', 'words', 'text_scope', 'tokenizer_version',
] as const;
const TYPES: NewsType[] = ['', 'news', 'announcement', 'research'];

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string');
}

function nonblankStrings(value: unknown): value is string[] {
  return strings(value) && value.every(item => Boolean(item.trim()));
}

function association(value: unknown, target: 'security' | 'topic'): boolean {
  if (!record(value)) return false;
  const id = value[`${target}_id`];
  return typeof value.association_id === 'string' && UUID.test(value.association_id) &&
    typeof id === 'string' && Boolean(id) &&
    ['direct', 'manual', 'ai'].includes(String(value.method)) &&
    ['accepted', 'unreviewed', 'rejected'].includes(String(value.status)) &&
    nonblankStrings(value.evidence_snippets) && value.evidence_snippets.length > 0 &&
    typeof value.effective === 'boolean' &&
    typeof value.pending_topic_publication === 'boolean';
}

function listArticle(value: unknown): value is NewsListArticle {
  if (!record(value)) return false;
  return typeof value.article_id === 'string' && UUID.test(value.article_id) &&
    Number.isInteger(value.revision) && Number(value.revision) >= 1 &&
    typeof value.event_id === 'string' && UUID.test(value.event_id) &&
    TYPES.slice(1).includes(value.article_type as NewsType) &&
    typeof value.source === 'string' && Boolean(value.source) &&
    typeof value.source_version === 'string' && Boolean(value.source_version) &&
    typeof value.title === 'string' && Boolean(value.title) &&
    ['published_at', 'available_at'].every(
      field => typeof value[field] === 'string' && Number.isFinite(Date.parse(String(value[field]))),
    ) &&
    (value.summary === null || typeof value.summary === 'string');
}

function article(value: unknown): value is NewsArticle {
  if (!listArticle(value) || !record(value)) return false;
  return typeof value.revision_id === 'string' && UUID.test(value.revision_id) &&
    typeof value.url === 'string' && value.url.startsWith('https://') &&
    typeof value.received_at === 'string' && Number.isFinite(Date.parse(value.received_at)) &&
    ['none', 'summary', 'full'].includes(String(value.content_scope)) &&
    (value.content === null || typeof value.content === 'string') &&
    (value.ai_summary === null || typeof value.ai_summary === 'string') &&
    nonblankStrings(value.related_security_ids) &&
    typeof value.is_novel_event === 'boolean' &&
    nonblankStrings(value.supporting_evidence) &&
    nonblankStrings(value.opposing_evidence) &&
    Array.isArray(value.securities) && value.securities.every(item => association(item, 'security')) &&
    Array.isArray(value.topics) && value.topics.every(item => association(item, 'topic'));
}

function envelope(value: unknown, date: string, snapshot: string): Failure {
  if (!record(value) || value.snapshot_id !== snapshot || value.trade_date !== date) {
    return 'selection_mismatch';
  }
  return null;
}

export function newsListPath(request: NewsListRequest): string {
  return `/api/news?${new URLSearchParams({
    date: request.date,
    snapshot_id: request.snapshot,
    ...(request.type ? { type: request.type } : {}),
    ...(request.source ? { source: request.source } : {}),
    ...(request.q ? { q: request.q } : {}),
    ...(request.securityId ? { security_id: request.securityId } : {}),
    page: String(request.page),
    size: String(request.size),
  })}`;
}

export function newsDetailPath(request: NewsDetailRequest): string {
  return `/api/news/${encodeURIComponent(request.articleId)}?${new URLSearchParams({
    date: request.date,
    snapshot_id: request.snapshot,
  })}`;
}

export function validateNewsListResponse(
  value: unknown,
  request: NewsListRequest,
): Failure {
  const mismatch = envelope(value, request.date, request.snapshot);
  if (mismatch) return mismatch;
  const data = (value as Record<string, unknown>).data;
  if (data === null) return null;
  if (
    record(data) &&
    Number.isInteger(data.page) &&
    Number.isInteger(data.size) &&
    (data.page !== request.page || data.size !== request.size)
  ) {
    return 'selection_mismatch';
  }
  if (
    !record(data) ||
    !Array.isArray(data.articles) ||
    !data.articles.every(listArticle) ||
    data.page !== request.page ||
    data.size !== request.size ||
    !Number.isInteger(data.total) ||
    Number(data.total) < data.articles.length ||
    !strings(data.sources) ||
    !Number.isInteger(data.event_count) ||
    Number(data.event_count) < 0
  ) return 'malformed_response';
  return null;
}

export function validateNewsDetailResponse(
  value: unknown,
  request: NewsDetailRequest,
): Failure {
  const mismatch = envelope(value, request.date, request.snapshot);
  if (mismatch) return mismatch;
  const data = (value as Record<string, unknown>).data;
  if (data === null) return null;
  if (!article(data)) return 'malformed_response';
  return data.article_id === request.articleId ? null : 'article_mismatch';
}

export function wordcloudPath(request: WordCloudRequest): string {
  return `/api/news/wordcloud?${new URLSearchParams({
    date: request.date,
    snapshot_id: request.snapshot,
    top_n: String(request.topN),
    type: request.group,
  })}`;
}

function exactKeys(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) &&
    Object.keys(value).length === keys.length &&
    keys.every(key => Object.prototype.hasOwnProperty.call(value, key));
}

// Python compares archive terms by Unicode code point; compare the same way
// (UTF-16 ordering would disagree for astral characters).
function termAfter(a: string, b: string): boolean {
  const left = Array.from(a, character => character.codePointAt(0) ?? 0);
  const right = Array.from(b, character => character.codePointAt(0) ?? 0);
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    if (left[index] !== right[index]) return left[index] > right[index];
  }
  return left.length > right.length;
}

export function validateWordcloudResponse(
  value: unknown,
  request: WordCloudRequest,
): Failure {
  const mismatch = envelope(value, request.date, request.snapshot);
  if (mismatch) return mismatch;
  const data = (value as Record<string, unknown>).data;
  if (data === null) return null;
  if (!exactKeys(data, WORDCLOUD_DATA_KEYS)) return 'malformed_response';
  const {
    type,
    top_n: topN,
    total,
    words,
    text_scope: scope,
    tokenizer_version: tokenizerVersion,
  } = data;
  if (
    typeof type !== 'string' ||
    !(WORDCLOUD_GROUPS as readonly string[]).includes(type) ||
    type !== request.group ||
    !Number.isInteger(topN) ||
    Number(topN) < WORDCLOUD_MIN_TOP_N ||
    Number(topN) > WORDCLOUD_MAX_TOP_N ||
    topN !== request.topN ||
    !Number.isInteger(total) ||
    Number(total) < 0 ||
    tokenizerVersion !== WORDCLOUD_TOKENIZER_V1 ||
    !Array.isArray(words) ||
    !exactKeys(scope, WORDCLOUD_SCOPE_KEYS) ||
    !WORDCLOUD_SCOPE_KEYS.every(key => {
      const scopeValue = scope[key];
      return Number.isInteger(scopeValue) && Number(scopeValue) >= 0;
    })
  ) return 'malformed_response';

  const seenTerms = new Set<string>();
  let previous: { count: number; term: string } | null = null;
  for (const rawWord of words) {
    if (!exactKeys(rawWord, WORDCLOUD_WORD_KEYS)) return 'malformed_response';
    const { term, document_count: count, article_ids: ids, sentiment } = rawWord;
    if (
      typeof term !== 'string' ||
      term.length === 0 ||
      !Number.isInteger(count) ||
      Number(count) < 1 ||
      sentiment !== null ||
      !Array.isArray(ids) ||
      ids.length === 0 ||
      !ids.every(id => typeof id === 'string' && UUID_LOWER.test(id))
    ) return 'malformed_response';
    const sortedIds = [...ids].sort();
    if (
      count !== ids.length ||
      new Set(ids).size !== ids.length ||
      sortedIds.some((sorted, index) => sorted !== ids[index])
    ) return 'malformed_response';
    if (seenTerms.has(term)) return 'malformed_response';
    seenTerms.add(term);
    if (
      previous !== null &&
      (Number(count) > previous.count ||
        (Number(count) === previous.count && !termAfter(term, previous.term)))
    ) return 'malformed_response';
    previous = { count: Number(count), term };
  }
  // The route always serves words[:top_n], whose length is exactly
  // min(total, top_n); reject both over-delivery and truncated archives.
  if (words.length !== Math.min(Number(total), Number(topN))) return 'malformed_response';
  return null;
}
