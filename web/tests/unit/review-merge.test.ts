import { expect, it } from 'vitest';
import { SessionStore } from '../../src/features/review/session';
import type { Note } from '../../src/features/review/types';

const note: Note = {
  id: '20000000-0000-4000-8000-000000000001', owner_id: '10000000-0000-4000-8000-000000000001',
  snapshot_id: '30000000-0000-4000-8000-000000000001', trade_date: '2026-09-10', security_id: null,
  kind: 'note', version: 1, state: 'draft', revision_id: '40000000-0000-4000-8000-000000000001',
  derived_from_revision_id: null, template_version: null, created_at: '2026-09-10T00:00:00Z',
  updated_at: '2026-09-10T00:00:00Z', saved_at: '2026-09-10T00:00:00Z',
  content: { title: '观察', judgment: '原判断', evidence: '', invalidation: '', verification_date: null },
  facts: { status: 'unavailable' },
};
const session = { owner_id: note.owner_id, session_id: 'session-a', expires_at: '2099-01-01T00:00:00Z' };

it('手工合并中间输入绑定草稿，锁定同主体恢复且采用后清除', () => {
  const store = new SessionStore();
  store.authenticate(session);
  const draft = store.open(note);
  draft.reject({ ...note, version: 2, revision_id: 'new-revision' });
  draft.editMerge({ ...note.content, judgment: '未采用的合并输入' });
  const identity = draft.identity;
  store.lock();
  store.authenticate({ ...session, session_id: 'session-b' });
  expect(store.open(note)).toBe(draft);
  expect(draft.identity).toBe(identity);
  expect(draft.mergeInput?.judgment).toBe('未采用的合并输入');
  draft.merge(draft.mergeInput!);
  expect(draft.content.judgment).toBe('未采用的合并输入');
  expect(draft.baseline.version).toBe(2);
  expect(draft.mergeInput).toBeUndefined();
});

it('异主体或显式退出销毁包含合并中间输入的草稿', () => {
  for (const operation of ['change-owner', 'logout']) {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open(note);
    draft.reject({ ...note, version: 2 });
    draft.editMerge({ ...note.content, judgment: '不能串主体' });
    if (operation === 'logout') store.clear();
    else store.authenticate({ ...session, owner_id: 'another-owner' });
    expect(store.drafts.size).toBe(0);
    store.authenticate(session);
    expect(store.open(note).mergeInput).toBeUndefined();
  }
});
