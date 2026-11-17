import { describe, expect, it } from 'vitest';
import { SessionStore } from '../../src/features/review/session';
import type { Note, Session } from '../../src/features/review/types';

const session: Session = { owner_id: 'owner-a', session_id: 'session-a', expires_at: '2099-01-01T00:00:00Z' };
const note = { owner_id: 'owner-a', id: 'note-a', revision_id: 'rev-a', state: 'draft',
  content: { title: '标题', judgment: '', evidence: '', invalidation: '', verification_date: null },
  facts: { status: 'unavailable' },
} as Note;
const availableFacts = {
  status: 'available' as const, snapshot_id: 'snapshot-a', trade_date: '2026-09-10',
  as_of: '2026-09-10T12:00:00Z', source_time: null, provenance: 'contemporaneous' as const,
  manifest: {}, result: null,
};
describe('会话与草稿隔离', () => {
  it('锁定立即取消请求，同主体恢复草稿，不同主体销毁', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open(note);
    draft.edit({ ...draft.content, judgment: '仅内存输入' });
    const controller = store.controller();
    const epoch = store.epoch;
    store.lock();
    expect(store.locked).toBe(true);
    expect(controller.signal.aborted).toBe(true);
    expect(store.epoch).toBeGreaterThan(epoch);
    store.authenticate({ ...session, session_id: 'new' });
    expect(store.open(note)).toBe(draft);
    expect(store.open(note).content.judgment).toBe('仅内存输入');
    store.authenticate({ ...session, owner_id: 'owner-b' });
    expect(store.drafts.size).toBe(0);
    expect(() => store.open(note)).toThrow();
  });
  it('过期不能解锁，检查期限会取消请求；显式清空删除主体与草稿', () => {
    const store = new SessionStore();
    store.authenticate(session);
    store.open(note);
    store.checkExpiry(Date.parse(session.expires_at));
    expect(store.locked).toBe(true);
    expect(() => store.authenticate({ ...session, expires_at: '2000-01-01T00:00:00Z' })).toThrow();
    store.clear();
    expect(store.drafts.size).toBe(0);
    expect(store.owner).toBeUndefined();
  });
  it('清行情事实不修改原创或未知结果命令', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1 });
    draft.edit({ ...draft.content, judgment: '原创' });
    const pending = draft.prepare('save');
    store.clearFacts();
    expect(draft.content.judgment).toBe('原创');
    expect(draft.pending).toBe(pending);
    expect(draft.baseline.facts).toEqual({ status: 'unavailable' });
  });
  it('同会话focus检查不使保存请求过期，换会话立即取消旧请求', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const epoch = store.epoch;
    const controller = store.controller();
    store.authenticate(session);
    expect(store.epoch).toBe(epoch);
    expect(controller.signal.aborted).toBe(false);
    store.authenticate({ ...session, session_id: 'replacement' });
    expect(store.epoch).toBeGreaterThan(epoch);
    expect(controller.signal.aborted).toBe(true);
  });
  it('clearFacts 后同主体恢复吸收同版本新事实且保留脏正文和命令', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1, facts: availableFacts });
    draft.edit({ ...draft.content, judgment: '仅内存原创' });
    const pending = draft.prepare('save');
    store.lock();
    store.authenticate({ ...session, session_id: 'recovered' });

    const reopened = store.open({ ...note, version: 1, facts: availableFacts });

    expect(reopened).toBe(draft);
    expect(reopened.content.judgment).toBe('仅内存原创');
    expect(reopened.pending).toBe(pending);
    expect(reopened.status).toBe('unknown');
    expect(reopened.baseline.facts).toEqual(availableFacts);
  });
  it('clean draft clearFacts 后同修订回读刷新事实且保持正文和 idle', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1, facts: availableFacts });
    const content = draft.content;
    store.clearFacts();

    const reopened = store.open({ ...note, version: 1, facts: availableFacts });

    expect(reopened).toBe(draft);
    expect(reopened.content).toBe(content);
    expect(reopened.status).toBe('idle');
    expect(reopened.baseline.facts).toEqual(availableFacts);
  });
  it('只按对应服务端版本恢复 baseline 或冲突 current 的 facts', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1, facts: availableFacts });
    draft.edit({ ...draft.content, judgment: '原创' });
    draft.reject({ ...note, version: 2, revision_id: 'rev-b', facts: availableFacts });
    draft.editMerge({ ...draft.content, judgment: '合并输入' });
    store.clearFacts();

    store.open({ ...note, version: 2, revision_id: 'rev-b', facts: availableFacts });
    expect(draft.baseline.facts).toEqual({ status: 'unavailable' });
    expect(draft.current?.facts).toEqual(availableFacts);
    expect(draft.content.judgment).toBe('原创');
    expect(draft.mergeInput?.judgment).toBe('合并输入');
  });
  it('同版本异常状态只恢复匹配 facts，不修改当前冲突、命令状态或正文', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1 });
    draft.edit({ ...draft.content, judgment: '本地未确认正文' });
    const pending = draft.prepare('save');
    const content = draft.content;
    const current = draft.current;
    const status = draft.status;

    store.open({
      ...note,
      version: 1,
      state: 'confirmed',
      revision_id: '异常的同版本确认修订',
      facts: availableFacts,
    });

    expect(draft.current).toBe(current);
    expect(draft.pending).toBe(pending);
    expect(draft.status).toBe(status);
    expect(draft.content).toBe(content);
    expect(draft.baseline.facts).toEqual(availableFacts);
  });
  it('脏草稿打开更高服务端修订时保留本地正文并形成冲突', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1 });
    draft.edit({ ...draft.content, judgment: '本地未保存判断' });
    const current = {
      ...note, version: 2, revision_id: 'rev-b',
      content: { ...note.content, judgment: '另一窗口已保存' },
    };

    expect(store.open(current)).toBe(draft);
    expect(draft.content.judgment).toBe('本地未保存判断');
    expect(draft.baseline.version).toBe(1);
    expect(draft.current).toEqual(current);
    expect(draft.status).toBe('conflict');
  });
  it('脏草稿打开更高确认修订时保留正文并进入只读或 fork 冲突语义', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const draft = store.open({ ...note, version: 1 });
    draft.edit({ ...draft.content, judgment: '待另存的本地判断' });
    const confirmed = {
      ...note, version: 2, revision_id: 'rev-confirmed', state: 'confirmed' as const,
      content: { ...note.content, judgment: '另一窗口已确认' },
    };

    store.open(confirmed);

    expect(draft.content.judgment).toBe('待另存的本地判断');
    expect(draft.current).toEqual(confirmed);
    expect(() => draft.merge(draft.content)).toThrow('最新版已确认，只能另存');
    expect(() => draft.prepare('save')).toThrow('请先处理只读版本或冲突');
    expect(draft.prepare('fork').path).toBe(`/notes/${note.id}/fork`);
  });
  it.each([
    ['确认修订', { selected: 2, date: '', snapshot: '' }],
    ['验证日期', { selected: 0, date: '2026-09-10', snapshot: '' }],
    ['验证快照', { selected: 0, date: '', snapshot: 'snapshot-a' }],
  ])('仅设置%s也属于未保存验证输入', (_name, selection) => {
    const store = new SessionStore();
    store.authenticate(session);
    expect(store.dirty).toBe(false);
    store.verifications.set('note-a', {
      ...selection, outcome: '', evidence: '', supersedes: null,
    });
    expect(store.dirty).toBe(true);
  });
  it('未交互的确认版默认选择不创建草稿，退出会清除验证输入', () => {
    const store = new SessionStore();
    store.authenticate(session);
    expect(store.verifications.size).toBe(0);
    expect(store.dirty).toBe(false);
    store.verifications.set('note-a', {
      selected: 1, date: '2026-09-10', snapshot: 'snapshot-a',
      outcome: '', evidence: '', supersedes: null,
    });
    store.clear();
    expect(store.verifications.size).toBe(0);
    expect(store.dirty).toBe(false);
  });
  it('fork 成功和同命令重放始终保持 Map key 与 Draft baseline id 恒等', () => {
    const store = new SessionStore();
    store.authenticate(session);
    const original = store.open(note);
    const sent = original.prepare('fork');
    const forked = {
      ...note, id: 'note-b', revision_id: 'rev-b', derived_from_revision_id: note.revision_id,
    };

    original.unknown();
    original.accept(forked, sent);
    const first = store.open(forked);
    const replay = store.open(forked);

    expect(replay).toBe(first);
    expect(store.drafts.size).toBe(2);
    expect([...store.drafts].every(([key, draft]) => key === draft.baseline.id)).toBe(true);
    expect(store.drafts.get('note-a')).toBe(original);
    expect(original.baseline.id).toBe('note-a');
  });
});
