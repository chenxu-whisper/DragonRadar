import { useState } from 'react';
import { useAccess, useResource } from '../../hooks/useResource';
import type { Detail, Envelope } from '../../types';
import { formatDecimal } from '../../format';
import type { Page, Watch } from './types';
import { command } from './draft';
import { usePrivate } from './usePrivate';
import { useCommand } from './useCommand';
import { PrivatePager, ReadStatus } from './Controls';

function WatchRow({ entry, snapshot, refresh }: { entry: Watch; snapshot: string; refresh: () => void }) {
  const { denied } = useAccess();
  const resource = useResource<Envelope<Detail>>(snapshot && !denied ? `/api/v1/snapshots/${snapshot}/securities/${encodeURIComponent(entry.security_id)}` : null, snapshot || undefined);
  const mutation = useCommand<Watch>(`watch:${entry.id}`);
  const [updated, setUpdated] = useState<Watch>();
  const current = updated && updated.version > entry.version ? updated : entry;
  const conflict = mutation.conflict as Watch | undefined;
  const setActive = (active: boolean, value = current) => void mutation.execute(
    () => command('PATCH', `/watchlist/${value.id}`, { active }, value.version),
    item => { setUpdated(item); refresh(); },
  );
  const quote = resource.data?.result.security;
  return <tr>
    <th>{entry.security_id}<small>{denied ? '来源不可用' : quote?.name || '本快照无行情'}</small></th>
    <td>{denied ? '来源不可用' : quote ? formatDecimal(quote.close) : '本快照无行情'}</td>
    <td>{current.active ? '自选中' : '已移除'} · v{current.version}</td>
    <td>
      <button disabled={mutation.busy} onClick={() => setActive(!current.active)}>
        {mutation.pending ? '重试原命令' : current.active ? `移除 ${entry.security_id}` : `撤销移除 ${entry.security_id}`}
      </button>
      {mutation.error && <p role="alert">{mutation.error}</p>}
      {conflict && <p>服务端版本 {conflict.version}：{conflict.active ? '自选中' : '已移除'}。<button onClick={() => {
        setUpdated(conflict); mutation.clearFeedback(); refresh();
      }}>采用最新状态</button></p>}
    </td>
  </tr>;
}
export function Watchlist({ snapshot, refreshToken }: { snapshot: string; refreshToken: number }) {
  const [page, setPage] = useState(1), [size, setSize] = useState(50);
  const list = usePrivate<Page<Watch>>(`/watchlist?page=${page}&page_size=${size}`, refreshToken);
  return <section aria-label="我的自选">
    <div className="section-heading"><h2>自选</h2><button onClick={list.refresh}>刷新自选</button></div>
    <p className="muted">包含已移除条目。名称与价格仅取当前选定可读快照，不沿用加入时缓存。</p>
    <ReadStatus loading={list.loading} error={list.error} retry={list.refresh} />
    {list.data && <><div className="table-scroll"><table aria-label="自选列表"><thead><tr><th>证券 / 当前名称</th><th>当前快照收盘</th><th>状态 / 版本</th><th>操作</th></tr></thead>
      <tbody>{list.data.items.map(entry => <WatchRow key={entry.id} entry={entry} snapshot={snapshot} refresh={list.refresh} />)}</tbody>
    </table></div>{!list.data.items.length && <p>暂无自选。可从行情列表加入。</p>}
      <PrivatePager name="自选" page={page} size={size} more={list.data.has_more} change={(p, s) => { setPage(p); setSize(s); }} /></>}
  </section>;
}
