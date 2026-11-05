"""Decimal settlement ledger, run metrics and cost sensitivity (Task 4).

Pure functions only — no IO, clock or model calls. Every transition returns a
new immutable ``LedgerState`` and preserves the cash/holdings/equity identity:
cash and quantities never go negative, T-day buys stay frozen until the next
``roll_overnight`` and a closed lot drops out of the holdings map. Run metrics
reuse the pure Decimal analytics kernel; ratios are reported at six fractional
digits and money at two, all ROUND_HALF_EVEN.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Annotated

from pydantic import Field, model_validator

from dragon_radar.analytics.metrics import (
    excess_return,
    interval_return,
    maximum_drawdown,
)
from dragon_radar.domain.models import Identifier
from dragon_radar.research.execution import Fill, OrderSide
from dragon_radar.research.models import (
    COST_SENSITIVITY_MULTIPLIERS,
    COST_SENSITIVITY_TIERS,
    CostModel,
    CostTier,
    StrictFrozenModel,
)

MONEY_QUANTUM = Decimal("0.01")
RATIO_QUANTUM = Decimal("0.000001")
ZERO_MONEY = Decimal("0.00")
ZERO_RATIO = Decimal("0.000000")

NonNegativeMoney = Annotated[Decimal, Field(ge=Decimal("0"))]
NonNegativeQuantity = Annotated[Decimal, Field(ge=Decimal("0"))]
PositiveIntQuantity = Annotated[int, Field(ge=1)]


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _quantize_ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Closed division: a zero denominator reports zero instead of raising."""
    if denominator == 0:
        return ZERO_RATIO
    with localcontext(_decimal_context()):
        return _quantize_ratio(numerator / denominator)


def _sum(decimals: Sequence[Decimal]) -> Decimal:
    total = ZERO_MONEY
    for value in decimals:
        total += value
    return total


class Holding(StrictFrozenModel):
    """Settled position; ``available_quantity`` excludes T+1 frozen lots."""

    quantity: NonNegativeQuantity
    available_quantity: NonNegativeQuantity

    @model_validator(mode="after")
    def _available_within_total(self) -> Holding:
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity must not exceed quantity")
        return self


class LedgerState(StrictFrozenModel):
    """Cash plus settled holdings at ``as_of``; cash can never be negative."""

    as_of: date
    cash: NonNegativeMoney
    holdings: dict[str, Holding]


class EquityPoint(StrictFrozenModel):
    trade_date: date
    equity: NonNegativeMoney


class OrderOutcome(StrictFrozenModel):
    """One intended order and whether it filled; feeds unfillable ratios."""

    security_id: Identifier
    side: OrderSide
    quantity: PositiveIntQuantity
    intended_amount: NonNegativeMoney
    filled: bool


class Metrics(StrictFrozenModel):
    """Golden run summary; ratios at six digits, money at two."""

    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    benchmark_return: Decimal
    excess_return: Decimal
    max_drawdown: Decimal
    turnover_rate: Decimal
    total_commission: Decimal
    total_stamp_tax: Decimal
    total_cost: Decimal
    unfillable_count_ratio: Decimal
    unfillable_amount_ratio: Decimal


def open_ledger(initial_cash: Decimal, as_of: date) -> LedgerState:
    """Create an empty ledger; the config layer owns initial-cash bounds."""
    return LedgerState(as_of=as_of, cash=initial_cash, holdings={})


def apply_fill(ledger: LedgerState, fill: Fill) -> LedgerState:
    """Apply a priced fill immutably, enforcing T+1 and non-negativity."""
    holdings = dict(ledger.holdings)
    quantity = Decimal(fill.quantity)

    if fill.side == "buy":
        current = holdings.get(fill.security_id)
        holdings[fill.security_id] = Holding(
            quantity=(current.quantity if current is not None else ZERO_MONEY)
            + quantity,
            # T-day buys are frozen: the available lot count does not move.
            available_quantity=(
                current.available_quantity if current is not None else Decimal("0")
            ),
        )
    else:
        current = holdings.get(fill.security_id)
        total = current.quantity if current is not None else Decimal("0")
        available = current.available_quantity if current is not None else Decimal("0")
        if quantity > total:
            raise ValueError("sell quantity exceeds settled holdings")
        if quantity > available:
            raise ValueError("sell quantity exceeds T+1 available lots")
        remaining = total - quantity
        if remaining == 0:
            holdings.pop(fill.security_id, None)
        else:
            holdings[fill.security_id] = Holding(
                quantity=remaining,
                available_quantity=available - quantity,
            )

    cash = ledger.cash + fill.net_cash_delta
    if cash < 0:
        raise ValueError("ledger cash must never go negative")
    return LedgerState(as_of=ledger.as_of, cash=cash, holdings=holdings)


def roll_overnight(ledger: LedgerState, next_date: date) -> LedgerState:
    """Release every frozen lot for settlement at the next session."""
    holdings = {
        security_id: Holding(
            quantity=holding.quantity,
            available_quantity=holding.quantity,
        )
        for security_id, holding in ledger.holdings.items()
    }
    return LedgerState(as_of=next_date, cash=ledger.cash, holdings=holdings)


def mark_equity(ledger: LedgerState, prices: dict[str, Decimal]) -> Decimal:
    """Cash plus holdings valued at supplied marks; never fabricates a price."""
    equity = ledger.cash
    for security_id, holding in ledger.holdings.items():
        price = prices.get(security_id)
        if price is None or not price.is_finite() or price <= 0:
            raise ValueError(f"missing or invalid mark price for {security_id}")
        equity += holding.quantity * price
    return equity


def summarize_run(
    equity_points: Sequence[EquityPoint],
    fills: Sequence[Fill],
    outcomes: Sequence[OrderOutcome],
    benchmark_prices: Sequence[Decimal] | None,
) -> Metrics:
    """Aggregate one deterministic run; empty streams close to zero ratios."""
    if not equity_points:
        raise ValueError("equity_points must contain at least one observation")

    equities = tuple(point.equity for point in equity_points)
    initial_equity = equities[0]
    final_equity = equities[-1]

    with localcontext(_decimal_context()):
        raw_total_return = interval_return(equities)
        raw_benchmark_return = (
            interval_return(tuple(benchmark_prices))
            if benchmark_prices is not None
            else Decimal("0")
        )
        raw_excess_return = excess_return(raw_total_return, raw_benchmark_return)
        raw_max_drawdown = maximum_drawdown(equities)

    total_commission = _sum(tuple(fill.commission for fill in fills))
    total_stamp_tax = _sum(tuple(fill.stamp_tax for fill in fills))
    gross_amount = _sum(tuple(fill.gross_amount for fill in fills))

    rejected = tuple(outcome for outcome in outcomes if not outcome.filled)
    intended_total = _sum(tuple(outcome.intended_amount for outcome in outcomes))
    rejected_amount = _sum(tuple(outcome.intended_amount for outcome in rejected))

    return Metrics(
        initial_equity=_quantize_money(initial_equity),
        final_equity=_quantize_money(final_equity),
        total_return=_quantize_ratio(raw_total_return),
        benchmark_return=_quantize_ratio(raw_benchmark_return),
        excess_return=_quantize_ratio(raw_excess_return),
        max_drawdown=_quantize_ratio(raw_max_drawdown),
        turnover_rate=_ratio(gross_amount, initial_equity),
        total_commission=_quantize_money(total_commission),
        total_stamp_tax=_quantize_money(total_stamp_tax),
        total_cost=_quantize_money(total_commission + total_stamp_tax),
        unfillable_count_ratio=_ratio(Decimal(len(rejected)), Decimal(len(outcomes))),
        unfillable_amount_ratio=_ratio(rejected_amount, intended_total),
    )


def cost_sensitivity_models(base: CostModel) -> dict[CostTier, CostModel]:
    """Build low/base/high cost models scaling every fee component together."""
    tiers: dict[CostTier, CostModel] = {}
    for tier_name in COST_SENSITIVITY_TIERS:
        multiplier = COST_SENSITIVITY_MULTIPLIERS[tier_name]
        tiers[tier_name] = CostModel(
            commission_rate=base.commission_rate * multiplier,
            stamp_tax_rate=base.stamp_tax_rate * multiplier,
            slippage_model=base.slippage_model,
            slippage_rate=base.slippage_rate * multiplier,
            stamp_tax_side=base.stamp_tax_side,
            min_commission=base.min_commission * multiplier,
        )
    return tiers


__all__ = [
    "EquityPoint",
    "Holding",
    "LedgerState",
    "Metrics",
    "OrderOutcome",
    "apply_fill",
    "cost_sensitivity_models",
    "mark_equity",
    "open_ledger",
    "roll_overnight",
    "summarize_run",
]
