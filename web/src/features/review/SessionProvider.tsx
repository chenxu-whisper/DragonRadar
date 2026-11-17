import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { PrivateError, privateRequest } from './api';
import { SessionStore } from './session';
import { registerLeaveGuard } from './navigation';
import type { Command, Session } from './types';
import { Pairing } from './Pairing';
import { SessionContext, useSession } from './SessionContext';

export { useSession };

export function SessionProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => new SessionStore());
  const version = useSyncExternalStore(store.subscribe, store.snapshot);
  const [message, setMessage] = useState('');
  const checking = useRef(false);
  const suppressAuth = useRef(false);
  const [revoking, setRevoking] = useState(false);
  const authGeneration = useRef(0);
  const lock = useCallback(() => { authGeneration.current++; checking.current = false; store.lock(); }, [store]);
  const check = useCallback(async () => {
    if (checking.current || suppressAuth.current) return;
    checking.current = true;
    const generation = ++authGeneration.current;
    try {
      const session = await privateRequest<Session>('/session');
      if (generation === authGeneration.current) { store.authenticate(session); setMessage(''); }
    } catch (error) {
      if (generation === authGeneration.current) {
        lock(); setMessage(error instanceof Error ? error.message : '会话检查失败。');
      }
    } finally { if (generation === authGeneration.current) checking.current = false; }
  }, [lock, store]);
  useEffect(() => {
    const focus = () => { store.checkExpiry(); void check(); };
    const visibility = () => { if (document.visibilityState === 'visible') focus(); };
    window.addEventListener('private-session-lock', lock);
    window.addEventListener('private-source-revoked', store.clearFacts);
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', visibility);
    void check();
    return () => {
      authGeneration.current++;
      checking.current = false;
      window.removeEventListener('private-session-lock', lock);
      window.removeEventListener('private-source-revoked', store.clearFacts);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [check, lock, store]);
  useEffect(() => {
    if (!store.session) return;
    const timer = setTimeout(() => store.checkExpiry(), Math.min(2147483647, Math.max(0, Date.parse(store.session.expires_at) - Date.now())));
    return () => clearTimeout(timer);
  }, [store, version]);
  useEffect(() => {
    const release = registerLeaveGuard(() => {
      if (!store.dirty) return true;
      return window.confirm('有未保存输入或结果未知的命令。离开后仍仅暂留本页内存，关闭页面会丢失。确认离开？');
    });
    const unload = (event: BeforeUnloadEvent) => { if (store.dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', unload);
    return () => { release(); window.removeEventListener('beforeunload', unload); };
  }, [store]);
  const call = useCallback(async <T,>(path: string, cmd?: Command, signal?: AbortSignal): Promise<T> => {
    store.checkExpiry();
    if (store.locked) throw new PrivateError(401, 'session_unauthorized');
    const epoch = store.epoch, controller = store.controller();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    try {
      const result = await privateRequest<T>(path, { ...cmd, signal: controller.signal });
      if (store.locked || store.epoch !== epoch) throw new DOMException('过期请求', 'AbortError');
      return result;
    } catch (error) {
      if (store.epoch === epoch && error instanceof PrivateError) {
        if (error.status === 401) lock();
        if (error.code === 'source_forbidden') window.dispatchEvent(new Event('private-source-revoked'));
      }
      throw error;
    } finally { store.release(controller); signal?.removeEventListener('abort', abort); }
  }, [lock, store]);
  const read = useCallback(<T,>(path: string, signal?: AbortSignal) => call<T>(path, undefined, signal), [call]);
  const send = useCallback(<T,>(cmd: Command) => call<T>(cmd.path, cmd), [call]);
  const logout = useCallback(async () => {
    if (!window.confirm(store.dirty ? '退出将放弃未保存输入与未知结果重试信息，并撤销当前会话。确定退出？' : '确定撤销当前会话并退出？')) return;
    suppressAuth.current = true;
    authGeneration.current++;
    checking.current = false;
    store.clear();
    setRevoking(true);
    setMessage('内存已清空，正在撤销当前会话。');
    try {
      await privateRequest('/session', { method: 'DELETE', body: '{}' });
      setMessage('当前会话已撤销，内存已清空。');
    } catch (error) {
      store.clear();
      setMessage(error instanceof PrivateError && error.status === 401 ? '会话已失效，内存已清空。' : '内存已清空，但退出请求未获确认；请重试撤销当前会话。');
    } finally { setRevoking(false); }
  }, [store]);
  return <SessionContext value={{ enabled: true, store, version, read, send, check, logout }}>
    {store.locked && <Pairing message={message} blocked={revoking} onPair={async code => {
      if (revoking) throw new Error('请等待会话撤销完成。');
      suppressAuth.current = true;
      const generation = ++authGeneration.current;
      checking.current = false;
      try {
        await privateRequest('/pair', { method: 'POST', pair: true, body: JSON.stringify({ code }) });
      } finally {
        if (generation === authGeneration.current) suppressAuth.current = false;
      }
      if (generation !== authGeneration.current) return;
      // Only GET /session establishes the authoritative owner and expiry.
      await check();
    }} onRevoke={logout} />}
    <div hidden={store.locked} inert={store.locked}>{children}</div>
  </SessionContext>;
}
