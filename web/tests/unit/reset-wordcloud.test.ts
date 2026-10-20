import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  WORDCLOUD_TOKENIZER_V1,
  validateWordcloudResponse,
  wordcloudPath,
} from '../../src/features/news/api';
import { cloudLayout, WordCloudView } from '../../src/features/news/WordCloud';
import type { CloudLayoutItem } from '../../src/features/news/WordCloud';
import type {
  WordCloud,
  WordCloudRequest,
  WordCloudResponse,
  WordCloudWord,
} from '../../src/features/news/types';

const DATE = '2026-10-20';
const SNAPSHOT = '00000000-0000-4000-8000-000000000001';
const OTHER_SNAPSHOT = '00000000-0000-4000-8000-000000000002';
const ID_A = '11111111-1111-4111-8111-111111111111';
const ID_B = '22222222-2222-4222-8222-222222222222';
const ID_C = '33333333-3333-4333-8333-333333333333';

const request: WordCloudRequest = { date: DATE, snapshot: SNAPSHOT, group: 'all', topN: 40 };

function word(term: string, documentCount: number, ids: string[]): WordCloudWord {
  return {
    term,
    document_count: documentCount,
    article_ids: [...ids].sort(),
    sentiment: null,
  };
}

function oneWord(over: Record<string, unknown> = {}): Record<string, unknown> {
  return { term: '涨停', document_count: 1, article_ids: [ID_A], sentiment: null, ...over };
}

function cloud(over: Record<string, unknown> = {}): WordCloud {
  return {
    type: 'all',
    top_n: 40,
    total: 3,
    words: [
      word('涨停', 3, [ID_A, ID_B, ID_C]),
      word('龙虎榜', 2, [ID_A, ID_B]),
      word('alpha', 1, [ID_C]),
    ],
    text_scope: { titles: 3, summaries: 2, contents: 1 },
    tokenizer_version: WORDCLOUD_TOKENIZER_V1,
    ...over,
  } as WordCloud;
}

function payload(
  data: unknown,
  envelopeOver: Record<string, unknown> = {},
): WordCloudResponse {
  return {
    trade_date: DATE,
    as_of: '2026-10-20T08:00:00Z',
    snapshot_id: SNAPSHOT,
    source: 'news_materialization',
    source_version: 'news-v1',
    coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 },
    quality_flags: [],
    rule_version: 'news-v1',
    basis: { unit: null, currency: null, price_type: null, benchmark: null },
    ...envelopeOver,
    data: data as WordCloud | null,
  };
}

function validate(
  data: unknown,
  envelopeOver: Record<string, unknown> = {},
  currentRequest: WordCloudRequest = request,
) {
  return validateWordcloudResponse(payload(data, envelopeOver), currentRequest);
}

describe('wordcloudPath', () => {
  it('always emits date, snapshot_id, top_n and type with proper encoding', () => {
    const url = new URL(wordcloudPath(request), 'https://example.test');
    expect(url.pathname).toBe('/api/news/wordcloud');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      date: DATE,
      snapshot_id: SNAPSHOT,
      top_n: '40',
      type: 'all',
    });
  });

  it('keeps type present for every group and stringifies top_n', () => {
    const path = wordcloudPath({ ...request, group: 'research', topN: 1 });
    const search = new URLSearchParams(path.split('?')[1]);
    expect(search.get('type')).toBe('research');
    expect(search.get('top_n')).toBe('1');
    expect(path).toBe('/api/news/wordcloud?date=2026-10-20&snapshot_id=00000000-0000-4000-8000-000000000001&top_n=1&type=research');
  });
});

describe('validateWordcloudResponse', () => {
  it('accepts a valid block and a null block', () => {
    expect(validate(cloud())).toBeNull();
    expect(validate(null, { quality_flags: ['no_data'] })).toBeNull();
  });

  it('accepts equal-count terms in ascending term order and an echoed group', () => {
    const tied: WordCloud = {
      ...cloud(),
      type: 'news',
      total: 2,
      words: [word('a', 2, [ID_A, ID_B]), word('涨', 2, [ID_A, ID_C])],
    };
    expect(validate(tied, {}, { ...request, group: 'news' })).toBeNull();
  });

  it('accepts the sliced normal case where total exceeds the returned words', () => {
    const sliced: WordCloud = {
      ...cloud(),
      top_n: 2,
      total: 3,
      words: [word('涨停', 3, [ID_A, ID_B, ID_C]), word('龙虎榜', 2, [ID_A, ID_B])],
    };
    expect(validate(sliced, {}, { ...request, topN: 2 })).toBeNull();
  });

  it('orders equal-count terms by Unicode code point, including astral terms', () => {
    const astral: WordCloud = {
      ...cloud(),
      total: 2,
      words: [word('启', 2, [ID_A, ID_B]), word('😀', 2, [ID_A, ID_C])],
    };
    expect(validate(astral)).toBeNull();
  });

  it('maps snapshot or date drift to selection_mismatch', () => {
    expect(validate(cloud(), { snapshot_id: OTHER_SNAPSHOT })).toBe('selection_mismatch');
    expect(validate(cloud(), { trade_date: '2026-10-19' })).toBe('selection_mismatch');
  });

  it.each<[string, () => unknown, WordCloudRequest?]>([
    ['data 不是对象', () => 'block'],
    ['type 回显与请求分组不符', () => cloud({ type: 'news' })],
    ['type 是未知分组', () => cloud({ type: 'company' })],
    ['top_n 是布尔值', () => cloud({ top_n: true })],
    ['top_n 是浮点数', () => cloud({ top_n: 1.5 })],
    ['top_n 超出上界', () => cloud({ top_n: 101 }), { ...request, topN: 101 }],
    ['top_n 低于下界', () => cloud({ top_n: 0 }), { ...request, topN: 0 }],
    ['top_n 与请求不符', () => cloud({ top_n: 20 })],
    ['total 小于 words 长度', () => cloud({ total: 2 })],
    ['total 为负数', () => cloud({ total: -1 })],
    ['total 是布尔值', () => cloud({ total: true })],
    ['tokenizer 版本错误', () => cloud({ tokenizer_version: 'wordcloud-tokenizer-v2' })],
    ['words 不是数组', () => cloud({ words: {} })],
    ['word 含未知键', () => cloud({ words: [oneWord({ extra: 1 })] })],
    ['word 缺少键', () => cloud({ words: [{ term: 'x', document_count: 1, article_ids: [ID_A] }] })],
    ['term 为空串', () => cloud({ words: [oneWord({ term: '' })] })],
    ['term 非字符串', () => cloud({ words: [oneWord({ term: 7 })] })],
    ['document_count 为 0', () => cloud({ words: [oneWord({ document_count: 0, article_ids: [] })] })],
    ['document_count 为布尔', () => cloud({ words: [oneWord({ document_count: true })] })],
    ['document_count 为浮点', () => cloud({ words: [oneWord({ document_count: 1.5 })] })],
    ['document_count 与 ids 长度不符', () => cloud({ words: [oneWord({ document_count: 2 })] })],
    ['article_id 含大写', () => cloud({ words: [oneWord({ article_ids: ['11111111-1111-4111-8111-11111111111A'] })] })],
    ['article_id 不是 UUID', () => cloud({ words: [oneWord({ article_ids: ['not-a-uuid'] })] })],
    ['article_id 重复', () => cloud({ words: [oneWord({ document_count: 2, article_ids: [ID_A, ID_A] })] })],
    ['article_id 逆序', () => cloud({ words: [oneWord({ document_count: 2, article_ids: [ID_B, ID_A] })] })],
    ['article_ids 不是数组', () => cloud({ words: [oneWord({ article_ids: ID_A })] })],
    ['sentiment 为 false', () => cloud({ words: [oneWord({ sentiment: false })] })],
    ['sentiment 为空串', () => cloud({ words: [oneWord({ sentiment: '' })] })],
    ['sentiment 为 positive', () => cloud({ words: [oneWord({ sentiment: 'positive' })] })],
    ['跨词 term 重复', () => cloud({
      total: 2,
      words: [word('涨停', 3, [ID_A, ID_B, ID_C]), word('涨停', 1, [ID_A])],
    })],
    ['同 term 异 count 重复', () => cloud({
      total: 2,
      words: [word('涨停', 2, [ID_A, ID_B]), word('涨停', 1, [ID_C])],
    })],
    ['词序按 count 升序', () => cloud({
      total: 2,
      words: [word('a', 1, [ID_A]), word('b', 2, [ID_A, ID_B])],
    })],
    ['同 count 词 term 逆序', () => cloud({
      total: 2,
      words: [word('b', 2, [ID_A, ID_B]), word('a', 2, [ID_A, ID_C])],
    })],
    ['text_scope 缺键', () => cloud({ text_scope: { titles: 1, summaries: 1 } })],
    ['text_scope 多键', () => cloud({ text_scope: { titles: 1, summaries: 1, contents: 0, extra: 1 } })],
    ['text_scope 负值', () => cloud({ text_scope: { titles: -1, summaries: 0, contents: 0 } })],
    ['text_scope 布尔值', () => cloud({ text_scope: { titles: true, summaries: 0, contents: 0 } })],
    ['data 含未知顶层键', () => cloud({ unexpected: true })],
    ['data 缺少顶层键', () => {
      const value = cloud() as unknown as Record<string, unknown>;
      delete value.tokenizer_version;
      return value;
    }],
    ['words 超过 top_n 上界', () => cloud({
      total: 41,
      words: Array.from({ length: 41 }, (_, index) =>
        oneWord({ term: `t${String(index).padStart(3, '0')}` })),
    })],
    ['返回词数少于切片不变量（疑似截断）', () => cloud({ total: 100 })],
    ['total 是浮点数', () => cloud({ total: 1.5 })],
    ['article_ids 含非字符串元素', () => cloud({ words: [oneWord({ article_ids: [1] })] })],
    ['words 含 null 元素', () => cloud({ words: [null] })],
    ['同 count astral 词逆序', () => cloud({
      total: 2,
      words: [word('😀', 2, [ID_A, ID_B]), word('启', 2, [ID_A, ID_C])],
    })],
  ])('%s 时返回 malformed_response', (_name, factory, currentRequest = request) => {
    expect(validate(factory(), {}, currentRequest)).toBe('malformed_response');
  });
});

describe('cloudLayout', () => {
  it('returns an empty layout for empty words', () => {
    expect(cloudLayout([])).toEqual([]);
  });

  it('is deterministic and returns the full item key set', () => {
    const words = [word('alpha', 1, [ID_A]), word('beta', 3, [ID_A, ID_B, ID_C]), word('涨停', 2, [ID_A, ID_B])];
    const first = cloudLayout(words);
    const second = cloudLayout(words);
    expect(second).toEqual(first);
    expect(first.map(item => Object.keys(item).sort())).toEqual(
      words.map(() => ['count', 'fontSize', 'term', 'x', 'y']),
    );
    expect(first[0]).toMatchObject({ term: 'alpha', count: 1, x: 0, y: 0 });
  });

  it('maps the highest count to maxFont and the lowest to minFont', () => {
    const items = cloudLayout([
      word('alpha', 1, [ID_A]),
      word('beta', 3, [ID_A, ID_B, ID_C]),
      word('涨停', 2, [ID_A, ID_B]),
    ]);
    const byTerm = Object.fromEntries(items.map(item => [item.term, item]));
    expect(byTerm.beta.fontSize).toBe(30);
    expect(byTerm.alpha.fontSize).toBe(13);
    expect(byTerm['涨停'].fontSize).toBeGreaterThan(13);
    expect(byTerm['涨停'].fontSize).toBeLessThan(30);
    for (const item of items) {
      expect(item.fontSize).toBeGreaterThanOrEqual(13);
      expect(item.fontSize).toBeLessThanOrEqual(30);
    }
  });

  it('wraps words that exceed the line width onto a second line', () => {
    const words = Array.from({ length: 40 }, (_, index) =>
      ({ term: `term${index}`, document_count: index + 1, article_ids: [], sentiment: null }) as WordCloudWord);
    const items = cloudLayout(words);
    const rows = new Set(items.map(item => item.y));
    expect(rows.size).toBeGreaterThanOrEqual(2);
    expect(items[items.length - 1].y).toBeGreaterThan(0);
    for (const item of items) {
      expect(item.x).toBeGreaterThanOrEqual(0);
      expect(item.fontSize).toBeGreaterThanOrEqual(13);
      expect(item.fontSize).toBeLessThanOrEqual(30);
    }
  });

  it('uses minFont for every word when all document counts tie', () => {
    const words = [
      word('a', 2, [ID_A, ID_B]),
      word('b', 2, [ID_A, ID_C]),
      word('c', 2, [ID_B, ID_C]),
    ];
    for (const item of cloudLayout(words)) {
      expect(item.fontSize).toBe(13);
      expect(item.y).toBe(0);
    }
  });

  it('restarts x at 0 on each wrapped line and keeps x strictly increasing within a line', () => {
    const words = Array.from({ length: 40 }, (_, index) =>
      ({ term: `term${index}`, document_count: index + 1, article_ids: [], sentiment: null }) as WordCloudWord);
    const items = cloudLayout(words);
    const rows = new Map<number, CloudLayoutItem[]>();
    for (const item of items) {
      rows.set(item.y, [...(rows.get(item.y) ?? []), item]);
    }
    expect(rows.size).toBeGreaterThanOrEqual(2);
    for (const row of rows.values()) {
      expect(row[0].x).toBe(0);
      for (let index = 1; index < row.length; index += 1) {
        expect(row[index].x).toBeGreaterThan(row[index - 1].x);
      }
    }
  });
});

describe('WordCloudView', () => {
  function render(data: WordCloud | null, selectedTerm = '') {
    return renderToStaticMarkup(createElement(WordCloudView, {
      data,
      selectedTerm,
      onSelectTerm: () => {},
    }));
  }

  it('renders the no-publication empty state for null data', () => {
    const markup = render(null);
    expect(markup).toContain('当前快照暂无可发布的词云数据。');
    expect(markup).not.toContain('新闻词云词语');
  });

  it('renders the no-licensed-text empty state for zero words', () => {
    const markup = render(cloud({ total: 0, words: [] }));
    expect(markup).toContain('当前范围没有获许可文本可生成词语。');
    expect(markup).not.toContain('<svg');
  });

  it('renders the svg words and the accessible button ledger with provenance', () => {
    const markup = render(cloud());

    expect(markup).toContain('新闻词云');
    // The SVG is decorative: the button ledger below is the accessible UI.
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).not.toContain('role="img"');
    expect(markup).not.toContain('新闻词云图示');
    expect(markup).toContain('<text');
    expect(markup).toContain('>涨停</text>');
    expect(markup).toContain('aria-label="新闻词云词语"');
    expect(markup).toContain('data-term="涨停"');
    expect(markup).toContain('aria-pressed="false"');
    // The accessible name is explicit so it never depends on flex-generated
    // text boundaries between the term and the count span.
    expect(markup).toContain('aria-label="涨停，3 篇"');
    expect(markup).toContain('aria-label="龙虎榜，2 篇"');
    expect(markup).toContain('3 篇');
    expect(markup).toContain('1 篇');
    expect(markup).toContain('标题 3');
    expect(markup).toContain('摘要 2');
    expect(markup).toContain('正文 1');
    expect(markup).toContain(WORDCLOUD_TOKENIZER_V1);
    expect(markup).not.toContain('Invalid Date');
    expect(markup).not.toContain('情感');
    expect(markup).not.toContain('正面');
    expect(markup).not.toContain('负面');
  });

  it('marks only the selected term button as pressed', () => {
    const markup = render(cloud(), '涨停');
    expect(markup).toContain('aria-pressed="true"');
    const pressedBlocks = markup.split('<li>').filter(block => block.includes('aria-pressed="true"'));
    expect(pressedBlocks).toHaveLength(1);
    expect(pressedBlocks[0]).toContain('data-term="涨停"');
  });
});
