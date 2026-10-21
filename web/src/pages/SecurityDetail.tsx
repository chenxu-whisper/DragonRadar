import { lazy, Suspense, useEffect, useMemo, useRef, type ComponentType } from 'react';
import { ArrowLeft, ExternalLink, X } from 'lucide-react';
import { formatDecimal, label, reasons } from '../format';
import { useResource } from '../hooks/useResource';
import { stateSearch, type ResearchState } from '../state';
import { klineRange } from '../features/analysis/query';
import type { Detail, Envelope, ResetKlineData, ResetStock, Snapshot, WorkbenchEnvelope } from '../types';
import { Chart } from '../components/Chart';
import { Percent, ResourceStatus, shouldHandleSpaClick, TableScroll, type Patch } from '../components/Common';
import { chartThemeColors } from '../features/preferences/settings';
import { useThemeMode } from '../hooks/useThemeMode';
import { evidenceStatusLabel, reasonLabel, roleLabel } from '../features/roles/labels';
import { newsListPath } from '../features/news/api';
import type { NewsListResponse } from '../features/news/types';
import { CsvExportButton } from '../export/CsvExportButton';

type ReviewActionComponent = ComponentType<{ snapshot: string; security: string }>;
const resetEnabled = import.meta.env.VITE_RESET_WORKBENCH === '1';
const EvidencePanel = resetEnabled
  ? lazy(async () => ({ default: (await import('../features/roles/EvidencePanel')).EvidencePanel }))
  : null;
const SeatsPanel = resetEnabled
  ? lazy(async () => ({ default: (await import('../features/lhb/SeatsPanel')).SeatsPanel }))
  : null;
const SecurityAITools = import.meta.env.VITE_PRIVATE_AI === '1'
  ? lazy(() => import('../features/ai/SecurityAITools'))
  : null;
const displayNumber = (value: number | null) =>
  value === null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value);
const yesNo = (value: boolean | null) => value === null ? '—' : value ? '是' : '否';

function DetailContent({ state, snapshot, id, evidenceSector, refreshToken, ReviewActions, patch }: {
  state: ResearchState; snapshot?: Snapshot; id: string; evidenceSector?: string; refreshToken: number; ReviewActions?: ReviewActionComponent; patch: Patch;
}) {
  const klineColors = chartThemeColors(useThemeMode() === 'dark');
  const modern = useResource<WorkbenchEnvelope<ResetStock | null>>(resetEnabled
    ? `/api/stock/${encodeURIComponent(id)}?${new URLSearchParams({ date: state.date, snapshot_id: state.snapshot })}`
    : null, state.snapshot, refreshToken);
  const modernData = modern.data?.data;
  const relatedNewsEnabled = resetEnabled && Boolean(modernData) && !modern.loading && !modern.error;
  const relatedNews = useResource<NewsListResponse>(relatedNewsEnabled
    ? newsListPath({
      date: state.date,
      snapshot: state.snapshot,
      type: '',
      source: '',
      q: '',
      securityId: id,
      page: 1,
      size: 20,
    })
    : null, state.snapshot);
  let rawQuery = '';
  try {
    if (resetEnabled && snapshot && modernData) {
      const range = klineRange(state, snapshot.result.calendar_sessions);
      rawQuery = new URLSearchParams({
        snapshot_id: state.snapshot,
        start: range.start,
        end: range.end,
        adj: 'raw',
      }).toString();
    }
  } catch { /* The analysis form owns invalid custom-window feedback. */ }
  const rawKline = useResource<WorkbenchEnvelope<ResetKlineData | null>>(resetEnabled && rawQuery
    ? `/api/stock/${encodeURIComponent(id)}/kline?${rawQuery}`
    : null, state.snapshot, refreshToken);
  const resource = useResource<Envelope<Detail>>(!resetEnabled
    ? `/api/v1/snapshots/${state.snapshot}/securities/${encodeURIComponent(id)}`
    : null, state.snapshot, refreshToken);
  const data = resource.data?.result;
  const rawData = rawKline.data?.data;
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
      itemStyle: { color: klineColors.up, color0: klineColors.down, borderColor: klineColors.up, borderColor0: klineColors.down },
    }] : [{
      type: 'line', name: '未复权收盘价', connectNulls: false, showSymbol: false,
      data: data?.history.map(point => point.close === null ? null : Number(point.close)) || [],
      lineStyle: { color: klineColors.accent, width: 2 },
    }],
  }), [data, klineColors]);
  const canDrawCandles = rawData?.chart_type === 'candlestick' && rawData.points.length > 0 &&
    rawData.points.every(point => [point.open, point.high, point.low, point.close]
      .every(value => typeof value === 'number' && Number.isFinite(value)));
  const rawOption = useMemo(() => ({
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    grid: { left: 52, right: 16, top: 28, bottom: 70 },
    xAxis: { type: 'category', data: rawData?.points.map(point => point.trade_date) || [] },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: canDrawCandles ? [{
      type: 'candlestick',
      name: '未复权价格',
      data: rawData!.points.map(point => [point.open, point.close, point.low, point.high]),
      itemStyle: { color: klineColors.up, color0: klineColors.down, borderColor: klineColors.up, borderColor0: klineColors.down },
    }] : [{
      type: 'line',
      name: '未复权收盘价',
      connectNulls: false,
      showSymbol: false,
      data: rawData?.points.map(point => point.close) || [],
      lineStyle: { color: klineColors.accent, width: 2 },
    }],
  }), [canDrawCandles, rawData, klineColors]);
  return <>
    {resetEnabled
      ? <>
        <ResourceStatus resource={modern} name="证券详情" />
        <ResourceStatus resource={rawKline} name="证券行情" />
        {modern.data?.data === null && <p className="notice" role="status">当前快照的证券详情不可用</p>}
      </>
      : <ResourceStatus resource={resource} name="证券详情" />}
    {modernData && <>
      <div className="detail-heading"><h2>{modernData.name || '名称未知'}</h2><span>{modernData.security_id}</span></div>
      {ReviewActions && <ReviewActions snapshot={state.snapshot} security={id} />}
      {SecurityAITools && snapshot && <Suspense fallback={<p role="status">正在加载 AI 个股解释…</p>}>
        <SecurityAITools
          snapshotId={state.snapshot}
          asOf={snapshot.result.published_at}
          version={`v${snapshot.result.revision}`}
          date={state.date}
          securityId={id}
          securityName={modernData.name}
        />
      </Suspense>}
      <dl className="quote">
        <div><dt>最新价</dt><dd>{modernData.quote.last ?? '—'}</dd></div>
        <div><dt>日涨跌幅</dt><dd><Percent value={modernData.quote.change_ratio === null ? null : String(modernData.quote.change_ratio)} /></dd></div>
        <div><dt>成交额</dt><dd>{displayNumber(modernData.quote.amount)}</dd></div>
        <div><dt>正式监管事件</dt><dd>{modernData.regulation.formal_event_count === null ? '未知' : modernData.regulation.formal_event_count}</dd></div>
      </dl>
      <p className="detail-reasons">正式监管事件：{modernData.regulation.formal_event_count === null ? '未知' : modernData.regulation.formal_event_count}</p>
      <p className="detail-reasons">事件来源完整性：{modernData.regulation.event_source_completeness === 'complete' ? '完整' : '未知'}
        {modernData.regulation.reason ? ` · ${reasonLabel(modernData.regulation.reason)}` : ''}</p>
      <p className="detail-reasons">收盘涨停 {yesNo(modernData.seal.is_limit_up)} · 盘中触及 {yesNo(modernData.seal.touched_limit)}</p>
      <p className="detail-reasons">首封 {modernData.seal.first_seal_time || '—'} · 末封 {modernData.seal.last_seal_time || '—'} · 开板 {modernData.seal.break_count ?? '—'} · 封单 {displayNumber(modernData.seal.seal_amount)} {modernData.seal.seal_amount_unit ? label(modernData.seal.seal_amount_unit) : '—'} · 连板 {modernData.seal.continuous_boards ?? '—'}
        {modernData.seal.reason ? ` · ${reasonLabel(modernData.seal.reason)}` : ''}</p>
      <p className="detail-reasons">{modernData.roles.length
        ? modernData.roles.map(role => `${roleLabel(role.role_type)}：${evidenceStatusLabel(role.evidence_status)}`).join('；')
        : '暂无角色证据'}</p>
      {modernData.role_reason && <p className="detail-reasons">角色状态：{reasonLabel(modernData.role_reason)}</p>}
      <CsvExportButton
        scope="stock-current-filter"
        label="导出当前筛选 CSV"
        envelope={rawKline.data || modern.data!}
        filters={{
          security_id: id,
          start: rawData?.adjusted_start ?? null,
          end: rawData?.adjusted_end ?? null,
          adjustment: rawData?.adjustment ?? null,
        }}
        columns={[
          { key: 'record_type', header: 'record_type' },
          { key: 'security_id', header: 'security_id' },
          { key: 'row_trade_date', header: 'row_trade_date' },
          { key: 'last', header: 'last' },
          { key: 'change_ratio', header: 'change_ratio' },
          { key: 'open', header: 'open' },
          { key: 'high', header: 'high' },
          { key: 'low', header: 'low' },
          { key: 'close', header: 'close' },
          { key: 'volume', header: 'volume' },
          { key: 'amount', header: 'amount' },
          { key: 'roles', header: 'roles' },
          { key: 'formal_event_count', header: 'formal_event_count' },
        ]}
        rows={[
          {
            snapshot_id: modern.data!.snapshot_id,
            record_type: 'quote',
            security_id: modernData.security_id,
            row_trade_date: modern.data!.trade_date,
            last: modernData.quote.last,
            change_ratio: modernData.quote.change_ratio,
            open: null,
            high: null,
            low: null,
            close: null,
            volume: null,
            amount: modernData.quote.amount,
            roles: modernData.roles.map(role => role.role_type).join('|'),
            formal_event_count: modernData.regulation.formal_event_count,
          },
          ...(rawData?.points || []).map(point => ({
            snapshot_id: rawKline.data!.snapshot_id,
            record_type: 'kline',
            security_id: rawData!.security_id,
            row_trade_date: point.trade_date,
            last: null,
            change_ratio: null,
            ...point,
            roles: '',
            formal_event_count: null,
          })),
        ]}
      />
      {rawData && <>
        <p className="muted">未复权价格 · 实际区间 {rawData.adjusted_start} 至 {rawData.adjusted_end} · 缺失处不连线</p>
        <Chart
          option={rawOption}
          description={`${modernData.security_id} ${canDrawCandles ? '历史日线图' : '历史收盘价线图'}，未复权价格，缺失处断开。`}
        />
        <details className="evidence"><summary>查看完整行情数据（{rawData.points.length} 个交易日）</summary>
          <TableScroll label="完整行情数据">
            <table aria-label="完整行情数据" className="responsive-record-table"><thead><tr>
              <th>交易日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>成交额</th>
            </tr></thead><tbody>{rawData.points.map(point => <tr key={point.trade_date}>
              <th data-label="交易日期">{point.trade_date}</th>
              <td data-label="开盘">{displayNumber(point.open)}</td><td data-label="最高">{displayNumber(point.high)}</td>
              <td data-label="最低">{displayNumber(point.low)}</td><td data-label="收盘">{displayNumber(point.close)}</td>
              <td data-label="成交量">{displayNumber(point.volume)}</td><td data-label="成交额">{displayNumber(point.amount)}</td>
            </tr>)}</tbody></table>
          </TableScroll>
        </details>
      </>}
      <details className="evidence"><summary>证券原始证据</summary>
        <pre>{JSON.stringify({ roles: modernData.roles, role_reason: modernData.role_reason, seal: modernData.seal, regulation: modernData.regulation }, null, 2)}</pre>
      </details>
    </>}
    {data && <>
      <div className="detail-heading"><h2>{data.security.name || '名称未知'}</h2><span>{data.security.security_id} · {label(data.security.exchange)} · {label(data.security.board)}</span></div>
      {ReviewActions && <ReviewActions snapshot={state.snapshot} security={id} />}
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
    {EvidencePanel && evidenceSector && <Suspense fallback={<p role="status" className="status">正在加载角色证据…</p>}>
      <EvidencePanel
        snapshot={state.snapshot}
        date={state.date}
        sector={evidenceSector}
        security={id}
        refreshToken={refreshToken}
      />
    </Suspense>}
    {SeatsPanel && snapshot && <Suspense fallback={<p role="status" className="status">正在加载龙虎榜席位证据…</p>}>
      <SeatsPanel
        date={state.date}
        securityId={id}
        asOf={snapshot.result.published_at}
        refreshToken={refreshToken}
      />
    </Suspense>}
    {relatedNewsEnabled && <section className="related-news" aria-label="关联消息">
      <h3>关联消息</h3>
      <ResourceStatus resource={relatedNews} name="关联消息" />
      {relatedNews.data?.data?.articles.length
        ? <ul>{relatedNews.data.data.articles.map(article => <li key={article.article_id}>
          <a
            href={stateSearch({ ...state, view: 'news', article: article.article_id, security: '' }, false, true)}
            onClick={event => {
              if (shouldHandleSpaClick(event)) {
                event.preventDefault();
                patch({ view: 'news', article: article.article_id, security: '' });
              }
            }}
          >{article.title}</a>
        </li>)}</ul>
        : !relatedNews.loading && !relatedNews.error && <p className="muted">当前快照没有关联消息。</p>}
    </section>}
  </>;
}
export interface SecurityDrawerProps {
  state: ResearchState; id: string; sector?: string; trigger: HTMLElement; close: () => void; patch: Patch; refreshToken: number;
  snapshot?: Snapshot; allowPrivate?: boolean; ReviewActions?: ReviewActionComponent;
}
export function SecurityDrawer({ state, snapshot, id, sector, trigger, close, patch, refreshToken, allowPrivate = false, ReviewActions }: SecurityDrawerProps) {
  const dialog = useRef<HTMLDialogElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    dialog.current?.showModal(); closeButton.current?.focus();
    return () => { dialog.current?.close(); if (trigger.isConnected) trigger.focus(); };
  }, [trigger]);
  const next = {
    ...state,
    view: 'stock' as const,
    security: id,
    return_view: state.view === 'leaders' ? 'leaders' as const : state.view === 'analysis' ? 'analysis' as const : state.view === 'news' ? 'news' as const : 'overview' as const,
  };
  return <dialog ref={dialog} className="security-drawer" aria-labelledby="drawer-title" onCancel={e => { e.preventDefault(); close(); }}>
    <div className="drawer-toolbar"><h2 id="drawer-title">证券详情 · 只读</h2><button ref={closeButton} onClick={close} aria-label="关闭详情" title="关闭详情（退出键）"><X size={20} /></button></div>
    <a className="fullpage-link" href={stateSearch(next, allowPrivate, resetEnabled)} onClick={e => { if (shouldHandleSpaClick(e)) { e.preventDefault(); if (patch(next)) close(); } }}><ExternalLink size={16} />打开完整详情</a>
    <DetailContent state={state} snapshot={snapshot} id={id} evidenceSector={sector} refreshToken={refreshToken} ReviewActions={ReviewActions} patch={patch} />
  </dialog>;
}
export interface SecurityPageProps {
  state: ResearchState; snapshot?: Snapshot; patch: Patch; refreshToken: number; allowPrivate?: boolean; ReviewActions?: ReviewActionComponent;
}
export function SecurityPage({ state, snapshot, patch, refreshToken, allowPrivate = false, ReviewActions }: SecurityPageProps) {
  const back = { ...state, view: state.return_view, security: '' };
  return <>
    <a className="back-link" href={stateSearch(back, allowPrivate, resetEnabled)} onClick={event => { if (shouldHandleSpaClick(event)) { event.preventDefault(); patch(back); } }}><ArrowLeft size={18} />返回研究列表</a>
    <h1>证券详情</h1>
    {state.security ? <DetailContent state={state} snapshot={snapshot} id={state.security} evidenceSector={state.sector || undefined} refreshToken={refreshToken} ReviewActions={ReviewActions} patch={patch} /> : <p className="empty">未指定证券，请返回研究列表选择。</p>}
  </>;
}
