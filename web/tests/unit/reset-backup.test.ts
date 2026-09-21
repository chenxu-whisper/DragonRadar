import 'fake-indexeddb/auto';
import { describe, expect, test } from 'vitest';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../src/repositories/local/backup';
import type { ReviewBackup } from '../../src/repositories/local/migrate';
import { LocalRepository } from '../../src/repositories/local/repository';

const localContent = {
  title: '本地版本', content: 'local', judgment: '观察', evidence: '依据',
  invalid_condition: '本地条件', verify_date: '2026-09-20',
};
const oldContent = {
  title: '旧版一', judgment: '判断一', evidence: '依据一',
  invalidation: '旧失效条件', verification_date: '2026-09-11',
};

function backup(profile = 'old-profile'): ReviewBackup {
  return {
    format_version: 1,
    exported_at: '2026-09-21T08:30:00+00:00',
    profile_mapping: { owner_id: 'old-owner', local_profile_id: profile },
    counts: { notes: 1, revisions: 2, verifications: 1, watchlist: 1 },
    notes: [{
      note_id: 'n1', snapshot_id: 'snapshot', security_id: 'A',
      current_revision: 2, created_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T09:00:00.000Z',
    }],
    revisions: [
      {
        revision_id: 'old-r1', note_id: 'n1', revision: 1, state: 'draft',
        content: { ...oldContent }, saved_at: '2026-09-20T08:00:00.000Z',
      },
      {
        revision_id: 'old-r2', note_id: 'n1', revision: 2, state: 'confirmed',
        content: { ...oldContent, title: '旧版二', invalidation: '第二条件', verification_date: '2026-09-12' },
        saved_at: '2026-09-20T09:00:00.000Z',
      },
    ],
    verifications: [{
      verification_id: 'verify-1', note_id: 'n1', revision_id: 'old-r2',
      snapshot_id: 'verification-snapshot', outcome: 'refuted', evidence: '验证记录',
      supersedes: null, created_at: '2026-09-21T08:00:00.000Z',
    }],
    watchlist: [{
      watch_id: 'watch-1', security_id: 'A', snapshot_id: 'snapshot', active: true,
      revision: 1, added_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T08:00:00.000Z',
    }],
  };
}

const repository = () => new LocalRepository(`backup-${crypto.randomUUID()}`);

describe('local review backup and restore', () => {
  test('validates old field names and restores every revision and verification', async () => {
    const repo = repository();
    const source = backup();
    const preview = await previewLocalBackup(repo, JSON.stringify(source), 'merge');
    expect(preview).toMatchObject({
      decision: 'merge',
      duplicates: 0,
      appended_revisions: 2,
      remapped_note_ids: {},
      remapped_revisions: { 'n1:1': 1, 'n1:2': 2 },
      unresolved_conflicts: [],
    });

    const result = await importLocalBackup(repo, JSON.stringify(source), 'merge');

    expect(result.added).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
    expect((await repo.listNotes())[0]).toMatchObject({
      note_id: 'n1',
      revision: 2,
      content: { title: '旧版二', content: '', invalid_condition: '第二条件', verify_date: '2026-09-12' },
    });
    expect((await repo.revisions('n1')).map(row => row.content.title)).toEqual(['旧版二', '旧版一']);
    expect(await repo.listVerifications('n1')).toMatchObject([{
      verification_id: 'verify-1', revision: 2, evidence: '验证记录',
    }]);
    repo.close();
  });

  test('merge skips canonical duplicates and appends divergent history in backup order', async () => {
    const repo = repository();
    await repo.putNote({
      note_id: 'n1', expected_revision: 0, key: 'local-1', snapshot_id: 'snapshot',
      content: { title: '旧版一', content: '', judgment: '判断一', evidence: '依据一', invalid_condition: '旧失效条件', verify_date: '2026-09-11' },
    });
    await repo.putNote({
      note_id: 'n1', expected_revision: 1, key: 'local-2', snapshot_id: 'snapshot', content: localContent,
    });

    const unresolved = await previewLocalBackup(repo, backup());
    expect(unresolved.unresolved_conflicts).toEqual(['n1']);
    const preview = await previewLocalBackup(repo, backup(), 'merge');
    expect(preview).toMatchObject({
      duplicates: 1,
      appended_revisions: 1,
      remapped_revisions: { 'n1:1': 1, 'n1:2': 3 },
      unresolved_conflicts: [],
    });
    await importLocalBackup(repo, backup(), 'merge');
    const revisions = await repo.revisions('n1');
    expect(revisions.map(row => [row.revision, row.content.title])).toEqual([
      [3, '旧版二'], [2, '本地版本'], [1, '旧版一'],
    ]);
    expect(await repo.listVerifications('n1')).toMatchObject([{ revision: 3 }]);
    repo.close();
  });

  test('save-as-new uses a deterministic note mapping and re-import adds nothing', async () => {
    const repo = repository();
    await repo.putNote({
      note_id: 'n1', expected_revision: 0, key: 'local', snapshot_id: 'snapshot', content: localContent,
    });
    const firstPreview = await previewLocalBackup(repo, backup(), 'save-as-new');
    const mapped = firstPreview.remapped_note_ids.n1;
    expect(mapped).toMatch(/^import-[\da-f]{32}$/);
    expect(mapped).not.toBe('n1');
    const first = await importLocalBackup(repo, backup(), 'save-as-new');
    const secondPreview = await previewLocalBackup(repo, backup(), 'save-as-new');
    const second = await importLocalBackup(repo, backup(), 'save-as-new');
    expect(secondPreview.remapped_note_ids.n1).toBe(mapped);
    expect(first.added).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
    expect(second.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    expect(second.duplicates).toBeGreaterThanOrEqual(4);
    expect(await repo.revisions(mapped)).toHaveLength(2);
    repo.close();
  });

  test('reports divergent keyed records as unresolved without overwriting them', async () => {
    const repo = repository();
    await importLocalBackup(repo, backup(), 'merge');
    const changed = backup();
    changed.verifications[0].evidence = '不同验证';
    changed.watchlist[0].snapshot_id = 'different-snapshot';

    const preview = await previewLocalBackup(repo, changed, 'merge');

    expect(preview.unresolved_conflicts).toEqual(['verification:verify-1', 'watch:A']);
    await expect(importLocalBackup(repo, changed, 'merge')).rejects.toThrow('未解决冲突');
    expect(await repo.listVerifications('n1')).toMatchObject([{ evidence: '验证记录' }]);
    expect(await repo.listWatchlist()).toMatchObject([{ snapshot_id: 'snapshot' }]);
    repo.close();
  });

  test('exports explicit profile mapping, counts, full history and no transport call', async () => {
    const repo = repository();
    await importLocalBackup(repo, backup(), 'merge');
    const exported = await exportLocalBackup(repo, new Date('2026-09-21T10:00:00.000Z'));
    expect(exported).toMatchObject({
      format_version: 1,
      exported_at: '2026-09-21T10:00:00.000Z',
      counts: { notes: 1, revisions: 2, verifications: 1, watchlist: 1 },
    });
    expect(exported.profile_mapping.local_profile_id).toBe(await repo.profile());
    expect(exported.profile_mapping.owner_id).toBe('local-browser');
    expect(exported.revisions.map(row => row.content)).toEqual([
      {
        title: '旧版一', content: '', judgment: '判断一', evidence: '依据一',
        invalid_condition: '旧失效条件', verify_date: '2026-09-11',
      },
      {
        title: '旧版二', content: '', judgment: '判断一', evidence: '依据一',
        invalid_condition: '第二条件', verify_date: '2026-09-12',
      },
    ]);
    expect(exported.revisions.map(row => [row.revision_id, row.state])).toEqual([
      ['old-r1', 'draft'], ['old-r2', 'confirmed'],
    ]);
    await repo.putNote({
      note_id: 'n1', expected_revision: 2, key: 'after-import', snapshot_id: 'snapshot',
      content: { ...localContent, title: '导入后编辑' },
    });
    const edited = await exportLocalBackup(repo);
    expect(edited.notes[0].security_id).toBe('A');
    expect(edited.revisions.map(row => row.revision_id)).toEqual(['old-r1', 'old-r2', 'n1:3']);
    repo.close();
  });

  test('rejects corrupt or inconsistent backup before any write', async () => {
    const repo = repository();
    await repo.putNote({
      note_id: 'local', expected_revision: 0, key: 'local', snapshot_id: 'snapshot', content: localContent,
    });
    const before = await exportLocalBackup(repo);
    const damaged = backup();
    damaged.counts.revisions = 99;
    damaged.verifications[0].revision_id = 'missing';
    await expect(importLocalBackup(repo, JSON.stringify(damaged), 'merge')).rejects.toThrow('备份');
    const impossibleDate = backup();
    impossibleDate.revisions[0].content.verification_date = '2026-02-30';
    await expect(importLocalBackup(repo, impossibleDate, 'merge')).rejects.toThrow('备份');
    const duplicateNote = backup();
    duplicateNote.notes.push({ ...duplicateNote.notes[0] });
    duplicateNote.counts.notes = 2;
    await expect(previewLocalBackup(repo, duplicateNote, 'merge')).rejects.toThrow('备份');

    const after = await exportLocalBackup(repo);
    expect({ ...after, exported_at: before.exported_at }).toEqual(before);
    repo.close();
  });

  test('transaction failure rolls back all stores', async () => {
    const repo = repository();
    const original = IDBObjectStore.prototype.add;
    IDBObjectStore.prototype.add = function (value: unknown, key?: IDBValidKey) {
      if (this.name === 'verification_records') {
        this.transaction.abort();
        throw new DOMException('Disk full', 'QuotaExceededError');
      }
      return key === undefined ? original.call(this, value) : original.call(this, value, key);
    };
    try {
      await expect(importLocalBackup(repo, backup(), 'merge')).rejects.toThrow();
    } finally {
      IDBObjectStore.prototype.add = original;
    }
    expect(await repo.listNotes()).toEqual([]);
    expect(await repo.listWatchlist()).toEqual([]);
    repo.close();
  });
});
