import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { timestamp } from '../format';
import { useResource, useAccess } from '../hooks/useResource';
import type { ResearchState } from '../state';
import type { Snapshot } from '../types';
import { ResourceStatus, type Patch } from './Common';

export function versionName(snapshot: Snapshot): string {
  return `${snapshot.result.revision <= 1 ? '首版' : `修订第 ${snapshot.result.revision} 版`} · ${snapshot.provenance === 'retrospective' ? '回溯' : '当时记录'} · ${timestamp(snapshot.result.published_at)}`;
}
export function SnapshotHeader({ state, snapshot, patch, refresh, refreshing, refreshToken }: {
  state: ResearchState; snapshot?: Snapshot; patch: Patch; refresh: () => void; refreshing: boolean; refreshToken: number;
}) {
  const { denied } = useAccess();
  const [offset, setOffset] = useState(0);
  const date = state.date || snapshot?.trade_date || '';
  useEffect(() => setOffset(0), [date]);
  const versions = useResource<Snapshot[]>(date && !denied ? `/api/v1/snapshots?trade_date=${date}&limit=100&offset=${offset}` : null, undefined, refreshToken);
  const options = versions.data || [];
  const includeCurrent = snapshot && !options.some(item => item.snapshot_id === snapshot.snapshot_id);
  return <header className="snapshot-header" aria-label="快照上下文">
    <div className="snapshot-controls">
      <label>交易日期<input type="date" value={date} onChange={e => patch({ date: e.target.value, snapshot: '', security: '', compare: [], page: 1 })} /></label>
      <label className="version-control">快照版本<select aria-label="快照版本" disabled={!snapshot || denied} value={snapshot?.snapshot_id || ''} onChange={e => patch({ snapshot: e.target.value, security: '', compare: [], page: 1 })}>
        {!snapshot && <option value="">待选择快照</option>}
        {includeCurrent && <option value={snapshot.snapshot_id}>{versionName(snapshot)}</option>}
        {options.map(item => <option key={item.snapshot_id} value={item.snapshot_id}>{versionName(item)}</option>)}
      </select></label>
      <button onClick={refresh} title="重新读取当前快照与研究数据" aria-label="刷新快照"><RefreshCw size={17} />{refreshing ? '刷新中' : '刷新'}</button>
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
