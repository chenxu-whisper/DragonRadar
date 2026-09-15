import { useRef, useState } from 'react';
import { PrivateError } from './api';
import { beijingTime, type Draft } from './draft';
import type { Note } from './types';
import { useSession } from './SessionProvider';
import { ContentFields } from './Controls';
import { ConflictDialog } from './ConflictDialog';
import { Facts } from './Facts';
import { captureNavigationContext, isNavigationContextCurrent } from './navigation';
import { contentFieldErrors, errorSummary, type ContentField, type ContentFieldErrors } from './validation';

export function NoteEditor({ draft, saved, snapshot }: { draft: Draft; saved: (note: Note, navigate?: boolean) => void; snapshot?: string }) {
  const { store, send } = useSession();
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<ContentFieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const conflictButton = useRef<HTMLButtonElement>(null);
  const fields = useRef<Partial<Record<ContentField, HTMLInputElement | HTMLTextAreaElement | null>>>({});
  const closeConflict = () => { setConflict(false); requestAnimationFrame(() => conflictButton.current?.focus()); };
  async function execute(action: 'save' | 'confirm' | 'fork', targetSnapshot?: string) {
    if (busy) return;
    setError('');
    if (!draft.pending) {
      const validation = contentFieldErrors(draft.content, action === 'confirm' ? draft.baseline.trade_date : undefined);
      if (Object.keys(validation).length) {
        setFieldErrors(validation);
        setError(errorSummary(validation));
        const first = (['title', 'judgment', 'evidence', 'invalidation', 'verification_date'] as const).find(field => validation[field]?.length);
        requestAnimationFrame(() => { if (first) fields.current[first]?.focus(); });
        return;
      }
    }
    setFieldErrors({});
    let cmd;
    try { cmd = draft.prepare(action, targetSnapshot); }
    catch (error) { setError(error instanceof Error ? error.message : '无法提交'); return; }
    const navigationContext = captureNavigationContext();
    setBusy(true); store.changed();
    try {
      const note = await send<Note>(cmd);
      draft.accept(note, cmd); setFieldErrors({}); store.changed(); saved(note, isNavigationContextCurrent(navigationContext));
    } catch (failure) {
      if (failure instanceof PrivateError && failure.code === 'version_conflict' && failure.current) {
        draft.reject(failure.current as Note); setConflict(true);
      } else if (!(failure instanceof PrivateError) || failure.uncertain || failure.status === 401) draft.unknown();
      else draft.fail();
      if (!store.locked) setError(failure instanceof Error ? failure.message : '保存失败。');
      store.changed();
    } finally { setBusy(false); }
  }
  const note = draft.baseline;
  return <section className="note-editor" aria-label="笔记编辑">
    <div className="section-heading"><h2>复盘正文</h2><span>{note.state === 'confirmed' ? '已确认 · 只读' : '草稿'}</span></div>
    <p className="muted">原快照 {note.snapshot_id} · 交易日 {note.trade_date}{note.security_id ? ` · ${note.security_id}` : ''}</p>
    {note.derived_from_revision_id && <p className="muted">另存来源修订 {note.derived_from_revision_id}</p>}
    <Facts facts={note.facts} />
    <ContentFields value={draft.content} errors={fieldErrors} fieldRef={(field, element) => { fields.current[field] = element; }}
      disabled={busy || Boolean(draft.pending) || note.state === 'confirmed'} onChange={content => {
        draft.edit(content); setError(''); setFieldErrors({}); store.changed();
      }} />
    <p role="status">{draft.pending ? (busy ? '提交中，尚未确认保存。' : '提交结果未知，输入已冻结，请重试原命令。')
      : draft.dirty ? '有未保存修改 · 仅暂留本页内存' : `已保存 · 版本 ${note.version} · ${beijingTime(note.saved_at)}（北京时间）`}</p>
    {error && <p className="notice" role="alert">{error}</p>}
    <div className="toolbar">
      {draft.pending ? <button className="primary" disabled={busy} onClick={() => void execute('save')}>重试原命令</button> : <>
        {note.state === 'draft' && <><button className="primary" disabled={busy || Boolean(draft.current)} onClick={() => void execute('save')}>保存草稿</button>
          <button disabled={busy || draft.dirty || Boolean(draft.current)} onClick={() => void execute('confirm')}>确认复盘</button></>}
        <button disabled={busy} onClick={() => void execute('fork')}>{note.state === 'confirmed' ? '以此新建草稿' : '另存为新笔记'}</button>
        <button disabled={busy || !snapshot} onClick={() => void execute('fork', snapshot)}>以当前快照另存</button>
      </>}
      {draft.current && <button ref={conflictButton} onClick={() => setConflict(true)}>处理版本冲突</button>}
    </div>
    {conflict && draft.current && !store.locked && <ConflictDialog draft={draft} trigger={conflictButton.current} close={closeConflict}
      merge={content => { draft.merge(content); store.changed(); setConflict(false); setError(''); }}
      fork={() => { setConflict(false); void execute('fork'); }} />}
  </section>;
}
