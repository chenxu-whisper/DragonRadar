import { useCallback, useEffect, useState } from 'react';
import { useSession } from './SessionProvider';
import { PrivateError } from './api';
import { useAccess } from '../../hooks/useResource';

export function usePrivate<T>(path: string | null, refreshToken = 0) {
  const { store, read } = useSession();
  const { epoch: sourceEpoch, denied } = useAccess();
  const [count, setCount] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: T; error?: string }>({ key: '' });
  const key = !store.locked && path ? `${store.epoch}:${sourceEpoch}:${denied}:${path}:${refreshToken}:${count}` : '';
  useEffect(() => {
    if (!key || !path) { setResult({ key: '' }); return; }
    const controller = new AbortController();
    void read<T>(path, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ key, data });
    }).catch(error => {
      if (!controller.signal.aborted) setResult({ key, error: error instanceof PrivateError ? error.message : '读取未完成，请重试。' });
    });
    return () => controller.abort();
  }, [key, path, read]);
  return {
    data: result.key === key && key ? result.data : undefined,
    error: result.key === key && key ? result.error : undefined,
    loading: Boolean(key) && result.key !== key,
    refresh: useCallback(() => setCount(value => value + 1), []),
  };
}
