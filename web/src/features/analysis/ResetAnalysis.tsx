import { lazy, Suspense, useEffect, useMemo, useState, type ComponentType } from 'react';
import { useResource } from '../../hooks/useResource';
import type { ResearchState, Sort } from '../../state';
import type { ResetRankData, Snapshot, WorkbenchEnvelope } from '../../types';
import { Exclusions, Pager, ResourceStatus, SearchForm, TableScroll, type Patch } from '../../components/Common';
import { IntervalForm } from '../../components/IntervalForm';
import { analysisRange, analysisSortDescriptor } from './query';
import { CsvExportButton } from '../../export/CsvExportButton';
import { reportIndexItems } from '../preferences/commands';
import { UnavailableModules } from '../workbench/PageStructure';
import type { AnalysisSelection } from '../analysis-jobs/types';

const OwnerAnalysisJobPanel = import.meta.env.VITE_RESET_WORKBENCH === '1'
  && import.meta.env.VITE_PUBLIC_SHARING !== '1'
  && import.meta.env.VITE_PRIVATE_REVIEW === '1'
  ? lazy(() => import('../analysis-jobs/OwnerAnalysisJobPanel')) : null;

export interface ResetAnalysisProps {
  state: ResearchState;
  patch: Patch;
  refreshToken: number;
  open: (id: string, sector: string | undefined, trigger: HTMLElement) => void;
  compare: (id: string) => void;
  snapshot?: Snapshot;
  ReviewActions?: ComponentType<{ snapshot: string; security: string }>;
}

function ratio(value: number | null, signed = true): string {
  if (value === null) return '—';
  const percent = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value * 100);
  return `${signed && value > 0 ? '+' : ''}${percent}%`;
}

function ResetSortHead({ title, field, state, patch }: {
  title: string; field: Sort; state: ResearchState; patch: Patch;
}) {
  const current = analysisSortDescriptor(state.sort, state.rank_order);
  const selected = analysisSortDescriptor(field);
  const active = current.requestField === selected.requestField;
  const order = active ? current.direction : selected.direction;
  const nextOrder = active ? order === 'asc' ? 'desc' : 'asc' : selected.direction;
  return <th aria-sort={active ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>
    <button className="sort-button" title={`按${title}${nextOrder === 'asc' ? '升序' : '降序'}排序`}
      onClick={() => patch({ sort: field, rank_order: active ? nextOrder : undefined, page: 1 })}>
      {title}
    </button>
  </th>;
}

export function ResetAnalysis({ state, patch, refreshToken, open, compare, snapshot, ReviewActions }: ResetAnalysisProps) {
  let query = '', error = '';
  let selection: AnalysisSelection | undefined;
  const sort = analysisSortDescriptor(state.sort, state.rank_order);
  try {
    if (snapshot) {
      const range = analysisRange(state, snapshot.result.calendar_sessions);
      query = new URLSearchParams({
        snapshot_id: state.snapshot,
        start: range.start,
        end: range.end,
        benchmark: state.benchmark,
        adj: state.adj,
        sort: sort.requestField,
        order: sort.direction,
      }).toString();
      selection = {
        snapshot_id: state.snapshot, start: range.start, end: range.end,
        benchmark: state.benchmark, adj: state.adj, symbols: [],
        sessions: snapshot.result.calendar_sessions.filter(day => day >= range.start && day <= range.end),
      };
    }
  } catch (failure) { error = (failure as Error).message; }
  const fingerprint = JSON.stringify([
    query, state.date, state.q, state.page, state.page_size, state.compare,
  ]);
  const [handoff, setHandoff] = useState<string | null>(null);
  const [jobCompletedFor, setJobCompletedFor] = useState<string | null>(null);
  const stopped = handoff === fingerprint;
  const resource = useResource<WorkbenchEnvelope<ResetRankData | null>>(
    query && !stopped ? `/api/rank/range?${query}` : null,
    state.snapshot,
    refreshToken,
  );
  const response = stopped ? undefined : resource.data;
  useEffect(() => {
    if (!query || !resource.loading || stopped) return;
    const timer = setTimeout(() => setHandoff(fingerprint), 5000);
    return () => clearTimeout(timer);
  }, [query, fingerprint, resource.loading, stopped, refreshToken]);
  const filtered = useMemo(() => {
    const term = state.q.toLowerCase();
    return (response?.data?.ranking || []).filter(item =>
      `${item.security_id} ${item.name || ''}`.toLowerCase().includes(term));
  }, [response, state.q]);
  const visible = filtered.slice((state.page - 1) * state.page_size, state.page * state.page_size);
  const rankData = response?.data;

  // 仅上报本页已加载排行证券；不新增请求，排行证券为空时不派发。
  // 不上报题材：排行项只携带题材快照域的 topic_id（与 News topic_id 同源），
  // 而面板题材命令导航到 leaders 的 sector_id 空间，两 id 域不保证相等，
  // 猜测映射会跳到 leaders 匹配不到的 sector；题材候选由 Overview/Leaders
  // 已加载的 sector_id 提供。
  useEffect(() => {
    if (!rankData || rankData.ranking.length === 0) return;
    const securities = rankData.ranking.map(item => ({ id: item.security_id, name: item.name }));
    reportIndexItems({ securities });
  }, [rankData]);

  return <>
    <div className="page-heading"><h1>涨幅分析</h1></div>
    <IntervalForm state={state} patch={patch} reset />
    {error && <p role="alert" className="notice error">{error}</p>}
    <ResourceStatus resource={resource} name="涨幅排行" />
    {stopped && <p className="notice" role="status">
      归档读取超过五秒，旧请求已失效。
      <button type="button" onClick={() => { setHandoff(null); resource.refresh(); }}>重试归档读取</button>
    </p>}
    {(stopped || response?.reason === 'analysis_not_precomputed') && OwnerAnalysisJobPanel && selection
      && <Suspense fallback={<p role="status">正在加载后台分析入口…</p>}>
        <OwnerAnalysisJobPanel selection={selection} fingerprint={fingerprint}
          onSucceeded={() => { setJobCompletedFor(fingerprint); setHandoff(null); resource.refresh(); }} />
      </Suspense>}
    {jobCompletedFor === fingerprint && !resource.loading
      && (response?.data === null || resource.error)
      && <p role="status" className="notice">后台计算已成功，但归档结果仍不可用；请显式重试归档读取。</p>}
    {!rankData && <UnavailableModules view="analysis" ids={['ranking']} loading={resource.loading} />}
    {response?.data === null && <p className="notice" role="status">
      {state.adj !== 'raw' ? '当前来源无法证明该复权口径' : '当前区间分析不可用'}
    </p>}
    {response?.data && <>
      <div className="analysis-context">
        <strong>实际区间：{response.data.adjusted_start} 至 {response.data.adjusted_end}</strong>
        <span>{response.data.window_days} 日区间 · 基准收益 {ratio(response.data.benchmark_return)}</span>
        <span>有效 {response.coverage.valid} / 应有 {response.coverage.expected} · 排除 {response.coverage.excluded} · 缺失 {response.coverage.missing}</span>
      </div>
      <div id="workbench-ranking" tabIndex={-1} className="section-heading"><h2>涨幅排行</h2><div>
        <span className="muted">空值不参与有效排序，以 — 展示</span>
        <CsvExportButton
          scope="analysis-current-page"
          label="导出当前页 CSV"
          envelope={response}
          filters={{
            start: response.data.adjusted_start,
            end: response.data.adjusted_end,
            q: state.q,
            sort: sort.requestField,
            order: sort.direction,
            page: state.page,
            page_size: state.page_size,
            adjustment: state.adj,
          }}
          columns={[
            { key: 'rank', header: 'rank' },
            { key: 'security_id', header: 'security_id' },
            { key: 'name', header: 'name' },
            { key: 'topics', header: 'topics' },
            { key: 'return_ratio', header: 'return_ratio' },
            { key: 'excess_return_ratio', header: 'excess_return_ratio' },
            { key: 'max_drawdown_ratio', header: 'max_drawdown_ratio' },
            { key: 'amount_change_ratio', header: 'amount_change_ratio' },
          ]}
          rows={visible.map(item => ({
            snapshot_id: response.snapshot_id,
            ...item,
            topics: item.topics.map(topic => `${topic.topic_id}:${topic.name}`).join('|'),
          }))}
        />
      </div></div>
      <SearchForm value={state.q} onSearch={q => patch({ q, page: 1 })} />
      <TableScroll label="涨幅排行">
        <table aria-label="涨幅排行" className="responsive-record-table analysis-ranking">
          <thead><tr>
            <th>排名</th><th>代码</th><th>名称</th><th>题材</th>
            <ResetSortHead title="区间收益" field="interval_return" state={state} patch={patch} />
            <ResetSortHead title="超额收益" field="excess_return" state={state} patch={patch} />
            <ResetSortHead title="最大回撤" field="maximum_drawdown" state={state} patch={patch} />
            <ResetSortHead title="成交额变化" field="amount_change" state={state} patch={patch} />
            <th>加入比较</th>
          </tr></thead>
          <tbody>{visible.map(item => <tr key={item.security_id}>
            <td data-label="排名">{item.rank ?? '—'}</td>
            <th data-label="代码"><button className="security-button" onClick={event => open(item.security_id, undefined, event.currentTarget)}>{item.security_id}</button>
              {ReviewActions && <ReviewActions snapshot={state.snapshot} security={item.security_id} />}</th>
            <td data-label="名称">{item.name || '—'}</td>
            <td data-label="题材">{item.topics.map(topic => topic.name).join('、') || (item.topic_reasons.length ? '不可用' : '—')}</td>
            <td data-label="区间收益" className="numeric">{ratio(item.return_ratio)}</td>
            <td data-label="超额收益" className="numeric">{ratio(item.excess_return_ratio)}</td>
            <td data-label="最大回撤" className="numeric">{ratio(item.max_drawdown_ratio, false)}</td>
            <td data-label="成交额变化" className="numeric">{ratio(item.amount_change_ratio)}</td>
            <td data-label="加入比较"><label className="check-target"><input type="checkbox" aria-label={`比较 ${item.security_id}`} checked={state.compare.includes(item.security_id)} onChange={() => compare(item.security_id)} /></label></td>
          </tr>)}</tbody>
        </table>
      </TableScroll>
      {filtered.length === 0 && <p className="empty">没有匹配的排行记录。</p>}
      <Pager page={state.page} size={state.page_size} total={filtered.length} change={(page, page_size) => patch({ page, page_size })} />
      <Exclusions items={response.data.exclusions} />
    </>}
  </>;
}
