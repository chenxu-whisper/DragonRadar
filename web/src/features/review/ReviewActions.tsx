import { useState } from 'react';
import { useSession } from './SessionProvider';
import { useCommand } from './useCommand';
import { command, securityNoteTitle } from './draft';
import type { Note, Watch } from './types';
import { allowNavigation } from './navigation';

export function ReviewActions({ snapshot, security }: { snapshot: string; security: string }) {
  const { enabled, store } = useSession();
  const watch = useCommand<Watch>(`add-watch:${security}`);
  const note = useCommand<Note>(`create-security:${security}`);
  const [notice, setNotice] = useState('');
  if (!enabled || store.locked) return null;
  const current = watch.conflict as Watch | undefined;
  return <div className="review-actions">
    <button disabled={watch.busy} aria-label={`加入自选 ${security}`} onClick={() => void watch.execute(
      () => command('POST', '/watchlist', { snapshot_id: snapshot, security_id: security }), () => setNotice('已在自选中'),
    )}>{watch.pending ? '重试自选原命令' : '加入自选'}</button>
    <button disabled={note.busy} aria-label={`记录笔记 ${security}`} onClick={() => {
      if (!allowNavigation()) return;
      void note.execute(() => command('POST', '/notes', { snapshot_id: snapshot, security_id: security,
        content: { title: securityNoteTitle(security), judgment: '', evidence: '', invalidation: '', verification_date: null } }),
      item => { store.open(item); },
      item => window.dispatchEvent(new CustomEvent('private-open-note', { detail: item.id })));
    }}>{note.pending ? '重试笔记原命令' : '记录笔记'}</button>
    {notice && <span role="status">{notice}</span>}
    {(watch.error || note.error) && <p role="alert">{watch.error || note.error}</p>}
    {current && !current.active && <button onClick={() => void watch.execute(
      () => command('PATCH', `/watchlist/${current.id}`, { active: true }, current.version), () => setNotice('已恢复自选'),
    )}>此项已移除，明确恢复</button>}
  </div>;
}
