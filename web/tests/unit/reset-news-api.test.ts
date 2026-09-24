import { describe, expect, test } from 'vitest';
import {
  ARTICLE_A,
  articleA,
  listArticleA,
  newsEnvelope,
  NEWS_SNAPSHOT,
} from '../fixtures/news';

describe('news runtime contract', () => {
  test('builds a fixed-snapshot list request with all supported filters', async () => {
    const { newsListPath } = await import('../../src/features/news/api');
    const path = newsListPath({
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
      type: 'announcement',
      source: 'synthetic-wire',
      q: 'robot',
      securityId: 'TEST001',
      page: 2,
      size: 20,
    });
    const url = new URL(path, 'https://example.test');

    expect(url.pathname).toBe('/api/news');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      date: '2026-09-10',
      snapshot_id: NEWS_SNAPSHOT,
      type: 'announcement',
      source: 'synthetic-wire',
      q: 'robot',
      security_id: 'TEST001',
      page: '2',
      size: '20',
    });
  });

  test('rejects malformed list data and mismatched pagination', async () => {
    const { validateNewsListResponse } = await import('../../src/features/news/api');
    const request = {
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
      type: '' as const,
      source: '',
      q: '',
      securityId: '',
      page: 1,
      size: 20,
    };

    expect(validateNewsListResponse(newsEnvelope({
      articles: [listArticleA],
      page: 1,
      size: 20,
      total: 1,
      sources: ['synthetic-wire'],
      event_count: 1,
    }), request)).toBeNull();
    expect(validateNewsListResponse(newsEnvelope({
      articles: 'old payload',
      page: 1,
      size: 20,
      total: 1,
      sources: [],
      event_count: 1,
    }), request)).toBe('malformed_response');
    expect(validateNewsListResponse(newsEnvelope({
      articles: [listArticleA],
      page: 2,
      size: 20,
      total: 1,
      sources: ['synthetic-wire'],
      event_count: 1,
    }), request)).toBe('selection_mismatch');
    expect(validateNewsListResponse(newsEnvelope({
      articles: [{ ...listArticleA, article_type: 'company' }],
      page: 1,
      size: 20,
      total: 1,
      sources: ['synthetic-wire'],
      event_count: 1,
    }), request)).toBe('malformed_response');
    expect(validateNewsListResponse(newsEnvelope({
      items: [listArticleA],
      page: 1,
      size: 20,
      total: 1,
      sources: ['synthetic-wire'],
      event_count: 1,
    }), request)).toBe('malformed_response');
  });

  test('accepts the frozen detail contract and rejects another article or snapshot', async () => {
    const { validateNewsDetailResponse } = await import('../../src/features/news/api');

    expect(validateNewsDetailResponse(newsEnvelope(articleA), {
      articleId: ARTICLE_A,
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
    })).toBeNull();
    expect(validateNewsDetailResponse(newsEnvelope({
      ...articleA,
      article_id: '00000000-0000-4000-8000-000000000199',
    }), {
      articleId: ARTICLE_A,
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
    })).toBe('article_mismatch');
    expect(validateNewsDetailResponse(newsEnvelope(articleA, {
      snapshot_id: '00000000-0000-4000-8000-000000000199',
    }), {
      articleId: ARTICLE_A,
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
    })).toBe('selection_mismatch');
    expect(validateNewsDetailResponse(newsEnvelope({
      ...articleA,
      source: { id: 'synthetic-wire', version: 'news-v1' },
    }), {
      articleId: ARTICLE_A,
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
    })).toBe('malformed_response');
    expect(validateNewsDetailResponse(newsEnvelope({
      ...articleA,
      supporting_evidence: [' '],
    }), {
      articleId: ARTICLE_A,
      date: '2026-09-10',
      snapshot: NEWS_SNAPSHOT,
    })).toBe('malformed_response');
  });
});
