import { useRef, useState } from 'react';
import { useResource } from '../../hooks/useResource';
import type { Snapshot } from '../../types';
import type { Note, Outcome, Page, Revision, VerificationRecord, VerificationDraft } from './types';
import { usePrivate } from './usePrivate';
import { useCommand } from './useCommand';
import { beijingTime, command, validReviewDate } from './draft';
import { ContentText, PrivatePager, ReadStatus } from './Controls';
import { Facts, MarketFacts } from './Facts';
import { allowNavigation } from './navigation';
import { useSession } from './SessionProvider';
import { errorSummary, verificationFieldErrors, type VerificationField, type VerificationFieldErrors } from './validation';

const outcomes: Record<Outcome, string> = { supported: '支持', weakened: '削弱', refuted: '否定', not_due: '未到期' };
export function Verification({ note }: { note: Note }) {
  const { store } = useSession();
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<VerificationFieldErrors>({});
  const fields = useRef<Partial<Record<VerificationField, HTMLSelectElement | HTMLTextAreaElement | null>>>({});
  const form = store.verifications.get(note.id) || {
    selected: note.state === 'confirmed' ? note.version : 0, date: '', snapshot: '',
    outcome: '' as const, evidence: '', supersedes: null,
  };
  const { selected, date, snapshot, outcome, evidence, supersedes } = form;
  const update = (patch: Partial<VerificationDraft>) => {
    setError(''); setFieldErrors({});
    store.verifications.set(note.id, { ...form, ...patch }); store.changed();
  };
  const [revisionPage, setRevisionPage] = useState(1), [revisionSize, setRevisionSize] = useState(50);
  const revisions = usePrivate<Page<Revision>>(`/notes/${note.id}/revisions?page=${revisionPage}&page_size=${revisionSize}`);
  const revision = usePrivate<Note>(selected ? `/notes/${note.id}/revisions/${selected}` : null);
  const [offset, setOffset] = useState(0);
  const snapshots = useResource<Snapshot[]>(validReviewDate(date) ? `/api/v1/snapshots?trade_date=${date}&limit=100&offset=${offset}` : null);
  const [page, setPage] = useState(1), [size, setSize] = useState(50);
  const history = usePrivate<Page<VerificationRecord>>(`/notes/${note.id}/verifications?page=${page}&page_size=${size}`);
  const mutation = useCommand<VerificationRecord>(`verify:${note.id}`);
  const [viewFacts, setViewFacts] = useState<VerificationRecord>();
  const frozen = mutation.busy || Boolean(mutation.pending);
  const revisionBlocked = selected > 0 && (revision.loading || Boolean(revision.error) || !revision.data);
  const revisionError = selected > 0 && !revision.loading && !revision.data
    ? revision.error || '修订详情读取失败，请重试。'
    : undefined;
  const selectedErrors = revisionBlocked ? undefined : fieldErrors.selected;
  function submit() {
    if (!mutation.pending) {
      if (revisionBlocked) return;
      const validation = verificationFieldErrors({
        selected, revisionConfirmed: selected ? revision.data?.state === 'confirmed' : false, outcome, snapshot, evidence,
      });
      if (Object.keys(validation).length) {
        setFieldErrors(validation); setError(errorSummary(validation));
        const first = (['selected', 'snapshot', 'outcome', 'evidence'] as const).find(field => validation[field]?.length);
        requestAnimationFrame(() => { if (first) fields.current[first]?.focus(); });
        return;
      }
    }
    setError(''); setFieldErrors({});
    void mutation.execute(() => command('POST', `/notes/${note.id}/verifications`, {
      version: selected, outcome, evidence, snapshot_id: outcome === 'not_due' ? null : snapshot, supersedes,
    }), () => {
      store.verifications.delete(note.id);
      store.changed();
      history.refresh();
    });
  }
  return <section aria-label="人工验证">
    <h2>人工验证</h2>
    <p>到期由服务端北京时间判定。未到期仅可记录“未到期”；事实缺失不代表支持或否定。不计算命中率或跨快照收益。</p>
    <ReadStatus loading={revisions.loading} error={revisions.error} retry={revisions.refresh} />
    <label>确认修订<select ref={element => { fields.current.selected = element; }} aria-label="确认修订"
      aria-invalid={selectedErrors?.length ? true : undefined}
      aria-describedby={selectedErrors?.length ? 'verification-selected-error' : undefined}
      value={selected || ''} disabled={frozen} onChange={event => {
      if (allowNavigation()) update({ selected: +event.target.value, supersedes: null });
    }}><option value="">请选择已确认修订</option>
      {revisions.data?.items.filter(item => item.state === 'confirmed').map(item => <option key={item.id} value={item.version}>版本 {item.version} · {beijingTime(item.saved_at)}</option>)}
    </select>{selectedErrors?.length && <span className="field-error" id="verification-selected-error">{selectedErrors.join(' ')}</span>}</label>
    {revisions.data && <PrivatePager name="确认修订" page={revisionPage} size={revisionSize} more={revisions.data.has_more} change={(p, s) => { setRevisionPage(p); setRevisionSize(s); }} />}
    {selected > 0 && <ReadStatus loading={revision.loading} error={revisionError} retry={revision.refresh} />}
    <div className="verification-columns">
      <section><h3>原判断与原事实</h3>
        {revision.data ? <><ContentText content={revision.data.content} /><Facts facts={revision.data.facts} />
          <MarketFacts snapshot={revision.data.snapshot_id} security={revision.data.security_id} />
        </> : <p>请选择确认修订。</p>}
      </section>
      <section><h3>明确选择验证快照</h3>
        <div className="review-fields"><label>验证快照日期<input type="date" value={date} disabled={frozen} onChange={event => { update({ date: event.target.value, snapshot: '' }); setOffset(0); }} /></label>
          <label>验证快照<select ref={element => { fields.current.snapshot = element; }} aria-label="验证快照"
            aria-invalid={fieldErrors.snapshot?.length ? true : undefined}
            aria-describedby={fieldErrors.snapshot?.length ? 'verification-snapshot-error' : undefined}
            value={snapshot} disabled={frozen} onChange={event => update({ snapshot: event.target.value })}>
            <option value="">请选择，不自动使用近邻日期</option>
            {snapshots.data?.map(item => <option key={item.snapshot_id} value={item.snapshot_id}>{item.trade_date} · 修订 {item.result.revision} · {item.snapshot_id}</option>)}
          </select>{fieldErrors.snapshot?.length && <span className="field-error" id="verification-snapshot-error">{fieldErrors.snapshot.join(' ')}</span>}</label></div>
        <ReadStatus loading={snapshots.loading} error={snapshots.error?.message} retry={snapshots.refresh} />
        {(offset > 0 || snapshots.data?.length === 100) && <div className="toolbar">
          <button disabled={frozen || offset === 0} onClick={() => { setOffset(value => value - 100); update({ snapshot: '' }); }}>上一组验证快照</button>
          <button disabled={frozen || snapshots.data?.length !== 100 || offset >= 10000} onClick={() => { setOffset(value => value + 100); update({ snapshot: '' }); }}>下一组验证快照</button>
        </div>}
        <MarketFacts snapshot={snapshot} security={note.security_id} />
      </section>
    </div>
    <div className="review-fields">
      <label>人工结论<select ref={element => { fields.current.outcome = element; }} aria-label="人工结论"
        aria-invalid={fieldErrors.outcome?.length ? true : undefined}
        aria-describedby={fieldErrors.outcome?.length ? 'verification-outcome-error' : undefined}
        value={outcome} disabled={frozen} onChange={event => update({ outcome: event.target.value as Outcome })}>
        <option value="">请明确选择</option>{Object.entries(outcomes).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
      </select>{fieldErrors.outcome?.length && <span className="field-error" id="verification-outcome-error">{fieldErrors.outcome.join(' ')}</span>}</label>
      <label>验证依据<textarea ref={element => { fields.current.evidence = element; }} aria-label="验证依据"
        aria-invalid={fieldErrors.evidence?.length ? true : undefined}
        aria-describedby={fieldErrors.evidence?.length ? 'verification-evidence-error' : undefined}
        rows={4} value={evidence} disabled={frozen} onChange={event => update({ evidence: event.target.value })} />
        {fieldErrors.evidence?.length && <span className="field-error" id="verification-evidence-error">{fieldErrors.evidence.join(' ')}</span>}</label>
    </div>
    {supersedes && <p>更正自 {supersedes}，追加记录，不覆盖旧结论。</p>}
    {(error || mutation.error) && <p role="alert" className="notice">{error || mutation.error}</p>}
    <button className="primary" disabled={mutation.busy || (!mutation.pending && revisionBlocked)} onClick={submit}>{mutation.pending ? '重试验证原命令' : '记录验证'}</button>
    <h3>验证历史</h3>
    <ReadStatus loading={history.loading} error={history.error} retry={history.refresh} />
    {history.data?.items.length === 0 && <p>尚未验证</p>}
    {history.data?.items.map(item => <article className="verification-entry" key={item.id}>
      <p><strong>{outcomes[item.outcome]}</strong> · {beijingTime(item.created_at)}（北京时间）</p>
      <p className="plain-text">{item.evidence || '未填写依据'}</p>
      <p className="muted">确认修订 {item.revision_id} · 验证快照 {item.snapshot_id || '无（未到期）'}</p>
      {item.supersedes && <p>更正自 {item.supersedes}</p>}
      <button disabled={frozen || revision.data?.revision_id !== item.revision_id} onClick={() => update({ supersedes: item.id, evidence: item.evidence, outcome: item.outcome })}>更正此记录</button>
      {item.snapshot_id && <button onClick={() => setViewFacts(item)}>读取此记录验证事实</button>}
    </article>)}
    {viewFacts?.snapshot_id && <MarketFacts snapshot={viewFacts.snapshot_id} security={note.security_id} />}
    {history.data && <PrivatePager name="验证记录" page={page} size={size} more={history.data.has_more} change={(p, s) => { setPage(p); setSize(s); }} />}
  </section>;
}
