import { describe, expect, it } from 'vitest';
import { analysisRange, analysisSort, klineRange } from '../../src/features/analysis/query';

const sessions = Array.from({ length: 130 }, (_, index) =>
  new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10));

describe('reset analysis query projection', () => {
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
});
