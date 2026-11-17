import { useEffect, useState } from 'react';
import { intervalQuery, type ResearchState } from '../state';
import type { Patch } from './Common';

export function IntervalForm({ state, patch, reset = false }: {
  state: ResearchState; patch: Patch; reset?: boolean;
}) {
  const [custom, setCustom] = useState(Boolean(state.start || state.end));
  const [n, setN] = useState(String(state.n));
  const [start, setStart] = useState(state.start);
  const [end, setEnd] = useState(state.end);
  const [error, setError] = useState('');
  useEffect(() => {
    setCustom(Boolean(state.start || state.end));
    setN(String(state.n));
    setStart(state.start);
    setEnd(state.end);
    setError('');
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
      <label>区间方式<select value={custom ? 'custom' : 'n'} onChange={event => setCustom(event.target.value === 'custom')}>
        <option value="n">最近交易日间隔</option><option value="custom">自定义起止日期</option>
      </select></label>
      {custom ? <>
        <label>起始日期<input type="date" value={start} onChange={event => setStart(event.target.value)} aria-describedby={error ? 'interval-error' : undefined} /></label>
        <label>结束日期<input type="date" value={end} onChange={event => setEnd(event.target.value)} aria-describedby={error ? 'interval-error' : undefined} /></label>
      </> : <label>交易日间隔<input type="number" min="1" max="250" step="1" value={n} onChange={event => setN(event.target.value)} aria-describedby="interval-help" aria-invalid={Boolean(error)} /></label>}
      {reset && <>
        <label>基准指数<select aria-label="基准指数" value={state.benchmark} onChange={event => patch({ benchmark: event.target.value, page: 1 })}>
          <option value="B000300">沪深 300</option>
          <option value="B000905">中证 500</option>
        </select></label>
        <label>复权方式<select aria-label="复权方式" value={state.adj} onChange={event => patch({ adj: event.target.value as ResearchState['adj'], page: 1 })}>
          <option value="raw">不复权</option>
          <option value="qfq">前复权</option>
          <option value="hfq">后复权</option>
        </select></label>
      </>}
      <button className="primary" type="submit">应用区间</button>
      <button type="button" onClick={() => {
        if (patch({ n: 20, start: '', end: '', page: 1 })) {
          setCustom(false);
          setN('20');
          setStart('');
          setEnd('');
          setError('');
        }
      }}>重置区间</button>
    </div>
    <p id="interval-help" className="muted">间隔 20 需要 21 个交易日价格；自定义日期按快照交易日历对齐。样本不足不会自动缩短。</p>
    {error && <p role="alert" id="interval-error" className="notice error">{error}</p>}
  </form>;
}
