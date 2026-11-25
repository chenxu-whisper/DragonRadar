import type { ResetRankItem } from '../../src/types';

const metrics = {
  return: { field: 'return_ratio', order: 'desc' },
  excess_return: { field: 'excess_return_ratio', order: 'desc' },
  drawdown: { field: 'max_drawdown_ratio', order: 'asc' },
  amount_change: { field: 'amount_change_ratio', order: 'desc' },
} as const;

// Independent test projection; golden assertions must not derive from this helper.
export function rankingForRequest<T extends ResetRankItem>(rows: readonly T[], query: URLSearchParams): T[] {
  const metric = metrics[(query.get('sort') || 'return') as keyof typeof metrics];
  if (!metric) throw new Error('Invalid fixture metric');
  const order = query.has('order') ? query.get('order') : metric.order;
  if (order !== 'asc' && order !== 'desc') throw new Error('Invalid fixture direction');
  const result = rows.map(row => ({ ...row }));
  result.sort((left, right) => {
    const a = left[metric.field], b = right[metric.field];
    if (a === b) {
      const leftPoints = Array.from(left.security_id, character => character.codePointAt(0) as number);
      const rightPoints = Array.from(right.security_id, character => character.codePointAt(0) as number);
      for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
        if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
      }
      return leftPoints.length - rightPoints.length;
    }
    if (a === null) return 1;
    if (b === null) return -1;
    return order === 'desc' ? b - a : a - b;
  });
  let previous: number | null = null, rank = 0;
  result.forEach((row, index) => {
    const value = row[metric.field];
    if (value !== null && value !== previous) rank = index + 1;
    row.rank = value === null ? null : rank;
    previous = value;
  });
  return result;
}
