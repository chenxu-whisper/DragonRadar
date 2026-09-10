import { useEffect, useMemo, useRef } from 'react';
import { ArrowLeft, ExternalLink, X } from 'lucide-react';
import { formatDecimal, label, reasons } from '../format';
import { useResource } from '../hooks/useResource';
import { stateSearch, type ResearchState } from '../state';
import type { Detail, Envelope } from '../types';
import { Chart } from '../components/Chart';
import { Percent, ResourceStatus, TableScroll, type Patch } from '../components/Common';

function DetailContent({ state, id, refreshToken }: { state: ResearchState; id: string; refreshToken: number }) {
  const resource = useResource<Envelope<Detail>>(`/api/v1/snapshots/${state.snapshot}/securities/${encodeURIComponent(id)}`, state.snapshot, refreshToken);
  const data = resource.data?.result;
  const pricedHistory = data?.history.filter(point => point.close !== null) || [];
  const firstPrice = pricedHistory[0];
  const inconsistentUnits = pricedHistory.some(point =>
    !point.currency?.trim() || !point.price_unit?.trim() ||
    point.currency !== firstPrice.currency || point.price_unit !== firstPrice.price_unit,
  );
  const option = useMemo(() => ({
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    grid: { left: 52, right: 16, top: 28, bottom: 70 },
    xAxis: { type: 'category', data: data?.history.map(point => point.trade_date) || [] },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: data?.chart_type === 'candlestick' ? [{
      type: 'candlestick', name: '未复权价格',
      data: data.history.map(point => [point.open, point.close, point.low, point.high].map(value => value === null ? '-' : Number(value))),
      itemStyle: { color: '#b42332', color0: '#087347', borderColor: '#b42332', borderColor0: '#087347' },
    }] : [{
      type: 'line', name: '未复权收盘价', connectNulls: false, showSymbol: false,
      data: data?.history.map(point => point.close === null ? null : Number(point.close)) || [],
      lineStyle: { color: '#1759ad', width: 2 },
    }],
  }), [data]);
  return <>
    <ResourceStatus resource={resource} name="证券详情" />
    {data && <>
      <div className="detail-heading"><h2>{data.security.name || '名称未知'}</h2><span>{data.security.security_id} · {label(data.security.exchange)} · {label(data.security.board)}</span></div>
      <dl className="quote">
        <div><dt>收盘价</dt><dd>{formatDecimal(data.security.close)}</dd></div>
        <div><dt>日涨跌幅</dt><dd><Percent value={data.security.change} /></dd></div>
        <div><dt>交易状态</dt><dd>{data.security.suspended === null ? '未知' : data.security.suspended ? '停牌' : '非停牌'}</dd></div>
      </dl>
      <p className="muted">{label(data.price_basis)} · {label(data.security.currency)} · {label(data.security.price_unit)}</p>
      <p className="detail-reasons">{reasons(data.security.missing_reasons)}</p>
      <h3>{data.chart_type === 'candlestick' ? '历史日线' : '历史收盘价'}</h3>
      {inconsistentUnits ? <p className="notice" role="alert">历史价格数据口径不一致：存在不同或未知币种、价格单位，暂不绘制历史图。未进行任何换算，完整精确值及每日单位保留在下方数据表。</p> : <>
        <p className="muted">{data.chart_type === 'candlestick' ? '开高低收数据完整时展示日线。' : '开高低收不完整，按接口口径展示收盘价线。'}缺失处不连线。图表为近似刻度，精确值见数据表。</p>
        <Chart option={option} description={`${data.security.security_id} ${data.chart_type === 'candlestick' ? '历史日线图' : '历史收盘价线图'}；完整历史精确数据见下方表格，缺失处断开。`} />
      </>}
      <details className="evidence"><summary>查看完整历史数据（{data.history.length} 个交易日）</summary>
        <TableScroll label="完整历史数据横向滚动"><table aria-label="完整历史数据"><thead><tr>
          <th>交易日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>成交额</th><th>币种 / 单位</th><th>状态与缺失</th>
        </tr></thead><tbody>{data.history.map(point => <tr key={point.trade_date}>
          <th>{point.trade_date}</th><td>{formatDecimal(point.open)}</td><td>{formatDecimal(point.high)}</td><td>{formatDecimal(point.low)}</td><td>{formatDecimal(point.close)}</td>
          <td>{formatDecimal(point.volume)}</td><td>{formatDecimal(point.amount)}</td>
          <td>{[point.currency, point.price_unit, point.volume_unit, point.amount_unit].map(label).join(' / ')}</td>
          <td>{point.suspended === null ? '状态未知' : point.suspended ? '停牌' : '非停牌'}；{reasons(point.missing_reasons)}</td>
        </tr>)}</tbody></table></TableScroll>
      </details>
    </>}
  </>;
}
export function SecurityDrawer({ state, id, trigger, close, patch, refreshToken }: {
  state: ResearchState; id: string; trigger: HTMLElement; close: () => void; patch: Patch; refreshToken: number;
}) {
  const dialog = useRef<HTMLDialogElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    dialog.current?.showModal(); closeButton.current?.focus();
    return () => { dialog.current?.close(); if (trigger.isConnected) trigger.focus(); };
  }, [trigger]);
  const next = { ...state, view: 'stock' as const, security: id, return_view: state.view === 'analysis' ? 'analysis' as const : 'overview' as const };
  return <dialog ref={dialog} className="security-drawer" aria-labelledby="drawer-title" onCancel={e => { e.preventDefault(); close(); }}>
    <div className="drawer-toolbar"><h2 id="drawer-title">证券详情 · 只读</h2><button ref={closeButton} onClick={close} aria-label="关闭详情" title="关闭详情（退出键）"><X size={20} /></button></div>
    <a className="fullpage-link" href={stateSearch(next)} onClick={e => { if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); close(); patch(next); } }}><ExternalLink size={16} />打开完整详情</a>
    <DetailContent state={state} id={id} refreshToken={refreshToken} />
  </dialog>;
}
export function SecurityPage({ state, patch, refreshToken }: { state: ResearchState; patch: Patch; refreshToken: number }) {
  const back = { ...state, view: state.return_view, security: '' };
  return <>
    <a className="back-link" href={stateSearch(back)} onClick={event => { if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); patch(back); } }}><ArrowLeft size={18} />返回研究列表</a>
    <h1>证券详情</h1>
    {state.security ? <DetailContent state={state} id={state.security} refreshToken={refreshToken} /> : <p className="empty">未指定证券，请返回研究列表选择。</p>}
  </>;
}
