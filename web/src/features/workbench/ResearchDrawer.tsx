import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import Decimal from 'decimal.js';
import { X } from 'lucide-react';
import { useAccess, useResource, type Resource } from '../../hooks/useResource';
import { useSession } from '../review/SessionContext';
import type { ResearchState } from '../../state';
import type { ResetOverview, ResetSnapshotData, ResetTopicDetail, WorkbenchEnvelope } from '../../types';
import { Pager, Percent, ResourceStatus, TableScroll, type Patch } from '../../components/Common';
import { ApiError } from '../../api';
import { validateIndexOverview } from './topic-validation';
import { fetchIndexHistory, indexHistorySegments, indexHistoryWindow, type HistoryWindow,
  type IndexHistory, type IndexHistoryResponse, type IndexSelection } from './index-history';

export interface ResearchDrawerProps {
  kind: 'index' | 'topic'; id: string; trigger: HTMLElement; close: () => void;
  state: ResearchState; refreshToken: number; patch: Patch;
  page: number; size: number; changePage: (page: number, size: number) => void; focusMember?: string;
  openMember: (id: string) => void;
}
const ratio = (value: number | null, signed = true) => <Percent value={value === null ? null : String(value)} signed={signed} />;
const covered = (value: { expected: number; valid: number; missing: number; excluded: number }) =>
  `覆盖 ${value.valid}/${value.expected} · 排除 ${value.excluded} · 缺失 ${value.missing}`;

function useIndexHistory(selection: IndexSelection | null, sessions: readonly string[], window: HistoryWindow, token: number): Resource<IndexHistoryResponse> {
  const { denied, epoch, revoke } = useAccess();
  const { enabled, store } = useSession();
  const locked = enabled && store.locked;
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: IndexHistoryResponse; error?: ApiError }>({ key: '' });
  const gate = useRef(0);
  const key = selection && !denied && !locked
    ? JSON.stringify([selection, sessions, window, token, retry, epoch, enabled ? store.epoch : 0]) : '';
  useEffect(() => {
    const generation = ++gate.current, controller = new AbortController();
    if (!key || !selection) return;
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      return fetchIndexHistory(selection, sessions, controller.signal);
    }).then(data => {
      if (gate.current === generation) setResult({ key, data });
    }).catch((error: unknown) => {
      if (gate.current !== generation || (error instanceof DOMException && error.name === 'AbortError')) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      if (enabled && failure.status === 401) {
        windowEvent('private-session-lock'); setResult({ key: '' }); return;
      }
      if (failure.status === 403 && (!enabled || failure.code === 'source_forbidden' || !failure.code)) {
        revoke(); if (enabled) windowEvent('private-source-revoked');
        setResult({ key: '' }); return;
      }
      setResult({ key, error: failure });
    });
    return () => { ++gate.current; controller.abort(); };
    // key includes every input coordinate; response state never changes request identity.
  }, [key, revoke, enabled]);
  const current = Boolean(key) && result.key === key && !denied && !locked;
  return { data: current ? result.data : undefined, error: current ? result.error : undefined,
    loading: Boolean(key) && !current, refresh: () => setRetry(n => n + 1) };
}
const windowEvent = (name: string) => globalThis.window.dispatchEvent(new Event(name));

function IndexHistoryChart({ data }: { data: IndexHistory }) {
  const prices = data.points.flatMap(p => [p.low, p.high, p.close].filter((v): v is string => v !== null)).map(v => new Decimal(v));
  if (!prices.length) return <p role="status">窗口内无有效价格，缺口不可连线。</p>;
  const min = Decimal.min(...prices), max = Decimal.max(...prices), range = max.minus(min);
  const x = (i: number) => 12 + i * 576 / Math.max(1, data.points.length - 1);
  const y = (value: string) => range.isZero() ? 100 : 180 - new Decimal(value).minus(min).div(range).times(160).toNumber();
  return <svg viewBox="0 0 600 200" role="img" aria-label="指数历史曲线，缺口不连线" style={{ width: '100%', height: 'auto' }}>
    <title>指数点数历史；精确数值和缺失原因见下方表格</title>
    {data.chart_type === 'candle' ? data.points.map((p, i) => <g key={p.trade_date}>
      <line x1={x(i)} x2={x(i)} y1={y(p.high!)} y2={y(p.low!)} stroke="currentColor" />
      <rect x={x(i) - 2} y={Math.min(y(p.open!), y(p.close!))} width={4}
        height={Math.max(1, Math.abs(y(p.open!) - y(p.close!)))} fill="currentColor" />
    </g>) : indexHistorySegments(data.points).map((segment, i) => <g key={i} data-history-segment>
      <polyline points={segment.map(j => `${x(j)},${y(data.points[j].close!)}`).join(' ')}
        fill="none" stroke="currentColor" strokeWidth={2} />
      {segment.map(j => <circle key={j} cx={x(j)} cy={y(data.points[j].close!)} r={2} fill="currentColor" />)}
    </g>)}
  </svg>;
}

function IndexHistoryPanel({ id, state, refreshToken }: Pick<ResearchDrawerProps, 'id' | 'state' | 'refreshToken'>) {
  const [window, setWindow] = useState<HistoryWindow>(60);
  const { denied } = useAccess();
  const { enabled, store } = useSession();
  const summary = useResource<WorkbenchEnvelope<ResetSnapshotData>>(`/api/snapshots/${encodeURIComponent(state.snapshot)}`, state.snapshot, refreshToken);
  const sessions = summary.data?.trade_date === state.date && Array.isArray(summary.data?.data?.calendar_sessions)
    ? summary.data.data.calendar_sessions : [];
  const bounds = indexHistoryWindow(sessions, state.date, window);
  const selection = bounds ? { securityId: id, snapshot: state.snapshot, date: state.date, ...bounds } : null;
  const resource = useIndexHistory(selection, sessions, window, refreshToken);
  if (denied || (enabled && store.locked)) return null;
  const data = resource.data?.data;
  return <section aria-labelledby="index-history-title">
    <h3 id="index-history-title">指数历史</h3>
    <label>指数历史窗口<select value={window} onChange={event => setWindow(Number(event.target.value) as HistoryWindow)}>
      {[20, 60, 120, 250].map(n => <option key={n} value={n}>{n} 交易日</option>)}
    </select></label>
    <ResourceStatus resource={summary} name="指数日历" />
    <ResourceStatus resource={resource} name="指数历史" />
    {!summary.loading && !summary.error && !bounds && <p role="status">当前 PUB 未提供有效规范日历，指数历史不可用。</p>}
    {resource.data?.data === null && <p role="status" className="notice">当前发布未归档指数历史行情，历史曲线不可用。 · {resource.data.reason}</p>}
    {data && !resource.loading && !resource.error && <>
      <p>价格单位 point（点数） · 序列 {data.index_basis.series_type} · {data.index_basis.series_reference} · raw</p>
      <p>成交量 {data.index_basis.volume_unit} · 成交额货币 {data.index_basis.amount_currency ?? '未提供（金额缺失）'}</p>
      <p>请求 {data.requested.start} 至 {data.requested.end} · 调整 {data.adjusted.start} 至 {data.adjusted.end}</p>
      <p>实际范围 {data.available.start} 至 {data.available.end} · 归档 {data.archive.start} 至 {data.archive.end}</p>
      <p>{covered(data.coverage)} · 不足所选窗口时按 PUB 日历如实缩短。</p>
      <p className="muted">来源 {data.source.source_id} / {data.source.source_version} · 截至 {data.source.as_of} · {data.source.provenance}
        <small>行情快照 {data.source.market_snapshot_id} · 源归档 {data.source.source_archive_id} · 日历 {data.source.calendar_version}</small></p>
      <IndexHistoryChart data={data} />
      <TableScroll label="指数历史精确数值横向滚动"><table aria-label="指数历史精确数值">
        <thead><tr><th>交易日</th><th>开 / 高 / 低 / 收（point）</th><th>成交量 / 成交额</th><th>可用时间 / 缺失原因</th></tr></thead>
        <tbody>{data.points.map(point => <tr key={point.trade_date}><th>{point.trade_date}</th>
          <td>{[point.open, point.high, point.low, point.close].map(v => v ?? '缺失').join(' / ')}</td>
          <td>{point.volume ?? '缺失'} / {point.amount ?? '缺失'}</td>
          <td>{point.available_at ?? '未知'}<small>{point.reason ?? point.chart_type}</small></td></tr>)}</tbody>
      </table></TableScroll>
    </>}
  </section>;
}

export default function ResearchDrawer(props: ResearchDrawerProps) {
  const { kind, id, state, refreshToken, page, size } = props;
  const selector = new URLSearchParams({ snapshot_id: state.snapshot, date: state.date });
  const rawOverview = useResource<WorkbenchEnvelope<ResetOverview | null>>(kind === 'index' ? `/api/market/overview?${selector}` : null, state.snapshot, refreshToken);
  const indexFailure = rawOverview.data ? validateIndexOverview(rawOverview.data, state.date) : undefined;
  const overview = indexFailure ? { ...rawOverview, data: undefined, error: new ApiError(
    indexFailure === 'date_mismatch' ? 409 : 502,
    indexFailure === 'date_mismatch' ? '响应日期与当前请求不匹配，已丢弃该响应。' : '指数详情响应格式无效，已丢弃该响应。',
  ) } : rawOverview;
  const topic = useResource<WorkbenchEnvelope<ResetTopicDetail | null>>(kind === 'topic'
    ? `/api/topics/${encodeURIComponent(id)}?${selector}&page=${page}&size=${size}` : null, state.snapshot, refreshToken);
  const dialog = useRef<HTMLDialogElement>(null), closeButton = useRef<HTMLButtonElement>(null);
  const generation = useRef(0);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const current = ++generation.current;
    if (!element.open) element.showModal();
    closeButton.current?.focus();
    return () => queueMicrotask(() => {
      if (generation.current !== current) return;
      if (element.open) element.close();
      if (!document.querySelector('dialog[open]') && props.trigger.isConnected) props.trigger.focus();
    });
  }, [props.trigger]);
  useEffect(() => {
    if (!props.focusMember || !topic.data?.data) return;
    const member = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>('[data-member]') ?? [])
      .find(button => button.dataset.member === props.focusMember);
    member?.focus();
  }, [props.focusMember, topic.data]);
  const trap = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key !== 'Tab') return;
    const element = dialog.current;
    if (!element) return;
    const focusable = Array.from(element.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],select:not([disabled]),input:not([disabled]),[tabindex="0"]'))
      .filter(node => node.getClientRects().length > 0);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  const index = overview.data?.data?.indexes.find(item => item.security_id === id);
  const factors = overview.data?.data?.factors['FAC-M03'].indexes.find(item => item.security_id === id);
  const data = topic.data?.data;
  return <dialog ref={dialog} className="security-drawer research-drawer" aria-labelledby="research-drawer-title"
    onKeyDown={trap} onCancel={event => { event.preventDefault(); props.close(); }}>
    <div className="drawer-toolbar"><h2 id="research-drawer-title">{kind === 'index' ? '指数' : '题材'}详情 · 只读</h2>
      <button ref={closeButton} onClick={props.close} aria-label="关闭详情"><X size={20} /></button></div>
    <p className="muted">交易日 {state.date} · 发布快照 {state.snapshot}</p>
    {kind === 'index' ? <>
      <ResourceStatus resource={overview} name="指数详情" />
      {index && <>
        <h3>{index.name} · {index.code ?? index.security_id}</h3>
        <dl className="quote"><div><dt>最新价</dt><dd>{index.last ?? '缺失'}</dd></div>
          <div><dt>涨跌幅</dt><dd>{ratio(index.change_ratio)}</dd></div>
          <div><dt>成交额</dt><dd>{index.amount ?? '缺失'}</dd></div></dl>
        {index.reason && <p className="muted">{index.reason}</p>}
        <h3>趋势与波动</h3>
        {factors ? <dl className="factor-index">
          {([
            ['C/MA20-1', factors.close_over_ma20, factors.coverage.ma20, factors.close_over_ma20_reason],
            ['C/MA60-1', factors.close_over_ma60, factors.coverage.ma60, factors.close_over_ma60_reason],
            ['20 日收益波动', factors.volatility_20, factors.coverage.return20, factors.volatility_20_reason],
          ] as const).map(([name, value, coverage, reason]) => <div key={name}><dt>{name}</dt><dd>{ratio(value, name !== '20 日收益波动')}
            <small>{covered(coverage)}{reason && ` · ${reason}`}</small></dd></div>)}
          <div><dt>波动方法</dt><dd>{factors.volatility_method}</dd></div>
        </dl> : <p className="muted">当前快照未提供该指数的趋势因子。</p>}
        <p className="muted">来源 {overview.data?.source} / {overview.data?.source_version} · 截至 {overview.data?.as_of}</p>
      </>}
      {overview.data?.data === null && <p role="status">指数物化不可用 · materialization_unavailable。</p>}
      {overview.data?.data && !index && <p role="status">当前快照未提供该指数行情。</p>}
      <IndexHistoryPanel key={`${id}:${state.snapshot}:${state.date}`} id={id} state={state} refreshToken={refreshToken} />
    </> : <>
      <ResourceStatus resource={topic} name="题材详情" />
      {topic.data?.data === null && <p role="status" className="notice">题材成员物化不可用 · materialization_unavailable。当前发布未归档完整成员。</p>}
      {data && <>
        <h3>{data.sector.name}</h3>
        <p>成员 {data.sector.member_count} · 中位收益 {ratio(data.sector.median_return)} · 涨停占比 {ratio(data.sector.limit_up_ratio.value, false)}</p>
        <p className="muted">{covered(data.sector.coverage)} · 净流入 {data.sector.net_inflow ?? '缺失'} {data.sector.net_inflow_reason}</p>
        {data.sector.quality_flags.includes('sample_insufficient') && <p className="notice">样本不足 · sample_insufficient</p>}
        <h3>题材口径与成员证据</h3>
        <p>来源 {data.topic_basis.source ?? '未知'} / {data.topic_basis.source_version ?? '未知'} · 截至 {data.topic_basis.as_of ?? '未知'} · 截断 {data.topic_basis.cutoff}</p>
        <p>行情快照 {data.topic_basis.market_snapshot_id} · 题材快照 {data.topic_basis.topic_snapshot_id}</p>
        <p>分类版本 {data.topic_basis.taxonomy_version} · 成员版本 {data.topic_basis.membership_version}</p>
        <p>定义有效期 {data.definition.effective_from} — {data.definition.effective_to ?? '持续有效'} · 可用时间 {data.definition.available_at} · {data.definition.source_reference}</p>
        <p>成员覆盖：应有 {data.membership_coverage.expected ?? '未知'} · 已接收 {data.membership_coverage.received ?? '未知'} · 已知 {data.membership_coverage.known} · {data.membership_coverage.status === 'complete' ? '完整' : '不完整'}
          {data.membership_coverage.reason && ` · ${data.membership_coverage.reason}`} · 完整截至 {data.membership_coverage.complete_as_of ?? '未知'}</p>
        <TableScroll label="题材成员横向滚动"><table aria-label="题材成员"><thead><tr><th>证券</th><th>有效期 / 可用时间</th><th>证据</th></tr></thead>
          <tbody>{data.members.map(member => <tr key={member.security_id}><th>
            <button data-member={member.security_id} className="security-button" onClick={() => props.openMember(member.security_id)}>{member.name ?? member.code ?? member.security_id}</button>
            <small>{member.code ?? '代码未知'} · {member.security_id}</small></th>
            <td>{member.effective_from} — {member.effective_to ?? '持续有效'}<small>{member.available_at}</small></td>
            <td>{member.evidence_refs.length ? member.evidence_refs.join(' · ') : '证据缺失'}</td></tr>)}</tbody></table></TableScroll>
        {data.total === 0 && <p role="status">当前题材无有效成员。</p>}
        <Pager page={data.page} size={data.size} total={data.total} change={props.changePage} />
      </>}
      <button onClick={() => { if (props.patch({ view: 'leaders', sector: id })) props.close(); }}>查看题材梯队</button>
    </>}
  </dialog>;
}
