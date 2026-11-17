import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  candidatePath, evidencePath, ladderPath, continuityPath,
  validateCandidates, validateEvidence, validateLadder,
} from '../../src/features/roles/api';
import type { RoleEnvelope } from '../../src/features/roles/types';

const snapshot = '00000000-0000-4000-8000-000000000001';
const base = {
  trade_date: '2026-09-10',
  as_of: '2026-09-10T10:00:00Z',
  snapshot_id: snapshot,
  source: 'role_materialization',
  source_version: 'role-source-v1',
  coverage: { expected: 2, valid: 2, excluded: 0, missing: 0 },
  quality_flags: [],
  rule_version: 'role-v1',
  basis: { unit: 'ratio', currency: 'CNY', price_type: 'adjusted', benchmark: 'CSI300' },
};

afterEach(() => vi.unstubAllGlobals());

describe('reset role API client', () => {
  it('builds 05-08 requests with one fixed date and snapshot identity', () => {
    const selection = { date: '2026-09-10', snapshot, sector: 'SEC001', role: 'trend_leader' as const };
    expect(ladderPath(selection)).toBe(`/api/limit-up/ladder?date=2026-09-10&snapshot_id=${snapshot}&sector=SEC001`);
    expect(continuityPath(selection)).toBe(`/api/limit-up/continuity?date=2026-09-10&snapshot_id=${snapshot}`);
    expect(candidatePath(selection)).toBe(`/api/role/candidates?date=2026-09-10&snapshot_id=${snapshot}&role=trend_leader&sector=SEC001`);
    expect(evidencePath(selection, 'SSE.600001')).toBe(`/api/role/evidence?date=2026-09-10&snapshot_id=${snapshot}&security_id=SSE.600001&sector=SEC001`);
  });

  it('rejects envelopes or nested identities that differ from the request', () => {
    const ladder = {
      ...base,
      data: {
        columns: [2],
        total: 1,
        cells: [{ boards: 2, count: 1, stocks: [{ security_id: 'A', name: '甲', roles: ['trend_leader'], sector_id: 'SEC002' }] }],
        promote_rate: { yesterday_ge2: 1, today_sealed: 1, tradable: 1, rate: 1 },
      },
    } satisfies RoleEnvelope<unknown>;
    expect(() => validateLadder(ladder, { snapshot, date: '2026-09-10', sector: 'SEC001' })).toThrow('题材');
    expect(() => validateLadder({ ...ladder, snapshot_id: 'other' }, { snapshot, date: '2026-09-10' })).toThrow('快照');

    const candidates = {
      ...base,
      data: {
        role: 'trend_leader',
        statuses: [{ sector_id: 'SEC001', status: 'ready', capability: 'ready', reasons: [] }],
        candidates: [{ security_id: 'A', name: '甲', rank: 1, evidence_status: 'candidate', sector_id: 'SEC001', boards: 2 }],
        change_summaries: [],
        role_changes: [],
        total: 1,
      },
    };
    expect(() => validateCandidates(candidates, {
      snapshot, date: '2026-09-10', role: 'zhongjun', sector: 'SEC001',
    })).toThrow('角色');

    const evidence = {
      ...base,
      data: { security_id: 'B', sector_id: 'SEC001', roles: [] },
    };
    expect(() => validateEvidence(evidence, {
      snapshot, date: '2026-09-10', security: 'A', sector: 'SEC001',
    })).toThrow('证券');
  });

  it('rejects malformed evidence factor provenance instead of displaying it', () => {
    const evidence = {
      ...base,
      data: {
        security_id: 'A',
        sector_id: 'SEC001',
        roles: [{
          role_type: 'trend_leader',
          evidence_status: 'candidate',
          factors: [{
            factor_id: 'FAC-R01', metric: 'relative_strength', value: 0.12,
            status: 'computed', unit: 'ratio', window: '20d',
            coverage: { expected: 20, valid: 20, excluded: 0, missing: 0 },
            reasons: [], source_time: null,
          }],
          supporting_evidence: ['relative_strength_positive'],
          opposing_evidence: [],
          missing_evidence: [],
        }],
      },
    };
    expect(() => validateEvidence(evidence, {
      snapshot, date: '2026-09-10', security: 'A', sector: 'SEC001',
    })).toThrow();
  });

  it('rejects malformed role change details instead of displaying unverified comparisons', () => {
    const candidates = {
      ...base,
      data: {
        role: 'trend_leader',
        statuses: [{ sector_id: 'SEC001', status: 'ready', capability: 'ready', reasons: [] }],
        candidates: [],
        change_summaries: [],
        role_changes: [{
          id: 'change-1',
          sector_id: 'SEC001',
          security_id: 600001,
          role: 'trend_leader',
          comparison_status: 'comparable',
          change_type: 'added',
          membership_changed: true,
          reasons: ['entered_rank'],
        }],
        total: 0,
      },
    };

    expect(() => validateCandidates(candidates, {
      snapshot, date: '2026-09-10', role: 'trend_leader',
    })).toThrow('变化');
  });
});
