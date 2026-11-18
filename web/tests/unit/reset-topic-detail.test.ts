import { describe, expect, it } from 'vitest';
import { validateTopicDetail } from '../../src/features/workbench/topic-validation';

const query = new URLSearchParams({ date: '2026-09-10', page: '1', size: '50' });
function response() {
  const ratio = { numerator: 1, denominator: 1, value: 1, reason: null };
  return {
    trade_date: '2026-09-10', quality_flags: [],
    data: {
      sector: { sector_id: 'T', name: '题材', member_count: 1, valid_returns: 1, median_return: 0.1, change_ratio: 0.1,
        limit_up_count: 1, net_inflow: null, net_inflow_reason: 'unavailable', quality_flags: [],
        up_ratio: { ...ratio }, limit_up_ratio: { ...ratio }, coverage: { expected: 1, valid: 1, excluded: 0, missing: 0 } },
      topic_basis: { source: 'synthetic', source_version: 'v1', as_of: '2026-09-10T08:00:00Z', source_time: null,
        cutoff: '2026-09-10T08:00:00Z', market_snapshot_id: 'market', topic_snapshot_id: 'topic', taxonomy_version: 'tax1', membership_version: 'members1' },
      definition: { effective_from: '2026-09-01', effective_to: null, available_at: '2026-09-10T07:00:00Z', source_reference: 'ref' },
      membership_coverage: { expected: 1 as number | null, received: 1 as number | null, known: 1, status: 'complete', reason: null as string | null, complete_as_of: '2026-09-10T08:00:00Z' as string | null },
      members: [{ security_id: 'A', code: null, name: null, effective_from: '2026-09-01', effective_to: null, available_at: '2026-09-10T07:00:00Z', evidence_refs: ['ref'] }],
      total: 1, page: 1, size: 50,
    },
  };
}

describe('published topic detail contracts', () => {
  it('accepts complete and explicitly unknown membership coverage without inventing counts', () => {
    const value = response();
    expect(validateTopicDetail(value, 'T', query)).toBeUndefined();
    value.data.membership_coverage = { expected: null, received: null, known: 1, status: 'unavailable', reason: 'topic_coverage_unavailable', complete_as_of: null };
    expect(validateTopicDetail(value, 'T', query)).toBeUndefined();
  });
  it.each(['aggregate', 'ratio', 'complete', 'known', 'page'] as const)('rejects inconsistent %s counts', change => {
    const value = response();
    if (change === 'aggregate') value.data.sector.coverage.expected = 2;
    if (change === 'ratio') value.data.sector.limit_up_ratio.numerator = 2;
    if (change === 'complete') value.data.membership_coverage.received = 0;
    if (change === 'known') value.data.membership_coverage.known = 2;
    if (change === 'page') value.data.members = [];
    expect(validateTopicDetail(value, 'T', query)).toBe('malformed_response');
  });
  it('rejects mismatched selection and duplicate identities', () => {
    const value = response();
    expect(validateTopicDetail(value, 'OTHER', query)).toBe('selection_mismatch');
    value.trade_date = '2026-09-09';
    expect(validateTopicDetail(value, 'T', query)).toBe('selection_mismatch');
    value.trade_date = '2026-09-10';
    value.data.members.push(value.data.members[0]);
    value.data.total = 2;
    value.data.membership_coverage.known = 2;
    expect(validateTopicDetail(value, 'T', query)).toBe('malformed_response');
  });
});
