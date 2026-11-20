import { useEffect, useRef, useState } from 'react';
import type { NewsArticle } from './types';
import { isObject, OwnerReadError, ownerRequest, readSession, type ReadSession } from './owner-api';

interface ReviewState {
  association_id: string;
  revision: number;
  status: string;
  decision: 'accept' | 'reject' | null;
  own_audit: { audit_id: string; decision: 'accept' | 'reject'; revision: number; reviewed_at: string }[];
}
const label = (decision: string) => decision === 'accept' ? '确认' : '驳回';

function AssociationEditor({ item, session, articleId, reread, reading }: {
  item?: ReviewState; session?: ReadSession; articleId: string;
  reread: () => Promise<void>; reading: boolean;
}) {
  const [decision, setDecision] = useState<'accept' | 'reject'>();
  const [conflict, setConflict] = useState(false);
  const [reloaded, setReloaded] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [receipt, setReceipt] = useState<{ decision: string; pending_topic_publication: boolean; revision: number }>();
  const [busy, setBusy] = useState(false);
  const command = useRef<Record<string, unknown> | undefined>(undefined);
  const operation = useRef<AbortController | undefined>(undefined);
  const [unknown, setUnknown] = useState(false);
  useEffect(() => () => operation.current?.abort(), []);
  const submit = async (nextDecision: 'accept' | 'reject', replay = false) => {
    if (!item || !session || busy || reading) return;
    if (!replay) command.current = {
      association_id: item.association_id, decision: nextDecision,
      expected_revision: Math.max(item.revision, receipt?.revision ?? 0),
      idempotency_key: crypto.randomUUID(),
    };
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    setBusy(true);
    setDecision(nextDecision);
    setError('');
    setStatus('');
    try {
      const live = await readSession(controller.signal);
      if (live.owner_id !== session.owner_id || live.session_id !== session.session_id) {
        throw new OwnerReadError(403, '所有者会话标识不一致，未提交。');
      }
      const value = await ownerRequest(`/api/news/${articleId}/associations/review`, controller.signal, command.current);
      if (controller.signal.aborted) return;
      if (!isObject(value) || value.association_id !== item.association_id
        || value.decision !== nextDecision || value.revision !== Number(command.current!.expected_revision) + 1
        || typeof value.audit_id !== 'string' || value.effective !== false
        || typeof value.pending_topic_publication !== 'boolean') {
        throw new OwnerReadError(502, '审核回执标识不一致，结果未知。');
      }
      setReceipt(value as unknown as typeof receipt);
      setConflict(false);
      setUnknown(false);
      command.current = undefined;
      setStatus('审核已记录');
    } catch (failure) {
      if (controller.signal.aborted) return;
      const problem = failure instanceof OwnerReadError ? failure : new OwnerReadError(0);
      setError(problem.message);
      setUnknown(problem.uncertain);
      if (problem.status === 401 || problem.status === 403) setReceipt(undefined);
      if (problem.status === 409) {
        setConflict(true);
        setReloaded(false);
        command.current = undefined;
      }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  };
  return <div>
    <p>关联修订 {receipt?.revision ?? item?.revision ?? '不可用'}</p>
    {item && <p>复核状态：{item.status}</p>}
    {item?.own_audit.map(audit => <p key={audit.audit_id}>
      独立审核记录：{label(audit.decision)} · 修订 {audit.revision} · {audit.reviewed_at}
    </p>)}
    {decision && (conflict || unknown) && <p>待提交决定：{label(decision)}</p>}
    {(error || conflict) && <p role="alert">{conflict ? '关联复核发生冲突。请重新读取后显式重新确认。' : error}</p>}
    {receipt && <p>审核决定：{label(receipt.decision)}
      {receipt.pending_topic_publication ? '；等待新题材快照发布' : '；独立审计，不改变已发布关联'}</p>}
    {!conflict && !unknown && <>
      <button disabled={!item || !session || busy || reading} onClick={() => void submit('accept')}>确认关联</button>
      <button disabled={!item || !session || busy || reading} onClick={() => void submit('reject')}>驳回关联</button>
    </>}
    {unknown && <button disabled={busy} onClick={() => decision && void submit(decision, true)}>使用原命令重试</button>}
    {conflict && !unknown && <>
      <button disabled={busy || reading} onClick={() => void reread().then(() => setReloaded(true))}>重新读取复核状态</button>
      <button disabled={!reloaded || !item || !session || busy || reading} onClick={() => decision && void submit(decision)}>重新确认决定</button>
    </>}
    <p role="status">{status}</p>
  </div>;
}

export default function AssociationReview({ article, date, snapshot }: {
  article: NewsArticle; date: string; snapshot: string;
}) {
  const associations = [...article.topics, ...article.securities];
  const [opened, setOpened] = useState(false);
  const [selected, setSelected] = useState(associations[0]?.association_id || '');
  const [items, setItems] = useState<ReviewState[]>([]);
  const [session, setSession] = useState<ReadSession>();
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const operation = useRef<AbortController | undefined>(undefined);
  const identity = useRef<ReadSession | undefined>(undefined);
  useEffect(() => {
    if (opened && !document.querySelector('dialog[open]')) closeButton.current?.focus();
  }, [opened]);
  const close = () => {
    operation.current?.abort();
    setOpened(false); setItems([]); setSession(undefined); setError('');
    identity.current = undefined;
    trigger.current?.focus();
  };
  useEffect(() => {
    const revoke = () => close();
    window.addEventListener('private-session-lock', revoke);
    window.addEventListener('private-source-revoked', revoke);
    return () => {
      operation.current?.abort();
      window.removeEventListener('private-session-lock', revoke);
      window.removeEventListener('private-source-revoked', revoke);
    };
  }, []);
  const read = async () => {
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    setReading(true); setItems([]); setSession(undefined); setError('');
    try {
      const owner = await readSession(controller.signal);
      if (identity.current && (owner.owner_id !== identity.current.owner_id
        || owner.session_id !== identity.current.session_id)) {
        throw new OwnerReadError(403, '所有者会话标识不一致，请关闭后重新读取。');
      }
      identity.current = owner;
      const value = await ownerRequest(
        `/api/news/${article.article_id}/associations/review-state?date=${date}&snapshot_id=${snapshot}`,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (!isObject(value) || value.owner_id !== owner.owner_id
        || value.article_id !== article.article_id || value.article_revision_id !== article.revision_id
        || value.article_revision !== article.revision || value.snapshot_id !== snapshot || value.trade_date !== date
        || !Array.isArray(value.associations)
        || new Set(value.associations.map(item => isObject(item) ? item.association_id : null)).size !== value.associations.length
        || !value.associations.every(item => isObject(item)
          && associations.some(association => association.association_id === item.association_id)
          && Number.isSafeInteger(item.revision) && Number(item.revision) >= 0
          && ['unreviewed', 'accepted', 'rejected'].includes(String(item.status))
          && [null, 'accept', 'reject'].includes(item.decision as null)
          && Array.isArray(item.own_audit) && item.own_audit.every(audit => isObject(audit)
            && typeof audit.audit_id === 'string' && ['accept', 'reject'].includes(String(audit.decision))
            && Number.isSafeInteger(audit.revision) && typeof audit.reviewed_at === 'string'))) {
        throw new OwnerReadError(502, '复核状态与所有者、发布、文章或关联标识不一致。');
      }
      setItems(value.associations as ReviewState[]); setSession(owner);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : '读取失败');
    } finally { if (!controller.signal.aborted) setReading(false); }
  };
  return <>
    <button ref={trigger} disabled={!associations.length} onClick={() => {
      setOpened(true); void read();
      if (opened && !document.querySelector('dialog[open]')) closeButton.current?.focus();
    }}>复核消息关联</button>
    {opened && <section aria-label="所有者关联复核" onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); close(); }
    }}>
      <h3>所有者关联复核</h3>
      <button ref={closeButton} onClick={close}>关闭关联复核</button>
      <label>实际关联<select value={selected} onChange={event => setSelected(event.target.value)}>
        {associations.map(item => <option key={item.association_id} value={item.association_id}>
          {'topic_id' in item ? item.topic_id : item.security_id}
        </option>)}
      </select></label>
      {error && <p role="alert">{error}</p>}
      {reading && <p>正在读取独立复核状态</p>}
      {associations.map(association => <div key={association.association_id} hidden={selected !== association.association_id}>
        <AssociationEditor
          articleId={article.article_id} session={session} reading={reading}
          item={items.find(item => item.association_id === association.association_id)} reread={read}
        />
      </div>)}
    </section>}
  </>;
}
