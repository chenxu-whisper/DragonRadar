import { useMemo } from 'react';
import { formatDecimal, label, reasons } from '../format';
import { useResource } from '../hooks/useResource';
import type { Envelope, Overview as Breadth, Securities } from '../types';
import type { ResearchState } from '../state';
import { Chart } from '../components/Chart';
import { Exclusions, Pager, Percent, ResourceStatus, SearchForm, SortHead, TableScroll, type Patch } from '../components/Common';

interface Props { state: ResearchState; patch: Patch; refreshToken: number; open: (id: string, trigger: HTMLElement) => void; compare: (id: string) => void }
export function Overview({ state, patch, refreshToken, open, compare }: Props) {
  const base = `/api/v1/snapshots/${state.snapshot}`;
  const breadth = useResource<Envelope<Breadth>>(`${base}/overview`, state.snapshot, refreshToken);
  const params = new URLSearchParams({
    q: state.q, sort: state.sort, order: state.order, page: String(state.page), page_size: String(state.page_size),
  });
  const rows = useResource<Envelope<Securities>>(`${base}/securities?${params}`, state.snapshot, refreshToken);
  const counts = breadth.data?.result;
  const option = useMemo(() => ({
    grid: { left: 48, right: 16, top: 16, bottom: 32 },
    xAxis: { type: 'category', data: ['上涨', '下跌', '平盘'] },
    yAxis: { type: 'value', minInterval: 1, name: '家' },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    series: [{ type: 'bar', barMaxWidth: 48, data: counts ? [
      { value: counts.advancing, itemStyle: { color: '#b42332' } },
      { value: counts.declining, itemStyle: { color: '#087347' } },
      { value: counts.unchanged, itemStyle: { color: '#64748b' } },
    ] : [] }],
  }), [counts]);
  return <>
    <div className="page-heading"><div><p className="eyebrow">快照事实 / 只读研究</p><h1>市场总览</h1></div><span className="muted">不提供交易指令</span></div>
    <section aria-label="市场涨跌分布">
      <ResourceStatus resource={breadth} name="涨跌分布" />
      {counts && <>
        <div className="breadth-layout">
          <div>
            <h2>涨跌分布</h2>
            <table className="breadth-table" aria-label="涨跌分布数据"><thead><tr><th>分类</th><th>证券数量</th></tr></thead>
              <tbody><tr><th>上涨</th><td className="positive">+ {counts.advancing}</td></tr><tr><th>下跌</th><td className="negative">- {counts.declining}</td></tr><tr><th>平盘</th><td>{counts.unchanged}</td></tr></tbody>
            </table>
          </div>
          <Chart option={option} description="市场上涨、下跌、平盘证券数量柱状图；左侧表格提供等价数据。" />
        </div>
        <p className="muted">日收益覆盖：有效 {counts.coverage.valid} / 应有 {counts.coverage.expected} · 合法排除 {counts.coverage.legal_excluded} · 缺失 {counts.coverage.missing}。未知不计入平盘。</p>
        <Exclusions items={counts.exclusions} />
        {counts.returns.some(item => item.warnings.length) && <details className="evidence"><summary>日收益计算提示</summary>
          <ul>{counts.returns.filter(item => item.warnings.length).map(item => <li key={item.security_id}>{item.security_id}：{reasons(item.warnings)}</li>)}</ul>
        </details>}
      </>}
    </section>
    <section className="securities" aria-labelledby="securities-title">
      <div className="section-heading"><h2 id="securities-title">证券行情</h2><span className="muted">未复权收盘价 · 涨跌为日收益率</span></div>
      <SearchForm value={state.q} onSearch={q => patch({ q, page: 1 })} />
      <ResourceStatus resource={rows} name="证券列表" />
      {rows.data && <>
        <TableScroll label="证券列表横向滚动">
          <table aria-label="证券列表"><thead><tr>
            <th className="identity">证券 / 名称</th><th>比较</th>
            <SortHead title="收盘价" field="close" state={state} patch={patch} />
            <SortHead title="日涨跌幅" field="change" state={state} patch={patch} />
            <SortHead title="成交额" field="amount" state={state} patch={patch} />
            <th>市场 / 板块</th><th>状态与缺失</th>
          </tr></thead>
            <tbody>{rows.data.result.items.map(row => <tr key={row.security_id} className="clickable-row" onClick={e => {
              if ((e.target as HTMLElement).closest('button,input,label,a')) return;
              const trigger = e.currentTarget.querySelector<HTMLButtonElement>('.security-button');
              if (trigger) { trigger.focus(); open(row.security_id, trigger); }
            }}>
              <th className="identity"><button className="security-button" onClick={e => open(row.security_id, e.currentTarget)}>{row.name || '名称未知'}</button><span className="security-id">{row.security_id}</span></th>
              <td><label className="check-target"><input type="checkbox" checked={state.compare.includes(row.security_id)} onChange={() => compare(row.security_id)} aria-label={`比较 ${row.security_id}`} /></label></td>
              <td className="numeric">{formatDecimal(row.close)}<small>{label(row.currency)} · {label(row.price_unit)}</small></td>
              <td className="numeric"><Percent value={row.change} /></td>
              <td className="numeric">{formatDecimal(row.amount)}<small>{label(row.currency)} · {label(row.amount_unit)}</small></td>
              <td>{label(row.exchange)}<small>{label(row.board)}</small></td>
              <td className="reason-cell"><span>{row.suspended === null ? '交易状态未知' : row.suspended ? '停牌' : '非停牌'}</span><small>{reasons(row.missing_reasons)}</small></td>
            </tr>)}</tbody>
          </table>
        </TableScroll>
        {rows.data.result.total === 0 && <p className="empty">没有匹配证券，请清除或修改搜索条件。</p>}
        <div className="toolbar">
          <label>身份排序<select aria-label="身份排序" value={['security_id', 'name'].includes(state.sort) ? state.sort : ''} onChange={e => patch({ sort: e.target.value as 'name' | 'security_id', page: 1 })}>
            <option value="" disabled>按数值列排序中</option><option value="security_id">证券代码</option><option value="name">证券名称</option>
          </select></label>
          <label>排序方向<select value={state.order} onChange={e => patch({ order: e.target.value as 'asc' | 'desc', page: 1 })}><option value="asc">升序</option><option value="desc">降序</option></select></label>
        </div>
        <Pager page={rows.data.result.page} size={rows.data.result.page_size} total={rows.data.result.total} change={(page, page_size) => patch({ page, page_size })} />
      </>}
    </section>
  </>;
}
