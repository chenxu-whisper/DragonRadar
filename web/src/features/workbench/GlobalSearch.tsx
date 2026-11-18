import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { ApiError, request } from '../../api';
import { Pager } from '../../components/Common';
import { useAccess } from '../../hooks/useResource';
import type { ResearchState } from '../../state';
import { validDate } from '../../state';
import { SEARCH_GROUPS, validateGlobalSearch, type GlobalSearchData, type GlobalSearchRequest } from './search-validation';
import './global-search.css';

export type GlobalSearchSelection = { snapshot: string; date: string } & (
  { kind: 'security'; security_id: string; code: string | null; name: string | null } |
  { kind: 'topic'; sector_id: string; name: string } |
  { kind: 'article'; article_id: string; title: string }
);
export interface GlobalSearchProps {
  state: Pick<ResearchState, 'snapshot' | 'date'>; refreshToken: number; trigger: HTMLElement;
  onClose: () => void; onSelect: (result: GlobalSearchSelection, trigger: HTMLElement) => void;
}
const groupNames = { securities: '证券', topics: '题材', messages: '消息' };

export default function GlobalSearch({ state, refreshToken, trigger, onClose, onSelect }: GlobalSearchProps) {
  const dialog = useRef<HTMLDialogElement>(null), input = useRef<HTMLInputElement>(null);
  const generation = useRef(0), modalGeneration = useRef(0);
  const { denied, revoke } = useAccess();
  const [query, setQuery] = useState('');
  const [submission, setSubmission] = useState<(GlobalSearchRequest & { revision: number }) | null>(null);
  const [result, setResult] = useState<{ key: string; data?: GlobalSearchData | null; error?: ApiError; loading: boolean }>();
  const key = submission && !denied ? JSON.stringify([submission, refreshToken]) : null;
  const bound = submission?.snapshot === state.snapshot && submission?.date === state.date;
  const visible = bound && key && result?.key === key && !denied ? result : undefined;
  const close = () => { generation.current++; dialog.current?.close(); onClose(); };
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const current = ++modalGeneration.current;
    if (!element.open) element.showModal();
    input.current?.focus();
    return () => {
      generation.current++;
      if (element.open) element.close();
      queueMicrotask(() => {
        if (modalGeneration.current === current && !document.querySelector('dialog[open]') && trigger.isConnected) trigger.focus();
      });
    };
  }, [trigger]);
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    if (!submission || !key || !bound) return;
    setResult({ key, loading: true });
    const params = new URLSearchParams({
      q: submission.query, snapshot_id: submission.snapshot, date: submission.date,
      page: String(submission.page), size: String(submission.size),
    });
    Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      const value = await request<unknown>(`/api/search?${params}`, { snapshot: submission.snapshot, signal: controller.signal });
      const failure = validateGlobalSearch(value, submission);
      if (failure) throw new ApiError(failure === 'selection_mismatch' ? 409 : 502,
        '搜索响应与请求不一致或格式无效，已清除结果。', failure);
      if (generation.current === current) setResult({
        key, loading: false, data: (value as { data: GlobalSearchData | null }).data,
      });
    }).catch((error: unknown) => {
      if (generation.current !== current || (error instanceof DOMException && error.name === 'AbortError')) return;
      const failure = error instanceof ApiError ? error : new ApiError(0);
      if (failure.status === 403) { revoke(); return; }
      setResult({ key, error: failure, loading: false });
    });
    return () => { generation.current++; controller.abort(); };
  }, [submission, key, bound, revoke]);
  const submit = () => {
    const term = query.trim();
    if (!term || term.length > 50 || !validDate(state.date) ||
      !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(state.snapshot)) return;
    setSubmission(previous => ({ snapshot: state.snapshot, date: state.date, query: term, page: 1, size: previous?.size ?? 50, revision: (previous?.revision ?? 0) + 1 }));
  };
  const trap = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href]') ?? [])
      .filter(element => element.getClientRects().length > 0);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  };
  const choose = (selection: GlobalSearchSelection) => {
    if (!visible?.data || visible.loading || visible.error || denied) return;
    onSelect(selection, trigger);
  };
  const data = visible?.data;
  return <dialog ref={dialog} className="global-search" aria-labelledby="global-search-title"
    onKeyDown={trap} onCancel={event => { event.preventDefault(); close(); }}>
    <div className="global-search-heading"><h2 id="global-search-title">全局搜索 · 发布归档</h2>
      <button type="button" aria-label="关闭全局搜索" onClick={close}>关闭</button></div>
    <p className="muted">交易日 {state.date} · 发布快照 {state.snapshot}</p>
    <p className="muted">仅查询本发布归档，证券、题材、消息合并分页；不读取当前输入或命令索引。</p>
    <form className="global-search-form" onSubmit={event => { event.preventDefault(); submit(); }}>
      <label>搜索发布归档<input ref={input} value={query} onChange={event => setQuery(event.target.value)} maxLength={50} required /></label>
      <button type="submit" disabled={denied || !query.trim() || !state.snapshot || !validDate(state.date)}>搜索归档</button>
    </form>
    {visible?.loading && <p role="status">正在搜索发布归档…</p>}
    {visible?.error && <p role="alert" className="notice error">{visible.error.message} 请显式重新搜索。</p>}
    {visible?.data === null && <p role="status">搜索物化不可用 · materialization_unavailable</p>}
    {data && <>
      <Pager page={data.page} size={data.size} total={data.total} change={(page, size) => setSubmission(previous =>
        previous ? { ...previous, page, size, revision: previous.revision + 1 } : null)} />
      {SEARCH_GROUPS.map(group => <section key={group} aria-label={`${groupNames[group]}搜索结果`}>
        <h3>{groupNames[group]} <small>{data.group_status[group].total ?? '不可用'}</small></h3>
        {data.group_status[group].status === 'unavailable'
          ? <p className="notice">归档不可用 · {data.group_status[group].reason}</p>
          : <ul className="global-search-results">
            {group === 'securities' && data.groups.securities.map(item => <li key={item.security_id}>
                <button onClick={() => choose({ snapshot: state.snapshot, date: state.date, kind: 'security', security_id: item.security_id, code: item.code, name: item.name })}>
                {item.name?.trim() || '名称未知'}</button><small>{item.code?.trim() || '代码未知'} · {item.security_id}</small></li>)}
            {group === 'topics' && data.groups.topics.map(item => <li key={item.sector_id}>
              <button onClick={() => choose({ snapshot: state.snapshot, date: state.date, kind: 'topic', sector_id: item.sector_id, name: item.name })}>{item.name}</button>
              <small>{item.sector_id}</small></li>)}
            {group === 'messages' && data.groups.messages.map(item => <li key={item.article_id}>
              <button onClick={() => choose({ snapshot: state.snapshot, date: state.date, kind: 'article', article_id: item.article_id, title: item.title })}>{item.title}</button>
              <small>{item.source} / {item.source_version} · {item.published_at}</small></li>)}
          </ul>}
      </section>)}
      {data.total === 0 && SEARCH_GROUPS.every(group => data.group_status[group].status === 'ready') &&
        <p className="empty">没有匹配结果</p>}
    </>}
  </dialog>;
}
