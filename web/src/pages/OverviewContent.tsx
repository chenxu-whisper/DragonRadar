import { lazy, Suspense, useEffect, useMemo, type ComponentType } from 'react';
import { formatDecimal, label, reasons } from '../format';
import { useResource } from '../hooks/useResource';
import type { Envelope, Overview as Breadth, ResetHeatmap, ResetOverview, ResetSecurityIdentity, Securities, WorkbenchEnvelope } from '../types';
import type { ResearchState } from '../state';
import { Chart } from '../components/Chart';
import { Exclusions, Pager, Percent, ResourceStatus, SearchForm, SortHead, TableScroll, shouldHandleSpaClick, type Patch } from '../components/Common';
import { UnavailableModules } from '../features/workbench/PageStructure';
import { LazyLoadBoundary } from '../components/LazyLoadBoundary';
import { chartThemeColors } from '../features/preferences/settings';
import { reportIndexItems } from '../features/preferences/commands';
import { useThemeMode } from '../hooks/useThemeMode';
import { sortOverviewMembers, type MemberSort } from '../features/workbench/overview-members';
import '../features/workbench/overview.css';

const RealtimePanel = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(() => import('../features/realtime/RealtimePanel'))
  : null;
const SentimentPanel = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(() => import('../features/research/SentimentPanel'))
  : null;
const OverviewAITools = import.meta.env.VITE_PRIVATE_AI === '1' && import.meta.env.VITE_PUBLIC_SHARING !== '1'
  ? lazy(() => import('../features/ai/OverviewAITools')) : null;
export interface OverviewProps {
  state: ResearchState; patch: Patch; refreshToken: number; open: (id: string, sector: string | undefined, trigger: HTMLElement) => void;
  compare: (id: string) => void; ReviewActions?: ComponentType<{ snapshot: string; security: string }>; reset?: boolean;
  openResearch?: (kind: 'index' | 'topic', id: string, trigger: HTMLElement) => void;
  publicationAsOf?: string; publicationVersion?: string;
}
const memberDirections = ['up', 'down', 'flat'] as const;
type RawSentimentPoint = ResetOverview['sentiment'] & { trade_date: string };
function compareMember(left: ResetSecurityIdentity, right: ResetSecurityIdentity) {
  for (const [a, b] of [[left.security_id, right.security_id], [left.code ?? '', right.code ?? ''], [left.name ?? '', right.name ?? '']]) {
    if (a < b) return -1;
    if (a > b) return 1;
  }
  return 0;
}
export function OverviewContent({ state, patch, refreshToken, open, openResearch, compare, ReviewActions, reset = false, publicationAsOf, publicationVersion }: OverviewProps) {
  const breadthColors = chartThemeColors(useThemeMode() === 'dark');
  const base = `/api/v1/snapshots/${state.snapshot}`;
  const selector = new URLSearchParams({ snapshot_id: state.snapshot });
  if (state.date) selector.set('date', state.date);
  const resetOverview = useResource<WorkbenchEnvelope<ResetOverview>>(reset ? `/api/market/overview?${selector}` : null, state.snapshot, refreshToken);
  const resetHeatmap = useResource<WorkbenchEnvelope<ResetHeatmap>>(reset ? `/api/market/heatmap?${selector}&days=60` : null, state.snapshot, refreshToken);
  const resetSectors = useResource<WorkbenchEnvelope<{ sectors: ResetOverview['hot_sectors'] }>>(reset ? `/api/market/sector-flow?${selector}&top_n=10` : null, state.snapshot, refreshToken);
  const resetSentiment = useResource<WorkbenchEnvelope<{ series: RawSentimentPoint[] }>>(reset ? `/api/market/sentiment?${selector}&days=20` : null, state.snapshot, refreshToken);
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
      { value: counts.advancing, itemStyle: { color: breadthColors.up } },
      { value: counts.declining, itemStyle: { color: breadthColors.down } },
      { value: counts.unchanged, itemStyle: { color: breadthColors.muted } },
    ] : [] }],
  }), [counts, breadthColors]);
  const sentimentSeries = resetSentiment.data?.data.series;
  const sentimentOption = useMemo(() => ({
    grid: { left: 48, right: 56, top: 48, bottom: 40 },
    legend: { data: ['涨停家数', '跌停家数', '晋级率'] },
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    xAxis: { type: 'category', data: sentimentSeries?.map(point => point.trade_date) ?? [] },
    yAxis: [
      { type: 'value', name: '家', minInterval: 1 },
      { type: 'value', name: '%', axisLabel: { formatter: '{value}%' } },
    ],
    series: [
      { name: '涨停家数', type: 'line', connectNulls: false, data: sentimentSeries?.map(point => point.limit_up_count ?? null) ?? [], itemStyle: { color: breadthColors.up } },
      { name: '跌停家数', type: 'line', connectNulls: false, data: sentimentSeries?.map(point => point.limit_down_count ?? null) ?? [], itemStyle: { color: breadthColors.down } },
      { name: '晋级率', type: 'line', yAxisIndex: 1, connectNulls: false, lineStyle: { type: 'dashed' },
        data: sentimentSeries?.map(point => point.promote_rate.value == null ? null : point.promote_rate.value * 100) ?? [] },
    ],
  }), [sentimentSeries, breadthColors]);
  // reset 模式下把本页已加载的证券（涨跌成员全集）与题材（热门+板块流）上报给命令面板索引；不新增任何请求。
  useEffect(() => {
    if (!reset) return;
    const overview = resetOverview.data?.data;
    if (!overview) return;
    const securities = memberDirections
      .flatMap(group => overview.breadth.members[group])
      .map(member => ({ id: member.security_id, name: member.name?.trim() || '名称未知' }));
    const sectorFlow = resetSectors.data?.data.sectors ?? [];
    const sectors = [...overview.hot_sectors, ...sectorFlow]
      .map(sector => ({ id: sector.sector_id, name: sector.name }));
    reportIndexItems({ securities, sectors });
  }, [reset, resetOverview.data, resetSectors.data]);
  if (import.meta.env.VITE_RESET_WORKBENCH === '1' && reset) {
    const overview = resetOverview.data?.data;
    const ratio = (value: number | null) => value === null ? '不适用' : `${(value * 100).toFixed(2)}%`;
    const filterNames = { up: '上涨', down: '下跌', flat: '平盘' };
    const coverage = (value: { expected: number; valid: number; excluded: number; missing: number }) =>
      `覆盖 ${value.valid}/${value.expected} · 排除 ${value.excluded} · 缺失 ${value.missing}`;
    const direction = memberDirections.includes(state.market_filter as typeof memberDirections[number])
      ? state.market_filter as 'up' | 'down' | 'flat'
      : null;
    const sourceMembers = overview
      ? direction
        ? overview.breadth.members[direction]
        : memberDirections.flatMap(group => overview.breadth.members[group])
      : [];
    const uniqueMembers = [...sourceMembers].sort(compareMember).filter(
      (item, index, items) => index === 0 || item.security_id !== items[index - 1].security_id,
    );
    const query = state.q.trim().toLocaleLowerCase();
    const filteredMembers = query
      ? uniqueMembers.filter(item => [item.security_id, item.code ?? '', item.name ?? ''].some(value => value.toLocaleLowerCase().includes(query)))
      : uniqueMembers;
    const memberSort: MemberSort = state.sort === 'name' || state.sort === 'code' ? state.sort : 'security_id';
    const selectedMembers = sortOverviewMembers(filteredMembers, memberSort, state.order);
    const pageSize = [20, 50, 100].includes(state.page_size) ? state.page_size : 50;
    const pageMembers = selectedMembers.slice((state.page - 1) * pageSize, state.page * pageSize);
    const memberContext = `${direction ? `${filterNames[direction]}成员` : '全部证券'}${state.q ? ` · 搜索“${state.q}”` : ''} · 共 ${selectedMembers.length} 家`;
    const heatmap = resetHeatmap.data?.data;
    const heatmapDates = [...new Set(heatmap?.cells.map(cell => cell.trade_date) ?? [])].sort();
    const heatmapBoards = [...new Set([...(heatmap?.columns ?? []), ...(heatmap?.cells.map(cell => cell.boards) ?? [])])].sort((a, b) => a - b);
    const heatmapCells = new Map(heatmap?.cells.map(cell => [`${cell.trade_date}-${cell.boards}`, cell]));
    const heatmapMax = Math.max(1, ...(heatmap?.cells.map(cell => cell.count) ?? []));
    const realtimeSelected = state.compare.length > 0 || Boolean(state.security || state.sector);
    return <div className="overview-workbench">
      <div className="page-heading"><div><p className="eyebrow">发布事实 / 原始市场观测</p><h1>市场总览</h1></div><span className="muted">不含研究评分与阶段判断</span></div>
      <ResourceStatus resource={resetOverview} name="市场总览" />
        <section id="workbench-indexes" tabIndex={-1} aria-label="指数行情" className="overview-indexes">
          <h2>指数行情</h2>
          <div className="index-strip">
            {overview?.indexes.map(item => <button key={item.security_id} aria-label={`查看指数详情 · ${item.name}`}
              onClick={event => openResearch?.('index', item.security_id, event.currentTarget)}>
              <strong>{item.name}</strong><span>{item.last ?? '缺失'}</span><small>涨跌幅 {ratio(item.change_ratio)}</small>
              <small>成交额 {item.amount ?? '缺失'}{item.reason && ` · ${item.reason}`}</small>
            </button>)}
            {!overview?.indexes.length && <div><strong>指数数据缺失</strong><small>{resetOverview.loading ? '正在读取快照指数行情。' : '当前快照未提供可用指数行情，不补零、不推算。'}</small></div>}
          </div>
        </section>
      {!overview && <UnavailableModules view="overview"
        ids={['ecology', 'breadth', 'sentiment', 'topics', 'sector-flow', 'heatmap', 'daily-review', 'factors']}
        loading={resetOverview.loading} />}
      {overview && <>
        <section id="workbench-ecology" tabIndex={-1} aria-label="涨停生态" className="overview-ecology">
          <h2>涨停生态</h2>
          <dl>
            <div><dt>涨停 / 跌停</dt><dd>{overview.sentiment.limit_up_count} / {overview.sentiment.limit_down_count} 家</dd></div>
            <div><dt>最高板</dt><dd>{overview.sentiment.max_boards} 板</dd></div>
            <div><dt>封板率</dt><dd>{ratio(overview.breadth.seal_rate.value)}
              <small>{overview.breadth.seal_rate.numerator}/{overview.breadth.seal_rate.denominator} · {coverage(overview.breadth.seal_rate.coverage)}</small></dd></div>
            <div><dt>晋级率</dt><dd>{ratio(overview.sentiment.promote_rate.value)}
              <small>{overview.sentiment.promote_rate.numerator}/{overview.sentiment.promote_rate.denominator}{overview.sentiment.promote_rate.reason && ` · ${overview.sentiment.promote_rate.reason}`}</small></dd></div>
          </dl>
        </section>
        <section aria-label="涨跌与情绪" className="overview-summary">
          <section id="workbench-breadth" tabIndex={-1} aria-label="涨跌分布">
            <h2>涨跌分布</h2>
            <div className="metric-actions">
              <button aria-pressed={state.market_filter === 'up'} onClick={() => patch({ market_filter: 'up', page: 1 })}>上涨 {overview.breadth.up}</button>
              <button aria-pressed={state.market_filter === 'down'} onClick={() => patch({ market_filter: 'down', page: 1 })}>下跌 {overview.breadth.down}</button>
              <button aria-pressed={state.market_filter === 'flat'} onClick={() => patch({ market_filter: 'flat', page: 1 })}>平盘 {overview.breadth.flat}</button>
            </div>
            <p className="muted">有效 {overview.breadth.coverage.valid} / 应有 {overview.breadth.coverage.expected} · 排除 {overview.breadth.coverage.excluded} · 缺失 {overview.breadth.coverage.missing}</p>
            <p>封板率 {overview.breadth.seal_rate.numerator}/{overview.breadth.seal_rate.denominator} · {ratio(overview.breadth.seal_rate.value)}</p>
            <small>{coverage(overview.breadth.seal_rate.coverage)}{overview.breadth.seal_rate.reason && ` · ${overview.breadth.seal_rate.reason}`}</small>
            <a href="#overview-securities">查看证券筛选结果</a>
          </section>
          <section id="workbench-sentiment" tabIndex={-1} aria-label="情绪原始观测">
            <h2>情绪原始观测</h2>
            <dl className="market-observations">
              <div><dt>涨停 / 跌停</dt><dd>{overview.sentiment.limit_up_count} / {overview.sentiment.limit_down_count}</dd></div>
              <div><dt>最高板</dt><dd>{overview.sentiment.max_boards}</dd></div>
              <div><dt>晋级率</dt><dd>{overview.sentiment.promote_rate.numerator}/{overview.sentiment.promote_rate.denominator} · {ratio(overview.sentiment.promote_rate.value)}
                <small>覆盖 {overview.sentiment.promote_rate.denominator}/{overview.sentiment.promote_rate.yesterday_ge2} · 排除 {overview.sentiment.promote_rate.excluded} · 缺失 {overview.sentiment.promote_rate.missing}{overview.sentiment.promote_rate.reason && ` · ${overview.sentiment.promote_rate.reason}`}</small>
              </dd></div>
              <div><dt>昨涨停今收益中位数</dt><dd>{ratio(overview.sentiment.yesterday_limit_up_median_return)}
                <small>{coverage(overview.sentiment.yesterday_limit_up_return_coverage)}{overview.sentiment.yesterday_limit_up_return_reason && ` · ${overview.sentiment.yesterday_limit_up_return_reason}`}</small>
              </dd></div>
            </dl>
          </section>
          <section id="workbench-topics" tabIndex={-1} aria-label="题材热度 TOP">
            <div className="section-heading"><h2>题材热度 TOP</h2><button disabled={!overview.historical_topic_analysis.enabled}>历史题材分析</button></div>
            {!overview.historical_topic_analysis.enabled && <p className="muted">缺少历史题材成员覆盖，历史分析不可用。{overview.historical_topic_analysis.reason && ` ${overview.historical_topic_analysis.reason}`}</p>}
            <ol className="overview-topic-top">{overview.hot_sectors.slice(0, 5).map(item => <li key={item.sector_id}>
              <a href={`?view=leaders&snapshot=${encodeURIComponent(state.snapshot)}&date=${encodeURIComponent(state.date)}&sector=${encodeURIComponent(item.sector_id)}`}
                onClick={event => { if (shouldHandleSpaClick(event)) { event.preventDefault(); openResearch?.('topic', item.sector_id, event.currentTarget); } }}>
                <strong>{item.name}</strong><small>成员 {item.member_count} · 中位收益 {ratio(item.median_return)}</small>
                <small>涨停占比 {item.limit_up_ratio.numerator}/{item.limit_up_ratio.denominator} · {ratio(item.limit_up_ratio.value)}</small>
                <small>{coverage(item.coverage)}{item.limit_up_ratio.reason && ` · ${item.limit_up_ratio.reason}`}</small>
                <small>净流入：{item.net_inflow === null ? '不适用 · 数据缺失' : formatDecimal(String(item.net_inflow))}{item.net_inflow_reason && ` · ${item.net_inflow_reason}`}{item.quality_flags.includes('sample_insufficient') ? ' · 样本不足' : ''}</small>
              </a>
            </li>)}</ol>
            {overview.hot_sectors.length === 0 && <p className="muted">当前快照无可用题材排行，不生成题材热度。</p>}
          </section>
        </section>
        <section aria-label="原始情绪曲线" className="overview-section">
          <h2>原始情绪曲线</h2>
          <ResourceStatus resource={resetSentiment} name="情绪序列" />
          {sentimentSeries && sentimentSeries.length > 0 ? <>
            <Chart option={sentimentOption} description="原始情绪曲线：按交易日展示涨停、跌停家数和晋级率；下方表格提供等价数据。缺失值不连线，不含研究评分。" />
            <TableScroll label="原始情绪序列横向滚动">
              <table aria-label="原始情绪序列"><thead><tr><th>交易日</th><th>涨停家数</th><th>跌停家数</th><th>最高板</th><th>晋级率</th><th>昨涨停今收益中位数</th></tr></thead>
                <tbody>{sentimentSeries.map(point => <tr key={point.trade_date}>
                  <th>{point.trade_date}</th><td>{point.limit_up_count ?? '缺失'}</td><td>{point.limit_down_count ?? '缺失'}</td><td>{point.max_boards ?? '缺失'}</td>
                  <td>{ratio(point.promote_rate.value)}<small>{point.promote_rate.numerator}/{point.promote_rate.denominator} · 覆盖 {point.promote_rate.denominator}/{point.promote_rate.yesterday_ge2} · 排除 {point.promote_rate.excluded} · 缺失 {point.promote_rate.missing}{point.promote_rate.reason && ` · ${point.promote_rate.reason}`}</small></td>
                  <td>{ratio(point.yesterday_limit_up_median_return)}<small>{coverage(point.yesterday_limit_up_return_coverage)}{point.yesterday_limit_up_return_reason && ` · ${point.yesterday_limit_up_return_reason}`}</small></td>
                </tr>)}</tbody>
              </table>
            </TableScroll>
          </> : <p className="muted">当前快照未提供可用情绪序列，不以当日数据拼接历史曲线。</p>}
        </section>
        <section id="workbench-sector-flow" tabIndex={-1} aria-label="板块资金流" className="overview-section">
          <h2>板块资金流</h2>
          <ResourceStatus resource={resetSectors} name="题材统计" />
          <p className="muted">使用板块流接口的快照统计；净流入缺失不等于零，不以涨幅替代资金流。</p>
          <div className="sector-list">{resetSectors.data?.data.sectors.map(item =>
            <button key={item.sector_id} onClick={event => openResearch?.('topic', item.sector_id, event.currentTarget)}>
              <strong>{item.name}</strong><span>成员 {item.member_count} · 中位收益 {ratio(item.median_return)}</span>
              <span>上涨占比 {item.up_ratio.numerator}/{item.up_ratio.denominator} · {ratio(item.up_ratio.value)}</span>
              <span>涨停占比 {item.limit_up_ratio.numerator}/{item.limit_up_ratio.denominator} · {ratio(item.limit_up_ratio.value)}</span>
              <small>{coverage(item.coverage)}{item.up_ratio.reason && ` · ${item.up_ratio.reason}`}{item.limit_up_ratio.reason && ` · ${item.limit_up_ratio.reason}`}</small>
              <small>净流入：{item.net_inflow === null ? '不适用 · 数据缺失' : formatDecimal(String(item.net_inflow))}{item.net_inflow_reason && ` · ${item.net_inflow_reason}`}{item.quality_flags.includes('sample_insufficient') ? ' · 样本不足' : ''}</small>
            </button>)}</div>
          {resetSectors.data?.data.sectors.length === 0 && <p className="muted">当前快照未提供可用板块流数据。</p>}
        </section>
        <section id="workbench-heatmap" tabIndex={-1} aria-labelledby="heatmap-title" className="overview-section">
          <h2 id="heatmap-title">涨停情绪热力图</h2>
          <ResourceStatus resource={resetHeatmap} name="热力图" />
          <p className="muted">交易日 × 连板高度 · 单位：家。缺失组合不补零；点击已有单元格查看当日梯队。</p>
          {heatmapDates.length > 0 && heatmapBoards.length > 0 ? <TableScroll label="涨停情绪热力图横向滚动">
            <table className="overview-heatmap" aria-label="涨停情绪热力图矩阵">
              <thead><tr><th>交易日 / 连板高度</th>{heatmapBoards.map(boards => <th key={boards}>{boards}板</th>)}</tr></thead>
              <tbody>{heatmapDates.map(date => <tr key={date}><th>{date}</th>{heatmapBoards.map(boards => {
                const cell = heatmapCells.get(`${date}-${boards}`);
                return <td key={boards}>{cell ? <button
                  style={{ backgroundColor: `color-mix(in srgb, var(--accent) ${Math.round(cell.count / heatmapMax * 28)}%, var(--bg-surface))` }}
                  aria-label={`${cell.trade_date} ${cell.boards}板 ${cell.count}家`}
                  onClick={() => patch({ view: 'leaders', date: cell.trade_date, snapshot: '', sector: '' })}>{cell.count}</button>
                  : <span className="muted">缺失</span>}</td>;
              })}</tr>)}</tbody>
            </table>
          </TableScroll> : <p className="muted">当前快照未提供可用日期与连板热力数据。</p>}
        </section>
        <section id="workbench-daily-review" tabIndex={-1} aria-label="AI 每日速览" className="overview-section overview-daily-review">
          <div className="section-heading"><h2>AI 每日速览</h2><strong className="status-token">快照事实模板</strong></div>
          <p>截至 {resetOverview.data?.as_of}，快照记录上涨 {overview.breadth.up} 家、下跌 {overview.breadth.down} 家、平盘 {overview.breadth.flat} 家；
            涨停 {overview.sentiment.limit_up_count} 家、跌停 {overview.sentiment.limit_down_count} 家，最高 {overview.sentiment.max_boards} 板。</p>
          <p className="muted">{coverage(overview.breadth.coverage)}。热门题材 {overview.hot_sectors.length > 0 ? overview.hot_sectors.slice(0, 3).map(item => item.name).join('、') : '数据缺失'}。</p>
          <p className="muted">AI 速览不可用：当前发布未归档已审核的 AI 每日速览。以上为确定性快照事实模板，非 AI 生成，不含阶段判断、投资建议或预测。</p>
          {OverviewAITools && publicationAsOf && publicationVersion && state.date &&
            <LazyLoadBoundary><Suspense fallback={<p role="status">正在加载每日 AI 操作…</p>}>
              <OverviewAITools key={`${state.snapshot}:${state.date}`} snapshotId={state.snapshot} asOf={publicationAsOf}
                version={publicationVersion} date={state.date}
                marketContext={`交易日 ${state.date} · 发布快照 ${state.snapshot} · 截至 ${resetOverview.data?.as_of}\n上涨 ${overview.breadth.up} 家、下跌 ${overview.breadth.down} 家、平盘 ${overview.breadth.flat} 家；涨停 ${overview.sentiment.limit_up_count} 家、跌停 ${overview.sentiment.limit_down_count} 家，最高 ${overview.sentiment.max_boards} 板。\n${coverage(overview.breadth.coverage)}。热门题材：${overview.hot_sectors.slice(0, 3).map(item => item.name).join('、') || '数据缺失'}。\n来源 ${resetOverview.data?.source} / ${resetOverview.data?.source_version}。`} />
            </Suspense></LazyLoadBoundary>}
        </section>
        <section id="overview-securities" aria-label="证券行情" className="overview-section">
          <h2>证券行情</h2>
          <SearchForm value={state.q} onSearch={q => patch({ q, page: 1 })} />
          <div className="toolbar">
            <button type="button" aria-pressed={!state.market_filter} onClick={() => patch({ market_filter: '', page: 1 })}>全部证券</button>
            <label>身份排序<select aria-label="身份排序" value={memberSort}
              onChange={event => patch({ sort: event.target.value as MemberSort, page: 1 })}>
              <option value="security_id">证券标识</option><option value="code">证券代码</option><option value="name">证券名称</option>
            </select></label>
            <label>排序方向<select value={state.order} onChange={event => patch({ order: event.target.value as 'asc' | 'desc', page: 1 })}>
              <option value="asc">升序</option><option value="desc">降序</option>
            </select></label>
            <button type="button" disabled={!pageMembers.length} onClick={() => {
              void import('../export/overview').then(module => module.downloadOverviewCsv(
                resetOverview.data!, state.market_filter, state.q, direction, pageMembers,
              ));
            }}>导出当前页 CSV</button>
          </div>
          <p className="muted">导出范围：当前筛选的当前页，仅 {pageMembers.length} 条，共 {selectedMembers.length} 条；保留原发布身份与排序。</p>
          <div className="filter-context" role="region" aria-label="证券筛选结果">
            <strong>{memberContext}</strong>
            <Pager page={state.page} size={pageSize} total={selectedMembers.length} change={(page, page_size) => patch({ page, page_size })} />
            <ul className="filter-members">{pageMembers.map(item =>
              <li key={item.security_id}>
                <button className="security-button" onClick={event => open(item.security_id, undefined, event.currentTarget)}>{item.name?.trim() || '名称未知'}</button>
                <small>{item.code?.trim() || '代码未知'} · {item.security_id}</small>
              </li>)}
            </ul>
            {selectedMembers.length === 0 && <p className="empty">没有匹配证券，请清除或修改搜索条件。</p>}
          </div>
        </section>
        <section id="workbench-factors" tabIndex={-1} aria-labelledby="factor-title" className="overview-section">
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
                <div><dt>{item.name} C/MA20-1</dt><dd>{ratio(item.close_over_ma20)}<small>窗口 20 日 · {coverage(item.coverage.ma20)}{item.close_over_ma20_reason && ` · ${item.close_over_ma20_reason}`}</small></dd></div>
                <div><dt>C/MA60-1</dt><dd>{ratio(item.close_over_ma60)}<small>窗口 60 日 · {coverage(item.coverage.ma60)}{item.close_over_ma60_reason && ` · ${item.close_over_ma60_reason}`}</small></dd></div>
                <div><dt>20 日收益波动</dt><dd>{ratio(item.volatility_20)}<small>20 个相邻收益，非年化样本标准差 · {coverage(item.coverage.return20)}{item.volatility_20_reason && ` · ${item.volatility_20_reason}`}</small></dd></div>
              </dl>)}
            </div>
          </div>
        </section>
        <details className="evidence"><summary>来源与方法</summary>
          <p>来源 {resetOverview.data?.source} / {resetOverview.data?.source_version} · 截至 {resetOverview.data?.as_of} · 比率单位为小数，页面按百分数展示。</p>
        </details>
      </>}
      <section id="workbench-realtime" tabIndex={-1} aria-label="实时看盘" className="overview-section">
        <h2>实时看盘</h2>
        <p className="muted">选择快照中的证券加入比较选择，或选择题材，查看已有实时证据。实时读取沿用原有授权与数据校验。</p>
        <div className="toolbar">
          <label>证券<select aria-label="实时看盘证券选择" value="" onChange={event => { if (event.target.value) compare(event.target.value); }}>
            <option value="">选择证券加入或移出比较</option>
            {uniqueMembers.map(item => <option key={item.security_id} value={item.security_id}>{item.name?.trim() || '名称未知'}{state.compare.includes(item.security_id) ? '（已选择）' : ''}</option>)}
          </select></label>
          <label>题材<select aria-label="实时看盘题材选择" value={state.sector} onChange={event => patch({ sector: event.target.value })}>
            <option value="">未选择题材</option>
            {[...new Map([...(overview?.hot_sectors ?? []), ...(resetSectors.data?.data.sectors ?? [])].map(item => [item.sector_id, item])).values()].map(item => <option key={item.sector_id} value={item.sector_id}>{item.name}</option>)}
          </select></label>
        </div>
        {!realtimeSelected && <p className="muted">尚未选择证券或题材，请先选择；未选择时不请求实时数据。</p>}
        {!state.date && <p className="muted">请先选择交易日，实时证据需要日期上下文。</p>}
        {import.meta.env.VITE_RESET_WORKBENCH !== '1' && <p className="muted">实时模块未启用，无法读取实时证据。</p>}
        {import.meta.env.VITE_RESET_WORKBENCH === '1' && RealtimePanel && state.date && realtimeSelected &&
        <LazyLoadBoundary>
          <Suspense fallback={<p role="status" className="status">正在加载实时证据账本…</p>}>
            <RealtimePanel
              date={state.date}
              securityIds={[...state.compare, ...(state.security ? [state.security] : [])]}
              sectorId={state.sector || undefined}
              refreshToken={refreshToken}
            />
          </Suspense>
        </LazyLoadBoundary>}
      </section>
      <section id="workbench-sentiment-research" tabIndex={-1} aria-label="情绪研究" className="overview-section">
      {import.meta.env.VITE_RESET_WORKBENCH === '1' && SentimentPanel && state.date ?
        <LazyLoadBoundary>
          <Suspense fallback={<p role="status" className="status">正在加载情绪研究分区…</p>}>
            <SentimentPanel snapshot={state.snapshot} date={state.date} refreshToken={refreshToken} />
          </Suspense>
        </LazyLoadBoundary> : <p className="muted">情绪研究需要交易日上下文与已启用的研究模块。</p>}
      </section>
    </div>;
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
              if (trigger) { trigger.focus(); open(row.security_id, undefined, trigger); }
            }}>
              <th className="identity"><button className="security-button" onClick={e => open(row.security_id, undefined, e.currentTarget)}>{row.name || '名称未知'}</button><span className="security-id">{row.security_id}</span>
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
