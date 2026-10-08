import type { Sort } from '../../state';

interface WindowState {
  n?: number;
  start: string;
  end: string;
}

export function analysisRange(window: WindowState, sessions: string[]): { start: string; end: string } {
  if (window.start || window.end) {
    if (!window.start || !window.end || window.start > window.end) {
      throw new Error('请输入完整且顺序正确的起止日期。');
    }
    return { start: window.start, end: window.end };
  }
  const n = window.n ?? 20;
  if (!Number.isInteger(n) || n < 1 || n > 250) {
    throw new Error('交易日间隔须为 1 至 250 的整数。');
  }
  if (sessions.length < n + 1) {
    throw new Error(`当前快照至少 ${n + 1} 个交易日才可计算，不能自动缩短区间。`);
  }
  return { start: sessions.at(-(n + 1))!, end: sessions.at(-1)! };
}

export function klineRange(window: Pick<WindowState, 'start' | 'end'>, sessions: string[]): { start: string; end: string } {
  if (window.start || window.end) return analysisRange({ ...window, n: 20 }, sessions);
  if (!sessions.length) throw new Error('当前快照没有可用交易日历。');
  const selected = sessions.slice(-120);
  return { start: selected[0], end: selected.at(-1)! };
}

export interface AnalysisSortDescriptor {
  requestField: 'return' | 'excess_return' | 'drawdown' | 'amount_change';
  direction: 'asc' | 'desc';
}

const analysisSorts: Partial<Record<Sort, AnalysisSortDescriptor>> = {
  interval_return: { requestField: 'return', direction: 'desc' },
  excess_return: { requestField: 'excess_return', direction: 'desc' },
  maximum_drawdown: { requestField: 'drawdown', direction: 'asc' },
  amount: { requestField: 'amount_change', direction: 'desc' },
  amount_change: { requestField: 'amount_change', direction: 'desc' },
};
const defaultAnalysisSort: AnalysisSortDescriptor = {
  requestField: 'return',
  direction: 'desc',
};

export function analysisSortDescriptor(sort: Sort): AnalysisSortDescriptor {
  return analysisSorts[sort] ?? defaultAnalysisSort;
}

export function analysisSort(sort: Sort): 'return' | 'excess_return' | 'drawdown' | 'amount_change' {
  return analysisSortDescriptor(sort).requestField;
}
