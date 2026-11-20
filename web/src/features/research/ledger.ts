/**
 * Strict TypeScript port of ``dragon_radar.research.ledger`` (Task 4).
 *
 * Pure functions only — no IO, clock or model calls. Every transition returns
 * a new immutable ledger state and preserves the backend invariants: cash and
 * quantities never go negative, T-day buys stay frozen until the next
 * ``rollOvernight`` and a closed lot drops out of the holdings map.
 */
import Decimal from 'decimal.js';
import { D, type Fill } from './execution';

const MONEY_PLACES = 2;
const HALF_EVEN = D.ROUND_HALF_EVEN;

export interface Holding {
  quantity: string;
  available_quantity: string;
}

/** Cash plus settled holdings at ``as_of``; cash can never be negative. */
export interface LedgerState {
  as_of: string;
  cash: string;
  holdings: Record<string, Holding>;
}

function money(value: Decimal): string {
  return value.toDecimalPlaces(MONEY_PLACES, HALF_EVEN).toFixed(MONEY_PLACES);
}

/** Create an empty ledger; the config layer owns initial-cash bounds. */
export function openLedger(initialCash: string, asOf: string): LedgerState {
  const cash = new D(initialCash);
  if (!cash.isFinite() || cash.isNegative()) {
    throw new Error('initial cash must be zero or positive');
  }
  return { as_of: asOf, cash: money(cash), holdings: {} };
}

/** Apply a priced fill immutably, enforcing T+1 and non-negativity. */
export function applyFill(ledger: LedgerState, fill: Fill): LedgerState {
  if (fill.kind !== 'fill') {
    throw new Error('only priced fills can be applied to the ledger');
  }
  const holdings: Record<string, Holding> = { ...ledger.holdings };
  const quantity = new D(fill.quantity);

  if (fill.side === 'buy') {
    const current = holdings[fill.security_id];
    const total = current !== undefined ? new D(current.quantity) : new D(0);
    holdings[fill.security_id] = {
      // T-day buys are frozen: the available lot count does not move.
      quantity: total.plus(quantity).toFixed(0),
      available_quantity: current !== undefined ? current.available_quantity : '0',
    };
  } else {
    const current = holdings[fill.security_id];
    const total = current !== undefined ? new D(current.quantity) : new D(0);
    const available = current !== undefined ? new D(current.available_quantity) : new D(0);
    if (quantity.gt(total)) throw new Error('sell quantity exceeds settled holdings');
    if (quantity.gt(available)) throw new Error('sell quantity exceeds T+1 available lots');
    const remaining = total.minus(quantity);
    if (remaining.isZero()) {
      delete holdings[fill.security_id];
    } else {
      holdings[fill.security_id] = {
        quantity: remaining.toFixed(0),
        available_quantity: available.minus(quantity).toFixed(0),
      };
    }
  }

  const cash = new D(ledger.cash).plus(fill.net_cash_delta);
  if (cash.isNegative()) throw new Error('ledger cash must never go negative');
  return { as_of: ledger.as_of, cash: money(cash), holdings };
}

/** Release every frozen lot for settlement at the next session. */
export function rollOvernight(ledger: LedgerState, nextDate: string): LedgerState {
  const holdings: Record<string, Holding> = {};
  for (const [securityId, holding] of Object.entries(ledger.holdings)) {
    holdings[securityId] = { quantity: holding.quantity, available_quantity: holding.quantity };
  }
  return { as_of: nextDate, cash: ledger.cash, holdings };
}

/** Cash plus holdings valued at supplied marks; never fabricates a price. */
export function markEquity(ledger: LedgerState, prices: Record<string, string>): string {
  let equity = new D(ledger.cash);
  for (const [securityId, holding] of Object.entries(ledger.holdings)) {
    const rawPrice = prices[securityId];
    if (rawPrice === undefined || rawPrice === null) {
      throw new Error(`missing or invalid mark price for ${securityId}`);
    }
    const price = new D(rawPrice);
    if (!price.isFinite() || price.lte(0)) {
      throw new Error(`missing or invalid mark price for ${securityId}`);
    }
    equity = equity.plus(new D(holding.quantity).mul(price));
  }
  return money(equity);
}
