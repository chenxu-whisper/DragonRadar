import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { ResourceStatus, TableScroll, type Patch } from '../components/Common';
import { UnavailableModules } from '../features/workbench/PageStructure';
import { useResource } from '../hooks/useResource';
import { continuityPath, ladderPath, validateContinuity, validateLadder } from '../features/roles/api';
import { ROLE_NAMES, type LadderHistoryRow, type RoleEnvelope, type LadderStock } from '../features/roles/types';
import { evidenceStatusLabel, reasonLabels, roleLabel } from '../features/roles/labels';
import RoleLists, { type OpenSecurity, type ReviewActionComponent } from '../features/roles/RoleLists';
import type { ResearchState } from '../state';
import type { ResetHeatmap, WorkbenchEnvelope } from '../types';
import { SectionBoundary } from '../features/roles/SectionBoundary';
import { reportIndexItems } from '../features/preferences/commands';
import '../features/roles/leaders.css';

const RotationTimeline = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(() => import('../features/roles/RotationTimeline')) : null;

export interface LeadersProps {
  state: ResearchState; patch: Patch; open: OpenSecurity; refreshToken: number;
  ReviewActions?: ReviewActionComponent;
  onOpenHistorical?: (context: { snapshot: string; date: string; sector: string; security: string }, trigger: HTMLElement) => void;
}

function decoded<T>(raw: unknown, decode: (value: unknown) => RoleEnvelope<T>) {
  if (!raw) return {};
  try { return { value: decode(raw) }; }
  catch (error) { return { error: error instanceof Error ? error.message : '响应格式无效' }; }
}
function percent(value: number | null | undefined) {
  return value == null ? '—' : `${(value * 100).toFixed(2)}%`;
}
function Card({ stock, highlighted, activate }: {
  stock: LadderStock; highlighted: boolean; activate?: (trigger: HTMLElement) => void;
}) {
  return <button type="button" className="matrix-security" aria-label={stock.name || stock.security_id}
    data-role-highlighted={String(highlighted)} disabled={!activate}
    onClick={event => activate?.(event.currentTarget)}>
    <strong>{stock.name || stock.security_id}</strong><span>{stock.security_id}</span>
    <span>{percent(stock.change_ratio)}</span>
    {stock.change_reasons?.length ? <span>{reasonLabels(stock.change_reasons).join('；')}</span> : null}
    {stock.topics?.map(topic => <span key={topic.sector_id}>{topic.name || topic.sector_id}</span>)}
    {(stock.role_evidence ?? stock.roles.map(role_type => ({
      role_type, sector_id: stock.sector_id, evidence_status: 'candidate',
    }))).map((role, index) => <span key={`${role.role_type}:${role.sector_id}:${index}`}>
      {roleLabel(role.role_type)} · {evidenceStatusLabel(role.evidence_status)}
    </span>)}
    {stock.role_reasons?.length ? <span>{reasonLabels(stock.role_reasons).join('；')}</span> : null}
    <span>首封 {stock.seal?.first_seal_time ?? '不可用'} · 末封 {stock.seal?.last_seal_time ?? '不可用'}</span>
    {stock.seal?.reason && <span>{reasonLabels([stock.seal.reason]).join('；')} · {stock.seal.reason}</span>}
  </button>;
}

export function Leaders({ state, patch, open, refreshToken, ReviewActions, onOpenHistorical }: LeadersProps) {
  const selection = { snapshot: state.snapshot, date: state.date, sector: state.sector || undefined, days: 10 };
  const selectedRoles = ROLE_NAMES.filter(role => (state.roles ?? [state.role]).includes(role));
  const enabled = Boolean(state.snapshot && state.date);
  const ladder = useResource<unknown>(enabled ? ladderPath(selection) : null, state.snapshot, refreshToken);
  const continuity = useResource<unknown>(enabled ? continuityPath(selection) : null, state.snapshot, refreshToken);
  const heatmap = useResource<WorkbenchEnvelope<ResetHeatmap>>(enabled && !state.sector
    ? `/api/market/heatmap?${new URLSearchParams({ date: state.date, snapshot_id: state.snapshot, days: '10' })}` : null,
  state.snapshot, refreshToken);
  const ladderResult = useMemo(() => decoded(ladder.data, value => validateLadder(value, selection)),
    [ladder.data, state.snapshot, state.date, state.sector]);
  const continuityResult = useMemo(() => decoded(continuity.data, value => validateContinuity(value, selection)),
    [continuity.data, state.snapshot, state.date]);
  const data = ladderResult.value?.data;
  const feedback = continuityResult.value?.data;
  useEffect(() => {
    if (data) reportIndexItems({
      securities: data.cells.flatMap(cell => cell.stocks.map(stock => ({ id: stock.security_id, name: stock.name }))),
      sectors: data.cells.flatMap(cell => cell.stocks.flatMap(stock => stock.topics?.map(topic => ({
        id: topic.sector_id, name: topic.name,
      })) ?? [{ id: stock.sector_id, name: null }])) ,
    });
  }, [data]);
  const [expandedFeedback, setExpandedFeedback] = useState(false);
  const [expandedPromotion, setExpandedPromotion] = useState(false);
  const sectorOptions = [...new Set([
    ...(data?.cells.flatMap(cell => cell.stocks.flatMap(stock => stock.topics?.map(topic => topic.sector_id) ?? [stock.sector_id])) ?? []),
    ...(state.sector ? [state.sector] : []),
  ])].sort();
  const rows: LadderHistoryRow[] = data?.history?.rows ?? (data ? [{
    trade_date: state.date, snapshot_id: state.snapshot, rule_version: ladderResult.value?.rule_version ?? null,
    status: 'available', reasons: [], coverage: ladderResult.value?.coverage ?? null,
    columns: data.columns, total: data.total, cells: data.cells,
  }] : []);
  const rowDates = new Set(rows.map(row => row.trade_date));
  const anchorCells = !state.sector && heatmap.data?.snapshot_id === state.snapshot
    && heatmap.data?.trade_date === state.date
    ? heatmap.data.data?.cells.filter(cell => rowDates.has(cell.trade_date)) ?? [] : [];
  const columns = [...new Set([
    ...rows.flatMap(row => row.columns ?? []), ...anchorCells.map(cell => cell.boards),
  ])].sort((a, b) => a - b);
  const anchorCounts = new Map(anchorCells.map(cell => [`${cell.trade_date}:${cell.boards}`, cell.count]));
  const detail = feedback?.details?.status === 'available' ? feedback.details.data : null;
  return <>
    <div className="page-heading leaders-heading"><div><p className="eyebrow">梯队结构 / 角色证据</p><h1>龙头梯队</h1></div>
      <span>{ladderResult.value?.rule_version || '规则版本未知'}</span></div>
    <div className="leaders-controls" aria-label="梯队筛选">
      <label>交易日期<input type="date" value={state.date} onChange={event => patch({ date: event.target.value, snapshot: '' })} /></label>
      <label>题材<select aria-label="题材筛选" value={state.sector} onChange={event => patch({ sector: event.target.value })}>
        <option value="">全部题材</option>{sectorOptions.map(sector => <option key={sector} value={sector}>{sector}</option>)}
      </select></label>
      <label>单角色<select aria-label="角色筛选" value={state.role} onChange={event => {
        const role = event.target.value as ResearchState['role'];
        patch({ role, roles: [role] });
      }}>{ROLE_NAMES.map(role => <option key={role} value={role}>{roleLabel(role)}</option>)}</select></label>
      <fieldset><legend>角色筛选（OR 高亮，独立候选）</legend>{ROLE_NAMES.map(role => <label key={role}>
        <input type="checkbox" checked={selectedRoles.includes(role)} onChange={event => {
          const next = ROLE_NAMES.filter(value => value === role ? event.target.checked : selectedRoles.includes(value));
          if (next.length) patch({ roles: next });
        }} />{roleLabel(role)}
      </label>)}</fieldset>
    </div>
    <ResourceStatus resource={ladder} name="梯队矩阵" /><ResourceStatus resource={continuity} name="晋级反馈" />
    {[ladderResult.error, continuityResult.error].filter(Boolean).map(error => <p key={error} className="notice error" role="alert">{error}</p>)}
    {ladderResult.value?.data === null && <p>角色域未发布</p>}
    <UnavailableModules view="leaders" ids={[...(!data ? ['ladder'] : []), ...(!feedback ? ['continuity'] : [])]}
      loading={ladder.loading || continuity.loading} />
    {data && <section className="leaders-section" aria-labelledby="ladder-title">
      <div id="workbench-ladder" tabIndex={-1} className="section-heading"><h2 id="ladder-title">日期 × 板数</h2>
        <span>实际历史发布；同一证券仅计一次</span></div>
      <TableScroll label="日期与板数矩阵横向滚动">
        <table className="ladder-matrix" aria-label="日期与板数矩阵"><thead><tr><th>日期 / 发布 / 规则</th>{columns.map(board => <th key={board}>{board}板</th>)}</tr></thead>
          <tbody>{[...rows].reverse().map(row => <tr key={row.trade_date}>
            <th>{row.trade_date}<br />{row.snapshot_id ?? '发布不可用'}<br />{row.rule_version ?? '规则不可用'}
              {row.status === 'unavailable' && <p>不可用 · {row.reasons.join('；')}</p>}</th>
            {columns.map(board => {
              const cell = row.cells?.find(cell => cell.boards === board);
              const count = anchorCounts.get(`${row.trade_date}:${board}`);
              return <td className="ladder-cell" key={board}>
                {cell?.stocks.map(stock => <Card key={stock.security_id} stock={stock}
                  highlighted={stock.roles.some(role => selectedRoles.includes(role))}
                  activate={row.trade_date === state.date
                    ? trigger => open(stock.security_id, stock.sector_id, trigger)
                    : onOpenHistorical && row.snapshot_id ? trigger => onOpenHistorical({
                      snapshot: row.snapshot_id!, date: row.trade_date, sector: stock.sector_id, security: stock.security_id,
                    }, trigger) : undefined} />)}
                {!cell?.stocks.length && <span>{row.status === 'unavailable' ? '不可用' : '0 家'}</span>}
                {count !== undefined && row.trade_date !== state.date && <small>锚点归档计数 {count}；历史发布列表 {cell?.count ?? (row.status === 'available' ? 0 : '不可用')}</small>}
              </td>;
            })}
          </tr>)}</tbody>
        </table>
      </TableScroll>
      <p className="muted">历史列表遵循原发布序号水位；锚点热力图计数不是历史发布成员列表。盘中封板字段未知时显示不可用。</p>
      {!data.history && state.sector && <p className="muted">题材筛选下仅展示所选日期的可验证梯队；历史题材矩阵不可用。</p>}
      {!data.history && <p className="muted">盘中封板字段不可用</p>}
    </section>}
    {feedback && <section className="leaders-section" aria-labelledby="continuity-title">
      <div id="workbench-continuity" tabIndex={-1} className="section-heading"><h2 id="continuity-title">晋级与昨日反馈</h2></div>
      <dl className="continuity-strip">
        <div><dt>昨日 ≥2板</dt><dd>{feedback.promote_rate.yesterday_ge2}</dd></div>
        <div><dt>今日封板 / 可交易</dt><dd>{feedback.promote_rate.today_sealed} / {feedback.promote_rate.tradable}</dd></div>
        <div><dt>晋级率</dt><dd>{percent(feedback.promote_rate.rate)}</dd></div>
        <div><dt>昨日涨停收益中位数</dt><dd>{percent(feedback.yesterday_limit_up_median_return)}</dd></div>
      </dl>
      <p>{reasonLabels([...feedback.promote_rate_quality.reasons, ...feedback.yesterday_limit_up_feedback.reasons]).join('；')}</p>
      {!detail && <p>详情不可用 · {feedback.details?.reasons.join('；') || 'continuity_detail_unavailable'}</p>}
      {detail && <>
        <p>固定昨日池 {detail.previous_trade_date ?? '基期不可用'} → {detail.trade_date} · 发布 {state.snapshot} · 归档版本 {feedback.details?.version}</p>
        <button type="button" aria-expanded={expandedPromotion} onClick={() => setExpandedPromotion(value => !value)}>查看昨日晋级详情</button>
        <button type="button" aria-expanded={expandedFeedback} onClick={() => setExpandedFeedback(value => !value)}>查看昨日涨停反馈详情</button>
        {expandedFeedback && <p>上涨 {detail.feedback.direction_counts.up} · 下跌 {detail.feedback.direction_counts.down} · 平盘 {detail.feedback.direction_counts.flat}</p>}
        {(['promotion', 'feedback'] as const).filter(pool => pool === 'promotion' ? expandedPromotion : expandedFeedback).map(pool =>
          <div key={pool}><h3>{pool === 'promotion' ? '昨日 ≥2板固定池' : '昨日涨停固定池'}</h3>
            <p>应有 {detail[pool].coverage.expected} · 有效 {detail[pool].coverage.valid} · 排除 {detail[pool].coverage.excluded} · 缺失 {detail[pool].coverage.missing}</p>
            <TableScroll label="固定池详情横向滚动"><table aria-label={pool === 'promotion' ? '昨日晋级固定池' : '昨日涨停反馈固定池'}>
              <thead><tr><th>证券</th><th>名称</th><th>状态</th><th>收益</th><th>方向</th><th>今日封板</th><th>原因</th></tr></thead>
              <tbody>{detail[pool].members.map(member => <tr key={member.security_id}>
                <th>{member.security_id}</th><td>{member.name ?? '名称不可用'}</td><td>{member.status}</td><td>{percent(member.return)}</td>
                <td>{member.direction ?? '不可用'}</td><td>{member.sealed === null ? '不可用' : member.sealed ? '是' : '否'}</td><td>{member.reasons.join('；') || '无原因'}</td>
              </tr>)}</tbody>
            </table></TableScroll>
          </div>)}
      </>}
    </section>}
    {selectedRoles.map(role => <RoleLists key={`${state.snapshot}:${state.date}:${state.sector}:${role}`}
      selection={{ ...selection, role }} refreshToken={refreshToken} open={open} ReviewActions={ReviewActions} />)}
    {RotationTimeline && enabled && <section id="workbench-rotation" tabIndex={-1}><SectionBoundary name="题材轮动时间轴">
      <Suspense fallback={<p role="status">正在加载题材轮动时间轴…</p>}>
        <RotationTimeline date={state.date} snapshot={state.snapshot} sector={state.sector || undefined} refreshToken={refreshToken} />
      </Suspense>
    </SectionBoundary></section>}
  </>;
}
