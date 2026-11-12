/**
 * Task 27 — Task 14 local simulation ledger (IDB v5) and the TypeScript port
 * of the Task 3/4 pure trading rules.
 *
 * Three frozen surfaces are pinned here before any production code exists:
 *
 *   TR-14.3  `features/research/execution.ts` + `features/research/ledger.ts`
 *            are a strict decimal.js port of the backend pure functions. They
 *            carry the SAME golden fixture as
 *            tests/test_reset_research_validation.py and must reproduce every
 *            fill, rejection, cash and holding value exactly.
 *   TR-14.1  `repositories/local/simulation.ts` opens IndexedDB v5 with four
 *            profile-partitioned simulation stores and mirrors the v4
 *            command idempotency / optimistic revision / readback contract.
 *   TR-14.2  v4 -> v5 is an additive upgrade: existing notes/watchlist data
 *            survives byte-for-byte and reopening an already-v5 database is
 *            idempotent.
 *
 * The simulation UI (SimulationPanel) and every e2e flip land in later tasks;
 * this file stays a vitest-only fake-indexeddb contract.
 */
import 'fake-indexeddb/auto';
import Decimal from 'decimal.js';
import { describe, expect, it } from 'vitest';
import { LocalRepository } from '../../src/repositories/local/repository';
import {
  SIMULATION_STORES,
  SimulationRepository,
  createSimulationStores,
  type OpenSimulationAccount,
  type PlaceSimulationOrder,
  type SimulationOrderResult,
} from '../../src/repositories/local/simulation';
import {
  D,
  REJECTION_REASONS,
  closingLimitHit,
  simulateFill,
  type CostModel,
  type Fill,
  type Order,
  type Position,
  type PriceBar,
  type Rejection,
  type RejectionReason,
  type SecurityStateInput,
} from '../../src/features/research/execution';
import {
  applyFill,
  markEquity,
  openLedger,
  rollOvernight,
  type LedgerState,
} from '../../src/features/research/ledger';
import { exportLocalBackup } from '../../src/repositories/local/backup';

// ---------------------------------------------------------------------------
// Shared golden fixture — identical inputs to
// tests/test_reset_research_validation.py (SIGNAL_DAY/FILL_DAY/DAY2, flat
// 10.00 close, commission 0.0003, stamp 0.001 sell-only, slippage 0.0005,
// min commission 5, initial cash 100000.00).
// ---------------------------------------------------------------------------

const SECURITY = '000001';
const SIGNAL_DAY = '2025-01-06';
const FILL_DAY = '2025-01-07';
const DAY2 = '2025-01-08';

function rules(overrides: Partial<CostModel> = {}): CostModel {
  return {
    commission_rate: '0.0003',
    stamp_tax_rate: '0.001',
    slippage_model: 'fixed_rate',
    slippage_rate: '0.0005',
    stamp_tax_side: 'sell',
    min_commission: '5',
    ...overrides,
  };
}

function bar(overrides: Partial<PriceBar> = {}): PriceBar {
  return {
    security_id: SECURITY,
    trade_date: FILL_DAY,
    close: '10.00',
    adjustment_factor: '1',
    limit_status: 'no_limit',
    limit_date: null,
    limit_up: null,
    limit_down: null,
    ...overrides,
  };
}

function order(overrides: Partial<Order> = {}): Order {
  return {
    security_id: SECURITY,
    side: 'buy',
    quantity: 100,
    signal_date: SIGNAL_DAY,
    ...overrides,
  };
}

function securityState(overrides: SecurityStateInput = {}): SecurityStateInput {
  return { suspended: false, is_delisting: false, ...overrides };
}

function position(total = '0', available?: string): Position {
  return {
    security_id: SECURITY,
    quantity: total,
    available_quantity: available ?? total,
  };
}

function limitedBar(close: string, up = '11.00', down = '9.00'): PriceBar {
  return bar({
    close,
    limit_status: 'limited',
    limit_date: FILL_DAY,
    limit_up: up,
    limit_down: down,
  });
}

// ---------------------------------------------------------------------------
// Frozen four-store contract (scaffold probes, now bound to the real module).
// ---------------------------------------------------------------------------

describe('Task 14 simulation store contract', () => {
  it('pins exactly four simulation stores under the simulation namespace', () => {
    expect(SIMULATION_STORES).toEqual([
      'simulation_accounts',
      'simulation_orders',
      'simulation_fills',
      'simulation_commands',
    ]);
    expect(new Set(SIMULATION_STORES).size).toBe(SIMULATION_STORES.length);
    for (const store of SIMULATION_STORES) {
      expect(store.startsWith('simulation_')).toBe(true);
    }
  });

  it('creates the simulation stores idempotently inside one upgrade transaction', async () => {
    const name = `test-${crypto.randomUUID()}`;
    await new Promise<void>((resolve, reject) => {
      const opening = indexedDB.open(name, 1);
      opening.onupgradeneeded = () => {
        createSimulationStores(opening.result);
        createSimulationStores(opening.result);
      };
      opening.onsuccess = () => {
        const db = opening.result;
        for (const store of SIMULATION_STORES) {
          expect(db.objectStoreNames.contains(store)).toBe(true);
        }
        db.close();
        resolve();
      };
      opening.onerror = () => reject(opening.error);
    });
  });
});

// ---------------------------------------------------------------------------
// TR-14.3 — strict TypeScript port of research/execution.py
// ---------------------------------------------------------------------------

describe('TR-14.3 simulateFill execution port', () => {
  it('keeps the nine rejection reason literals identical to the backend closed set', () => {
    expect(REJECTION_REASONS).toEqual([
      'suspended',
      'limit_up_blocked_buy',
      'limit_down_blocked_sell',
      't1_sell_blocked',
      'insufficient_cash',
      'insufficient_position',
      'delisted',
      'no_tradable_price',
      'unknown_limit_untradable',
    ]);
    expect(new Set(REJECTION_REASONS).size).toBe(9);
  });

  it('uses an isolated HALF_EVEN 34-digit Decimal context without touching global decimal.js', () => {
    expect(D.precision).toBe(34);
    expect(D.rounding).toBe(Decimal.ROUND_HALF_EVEN);
    expect(Decimal.rounding).toBe(Decimal.ROUND_HALF_UP);
  });

  it.each<[string, RejectionReason]>([
    ['suspended', 'suspended'],
    ['limit_up_buy', 'limit_up_blocked_buy'],
    ['limit_down_sell', 'limit_down_blocked_sell'],
    ['t1', 't1_sell_blocked'],
    ['cash', 'insufficient_cash'],
    ['position', 'insufficient_position'],
    ['delisted', 'delisted'],
    ['no_price', 'no_tradable_price'],
    ['unknown_limit', 'unknown_limit_untradable'],
  ])('rejection %s maps deterministically to %s', (_case, expectedReason) => {
    let result: Fill | Rejection;
    if (_case === 'suspended') {
      result = simulateFill(order(), bar(), securityState({ suspended: true }), rules(), '100000', null);
    } else if (_case === 'limit_up_buy') {
      result = simulateFill(order(), limitedBar('11.00'), securityState(), rules(), '100000', null);
    } else if (_case === 'limit_down_sell') {
      result = simulateFill(
        order({ side: 'sell' }), limitedBar('9.00'), securityState(), rules(), '0', position('100'),
      );
    } else if (_case === 't1') {
      result = simulateFill(
        order({ side: 'sell' }), bar(), securityState(), rules(), '0', position('100', '0'),
      );
    } else if (_case === 'cash') {
      result = simulateFill(order(), bar(), securityState(), rules(), '1', null);
    } else if (_case === 'position') {
      result = simulateFill(
        order({ side: 'sell' }), bar(), securityState(), rules(), '0', position('0'),
      );
    } else if (_case === 'delisted') {
      result = simulateFill(order(), bar(), securityState({ is_delisting: true }), rules(), '100000', null);
    } else if (_case === 'no_price') {
      result = simulateFill(order(), bar({ close: null }), securityState(), rules(), '100000', null);
    } else {
      result = simulateFill(order(), bar({ limit_status: 'unknown' }), securityState(), rules(), '100000', null);
    }
    expect(result.kind).toBe('rejection');
    const rejection = result as Rejection;
    expect(rejection.reason).toBe(expectedReason);
    expect(rejection.security_id).toBe(SECURITY);
    expect(rejection.trade_date).toBe(FILL_DAY);
  });

  it('never emits a fill for a rejection', () => {
    const result = simulateFill(order(), bar({ close: null }), securityState(), rules(), '100000', null);
    expect(result.kind).toBe('rejection');
    expect((result as Rejection).reason).toBe('no_tradable_price');
    expect('fill_price' in result).toBe(false);
  });

  it('treats a fill at or before the signal session as a contract violation', () => {
    expect(() =>
      simulateFill(order({ signal_date: FILL_DAY }), bar(), securityState(), rules(), '100000', null),
    ).toThrow(/next tradable session/);
  });

  it('rejects a bar whose security does not match the order', () => {
    expect(() =>
      simulateFill(order(), bar({ security_id: '000002' }), securityState(), rules(), '100000', null),
    ).toThrow(/security/);
  });

  it('blocks a limit-up buy but still allows a limit-up sell', () => {
    const sealedUp = limitedBar('11.00');
    const blocked = simulateFill(order(), sealedUp, securityState(), rules(), '100000', null);
    expect(blocked.kind).toBe('rejection');
    expect((blocked as Rejection).reason).toBe('limit_up_blocked_buy');
    const filled = simulateFill(
      order({ side: 'sell' }), sealedUp, securityState(), rules(), '0', position('100'),
    );
    expect(filled.kind).toBe('fill');
  });

  it('blocks a limit-down sell but still allows a limit-down buy', () => {
    const sealedDown = limitedBar('9.00');
    const blocked = simulateFill(
      order({ side: 'sell' }), sealedDown, securityState(), rules(), '0', position('100'),
    );
    expect(blocked.kind).toBe('rejection');
    expect((blocked as Rejection).reason).toBe('limit_down_blocked_sell');
    const filled = simulateFill(order(), sealedDown, securityState(), rules(), '100000', null);
    expect(filled.kind).toBe('fill');
  });

  it('rejects both sides conservatively when limit metadata is unknown', () => {
    const unknown = bar({ limit_status: 'unknown' });
    const buy = simulateFill(order(), unknown, securityState(), rules(), '100000', null);
    const sell = simulateFill(
      order({ side: 'sell' }), unknown, securityState(), rules(), '0', position('100'),
    );
    expect(buy.kind).toBe('rejection');
    expect(sell.kind).toBe('rejection');
    expect((buy as Rejection).reason).toBe('unknown_limit_untradable');
    expect((sell as Rejection).reason).toBe('unknown_limit_untradable');
  });

  it('does not assert limit metadata carried by another trade date', () => {
    const stale = limitedBar('11.00');
    stale.limit_date = SIGNAL_DAY;
    const result = simulateFill(order(), stale, securityState(), rules(), '100000', null);
    expect(result.kind).toBe('rejection');
    expect((result as Rejection).reason).toBe('unknown_limit_untradable');
  });

  it('returns none/up/down/unknown from closingLimitHit exactly like the backend helper', () => {
    expect(closingLimitHit(bar())).toBe('none');
    expect(closingLimitHit(limitedBar('11.00'))).toBe('up');
    expect(closingLimitHit(limitedBar('9.00'))).toBe('down');
    expect(closingLimitHit(bar({ close: null }))).toBe('unknown');
    expect(closingLimitHit(bar({ limit_status: 'unknown' }))).toBe('unknown');
    const noLimitWithMetadata = bar({ limit_up: '11.00' });
    expect(closingLimitHit(noLimitWithMetadata)).toBe('unknown');
    const insideBand = limitedBar('10.00');
    expect(closingLimitHit(insideBand)).toBe('none');
  });

  it('blocks a T-day sell until the next session settles the lot', () => {
    const blocked = simulateFill(
      order({ side: 'sell' }), bar(), securityState(), rules(), '0', position('100', '0'),
    );
    expect(blocked.kind).toBe('rejection');
    expect((blocked as Rejection).reason).toBe('t1_sell_blocked');
    const filled = simulateFill(
      order({ side: 'sell', signal_date: FILL_DAY }),
      bar({ trade_date: DAY2 }),
      securityState(),
      rules(),
      '0',
      position('100', '100'),
    );
    expect(filled.kind).toBe('fill');
  });

  it('classifies selling more than the total holding as insufficient_position, not T+1', () => {
    const result = simulateFill(
      order({ side: 'sell', quantity: 200 }), bar(), securityState(), rules(), '0',
      position('100', '100'),
    );
    expect(result.kind).toBe('rejection');
    expect((result as Rejection).reason).toBe('insufficient_position');
  });

  it('classifies selling a frozen lot within the total as T+1 blocked', () => {
    const result = simulateFill(
      order({ side: 'sell', quantity: 150 }), bar(), securityState(), rules(), '0',
      position('200', '100'),
    );
    expect(result.kind).toBe('rejection');
    expect((result as Rejection).reason).toBe('t1_sell_blocked');
  });

  it('accounts for slippage and commission in the insufficient-cash guard', () => {
    const short = simulateFill(order(), bar(), securityState(), rules(), '1005', null);
    expect(short.kind).toBe('rejection');
    expect((short as Rejection).reason).toBe('insufficient_cash');
    const exact = simulateFill(order(), bar(), securityState(), rules(), '1005.50', null);
    expect(exact.kind).toBe('fill');
  });

  it('reproduces the backend buy golden sample with quantized decimal strings', () => {
    const result = simulateFill(order(), bar(), securityState(), rules(), '100000', null);
    expect(result.kind).toBe('fill');
    const fill = result as Fill;
    expect(fill.raw_close).toBe('10.00');
    expect(fill.fill_price).toBe('10.0050');
    expect(fill.gross_amount).toBe('1000.50');
    expect(fill.commission).toBe('5.00');
    expect(fill.stamp_tax).toBe('0.00');
    expect(fill.net_cash_delta).toBe('-1005.50');
    expect(fill.trade_date).toBe(FILL_DAY);
    for (const value of [
      fill.raw_close,
      fill.fill_price,
      fill.gross_amount,
      fill.commission,
      fill.stamp_tax,
      fill.net_cash_delta,
    ]) {
      expect(typeof value).toBe('string');
    }
  });

  it('reproduces the backend sell golden sample including sell-only stamp tax', () => {
    const result = simulateFill(
      order({ side: 'sell' }), bar(), securityState(), rules(), '0', position('100', '100'),
    );
    expect(result.kind).toBe('fill');
    const fill = result as Fill;
    expect(fill.fill_price).toBe('9.9950');
    expect(fill.gross_amount).toBe('999.50');
    expect(fill.commission).toBe('5.00');
    expect(fill.stamp_tax).toBe('1.00');
    expect(fill.net_cash_delta).toBe('993.50');
  });

  it('quantizes rate-based commission above the minimum with ROUND_HALF_EVEN', () => {
    const result = simulateFill(
      order({ quantity: 10000 }), bar(), securityState(), rules(), '1000000', null,
    );
    expect(result.kind).toBe('fill');
    const fill = result as Fill;
    expect(fill.fill_price).toBe('10.0050');
    expect(fill.gross_amount).toBe('100050.00');
    expect(fill.commission).toBe('30.02');
  });

  it('never lets the adjustment factor enter the raw fill price', () => {
    const result = simulateFill(
      order(), bar({ adjustment_factor: '1.5' }), securityState(), rules(), '100000', null,
    );
    expect(result.kind).toBe('fill');
    expect((result as Fill).fill_price).toBe('10.0050');
  });

  it('keeps limit precedence ahead of cash and position guards', () => {
    const sealedUp = limitedBar('11.00');
    const buy = simulateFill(order(), sealedUp, securityState(), rules(), '0', null);
    expect(buy.kind).toBe('rejection');
    expect((buy as Rejection).reason).toBe('limit_up_blocked_buy');
    const sealedDown = limitedBar('9.00');
    const sell = simulateFill(
      order({ side: 'sell' }), sealedDown, securityState(), rules(), '0', position('0'),
    );
    expect(sell.kind).toBe('rejection');
    expect((sell as Rejection).reason).toBe('limit_down_blocked_sell');
  });
});

// ---------------------------------------------------------------------------
// TR-14.3 — strict TypeScript port of research/ledger.py
// ---------------------------------------------------------------------------

describe('TR-14.3 settlement ledger port', () => {
  it('opens a ledger and refuses negative initial cash', () => {
    const ledger = openLedger('100000.00', SIGNAL_DAY);
    expect(ledger.cash).toBe('100000.00');
    expect(ledger.as_of).toBe(SIGNAL_DAY);
    expect(ledger.holdings).toEqual({});
    expect(() => openLedger('-0.01', SIGNAL_DAY)).toThrow();
  });

  it('freezes T-day buys until rollOvernight and keeps cash stable across the roll', () => {
    let ledger: LedgerState = openLedger('100000.00', SIGNAL_DAY);
    const buy = simulateFill(
      order(), bar({ trade_date: FILL_DAY }), securityState(), rules(), ledger.cash, null,
    );
    expect(buy.kind).toBe('fill');
    ledger = applyFill(ledger, buy as Fill);
    expect(ledger.holdings[SECURITY]).toEqual({ quantity: '100', available_quantity: '0' });
    const settled = rollOvernight(ledger, DAY2);
    expect(settled.holdings[SECURITY]).toEqual({ quantity: '100', available_quantity: '100' });
    expect(settled.cash).toBe(ledger.cash);
  });

  it('reproduces the backend round-trip identity: 99994.50 marked, 99988.00 final cash', () => {
    let ledger = openLedger('100000.00', SIGNAL_DAY);
    const buy = simulateFill(
      order(), bar({ trade_date: FILL_DAY }), securityState(), rules(), ledger.cash, null,
    );
    expect(buy.kind).toBe('fill');
    ledger = applyFill(ledger, buy as Fill);
    expect(markEquity(ledger, { [SECURITY]: '10.00' })).toBe('99994.50');
    ledger = rollOvernight(ledger, DAY2);
    const holding = ledger.holdings[SECURITY];
    const sell = simulateFill(
      order({ side: 'sell', signal_date: FILL_DAY }),
      bar({ trade_date: DAY2 }),
      securityState(),
      rules(),
      ledger.cash,
      { security_id: SECURITY, quantity: holding.quantity, available_quantity: holding.available_quantity },
    );
    expect(sell.kind).toBe('fill');
    ledger = applyFill(ledger, sell as Fill);
    expect(SECURITY in ledger.holdings).toBe(false);
    expect(ledger.cash).toBe('99988.00');
  });

  it('refuses sells that exceed settled holdings or T+1 available lots', () => {
    const ledger = openLedger('100000.00', SIGNAL_DAY);
    const buy = simulateFill(
      order(), bar({ trade_date: FILL_DAY }), securityState(), rules(), ledger.cash, null,
    );
    const frozen = applyFill(ledger, buy as Fill);
    const overTotal: Order = { ...order(), side: 'sell', quantity: 200, signal_date: FILL_DAY };
    const overTotalFill = simulateFill(
      overTotal, bar({ trade_date: DAY2 }), securityState(), rules(), frozen.cash,
      position('100', '100'),
    );
    expect(overTotalFill.kind).toBe('rejection');
    const sameDaySell = simulateFill(
      { ...order(), side: 'sell' },
      bar({ trade_date: FILL_DAY }),
      securityState(),
      rules(),
      frozen.cash,
      position('100', '0'),
    );
    expect(sameDaySell.kind).toBe('rejection');
    expect((sameDaySell as Rejection).reason).toBe('t1_sell_blocked');
    expect(() => applyFill(frozen, sameDaySell as Fill)).toThrow();
  });

  it('marks equity fail-closed on missing or invalid prices', () => {
    const ledger = applyFill(
      openLedger('100000.00', SIGNAL_DAY),
      simulateFill(order(), bar({ trade_date: FILL_DAY }), securityState(), rules(), '100000', null) as Fill,
    );
    expect(() => markEquity(ledger, {})).toThrow();
    expect(() => markEquity(ledger, { [SECURITY]: '0' })).toThrow();
    expect(() => markEquity(ledger, { [SECURITY]: '-1.00' })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// TR-14.1 — SimulationRepository on fake-indexeddb
// ---------------------------------------------------------------------------

function simulationPair(name = `test-${crypto.randomUUID()}`) {
  const local = new LocalRepository(name);
  const sim = new SimulationRepository(local);
  return { name, local, sim };
}

function rawDatabase(name: string, version?: number): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = version === undefined ? indexedDB.open(name) : indexedDB.open(name, version);
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error);
  });
}

async function allRows(db: IDBDatabase, store: string): Promise<unknown[]> {
  return new Promise((resolve, reject) => {
    const query = db.transaction(store).objectStore(store).getAll();
    query.onsuccess = () => resolve(query.result as unknown[]);
    query.onerror = () => reject(query.error);
  });
}

const openDefault = (asOf = SIGNAL_DAY): OpenSimulationAccount => ({
  key: 'open-1',
  initial_cash: '100000.00',
  as_of: asOf,
});

const buyAtFill = (key = 'order-1', expectedRevision = 0): PlaceSimulationOrder => ({
  key,
  expected_revision: expectedRevision,
  order: order(),
  bar: bar(),
  state: securityState(),
  rules: rules(),
});

describe('TR-14.1 simulation repository schema and partition keys', () => {
  it('upgrades to v5 with profile-scoped composite keys on every simulation store', async () => {
    const { name, local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    const db = await rawDatabase(name);
    expect(db.version).toBe(5);
    for (const store of ['profiles', 'notes', 'note_revisions', 'watchlist', 'verification_records', 'commands', 'migration_payload']) {
      expect(db.objectStoreNames.contains(store)).toBe(true);
    }
    expect(db.transaction('simulation_accounts').objectStore('simulation_accounts').keyPath)
      .toEqual(['local_profile_id', 'account_id']);
    expect(db.transaction('simulation_orders').objectStore('simulation_orders').keyPath)
      .toEqual(['local_profile_id', 'order_id']);
    expect(db.transaction('simulation_fills').objectStore('simulation_fills').keyPath)
      .toEqual(['local_profile_id', 'fill_id']);
    expect(db.transaction('simulation_commands').objectStore('simulation_commands').keyPath)
      .toEqual(['local_profile_id', 'key']);
    db.close();
    local.close();
  });

  it('partitions every persisted row by the active local profile', async () => {
    const { name, local, sim } = simulationPair();
    const profile = await local.profile();
    await sim.openAccount(openDefault());
    const result = await sim.submitOrder(buyAtFill());
    expect(result.status).toBe('filled');
    const db = await rawDatabase(name);
    for (const store of SIMULATION_STORES) {
      const rows = await allRows(db, store);
      expect(rows.length).toBeGreaterThan(0);
      for (const row of rows) {
        expect((row as { local_profile_id: string }).local_profile_id).toBe(profile);
      }
    }
    db.close();
    local.close();
  });
});

describe('TR-14.1 simulation command idempotency, revision and readback', () => {
  it('opens an account and settles a filled order with the golden economics', async () => {
    const { local, sim } = simulationPair();
    const account = await sim.openAccount(openDefault());
    expect(account.revision).toBe(0);
    expect(account.cash).toBe('100000.00');
    const result = await sim.submitOrder(buyAtFill());
    expect(result.committed).toBe(true);
    expect(result.readback_verified).toBe(true);
    expect(result.status).toBe('filled');
    expect(result.rejection).toBeNull();
    expect(result.fill).toMatchObject({
      fill_price: '10.0050',
      gross_amount: '1000.50',
      commission: '5.00',
      stamp_tax: '0.00',
      net_cash_delta: '-1005.50',
    });
    expect(result.account.revision).toBe(1);
    expect(result.account.cash).toBe('98994.50');
    expect(result.account.holdings[SECURITY]).toEqual({ quantity: '100', available_quantity: '0' });
    expect(result.order).toMatchObject({ status: 'filled', revision: 1, reason: null });
    expect(result.fill?.order_id).toBe(result.order.order_id);
    expect(await sim.listOrders()).toHaveLength(1);
    expect(await sim.listFills()).toHaveLength(1);
    local.close();
  });

  it('replays the same command without creating another revision, order or fill', async () => {
    const { local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    const command = buyAtFill();
    const first = await sim.submitOrder(command);
    const replayed = await sim.submitOrder(command);
    expect(replayed).toEqual(first);
    expect(await sim.listOrders()).toHaveLength(1);
    expect(await sim.listFills()).toHaveLength(1);
    expect((await sim.getAccount())?.revision).toBe(1);
    local.close();
  });

  it('rejects the same idempotency key carrying a different payload', async () => {
    const { local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    await sim.submitOrder(buyAtFill());
    const tampered = buyAtFill();
    tampered.bar = bar({ close: '9.99' });
    await expect(sim.submitOrder(tampered)).rejects.toThrow(/幂等/);
    expect(await sim.listOrders()).toHaveLength(1);
    expect(await sim.listFills()).toHaveLength(1);
    expect((await sim.getAccount())?.revision).toBe(1);
    local.close();
  });

  it('refuses a stale expected revision without writing anything', async () => {
    const { local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    const stale = buyAtFill('stale', 5);
    await expect(sim.submitOrder(stale)).rejects.toThrow(/版本冲突/);
    expect(await sim.listOrders()).toEqual([]);
    expect(await sim.listFills()).toEqual([]);
    expect((await sim.getAccount())?.revision).toBe(0);
    local.close();
  });

  it('persists a rejected order with its reason but never touches cash, revision or fills', async () => {
    const { local, sim } = simulationPair();
    await sim.openAccount({ key: 'open-1', initial_cash: '500.00', as_of: SIGNAL_DAY });
    const result = await sim.submitOrder(buyAtFill());
    expect(result.status).toBe('rejected');
    expect(result.fill).toBeNull();
    expect(result.rejection).toEqual({ reason: 'insufficient_cash' });
    expect(result.order).toMatchObject({ status: 'rejected', reason: 'insufficient_cash' });
    expect(result.account.revision).toBe(0);
    expect(result.account.cash).toBe('500.00');
    expect(await sim.listFills()).toEqual([]);
    const orders = await sim.listOrders();
    expect(orders).toHaveLength(1);
    expect(orders[0]).toMatchObject({ status: 'rejected', reason: 'insufficient_cash' });
    local.close();
  });

  it('rolls the whole command back on quota failure and allows the same key to retry', async () => {
    const { name, local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on('unhandledRejection', onUnhandled);
    const original = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value: unknown, key?: IDBValidKey) {
      if (this.name === 'simulation_fills') {
        this.transaction.abort();
        throw new DOMException('Disk full', 'QuotaExceededError');
      }
      return key === undefined ? original.call(this, value) : original.call(this, value, key);
    };
    try {
      await expect(sim.submitOrder(buyAtFill())).rejects.toThrow();
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
    } finally {
      IDBObjectStore.prototype.put = original;
      process.off('unhandledRejection', onUnhandled);
    }
    const db = await rawDatabase(name);
    expect(await allRows(db, 'simulation_orders')).toEqual([]);
    expect(await allRows(db, 'simulation_fills')).toEqual([]);
    // The failed order command must roll back; the earlier open-account command stays.
    const commandRows = await allRows(db, 'simulation_commands');
    expect(commandRows).toHaveLength(1);
    expect((commandRows[0] as { key: string }).key).toBe('open-1');
    db.close();
    const retried = await sim.submitOrder(buyAtFill());
    expect(retried.status).toBe('filled');
    expect(await sim.listFills()).toHaveLength(1);
    local.close();
  });

  it('fails the readback guard when the post-commit verification transaction aborts', async () => {
    const { local, sim } = simulationPair();
    await sim.openAccount(openDefault());
    const original = IDBObjectStore.prototype.get;
    IDBObjectStore.prototype.get = function (query: IDBValidKey | IDBKeyRange) {
      if (this.transaction.mode === 'readonly' && this.name === 'simulation_accounts') {
        this.transaction.abort();
        throw new DOMException('readback lost', 'AbortError');
      }
      return original.call(this, query);
    };
    try {
      await expect(sim.submitOrder(buyAtFill())).rejects.toThrow(/回读/);
    } finally {
      IDBObjectStore.prototype.get = original;
    }
    local.close();
  });

  it('refuses orders before an account exists and leaves every store empty', async () => {
    const { name, local, sim } = simulationPair();
    await expect(sim.submitOrder(buyAtFill())).rejects.toThrow();
    const db = await rawDatabase(name);
    for (const store of SIMULATION_STORES) {
      expect(await allRows(db, store)).toEqual([]);
    }
    db.close();
    local.close();
  });

  it('makes account opening idempotent but rejects a second opening with new parameters', async () => {
    const { local, sim } = simulationPair();
    const first = await sim.openAccount(openDefault());
    expect(await sim.openAccount(openDefault())).toEqual(first);
    await expect(
      sim.openAccount({ key: 'open-1', initial_cash: '999.00', as_of: SIGNAL_DAY }),
    ).rejects.toThrow(/幂等/);
    await expect(
      sim.openAccount({ key: 'open-2', initial_cash: '200000.00', as_of: SIGNAL_DAY }),
    ).rejects.toThrow();
    local.close();
  });
});

describe('TR-14.1 reopen recovery and isolation', () => {
  it('restores account, orders and fills completely after reopening the database', async () => {
    const name = `test-${crypto.randomUUID()}`;
    const firstLocal = new LocalRepository(name);
    const first = new SimulationRepository(firstLocal);
    await first.openAccount(openDefault());
    const committed = await first.submitOrder(buyAtFill());
    firstLocal.close();

    const reopenedLocal = new LocalRepository(name);
    const reopened = new SimulationRepository(reopenedLocal);
    expect(await reopened.getAccount()).toEqual(committed.account);
    expect(await reopened.listOrders()).toEqual(committed.order ? [committed.order] : []);
    expect(await reopened.listFills()).toEqual(committed.fill ? [committed.fill] : []);
    reopenedLocal.close();
  });

  it('keeps separate database names fully isolated from each other', async () => {
    const one = simulationPair();
    await one.sim.openAccount(openDefault());
    await one.sim.submitOrder(buyAtFill());
    const other = simulationPair();
    expect(await other.sim.getAccount()).toBeNull();
    expect(await other.sim.listOrders()).toEqual([]);
    one.local.close();
    other.local.close();
  });
});

// ---------------------------------------------------------------------------
// TR-14.2 — v4 -> v5 additive migration and backup compatibility
// ---------------------------------------------------------------------------

const v4Note = {
  note_id: 'legacy-note',
  local_profile_id: 'stable-profile',
  snapshot_id: 'snapshot',
  trade_date: '2026-09-10',
  security_id: 'SSE.600519',
  content: {
    title: '旧笔记', content: '正文', judgment: null, evidence: null,
    invalid_condition: null, verify_date: null,
  },
  revision: 1,
  content_hash: 'a'.repeat(64),
  idempotency_key: 'legacy:legacy-note:1',
  state: 'confirmed',
  updated_at: '2026-09-20T10:00:00.000Z',
};

async function seedVersionFour(name: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const opening = indexedDB.open(name, 4);
    opening.onupgradeneeded = () => {
      const db = opening.result;
      db.createObjectStore('profiles').put('stable-profile', 'active');
      db.createObjectStore('notes', { keyPath: ['local_profile_id', 'note_id'] }).put(v4Note);
      db.createObjectStore('note_revisions', { keyPath: ['local_profile_id', 'note_id', 'revision'] }).put(v4Note);
      db.createObjectStore('watchlist', { keyPath: ['local_profile_id', 'security_id'] }).put({
        security_id: 'SSE.600519',
        local_profile_id: 'stable-profile',
        snapshot_id: 'snapshot',
        active: true,
        added_at: '2026-09-20T09:00:00.000Z',
        revision: 1,
      });
      db.createObjectStore('verification_records', {
        keyPath: ['local_profile_id', 'note_id', 'revision', 'verification_id'],
      });
      db.createObjectStore('commands', { keyPath: ['local_profile_id', 'key'] });
      db.createObjectStore('migration_payload');
    };
    opening.onsuccess = () => { opening.result.close(); resolve(); };
    opening.onerror = () => reject(opening.error);
  });
}

describe('TR-14.2 v4 to v5 upgrade', () => {
  it('preserves every v4 note and watchlist row while adding the simulation stores', async () => {
    const name = `test-${crypto.randomUUID()}`;
    await seedVersionFour(name);

    const local = new LocalRepository(name);
    expect(await local.profile()).toBe('stable-profile');
    const notes = await local.listNotes();
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatchObject({
      note_id: 'legacy-note',
      revision: 1,
      snapshot_id: 'snapshot',
      trade_date: '2026-09-10',
      security_id: 'SSE.600519',
      content: { title: '旧笔记', content: '正文' },
      content_hash: expect.stringMatching(/^[\da-f]{64}$/),
    });
    const watchlist = await local.listWatchlist();
    expect(watchlist).toHaveLength(1);
    expect(watchlist[0].security_id).toBe('SSE.600519');

    const db = await rawDatabase(name);
    expect(db.version).toBe(5);
    for (const store of SIMULATION_STORES) {
      expect(db.objectStoreNames.contains(store)).toBe(true);
      expect(await allRows(db, store)).toEqual([]);
    }
    db.close();

    const sim = new SimulationRepository(local);
    await sim.openAccount(openDefault());
    const result = await sim.submitOrder(buyAtFill());
    expect(result.status).toBe('filled');
    local.close();
  });

  it('reopens an already-v5 database idempotently', async () => {
    const name = `test-${crypto.randomUUID()}`;
    const firstLocal = new LocalRepository(name);
    const first = new SimulationRepository(firstLocal);
    await first.openAccount(openDefault());
    await first.submitOrder(buyAtFill());
    firstLocal.close();

    const reopenedLocal = new LocalRepository(name);
    const reopened = new SimulationRepository(reopenedLocal);
    await expect(reopened.openAccount(openDefault())).resolves.toBeDefined();
    expect((await reopened.getAccount())?.revision).toBe(1);
    const raw = await rawDatabase(name);
    expect(raw.version).toBe(5);
    raw.close();
    reopenedLocal.close();
  });

  it('keeps the review backup format review-only: simulation data never leaks into it', async () => {
    const name = `test-${crypto.randomUUID()}`;
    await seedVersionFour(name);
    const local = new LocalRepository(name);
    const sim = new SimulationRepository(local);
    await sim.openAccount(openDefault());
    await sim.submitOrder(buyAtFill());
    const backup = await exportLocalBackup(local);
    expect(Object.keys(backup).sort()).toEqual(
      ['counts', 'exported_at', 'format_version', 'notes', 'profile_mapping',
        'revisions', 'verifications', 'watchlist'].sort(),
    );
    expect(backup.notes).toHaveLength(1);
    expect(JSON.stringify(backup)).not.toContain('simulation_');
    local.close();
  });

  it('converges on the full v5 schema even when simulation opens a fresh database first', async () => {
    const name = `test-${crypto.randomUUID()}`;
    const firstLocal = new LocalRepository(name);
    const sim = new SimulationRepository(firstLocal);
    await sim.openAccount(openDefault());
    firstLocal.close();

    const reopened = new LocalRepository(name);
    expect(await reopened.profile()).toBeDefined();
    await expect(reopened.listNotes()).resolves.toEqual([]);
    await expect(reopened.listWatchlist()).resolves.toEqual([]);
    const raw = await rawDatabase(name);
    for (const store of ['notes', 'watchlist', 'commands', ...SIMULATION_STORES]) {
      expect(raw.objectStoreNames.contains(store)).toBe(true);
    }
    raw.close();
    reopened.close();
  });
});
