import { useEffect, useRef, useState } from 'react';
import { ApiError, requestGeneration } from '../../api';
import { useAccess, type Resource } from '../../hooks/useResource';
import { useSession } from '../review/SessionContext';
import { requestRealtime } from './client';

export function useRealtimeResource<T>(
  path: string | null,
  refreshToken = 0,
): Resource<T> {
  const { denied, revoke, epoch } = useAccess();
  const { enabled, store } = useSession();
  const sessionEpoch = enabled ? store.epoch : 0;
  const locked = enabled && store.locked;
  const [count, setCount] = useState(0);
  const [result, setResult] = useState<{
    key: string | null;
    data?: T;
    error?: ApiError;
    loading: boolean;
  }>({ key: null, loading: false });
  const gate = useRef(requestGeneration());
  const key = path && !denied && !locked ? `${epoch}:${sessionEpoch}:${path}` : null;

  useEffect(() => {
    const generation = gate.current.next();
    const controller = new AbortController();
    if (!key || !path) {
      setResult({ key: null, loading: false });
      return;
    }
    setResult(previous => ({
      key,
      data: previous.key === key ? previous.data : undefined,
      error: previous.key === key ? previous.error : undefined,
      loading: true,
    }));
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      return requestRealtime<T>(path, { signal: controller.signal });
    }).then(data => {
      if (gate.current.isCurrent(generation)) setResult({ key, data, loading: false });
    }).catch((error: unknown) => {
      if (!gate.current.isCurrent(generation)
        || (error instanceof DOMException && error.name === 'AbortError')) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      if (enabled && failure.status === 401) {
        window.dispatchEvent(new Event('private-session-lock'));
        setResult({ key: null, loading: false });
        return;
      }
      if (failure.status === 403 && failure.code === 'FORBIDDEN') {
        setResult({ key, error: failure, loading: false });
        return;
      }
      if (failure.status === 403
        && (!enabled || failure.code === 'source_forbidden' || !failure.code)) {
        revoke();
        if (enabled) window.dispatchEvent(new Event('private-source-revoked'));
        setResult({ key: null, loading: false });
        return;
      }
      const retain = failure.code !== 'malformed_response'
        && (failure.status === 0 || failure.status === 408 || failure.status >= 500);
      setResult(previous => ({
        key,
        data: retain && previous.key === key ? previous.data : undefined,
        error: failure,
        loading: false,
      }));
    });
    return () => {
      gate.current.next();
      controller.abort();
    };
  }, [path, key, count, refreshToken, revoke, enabled]);

  const current = result.key === key && key !== null && !denied && !locked;
  return {
    data: current ? result.data : undefined,
    error: current ? result.error : undefined,
    loading: Boolean(key) && (!current || result.loading),
    refresh: () => setCount(value => value + 1),
  };
}
