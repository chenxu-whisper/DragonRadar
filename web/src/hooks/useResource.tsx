import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, request, requestGeneration } from '../api';

interface Access { denied: boolean; revoke: () => void; retry: () => void; epoch: number }
const AccessContext = createContext<Access>({ denied: false, revoke: () => {}, retry: () => {}, epoch: 0 });
export function AccessProvider({ children }: { children: ReactNode }) {
  const [denied, setDenied] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const revoke = useCallback(() => setDenied(true), []);
  const retry = useCallback(() => { setEpoch(v => v + 1); setDenied(false); }, []);
  return <AccessContext value={{ denied, revoke, retry, epoch }}>{children}</AccessContext>;
}
export const useAccess = () => useContext(AccessContext);
export interface Resource<T> { data?: T; error?: ApiError; loading: boolean; refresh: () => void }
export function useResource<T>(path: string | null, snapshot?: string, refreshToken = 0): Resource<T> {
  const { denied, revoke, epoch } = useAccess();
  const [count, setCount] = useState(0);
  const [result, setResult] = useState<{ key: string | null; data?: T; error?: ApiError; loading: boolean }>({ key: null, loading: false });
  const gate = useRef(requestGeneration());
  const key = path && !denied ? `${epoch}:${snapshot || ''}:${path}` : null;
  useEffect(() => {
    const generation = gate.current.next();
    const controller = new AbortController();
    if (!key || !path) { setResult({ key: null, loading: false }); return; }
    setResult(previous => ({ key, data: previous.key === key ? previous.data : undefined, error: previous.key === key ? previous.error : undefined, loading: true }));
    // Let effect cleanup cancel superseded work before starting network I/O, including StrictMode replay.
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      return request<T>(path, { snapshot, signal: controller.signal });
    }).then(data => {
      if (gate.current.isCurrent(generation)) setResult({ key, data, loading: false });
    }).catch((error: unknown) => {
      if (!gate.current.isCurrent(generation) || (error instanceof DOMException && error.name === 'AbortError')) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      if (failure.status === 403) { revoke(); setResult({ key: null, loading: false }); return; }
      const retain = failure.status === 0 || failure.status === 408 || failure.status >= 500;
      setResult(previous => ({ key, data: retain && previous.key === key ? previous.data : undefined, error: failure, loading: false }));
    });
    return () => { gate.current.next(); controller.abort(); };
  }, [path, key, snapshot, count, refreshToken, revoke]);
  const current = result.key === key && key !== null && !denied;
  return {
    data: current ? result.data : undefined, error: current ? result.error : undefined,
    loading: Boolean(key) && (!current || result.loading), refresh: () => setCount(v => v + 1),
  };
}
