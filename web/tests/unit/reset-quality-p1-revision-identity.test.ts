import { describe, expect, test } from 'vitest';
import { parseReviewBackup, planReviewImport, type ReviewBackup } from '../../src/repositories/local/migrate';
import { validateVerificationFacts } from '../../src/repositories/local/verification-validation';

function backup(): ReviewBackup {
  return {
    format_version: 1, exported_at: '2026-09-11T10:00:00Z',
    profile_mapping: { owner_id: 'owner', local_profile_id: 'profile' },
    counts: { notes: 1, revisions: 1, verifications: 1, watchlist: 0 },
    notes: [{
      note_id: 'n1', snapshot_id: 'publication-A', trade_date: '2026-09-09', security_id: 'TEST001',
      current_revision: 1, created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z',
    }],
    revisions: [{
      revision_id: 'r1', note_id: 'n1', revision: 1, state: 'confirmed',
      content: { title: 'Original', content: '', judgment: 'Strength', evidence: 'Facts',
        invalid_condition: 'Below 10', verify_date: '2026-09-10' },
      saved_at: '2026-09-09T10:00:00Z',
    }],
    verifications: [{
      verification_id: 'v1', note_id: 'n1', revision_id: 'r1', snapshot_id: 'publication-B',
      outcome: 'supported', evidence: 'Observed interval', supersedes: null, created_at: '2026-09-11T09:00:00Z',
      context: {
        requested_start: '2026-09-09', requested_end: '2026-09-10',
        adjusted_start: '2026-09-09', adjusted_end: '2026-09-10',
        trade_date: '2026-09-10', as_of: '2026-09-10T10:00:00Z',
        source: 'synthetic-market', source_version: 'market-v1', rule_version: 'rule-v1',
        calendar_sessions: ['2026-09-09', '2026-09-10'],
      },
      fact_interval: {
        security_id: 'TEST001', adjustment: 'raw', adjusted_start: '2026-09-09',
        adjusted_end: '2026-09-10', chart_type: 'candlestick',
        points: [
          { trade_date: '2026-09-09', open: 10, high: 12, low: 9, close: 11, volume: 100, amount: 1100 },
          { trade_date: '2026-09-10', open: 11, high: 13, low: 10, close: 12, volume: 200, amount: 2400 },
        ],
      },
    }],
    watchlist: [],
  };
}
const emptyState = { profile: 'target', notes: [], revisions: [], verifications: [], watchlist: [] };

describe('QUALITY P1 revision identity at backup validation', () => {
  test.each(['extended', 'legacy'] as const)('confirmed explicit null date is rejected for %s verification', mode => {
    const source = backup();
    source.revisions[0].trade_date = null;
    if (mode === 'legacy') {
      delete source.verifications[0].context;
      delete source.verifications[0].fact_interval;
    }
    expect(() => parseReviewBackup(source)).toThrow(/备份/);
  });

  test('explicit null security cannot borrow parent TEST001 identity for frozen facts', () => {
    const source = backup();
    source.revisions[0].security_id = null;
    expect(() => parseReviewBackup(source)).toThrow(/备份/);
  });

  test('omitted identity inherits parent fields and remains valid under restored fact validation', async () => {
    const parsed = parseReviewBackup(backup());
    expect(parsed.revisions[0]).not.toHaveProperty('trade_date');
    expect(parsed.revisions[0]).not.toHaveProperty('security_id');
    const plan = await planReviewImport(parsed, emptyState, 'merge');
    expect(plan.revisions[0]).toMatchObject({ trade_date: '2026-09-09', security_id: 'TEST001' });
    expect(() => validateVerificationFacts(plan.verifications[0], plan.revisions[0], false, true)).not.toThrow();
  });

  test.each(['draft', 'confirmed'] as const)('explicit null security is preserved for a context-free %s revision', async state => {
    const source = backup();
    source.revisions[0].state = state;
    source.revisions[0].security_id = null;
    if (state === 'draft') {
      source.verifications = []; source.counts.verifications = 0;
      source.revisions[0].trade_date = null;
    } else {
      delete source.verifications[0].context; delete source.verifications[0].fact_interval;
    }
    const plan = await planReviewImport(parseReviewBackup(source), emptyState, 'merge');
    expect(plan.revisions[0].security_id).toBeNull();
    expect(plan.revisions[0].trade_date).toBe(state === 'draft' ? null : '2026-09-09');
  });

  test('explicit revision identity overrides a different parent and validates the restored facts', async () => {
    const source = backup();
    source.notes[0].trade_date = '2026-09-08';
    source.notes[0].security_id = 'OTHER';
    Object.assign(source.revisions[0], { trade_date: '2026-09-09', security_id: 'TEST001' });
    const plan = await planReviewImport(parseReviewBackup(source), emptyState, 'merge');
    expect(plan.revisions[0]).toMatchObject({ trade_date: '2026-09-09', security_id: 'TEST001' });
    expect(() => validateVerificationFacts(plan.verifications[0], plan.revisions[0], false, true)).not.toThrow();
  });

  test('confirmed explicit date after verify date cannot borrow the earlier parent date', () => {
    const source = backup();
    source.revisions[0].trade_date = '2026-09-11';
    source.verifications = []; source.counts.verifications = 0;
    expect(() => parseReviewBackup(source)).toThrow(/备份/);
  });

  test('explicit different security rejects parent-security facts', () => {
    const source = backup();
    source.revisions[0].security_id = 'OTHER';
    expect(() => parseReviewBackup(source)).toThrow(/备份/);
  });
});
