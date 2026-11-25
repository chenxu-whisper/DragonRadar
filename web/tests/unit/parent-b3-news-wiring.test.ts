import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  newsDetailPath, newsListPath, validateNewsDetailResponse, validateNewsListResponse,
} from '../../src/features/news/api';
import { News } from '../../src/pages/News';
import { parseState } from '../../src/state';
import { ARTICLE_A, ARTICLE_B, NEWS_SNAPSHOT, articleA, listArticleA, newsEnvelope } from '../fixtures/news';

const resources = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock('../../src/hooks/useResource', () => ({ useResource: resources.read }));

const request = {
  date: '2026-09-10', snapshot: NEWS_SNAPSHOT, type: '' as const, source: '',
  q: '', securityId: '', page: 1, size: 20,
};
const detailRequest = { date: request.date, snapshot: request.snapshot, articleId: ARTICLE_A };
const preview = {
  status: 'ready' as const, text: 'Frozen reviewed preview', truncated: true, reason: null,
  summary_id: '00000000-0000-4000-8000-000000000191', summary_revision: 3,
  provenance: 'ai_human_edited' as const,
};
const unavailable = {
  status: 'unavailable' as const, text: null, truncated: false,
  reason: 'summary_display_denied', summary_id: null, summary_revision: null, provenance: null,
};
function list(value: unknown = preview) {
  return newsEnvelope({
    articles: [{ ...listArticleA, ai_preview: value }],
    page: 1, size: 20, total: 1, sources: ['synthetic-wire'], event_count: 1,
  });
}
function detail(value: unknown = preview) {
  return newsEnvelope({ ...articleA, ai_preview: value });
}

beforeEach(() => resources.read.mockReset());

describe('parent B3 news API integration', () => {
  it('accepts frozen previews, unavailable previews, null, and old v2 absence', () => {
    for (const value of [preview, unavailable, null]) {
      expect(validateNewsListResponse(list(value), request)).toBeNull();
      expect(validateNewsDetailResponse(detail(value), detailRequest)).toBeNull();
    }
    const legacy = list();
    delete (legacy.data as { articles: Record<string, unknown>[] }).articles[0].ai_preview;
    expect(validateNewsListResponse(legacy, request)).toBeNull();
    expect(validateNewsDetailResponse(newsEnvelope(articleA), detailRequest)).toBeNull();
  });

  it.each([
    { ...preview, owner_id: 'private' },
    { ...preview, text: '\u{1f600}'.repeat(101) },
    { ...preview, summary_revision: true },
    { ...preview, summary_id: 'private-task' },
    { ...preview, provenance: 'unreviewed' },
    { ...unavailable, text: 'withdrawn text' },
    { ...unavailable, reason: 'unknown' },
    undefined,
  ])('rejects malformed embedded previews in both shared validators: %j', value => {
    const response = list();
    (response.data as { articles: Record<string, unknown>[] }).articles[0].ai_preview = value;
    expect(validateNewsListResponse(response, request)).toBe('malformed_response');
    expect(validateNewsDetailResponse(newsEnvelope({ ...articleA, ai_preview: value }), detailRequest))
      .toBe('malformed_response');
  });

  it('uses code points and retains exact PUB/date/article checks', () => {
    const astral = { ...preview, text: '\u{1f600}'.repeat(100) };
    expect(validateNewsListResponse(list(astral), request)).toBeNull();
    expect(validateNewsDetailResponse(detail(astral), detailRequest)).toBeNull();
    expect(validateNewsListResponse(list(), { ...request, snapshot: ARTICLE_B })).toBe('selection_mismatch');
    expect(validateNewsDetailResponse(detail(), { ...detailRequest, date: '2026-09-11' })).toBe('selection_mismatch');
    expect(validateNewsDetailResponse(detail(), { ...detailRequest, articleId: ARTICLE_B })).toBe('article_mismatch');
  });
});

describe('parent B3 shared news rendering', () => {
  function render(listResponse = list(), detailResponse = detail()) {
    resources.read.mockImplementation((path: string | null) => ({
      data: path === newsListPath(request) ? listResponse
        : path === newsDetailPath(detailRequest) ? detailResponse : undefined,
      loading: false, refresh: vi.fn(),
    }));
    const state = parseState(
      `?view=news&snapshot=${NEWS_SNAPSHOT}&date=${request.date}&article=${ARTICLE_A}&page_size=20`,
      false, true,
    ).state;
    return renderToStaticMarkup(createElement(News, {
      state, patch: vi.fn(), refreshToken: 0, open: vi.fn(),
    }));
  }

  it('renders archived list and detail previews with only the two existing reads', () => {
    const fetcher = vi.spyOn(globalThis, 'fetch');
    const markup = render();
    expect(markup.match(/aria-label="AI 复核摘要"/g)).toHaveLength(2);
    expect(markup.match(/Frozen reviewed preview/g)).toHaveLength(2);
    expect(markup.match(/摘要已截断/g)).toHaveLength(2);
    expect(markup.match(/人工编辑后复核/g)).toHaveLength(2);
    expect(resources.read.mock.calls.map(call => call.slice(0, 2))).toEqual([
      [newsListPath(request), NEWS_SNAPSHOT], [newsDetailPath(detailRequest), NEWS_SNAPSHOT],
    ]);
    expect(fetcher).not.toHaveBeenCalled();
    // The preview is a sibling, not a section nested inside the article button.
    expect(markup).not.toMatch(/<button[^>]*class="news-list-row"[^>]*>(?:(?!<\/button>)[\s\S])*<section/);
  });

  it('shows unavailable previews while preserving authorized article text', () => {
    const markup = render(list(unavailable), detail(unavailable));
    expect(markup.match(/AI 暂不可用/g)).toHaveLength(2);
    expect(markup).toContain(articleA.summary);
    expect(markup).not.toContain(preview.text);
  });

  it('keeps old v2 detail summaries readable without inventing a promoted preview', () => {
    const legacyList = list(null);
    delete (legacyList.data as { articles: Record<string, unknown>[] }).articles[0].ai_preview;
    const markup = render(legacyList, newsEnvelope({ ...articleA, ai_summary: 'Legacy frozen AI summary' }));
    expect(markup).toContain('Legacy frozen AI summary');
    expect(markup).not.toContain('aria-label="AI 复核摘要"');
  });
});
