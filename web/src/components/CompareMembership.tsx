import { useCallback, useEffect, useState } from 'react';
import { useResource } from '../hooks/useResource';
import { parseState, type ResearchState } from '../state';
import type { Detail, Envelope } from '../types';
import { ResourceStatus, type Patch } from './Common';

function MemberCheck({ id, snapshot, refreshToken, missing }: {
  id: string; snapshot: string; refreshToken: number; missing: (id: string, snapshot: string) => void;
}) {
  const resource = useResource<Envelope<Detail>>(
    `/api/v1/snapshots/${snapshot}/securities/${encodeURIComponent(id)}`, snapshot, refreshToken,
  );
  useEffect(() => {
    if (resource.error?.status === 404) missing(id, snapshot);
  }, [resource.error, id, snapshot, missing]);
  return resource.error && resource.error.status !== 404
    ? <ResourceStatus resource={resource} name={`比较标的 ${id}`} />
    : null;
}

export function CompareMembership({ state, patch, refreshToken, allowPrivate = false }: {
  state: ResearchState; patch: Patch; refreshToken: number; allowPrivate?: boolean;
}) {
  const [removed, setRemoved] = useState<string[]>([]);
  const missing = useCallback((id: string, snapshot: string) => {
    // Concurrent responses must remove from the latest selection, never a captured list.
    const current = parseState(window.location.search, allowPrivate).state;
    if (current.snapshot !== snapshot || !current.compare.includes(id)) return;
    setRemoved(previous => [...new Set([...previous, id])]);
    patch({ compare: current.compare.filter(value => value !== id) }, true);
  }, [allowPrivate, patch]);
  return <>
    {removed.length > 0 && <div role="alert" className="notice">
      <span>已移除不在快照证券池中的比较标的：{removed.join('、')}。可重新选择，研究区间保持不变。</span>
      <button onClick={() => setRemoved([])}>关闭比较校验提示</button>
    </div>}
    {state.compare.map(id => <MemberCheck key={`${state.snapshot}:${id}`} id={id} snapshot={state.snapshot} refreshToken={refreshToken} missing={missing} />)}
  </>;
}
