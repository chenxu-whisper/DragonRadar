import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { compareDecimal, label } from '../format';
import { intervalQuery, type ResearchState } from '../state';
import { useResource } from '../hooks/useResource';
import type { Envelope, Interval, IntervalItem } from '../types';
import { Exclusions, Pager, Percent, ResourceStatus, SearchForm, SortHead, TableScroll, type Patch } from '../components/Common';

export function IntervalForm({ state, patch }: { state: ResearchState; patch: Patch }) {
  const [custom, setCustom] = useState(Boolean(state.start || state.end));
  const [n, setN] = useState(String(state.n));
  const [start, setStart] = useState(state.start), [end, setEnd] = useState(state.end);
  const [error, setError] = useState('');
  useEffect(() => {
    setCustom(Boolean(state.start || state.end)); setN(String(state.n)); setStart(state.start); setEnd(state.end); setError('');
  }, [state.n, state.start, state.end]);
  return <form className="interval-form" noValidate onSubmit={event => {
    event.preventDefault();
    const next = { n: n.trim() ? Number(n) : 0, start: custom ? start : '', end: custom ? end : '' };
    try {
      if (custom && (!start || !end)) throw new Error('请输入完整的起止日期。');
      intervalQuery(next);
      if (patch({ ...next, page: 1 })) setError('');
    } catch (failure) { setError((failure as Error).message); }
  }}>
    <div className="toolbar">
      <label>区间方式<select value={custom ? 'custom' : 'n'} onChange={e => setCustom(e.target.value === 'custom')}><option value="n">最近交易日间隔</option><option value="custom">自定义起止日期</option></select></label>
      {custom ? <>
        <label>起始日期<input type="date" value={start} onChange={e => setStart(e.target.value)} aria-describedby={error ? 'interval-error' : undefined} /></label>
        <label>结束日期<input type="date" value={end} onChange={e => setEnd(e.target.value)} aria-describedby={error ? 'interval-error' : undefined} /></label>
      </> : <label>交易日间隔<input type="number" min="1" max="250" step="1" value={n} onChange={e => setN(e.target.value)} aria-describedby="interval-help" aria-invalid={Boolean(error)} /></label>}
      <button className="primary" type="submit">应用区间</button>
      <button type="button" onClick={() => {
        if (patch({ n: 20, start: '', end: '', page: 1 })) {
          setCustom(false); setN('20'); setStart(''); setEnd(''); setError('');
        }
      }}>重置区间</button>
    </div>
    <p id="interval-help" className="muted">间隔 20 需要 21 个交易日价格；自定义日期按快照交易日历对齐。样本不足不会自动缩短。</p>
    {error && <p role="alert" id="interval-error" className="notice error">{error}</p>}
  </form>;
}

export function Analysis({ state, patch, refreshToken, open, compare, ReviewActions }: {
  state: ResearchState; patch: Patch; refreshToken: number;
  open: (id: string, trigger: HTMLElement) => void; compare: (id: string) => void;
  ReviewActions?: ComponentType<{ snapshot: string; security: string }>;
}) {
  let query = '', error = '';
  try { query = intervalQuery(state); } catch (failure) { error = (failure as Error).message; }
  const resource = useResource<Envelope<Interval>>(query ? `/api/v1/snapshots/${state.snapshot}/interval?${query}` : null, state.snapshot, refreshToken);
  const data = resource.data;
  const filtered = useMemo(() => {
    const items = (data?.result.items || []).filter(item => item.security_id.toLowerCase().includes(state.q.toLowerCase()));
    const field = ['interval_return', 'maximum_drawdown'].includes(state.sort) ? state.sort as keyof IntervalItem : 'security_id';
    return [...items].sort((a, b) => {
      const tie = a.security_id.localeCompare(b.security_id);
      return field === 'security_id' ? tie * (state.order === 'asc' ? 1 : -1) :
        compareDecimal(a[field] as string | null, b[field] as string | null, state.order) || tie;
    });
  }, [data, state.q, state.sort, state.order]);
  const visible = filtered.slice((state.page - 1) * state.page_size, state.page * state.page_size);
  const coverage = data?.analysis_coverage;
  return <>
    <div className="page-heading"><div><p className="eyebrow">统一端点 / 可追溯计算</p><h1>区间分析</h1></div></div>
    <IntervalForm state={state} patch={patch} />
    {error && <p role="alert" className="notice error">{error}</p>}
    <ResourceStatus resource={resource} name="区间分析" />
    {data && <>
      <div className="analysis-context">
        <strong>实际区间：{data.result.sessions[0]} 至 {data.result.sessions.at(-1)}</strong>
        <span>{data.result.sessions.length} 个交易日价格 · {label(data.basis)}</span>
        {coverage && 'requested' in coverage && <span>区间覆盖：有效 {coverage.eligible} / 请求 {coverage.requested} · 排除 {coverage.excluded}</span>}
      </div>
      <p className="muted">收益率以百分比展示；最大回撤是非负下降幅度。曲线基准值为 100，不是百分比。</p>
      <div className="section-heading"><h2>区间指标</h2><span className="muted">搜索证券代码 · 全区间结果精确排序</span></div>
      <SearchForm value={state.q} onSearch={q => patch({ q, page: 1 })} />
      <TableScroll label="区间指标横向滚动"><table aria-label="区间指标"><thead><tr>
        <SortHead title="证券代码" field="security_id" state={state} patch={patch} /><th>比较</th>
        <SortHead title="区间收益率" field="interval_return" state={state} patch={patch} />
        <SortHead title="最大回撤幅度" field="maximum_drawdown" state={state} patch={patch} />
      </tr></thead><tbody>{visible.map(item => <tr key={item.security_id} className="clickable-row" onClick={event => {
        if ((event.target as HTMLElement).closest('button,input,label,a')) return;
        const trigger = event.currentTarget.querySelector<HTMLButtonElement>('button');
        if (trigger) { trigger.focus(); open(item.security_id, trigger); }
      }}>
        <th className="identity"><button className="security-button" onClick={e => open(item.security_id, e.currentTarget)}>{item.security_id}</button>
          {ReviewActions && <ReviewActions snapshot={state.snapshot} security={item.security_id} />}</th>
        <td><label className="check-target"><input type="checkbox" aria-label={`比较 ${item.security_id}`} checked={state.compare.includes(item.security_id)} onChange={() => compare(item.security_id)} /></label></td>
        <td className="numeric"><Percent value={item.interval_return} /></td><td className="numeric"><Percent value={item.maximum_drawdown} signed={false} /></td>
      </tr>)}</tbody></table></TableScroll>
      {filtered.length === 0 && <p className="empty">没有可分析的匹配标的，请检查搜索条件与排除原因。</p>}
      <Pager page={state.page} size={state.page_size} total={filtered.length} change={(page, page_size) => patch({ page, page_size })} />
      <Exclusions items={data.result.exclusions} />
    </>}
  </>;
}
