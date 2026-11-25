import { useEffect, useRef, useState } from 'react';
import { ArchiveReadError, readCitationSource, type PublicCitation } from './api';

export default function PublicCitationSource({
  snapshotId, date, archiveId, citation, returnFocus, onClose,
}: {
  snapshotId: string; date: string; archiveId: string; citation: PublicCitation;
  returnFocus: HTMLElement; onClose: () => void;
}) {
  const [source, setSource] = useState<string | number | boolean>();
  const [error, setError] = useState('');
  const closeButton = useRef<HTMLButtonElement>(null);
  const identity = JSON.stringify([snapshotId, date, archiveId, citation]);
  const [accepted, setAccepted] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    closeButton.current?.focus();
    setSource(undefined); setError(''); setAccepted('');
    const close = () => { controller.abort(); setSource(undefined); onClose(); returnFocus.focus(); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); close(); }
    };
    window.addEventListener('keydown', escape);
    window.addEventListener('dragon-source-revoked', close);
    readCitationSource(snapshotId, date, archiveId, citation, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setSource(value.fragment); setAccepted(identity);
    }).catch(failure => {
      if (controller.signal.aborted) return;
      setSource(undefined);
      setError('引用来源不可用，原文字段已清空。');
      if (failure instanceof ArchiveReadError && failure.status === 403) {
        window.dispatchEvent(new Event('dragon-source-revoked'));
      }
    });
    return () => {
      controller.abort();
      window.removeEventListener('keydown', escape);
      window.removeEventListener('dragon-source-revoked', close);
    };
  }, [archiveId, citation, date, identity, onClose, returnFocus, snapshotId]);
  return <section aria-label="公开引用原文" className="public-daily-ai__source" tabIndex={-1}>
    <h4>精确 PUB 原文字段</h4>
    <button type="button" ref={closeButton} onClick={() => { onClose(); returnFocus.focus(); }}>关闭原文</button>
    <p className="public-daily-ai__identity">PUB {snapshotId} · {citation.source_identity}</p>
    <p className="public-daily-ai__identity">{citation.locator.position}</p>
    {error ? <p role="alert">{error}</p> : accepted === identity
      ? <pre><mark>{String(source)}</mark></pre>
      : <p role="status">正在重新核对公开归档与来源显示授权…</p>}
    <p>高亮实际归档字段的完整值，不推断原文段落范围。</p>
  </section>;
}
