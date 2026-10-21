import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import { timestamp } from '../format';
import { useResource, useAccess } from '../hooks/useResource';
import type { ResearchState } from '../state';
import type { ResetSnapshotData, Snapshot, WorkbenchEnvelope } from '../types';
import { ResourceStatus, type Patch } from './Common';

// 偏好控件为独立懒 chunk，仅 reset 工作台渲染时才拉取。
const PreferenceControls = lazy(async () => ({
  default: (await import('../features/preferences/PreferenceControls')).PreferenceControls,
}));
// 等尺寸（44px 高）占位，避免 chunk 加载完成前控件区布局跳动。
const preferenceFallback = <>
  <span className="pref-toggle theme-toggle pref-toggle-placeholder" aria-hidden="true" />
  <span className="pref-toggle density-toggle pref-toggle-placeholder" aria-hidden="true" />
</>;

export function versionName(snapshot: Snapshot): string {
  return `${snapshot.result.revision <= 1 ? '首版' : `修订第 ${snapshot.result.revision} 版`} · ${snapshot.provenance === 'retrospective' ? '回溯' : '当时记录'} · ${timestamp(snapshot.result.published_at)}`;
}
export function SnapshotHeader({ state, snapshot, patch, refresh, refreshing, refreshToken, reset = false, paletteTrigger }: {
  state: ResearchState; snapshot?: Snapshot; patch: Patch; refresh: () => void; refreshing: boolean; refreshToken: number; reset?: boolean;
  paletteTrigger?: ReactNode;
}) {
  const { denied } = useAccess();
  const [offset, setOffset] = useState(0);
  const date = state.date || snapshot?.trade_date || '';
  useEffect(() => setOffset(0), [date]);
  const versions = useResource<Snapshot[] | WorkbenchEnvelope<{ snapshots: WorkbenchEnvelope<ResetSnapshotData>[] }>>(
    date && !denied
      ? reset ? `/api/snapshots/list?date=${date}&limit=100&offset=${offset}` : `/api/v1/snapshots?trade_date=${date}&limit=100&offset=${offset}`
      : null,
    undefined,
    refreshToken,
  );
  const options: Snapshot[] = versions.data
    ? Array.isArray(versions.data) ? versions.data : versions.data.data.snapshots.map(item => ({
      snapshot_id: item.snapshot_id, trade_date: item.trade_date, as_of: item.as_of, source_time: null,
      source: { id: item.source, version: item.source_version },
      coverage: { ...item.coverage, legal_excluded: item.coverage.excluded },
      coverage_scope: 'snapshot_current', analysis_coverage: null,
      units: { currency: item.basis.currency ? [item.basis.currency] : [], price_unit: [], volume_unit: [], amount_unit: [] },
      quality_flags: item.quality_flags, rule_version: item.rule_version, basis: 'published_market',
      manifest: { rule_version: item.rule_version }, provenance: 'contemporaneous',
      result: {
        ...item.data,
        calendar_sessions: item.data.calendar_sessions || [],
        earliest_date: item.data.earliest_date || null,
      },
    }))
    : [];
  const includeCurrent = snapshot && !options.some(item => item.snapshot_id === snapshot.snapshot_id);
  return <header className="snapshot-header" aria-label="快照上下文">
    <div className="snapshot-controls">
      <label>交易日期<input id="trade-date-input" type="date" value={date} onChange={e => patch({ date: e.target.value, snapshot: '', security: '', compare: [], page: 1 })} /></label>
      <label className="version-control">快照版本<select aria-label="快照版本" disabled={!snapshot || denied} value={snapshot?.snapshot_id || ''} onChange={e => patch({ snapshot: e.target.value, security: '', compare: [], page: 1 })}>
        {!snapshot && <option value="">待选择快照</option>}
        {includeCurrent && <option value={snapshot.snapshot_id}>{versionName(snapshot)}</option>}
        {options.map(item => <option key={item.snapshot_id} value={item.snapshot_id}>{versionName(item)}</option>)}
      </select></label>
      <button onClick={refresh} title="重新读取当前快照与研究数据" aria-label="刷新快照"><RefreshCw size={17} />{refreshing ? '刷新中' : '刷新'}</button>
      {!denied && paletteTrigger}
      {reset && <Suspense fallback={preferenceFallback}><PreferenceControls /></Suspense>}
    </div>
    {(offset > 0 || options.length === 100) && <div className="version-pages">
      <span>该日期版本第 {offset / 100 + 1} 页（每页最多 100 条）</span>
      <button disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - 100))}>上一组版本</button>
      <button disabled={options.length < 100 || offset >= 10000} onClick={() => setOffset(v => v + 100)}>下一组版本</button>
    </div>}
    {!denied && <ResourceStatus resource={versions} name="版本列表" />}
    <div className="snapshot-meta" aria-live="polite">
      {snapshot && !denied ? <>
        <span><strong>数据日期</strong> {snapshot.trade_date}</span>
        <span><strong>版本性质</strong> {snapshot.result.revision <= 1 ? '首版' : `修订第 ${snapshot.result.revision} 版`} · {snapshot.provenance === 'retrospective' ? '回溯记录' : '当时记录'}</span>
        <span><strong>来源</strong> {snapshot.source.id} / {snapshot.source.version}</span>
        <span><strong>快照覆盖</strong> 有效 {snapshot.coverage.valid} / 应有 {snapshot.coverage.expected}</span>
        <span>合法排除 {snapshot.coverage.legal_excluded} · 缺失 {snapshot.coverage.missing}</span>
        <span><strong>统计截至</strong> {timestamp(snapshot.as_of)}（北京时间）</span>
        <span><strong>来源时间</strong> {timestamp(snapshot.source_time)}</span>
      </> : <span>{denied ? '来源内容已清除' : '尚未载入匹配快照，来源与覆盖未知'}</span>}
    </div>
  </header>;
}
