import 'fake-indexeddb/auto';
import { describe, expect, test } from 'vitest';
import { LocalRepository } from '../../src/repositories/local/repository';

const content = { title: '复盘', content: '原始判断', judgment: '观察', evidence: '', invalid_condition: '', verify_date: '' };
const repository = () => new LocalRepository(`test-${crypto.randomUUID()}`);

function database(name: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(name);
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error);
  });
}

describe('local review transactions', () => {
  test('saves, reads back and restores with stable profile after reopen', async () => {
    const name = `test-${crypto.randomUUID()}`;
    const repo = new LocalRepository(name);
    const profile = await repo.profile();
    const saved = await repo.putNote({
      note_id: 'n1', expected_revision: 0, key: 'save1', snapshot_id: 'snapshot',
      trade_date: '2026-09-10', security_id: 'SSE.600519', content,
    });
    expect(saved.revision).toBe(1);
    expect(saved.content_hash).toMatch(/^[\da-f]{64}$/);
    expect(saved.trade_date).toBe('2026-09-10');
    expect(saved.security_id).toBe('SSE.600519');
    expect(saved.idempotency_key).toBe('save1');
    expect((await repo.listNotes())[0].content).toEqual({
      ...content, judgment: '观察', evidence: null, invalid_condition: null, verify_date: null,
    });
    expect(await repo.listNotes({ snapshot_id: 'other' })).toEqual([]);
    expect((await repo.listNotes({ snapshot_id: 'snapshot' }))[0].note_id).toBe('n1');
    repo.close();
    const reopened = new LocalRepository(name);
    expect(await reopened.profile()).toBe(profile);
    expect((await reopened.listNotes())[0]).toEqual(saved);
    reopened.close();
  });
  test('binds deep-link security identity into every saved revision and command digest', async () => {
    const repo = repository();
    const command = {
      note_id: 'n1', expected_revision: 0, key: 'bound', snapshot_id: 'snapshot',
      trade_date: '2026-09-10', security_id: 'SSE.600519', content,
    };
    const first = await repo.putNote(command);
    expect(first.security_id).toBe('SSE.600519');
    expect((await repo.revisions('n1'))[0].security_id).toBe('SSE.600519');
    await expect(repo.putNote({ ...command, security_id: 'SZSE.000001' })).rejects.toThrow('幂等');
    repo.close();
  });
  test('same command replays; changed payload with same key fails', async () => {
    const repo = repository();
    const command = {
      note_id: 'n1', expected_revision: 0, key: 'k1', snapshot_id: 'snapshot',
      trade_date: '2026-09-10', content,
    };
    expect(await repo.putNote(command)).toEqual(await repo.putNote(command));
    await expect(repo.putNote({ ...command, content: { ...content, title: 'different' } })).rejects.toThrow('幂等');
    await expect(repo.putNote({ ...command, trade_date: '2026-09-11' })).rejects.toThrow('幂等');
    expect((await repo.revisions('n1'))).toHaveLength(1);
    repo.close();
  });
  test('replaying an older committed command reports the current revision without rewriting', async () => {
    const repo = repository();
    const first = { note_id: 'n1', expected_revision: 0, key: 'first', snapshot_id: 'snapshot', content };
    expect((await repo.putNote(first)).revision).toBe(1);
    expect((await repo.putNote({ ...first, expected_revision: 1, key: 'second', content: { ...content, title: 'new' } })).revision).toBe(2);
    const replay = await repo.putNote(first);
    expect(replay).toMatchObject({ revision: 1, current_revision: 2 });
    expect((await repo.listNotes())[0]).toMatchObject({ revision: 2, content: { title: 'new' } });
    expect(await repo.revisions('n1')).toHaveLength(2);
    repo.close();
  });
  test('two connections updating same revision produce exactly one winner', async () => {
    const name = `test-${crypto.randomUUID()}`;
    const a = new LocalRepository(name), b = new LocalRepository(name);
    await a.putNote({ note_id: 'n1', expected_revision: 0, key: 'create', snapshot_id: 'snapshot', content });
    const results = await Promise.allSettled([
      a.putNote({ note_id: 'n1', expected_revision: 1, key: 'a', snapshot_id: 'snapshot', content: { ...content, title: 'A' } }),
      b.putNote({ note_id: 'n1', expected_revision: 1, key: 'b', snapshot_id: 'snapshot', content: { ...content, title: 'B' } }),
    ]);
    expect(results.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect((await a.listNotes())[0].revision).toBe(2);
    expect(await a.revisions('n1')).toHaveLength(2);
    a.close(); b.close();
  });
  test('watchlist add is idempotent, remove and undo preserve identity', async () => {
    const name = `test-${crypto.randomUUID()}`, repo = new LocalRepository(name);
    await repo.putWatchlist('SSE.600519', 'add', 'a', 'snapshot');
    await repo.putWatchlist('SSE.600519', 'add', 'b', 'snapshot');
    expect(await repo.listWatchlist()).toHaveLength(1);
    await repo.putWatchlist('SSE.600519', 'remove', 'c', '');
    expect(await repo.listWatchlist()).toHaveLength(0);
    const db = await database(name);
    const tombstones = await new Promise<unknown[]>((resolve, reject) => {
      const query = db.transaction('watchlist').objectStore('watchlist').getAll();
      query.onsuccess = () => resolve(query.result);
      query.onerror = () => reject(query.error);
    });
    expect(tombstones).toMatchObject([{ security_id: 'SSE.600519', active: false, revision: 2 }]);
    db.close();
    await repo.putWatchlist('SSE.600519', 'add', 'd', 'snapshot');
    expect((await repo.listWatchlist())[0].security_id).toBe('SSE.600519');
    repo.close();
  });
  test('rejects invalid content without persisting commands or revisions', async () => {
    const repo = repository();
    await expect(repo.putNote({ note_id: 'n', expected_revision: 0, key: 'k', snapshot_id: 'snapshot', content: { ...content, title: ' ' } })).rejects.toThrow('标题');
    await expect(repo.putNote({ note_id: 'n', expected_revision: 0, key: 'k2', snapshot_id: '', content })).rejects.toThrow('快照');
    expect(await repo.listNotes()).toHaveLength(0);
    expect(await repo.revisions('n')).toHaveLength(0);
    repo.close();
  });
  test('uses profile-scoped composite keys for all business stores', async () => {
    const name = `test-${crypto.randomUUID()}`, repo = new LocalRepository(name);
    await repo.profile();
    const db = await database(name);
    expect(db.transaction('notes').objectStore('notes').keyPath).toEqual(['local_profile_id', 'note_id']);
    expect(db.transaction('note_revisions').objectStore('note_revisions').keyPath).toEqual(['local_profile_id', 'note_id', 'revision']);
    expect(db.transaction('watchlist').objectStore('watchlist').keyPath).toEqual(['local_profile_id', 'security_id']);
    expect(db.transaction('commands').objectStore('commands').keyPath).toEqual(['local_profile_id', 'key']);
    expect(db.objectStoreNames.contains('verification_records')).toBe(true);
    db.close(); repo.close();
  });
  test('upgrades the earlier local schema without replacing its profile or records', async () => {
    const name = `test-${crypto.randomUUID()}`;
    await new Promise<void>((resolve, reject) => {
      const opening = indexedDB.open(name, 1);
      opening.onupgradeneeded = () => {
        const db = opening.result;
        db.createObjectStore('profiles').put('stable-profile', 'active');
        db.createObjectStore('notes', { keyPath: 'note_id' }).put({
          note_id: 'legacy-note', local_profile_id: 'stable-profile', snapshot_id: 'snapshot',
          content, revision: 1, updated_at: '2026-09-20T10:00:00.000Z',
        });
        db.createObjectStore('note_revisions', { keyPath: ['note_id', 'revision'] }).put({
          note_id: 'legacy-note', local_profile_id: 'stable-profile', snapshot_id: 'snapshot',
          content, revision: 1, updated_at: '2026-09-20T10:00:00.000Z',
        });
        db.createObjectStore('watchlist', { keyPath: 'security_id' }).put({
          security_id: 'SSE.600519', local_profile_id: 'stable-profile', snapshot_id: 'snapshot',
          active: true, added_at: '2026-09-20T09:00:00.000Z', revision: 1,
        });
        db.createObjectStore('commands', { keyPath: 'key' });
      };
      opening.onsuccess = () => { opening.result.close(); resolve(); };
      opening.onerror = () => reject(opening.error);
    });
    const repo = new LocalRepository(name);
    expect(await repo.profile()).toBe('stable-profile');
    expect((await repo.listNotes())[0]).toMatchObject({
      note_id: 'legacy-note', revision: 1, content_hash: expect.stringMatching(/^[\da-f]{64}$/),
    });
    expect((await repo.listWatchlist())[0].security_id).toBe('SSE.600519');
    repo.close();
  });
  test('migration write failure rejects cleanly without an unhandled transaction rejection', async () => {
    const name = `test-${crypto.randomUUID()}`;
    await new Promise<void>((resolve, reject) => {
      const opening = indexedDB.open(name, 1);
      opening.onupgradeneeded = () => {
        const db = opening.result;
        db.createObjectStore('profiles').put('stable-profile', 'active');
        db.createObjectStore('notes', { keyPath: 'note_id' }).put({
          note_id: 'legacy-note', local_profile_id: 'stable-profile', snapshot_id: 'snapshot',
          content, revision: 1, updated_at: '2026-09-20T10:00:00.000Z',
        });
        db.createObjectStore('note_revisions', { keyPath: ['note_id', 'revision'] });
        db.createObjectStore('watchlist', { keyPath: 'security_id' });
        db.createObjectStore('commands', { keyPath: 'key' });
      };
      opening.onsuccess = () => { opening.result.close(); resolve(); };
      opening.onerror = () => reject(opening.error);
    });
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on('unhandledRejection', onUnhandled);
    const original = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value: unknown, key?: IDBValidKey) {
      if (this.name === 'notes') {
        this.transaction.abort();
        throw new DOMException('Disk full', 'QuotaExceededError');
      }
      return key === undefined ? original.call(this, value) : original.call(this, value, key);
    };
    const repo = new LocalRepository(name);
    try {
      await expect(repo.profile()).rejects.toThrow();
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
    } finally {
      IDBObjectStore.prototype.put = original;
      process.off('unhandledRejection', onUnhandled);
      repo.close();
    }
  });
  test('transaction abort rolls back note and command; same key can retry', async () => {
    const repo = repository();
    const original = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value: unknown, key?: IDBValidKey) {
      if (this.name === 'note_revisions') {
        this.transaction.abort();
        throw new DOMException('Disk full', 'QuotaExceededError');
      }
      return key === undefined ? original.call(this, value) : original.call(this, value, key);
    };
    try {
      await expect(repo.putNote({ note_id: 'n', expected_revision: 0, key: 'retry', snapshot_id: 'snapshot', content })).rejects.toThrow();
    } finally { IDBObjectStore.prototype.put = original; }
    expect(await repo.listNotes()).toHaveLength(0);
    expect(await repo.revisions('n')).toHaveLength(0);
    expect((await repo.putNote({ note_id: 'n', expected_revision: 0, key: 'retry', snapshot_id: 'snapshot', content })).revision).toBe(1);
    repo.close();
  });
});
