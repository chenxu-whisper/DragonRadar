import { describe, expect, it } from 'vitest';
import {
  analysisRange,
  analysisSort,
  analysisSortDescriptor,
  klineRange,
} from '../../src/features/analysis/query';
import {
  validateCurvesResponse,
  validateKlineResponse,
  validateRankResponse,
} from '../../src/features/analysis/validation';
import { rankingForRequest } from '../fixtures/ranking-direction';
import type { ResetRankItem } from '../../src/types';

const sessions = Array.from({ length: 130 }, (_, index) =>
  new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10));
const query = new URLSearchParams(
  'start=2026-08-13&end=2026-09-10&symbols=TEST001&benchmark=B000300&adj=raw',
);

function envelope(data: unknown, coverage = { expected: 1, valid: 1, excluded: 0, missing: 0 }) {
  return {
    trade_date: '2026-09-10',
    as_of: '2026-09-10T10:00:00Z',
    snapshot_id: 'snapshot-a',
    source: 'analysis',
    source_version: 'v1',
    coverage,
    quality_flags: [],
    rule_version: 'analysis-v1',
    basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: 'B000300' },
    data,
  };
}

function rankData() {
  return {
    window_days: 20,
    adjusted_start: '2026-08-13',
    adjusted_end: '2026-09-10',
    benchmark_return: 0.02,
    ranking: [{
      rank: 1, security_id: 'TEST001', name: '合成甲',
      topics: [{ topic_id: 'SEC001', name: '机器人' }],
      topic_status: 'available', topic_reasons: [], reasons: [],
      return_ratio: 0.1, excess_return_ratio: 0.08,
      max_drawdown_ratio: 0.25, amount_change_ratio: 0.2,
    }],
    exclusions: [{ security_id: 'TEST002', reasons: ['missing_price'] }],
  };
}

function curvesData() {
  return {
    adjusted_start: '2026-08-13',
    adjusted_end: '2026-09-10',
    benchmark: 'B000300',
    curves: [{
      security_id: 'TEST001',
      reasons: [],
      points: [
        { trade_date: '2026-08-13', normalized: 100, drawdown: 0 },
        { trade_date: '2026-09-10', normalized: 90, drawdown: 0.1 },
      ],
    }],
    exclusions: [],
  };
}

function klineData(chartType: 'candlestick' | 'line' = 'candlestick') {
  const points: {
    trade_date: string;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    volume: number | null;
    amount: number | null;
  }[] = [{
    trade_date: '2026-08-13',
    open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100,
  }];
  return {
    security_id: 'TEST001',
    adjustment: 'raw',
    adjusted_start: '2026-08-13',
    adjusted_end: '2026-09-10',
    chart_type: chartType,
    points,
  };
}

describe('reset analysis query projection', () => {
  it.each([
    ['interval_return', 'return'], ['excess_return', 'excess_return'],
    ['maximum_drawdown', 'drawdown'], ['amount_change', 'amount_change'],
  ] as const)('uses explicit direction for %s without mutating its default', (sort, requestField) => {
    const before = analysisSortDescriptor(sort);
    for (const direction of ['asc', 'desc'] as const) {
      expect(analysisSortDescriptor(sort, direction)).toEqual({ requestField, direction });
    }
    expect(analysisSortDescriptor(sort)).toEqual(before);
  });
  it('uses exactly N+1 calendar positions ending at the snapshot', () => {
    expect(analysisRange({ n: 20, start: '', end: '' }, sessions)).toEqual({
      start: sessions[109],
      end: sessions[129],
    });
  });

  it('keeps explicit dates for server-side trading-day adjustment', () => {
    expect(analysisRange({ n: 20, start: '2026-01-03', end: '2026-04-30' }, sessions)).toEqual({
      start: '2026-01-03',
      end: '2026-04-30',
    });
  });

  it('rejects a fixed window when the snapshot calendar is too short', () => {
    expect(() => analysisRange({ n: 20, start: '', end: '' }, sessions.slice(-20)))
      .toThrow('至少 21 个交易日');
  });

  it('limits default raw K-line history to the latest 120 sessions', () => {
    expect(klineRange({ start: '', end: '' }, sessions)).toEqual({
      start: sessions[10],
      end: sessions[129],
    });
  });

  it.each([
    ['interval_return', 'return'],
    ['excess_return', 'excess_return'],
    ['maximum_drawdown', 'drawdown'],
    ['amount', 'amount_change'],
    ['security_id', 'return'],
  ] as const)('maps %s to backend sort %s', (sort, expected) => {
    expect(analysisSort(sort)).toBe(expected);
  });

  it.each([
    ['interval_return', 'return', 'desc'],
    ['excess_return', 'excess_return', 'desc'],
    ['maximum_drawdown', 'drawdown', 'asc'],
    ['amount_change', 'amount_change', 'desc'],
  ] as const)(
    'uses one descriptor for %s request and CSV sort metadata',
    (sort, requestField, direction) => {
      expect(analysisSortDescriptor(sort)).toEqual({
        requestField,
        direction,
      });
    },
  );
});

describe('reset analysis response validation', () => {
  const directionRows: ResetRankItem[] = [
    ['D', 0.1, 0.2], ['A', -0.1, 0], ['C', 0.1, 0.2], ['B', 0, 0.1],
  ].map(([security_id, value, drawdown]) => ({
    ...rankData().ranking[0], security_id: security_id as string,
    return_ratio: value as number, excess_return_ratio: (value as number) - 0.02,
    max_drawdown_ratio: drawdown as number, amount_change_ratio: value as number,
  }));

  for (const sort of ['return', 'excess_return', 'drawdown', 'amount_change'] as const) {
    it.each(['asc', 'desc'] as const)(`accepts Unicode codepoint identity ties for ${sort} %s`, order => {
      const params = new URLSearchParams(query);
      params.set('sort', sort); params.set('order', order);
      const data = rankData();
      // Fixed Python str ordering: U+E000 precedes U+10000, unlike UTF-16 ordering.
      data.ranking = [
        { ...data.ranking[0], security_id: '\uE000', rank: 1 },
        { ...data.ranking[0], security_id: '\u{10000}', rank: 1 },
      ];
      expect(validateRankResponse(
        envelope(data, { expected: 3, valid: 2, excluded: 0, missing: 1 }), params,
      )).toBeNull();
    });

    it.each(['asc', 'desc'] as const)(`rejects reversed Unicode identity ties for ${sort} %s`, order => {
      const params = new URLSearchParams(query);
      params.set('sort', sort); params.set('order', order);
      const data = rankData();
      data.ranking = [
        { ...data.ranking[0], security_id: '\u{10000}', rank: 1 },
        { ...data.ranking[0], security_id: '\uE000', rank: 1 },
      ];
      expect(validateRankResponse(
        envelope(data, { expected: 3, valid: 2, excluded: 0, missing: 1 }), params,
      )).toBe('malformed');
    });

    it.each(['asc', 'desc'] as const)(`validates ${sort} %s and rejects the opposite response`, order => {
      const params = new URLSearchParams(query);
      params.set('sort', sort); params.set('order', order);
      const data = { ...rankData(), ranking: rankingForRequest(directionRows, params), exclusions: [] };
      expect(data.ranking.map(row => row.security_id)).toEqual(order === 'asc' ? ['A', 'B', 'C', 'D'] : ['C', 'D', 'B', 'A']);
      expect(data.ranking.map(row => row.rank)).toEqual(order === 'asc' ? [1, 2, 3, 3] : [1, 1, 3, 4]);
      const body = envelope(data, { expected: 4, valid: 4, excluded: 0, missing: 0 });
      expect(validateRankResponse(body, params)).toBeNull();
      params.set('order', order === 'asc' ? 'desc' : 'asc');
      expect(validateRankResponse(body, params)).toBe('malformed');
    });
  }

  for (const shape of ['null-last', 'all-null'] as const) {
    it.each(['asc', 'desc'] as const)(`accepts Unicode identity ${shape} ties for %s`, order => {
      const params = new URLSearchParams(query);
      params.set('sort', 'amount_change'); params.set('order', order);
      const base = rankData().ranking[0];
      const ranking: ResetRankItem[] = [
        ...(shape === 'null-last' ? [{ ...base, security_id: 'A' }] : []),
        { ...base, security_id: '\uE000', rank: null, amount_change_ratio: null },
        { ...base, security_id: '\u{10000}', rank: null, amount_change_ratio: null },
      ];
      const data = { ...rankData(), ranking, exclusions: [] };
      expect(validateRankResponse(envelope(data, {
        expected: ranking.length, valid: ranking.length - 2, excluded: 0, missing: 2,
      }), params)).toBeNull();
    });

    it.each(['asc', 'desc'] as const)(`rejects reversed Unicode identity ${shape} ties for %s`, order => {
      const params = new URLSearchParams(query);
      params.set('sort', 'amount_change'); params.set('order', order);
      const base = rankData().ranking[0];
      const ranking: ResetRankItem[] = [
        ...(shape === 'null-last' ? [{ ...base, security_id: 'A' }] : []),
        { ...base, security_id: '\u{10000}', rank: null, amount_change_ratio: null },
        { ...base, security_id: '\uE000', rank: null, amount_change_ratio: null },
      ];
      const data = { ...rankData(), ranking, exclusions: [] };
      expect(validateRankResponse(envelope(data, {
        expected: ranking.length, valid: ranking.length - 2, excluded: 0, missing: 2,
      }), params)).toBe('malformed');
    });
  }

  it.each(['asc', 'desc'] as const)('projects fixture Unicode ties to fixed codepoint goldens for %s', order => {
    const params = new URLSearchParams(query);
    params.set('sort', 'amount_change'); params.set('order', order);
    const base = rankData().ranking[0];
    const ids = ['\u{10000}A', '\uE000A', '\u{10000}', '\uE000', 'A'];
    const rows = ids.map(security_id => ({ ...base, security_id }));
    const expectedIds = ['A', '\uE000', '\uE000A', '\u{10000}', '\u{10000}A'];
    expect(rankingForRequest(rows, params).map(row => [row.security_id, row.rank]))
      .toEqual(expectedIds.map(id => [id, 1]));
    expect(rankingForRequest(rows.map(row => ({ ...row, amount_change_ratio: null })), params)
      .map(row => [row.security_id, row.rank]))
      .toEqual(expectedIds.map(id => [id, null]));
  });

  it.each(['asc', 'desc'] as const)('validates null-last and all-null ranking for %s', order => {
    const params = new URLSearchParams(query); params.set('sort', 'amount_change'); params.set('order', order);
    const rows = [...directionRows, { ...directionRows[0], security_id: 'E', amount_change_ratio: null }];
    const data = { ...rankData(), ranking: rankingForRequest(rows, params), exclusions: [] };
    expect(data.ranking.at(-1)).toMatchObject({ security_id: 'E', rank: null });
    expect(validateRankResponse(envelope(data, { expected: 5, valid: 4, excluded: 0, missing: 1 }), params)).toBeNull();
    data.ranking = rankingForRequest(rows.map(row => ({ ...row, amount_change_ratio: null })), params);
    expect(data.ranking.map(row => row.security_id)).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(validateRankResponse(envelope(data, { expected: 5, valid: 0, excluded: 0, missing: 5 }), params)).toBeNull();
    data.ranking.reverse();
    expect(validateRankResponse(envelope(data, { expected: 5, valid: 0, excluded: 0, missing: 5 }), params)).toBe('malformed');
  });

  it.each(['asc', 'desc'] as const)('rejects unstable tie identity ordering for %s', order => {
    const params = new URLSearchParams(query); params.set('order', order);
    const data = { ...rankData(), ranking: rankingForRequest(directionRows, params), exclusions: [] };
    const c = data.ranking.findIndex(row => row.security_id === 'C');
    [data.ranking[c], data.ranking[c + 1]] = [data.ranking[c + 1], data.ranking[c]];
    expect(validateRankResponse(envelope(data, { expected: 4, valid: 4, excluded: 0, missing: 0 }), params)).toBe('malformed');
  });

  it.each(['order=', 'order=ASC', 'order=invalid', 'order=asc&order=desc'])('rejects invalid requested direction %s', suffix => {
    const params = new URLSearchParams(query);
    new URLSearchParams(suffix).forEach((value, key) => params.append(key, value));
    expect(validateRankResponse(envelope(rankData(), { expected: 2, valid: 1, excluded: 0, missing: 1 }), params)).toBe('malformed');
  });
  it('rejects curves whose common axis ends before adjusted_end', () => {
    const data = curvesData();
    data.curves[0].points[1].trade_date = '2026-09-09';
    expect(validateCurvesResponse(envelope(data), query)).toBe('date_mismatch');
  });

  it('rejects curves whose common axis starts after adjusted_start despite a valid baseline', () => {
    const data = curvesData();
    data.curves[0].points[0].trade_date = '2026-08-14';
    expect(data.curves[0].points[0]).toMatchObject({ normalized: 100, drawdown: 0 });
    expect(validateCurvesResponse(envelope(data), query)).toBe('date_mismatch');
  });

  it('allows paired null gaps inside a complete curve axis', () => {
    const data = curvesData();
    data.curves[0].points.splice(1, 0, {
      trade_date: '2026-08-14',
      normalized: null as unknown as number,
      drawdown: null as unknown as number,
    });
    expect(validateCurvesResponse(envelope(data), query)).toBeNull();
  });

  it.each([
    ['unpaired nulls', 1, { normalized: null }],
    ['wrong normalized baseline', 0, { normalized: 99 }],
    ['wrong drawdown baseline', 0, { drawdown: 0.01 }],
    ['null baseline followed by data', 0, { normalized: null, drawdown: null }],
    ['nonpositive normalized value', 1, { normalized: 0 }],
    ['negative drawdown', 1, { drawdown: -0.01 }],
    ['complete drawdown', 1, { drawdown: 1 }],
    ['nonfinite normalized value', 1, { normalized: Number.POSITIVE_INFINITY }],
  ])('rejects curve %s', (_name, pointIndex, patch) => {
    const data = curvesData();
    Object.assign(data.curves[0].points[pointIndex], patch);
    expect(validateCurvesResponse(envelope(data), query)).toBe('malformed');
  });

  it.each([
    ['duplicate ranking ID', (data: ReturnType<typeof rankData>) => {
      data.ranking.push({ ...data.ranking[0] });
    }],
    ['duplicate exclusion ID', (data: ReturnType<typeof rankData>) => {
      data.exclusions.push({ ...data.exclusions[0] });
    }],
    ['ranking/exclusion overlap', (data: ReturnType<typeof rankData>) => {
      data.exclusions[0].security_id = 'TEST001';
    }],
    ['empty exclusion reason', (data: ReturnType<typeof rankData>) => {
      data.exclusions[0].reasons = [''];
    }],
    ['null rank', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].rank = null as unknown as number;
    }],
    ['unsafe rank', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].rank = Number.MAX_SAFE_INTEGER + 1;
    }],
    ['total loss return', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].return_ratio = -1;
    }],
    ['complete drawdown', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].max_drawdown_ratio = 1;
    }],
    ['impossible amount change', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].amount_change_ratio = -1.01;
    }],
  ])('rejects rank %s', (_name, corrupt) => {
    const data = rankData();
    corrupt(data);
    expect(validateRankResponse(
      envelope(data, { expected: 2, valid: 1, excluded: 0, missing: 1 }),
      query,
    )).toBe('malformed');
  });

  it.each([
    { expected: 3, valid: 1, excluded: 0, missing: 2 },
    { expected: 2, valid: 2, excluded: 0, missing: 0 },
    { expected: 2, valid: 1, excluded: 1, missing: 1 },
  ])('rejects rank counts inconsistent with coverage %j', coverage => {
    expect(validateRankResponse(envelope(rankData(), coverage), query)).toBe('malformed');
  });

  it('allows tied positive safe ranks', () => {
    const data = rankData();
    data.ranking.push({ ...data.ranking[0], security_id: 'TEST003' });
    expect(validateRankResponse(
      envelope(data, { expected: 3, valid: 2, excluded: 0, missing: 1 }),
      query,
    )).toBeNull();
  });

  it('allows a null rank only when the requested sort value is unavailable', () => {
    const data = rankData();
    data.ranking.push({
      ...data.ranking[0],
      rank: null as unknown as number,
      security_id: 'TEST003',
      amount_change_ratio: null as unknown as number,
    });
    const amountQuery = new URLSearchParams(query);
    amountQuery.set('sort', 'amount_change');
    expect(validateRankResponse(
      envelope(data, { expected: 3, valid: 1, excluded: 0, missing: 2 }),
      amountQuery,
    )).toBeNull();
  });

  it('rejects a null sort value before a ranked value', () => {
    const data = rankData();
    data.ranking.unshift({
      ...data.ranking[0],
      rank: null as unknown as number,
      security_id: 'TEST003',
      amount_change_ratio: null as unknown as number,
    });
    data.ranking[1].rank = 2;
    const amountQuery = new URLSearchParams(query);
    amountQuery.set('sort', 'amount_change');
    expect(validateRankResponse(
      envelope(data, { expected: 3, valid: 1, excluded: 0, missing: 2 }),
      amountQuery,
    )).toBe('malformed');
  });

  it.each([
    ['wrong rank order', (data: ReturnType<typeof rankData>) => {
      data.ranking.push({
        ...data.ranking[0], rank: 1, security_id: 'TEST003', return_ratio: 0.2,
      });
    }],
    ['wrong excess return', (data: ReturnType<typeof rankData>) => {
      data.ranking[0].excess_return_ratio = 999;
    }],
  ])('rejects rank semantic mismatch: %s', (_name, corrupt) => {
    const data = rankData();
    corrupt(data);
    expect(validateRankResponse(
      envelope(data, {
        expected: data.ranking.length + data.exclusions.length,
        valid: data.ranking.length,
        excluded: 0,
        missing: data.exclusions.length,
      }),
      query,
    )).toBe('malformed');
  });

  it('allows a complete loss of current amount against a positive baseline', () => {
    const data = rankData();
    data.ranking[0].amount_change_ratio = -1;
    expect(validateRankResponse(
      envelope(data, { expected: 2, valid: 1, excluded: 0, missing: 1 }),
      query,
    )).toBeNull();
  });

  it.each([
    ['partial OHLC', 'line', { open: 10, high: null, low: null, close: 11 }],
    ['close-only candle', 'candlestick', { open: null, high: null, low: null, close: 11 }],
    ['nonpositive close', 'line', { open: null, high: null, low: null, close: 0 }],
    ['low above open', 'line', { open: 10, high: 12, low: 10.5, close: 11 }],
    ['high below close', 'line', { open: 10, high: 10.5, low: 9, close: 11 }],
    ['negative volume', 'line', { volume: -1 }],
    ['nonfinite amount', 'line', { amount: Number.NaN }],
  ] as const)('rejects kline %s', (_name, chartType, patch) => {
    const data = klineData(chartType);
    Object.assign(data.points[0], patch);
    expect(validateKlineResponse(envelope(data), 'TEST001', query)).toBe('malformed');
  });

  it('allows line responses to mix complete OHLC, close-only points and gaps', () => {
    const data = klineData('line');
    data.points.push(
      {
        trade_date: '2026-08-14',
        open: null, high: null, low: null, close: 11, volume: null, amount: 0,
      },
      {
        trade_date: '2026-08-15',
        open: null, high: null, low: null, close: null, volume: null, amount: null,
      },
    );
    expect(validateKlineResponse(envelope(data), 'TEST001', query)).toBeNull();
  });
});
