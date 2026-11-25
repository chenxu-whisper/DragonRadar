import { useMemo, useState } from 'react';
import { TableScroll } from '../../components/Common';
import type { ResetKlinePoint, WorkbenchEnvelope } from '../../types';

const fields = [
  ['trade_date', '交易日期'], ['open', '开盘'], ['high', '最高'], ['low', '最低'],
  ['close', '收盘'], ['volume', '成交量'], ['amount', '成交额'],
] as const;
type Field = typeof fields[number][0];
const number = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value);

export type RawPoint = ResetKlinePoint & Partial<Record<'currency' | 'price_unit' | 'volume_unit' | 'amount_unit', string | null>>;
const unitText = (value: string | null | undefined) => value?.trim() || '单位未知';

export default function StockHistoryTable({ points, basis }: {
  points: RawPoint[]; basis?: WorkbenchEnvelope<unknown>['basis'];
}) {
  const [page, setPage] = useState(1), [size, setSize] = useState(50);
  const [sort, setSort] = useState<Field>('trade_date'), [order, setOrder] = useState<'asc' | 'desc'>('asc');
  const sorted = useMemo(() => [...points].sort((a, b) => {
    const av = a[sort], bv = b[sort];
    if (av === null || bv === null) return av === bv ? a.trade_date.localeCompare(b.trade_date) : av === null ? 1 : -1;
    const compared = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return (order === 'asc' ? compared : -compared) || a.trade_date.localeCompare(b.trade_date);
  }), [points, sort, order]);
  const pages = Math.max(1, Math.ceil(sorted.length / size)), current = Math.min(page, pages);
  const changeSort = (field: Field) => {
    setSort(field); setOrder(sort === field && order === 'asc' ? 'desc' : 'asc'); setPage(1);
  };
  return <>
    <div className="pager">
      <span role="status">共 {points.length} 条 · 第 {current} / {pages} 页</span>
      <label>行情每页条数<select value={size} onChange={event => { setSize(Number(event.target.value)); setPage(1); }}>
        {[20, 50, 100].map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <button aria-label="行情上一页" disabled={current === 1} onClick={() => setPage(current - 1)}>上一页</button>
      <button aria-label="行情下一页" disabled={current === pages} onClick={() => setPage(current + 1)}>下一页</button>
    </div>
    <TableScroll label="完整行情数据">
      <table aria-label="完整行情数据" className="responsive-record-table stock-history-table">
        <thead><tr>{fields.map(([field, label]) =>
          <th key={field} aria-sort={sort === field ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>
            <button aria-label={`${label}排序`} onClick={() => changeSort(field)}>{label}{sort === field ? order === 'asc' ? ' ↑' : ' ↓' : ''}</button>
          </th>,
        )}<th>币种 / 单位</th></tr></thead>
        <tbody>{sorted.slice((current - 1) * size, current * size).map(point =>
          <tr key={point.trade_date}>{fields.map(([field, label]) => {
            const value = point[field];
            return field === 'trade_date'
              ? <th key={field} data-label={label}>{value}</th>
              : <td key={field} data-label={label}>{value === null ? '—' : number(value as number)}</td>;
          })}<td data-label="币种 / 单位">
            币种 {unitText(point.currency === undefined ? basis?.currency : point.currency)}
            {' · '}价格 {unitText(point.price_unit === undefined ? basis?.unit : point.price_unit)}
            {' · '}成交量 {unitText(point.volume_unit)}
            {' · '}成交额 {unitText(point.amount_unit)}
          </td></tr>,
        )}</tbody>
      </table>
    </TableScroll>
  </>;
}
