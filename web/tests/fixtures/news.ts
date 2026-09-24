import type { Page, Route } from '@playwright/test';

export const NEWS_SNAPSHOT = '00000000-0000-4000-8000-000000000141';
export const ARTICLE_A = '00000000-0000-4000-8000-000000000151';
export const ARTICLE_B = '00000000-0000-4000-8000-000000000152';

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
  type: 'announcement',
  source: { id: 'synthetic-wire', version: 'news-v1' },
  title: '机器人公司签署合成测试订单',
  url: 'https://example.test/news/a/very/long/path/that/must/wrap',
  published_at: '2026-09-10T07:00:00Z',
  received_at: '2026-09-10T08:00:00Z',
  available_at: '2026-09-10T09:00:00Z',
  content_scope: 'summary',
  summary: '仅获摘要展示许可的合成消息。',
  content: null,
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
  type: 'research',
  title: '行业跟踪：同一事件的第二篇报道与超长标题用于验证窄屏换行不会造成页面横向溢出',
  url: 'https://example.test/news/b',
  content_scope: 'none',
  summary: null,
  securities: [],
  topics: [],
};

export type NewsMockState = {
  malformed?: boolean;
  wrongArticle?: boolean;
  offline?: boolean;
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
      let items = [articleA, articleB];
      if (type) items = items.filter(item => item.type === type);
      if (source) items = items.filter(item => item.source.id === source);
      if (q) items = items.filter(item => `${item.title} ${item.summary || ''}`.toLowerCase().includes(q));
      return respond(newsEnvelope({
        items,
        page: pageNumber,
        size: Number(url.searchParams.get('size') || 20),
        total: items.length,
        sources: ['synthetic-wire'],
        event_count: new Set(items.map(item => item.event_id)).size,
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
        regulation: { active_events: 0, reason: null },
      }));
    }
    return respond({}, 404);
  });
}
