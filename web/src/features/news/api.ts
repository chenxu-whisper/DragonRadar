import type {
  NewsArticle,
  NewsDetailRequest,
  NewsListArticle,
  NewsListRequest,
  NewsType,
} from './types';

type Failure = 'malformed_response' | 'selection_mismatch' | 'article_mismatch' | null;
const UUID = /^[\da-f]{8}-[\da-f]{4}-[1-5][\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i;
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
