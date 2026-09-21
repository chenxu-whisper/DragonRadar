import { useMemo, type ComponentType } from 'react';
import { formatDecimal, label, reasons } from '../format';
import { useResource } from '../hooks/useResource';
import type { Envelope, Overview as Breadth, ResetHeatmap, ResetOverview, Securities, WorkbenchEnvelope } from '../types';
import type { ResearchState } from '../state';
import { Chart } from '../components/Chart';
import { Exclusions, Pager, Percent, ResourceStatus, SearchForm, SortHead, TableScroll, type Patch } from '../components/Common';

interface Props {
  state: ResearchState; patch: Patch; refreshToken: number; open: (id: string, trigger: HTMLElement) => void;
  compare: (id: string) => void; ReviewActions?: ComponentType<{ snapshot: string; security: string }>; reset?: boolean;
}
export function Overview({ state, patch, refreshToken, open, compare, ReviewActions, reset = false }: Props) {
  const base = `/api/v1/snapshots/${state.snapshot}`;
  const selector = new URLSearchParams({ snapshot_id: state.snapshot });
  if (state.date) selector.set('date', state.date);
  const resetOverview = useResource<WorkbenchEnvelope<ResetOverview>>(reset ? `/api/market/overview?${selector}` : null, state.snapshot, refreshToken);
  const resetHeatmap = useResource<WorkbenchEnvelope<ResetHeatmap>>(reset ? `/api/market/heatmap?${selector}&days=60` : null, state.snapshot, refreshToken);
  const resetSectors = useResource<WorkbenchEnvelope<{ sectors: ResetOverview['hot_sectors'] }>>(reset ? `/api/market/sector-flow?${selector}&top_n=10` : null, state.snapshot, refreshToken);
  const resetSentiment = useResource<WorkbenchEnvelope<{ series: unknown[] }>>(reset ? `/api/market/sentiment?${selector}&days=20` : null, state.snapshot, refreshToken);
  const breadth = useResource<Envelope<Breadth>>(reset ? null : `${base}/overview`, state.snapshot, refreshToken);
  const params = new URLSearchParams({
    q: state.q, sort: state.sort, order: state.order, page: String(state.page), page_size: String(state.page_size),
  });
  const rows = useResource<Envelope<Securities>>(reset ? null : `${base}/securities?${params}`, state.snapshot, refreshToken);
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
  if (reset) {
    const overview = resetOverview.data?.data;
    const ratio = (value: number | null) => value === null ? '不适用' : `${(value * 100).toFixed(2)}%`;
    const filterNames = { up: '上涨', down: '下跌', flat: '平盘', limit_up: '涨停', limit_down: '跌停' };
    const coverage = (value: { expected: number; valid: number; excluded: number; missing: number }) =>
      `覆盖 ${value.valid}/${value.expected} · 排除 ${value.excluded} · 缺失 ${value.missing}`;
    const direction = ['up', 'down', 'flat'].includes(state.market_filter)
      ? state.market_filter as 'up' | 'down' | 'flat'
      : null;
    const selectedMembers = overview && direction ? overview.breadth.members[direction] : null;
    return <>
      <div className="page-heading"><div><p className="eyebrow">发布事实 / 原始市场观测</p><h1>市场总览</h1></div><span className="muted">不含研究评分与阶段判断</span></div>
      <ResourceStatus resource={resetOverview} name="市场总览" />
      {overview && <>
        {overview.indexes.length > 0 && <section aria-label="指数横条" className="index-strip">
          {overview.indexes.map(item => <div key={item.security_id}><strong>{item.name}</strong><span>{item.last ?? '—'}</span><small>{ratio(item.change_ratio)}</small></div>)}
        </section>}
        <section aria-label="涨跌与情绪" className="market-grid">
          <div>
            <h2>涨跌分布</h2>
            <div className="metric-actions">
              <button aria-pressed={state.market_filter === 'up'} onClick={() => patch({ market_filter: 'up' })}>上涨 {overview.breadth.up}</button>
              <button aria-pressed={state.market_filter === 'down'} onClick={() => patch({ market_filter: 'down' })}>下跌 {overview.breadth.down}</button>
              <button aria-pressed={state.market_filter === 'flat'} onClick={() => patch({ market_filter: 'flat' })}>平盘 {overview.breadth.flat}</button>
            </div>
            {direction && selectedMembers && <div className="filter-context" role="region" aria-label={`${filterNames[direction]}成员清单`}>
              <strong>{filterNames[direction]}成员 · 共 {selectedMembers.length} 家</strong>
              <ul className="filter-members">{selectedMembers.map(item =>
                <li key={item.security_id}><span>{item.name}</span><small>{item.code ?? '代码未知'} · {item.security_id}</small></li>)}
              </ul>
            </div>}
            <p className="muted">有效 {overview.breadth.coverage.valid} / 应有 {overview.breadth.coverage.expected} · 排除 {overview.breadth.coverage.excluded} · 缺失 {overview.breadth.coverage.missing}</p>
            <p>封板率 {overview.breadth.seal_rate.numerator}/{overview.breadth.seal_rate.denominator} · {ratio(overview.breadth.seal_rate.value)}</p>
            <small>{coverage(overview.breadth.seal_rate.coverage)}{overview.breadth.seal_rate.reason && ` · ${overview.breadth.seal_rate.reason}`}</small>
          </div>
          <div>
            <h2>情绪原始观测</h2>
            <dl className="market-observations">
              <div><dt>涨停 / 跌停</dt><dd>{overview.sentiment.limit_up_count} / {overview.sentiment.limit_down_count}</dd></div>
              <div><dt>最高板</dt><dd>{overview.sentiment.max_boards}</dd></div>
              <div><dt>晋级率</dt><dd>{overview.sentiment.promote_rate.numerator}/{overview.sentiment.promote_rate.denominator} · {ratio(overview.sentiment.promote_rate.value)}</dd>
                <small>覆盖 {overview.sentiment.promote_rate.denominator}/{overview.sentiment.promote_rate.yesterday_ge2} · 排除 {overview.sentiment.promote_rate.excluded} · 缺失 {overview.sentiment.promote_rate.missing}{overview.sentiment.promote_rate.reason && ` · ${overview.sentiment.promote_rate.reason}`}</small>
              </div>
              <div><dt>昨涨停今收益中位数</dt><dd>{ratio(overview.sentiment.yesterday_limit_up_median_return)}</dd>
                <small>{coverage(overview.sentiment.yesterday_limit_up_return_coverage)}{overview.sentiment.yesterday_limit_up_return_reason && ` · ${overview.sentiment.yesterday_limit_up_return_reason}`}</small>
              </div>
            </dl>
          </div>
        </section>
        <section aria-labelledby="factor-title">
          <div className="section-heading"><h2 id="factor-title">市场因子</h2><span className="muted">独立窗口 · 发布时物化</span></div>
          <div className="factor-grid">
            <div>
              <strong>FAC-M01 市场广度</strong>
              <p>上涨占比 {overview.factors['FAC-M01'].advancing.numerator}/{overview.factors['FAC-M01'].advancing.denominator} · {ratio(overview.factors['FAC-M01'].advancing.value)}</p>
              <small>{coverage(overview.factors['FAC-M01'].advancing.coverage)}{overview.factors['FAC-M01'].advancing.reason && ` · ${overview.factors['FAC-M01'].advancing.reason}`}</small>
              <p>高于 MA20 {overview.factors['FAC-M01'].above_ma20.numerator}/{overview.factors['FAC-M01'].above_ma20.denominator} · {ratio(overview.factors['FAC-M01'].above_ma20.value)}</p>
              <small>{coverage(overview.factors['FAC-M01'].above_ma20.coverage)}{overview.factors['FAC-M01'].above_ma20.reason && ` · ${overview.factors['FAC-M01'].above_ma20.reason}`}</small>
            </div>
            <div>
              <strong>FAC-M02 成交变化</strong>
              <p>相对前 20 日均值 {ratio(overview.factors['FAC-M02'].value)}</p>
              <small>窗口 {overview.factors['FAC-M02'].baseline_days} 日，不含当日 · {coverage(overview.factors['FAC-M02'].coverage)}</small>
              {overview.factors['FAC-M02'].reason && <small>{overview.factors['FAC-M02'].reason}</small>}
            </div>
            <div>
              <strong>FAC-M03 指数趋势与波动</strong>
              {overview.factors['FAC-M03'].indexes.map(item => <dl className="factor-index" key={item.security_id}>
                <div><dt>{item.name} C/MA20-1</dt><dd>{ratio(item.close_over_ma20)}</dd><small>窗口 20 日 · {coverage(item.coverage.ma20)}{item.close_over_ma20_reason && ` · ${item.close_over_ma20_reason}`}</small></div>
                <div><dt>C/MA60-1</dt><dd>{ratio(item.close_over_ma60)}</dd><small>窗口 60 日 · {coverage(item.coverage.ma60)}{item.close_over_ma60_reason && ` · ${item.close_over_ma60_reason}`}</small></div>
                <div><dt>20 日收益波动</dt><dd>{ratio(item.volatility_20)}</dd><small>20 个相邻收益，非年化样本标准差 · {coverage(item.coverage.return20)}{item.volatility_20_reason && ` · ${item.volatility_20_reason}`}</small></div>
              </dl>)}
            </div>
          </div>
        </section>
        <section aria-labelledby="sector-title">
          <div className="section-heading"><h2 id="sector-title">题材扩散</h2><button disabled={!overview.historical_topic_analysis.enabled}>历史题材分析</button></div>
          {!overview.historical_topic_analysis.enabled && <p className="muted">缺少历史题材成员覆盖，历史分析不可用。{overview.historical_topic_analysis.reason && ` ${overview.historical_topic_analysis.reason}`}</p>}
          <div className="sector-list">{overview.hot_sectors.map(item =>
            <button key={item.sector_id} onClick={() => patch({ view: 'leaders', sector: item.sector_id })}>
              <strong>{item.name}</strong><span>成员 {item.member_count} · 中位收益 {ratio(item.median_return)}</span>
              <span>上涨占比 {item.up_ratio.numerator}/{item.up_ratio.denominator} · {ratio(item.up_ratio.value)}</span>
              <span>涨停占比 {item.limit_up_ratio.numerator}/{item.limit_up_ratio.denominator} · {ratio(item.limit_up_ratio.value)}</span>
              <small>{coverage(item.coverage)}{item.up_ratio.reason && ` · ${item.up_ratio.reason}`}{item.limit_up_ratio.reason && ` · ${item.limit_up_ratio.reason}`}</small>
              <small>净流入：不适用 · {item.net_inflow_reason}{item.quality_flags.includes('sample_insufficient') ? ' · 样本不足' : ''}</small>
            </button>)}</div>
        </section>
        <section aria-labelledby="heatmap-title">
          <h2 id="heatmap-title">涨停情绪热力图</h2>
          <ResourceStatus resource={resetHeatmap} name="热力图" />
          <div className="heatmap-cells">{resetHeatmap.data?.data.cells.map(cell =>
            <button key={`${cell.trade_date}-${cell.boards}`} aria-label={`${cell.trade_date} ${cell.boards}板 ${cell.count}家`}
              onClick={() => patch({ view: 'leaders', date: cell.trade_date, snapshot: '', sector: '' })}>
              <span>{cell.trade_date}</span><strong>{cell.boards}板</strong><b>{cell.count}</b>
            </button>)}</div>
        </section>
        <details className="evidence"><summary>来源与方法</summary>
          <p>来源 {resetOverview.data?.source} / {resetOverview.data?.source_version} · 截至 {resetOverview.data?.as_of} · 比率单位为小数，页面按百分数展示。</p>
        </details>
      </>}
      <ResourceStatus resource={resetSectors} name="题材统计" />
      <ResourceStatus resource={resetSentiment} name="情绪序列" />
    </>;
  }
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
              <th className="identity"><button className="security-button" onClick={e => open(row.security_id, e.currentTarget)}>{row.name || '名称未知'}</button><span className="security-id">{row.security_id}</span>
                {ReviewActions && <ReviewActions snapshot={state.snapshot} security={row.security_id} />}</th>
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
