import { useState } from 'react';

export function Pairing({ message, onPair, onRevoke, blocked }: {
  message: string; onPair: (code: string) => Promise<void>; onRevoke: () => Promise<void>; blocked?: boolean;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <main className="pairing">
    <p className="eyebrow">DragonRadar / 本机私人复盘</p>
    <h1>私人会话已锁定</h1>
    <p>正文与行情已隐藏。未提交输入仅暂留本页内存，同一主体重新配对后可恢复；刷新或关闭会丢失。</p>
    <p>仅在证书已信任的本机 HTTPS 入口提交由本机命令生成的一次性配对码。</p>
    <form onSubmit={async event => {
      event.preventDefault();
      if (busy || blocked || !code) return;
      const submitted = code;
      setCode(''); setBusy(true); setError('');
      try { await onPair(submitted); }
      catch (failure) { setError(failure instanceof Error ? failure.message : '配对失败。'); }
      finally { setBusy(false); }
    }}>
      <label>一次性配对码<input type="password" autoComplete="off" spellCheck={false} value={code} onChange={event => setCode(event.target.value)} disabled={busy || blocked} /></label>
      <button className="primary" type="submit" disabled={busy || blocked || !code}>{busy ? '配对中' : '配对'}</button>
    </form>
    {(error || message) && <p role="alert">{error || message}</p>}
    <button disabled={busy || blocked} onClick={() => void onRevoke()}>撤销当前会话并清空内存</button>
  </main>;
}
