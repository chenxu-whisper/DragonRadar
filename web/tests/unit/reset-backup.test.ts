import 'fake-indexeddb/auto';
import { describe, expect, test } from 'vitest';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../src/repositories/local/backup';
import {
  BACKUP_LIMITS, parseReviewBackup, type ReviewBackup,
} from '../../src/repositories/local/migrate';
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
      note_id: 'n1', snapshot_id: 'snapshot', trade_date: '2026-09-10', security_id: 'A',
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
async function restore(repo: LocalRepository, source: ReviewBackup, decision: 'merge' | 'save-as-new') {
  return importLocalBackup(repo, await previewLocalBackup(repo, source, decision));
}

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

    const result = await restore(repo, source, 'merge');

    expect(result.added).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
    expect((await repo.listNotes())[0]).toMatchObject({
      note_id: 'n1',
      trade_date: '2026-09-10',
      revision: 2,
      content: { title: '旧版二', content: '', invalid_condition: '第二条件', verify_date: '2026-09-12' },
    });
    expect((await repo.revisions('n1')).map(row => row.content.title)).toEqual(['旧版二', '旧版一']);
    expect(await repo.listVerifications('n1')).toMatchObject([{
      verification_id: 'verify-1', revision: 2, evidence: '验证记录',
    }]);
    repo.close();
  });

  test('merge appends source history behind unrelated local revisions in backup order', async () => {
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
      duplicates: 0,
      appended_revisions: 2,
      remapped_revisions: { 'n1:1': 3, 'n1:2': 4 },
      unresolved_conflicts: [],
    });
    await restore(repo, backup(), 'merge');
    const revisions = await repo.revisions('n1');
    expect(revisions.map(row => [row.revision, row.content.title])).toEqual([
      [4, '旧版二'], [3, '旧版一'], [2, '本地版本'], [1, '旧版一'],
    ]);
    expect(await repo.listVerifications('n1')).toMatchObject([{ revision: 4 }]);
    repo.close();
  });

  test('preserves distinct source revisions when unchanged content is confirmed', async () => {
    const repo = repository();
    const source = backup();
    source.revisions[1].content = { ...source.revisions[0].content };

    const result = await restore(repo, source, 'merge');

    expect(result.added).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
    expect((await repo.revisions('n1')).map(row => ({
      revision: row.revision,
      source_revision_id: row.source_revision_id,
      state: row.state,
    }))).toEqual([
      { revision: 2, source_revision_id: 'old-r2', state: 'confirmed' },
      { revision: 1, source_revision_id: 'old-r1', state: 'draft' },
    ]);
    expect((await repo.listNotes())[0]).toMatchObject({
      revision: 2,
      source_revision_id: 'old-r2',
      state: 'confirmed',
    });
    expect(await repo.listVerifications('n1')).toMatchObject([{
      verification_id: 'verify-1',
      revision: 2,
    }]);
    repo.close();
  });

  test('uses backup and source revision provenance for exact repeat imports', async () => {
    const repo = repository();
    const firstSource = backup('source-profile-a');
    await restore(repo, firstSource, 'merge');
    const laterExport = structuredClone(firstSource);
    laterExport.exported_at = '2026-09-21T09:00:00+00:00';
    const sameSource = await restore(repo, laterExport, 'merge');
    expect(sameSource.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    expect(sameSource.remapped_revisions).toEqual({ 'n1:1': 1, 'n1:2': 2 });

    const secondSource = structuredClone(firstSource);
    secondSource.exported_at = '2026-09-21T09:30:00+00:00';
    secondSource.profile_mapping.local_profile_id = 'source-profile-b';
    secondSource.verifications[0].verification_id = 'verify-2';

    const second = await restore(repo, secondSource, 'merge');
    expect(second.added.revisions).toBe(2);
    expect(second.remapped_revisions).toEqual({ 'n1:1': 3, 'n1:2': 4 });

    await repo.putNote({
      note_id: 'n1',
      expected_revision: 4,
      key: 'later-same-content',
      snapshot_id: 'snapshot',
      content: {
        title: '旧版二', content: '', judgment: '判断一', evidence: '依据一',
        invalid_condition: '第二条件', verify_date: '2026-09-12',
      },
    });
    const repeated = await restore(repo, firstSource, 'merge');

    expect(repeated.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    expect(repeated.remapped_revisions).toEqual({ 'n1:1': 1, 'n1:2': 2 });
    expect((await repo.listVerifications('n1')).find(row => row.verification_id === 'verify-1'))
      .toMatchObject({ revision: 2 });
    expect(await repo.revisions('n1')).toHaveLength(5);
    repo.close();
  });

  test('rejects an approved import plan when IndexedDB changed after preview', async () => {
    const repo = repository();
    const approved = await previewLocalBackup(repo, backup(), 'merge');
    expect(Object.isFrozen(approved)).toBe(true);
    expect(Object.isFrozen(approved.expected_state.revisions)).toBe(true);
    const serializedPlan = JSON.parse(JSON.stringify(approved)) as typeof approved;
    await repo.putNote({
      note_id: 'concurrent-note',
      expected_revision: 0,
      key: 'concurrent-save',
      snapshot_id: 'snapshot',
      content: localContent,
    });
    await expect(importLocalBackup(repo, serializedPlan)).rejects.toThrow('预览已过期');
    expect((await repo.listNotes()).map(row => row.note_id)).toEqual(['concurrent-note']);
    repo.close();
  });

  test('save-as-new uses stable source provenance across later exports', async () => {
    const repo = repository();
    await repo.putNote({
      note_id: 'n1', expected_revision: 0, key: 'local', snapshot_id: 'snapshot', content: localContent,
    });
    const firstPreview = await previewLocalBackup(repo, backup(), 'save-as-new');
    const mapped = firstPreview.remapped_note_ids.n1;
    expect(mapped).toMatch(/^import-[\da-f]{32}$/);
    expect(mapped).not.toBe('n1');
    const first = await restore(repo, backup(), 'save-as-new');
    const laterExport = backup();
    laterExport.exported_at = '2026-09-21T09:00:00+00:00';
    const secondPreview = await previewLocalBackup(repo, laterExport, 'save-as-new');
    const second = await restore(repo, laterExport, 'save-as-new');
    expect(secondPreview.remapped_note_ids.n1).toBe(mapped);
    expect(first.added).toEqual({ notes: 1, revisions: 2, verifications: 1, watchlist: 1 });
    expect(second.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    expect(second.duplicates).toBeGreaterThanOrEqual(4);
    expect(await repo.revisions(mapped)).toHaveLength(2);
    repo.close();
  });

  test('reports divergent keyed records as unresolved without overwriting them', async () => {
    const repo = repository();
    await restore(repo, backup(), 'merge');
    const changed = backup();
    changed.verifications[0].evidence = '不同验证';
    changed.watchlist[0].snapshot_id = 'different-snapshot';

    const preview = await previewLocalBackup(repo, changed, 'merge');

    expect(preview.unresolved_conflicts).toEqual(['verification:verify-1', 'watch:A']);
    await expect(restore(repo, changed, 'merge')).rejects.toThrow('未解决冲突');
    expect(await repo.listVerifications('n1')).toMatchObject([{ evidence: '验证记录' }]);
    expect(await repo.listWatchlist()).toMatchObject([{ snapshot_id: 'snapshot' }]);
    repo.close();
  });

  test('round-trips a locally created watch without a false conflict', async () => {
    const repo = repository();
    await repo.putWatchlist('A', 'add', 'watch-command', 'snapshot');
    const exported = await exportLocalBackup(repo, new Date('2026-09-21T10:00:00.000Z'));

    const preview = await previewLocalBackup(repo, JSON.stringify(exported), 'merge');

    expect(preview.unresolved_conflicts).toEqual([]);
    expect(preview.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    await expect(importLocalBackup(repo, preview)).resolves.toBe(preview);
    expect(await repo.listWatchlist()).toHaveLength(1);
    repo.close();
  });

  test('exports explicit profile mapping, counts, full history and no transport call', async () => {
    const repo = repository();
    await restore(repo, backup(), 'merge');
    const exported = await exportLocalBackup(repo, new Date('2026-09-21T10:00:00.000Z'));
    expect(exported).toMatchObject({
      format_version: 1,
      exported_at: '2026-09-21T10:00:00.000Z',
      counts: { notes: 1, revisions: 2, verifications: 1, watchlist: 1 },
    });
    expect(exported.profile_mapping.local_profile_id).toBe(await repo.profile());
    expect(exported.profile_mapping.owner_id).toBe('local-browser');
    expect(exported.notes[0].trade_date).toBe('2026-09-10');
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
    await expect(previewLocalBackup(repo, JSON.stringify(damaged), 'merge')).rejects.toThrow('备份');
    const impossibleDate = backup();
    impossibleDate.revisions[0].content.verification_date = '2026-02-30';
    await expect(previewLocalBackup(repo, impossibleDate, 'merge')).rejects.toThrow('备份');
    const duplicateNote = backup();
    duplicateNote.notes.push({ ...duplicateNote.notes[0] });
    duplicateNote.counts.notes = 2;
    await expect(previewLocalBackup(repo, duplicateNote, 'merge')).rejects.toThrow('备份');

    const after = await exportLocalBackup(repo);
    expect({ ...after, exported_at: before.exported_at }).toEqual(before);
    repo.close();
  });

  test.each([
    ['missing note trade date', (source: ReviewBackup) => { source.notes[0].trade_date = null; }],
    ['blank judgment', (source: ReviewBackup) => { source.revisions[1].content.judgment = ' '; }],
    ['blank evidence', (source: ReviewBackup) => { source.revisions[1].content.evidence = ''; }],
    ['blank invalid condition', (source: ReviewBackup) => { source.revisions[1].content.invalid_condition = null; }],
    ['missing verify date', (source: ReviewBackup) => { source.revisions[1].content.verify_date = null; }],
    ['verify date before trade date', (source: ReviewBackup) => { source.revisions[1].content.verify_date = '2026-09-09'; }],
  ])('rejects a confirmed revision with %s', async (_name, damage) => {
    const source = backup();
    damage(source);
    expect(() => parseReviewBackup(source)).toThrow('备份');
  });

  test.each([
    ['draft revision', (source: ReviewBackup) => { source.verifications[0].revision_id = 'old-r1'; }],
    ['blank evidence', (source: ReviewBackup) => { source.verifications[0].evidence = ' '; }],
    ['missing snapshot', (source: ReviewBackup) => { source.verifications[0].snapshot_id = null; }],
    ['not-due snapshot', (source: ReviewBackup) => {
      source.verifications[0].outcome = 'not_due';
    }],
  ])('rejects a verification with %s', async (_name, damage) => {
    const source = backup();
    damage(source);
    expect(() => parseReviewBackup(source)).toThrow('备份');
  });

  test('accepts historical not-due verification without comparing it to today', () => {
    const source = backup();
    source.verifications[0].outcome = 'not_due';
    source.verifications[0].snapshot_id = null;
    source.verifications[0].evidence = '';
    source.revisions[1].content.verify_date = '2026-09-10';

    expect(parseReviewBackup(source).verifications[0]).toMatchObject({
      outcome: 'not_due',
      snapshot_id: null,
    });
  });

  test('enforces byte, array, total-record, identifier, timestamp and text limits', () => {
    expect(() => parseReviewBackup(' '.repeat(BACKUP_LIMITS.backup_bytes + 1))).toThrow('备份');

    const tooMany = backup() as unknown as Record<string, unknown>;
    tooMany.notes = new Array(BACKUP_LIMITS.array_records + 1);
    expect(() => parseReviewBackup(tooMany)).toThrow('备份');

    const tooManyTotal = backup() as unknown as Record<string, unknown>;
    for (const key of ['notes', 'revisions', 'verifications', 'watchlist']) {
      tooManyTotal[key] = new Array(BACKUP_LIMITS.total_records / 4 + 1);
    }
    expect(() => parseReviewBackup(tooManyTotal)).toThrow('备份');

    const longId = backup();
    longId.notes[0].note_id = 'i'.repeat(BACKUP_LIMITS.identifier + 1);
    expect(() => parseReviewBackup(longId)).toThrow('备份');

    const longTimestamp = backup();
    longTimestamp.exported_at = `${'2'.repeat(BACKUP_LIMITS.timestamp)}Z`;
    expect(() => parseReviewBackup(longTimestamp)).toThrow('备份');

    const longEvidence = backup();
    longEvidence.verifications[0].evidence = 'e'.repeat(BACKUP_LIMITS.text + 1);
    expect(() => parseReviewBackup(longEvidence)).toThrow('备份');
  });

  test('rejects mismatched note and revision references', () => {
    const source = backup();
    source.notes.push({
      note_id: 'n2',
      snapshot_id: 'snapshot',
      trade_date: '2026-09-10',
      security_id: 'B',
      current_revision: 1,
      created_at: '2026-09-20T08:00:00.000Z',
      updated_at: '2026-09-20T08:00:00.000Z',
    });
    source.revisions.push({
      revision_id: 'n2-r1',
      note_id: 'n2',
      revision: 1,
      state: 'draft',
      content: { ...oldContent, title: 'Second note' },
      saved_at: '2026-09-20T08:00:00.000Z',
    });
    source.verifications[0].note_id = 'n2';
    source.counts = { notes: 2, revisions: 3, verifications: 1, watchlist: 1 };

    expect(() => parseReviewBackup(source)).toThrow('备份');
  });

  test('rejects supersedes links across source notes or source revisions', async () => {
    const repo = repository();
    const crossRevision = backup();
    crossRevision.verifications.unshift({
      verification_id: 'verify-prior',
      note_id: 'n1',
      revision_id: 'old-r1',
      snapshot_id: 'verification-snapshot',
      outcome: 'weakened',
      evidence: 'prior evidence',
      supersedes: null,
      created_at: '2026-09-21T07:00:00.000Z',
    });
    crossRevision.verifications[1].supersedes = 'verify-prior';
    crossRevision.counts.verifications = 2;
    await expect(previewLocalBackup(repo, crossRevision, 'merge')).rejects.toThrow('备份');

    const crossNote = backup();
    crossNote.notes.push({
      note_id: 'n2',
      snapshot_id: 'snapshot',
      trade_date: '2026-09-10',
      security_id: 'B',
      current_revision: 1,
      created_at: '2026-09-20T08:00:00.000Z',
      updated_at: '2026-09-20T08:00:00.000Z',
    });
    crossNote.revisions.push({
      revision_id: 'n2-r1',
      note_id: 'n2',
      revision: 1,
      state: 'confirmed',
      content: { ...oldContent, title: 'Second note' },
      saved_at: '2026-09-20T08:00:00.000Z',
    });
    crossNote.verifications.push({
      verification_id: 'verify-n2',
      note_id: 'n2',
      revision_id: 'n2-r1',
      snapshot_id: 'verification-snapshot',
      outcome: 'supported',
      evidence: 'second note evidence',
      supersedes: 'verify-1',
      created_at: '2026-09-21T09:00:00.000Z',
    });
    crossNote.counts = { notes: 2, revisions: 3, verifications: 2, watchlist: 1 };
    await expect(previewLocalBackup(repo, crossNote, 'merge')).rejects.toThrow('备份');
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
      await expect(restore(repo, backup(), 'merge')).rejects.toThrow();
    } finally {
      IDBObjectStore.prototype.add = original;
    }
    expect(await repo.listNotes()).toEqual([]);
    expect(await repo.listWatchlist()).toEqual([]);
    repo.close();
  });
});
