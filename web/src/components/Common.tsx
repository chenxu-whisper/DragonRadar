import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, RefreshCw, Search } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { formatPercent, label, reasons, signClass, timestamp } from '../format';
import type { Resource } from '../hooks/useResource';
import type { Exclusion, Snapshot } from '../types';
import type { ResearchState, Sort } from '../state';

export type Patch = (change: Partial<ResearchState>, replace?: boolean) => void;
export function ResourceStatus<T>({ resource, name }: { resource: Resource<T>; name: string }) {
  return <>
    {resource.loading && <p role="status" className="status">正在加载{name}…</p>}
    {resource.loading && resource.data === undefined && <div className="resource-skeleton" aria-hidden="true">
      <span /><span /><span />
    </div>}
    {resource.error && <div role="alert" className="notice error">
      <span>{resource.error.message}{resource.data ? ' 当前保留上次成功数据与原始时间，尚未更新。' : ''}</span>
      <button type="button" onClick={resource.refresh} title={`重新请求${name}`}><RefreshCw size={16} />重试{name}</button>
    </div>}
  </>;
}
export function Percent({ value, signed = true }: { value: string | null; signed?: boolean }) {
  return <span className={signed ? signClass(value) : 'neutral'}>{formatPercent(value, signed)}</span>;
}
export function TableScroll({ children, label: name }: { children: ReactNode; label: string }) {
  return <div className="table-scroll" role="region" aria-label={name} tabIndex={0}>{children}</div>;
}
export function Pager({ page, size, total, change }: { page: number; size: number; total: number; change: (page: number, size: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return <div className="pager">
    <span role="status">共 {total} 条 · 第 {page} / {pages} 页</span>
    <label>每页条数<select value={size} onChange={e => change(1, +e.target.value)}>
      {[20, 50, 100].map(v => <option key={v} value={v}>{v}</option>)}
    </select></label>
    <button disabled={page <= 1} title="前往上一页" onClick={() => change(page - 1, size)} aria-label="上一页"><ChevronLeft size={18} /></button>
    <button disabled={page >= pages} title="前往下一页" onClick={() => change(page + 1, size)} aria-label="下一页"><ChevronRight size={18} /></button>
    {page > pages && <button onClick={() => change(1, size)}>返回第一页</button>}
  </div>;
}
export function SearchForm({ value, onSearch }: { value: string; onSearch: (q: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return <form className="search-form" onSubmit={e => { e.preventDefault(); onSearch(text.trim()); }}>
    <label className="search-label"><Search size={17} aria-hidden="true" />
      <span className="sr-only">搜索证券</span>
      <input value={text} maxLength={100} onChange={e => setText(e.target.value)} placeholder="输入证券代码或名称" />
    </label>
    <button type="submit">搜索</button>
    {value && <button type="button" onClick={() => { setText(''); onSearch(''); }}>清除搜索</button>}
  </form>;
}
export function SortHead({ title, field, state, patch }: { title: string; field: Sort; state: ResearchState; patch: Patch }) {
  const active = state.sort === field;
  return <th aria-sort={active ? state.order === 'asc' ? 'ascending' : 'descending' : 'none'}>
    <button className="sort-button" title={`按${title}排序`} onClick={() => patch({ sort: field, order: active && state.order === 'asc' ? 'desc' : 'asc', page: 1 })}>
      {title}{active && (state.order === 'asc' ? <ArrowUp size={14} /> : <ArrowDown size={14} />)}
    </button>
  </th>;
}
export function Exclusions({ items }: { items: Exclusion[] }) {
  return <details className="evidence">
    <summary>查看排除原因（{items.length}）</summary>
    {items.length === 0 ? <p>无已报告排除。</p> : <TableScroll label="排除原因横向滚动">
      <table aria-label="排除原因"><thead><tr><th>证券代码</th><th>原因</th></tr></thead>
        <tbody>{items.map(item => <tr key={item.security_id}><th>{item.security_id}</th><td>{reasons(item.reasons)}</td></tr>)}</tbody>
      </table>
    </TableScroll>}
  </details>;
}
export function SnapshotEvidence({ snapshot }: { snapshot: Snapshot }) {
  return <details className="evidence">
    <summary>快照依据与单位</summary>
    <dl className="metadata-grid">
      <dt>快照标识</dt><dd>{snapshot.snapshot_id}</dd>
      <dt>统计截至</dt><dd>{timestamp(snapshot.as_of)}（北京时间）</dd>
      <dt>来源时间</dt><dd>{timestamp(snapshot.source_time)}（北京时间）</dd>
      <dt>发布时间</dt><dd>{timestamp(snapshot.result.published_at)}</dd>
      <dt>规则版本</dt><dd>{snapshot.rule_version}</dd>
      <dt>币种</dt><dd>{snapshot.units.currency.map(label).join('、') || '未知'}</dd>
      <dt>价格单位</dt><dd>{snapshot.units.price_unit.map(label).join('、') || '未知'}</dd>
      <dt>成交量单位</dt><dd>{snapshot.units.volume_unit.map(label).join('、') || '未知'}</dd>
      <dt>成交额单位</dt><dd>{snapshot.units.amount_unit.map(label).join('、') || '未知'}</dd>
      <dt>质量报告条数</dt><dd>{snapshot.quality_flags.length}</dd>
      <dt>历史起点</dt><dd>{snapshot.result.earliest_date}</dd>
      <dt>替代版本标识</dt><dd>{snapshot.result.supersedes || '无'}</dd>
    </dl>
    {snapshot.quality_flags.length > 0 && <p>存在质量报告记录，请结合各证券缺失原因判断；未知不代表无风险。</p>}
  </details>;
}
