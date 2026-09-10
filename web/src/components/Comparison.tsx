import { useMemo } from 'react';
import { X } from 'lucide-react';
import { intervalQuery, type ResearchState } from '../state';
import { formatDecimal, label } from '../format';
import { useResource } from '../hooks/useResource';
import type { Envelope, Interval } from '../types';
import { Chart, lineStyles, palette } from './Chart';
import { Exclusions, Percent, ResourceStatus, TableScroll, type Patch } from './Common';

export function Comparison({ state, patch, refreshToken }: { state: ResearchState; patch: Patch; refreshToken: number }) {
  let query = '';
  try { query = intervalQuery(state, state.compare); } catch { /* The interval form reports invalid windows. */ }
  const resource = useResource<Envelope<Interval>>(
    state.compare.length && query ? `/api/v1/snapshots/${state.snapshot}/interval?${query}` : null,
    state.snapshot, refreshToken,
  );
  const data = resource.data;
  const option = useMemo(() => ({
    color: palette,
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    legend: { top: 4, type: 'scroll', textStyle: { fontSize: 12 } },
    grid: { left: 52, right: 20, top: 60, bottom: 64 },
    xAxis: { type: 'category', data: data?.result.sessions || [], boundaryGap: false },
    yAxis: { type: 'value', scale: true, name: '基准值' },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: data?.result.items.map((item, i) => ({
      name: item.security_id, type: 'line', data: item.normalized_curve.map(value => value === null ? null : Number(value)),
      connectNulls: false, showSymbol: false, lineStyle: { type: lineStyles[i % 5], width: 2 },
    })) || [],
  }), [data]);
  if (!state.compare.length) return <p className="muted comparison-hint">勾选证券可比较同一区间，最多 5 个标的。</p>;
  return <section className="comparison" aria-labelledby="comparison-title">
    <div className="section-heading"><h2 id="comparison-title" tabIndex={-1}>同区间比较</h2><button onClick={() => patch({ compare: [] })}>清空比较</button></div>
    <div className="selection-list" aria-label="已选比较标的">
      {state.compare.map(id => <button key={id} title={`移除 ${id}`} onClick={() => patch({ compare: state.compare.filter(v => v !== id) })}>{id}<X size={14} /><span className="sr-only">移除</span></button>)}
    </div>
    <ResourceStatus resource={resource} name="比较" />
    {!query && <p role="alert" className="notice">请输入有效研究区间后比较。</p>}
    {data && <>
      <p className="muted">实际区间 {data.result.sessions[0]} 至 {data.result.sessions.at(-1)} · {label(data.basis)} · 基准值 100（非收益率百分比）</p>
      <Chart option={option} description="同区间比较曲线，基准值为 100，缺失处断开；等价数据见下方比较数据表。" />
      <TableScroll label="比较指标横向滚动"><table aria-label="比较指标">
        <thead><tr><th>证券代码</th><th>区间收益率</th><th>最大回撤幅度</th></tr></thead>
        <tbody>{data.result.items.map(item => <tr key={item.security_id}><th>{item.security_id}</th><td><Percent value={item.interval_return} /></td><td><Percent value={item.maximum_drawdown} signed={false} /></td></tr>)}</tbody>
      </table></TableScroll>
      <details className="evidence"><summary>查看比较数据表</summary>
        <TableScroll label="比较曲线数据横向滚动"><table aria-label="比较曲线数据">
          <thead><tr><th>交易日期</th>{data.result.items.map(item => <th key={item.security_id}>{item.security_id}</th>)}</tr></thead>
          <tbody>{data.result.sessions.map((day, index) => <tr key={day}><th>{day}</th>{data.result.items.map(item => <td key={item.security_id}>{formatDecimal(item.normalized_curve[index])}</td>)}</tr>)}</tbody>
        </table></TableScroll>
      </details>
      <Exclusions items={data.result.exclusions} />
    </>}
  </section>;
}
