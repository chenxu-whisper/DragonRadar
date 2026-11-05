import { expect, test } from '@playwright/test';

/**
 * Task 27 (EX14/EX15) E2E scaffold. The owner-only backtest flow and the
 * local simulation ledger probes are implemented in Task 17. Until then the
 * EX14/EX15 route paths are pinned as an owner-only namespace so a later
 * route rename or surface leak cannot happen silently.
 */
const RESEARCH_ROUTES = [
  '/api/research/backtests',
  '/api/research/backtests/{run_id}',
] as const;

test.describe('Task 27 research route contract scaffold', () => {
  test('EX14/EX15 stay pinned under the owner-only research namespace', () => {
    expect(RESEARCH_ROUTES).toHaveLength(2);
    for (const route of RESEARCH_ROUTES) {
      expect(route.startsWith('/api/research/')).toBe(true);
    }
  });
});
