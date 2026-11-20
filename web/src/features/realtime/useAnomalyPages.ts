import { useEffect, useRef, useState } from 'react';
import { useAccess, type Resource } from '../../hooks/useResource';
import { useSession } from '../review/SessionContext';
import { AnomalyPages, type AnomalyPageState } from './anomaly-pages';
import { requestRealtime } from './client';
import type { AnomalyResponse, AnomalySelection } from './types';

export function useAnomalyPages(
  selection: AnomalySelection | null,
  context: string,
  refreshToken: number,
  blocked: boolean,
): Resource<AnomalyResponse> & { hasMore: boolean; loadMore: () => void } {
  const { denied, epoch, revoke } = useAccess();
  const { enabled, store } = useSession();
  const [count, setCount] = useState(0);
  const key = selection && !denied && !(enabled && store.locked) && !blocked
    ? JSON.stringify([selection, context, epoch, enabled ? store.epoch : 0, refreshToken, count])
    : null;
  const [result, setResult] = useState<{ key: string | null; state: AnomalyPageState }>({
    key: null, state: { loading: false, hasMore: false },
  });
  const active = useRef<AnomalyPages | null>(null);
  useEffect(() => {
    const chain = new AnomalyPages(requestRealtime, () => {
      const failure = chain.state.error;
      if (enabled && failure?.status === 401) {
        window.dispatchEvent(new Event('private-session-lock'));
      } else if (failure?.status === 403 && failure.code !== 'FORBIDDEN'
        && (!enabled || failure.code === 'source_forbidden' || !failure.code)) {
        revoke();
        if (enabled) window.dispatchEvent(new Event('private-source-revoked'));
      }
      setResult({ key, state: chain.state });
    });
    active.current = chain;
    chain.reset(key, key ? selection : null);
    void Promise.resolve().then(() => chain.load());
    return () => {
      active.current = null;
      chain.reset(null, null);
    };
  }, [key, enabled, revoke]);
  const current = key !== null && result.key === key;
  return {
    data: current ? result.state.data : undefined,
    error: current ? result.state.error : undefined,
    loading: Boolean(key) && (!current || result.state.loading),
    hasMore: current && result.state.hasMore,
    loadMore: () => { if (current) void active.current?.load(); },
    refresh: () => setCount(value => value + 1),
  };
}
