/**
 * Strict TypeScript port of ``dragon_radar.research.execution`` (Task 3).
 *
 * Pure functions only — no IO, clock or IDB access. All money is decimal.js
 * fixed-point arithmetic on an isolated HALF_EVEN 34-digit context so the
 * global decimal.js configuration is never mutated. Numeric values cross the
 * boundary as quantized strings, matching the backend Decimal serialization.
 *
 * Rejection precedence is frozen and identical to the Python kernel:
 * price availability, suspension, delisting, limit status, then side-specific
 * T+1 / position / cash guards.
 */
import Decimal from 'decimal.js';

/** Isolated context: precision 34 and ROUND_HALF_EVEN, mirroring Python Decimal. */
export const D = Decimal.clone({ precision: 34, rounding: Decimal.ROUND_HALF_EVEN });

const MONEY_PLACES = 2;
const PRICE_PLACES = 4;
const HALF_EVEN = D.ROUND_HALF_EVEN;

export type OrderSide = 'buy' | 'sell';

export type RejectionReason =
  | 'suspended'
  | 'limit_up_blocked_buy'
  | 'limit_down_blocked_sell'
  | 't1_sell_blocked'
  | 'insufficient_cash'
  | 'insufficient_position'
  | 'delisted'
  | 'no_tradable_price'
  | 'unknown_limit_untradable';

/** Closed set in the backend declaration order; Task 15 UI maps these literals. */
export const REJECTION_REASONS: readonly RejectionReason[] = [
  'suspended',
  'limit_up_blocked_buy',
  'limit_down_blocked_sell',
  't1_sell_blocked',
  'insufficient_cash',
  'insufficient_position',
  'delisted',
  'no_tradable_price',
  'unknown_limit_untradable',
];

export type LimitStatus = 'no_limit' | 'limited' | 'unknown';
export type StampTaxSide = 'sell' | 'buy' | 'both';

export interface CostModel {
  commission_rate: string;
  stamp_tax_rate: string;
  slippage_model: 'fixed_rate' | string;
  slippage_rate: string;
  stamp_tax_side: StampTaxSide | string;
  min_commission: string;
}

export interface Order {
  security_id: string;
  side: OrderSide;
  quantity: number;
  signal_date: string;
}

/** Settlement snapshot; ``available_quantity`` excludes T+1 frozen lots. */
export interface Position {
  security_id: string;
  quantity: string;
  available_quantity: string;
}

export interface PriceBar {
  security_id: string;
  trade_date: string;
  close: string | null;
  adjustment_factor: string;
  limit_status: LimitStatus | string;
  limit_date: string | null;
  limit_up: string | null;
  limit_down: string | null;
}

export interface SecurityStateInput {
  // Optional to mirror the backend defaults (False); absent means not set.
  suspended?: boolean;
  is_delisting?: boolean;
}

export interface Fill {
  kind: 'fill';
  security_id: string;
  side: OrderSide;
  quantity: number;
  signal_date: string;
  trade_date: string;
  raw_close: string;
  fill_price: string;
  gross_amount: string;
  commission: string;
  stamp_tax: string;
  net_cash_delta: string;
}

export interface Rejection {
  kind: 'rejection';
  security_id: string;
  side: OrderSide;
  quantity: number;
  signal_date: string;
  trade_date: string;
  reason: RejectionReason;
}

export type FillOutcome = Fill | Rejection;

type LimitHit = 'none' | 'up' | 'down' | 'unknown';

function quantizeMoney(value: Decimal): string {
  return value.toDecimalPlaces(MONEY_PLACES, HALF_EVEN).toFixed(MONEY_PLACES);
}

function quantizePrice(value: Decimal): string {
  return value.toDecimalPlaces(PRICE_PLACES, HALF_EVEN).toFixed(PRICE_PLACES);
}

function reject(order: Order, bar: PriceBar, reason: RejectionReason): Rejection {
  return {
    kind: 'rejection',
    security_id: order.security_id,
    side: order.side,
    quantity: order.quantity,
    signal_date: order.signal_date,
    trade_date: bar.trade_date,
    reason,
  };
}

/**
 * Exact RAW close equality to dated supplied limits. Unknown metadata is never
 * treated as an explicit no-limit session (port of ``closing_limit_hit``).
 */
export function closingLimitHit(bar: PriceBar): LimitHit {
  if (bar.close === null) return 'unknown';
  const close = new D(bar.close);
  if (!close.isFinite() || close.lte(0)) return 'unknown';

  if (bar.limit_status === 'no_limit') {
    return bar.limit_date === null && bar.limit_up === null && bar.limit_down === null
      ? 'none'
      : 'unknown';
  }
  if (bar.limit_status !== 'limited' || bar.limit_date !== bar.trade_date) return 'unknown';
  if (bar.limit_up === null || bar.limit_down === null) return 'unknown';
  const limitUp = new D(bar.limit_up);
  const limitDown = new D(bar.limit_down);
  if (!limitUp.isFinite() || !limitDown.isFinite()
    || limitUp.lte(0) || limitDown.lte(0)
    || !limitDown.lt(limitUp)
    || close.lt(limitDown) || close.gt(limitUp)) {
    return 'unknown';
  }
  if (close.eq(limitUp)) return 'up';
  if (close.eq(limitDown)) return 'down';
  return 'none';
}

/** Return a priced Fill or a closed-set Rejection; never mutates inputs. */
export function simulateFill(
  order: Order,
  bar: PriceBar,
  state: SecurityStateInput | null,
  rules: CostModel,
  cash: string,
  position: Position | null,
): FillOutcome {
  if (bar.security_id !== order.security_id) {
    throw new Error('bar security_id must match the order');
  }
  if (bar.trade_date <= order.signal_date) {
    throw new Error('fill must occur at the next tradable session after signal_date');
  }

  if (bar.close === null) return reject(order, bar, 'no_tradable_price');
  const close = new D(bar.close);
  if (!close.isFinite() || close.lte(0)) return reject(order, bar, 'no_tradable_price');

  if (state !== null) {
    if (state.suspended === true) return reject(order, bar, 'suspended');
    if (state.is_delisting === true) return reject(order, bar, 'delisted');
  }

  const limitHit = closingLimitHit(bar);
  if (limitHit === 'unknown') return reject(order, bar, 'unknown_limit_untradable');
  if (limitHit === 'up' && order.side === 'buy') return reject(order, bar, 'limit_up_blocked_buy');
  if (limitHit === 'down' && order.side === 'sell') {
    return reject(order, bar, 'limit_down_blocked_sell');
  }

  if (order.side === 'sell') {
    const total = position !== null ? new D(position.quantity) : new D(0);
    if (new D(order.quantity).gt(total)) return reject(order, bar, 'insufficient_position');
    const available = position !== null ? new D(position.available_quantity) : new D(0);
    if (new D(order.quantity).gt(available)) return reject(order, bar, 't1_sell_blocked');
  }

  const quantity = new D(order.quantity);
  const slippage = new D(rules.slippage_rate);
  const rawFillPrice = order.side === 'buy'
    ? close.mul(new D(1).plus(slippage))
    : close.mul(new D(1).minus(slippage));
  const fillPrice = rawFillPrice.toDecimalPlaces(PRICE_PLACES, HALF_EVEN);
  const grossAmount = fillPrice.mul(quantity).toDecimalPlaces(MONEY_PLACES, HALF_EVEN);
  const rateCommission = grossAmount
    .mul(rules.commission_rate)
    .toDecimalPlaces(MONEY_PLACES, HALF_EVEN);
  const minimumCommission = new D(rules.min_commission);
  const commission = rateCommission.gte(minimumCommission) ? rateCommission : minimumCommission;
  const stampTax = order.side === 'sell'
    ? grossAmount.mul(rules.stamp_tax_rate).toDecimalPlaces(MONEY_PLACES, HALF_EVEN)
    : new D(0);

  let netCashDelta: Decimal;
  if (order.side === 'buy') {
    const requiredCash = grossAmount.plus(commission).plus(stampTax);
    if (new D(cash).lt(requiredCash)) return reject(order, bar, 'insufficient_cash');
    netCashDelta = requiredCash.neg();
  } else {
    netCashDelta = grossAmount.minus(commission).minus(stampTax);
  }

  return {
    kind: 'fill',
    security_id: order.security_id,
    side: order.side,
    quantity: order.quantity,
    signal_date: order.signal_date,
    trade_date: bar.trade_date,
    raw_close: bar.close,
    fill_price: quantizePrice(fillPrice),
    gross_amount: quantizeMoney(grossAmount),
    commission: quantizeMoney(commission),
    stamp_tax: quantizeMoney(stampTax),
    net_cash_delta: quantizeMoney(netCashDelta),
  };
}
