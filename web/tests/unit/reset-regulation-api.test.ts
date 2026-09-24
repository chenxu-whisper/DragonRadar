import { describe, expect, it } from 'vitest';

import {
  validateRegulationEventsResponse,
  validateRegulationStatusResponse,
} from '../../src/features/regulation/api';

const selection = { date: '2026-09-10', snapshot: 'snapshot-a' };
const coverage = { expected: 1, valid: 1, excluded: 0, missing: 0 };
const envelope = (data: unknown, patch: Record<string, unknown> = {}) => ({
  trade_date: selection.date,
  as_of: '2026-09-10T10:00:00Z',
  snapshot_id: selection.snapshot,
  source: 'regulation_materialization',
  source_version: '1',
  coverage,
  quality_flags: [],
  rule_version: null,
  basis: { unit: 'ratio', currency: 'CNY', price_type: 'raw', benchmark: null },
  data,
  ...patch,
});
const event = {
  event_id: 'event-1',
  rule_id: 'rule-1',
  rule_version: 'v1',
  security_id: 'TEST001',
  event_type: 'monitoring',
  display_state: 'monitoring',
  announcement_id: 'notice-1',
  announcement_url: 'https://exchange.example.test/notices/1',
  effective_from: selection.date,
  effective_to: null,
};
const calculation = {
  rule_id: 'rule-1',
  rule_version: 'v1',
  security_id: 'TEST001',
  trade_date: selection.date,
  window_days: 10,
  direction: 'upper',
  threshold: 0.7,
  warning_threshold: 0.65,
  review_switch: 'pending',
  benchmark_index: null,
  recompute_condition: null,
  computed_value: 0.4,
  state: 'pending',
  reasons: ['rule_not_approved'],
  official_source_url: 'https://exchange.example.test/rules/1',
  effective_from: '2026-09-01',
  effective_to: null,
  production_enabled: false,
  production_gate_reasons: ['rule_not_approved'],
};

describe('regulation response validation', () => {
  it('accepts complete status data while preserving a pending rule', () => {
    const response = envelope({
      region1_completeness: {
        status: 'complete',
        coverage_ratio: 1,
        missing_count: 0,
        source_check: 'pass',
        ...coverage,
      },
      region2_calc_state: [calculation],
      region3_events: [event],
    });

    expect(validateRegulationStatusResponse(response, selection)).toBeNull();
  });

  it.each<[string, Record<string, unknown>]>([
    ['count', { ...coverage, expected: -1 }],
    ['completeness enum', { status: 'ready' }],
    ['calculation enum', {
      region2_calc_state: [{ ...calculation, state: 'safe' }],
    }],
    ['event enum', {
      region3_events: [{ ...event, event_type: 'rumor' }],
    }],
  ])('rejects malformed status %s', (_name, patch) => {
    const completeness = {
      status: 'complete',
      coverage_ratio: 1,
      missing_count: 0,
      source_check: 'pass',
      ...coverage,
      ...(patch.status ? patch : {}),
    };
    const data = {
      region1_completeness: completeness,
      region2_calc_state: [calculation],
      region3_events: [event],
      ...(patch.region2_calc_state || patch.region3_events ? patch : {}),
    };
    const response = envelope(data, patch.expected ? { coverage: patch } : {});

    expect(validateRegulationStatusResponse(response, selection)).toBe('malformed_response');
  });

  it('rejects status selection mismatches', () => {
    expect(validateRegulationStatusResponse(
      envelope(null, { snapshot_id: 'snapshot-b' }),
      selection,
    )).toBe('selection_mismatch');
  });

  it('validates event totals and unknown-source nullability', () => {
    expect(validateRegulationEventsResponse(
      envelope({ events: [event], listed_count: 1, total: 0, source_check: 'pass' }),
      selection,
    )).toBe('malformed_response');
    expect(validateRegulationEventsResponse(
      envelope({ events: [], listed_count: 0, total: null, source_check: 'unknown' }),
      selection,
    )).toBeNull();
    expect(validateRegulationEventsResponse(
      envelope({ events: [event], listed_count: 1, total: null, source_check: 'unknown' }),
      selection,
    )).toBeNull();
    expect(validateRegulationEventsResponse(
      envelope({ events: [event], listed_count: 1, total: 1, source_check: 'unknown' }),
      selection,
    )).toBe('malformed_response');
    expect(validateRegulationEventsResponse(
      envelope({ events: null, listed_count: null, total: null, source_check: 'unknown' }),
      selection,
    )).toBeNull();
  });
});
