import { useEffect, useRef, type KeyboardEvent } from 'react';
import { X } from 'lucide-react';
import { useResource } from '../../hooks/useResource';
import type { ResearchState } from '../../state';
import type { ResetOverview, ResetTopicDetail, WorkbenchEnvelope } from '../../types';
import { Pager, Percent, ResourceStatus, TableScroll, type Patch } from '../../components/Common';
import { ApiError } from '../../api';
import { validateIndexOverview } from './topic-validation';

export interface ResearchDrawerProps {
  kind: 'index' | 'topic'; id: string; trigger: HTMLElement; close: () => void;
  state: ResearchState; refreshToken: number; patch: Patch;
  page: number; size: number; changePage: (page: number, size: number) => void; focusMember?: string;
  openMember: (id: string) => void;
}
const ratio = (value: number | null, signed = true) => <Percent value={value === null ? null : String(value)} signed={signed} />;
const covered = (value: { expected: number; valid: number; missing: number; excluded: number }) =>
  `覆盖 ${value.valid}/${value.expected} · 排除 ${value.excluded} · 缺失 ${value.missing}`;

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
        <p className="notice">当前发布未归档指数历史行情，历史曲线不可用。</p>
        <p className="muted">来源 {overview.data?.source} / {overview.data?.source_version} · 截至 {overview.data?.as_of}</p>
      </>}
      {overview.data?.data === null && <p role="status">指数物化不可用 · materialization_unavailable。</p>}
      {overview.data?.data && !index && <p role="status">当前快照未提供该指数行情。</p>}
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
