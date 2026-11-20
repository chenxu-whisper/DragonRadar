import { useEffect, useRef, useState } from 'react';
import type { AICitation, AITask } from './client';
import { isObject, isUUID, OwnerReadError, ownerRequest, readSession } from '../news/owner-api';

interface Source {
  source_identity: string;
  available_at: string;
  value: unknown;
  status: 'ready' | 'unavailable';
  locator: {
    kind: string; field: string; index: number | null;
    article_id?: string; article_revision_id?: string; article_revision?: number;
  } | null;
}

export default function CitationSource({ task, blocked = false, articleId }: {
  task: AITask; blocked?: boolean; articleId?: string;
}) {
  const [selected, setSelected] = useState<AICitation>();
  const [source, setSource] = useState<Source>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useRef<AbortController | undefined>(undefined);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const close = () => {
    operation.current?.abort();
    operation.current = undefined;
    setSelected(undefined); setSource(undefined); setError(''); setBusy(false);
    trigger.current?.focus();
  };
  const context = JSON.stringify([
    task.task_id, task.snapshot_id, task.revision, task.human_review_status,
    task.content_draft?.citations, blocked, articleId,
  ]);
  useEffect(() => {
    close();
    const clear = () => close();
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && operation.current) {
        event.stopPropagation(); close();
      }
    };
    window.addEventListener('private-session-lock', clear);
    window.addEventListener('private-source-revoked', clear);
    window.addEventListener('keydown', escape);
    return () => {
      operation.current?.abort();
      window.removeEventListener('private-session-lock', clear);
      window.removeEventListener('private-source-revoked', clear);
      window.removeEventListener('keydown', escape);
    };
  }, [context]);

  const open = async (citation: AICitation, button: HTMLButtonElement) => {
    if (blocked || task.revision === null || task.human_review_status === 'rejected') return;
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    trigger.current = button;
    setSelected(citation); setSource(undefined); setError(''); setBusy(true);
    try {
      const session = await readSession(controller.signal);
      const value = await ownerRequest(
        `/api/ai/task/${task.task_id}/evidence/${citation.evidence_id}?revision=${task.revision}&snapshot_id=${task.snapshot_id}`,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const mismatch = () => { throw new OwnerReadError(502, '引用与所有者、任务、草稿修订或实际字段不一致。'); };
      if (!isObject(value) || value.owner_id !== session.owner_id
        || value.task_id !== task.task_id || value.revision !== task.revision
        || value.snapshot_id !== task.snapshot_id || Date.parse(String(value.as_of)) !== Date.parse(task.as_of)
        || value.version !== task.version || value.evidence_id !== citation.evidence_id
        || value.source_identity !== citation.source_identity
        || value.evidence_position !== citation.evidence_position
        || value.available_at !== citation.available_at
        || value.unit !== citation.unit || value.precision !== citation.precision) {
        mismatch();
      }
      const data = value as Record<string, unknown>;
      if (data.status === 'unavailable') {
        if (data.reason !== 'legacy_locator_unavailable' || data.locator !== null || data.value !== null) mismatch();
      } else if (data.status === 'ready') {
        if (data.reason !== null || JSON.stringify(data.value) !== JSON.stringify(citation.value)
          || !isObject(data.locator) || typeof data.locator.field !== 'string'
          || (data.locator.index !== null && (!Number.isSafeInteger(data.locator.index) || Number(data.locator.index) < 0))) mismatch();
        const locator = data.locator as Record<string, unknown>;
        if (locator.kind === 'news_article_field') {
          if (!isUUID(locator.article_id) || !isUUID(locator.article_revision_id)
            || !Number.isSafeInteger(locator.article_revision) || Number(locator.article_revision) < 1
            || (articleId !== undefined && locator.article_id !== articleId)) mismatch();
          const position = citation.evidence_position.replace(/^\/input\/(?:article|articles\/\d+)\//, '');
          const field = `${locator.field}${locator.index !== null ? `/${locator.index}` : ''}`;
          if (position === citation.evidence_position || field !== position) mismatch();
        } else if (locator.kind === 'publication_field') {
          if (locator.field !== citation.evidence_position || locator.index !== null) mismatch();
        } else mismatch();
      } else mismatch();
      setSource(data as unknown as Source);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setSource(undefined);
        setError(failure instanceof Error ? failure.message : '引用来源不可用');
      }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <>
    <details><summary>引用与来源</summary><ul>
      {task.content_draft?.citations.map((item, index) => <li key={`${item.evidence_id}:${index}`}>
        {item.source_identity} · {item.evidence_position} · {item.available_at}
        <button disabled={blocked || task.human_review_status === 'rejected' || task.revision === null}
          onClick={event => void open(item, event.currentTarget)}>查看引用 {item.evidence_id}</button>
      </li>)}
    </ul></details>
    {selected && <section aria-label="引用来源">
      <h4>引用来源</h4>
      <button onClick={close}>关闭引用来源</button>
      {busy && <p>正在核对来源显示授权</p>}
      {error && <p role="alert">{error}</p>}
      {source?.status === 'unavailable' && <p>旧输入不可定位：缺少不可变文章标识。</p>}
      {source?.status === 'ready' && source.locator && <>
        <p>{source.source_identity} · {source.available_at}</p>
        {source.locator.kind === 'news_article_field' && <p>
          文章 {source.locator.article_id} · 修订 {source.locator.article_revision_id}
          {' · '}第 {source.locator.article_revision} 版
        </p>}
        <p>实际字段 {source.locator.field}
          {source.locator.index !== null ? ` · 索引 ${source.locator.index}` : ''}
          {' · '}字段完整值，不推断原文段落范围</p>
        <pre><mark>{typeof source.value === 'string' ? source.value : JSON.stringify(source.value)}</mark></pre>
      </>}
    </section>}
  </>;
}
