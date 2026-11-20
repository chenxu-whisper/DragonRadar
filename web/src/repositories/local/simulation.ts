/**
 * Task 14 — local simulation ledger on IndexedDB v5.
 *
 * Four profile-partitioned object stores back an immutable command log with
 * the same contract as the v4 review repository: idempotency keys hash the
 * full command payload, optimistic revisions reject stale writers, every
 * write happens in one read-modify-write transaction and success is only
 * acknowledged after a fresh readback transaction agrees byte-for-byte.
 *
 * The repository never opens its own connection — it shares the owning
 * LocalRepository connection so a single v5 upgrade serves both surfaces.
 */
import {
  simulateFill,
  type CostModel,
  type Fill,
  type Order,
  type OrderSide,
  type Position,
  type PriceBar,
  type Rejection,
  type RejectionReason,
  type SecurityStateInput,
} from '../../features/research/execution';
import { applyFill, openLedger, type Holding, type LedgerState } from '../../features/research/ledger';
import type { LocalRepository } from './repository';

export const SIMULATION_STORES = [
  'simulation_accounts',
  'simulation_orders',
  'simulation_fills',
  'simulation_commands',
] as const;

const PRIMARY_ACCOUNT = 'primary';

/** Add the four v5 simulation stores; safe to call repeatedly in one upgrade. */
export function createSimulationStores(db: IDBDatabase): void {
  if (!db.objectStoreNames.contains('simulation_accounts')) {
    db.createObjectStore('simulation_accounts', { keyPath: ['local_profile_id', 'account_id'] });
  }
  if (!db.objectStoreNames.contains('simulation_orders')) {
    db.createObjectStore('simulation_orders', { keyPath: ['local_profile_id', 'order_id'] });
  }
  if (!db.objectStoreNames.contains('simulation_fills')) {
    db.createObjectStore('simulation_fills', { keyPath: ['local_profile_id', 'fill_id'] });
  }
  if (!db.objectStoreNames.contains('simulation_commands')) {
    db.createObjectStore('simulation_commands', { keyPath: ['local_profile_id', 'key'] });
  }
}

export interface SimulationAccountRecord {
  local_profile_id: string;
  account_id: string;
  revision: number;
  cash: string;
  holdings: Record<string, Holding>;
  as_of: string;
}

export interface SimulationOrderRecord {
  local_profile_id: string;
  order_id: string;
  command_key: string;
  security_id: string;
  side: OrderSide;
  quantity: number;
  signal_date: string;
  trade_date: string;
  status: 'filled' | 'rejected';
  reason: RejectionReason | null;
  revision: number;
}

export interface SimulationFillRecord extends Fill {
  local_profile_id: string;
  fill_id: string;
  order_id: string;
}

interface SimulationCommandRecord {
  local_profile_id: string;
  key: string;
  payload: string;
  result: SimulationOrderResult | SimulationAccountRecord;
}

export interface OpenSimulationAccount {
  key: string;
  initial_cash: string;
  as_of: string;
}

export interface PlaceSimulationOrder {
  key: string;
  expected_revision: number;
  order: Order;
  bar: PriceBar;
  state: SecurityStateInput;
  rules: CostModel;
}

export interface SimulationOrderResult {
  committed: boolean;
  readback_verified: boolean;
  status: 'filled' | 'rejected';
  rejection: { reason: RejectionReason } | null;
  fill: SimulationFillRecord | null;
  account: SimulationAccountRecord;
  order: SimulationOrderRecord;
}

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result);
    value.onerror = () => reject(value.error ?? new Error('模拟账本读取失败'));
  });
}

function completion(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new Error('模拟账本事务已取消，未保存'));
    tx.onerror = () => { /* abort reports the final failure */ };
  });
}

async function digest(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const result = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(result)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export class SimulationRepository {
  constructor(private readonly local: LocalRepository) {}

  private async connect(): Promise<IDBDatabase> {
    return this.local.connect();
  }

  private async profile(): Promise<string> {
    return this.local.profile();
  }

  async openAccount(input: OpenSimulationAccount): Promise<SimulationAccountRecord> {
    const profile = await this.profile();
    const payload = await digest(JSON.stringify(['openAccount', input]));
    const db = await this.connect();
    const tx = db.transaction(SIMULATION_STORES, 'readwrite');
    const done = completion(tx);
    let account: SimulationAccountRecord;
    let replayed = false;
    try {
      const commands = tx.objectStore('simulation_commands');
      const previous = await request(commands.get([profile, input.key])) as
        SimulationCommandRecord | undefined;
      const accounts = tx.objectStore('simulation_accounts');
      const current = await request(accounts.get([profile, PRIMARY_ACCOUNT])) as
        SimulationAccountRecord | undefined;
      if (previous) {
        if (previous.payload !== payload) {
          throw new Error('幂等键已用于不同的模拟开户参数，请更换操作键');
        }
        account = previous.result as SimulationAccountRecord;
        replayed = true;
      } else {
        if (current) throw new Error('模拟账本已存在，不能重复开户');
        const ledger = openLedger(input.initial_cash, input.as_of);
        account = {
          local_profile_id: profile,
          account_id: PRIMARY_ACCOUNT,
          revision: 0,
          cash: ledger.cash,
          holdings: {},
          as_of: input.as_of,
        };
        accounts.put(account);
        commands.put({ local_profile_id: profile, key: input.key, payload, result: account });
      }
      await done;
    } catch (error) {
      try { tx.abort(); } catch { /* already finished */ }
      await done.catch(() => undefined);
      throw error;
    }
    try {
      const readback = await request(
        db.transaction('simulation_accounts')
          .objectStore('simulation_accounts')
          .get([profile, PRIMARY_ACCOUNT]),
      ) as SimulationAccountRecord | undefined;
      // A replayed opening is a historical command: the ledger may already have
      // advanced, so only its continued existence is verified (mirrors putNote).
      if (replayed) {
        if (!readback) throw new Error('readback mismatch');
      } else if (JSON.stringify(readback) !== JSON.stringify(account)) {
        throw new Error('readback mismatch');
      }
    } catch {
      throw new Error('回读检测到模拟账本版本变化，请重试');
    }
    return account;
  }

  async getAccount(): Promise<SimulationAccountRecord | null> {
    const profile = await this.profile();
    const db = await this.connect();
    const account = await request(
      db.transaction('simulation_accounts')
        .objectStore('simulation_accounts')
        .get([profile, PRIMARY_ACCOUNT]),
    ) as SimulationAccountRecord | undefined;
    return account ?? null;
  }

  async listOrders(): Promise<SimulationOrderRecord[]> {
    const profile = await this.profile();
    const db = await this.connect();
    const rows = await request(
      db.transaction('simulation_orders').objectStore('simulation_orders').getAll(),
    ) as SimulationOrderRecord[];
    return rows.filter(row => row.local_profile_id === profile);
  }

  async listFills(): Promise<SimulationFillRecord[]> {
    const profile = await this.profile();
    const db = await this.connect();
    const rows = await request(
      db.transaction('simulation_fills').objectStore('simulation_fills').getAll(),
    ) as SimulationFillRecord[];
    return rows.filter(row => row.local_profile_id === profile);
  }

  async submitOrder(input: PlaceSimulationOrder): Promise<SimulationOrderResult> {
    const profile = await this.profile();
    const payload = await digest(JSON.stringify(['submitOrder', input]));
    const db = await this.connect();
    const tx = db.transaction(SIMULATION_STORES, 'readwrite');
    const done = completion(tx);
    let result: SimulationOrderResult;
    try {
      const commands = tx.objectStore('simulation_commands');
      const previous = await request(commands.get([profile, input.key])) as
        SimulationCommandRecord | undefined;
      if (previous) {
        if (previous.payload !== payload) {
          throw new Error('幂等键已用于不同的模拟下单内容，请更换操作键');
        }
        await done;
        return previous.result as SimulationOrderResult;
      }

      const accounts = tx.objectStore('simulation_accounts');
      const current = await request(accounts.get([profile, PRIMARY_ACCOUNT])) as
        SimulationAccountRecord | undefined;
      if (!current) throw new Error('模拟账本不存在，请先开户');
      if (current.revision !== input.expected_revision) {
        throw new Error('版本冲突：模拟账本已被其他操作修改，请对照最新 revision');
      }

      const holding = current.holdings[input.order.security_id];
      const position: Position | null = holding
        ? {
            security_id: input.order.security_id,
            quantity: holding.quantity,
            available_quantity: holding.available_quantity,
          }
        : null;
      const outcome = simulateFill(
        input.order, input.bar, input.state, input.rules, current.cash, position,
      );

      const orderId = crypto.randomUUID();
      const orderRecord: SimulationOrderRecord = {
        local_profile_id: profile,
        order_id: orderId,
        command_key: input.key,
        security_id: input.order.security_id,
        side: input.order.side,
        quantity: input.order.quantity,
        signal_date: input.order.signal_date,
        trade_date: input.bar.trade_date,
        status: outcome.kind === 'fill' ? 'filled' : 'rejected',
        reason: outcome.kind === 'rejection' ? (outcome as Rejection).reason : null,
        revision: current.revision,
      };

      let fillRecord: SimulationFillRecord | null = null;
      let nextAccount = current;
      if (outcome.kind === 'fill') {
        const ledger: LedgerState = {
          as_of: current.as_of,
          cash: current.cash,
          holdings: structuredClone(current.holdings),
        };
        const settled = applyFill(ledger, outcome as Fill);
        nextAccount = {
          ...current,
          cash: settled.cash,
          holdings: settled.holdings,
          revision: current.revision + 1,
        };
        orderRecord.revision = nextAccount.revision;
        fillRecord = {
          ...(outcome as Fill),
          local_profile_id: profile,
          fill_id: crypto.randomUUID(),
          order_id: orderId,
        };
        tx.objectStore('simulation_fills').put(fillRecord);
        accounts.put(nextAccount);
      }

      result = {
        committed: true,
        readback_verified: true,
        status: orderRecord.status,
        rejection: outcome.kind === 'rejection'
          ? { reason: (outcome as Rejection).reason }
          : null,
        fill: fillRecord,
        account: nextAccount,
        order: orderRecord,
      };
      tx.objectStore('simulation_orders').put(orderRecord);
      commands.put({ local_profile_id: profile, key: input.key, payload, result });
      await done;
    } catch (error) {
      try { tx.abort(); } catch { /* already finished */ }
      await done.catch(() => undefined);
      throw error;
    }

    try {
      const readbackTx = db.transaction(SIMULATION_STORES, 'readonly');
      const account = await request(
        readbackTx.objectStore('simulation_accounts').get([profile, PRIMARY_ACCOUNT]),
      ) as SimulationAccountRecord;
      const order = await request(
        readbackTx.objectStore('simulation_orders').get([profile, result.order.order_id]),
      ) as SimulationOrderRecord;
      if (JSON.stringify(account) !== JSON.stringify(result.account)
        || JSON.stringify(order) !== JSON.stringify(result.order)) {
        throw new Error('readback mismatch');
      }
      if (result.fill) {
        const fill = await request(
          readbackTx.objectStore('simulation_fills').get([profile, result.fill.fill_id]),
        ) as SimulationFillRecord;
        if (JSON.stringify(fill) !== JSON.stringify(result.fill)) {
          throw new Error('readback mismatch');
        }
      }
      const command = await request(
        readbackTx.objectStore('simulation_commands').get([profile, input.key]),
      ) as SimulationCommandRecord;
      if (JSON.stringify(command.result) !== JSON.stringify(result)) {
        throw new Error('readback mismatch');
      }
    } catch {
      throw new Error('回读检测到模拟账本状态变化，请重试');
    }
    return result;
  }
}
