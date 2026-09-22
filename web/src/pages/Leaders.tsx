import { AlertTriangle, ArrowRight, CircleDashed } from 'lucide-react';
import { useMemo, type ComponentType } from 'react';
import { ResourceStatus, TableScroll, type Patch } from '../components/Common';
import { useResource } from '../hooks/useResource';
import {
  candidatePath, continuityPath, ladderPath,
  validateCandidates, validateContinuity, validateLadder,
} from '../features/roles/api';
import {
  ROLE_LABELS, ROLE_NAMES,
  type CandidatesData, type ContinuityData, type LadderData, type RoleEnvelope,
} from '../features/roles/types';
import type { ResearchState } from '../state';
import type { ResetHeatmap, WorkbenchEnvelope } from '../types';

type ReviewActionComponent = ComponentType<{ snapshot: string; security: string }>;
export interface LeadersProps {
  state: ResearchState;
  patch: Patch;
  open: (id: string, trigger: HTMLElement) => void;
  refreshToken: number;
  ReviewActions?: ReviewActionComponent;
}

function decoded<T>(
  raw: unknown,
  decode: (value: unknown) => RoleEnvelope<T>,
): { value?: RoleEnvelope<T>; error?: string } {
  if (!raw) return {};
  try { return { value: decode(raw) }; }
  catch (error) { return { error: error instanceof Error ? error.message : '响应格式无效' }; }
}

function percent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(2)}%`;
}

function StateMessage({ status, reasons }: { status?: string; reasons?: string[] }) {
  if (status === 'no_candidate') return <div className="leader-state"><CircleDashed size={18} /><strong>完整输入下暂无候选</strong><span>{reasons?.join('；')}</span></div>;
  if (status === 'insufficient') return <div className="leader-state"><AlertTriangle size={18} /><strong>角色输入不足</strong><span>{reasons?.join('；')}</span></div>;
  return null;
}

export function Leaders({ state, patch, open, refreshToken, ReviewActions: _ReviewActions }: LeadersProps) {
  const selection = { snapshot: state.snapshot, date: state.date, sector: state.sector || undefined, role: state.role };
  const enabled = Boolean(state.snapshot && state.date);
  const ladder = useResource<unknown>(enabled ? ladderPath(selection) : null, state.snapshot, refreshToken);
  const continuity = useResource<unknown>(enabled ? continuityPath(selection) : null, state.snapshot, refreshToken);
  const candidates = useResource<unknown>(enabled ? candidatePath({ ...selection, role: state.role }) : null, state.snapshot, refreshToken);
  const heatmap = useResource<WorkbenchEnvelope<ResetHeatmap>>(enabled
    ? `/api/market/heatmap?${new URLSearchParams({ date: state.date, snapshot_id: state.snapshot, days: '10' })}`
    : null, state.snapshot, refreshToken);
  const ladderResult = useMemo(
    () => decoded(ladder.data, value => validateLadder(value, selection)),
    [ladder.data, state.snapshot, state.date, state.sector],
  );
  const continuityResult = useMemo(
    () => decoded(continuity.data, value => validateContinuity(value, selection)),
    [continuity.data, state.snapshot, state.date],
  );
  const candidateResult = useMemo(
    () => decoded(candidates.data, value => validateCandidates(value, { ...selection, role: state.role })),
    [candidates.data, state.snapshot, state.date, state.sector, state.role],
  );
  const ladderData = ladderResult.value?.data;
  const continuityData = continuityResult.value?.data;
  const candidateData = candidateResult.value?.data;
  const unavailable = [ladderResult.value, continuityResult.value, candidateResult.value]
    .some(value => value && value.data === null);
  const sectorOptions = useMemo(() => {
    const values = new Set<string>();
    candidateData?.statuses.forEach(item => values.add(item.sector_id));
    ladderData?.cells.forEach(cell => cell.stocks.forEach(stock => values.add(stock.sector_id)));
    if (state.sector) values.add(state.sector);
    return [...values].sort();
  }, [candidateData, ladderData, state.sector]);
  const columns = useMemo(() => {
    const values = new Set<number>(ladderData?.columns ?? []);
    heatmap.data?.data?.cells.forEach(cell => values.add(cell.boards));
    return [...values].sort((a, b) => a - b);
  }, [ladderData, heatmap.data]);
  const dates = useMemo(() => {
    const values = new Set<string>(heatmap.data?.data?.cells.map(cell => cell.trade_date) ?? []);
    if (state.date) values.add(state.date);
    return [...values].sort().reverse();
  }, [heatmap.data, state.date]);
  const currentCells = new Map(ladderData?.cells.map(cell => [cell.boards, cell]) ?? []);
  const historical = new Map(
    heatmap.data?.data?.cells.map(cell => [`${cell.trade_date}:${cell.boards}`, cell.count]) ?? [],
  );
  const selectedStatus = candidateData?.statuses.find(item => !state.sector || item.sector_id === state.sector);

  return <>
    <div className="page-heading leaders-heading"><div><p className="eyebrow">梯队结构 / 角色证据</p><h1>梯队</h1></div>
      <span className="muted">{candidateResult.value?.rule_version || '规则版本未知'}</span></div>
    <div className="leaders-controls" aria-label="梯队筛选">
      <label>交易日期<input type="date" value={state.date} onChange={event => patch({ date: event.target.value, snapshot: '' })} /></label>
      <label>题材<select aria-label="题材筛选" value={state.sector} onChange={event => patch({ sector: event.target.value })}>
        <option value="">全部题材</option>
        {sectorOptions.map(sector => <option key={sector} value={sector}>{sector}</option>)}
      </select></label>
      <label>角色<select aria-label="角色筛选" value={state.role} onChange={event => patch({ role: event.target.value as ResearchState['role'] })}>
        {ROLE_NAMES.map(role => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
      </select></label>
      <span className="status-token">{selectedStatus?.status || (unavailable ? 'unavailable' : 'loading')}</span>
    </div>

    <ResourceStatus resource={ladder} name="梯队矩阵" />
    <ResourceStatus resource={continuity} name="晋级反馈" />
    <ResourceStatus resource={candidates} name="角色候选" />
    <ResourceStatus resource={heatmap} name="多日板数" />
    {[ladderResult.error, continuityResult.error, candidateResult.error].filter(Boolean).map(error =>
      <p key={error} className="notice error" role="alert">{error}</p>)}
    {unavailable && <div className="leader-state"><AlertTriangle size={18} /><strong>角色域未发布</strong><span>当前快照没有可读取的角色物化结果。</span></div>}

    {ladderData && <section className="leaders-section" aria-labelledby="ladder-title">
      <div className="section-heading"><h2 id="ladder-title">日期 × 板数</h2><span className="muted">收盘连板；同一证券仅计一次</span></div>
      <TableScroll label="日期与板数矩阵横向滚动">
        <table className="ladder-matrix" aria-label="日期与板数矩阵">
          <thead><tr><th className="identity">日期</th>{columns.map(board => <th key={board}>{board}板</th>)}</tr></thead>
          <tbody>{dates.map(date => <tr key={date}><th className="identity">{date}</th>{columns.map(board => {
            const cell = date === state.date ? currentCells.get(board) : undefined;
            const count = date === state.date ? cell?.count : historical.get(`${date}:${board}`);
            return <td key={board} className="ladder-cell">
              {cell?.stocks.map(stock => <button type="button" className="matrix-security" key={stock.security_id}
                aria-label={stock.name}
                onClick={event => open(stock.security_id, event.currentTarget)}>
                <strong>{stock.name}</strong><span>{stock.security_id}</span>
              </button>)}
              {!cell?.stocks.length && count !== undefined && <span className="matrix-count">{count} 家</span>}
              {count === undefined && <span aria-label="无数据">—</span>}
            </td>;
          })}</tr>)}</tbody>
        </table>
      </TableScroll>
      <p className="muted">盘中封板字段不可用</p>
    </section>}

    {continuityData && <section className="leaders-section" aria-labelledby="continuity-title">
      <div className="section-heading"><h2 id="continuity-title">晋级与昨日反馈</h2></div>
      <dl className="continuity-strip">
        <div><dt>昨日 ≥2板</dt><dd>{continuityData.promote_rate.yesterday_ge2}</dd></div>
        <div><dt>今日封板 / 可交易</dt><dd>{continuityData.promote_rate.today_sealed} / {continuityData.promote_rate.tradable}</dd></div>
        <div><dt>晋级率</dt><dd>{percent(continuityData.promote_rate.rate)}</dd></div>
        <div><dt>昨日涨停收益中位数</dt><dd>{percent(continuityData.yesterday_limit_up_median_return)}</dd></div>
      </dl>
      {[...continuityData.promote_rate_quality.reasons, ...continuityData.yesterday_limit_up_feedback.reasons].length > 0 &&
        <p className="muted">{[...continuityData.promote_rate_quality.reasons, ...continuityData.yesterday_limit_up_feedback.reasons].join('；')}</p>}
    </section>}

    {candidateData && <section className="leaders-section" aria-labelledby="candidates-title">
      <div className="section-heading"><h2 id="candidates-title">{ROLE_LABELS[state.role]}候选</h2>
        <span className="muted">有效 {candidateResult.value?.coverage?.valid ?? '—'} / 应有 {candidateResult.value?.coverage?.expected ?? '—'}</span></div>
      {candidateResult.value?.quality_flags.includes('rule_version_not_approved') &&
        <div className="leader-state"><AlertTriangle size={18} /><strong>规则尚未批准</strong><span>不展示正式候选。</span></div>}
      <StateMessage status={selectedStatus?.status} reasons={selectedStatus?.reasons} />
      {candidateData.candidates.length > 0 && <TableScroll label="角色候选横向滚动">
        <table aria-label="角色候选"><thead><tr><th>名次</th><th>证券</th><th>题材</th><th>板数</th><th>证据状态</th></tr></thead>
          <tbody>{candidateData.candidates.map(candidate => <tr key={`${candidate.sector_id}:${candidate.security_id}`}>
            <td>并列 {candidate.rank}</td>
            <th><button type="button" className="security-button" aria-label={candidate.name} onClick={event => open(candidate.security_id, event.currentTarget)}>
              {candidate.name}<span className="security-id">{candidate.security_id}</span></button></th>
            <td>{candidate.sector_id}</td><td>{candidate.boards ?? '—'}</td><td><span className="status-token">{candidate.evidence_status}</span></td>
          </tr>)}</tbody></table>
      </TableScroll>}
    </section>}

    {candidateData && <section className="leaders-section changes-section" aria-labelledby="changes-title">
      <div className="section-heading"><h2 id="changes-title">角色变化</h2></div>
      {candidateData.change_summaries.length === 0 && candidateData.role_changes.length === 0
        ? <p className="muted">没有已发布的变化记录。</p>
        : candidateData.change_summaries.map((change, index) => <div className="change-row" key={`${change.sector_id}:${index}`}>
          <span>{change.sector_id}</span>
          <strong>{change.comparison_status === 'not_comparable' ? '不可比较' : '可比较'}</strong>
          <span>{change.reasons.join('；') || '无原因'}</span>
          {change.membership_changed && <span>成员发生变化</span>}
          <ArrowRight size={16} aria-hidden="true" />
        </div>)}
    </section>}
  </>;
}
