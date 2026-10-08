import { useMemo, type ComponentType } from 'react';
import { useResource } from '../../hooks/useResource';
import type { ResearchState, Sort } from '../../state';
import type { ResetRankData, Snapshot, WorkbenchEnvelope } from '../../types';
import { Exclusions, Pager, ResourceStatus, SearchForm, TableScroll, type Patch } from '../../components/Common';
import { IntervalForm } from '../../components/IntervalForm';
import { analysisRange, analysisSortDescriptor } from './query';
import { CsvExportButton } from '../../export/CsvExportButton';

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
  const current = analysisSortDescriptor(state.sort);
  const selected = analysisSortDescriptor(field);
  const active = current.requestField === selected.requestField;
  const order = selected.direction;
  return <th aria-sort={active ? order === 'asc' ? 'ascending' : 'descending' : 'none'}>
    <button className="sort-button" title={`按${title}排序`} onClick={() => patch({ sort: field, order, page: 1 })}>
      {title}
    </button>
  </th>;
}

export function ResetAnalysis({ state, patch, refreshToken, open, compare, snapshot, ReviewActions }: ResetAnalysisProps) {
  let query = '', error = '';
  const sort = analysisSortDescriptor(state.sort);
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
      }).toString();
    }
  } catch (failure) { error = (failure as Error).message; }
  const resource = useResource<WorkbenchEnvelope<ResetRankData | null>>(
    query ? `/api/rank/range?${query}` : null,
    state.snapshot,
    refreshToken,
  );
  const response = resource.data;
  const filtered = useMemo(() => {
    const term = state.q.toLowerCase();
    return (response?.data?.ranking || []).filter(item =>
      `${item.security_id} ${item.name || ''}`.toLowerCase().includes(term));
  }, [response, state.q]);
  const visible = filtered.slice((state.page - 1) * state.page_size, state.page * state.page_size);

  return <>
    <div className="page-heading"><h1>涨幅分析</h1></div>
    <IntervalForm state={state} patch={patch} reset />
    {error && <p role="alert" className="notice error">{error}</p>}
    <ResourceStatus resource={resource} name="涨幅排行" />
    {response?.data === null && <p className="notice" role="status">
      {state.adj !== 'raw' ? '当前来源无法证明该复权口径' : '当前区间分析不可用'}
    </p>}
    {response?.data && <>
      <div className="analysis-context">
        <strong>实际区间：{response.data.adjusted_start} 至 {response.data.adjusted_end}</strong>
        <span>{response.data.window_days} 日区间 · 基准收益 {ratio(response.data.benchmark_return)}</span>
        <span>有效 {response.coverage.valid} / 应有 {response.coverage.expected} · 排除 {response.coverage.excluded} · 缺失 {response.coverage.missing}</span>
      </div>
      <div className="section-heading"><h2>涨幅排行</h2><div>
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
