import type { Page, Route } from '@playwright/test';

export const NEWS_SNAPSHOT = '00000000-0000-4000-8000-000000000141';
export const ARTICLE_A = '00000000-0000-4000-8000-000000000151';
export const ARTICLE_B = '00000000-0000-4000-8000-000000000152';
// Word-cloud-fixture only: never part of the /api/news list or article mocks.
// It models licensed documents that contribute terms without a list row.
const ARTICLE_C = '00000000-0000-4000-8000-000000000153';

const WORDCLOUD_TOKENIZER_VERSION = 'wordcloud-tokenizer-v1';

const coverage = { expected: 2, valid: 2, excluded: 0, missing: 0 };
const basis = { unit: null, currency: null, price_type: null, benchmark: null };

export function newsEnvelope(data: unknown, overrides: Record<string, unknown> = {}) {
  return {
    trade_date: '2026-09-10',
    as_of: '2026-09-10T10:00:00Z',
    snapshot_id: NEWS_SNAPSHOT,
    source: 'news_materialization',
    source_version: 'news-v1',
    coverage,
    quality_flags: [],
    rule_version: 'news-v1',
    basis,
    data,
    ...overrides,
  };
}

export const articleA = {
  article_id: ARTICLE_A,
  revision_id: '00000000-0000-4000-8000-000000000161',
  revision: 1,
  event_id: '00000000-0000-4000-8000-000000000171',
  article_type: 'announcement',
  source: 'synthetic-wire',
  source_version: 'news-v1',
  title: '机器人公司签署合成测试订单',
  url: 'https://example.test/news/a/very/long/path/that/must/wrap',
  published_at: '2026-09-10T07:00:00Z',
  received_at: '2026-09-10T08:00:00Z',
  available_at: '2026-09-10T09:00:00Z',
  content_scope: 'summary',
  summary: '仅获摘要展示许可的合成消息。',
  content: null,
  ai_summary: null,
  related_security_ids: ['TEST001'],
  is_novel_event: true,
  supporting_evidence: ['首次披露合成测试订单。'],
  opposing_evidence: ['订单仍需满足交割条件。'],
  securities: [{
    association_id: '00000000-0000-4000-8000-000000000181',
    security_id: 'TEST001',
    method: 'direct',
    status: 'accepted',
    evidence_snippets: ['公告主体代码 TEST001'],
    effective: true,
    pending_topic_publication: false,
  }],
  topics: [{
    association_id: '00000000-0000-4000-8000-000000000182',
    topic_id: 'SEC001',
    method: 'ai',
    status: 'unreviewed',
    evidence_snippets: ['机器人业务描述'],
    effective: false,
    pending_topic_publication: false,
  }],
};

export const articleB = {
  ...articleA,
  article_id: ARTICLE_B,
  revision_id: '00000000-0000-4000-8000-000000000162',
  event_id: articleA.event_id,
  article_type: 'research',
  title: '行业跟踪：同一事件的第二篇报道与超长标题用于验证窄屏换行不会造成页面横向溢出',
  url: 'https://example.test/news/b',
  content_scope: 'none',
  summary: null,
  securities: [],
  topics: [],
};

export const listArticleA = {
  article_id: articleA.article_id,
  title: articleA.title,
  source: articleA.source,
  source_version: articleA.source_version,
  published_at: articleA.published_at,
  available_at: articleA.available_at,
  article_type: articleA.article_type,
  summary: articleA.summary,
  event_id: articleA.event_id,
  revision: articleA.revision,
};

export const listArticleB = {
  ...listArticleA,
  article_id: articleB.article_id,
  title: articleB.title,
  article_type: articleB.article_type,
  summary: articleB.summary,
};

type CloudGroup = 'all' | 'announcement' | 'news' | 'research';
type CloudScope = { titles: number; summaries: number; contents: number };

function cloudWord(term: string, count: number, ids: string[]) {
  // Canonical ascending article ids mirror the backend contract the frontend
  // validator enforces fail-closed.
  return { term, document_count: count, article_ids: [...ids].sort(), sentiment: null };
}
type CloudWord = ReturnType<typeof cloudWord>;

// Word-cloud groups and text scopes are synthesized independently of the
// article-list mock on purpose: the frontend validator only checks the
// wordcloud block contract and never cross-correlates terms with list rows
// (the backend materializes them from different licensed-text projections).
// E.g. the research list still carries article B while its cloud group is
// empty because that revision grants no licensed text (titles: 0).
//
// Ordered by document_count desc, ties by Unicode code point asc; the
// validator rejects any other ordering.
const WORDCLOUD_WORDS: Record<CloudGroup, CloudWord[]> = {
  all: [
    cloudWord('机器人', 2, [ARTICLE_A, ARTICLE_C]),
    cloudWord('订单', 2, [ARTICLE_A, ARTICLE_B]),
    cloudWord('robot', 1, [ARTICLE_C]),
    cloudWord('涨停', 1, [ARTICLE_A]),
    cloudWord('行业', 1, [ARTICLE_B]),
  ],
  announcement: [cloudWord('订单', 1, [ARTICLE_A])],
  news: [cloudWord('机器人', 1, [ARTICLE_A])],
  research: [],
};

const WORDCLOUD_SCOPE: Record<CloudGroup, CloudScope> = {
  all: { titles: 3, summaries: 2, contents: 0 },
  announcement: { titles: 1, summaries: 1, contents: 0 },
  news: { titles: 1, summaries: 1, contents: 0 },
  research: { titles: 0, summaries: 0, contents: 0 },
};

export type NewsMockState = {
  malformed?: boolean;
  wrongArticle?: boolean;
  offline?: boolean;
  wordcloud?: 'null' | 'malformed';
};

export async function mockNews(page: Page, state: NewsMockState = {}) {
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const respond = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (state.offline) return route.abort('failed');
    if (url.pathname === '/api/snapshots/list') {
      return respond(newsEnvelope({ snapshots: [newsEnvelope({
        revision: 1,
        supersedes: null,
        published_at: '2026-09-10T10:05:00Z',
        security_count: 2,
        calendar_sessions: ['2026-09-10'],
        earliest_date: '2026-09-10',
      })] }));
    }
    if (url.pathname === `/api/snapshots/${NEWS_SNAPSHOT}`) {
      return respond(newsEnvelope({
        revision: 1,
        supersedes: null,
        published_at: '2026-09-10T10:05:00Z',
        security_count: 2,
        calendar_sessions: ['2026-09-10'],
        earliest_date: '2026-09-10',
      }));
    }
    if (url.pathname === '/api/news') {
      if (state.malformed) return respond(newsEnvelope({ items: 'stale malformed news' }));
      const type = url.searchParams.get('type');
      const source = url.searchParams.get('source');
      const q = (url.searchParams.get('q') || '').toLowerCase();
      const pageNumber = Number(url.searchParams.get('page') || 1);
      let articles = [listArticleA, listArticleB];
      if (type) articles = articles.filter(item => item.article_type === type);
      if (source) articles = articles.filter(item => item.source === source);
      if (q) articles = articles.filter(item => `${item.title} ${item.summary || ''}`.toLowerCase().includes(q));
      return respond(newsEnvelope({
        articles,
        page: pageNumber,
        size: Number(url.searchParams.get('size') || 20),
        total: articles.length,
        sources: ['synthetic-wire'],
        event_count: new Set(articles.map(item => item.event_id)).size,
      }));
    }
    if (url.pathname === '/api/news/wordcloud') {
      // Must precede the startsWith('/api/news/') article branch, otherwise
      // "wordcloud" would be treated as an article id and 404.
      if (state.wordcloud === 'malformed') {
        // Declares 2 documents while carrying a single article id: the
        // fail-closed validator must discard the whole block.
        return respond(newsEnvelope({
          type: 'all',
          top_n: 40,
          total: 1,
          words: [cloudWord('坏块', 2, [ARTICLE_A])],
          text_scope: { titles: 1, summaries: 0, contents: 0 },
          tokenizer_version: WORDCLOUD_TOKENIZER_VERSION,
        }));
      }
      if (state.wordcloud === 'null') {
        return respond(newsEnvelope(null));
      }
      const requestedGroup = url.searchParams.get('type') || 'all';
      const group: CloudGroup = requestedGroup === 'announcement'
        || requestedGroup === 'news' || requestedGroup === 'research'
        ? requestedGroup
        : 'all';
      const topN = Number(url.searchParams.get('top_n') || '40');
      const full = WORDCLOUD_WORDS[group];
      return respond(newsEnvelope({
        type: group,
        top_n: topN,
        total: full.length,
        words: full.slice(0, topN),
        text_scope: WORDCLOUD_SCOPE[group],
        tokenizer_version: WORDCLOUD_TOKENIZER_VERSION,
      }));
    }
    if (url.pathname.startsWith('/api/news/')) {
      const requested = decodeURIComponent(url.pathname.split('/').at(-1)!);
      const found = requested === ARTICLE_A ? articleA : requested === ARTICLE_B ? articleB : null;
      if (!found) return respond({ code: 'NOT_FOUND' }, 404);
      return respond(newsEnvelope(state.wrongArticle ? { ...found, article_id: ARTICLE_B } : found));
    }
    if (url.pathname === '/api/stock/TEST001') {
      return respond(newsEnvelope({
        security_id: 'TEST001',
        name: '合成测试证券1',
        quote: { last: 11, change_ratio: 0.1, amount: 120000 },
        roles: [],
        seal: {
          is_limit_up: null, touched_limit: null, first_seal_time: null,
          last_seal_time: null, break_count: null, seal_amount: null,
          seal_amount_unit: null, continuous_boards: null, reason: 'seal_event_missing',
        },
        regulation: {
          formal_event_count: 0,
          event_source_completeness: 'complete',
          reason: null,
        },
      }));
    }
    return respond({}, 404);
  });
}
