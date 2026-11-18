import 'fake-indexeddb/auto';
import { afterEach, describe, expect, test, vi } from 'vitest';
import type { ResetKlineData } from '../../src/types';
import { exportLocalBackup, importLocalBackup, previewLocalBackup } from '../../src/repositories/local/backup';
import { parseReviewBackup, type ReviewBackup } from '../../src/repositories/local/migrate';
import {
  LocalRepository, type LocalVerification, type SaveNote,
} from '../../src/repositories/local/repository';

const content = {
  title: 'Original judgment', content: 'Original text', judgment: 'Strength persists',
  evidence: 'Original publication facts', invalid_condition: 'Close below 10',
  verify_date: '2026-09-10',
};
const interval: ResetKlineData = {
  security_id: 'TEST001', adjustment: 'raw', adjusted_start: '2026-09-09',
  adjusted_end: '2026-09-10', chart_type: 'candlestick',
  points: [
    { trade_date: '2026-09-09', open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100 },
    { trade_date: '2026-09-10', open: 11, high: 13, low: 10, close: 12, volume: 200, amount: 2400 },
  ],
};
type VerificationContext = {
  requested_start: string; requested_end: string; adjusted_start: string; adjusted_end: string;
  trade_date: string; as_of: string; source: string; source_version: string; rule_version: string;
  calendar_sessions?: string[];
};
const context: VerificationContext = {
  requested_start: '2026-09-09', requested_end: '2026-09-10',
  adjusted_start: '2026-09-09', adjusted_end: '2026-09-10',
  trade_date: '2026-09-10', as_of: '2026-09-10T10:00:00Z',
  source: 'synthetic-market', source_version: 'market-v1', rule_version: 'rule-v1',
};
type VerificationCommand = {
  verification_id: string; note_id: string; revision: number; key: string;
  snapshot_id: string | null; outcome: LocalVerification['outcome'];
  evidence: string; supersedes: string | null; fact_interval: ResetKlineData | null;
  context: VerificationContext | null;
};
type Verified = LocalVerification & {
  context?: VerificationContext | null; fact_interval: ResetKlineData | null; current_verification_id?: string;
};
const repos: LocalRepository[] = [];
function repository(name = `ig07-${crypto.randomUUID()}`) {
  const repo = new LocalRepository(name);
  repos.push(repo);
  return repo;
}
function save(repo: LocalRepository, revision: number, state: 'draft' | 'confirmed', key = `note-${revision}`) {
  const input: SaveNote & { state: 'draft' | 'confirmed' } = {
    note_id: 'n1', expected_revision: revision, key, snapshot_id: 'publication-A',
    trade_date: '2026-09-09', security_id: 'TEST001', content, state,
  };
  return repo.putNote(input);
}
function command(revision = 1): VerificationCommand {
  return {
    verification_id: 'v1', note_id: 'n1', revision, key: 'verification-first',
    snapshot_id: 'publication-B', outcome: 'supported', evidence: 'Human decision from actual interval',
    supersedes: null, fact_interval: structuredClone(interval),
    context: { ...context, calendar_sessions: interval.points.map(point => point.trade_date) },
  };
}
function verify(repo: LocalRepository, input: VerificationCommand): Promise<Verified> {
  const writer = (repo as unknown as { putVerification?: (input: VerificationCommand) => Promise<Verified> }).putVerification;
  expect(writer, 'IG07 requires a real atomic repository writer').toBeTypeOf('function');
  return writer!.call(repo, input);
}
function heads(rows: LocalVerification[]) {
  const superseded = new Set(rows.map(row => row.supersedes).filter(Boolean));
  return rows.filter(row => !superseded.has(row.verification_id));
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  repos.splice(0).forEach(repo => repo.close());
});

function legacyBackup(): ReviewBackup {
  return {
    format_version: 1, exported_at: '2026-09-11T10:00:00Z',
    profile_mapping: { owner_id: 'legacy-owner', local_profile_id: 'legacy-profile' },
    counts: { notes: 1, revisions: 1, verifications: 1, watchlist: 0 },
    notes: [{
      note_id: 'n1', snapshot_id: 'publication-A', trade_date: '2026-09-09', security_id: 'TEST001',
      current_revision: 1, created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z',
    }],
    revisions: [{
      revision_id: 'legacy-r1', note_id: 'n1', revision: 1, state: 'confirmed',
      content, saved_at: '2026-09-09T10:00:00Z',
    }],
    verifications: [{
      verification_id: 'legacy-v1', note_id: 'n1', revision_id: 'legacy-r1',
      snapshot_id: 'publication-B', outcome: 'supported', evidence: 'Legacy human decision',
      supersedes: null, created_at: '2026-09-11T09:00:00Z',
    }],
    watchlist: [],
  };
}

describe('IG07 optional frozen provenance backup compatibility', () => {
  test('legacy context-free verification imports as unknown without inventing facts or provenance', async () => {
    const repo = repository();
    const backup = legacyBackup();
    expect(parseReviewBackup(JSON.stringify(backup)).verifications[0]).not.toHaveProperty('context');
    await importLocalBackup(repo, await previewLocalBackup(repo, backup, 'merge'));
    const before = await repo.listVerifications();
    expect(before).toMatchObject([{ verification_id: 'legacy-v1', revision: 1 }]);
    expect(before[0]).not.toHaveProperty('context');
    expect(before[0]).not.toHaveProperty('fact_interval');
    await save(repo, 1, 'draft', 'later-edit');
    expect(await repo.listVerifications()).toEqual(before);
    const roundTrip = parseReviewBackup(JSON.stringify(await exportLocalBackup(repo)));
    expect(roundTrip.verifications[0]).not.toHaveProperty('context');
  });

  test('extended v1 backup freezes optional context and actual facts across export/import', async () => {
    const repo = repository();
    const source = legacyBackup();
    Object.assign(source.verifications[0], { context, fact_interval: interval });
    const parsed = parseReviewBackup(JSON.stringify(source));
    expect(parsed.verifications[0]).toMatchObject({ context, fact_interval: interval });
    await importLocalBackup(repo, await previewLocalBackup(repo, parsed, 'merge'));
    expect((await exportLocalBackup(repo)).verifications[0]).toMatchObject({ context, fact_interval: interval });
    const changed = structuredClone(source);
    Object.assign(changed.verifications[0], { context: { ...context, source_version: 'changed' } });
    const conflict = await previewLocalBackup(repo, changed, 'merge');
    expect(conflict.unresolved_conflicts).toEqual(['verification:legacy-v1']);
    await expect(importLocalBackup(repo, conflict)).rejects.toThrow(/冲突/);
    expect((await repo.listVerifications())[0]).toMatchObject({ context });
  });

  test('legacy not_due keeps null snapshot and unknown provenance on round-trip', async () => {
    const repo = repository();
    const source = legacyBackup();
    Object.assign(source.verifications[0], { outcome: 'not_due', snapshot_id: null, evidence: '' });
    await importLocalBackup(repo, await previewLocalBackup(repo, source, 'merge'));
    const backup = parseReviewBackup(JSON.stringify(await exportLocalBackup(repo)));
    expect(backup.verifications[0]).toMatchObject({ outcome: 'not_due', snapshot_id: null });
    expect(backup.verifications[0]).not.toHaveProperty('context');
  });

  test('backup rejects competing corrections and cycles rather than importing multiple heads', () => {
    const fork = legacyBackup();
    const prior = fork.verifications[0];
    fork.verifications.push(
      { ...prior, verification_id: 'correction-a', supersedes: 'legacy-v1', outcome: 'weakened' },
      { ...prior, verification_id: 'correction-b', supersedes: 'legacy-v1', outcome: 'refuted' },
    );
    fork.counts.verifications = 3;
    expect(() => parseReviewBackup(fork)).toThrow(/备份/);
    const cycle = legacyBackup();
    cycle.verifications[0].supersedes = 'correction';
    cycle.verifications.push({ ...prior, verification_id: 'correction', supersedes: 'legacy-v1' });
    cycle.counts.verifications = 2;
    expect(() => parseReviewBackup(cycle)).toThrow(/备份/);
  });
});

describe('IG07 explicit confirmation and immutable verification', () => {
  test('confirmation writes a distinct new revision, preserves the draft, and is idempotent', async () => {
    const repo = repository();
    const draft = await save(repo, 0, 'draft');
    const confirmed = await save(repo, 1, 'confirmed', 'confirm');
    expect(confirmed).toMatchObject({ revision: 2, state: 'confirmed', content: draft.content });
    expect((await repo.revisions('n1')).find(row => row.revision === 1)).toEqual(draft);
    expect(await save(repo, 1, 'confirmed', 'confirm')).toEqual(confirmed);
    await expect(save(repo, 1, 'draft', 'confirm')).rejects.toThrow(/幂等|冲突/);
    expect(await repo.revisions('n1')).toHaveLength(2);
  });

  test('confirm r1 then edit r2: verification binds original r1 without overwriting either source', async () => {
    const repo = repository();
    const r1 = await save(repo, 0, 'confirmed');
    const edit: SaveNote & { state: 'draft' } = {
      note_id: 'n1', expected_revision: 1, key: 'edit', snapshot_id: 'publication-A',
      content: { ...content, judgment: 'Edited r2 must not replace original' }, state: 'draft',
    };
    const r2 = await repo.putNote(edit);
    const before = await repo.revisions('n1');
    const result = await verify(repo, command());
    expect(result).toMatchObject({
      note_id: 'n1', revision: 1, snapshot_id: 'publication-B', outcome: 'supported',
      fact_interval: interval, context, supersedes: null,
    });
    expect(await repo.revisions('n1')).toEqual(before);
    expect((await repo.listNotes())[0]).toEqual(r2);
    expect((await repo.revisions('n1')).find(row => row.revision === 1)).toEqual(r1);
    expect(await repo.listVerifications('n1')).toEqual([result]);
  });

  test.each(['judgment', 'evidence', 'invalid_condition', 'verify_date'] as const)(
    'confirmation rejects missing %s without partial revisions or commands',
    async field => {
      const repo = repository();
      const input: SaveNote & { state: 'confirmed' } = {
        note_id: 'n1', expected_revision: 0, key: 'confirm', snapshot_id: 'publication-A',
        trade_date: '2026-09-09', security_id: 'TEST001',
        content: { ...content, [field]: '' }, state: 'confirmed',
      };
      await expect(repo.putNote(input)).rejects.toThrow();
      expect(await repo.listNotes()).toEqual([]);
      expect(await repo.revisions('n1')).toEqual([]);
    },
  );

  test.each(['supported', 'weakened', 'refuted', 'not_due'] as const)(
    'persists explicit %s and reads the same immutable result after reopening',
    async outcome => {
      const repo = repository();
      await save(repo, 0, 'confirmed');
      const input = { ...command(), outcome };
      if (outcome === 'not_due') {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(new Date('2026-09-09T12:00:00Z'));
        input.snapshot_id = null;
        input.fact_interval = null;
        input.context = null;
        input.evidence = '';
      }
      const first = await verify(repo, input);
      expect(await verify(repo, input)).toEqual(first);
      const name = (await repo.connect()).name;
      repo.close();
      const reopened = repository(name);
      expect(await reopened.listVerifications('n1')).toEqual([first]);
      expect((await reopened.connect()).version).toBe(5);
    },
  );

  test('draft, wrong revision, wrong security, adjusted basis and missing interval fail closed', async () => {
    const repo = repository();
    await save(repo, 0, 'draft');
    await expect(verify(repo, command())).rejects.toThrow();
    await save(repo, 1, 'confirmed');
    for (const input of [
      command(99),
      { ...command(2), fact_interval: { ...interval, security_id: 'OTHER' } },
      { ...command(2), fact_interval: { ...interval, adjustment: 'qfq' as const } },
      { ...command(2), fact_interval: null },
      { ...command(2), fact_interval: { ...interval, points: [] } },
      { ...command(2), fact_interval: { ...interval, adjusted_start: '2026-09-11' } },
    ]) await expect(verify(repo, input)).rejects.toThrow();
    expect(await repo.listVerifications()).toEqual([]);
  });

  test('a missing endpoint remains unavailable, not a single-day substitute', async () => {
    const repo = repository();
    await save(repo, 0, 'confirmed');
    await expect(verify(repo, {
      ...command(), fact_interval: { ...interval, points: interval.points.slice(1) },
    })).rejects.toThrow();
    expect(await repo.listVerifications()).toEqual([]);
  });

  test('changed outcome, publication, interval or supersedes cannot reuse a command key', async () => {
    const repo = repository();
    await save(repo, 0, 'confirmed');
    const input = command();
    await verify(repo, input);
    for (const changed of [
      { ...input, outcome: 'refuted' as const },
      { ...input, snapshot_id: 'publication-C' },
      { ...input, fact_interval: { ...interval, points: interval.points.map(point => ({ ...point, close: 99 })) } },
      { ...input, supersedes: 'v1' },
      { ...input, context: { ...context, source_version: 'market-v2' } },
      { ...input, context: { ...context, requested_end: '2026-09-11' } },
    ]) await expect(verify(repo, changed)).rejects.toThrow(/幂等|冲突/);
    expect(await repo.listVerifications()).toHaveLength(1);
  });

  test('transaction abort rolls back verification and command; original key can retry', async () => {
    const repo = repository();
    await save(repo, 0, 'confirmed');
    const before = await repo.backupState();
    let writes = 0;
    const original = IDBObjectStore.prototype.put;
    const spy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (
      this: IDBObjectStore, value: unknown, key?: IDBValidKey,
    ) {
      if (this.name === 'commands') {
        writes += 1;
        this.transaction.abort();
        throw new DOMException('Disk full', 'QuotaExceededError');
      }
      return key === undefined ? original.call(this, value) : original.call(this, value, key);
    });
    await expect(verify(repo, command())).rejects.toThrow();
    spy.mockRestore();
    expect(writes).toBe(1);
    expect(await repo.backupState()).toEqual(before);
    const result = await verify(repo, command());
    expect(await repo.listVerifications()).toEqual([result]);
  });

  test('two competing corrections append exactly one winner and keep a single head', async () => {
    const name = `ig07-${crypto.randomUUID()}`;
    const a = repository(name), b = repository(name);
    await save(a, 0, 'confirmed');
    const first = await verify(a, command());
    const results = await Promise.allSettled([
      verify(a, { ...command(), verification_id: 'v2', key: 'correction-a', outcome: 'weakened', supersedes: 'v1' }),
      verify(b, { ...command(), verification_id: 'v3', key: 'correction-b', outcome: 'refuted', supersedes: 'v1' }),
    ]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
    const rows = await a.listVerifications();
    expect(rows).toHaveLength(2);
    expect(rows.find(row => row.verification_id === 'v1')).toEqual(first);
    expect(heads(rows)).toHaveLength(1);
    expect(await verify(a, command())).toMatchObject({
      verification_id: 'v1', current_verification_id: heads(rows)[0].verification_id,
    });
    expect(await a.listVerifications()).toEqual(rows);
  });

  test('backup round-trip preserves confirmed r1, edited r2, interval facts and correction chain', async () => {
    const repo = repository(), target = repository();
    await save(repo, 0, 'confirmed');
    await save(repo, 1, 'draft');
    await verify(repo, command());
    await verify(repo, { ...command(), verification_id: 'v2', key: 'correction', outcome: 'refuted', supersedes: 'v1' });
    const backup = await exportLocalBackup(repo);
    expect(backup.revisions.map(row => row.state)).toEqual(['confirmed', 'draft']);
    expect(backup.verifications).toMatchObject([
      { revision_id: 'n1:1', fact_interval: interval, context },
      { revision_id: 'n1:1', supersedes: 'v1', fact_interval: interval, context },
    ]);
    const parsed = parseReviewBackup(JSON.stringify(backup));
    await importLocalBackup(target, await previewLocalBackup(target, parsed, 'merge'));
    expect((await target.revisions('n1')).find(row => row.revision === 1)).toMatchObject({ state: 'confirmed' });
    expect(heads(await target.listVerifications())).toHaveLength(1);
    expect(await target.listVerifications()).toMatchObject([
      { revision: 1, fact_interval: interval, context }, { revision: 1, supersedes: 'v1', fact_interval: interval, context },
    ]);
    const repeat = await previewLocalBackup(target, parsed, 'merge');
    expect(repeat.unresolved_conflicts).toEqual([]);
    expect(repeat.added.verifications).toBe(0);
  });

  test.each([
    ['absent context', null],
    ['wrong confirmed baseline', { ...context, requested_start: '2026-09-08' }],
    ['wrong confirmed verify date', { ...context, requested_end: '2026-09-11' }],
    ['publication before requested end', { ...context, trade_date: '2026-09-09', as_of: '2026-09-09T10:00:00Z' }],
    ['wrong actual start', { ...context, adjusted_start: '2026-09-08' }],
    ['wrong actual end', { ...context, adjusted_end: '2026-09-09' }],
    ['invalid as-of', { ...context, as_of: 'invalid' }],
    ['missing source version', { ...context, source_version: '' }],
  ] as const)('new non-not-due write rejects %s with no audit mutation', async (_label, damaged) => {
    const repo = repository();
    await save(repo, 0, 'confirmed');
    const before = await repo.backupState();
    await expect(verify(repo, { ...command(), context: damaged })).rejects.toThrow();
    expect(await repo.backupState()).toEqual(before);
  });

  test('calendar-adjusted actual bounds are frozen separately from requested bounds', async () => {
    const repo = repository();
    const input: SaveNote & { state: 'confirmed' } = {
      note_id: 'n1', expected_revision: 0, key: 'weekend', snapshot_id: 'publication-A',
      trade_date: '2026-09-09', security_id: 'TEST001',
      content: { ...content, verify_date: '2026-09-12' }, state: 'confirmed',
    };
    await repo.putNote(input);
    const actual = {
      ...interval, adjusted_end: '2026-09-11',
      points: [...interval.points, {
        trade_date: '2026-09-11', open: 12, high: 14, low: 11, close: 13, volume: 300, amount: 3900,
      }],
    };
    const frozen = {
      ...context, requested_end: '2026-09-12', adjusted_end: '2026-09-11',
      trade_date: '2026-09-14', as_of: '2026-09-14T10:00:00Z',
      calendar_sessions: actual.points.map(point => point.trade_date),
    };
    const record = await verify(repo, { ...command(), fact_interval: actual, context: frozen });
    expect(record).toMatchObject({ fact_interval: actual, context: frozen });
    expect((await exportLocalBackup(repo)).verifications[0]).toMatchObject({ context: frozen });
  });

  test('not_due is explicit time status with null publication/context, never a missing-facts fallback', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-09T12:00:00Z'));
    const repo = repository();
    await save(repo, 0, 'confirmed');
    const notDue: VerificationCommand = {
      ...command(), outcome: 'not_due', snapshot_id: null, context: null, fact_interval: null,
      evidence: 'Verification date has not arrived',
    };
    const result = await verify(repo, notDue);
    expect(result).toMatchObject({ outcome: 'not_due', snapshot_id: null, context: null });
    await expect(verify(repo, {
      ...command(), key: 'missing-facts', verification_id: 'v2', fact_interval: null,
    })).rejects.toThrow();
    expect(await repo.listVerifications()).toEqual([result]);
  });

  test('not_due refuses a publication/context and refuses a date already due', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-09T12:00:00Z'));
    const repo = repository();
    await save(repo, 0, 'confirmed');
    await expect(verify(repo, { ...command(), outcome: 'not_due' })).rejects.toThrow();
    vi.setSystemTime(new Date('2026-09-11T12:00:00Z'));
    await expect(verify(repo, {
      ...command(), outcome: 'not_due', snapshot_id: null, context: null, fact_interval: null,
    })).rejects.toThrow();
    expect(await repo.listVerifications()).toEqual([]);
  });
});
