import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { intervalQuery, type ResearchState } from '../state';
import { formatDecimal, label } from '../format';
import { useResource } from '../hooks/useResource';
import { analysisRange } from '../features/analysis/query';
import type { Envelope, Interval, ResetCurveData, Snapshot, WorkbenchEnvelope } from '../types';
import { Chart, lineStyles, palette } from './Chart';
import { Exclusions, Percent, ResourceStatus, TableScroll, type Patch } from './Common';

function curveValue(value: number | null, mode: 'normalized' | 'drawdown'): string {
  if (value === null) return '—';
  return mode === 'drawdown'
    ? `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value * 100)}%`
    : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value);
}

export interface ComparisonProps {
  state: ResearchState; patch: Patch; refreshToken: number; snapshot?: Snapshot; reset?: boolean;
}
export function Comparison({ state, patch, refreshToken, snapshot, reset = false }: ComparisonProps) {
  const [mode, setMode] = useState<'normalized' | 'drawdown'>('normalized');
  let query = '';
  try { query = intervalQuery(state, state.compare); } catch { /* The interval form reports invalid windows. */ }
  const resource = useResource<Envelope<Interval>>(
    !reset && state.compare.length && query ? `/api/v1/snapshots/${state.snapshot}/interval?${query}` : null,
    state.snapshot, refreshToken,
  );
  let resetQuery = '';
  try {
    if (reset && snapshot && state.compare.length <= 5) {
      const range = analysisRange(state, snapshot.result.calendar_sessions);
      resetQuery = new URLSearchParams({
        snapshot_id: state.snapshot,
        symbols: state.compare.join(','),
        start: range.start,
        end: range.end,
        benchmark: state.benchmark,
        adj: state.adj,
      }).toString();
    }
  } catch { /* The interval form reports invalid windows. */ }
  const resetResource = useResource<WorkbenchEnvelope<ResetCurveData | null>>(
    reset && state.compare.length && resetQuery ? `/api/compare/curves?${resetQuery}` : null,
    state.snapshot,
    refreshToken,
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
  const resetData = resetResource.data;
  const dates = resetData?.data?.curves[0]?.points.map(point => point.trade_date) || [];
  const resetOption = useMemo(() => ({
    color: palette,
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    legend: { top: 4, type: 'scroll', textStyle: { fontSize: 12 } },
    grid: { left: 52, right: 20, top: 60, bottom: 64 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', scale: true, name: mode === 'normalized' ? '基准值' : '回撤' },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: resetData?.data?.curves.map((curve, index) => ({
      name: curve.security_id,
      type: 'line',
      data: curve.points.map(point => point[mode]),
      connectNulls: false,
      showSymbol: false,
      lineStyle: { type: lineStyles[index % 5], width: 2 },
    })) || [],
  }), [dates, mode, resetData]);
  if (!state.compare.length) return <p className="muted comparison-hint">勾选证券可比较同一区间，最多 5 个标的。</p>;
  if (reset) return <section className="comparison" aria-labelledby="comparison-title">
    <div className="section-heading">
      <h2 id="comparison-title" tabIndex={-1}>同区间比较</h2>
      <button onClick={() => patch({ compare: [] })}>清空比较</button>
    </div>
    <div className="selection-list" aria-label="已选比较标的">
      {state.compare.map(id => <button key={id} title={`移除 ${id}`} onClick={() => patch({ compare: state.compare.filter(value => value !== id) })}>{id}<X size={14} /><span className="sr-only">移除</span></button>)}
    </div>
    <div className="segmented" role="radiogroup" aria-label="曲线模式">
      <label><input type="radio" name="curve-mode" value="normalized" checked={mode === 'normalized'} onChange={() => setMode('normalized')} />归一化</label>
      <label><input type="radio" name="curve-mode" value="drawdown" checked={mode === 'drawdown'} onChange={() => setMode('drawdown')} />回撤</label>
    </div>
    <ResourceStatus resource={resetResource} name="比较曲线" />
    {!resetQuery && <p role="alert" className="notice">请输入有效研究区间后比较。</p>}
    {resetData?.data === null && <p className="notice" role="status">
      {state.adj !== 'raw' ? '当前来源无法证明该复权口径' : '当前比较曲线不可用'}
    </p>}
    {resetData?.data && <>
      <p className="muted">实际区间 {resetData.data.adjusted_start} 至 {resetData.data.adjusted_end} · 缺失点不跨越连线</p>
      <Chart
        option={resetOption}
        description={`${mode === 'normalized' ? '归一化' : '回撤'}比较曲线，缺失处断开；精确值见下方表格。`}
      />
      <TableScroll label="比较曲线精确值">
        <table aria-label="比较曲线精确值" className="responsive-record-table">
          <thead><tr><th>交易日期</th>{resetData.data.curves.map(curve => <th key={curve.security_id}>{curve.security_id}</th>)}</tr></thead>
          <tbody>{dates.map((day, pointIndex) => <tr key={day}>
            <th data-label="交易日期">{day}</th>
            {resetData.data!.curves.map(curve =>
              <td data-label={curve.security_id} key={curve.security_id}>{curveValue(curve.points[pointIndex]?.[mode] ?? null, mode)}</td>)}
          </tr>)}</tbody>
        </table>
      </TableScroll>
      <Exclusions items={resetData.data.exclusions} />
    </>}
  </section>;
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
