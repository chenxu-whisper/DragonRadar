import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { Pager, ResourceStatus, TableScroll } from '../../components/Common';
import { useResource } from '../../hooks/useResource';
import { CsvExportButton } from '../../export/CsvExportButton';
import { reportIndexItems } from '../preferences/commands';
import { candidatePath, validateCandidates } from './api';
import {
  aggregateRoleStatuses, capabilityLabel, changeTypeLabel, comparisonStatusLabel,
  evidenceStatusLabel, reasonLabels, roleLabel, statusLabel,
} from './labels';
import type { RoleCandidate, RoleChange, RoleName, RoleSelection } from './types';

export interface ReviewActionProps {
  snapshot: string; security: string;
  context?: { trade_date: string | null; security_name: string | null;
    sector_id: string | null; sector_name: string | null };
}
export type ReviewActionComponent = ComponentType<ReviewActionProps>;
export type OpenSecurity = (id: string, sector: string | undefined, trigger: HTMLElement) => void;
type ListRow = RoleCandidate | RoleChange;
type Field = 'security_id' | 'name' | 'rank' | 'sector_id' | 'boards' | 'change_type';

function useList(rows: ListRow[]) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);
  const [sort, setSort] = useState<{ field: Field; order: 'asc' | 'desc' } | null>(null);
  const sorted = useMemo(() => rows.map((row, index) => ({ row, index })).sort((a, b) => {
    if (!sort) return a.index - b.index;
    const left = (a.row as unknown as Record<string, unknown>)[sort.field];
    const right = (b.row as unknown as Record<string, unknown>)[sort.field];
    if (left == null || right == null) return left == null ? right == null ? a.index - b.index : 1 : -1;
    const delta = typeof left === 'number' && typeof right === 'number'
      ? left - right : String(left).localeCompare(String(right), 'zh-CN', { numeric: true });
    return (sort.order === 'asc' ? delta : -delta) || a.index - b.index;
  }).map(item => item.row), [rows, sort]);
  const current = Math.min(page, Math.max(1, Math.ceil(rows.length / size)));
  return {
    page: current, size, total: rows.length,
    rows: sorted.slice((current - 1) * size, current * size),
    change: (next: number, size: number) => { setPage(next); setSize(size); },
    header: (field: Field, title: string) => <th aria-sort={sort?.field === field
      ? sort.order === 'asc' ? 'ascending' : 'descending' : 'none'}>
      <button type="button" aria-label={`${title}排序`} onClick={() => {
        setSort({ field, order: sort?.field === field && sort.order === 'asc' ? 'desc' : 'asc' });
        setPage(1);
      }}>{title}</button>
    </th>,
  };
}

const EMPTY: never[] = [];
export default function RoleLists({ selection, refreshToken, open, ReviewActions }: {
  selection: RoleSelection & { role: RoleName }; refreshToken: number;
  open: OpenSecurity; ReviewActions?: ReviewActionComponent;
}) {
  const enabled = Boolean(selection.snapshot && selection.date);
  const resource = useResource<unknown>(enabled ? candidatePath(selection) : null, selection.snapshot, refreshToken);
  const result = useMemo(() => {
    if (!resource.data) return {};
    try { return { value: validateCandidates(resource.data, selection) }; }
    catch (error) { return { error: error instanceof Error ? error.message : '角色响应无效' }; }
  }, [resource.data, selection.snapshot, selection.date, selection.sector, selection.role]);
  const data = result.value?.data;
  const candidates = useList(data?.candidates ?? EMPTY);
  const changes = useList(data?.role_changes ?? EMPTY);
  const status = selection.sector ? data?.statuses.find(item => item.sector_id === selection.sector)
    : aggregateRoleStatuses(data?.statuses ?? []);
  const name = roleLabel(selection.role);
  useEffect(() => {
    candidates.change(1, 50); changes.change(1, 50);
  }, [selection.snapshot, selection.date, selection.sector, selection.role]);
  useEffect(() => {
    if (data) reportIndexItems({
      securities: data.candidates.map(item => ({ id: item.security_id, name: item.name })),
      sectors: data.statuses.map(item => ({ id: item.sector_id, name: null })),
    });
  }, [data]);
  const approved = !result.value?.quality_flags.includes('rule_version_not_approved');
  return <>
    <section role="region" className="leaders-section" aria-label={`${name}候选`}>
      <div id={selection.role === 'trend_leader' ? 'workbench-candidates' : undefined} tabIndex={-1} className="section-heading">
        <h2>{name}候选</h2>
        <span className="muted">有效 {result.value?.coverage?.valid ?? '—'} / 应有 {result.value?.coverage?.expected ?? '—'} · 排除 {result.value?.coverage?.excluded ?? '—'} · 缺失 {result.value?.coverage?.missing ?? '—'}</span>
      </div>
      <ResourceStatus resource={resource} name={`${name}候选`} />
      {result.error && <p className="notice error" role="alert">{result.error}</p>}
      {result.value?.data === null && <p>角色域未发布</p>}
      {!approved && <p>规则尚未批准，不展示正式候选。</p>}
      {approved && status && <p className="leader-state">
        <strong>{status.status === 'insufficient' ? '角色输入不足'
          : status.status === 'no_candidate' ? '完整输入下暂无候选' : statusLabel(status.status)}</strong>
        {reasonLabels(status.reasons).join('；')}
      </p>}
      {approved && data && data.candidates.length > 0 && <>
        <TableScroll label={`${name}候选横向滚动`}>
          <table className="responsive-record-table candidate-table" aria-label="角色候选">
            <thead><tr>{candidates.header('rank', '名次')}{candidates.header('security_id', '证券')}
              {candidates.header('name', '名称')}{candidates.header('sector_id', '题材')}
              {candidates.header('boards', '板数')}<th>证据状态</th>{ReviewActions && <th>本地记录</th>}</tr></thead>
            <tbody>{(candidates.rows as RoleCandidate[]).map(candidate => <tr key={`${candidate.sector_id}:${candidate.security_id}`}>
              <td data-label="名次">并列 {candidate.rank}</td>
              <th data-label="证券"><button type="button" className="security-button" aria-label={candidate.name || candidate.security_id}
                onClick={event => open(candidate.security_id, candidate.sector_id, event.currentTarget)}>
                {candidate.name || candidate.security_id}<span className="security-id">{candidate.security_id}</span>
              </button></th>
              <td data-label="名称">{candidate.name ?? '名称不可用'}</td><td data-label="题材">{candidate.sector_id}</td><td data-label="板数">{candidate.boards ?? '—'}</td>
              <td data-label="证据状态">{evidenceStatusLabel(candidate.evidence_status)}</td>
              {ReviewActions && <td data-label="本地记录"><ReviewActions snapshot={selection.snapshot} security={candidate.security_id}
                context={{ trade_date: selection.date, security_name: candidate.name,
                  sector_id: candidate.sector_id, sector_name: null }} /></td>}
            </tr>)}</tbody>
          </table>
        </TableScroll>
        <Pager {...candidates} />
        {result.value?.rule_version && <CsvExportButton scope="leaders-current-filter" label="导出当前筛选 CSV"
          envelope={result.value as typeof result.value & { rule_version: string }}
          filters={{ date: selection.date, sector: selection.sector ?? '', role: selection.role,
            page: String(candidates.page), page_size: String(candidates.size) }}
          columns={['rank', 'security_id', 'name', 'sector_id', 'boards', 'evidence_status'].map(key => ({ key, header: key }))}
          rows={(candidates.rows as RoleCandidate[]).map(item => ({ snapshot_id: selection.snapshot, ...item }))} />}
      </>}
      {data && <details className="evidence"><summary>逐题材状态与原始依据</summary>
        <TableScroll label={`${name}逐题材状态横向滚动`}>
          <table aria-label="逐题材状态与原始依据"><thead><tr><th>题材</th><th>状态</th><th>能力</th><th>原因</th><th>原始码</th></tr></thead>
            <tbody>{data.statuses.map(item => <tr key={item.sector_id}>
              <th>{item.sector_id}</th><td>{statusLabel(item.status)}</td><td>{capabilityLabel(item.capability)}</td>
              <td>{reasonLabels(item.reasons).join('；') || '无原因'}</td><td><code>{[item.status, item.capability, ...item.reasons].join(' / ')}</code></td>
            </tr>)}</tbody>
          </table>
        </TableScroll>
      </details>}
    </section>
    {data && <section role="region" aria-label={`${name}角色变化`} className="leaders-section changes-section">
      <h2>{name}角色变化</h2>
      {data.change_summaries.map((change, index) => <div className="change-row" key={`${change.sector_id}:${index}`}>
        <span>{change.sector_id}</span><strong>{comparisonStatusLabel(change.comparison_status)}</strong>
        <span>{reasonLabels(change.reasons).join('；') || '无原因'}</span>{change.membership_changed && <span>成员发生变化</span>}
      </div>)}
      {data.role_changes.length > 0 && <>
        <TableScroll label={`${name}角色变化横向滚动`}>
          <table className="responsive-record-table role-changes-table" aria-label="角色变化明细">
            <thead><tr>{changes.header('sector_id', '题材')}{changes.header('security_id', '证券')}
              {changes.header('name', '名称')}{changes.header('rank', '名次')}{changes.header('change_type', '变化类型')}
              <th>角色</th><th>比较状态</th><th>原因</th></tr></thead>
            <tbody>{(changes.rows as RoleChange[]).map(change => <tr key={change.id}>
              <td data-label="题材">{change.sector_id}</td><th data-label="证券">{change.security_id}</th><td data-label="名称">{change.name ?? '名称不可用'}</td>
              <td data-label="名次">{change.rank ?? '—'}</td><td data-label="变化类型">{change.change_type ? changeTypeLabel(change.change_type) : '—'}</td>
              <td data-label="角色">{roleLabel(change.role)}</td><td data-label="比较状态">{comparisonStatusLabel(change.comparison_status)}</td>
              <td data-label="原因">{reasonLabels(change.reasons).join('；') || '无原因'}</td>
            </tr>)}</tbody>
          </table>
        </TableScroll><Pager {...changes} />
      </>}
      {!data.role_changes.length && !data.change_summaries.length && <p>没有已发布的变化记录。</p>}
      <details className="evidence"><summary>角色变化原始证据</summary>
        <pre>{JSON.stringify({ change_summaries: data.change_summaries, role_changes: data.role_changes }, null, 2)}</pre>
      </details>
    </section>}
  </>;
}
