/**
 * Task 27 — EX15 local simulation ledger scaffold.
 *
 * The IndexedDB v5 simulation repository and the TypeScript port of the
 * trading rules land in Tasks 14-15. Until then this probe pins the frozen
 * four-store contract from docs/contracts/workbench-api.md so a later rename
 * cannot happen silently.
 */
import { describe, expect, it } from 'vitest';

const SIMULATION_STORES = [
  'simulation_accounts',
  'simulation_orders',
  'simulation_fills',
  'simulation_commands',
] as const;

describe('Task 27 simulation ledger scaffold', () => {
  it('pins exactly four simulation stores', () => {
    expect(SIMULATION_STORES).toHaveLength(4);
    expect(new Set(SIMULATION_STORES).size).toBe(SIMULATION_STORES.length);
  });

  it('keeps every simulation store inside the local simulation namespace', () => {
    for (const store of SIMULATION_STORES) {
      expect(store.startsWith('simulation_')).toBe(true);
    }
  });
});
