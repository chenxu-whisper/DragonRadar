import { useRef, useState } from 'react';
import { PrivateError } from './api';

const resetOwner = import.meta.env.VITE_RESET_WORKBENCH === '1'
  && import.meta.env.VITE_PRIVATE_REVIEW === '1';

export function Pairing({ message, onPair, onRevoke, blocked }: {
  message: string; onPair: (code: string) => Promise<void>; onRevoke: () => Promise<void>; blocked?: boolean;
}) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState('');
  return <main className="pairing">
    <p className="eyebrow">DragonRadar / 本机私人复盘</p>
    <h1>私人会话已锁定</h1>
    <p>正文与行情已隐藏。未提交输入仅暂留本页内存，同一主体重新配对后可恢复；刷新或关闭会丢失。</p>
    <p>仅在证书已信任的本机 HTTPS 入口提交由本机命令生成的一次性配对码。</p>
    {resetOwner && <section aria-label="本机配对步骤">
      <h2>本机配对步骤</h2>
      <ol>
        <li>在项目根目录运行 <code>{'powershell.exe -NoProfile -File .\\scripts\\reset-owner.ps1 -Action Pair'}</code>。</li>
        <li>按命令输出的受保护文件路径，手动打开 <code>{'.temp/reset-secrets/pair-<随机标识>.txt'}</code>，查看配对码。</li>
        <li>回到证书已信任的 <a href={window.location.origin}>当前 Owner 入口</a>（<code>{window.location.origin}</code>），在下方手动输入配对码并提交。</li>
      </ol>
      <p>配对码有效期为 5 分钟，仅可使用一次。不要将配对码复制到剪贴板、发送到聊天、放入 URL 或写入共享输出，也不要发送给他人或输入到公共入口。浏览器不会读取配对文件，也不会自动签发或提交配对码。</p>
    </section>}
    <form onSubmit={async event => {
      event.preventDefault();
      if (submitting.current || busy || blocked || !code.trim()) return;
      const submitted = code;
      submitting.current = true;
      setCode(''); setBusy(true); setError('');
      try { await onPair(submitted); }
      catch (failure) {
        setError(resetOwner && failure instanceof PrivateError && failure.code === 'invalid_pairing'
          ? '配对未完成。输入已清空，请运行上述命令重新生成配对码后重试。'
          : failure instanceof Error ? failure.message : '配对失败。');
      }
      finally { submitting.current = false; setBusy(false); }
    }}>
      <label>一次性配对码<input type="password" autoComplete="off" spellCheck={false} value={code} onChange={event => setCode(event.target.value)} disabled={busy || blocked} /></label>
      <button className="primary" type="submit" disabled={busy || blocked || !code.trim()}>{busy ? '配对中' : '配对'}</button>
    </form>
    {(error || message) && <p role="alert">{error || message}</p>}
    <button disabled={busy || blocked} onClick={() => void onRevoke()}>撤销当前会话并清空内存</button>
  </main>;
}
