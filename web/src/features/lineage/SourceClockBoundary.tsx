import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';

const DenialContext = createContext<((status: 401 | 403) => void) | null>(null);
export const useSourceClockDenial = () => useContext(DenialContext);

// Unmount the owning ledger, including its pending reads and history dialogs.
export function SourceClockBoundary({ children, label }: { children: ReactNode; label: string }) {
  const [denial, setDenial] = useState<401 | 403>();
  const deny = useCallback((status: 401 | 403) => setDenial(status), []);
  useEffect(() => {
    const locked = () => deny(401);
    window.addEventListener('private-session-lock', locked);
    return () => window.removeEventListener('private-session-lock', locked);
  }, [deny]);
  if (denial) return <section aria-label={label}>
    <p role="alert">{denial === 401 ? '所有者会话已失效' : '来源授权已撤销'}，已清除受保护证据。</p>
    <button type="button" onClick={() => setDenial(undefined)}>重新检查来源与会话</button>
  </section>;
  return <DenialContext value={deny}>{children}</DenialContext>;
}
