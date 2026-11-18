import 'fake-indexeddb/auto';
import { afterEach, describe, expect, test } from 'vitest';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../src/repositories/local/backup';
import { parseReviewBackup } from '../../src/repositories/local/migrate';
import { LocalRepository, type SaveVerification } from '../../src/repositories/local/repository';
import type { ResetKlineData } from '../../src/types';

const content = {
  title: 'Confirmed original', content: 'Original text', judgment: 'Strength persists',
  evidence: 'Original facts', invalid_condition: 'Close below 10', verify_date: '2026-09-10',
};
const interval: ResetKlineData = {
  security_id: 'TEST001', adjustment: 'raw', adjusted_start: '2026-09-09',
  adjusted_end: '2026-09-10', chart_type: 'candlestick',
  points: [
    { trade_date: '2026-09-09', open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100 },
    { trade_date: '2026-09-10', open: 11, high: 13, low: 10, close: 12, volume: 200, amount: 2400 },
  ],
};
const repos: LocalRepository[] = [];
function repository() {
  const repo = new LocalRepository(`collected3-${crypto.randomUUID()}`);
  repos.push(repo);
  return repo;
}
function confirm(repo: LocalRepository) {
  return repo.putNote({
    note_id: 'n1', expected_revision: 0, key: 'confirm', snapshot_id: 'publication-A',
    trade_date: '2026-09-09', security_id: 'TEST001', content, state: 'confirmed',
  });
}
function command(): SaveVerification {
  return {
    verification_id: 'v1', note_id: 'n1', revision: 1, key: 'verify',
    snapshot_id: 'publication-B', outcome: 'supported', evidence: 'Actual interval supports original',
    supersedes: null, fact_interval: structuredClone(interval),
    context: {
      requested_start: '2026-09-09', requested_end: '2026-09-10',
      adjusted_start: '2026-09-09', adjusted_end: '2026-09-10',
      trade_date: '2026-09-10', as_of: '2026-09-10T10:00:00Z',
      source: 'synthetic-market', source_version: 'market-v1', rule_version: 'rule-v1',
      published_at: '2026-09-10T10:01:00Z', publication_revision: 1,
      calendar_sessions: ['2026-09-09', '2026-09-10'],
    },
  };
}
afterEach(() => repos.splice(0).forEach(repo => repo.close()));

describe('collected3 copy export semantic bindings', () => {
  test('original plus save-as-new re-exports unique revision and verification identities with intact corrections', async () => {
    const repo = repository(), restored = repository();
    await confirm(repo);
    await repo.putVerification(command());
    await repo.putVerification({
      ...command(), verification_id: 'v2', key: 'correction', supersedes: 'v1', outcome: 'weakened',
    });
    const original = await exportLocalBackup(repo);
    const copyPlan = await previewLocalBackup(repo, original, 'save-as-new');
    await importLocalBackup(repo, copyPlan);
    const copiedId = copyPlan.remapped_note_ids.n1;
    const combined = await exportLocalBackup(repo);
    expect(combined.counts).toEqual({ notes: 2, revisions: 2, verifications: 4, watchlist: 0 });
    expect(new Set(combined.revisions.map(row => row.revision_id)).size).toBe(2);
    expect(new Set(combined.verifications.map(row => row.verification_id)).size).toBe(4);
    const parsed = parseReviewBackup(JSON.stringify(combined));
    for (const id of ['n1', copiedId]) {
      const revision = parsed.revisions.find(row => row.note_id === id)!;
      const rows = parsed.verifications.filter(row => row.note_id === id);
      expect(revision.content).toEqual(original.revisions[0].content);
      expect(rows.every(row => row.revision_id === revision.revision_id)).toBe(true);
      const first = rows.find(row => row.supersedes === null)!;
      expect(rows.find(row => row.supersedes === first.verification_id)).toMatchObject({
        outcome: 'weakened', context: command().context, fact_interval: interval,
      });
    }
    await importLocalBackup(restored, await previewLocalBackup(restored, parsed, 'merge'));
    expect(await restored.listNotes()).toHaveLength(2);
    expect(await restored.listVerifications()).toHaveLength(4);
    const repeat = await previewLocalBackup(repo, original, 'save-as-new');
    expect(repeat.unresolved_conflicts).toEqual([]);
    expect(repeat.added).toEqual({ notes: 0, revisions: 0, verifications: 0, watchlist: 0 });
    expect((await exportLocalBackup(repo)).revisions).toEqual(combined.revisions);
    expect((await exportLocalBackup(repo)).verifications).toEqual(combined.verifications);
  });
});

describe('collected3 required baseline and session provenance', () => {
  test.each(['absent sessions', 'single-day with sessions', 'single-day without sessions', 'missing interior session'])(
    'repository rejects %s without writing a verification or command', async mode => {
      const repo = repository();
      await confirm(repo);
      const input = command();
      if (mode === 'absent sessions') delete input.context!.calendar_sessions;
      else if (mode === 'missing interior session') {
        await repo.putNote({
          note_id: 'n1', expected_revision: 1, key: 'extended', snapshot_id: 'publication-A',
          trade_date: '2026-09-09', security_id: 'TEST001',
          content: { ...content, verify_date: '2026-09-11' }, state: 'confirmed',
        });
        input.revision = 2;
        Object.assign(input.context!, {
          requested_end: '2026-09-11', adjusted_end: '2026-09-11',
          trade_date: '2026-09-11', as_of: '2026-09-11T10:00:00Z',
          calendar_sessions: ['2026-09-09', '2026-09-10', '2026-09-11'],
        });
        input.fact_interval!.adjusted_end = '2026-09-11';
        input.fact_interval!.points[1].trade_date = '2026-09-11';
      } else {
        input.context!.adjusted_start = '2026-09-10';
        input.fact_interval!.adjusted_start = '2026-09-10';
        input.fact_interval!.points = input.fact_interval!.points.slice(1);
        if (mode.endsWith('without sessions')) delete input.context!.calendar_sessions;
        else input.context!.calendar_sessions = ['2026-09-10'];
      }
      const before = await repo.backupState();
      await expect(repo.putVerification(input)).rejects.toThrow(/区间/);
      expect(await repo.backupState()).toEqual(before);
      await repo.putVerification({ ...command(), revision: input.revision,
        ...(mode === 'missing interior session' ? {
          context: { ...input.context!, calendar_sessions: ['2026-09-09', '2026-09-10', '2026-09-11'] },
          fact_interval: { ...input.fact_interval!, points: [...interval.points,
            { ...interval.points[1], trade_date: '2026-09-11' }] },
        } : {}),
      });
      expect(await repo.listVerifications()).toHaveLength(1);
    },
  );

  test.each(['with sessions', 'without sessions'])('backup rejects narrowed single-day evidence %s before import', async mode => {
    const repo = repository();
    await confirm(repo);
    await repo.putVerification(command());
    const source = await exportLocalBackup(repo);
    const row = source.verifications[0];
    row.context!.adjusted_start = '2026-09-10';
    row.fact_interval!.adjusted_start = '2026-09-10';
    row.fact_interval!.points = row.fact_interval!.points.slice(1);
    if (mode === 'without sessions') delete row.context!.calendar_sessions;
    else row.context!.calendar_sessions = ['2026-09-10'];
    const before = await repo.backupState();
    await expect(previewLocalBackup(repo, source, 'save-as-new')).rejects.toThrow(/备份/);
    expect(await repo.backupState()).toEqual(before);
  });

  test('full-bounds legacy evidence remains importable without fabricating calendar provenance', async () => {
    const repo = repository(), target = repository();
    await confirm(repo);
    await repo.putVerification(command());
    const source = await exportLocalBackup(repo);
    delete source.verifications[0].context!.calendar_sessions;
    await importLocalBackup(target, await previewLocalBackup(target, source, 'merge'));
    const roundTrip = parseReviewBackup(await exportLocalBackup(target));
    expect(roundTrip.verifications[0].context).not.toHaveProperty('calendar_sessions');
    expect(roundTrip.verifications[0].fact_interval).toEqual(interval);
  });
});
