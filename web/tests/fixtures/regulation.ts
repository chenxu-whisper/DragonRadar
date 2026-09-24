import type { Page, Route } from '@playwright/test';

import { ID, mockMarket, resetSummary } from './market';

// Synthetic UI-only evidence. It is not a production regulation rule set.
export const REGULATION_SNAPSHOT = ID;
export const REGULATION_DATE = '2026-09-10';

const coverage = { expected: 4, valid: 3, excluded: 0, missing: 1 };
const rule = {
  rule_version: 'synthetic-v1',
  window_days: 10,
  direction: 'upper',
  threshold: 0.7,
  warning_threshold: 0.65,
  benchmark_index: 'TEST-BENCHMARK',
  recompute_condition: 'window_reset',
  official_source_url: 'https://exchange.example.test/rules/synthetic-v1',
  effective_from: '2026-09-01',
  effective_to: null,
  production_enabled: false,
  production_gate_reasons: ['synthetic_fixture_only'],
};

const calculations = [
  {
    ...rule,
    rule_id: 'synthetic-pending',
    review_switch: 'pending',
    computed_value: 0.4,
    state: 'pending',
    reasons: ['rule_not_approved'],
  },
  {
    ...rule,
    rule_id: 'synthetic-idle',
    review_switch: 'approved',
    computed_value: 0.4,
    state: 'idle',
    reasons: [],
  },
  {
    ...rule,
    rule_id: 'synthetic-approaching',
    review_switch: 'approved',
    computed_value: 0.68,
    state: 'approaching_alert',
    reasons: [],
  },
  {
    ...rule,
    rule_id: 'synthetic-triggered',
    review_switch: 'approved',
    computed_value: 0.8,
    state: 'triggered',
    reasons: [],
  },
];

const event = (
  event_id: string,
  event_type: 'abnormal' | 'serious_abnormal' | 'monitoring' | 'restriction',
  display_state: 'disclosed' | 'monitoring' | 'restricted',
) => ({
  event_id,
  rule_id: 'synthetic-formal-event',
  rule_version: 'synthetic-v1',
  security_id: 'TEST001',
  event_type,
  display_state,
  announcement_id: `notice-${event_id}`,
  announcement_url: `https://exchange.example.test/notices/${event_id}`,
  effective_from: REGULATION_DATE,
  effective_to: event_type === 'restriction' ? '2026-09-20' : null,
});

const events = [
  event('event-abnormal', 'abnormal', 'disclosed'),
  event('event-serious', 'serious_abnormal', 'disclosed'),
  event('event-monitoring', 'monitoring', 'monitoring'),
  event('event-restriction', 'restriction', 'restricted'),
];

function regulationEnvelope(data: unknown) {
  return {
    ...resetSummary(),
    source: 'regulation_materialization',
    source_version: '1',
    coverage,
    rule_version: null,
    basis: {
      unit: 'ratio',
      currency: 'CNY',
      price_type: 'raw',
      benchmark: null,
    },
    data,
  };
}

export interface RegulationMockState {
  unknownEvents?: boolean;
}

export async function mockRegulation(
  page: Page,
  state: RegulationMockState = {},
) {
  await mockMarket(page);
  await page.route('**/api/regulation/**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/regulation/status') {
      return route.fulfill({
        json: regulationEnvelope({
          region1_completeness: {
            status: 'partial',
            coverage_ratio: 0.75,
            missing_count: 1,
            source_check: 'pass',
            ...coverage,
          },
          region2_calc_state: calculations,
          region3_events: events,
        }),
      });
    }
    if (url.pathname === '/api/regulation/events') {
      return route.fulfill({
        json: regulationEnvelope(state.unknownEvents
          ? { events: null, total: null, source_check: 'unknown' }
          : { events, total: events.length, source_check: 'pass' }),
      });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.route('**/api/stock/**', async route => {
    const url = new URL(route.request().url());
    const securityId = decodeURIComponent(url.pathname.split('/').at(-1)!);
    return route.fulfill({
      json: {
        ...resetSummary(),
        data: {
          security_id: securityId,
          name: '合成测试证券1',
          quote: { last: 11.2, change_ratio: 0.1, amount: 100 },
          roles: [],
          seal: {
            is_limit_up: null,
            touched_limit: null,
            first_seal_time: null,
            last_seal_time: null,
            break_count: null,
            seal_amount: null,
            seal_amount_unit: null,
            continuous_boards: null,
            reason: 'intraday_seal_fields_unavailable',
          },
          regulation: {
            formal_event_count: state.unknownEvents ? null : events.length,
            event_source_completeness: state.unknownEvents
              ? 'unknown'
              : 'complete',
            reason: state.unknownEvents ? 'regulation_missing' : null,
          },
        },
      },
    });
  });
}
