import { useEffect, useRef, useState } from 'react';
import type { Draft } from './draft';
import { ContentFields, ContentText } from './Controls';
import type { NoteContent } from './types';
import { useSession } from './SessionProvider';
import { contentFieldErrors, errorSummary, type ContentField, type ContentFieldErrors } from './validation';

export function ConflictDialog({ draft, close, merge, fork, trigger }: {
  draft: Draft; close: () => void; merge: (content: NoteContent) => void; fork: () => void; trigger: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { store } = useSession();
  const content = draft.mergeInput || draft.content;
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<ContentFieldErrors>({});
  const fields = useRef<Partial<Record<ContentField, HTMLInputElement | HTMLTextAreaElement | null>>>({});
  useEffect(() => {
    dialog.current?.showModal();
    return () => { dialog.current?.close(); if (trigger?.isConnected) trigger.focus(); };
  }, [trigger]);
  return <dialog className="review-dialog" ref={dialog} aria-labelledby="conflict-title" onCancel={event => { event.preventDefault(); close(); }}>
    <div className="section-heading"><h2 id="conflict-title">版本冲突</h2><button autoFocus onClick={close}>关闭对照</button></div>
    <p>原输入未被覆盖。手工合并基于服务端新版，另存则保留原基线的来源修订。</p>
    <div className="conflict-columns">
      <section><h3>原基线</h3><p>版本 {draft.baseline.version}</p><ContentText content={draft.baseline.content} /></section>
      <section><h3>当前输入</h3><ContentText content={draft.content} /></section>
      <section><h3>服务端最新版</h3><p>版本 {draft.current?.version}</p>{draft.current && <ContentText content={draft.current.content} />}</section>
    </div>
    <h3>手工合并</h3>
    <ContentFields prefix="合并" idPrefix="merge-content" value={content} errors={fieldErrors}
      fieldRef={(field, element) => { fields.current[field] = element; }}
      onChange={value => { draft.editMerge(value); setFieldErrors({}); setError(''); store.changed(); }}
      disabled={draft.current?.state === 'confirmed'} />
    {error && <p role="alert">{error}</p>}
    <div className="toolbar">
      <button className="primary" disabled={draft.current?.state === 'confirmed'} onClick={() => {
        const errors = contentFieldErrors(content);
        if (Object.keys(errors).length) {
          setFieldErrors(errors);
          setError(errorSummary(errors));
          const first = (['title', 'judgment', 'evidence', 'invalidation', 'verification_date'] as const).find(field => errors[field]?.length);
          requestAnimationFrame(() => { if (first) fields.current[first]?.focus(); });
        } else merge(content);
      }}>采用手工合并</button>
      <button onClick={fork}>保留原输入另存</button>
      <button onClick={close}>取消</button>
    </div>
    {draft.current?.state === 'confirmed' && <p>服务端已确认，不能原地合并；可保留原输入另存。</p>}
  </dialog>;
}
