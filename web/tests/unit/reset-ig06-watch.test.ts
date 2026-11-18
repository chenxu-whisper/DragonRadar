import 'fake-indexeddb/auto';
import { afterEach, describe, expect, test } from 'vitest';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../src/repositories/local/backup';
import { parseReviewBackup } from '../../src/repositories/local/migrate';
import { LocalRepository, type LocalWatch } from '../../src/repositories/local/repository';

type WatchContext = {
  trade_date: string | null; security_name: string | null;
  sector_id: string | null; sector_name: string | null;
};
type ContextWatch = LocalWatch & { context?: WatchContext };
const original: WatchContext = {
  trade_date: '2026-09-09', security_name: 'Original name',
  sector_id: 'SEC001', sector_name: 'Original sector',
};
const later: WatchContext = {
  trade_date: '2026-09-10', security_name: 'Renamed security',
  sector_id: 'SEC002', sector_name: 'Later sector',
};
const repos: LocalRepository[] = [];
function repository() {
  const repo = new LocalRepository(`ig06-${crypto.randomUUID()}`);
  repos.push(repo);
  return repo;
}
// Test the proposed optional fifth argument without editing production types in RED.
function watch(repo: LocalRepository, action: 'add' | 'remove', key: string, snapshot: string, context?: WatchContext) {
  const put = repo.putWatchlist.bind(repo) as (
    security: string, action: 'add' | 'remove', key: string, snapshot: string, context?: WatchContext,
  ) => Promise<ContextWatch>;
  return put('TEST001', action, key, snapshot, context);
}
afterEach(() => { repos.splice(0).forEach(repo => repo.close()); });

describe('IG06 original watch provenance', () => {
  test('duplicate add, removal, undo and reopen retain original identity, time, publication and context', async () => {
    const repo = repository();
    const first = await watch(repo, 'add', 'first', 'publication-A', original);
    expect(first.context).toEqual(original);
    const duplicate = await watch(repo, 'add', 'duplicate', 'publication-B', later);
    expect(duplicate).toEqual(first);
    const removed = await watch(repo, 'remove', 'remove', 'publication-B', later);
    expect(removed).toMatchObject({
      active: false, revision: 2, snapshot_id: 'publication-A', added_at: first.added_at,
      watch_id: first.watch_id, context: original,
    });
    expect(await repo.listWatchlist()).toEqual([]);
    const undone = await watch(repo, 'add', 'undo', 'publication-B', later);
    expect(undone).toMatchObject({
      active: true, revision: 3, snapshot_id: 'publication-A', added_at: first.added_at,
      watch_id: first.watch_id, context: original,
    });
    const name = (await repo.connect()).name;
    repo.close();
    const reopened = new LocalRepository(name);
    repos.push(reopened);
    expect(await reopened.listWatchlist()).toEqual([undone]);
    expect((await reopened.connect()).version).toBe(5);
  });

  test.each(['trade_date', 'security_name', 'sector_id', 'sector_name'] as const)(
    'same idempotency key with changed %s must fail even on a duplicate add',
    async field => {
      const repo = repository();
      await watch(repo, 'add', 'same-key', 'publication-A', original);
      await expect(watch(repo, 'add', 'same-key', 'publication-A', {
        ...original, [field]: later[field],
      })).rejects.toThrow(/幂等|冲突/);
      expect((await repo.listWatchlist())[0]).toMatchObject({ context: original, revision: 1 });
    },
  );

  test('legacy absence is not backfilled with context from a later duplicate or undo', async () => {
    const repo = repository();
    const first = await watch(repo, 'add', 'legacy', 'publication-A');
    await watch(repo, 'add', 'later', 'publication-B', later);
    await watch(repo, 'remove', 'remove', 'publication-B', later);
    await watch(repo, 'add', 'undo', 'publication-B', later);
    const row = (await repo.listWatchlist())[0] as ContextWatch;
    expect(row.snapshot_id).toBe('publication-A');
    expect(row.added_at).toBe(first.added_at);
    expect(row.context).toEqual(first.context);
  });

  test('backup preserves context and inactive tombstones through parse, restore and repeat import', async () => {
    const source = repository(), target = repository();
    const first = await watch(source, 'add', 'first', 'publication-A', original);
    await watch(source, 'remove', 'remove', 'publication-B', later);
    const backup = await exportLocalBackup(source);
    expect(backup.watchlist[0]).toMatchObject({
      snapshot_id: 'publication-A', active: false, added_at: first.added_at, context: original,
    });
    const parsed = parseReviewBackup(JSON.stringify(backup));
    expect(parsed.watchlist[0]).toMatchObject({ context: original });
    await importLocalBackup(target, await previewLocalBackup(target, parsed, 'merge'));
    expect((await target.backupState()).watchlist[0]).toMatchObject({ context: original, active: false });
    const repeated = await previewLocalBackup(target, parsed, 'merge');
    expect(repeated.unresolved_conflicts).toEqual([]);
    expect(repeated.added.watchlist).toBe(0);
    const restored = await watch(target, 'add', 'undo', 'publication-B', later);
    expect(restored).toMatchObject({ snapshot_id: 'publication-A', added_at: first.added_at, context: original });
  });

  test('format v1 legacy watch backup remains importable without invented context', async () => {
    const source = repository(), target = repository();
    await watch(source, 'add', 'legacy', 'publication-A');
    const backup = await exportLocalBackup(source);
    const legacy = structuredClone(backup);
    delete (legacy.watchlist[0] as unknown as Record<string, unknown>).context;
    await importLocalBackup(target, await previewLocalBackup(target, JSON.stringify(legacy), 'merge'));
    expect((await target.listWatchlist())[0]).toMatchObject({ snapshot_id: 'publication-A' });
    expect((await target.listWatchlist())[0] as ContextWatch).not.toHaveProperty('context.security_name', later.security_name);
  });

  test('divergent context in an otherwise identical imported watch is a conflict, not a duplicate', async () => {
    const source = repository(), target = repository();
    await watch(source, 'add', 'first', 'publication-A', original);
    const backup = await exportLocalBackup(source);
    // Keep this case independent of the missing exporter.
    Object.assign(backup.watchlist[0], { context: original });
    await importLocalBackup(target, await previewLocalBackup(target, backup, 'merge'));
    const changed = structuredClone(backup);
    Object.assign(changed.watchlist[0], { context: later });
    const plan = await previewLocalBackup(target, changed, 'merge');
    expect(plan.unresolved_conflicts).toEqual(['watch:TEST001']);
    await expect(importLocalBackup(target, plan)).rejects.toThrow(/冲突/);
    expect((await target.backupState()).watchlist[0]).toMatchObject({ context: original });
  });
});
