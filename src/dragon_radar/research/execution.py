"""Deterministic A-share execution rule ``simulate_fill``.

Pure function with Decimal fixed-point arithmetic and no IO or clock. A T-day
signal can only fill at the next tradable session (``bar.trade_date`` strictly
after ``order.signal_date``). Fills price at RAW close plus the disclosed
fixed-rate slippage; the adjustment factor never enters the fill price.

Rejection precedence is fixed and documented: price availability, suspension,
delisting, limit status, then side-specific T+1/position/cash guards. Unknown
limit metadata is never treated as an explicit no-limit session.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from dragon_radar.analytics.metrics import closing_limit_hit
from dragon_radar.domain.models import DailyBar, Identifier, SecurityState
from dragon_radar.research.models import CostModel, StrictFrozenModel

MONEY_QUANTUM = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0001")

OrderSide = Literal["buy", "sell"]
RejectionReason = Literal[
    "suspended",
    "limit_up_blocked_buy",
    "limit_down_blocked_sell",
    "t1_sell_blocked",
    "insufficient_cash",
    "insufficient_position",
    "delisted",
    "no_tradable_price",
    "unknown_limit_untradable",
]

NonNegativeQuantity = Annotated[Decimal, Field(ge=Decimal("0"))]
PositiveQuantity = Annotated[int, Field(ge=1)]


class Order(StrictFrozenModel):
    security_id: Identifier
    side: OrderSide
    quantity: PositiveQuantity
    signal_date: date


class Position(StrictFrozenModel):
    """Settlement snapshot; ``available_quantity`` excludes T+1 frozen lots."""

    security_id: Identifier
    quantity: NonNegativeQuantity
    available_quantity: NonNegativeQuantity

    @model_validator(mode="after")
    def _available_within_total(self) -> Position:
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity must not exceed quantity")
        return self


class Fill(StrictFrozenModel):
    security_id: Identifier
    side: OrderSide
    quantity: PositiveQuantity
    signal_date: date
    trade_date: date
    raw_close: Decimal
    fill_price: Decimal
    gross_amount: Decimal
    commission: Decimal
    stamp_tax: Decimal
    net_cash_delta: Decimal


class Rejection(StrictFrozenModel):
    security_id: Identifier
    side: OrderSide
    quantity: PositiveQuantity
    signal_date: date
    trade_date: date
    reason: RejectionReason


def _reject(order: Order, bar: DailyBar, reason: RejectionReason) -> Rejection:
    return Rejection(
        security_id=order.security_id,
        side=order.side,
        quantity=order.quantity,
        signal_date=order.signal_date,
        trade_date=bar.trade_date,
        reason=reason,
    )


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def simulate_fill(
    order: Order,
    bar: DailyBar,
    state: SecurityState | None,
    rules: CostModel,
    cash: Decimal,
    position: Position | None,
) -> Fill | Rejection:
    """Return a priced Fill or a closed-set Rejection; never mutates inputs."""
    if bar.security_id != order.security_id:
        raise ValueError("bar security_id must match the order")
    if bar.trade_date <= order.signal_date:
        raise ValueError(
            "fill must occur at the next tradable session after signal_date"
        )

    close = bar.close
    if close is None or not close.is_finite() or close <= 0:
        return _reject(order, bar, "no_tradable_price")

    if state is not None:
        if state.suspended is True:
            return _reject(order, bar, "suspended")
        if state.is_delisting is True:
            return _reject(order, bar, "delisted")

    limit_hit = closing_limit_hit(bar)
    if limit_hit == "unknown":
        return _reject(order, bar, "unknown_limit_untradable")
    if limit_hit == "up" and order.side == "buy":
        return _reject(order, bar, "limit_up_blocked_buy")
    if limit_hit == "down" and order.side == "sell":
        return _reject(order, bar, "limit_down_blocked_sell")

    if order.side == "sell":
        total = position.quantity if position is not None else Decimal("0")
        if order.quantity > total:
            return _reject(order, bar, "insufficient_position")
        available = (
            position.available_quantity if position is not None else Decimal("0")
        )
        if order.quantity > available:
            return _reject(order, bar, "t1_sell_blocked")

    quantity_decimal = Decimal(order.quantity)
    if order.side == "buy":
        fill_price = (close * (Decimal("1") + rules.slippage_rate)).quantize(
            PRICE_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    else:
        fill_price = (close * (Decimal("1") - rules.slippage_rate)).quantize(
            PRICE_QUANTUM, rounding=ROUND_HALF_EVEN
        )
    gross_amount = _quantize_money(fill_price * quantity_decimal)
    raw_commission = gross_amount * rules.commission_rate
    commission = _quantize_money(
        max(_quantize_money(raw_commission), rules.min_commission)
    )
    stamp_tax = (
        _quantize_money(gross_amount * rules.stamp_tax_rate)
        if order.side == "sell"
        else Decimal("0.00")
    )

    if order.side == "buy":
        required_cash = gross_amount + commission + stamp_tax
        if cash < required_cash:
            return _reject(order, bar, "insufficient_cash")
        net_cash_delta = -required_cash
    else:
        net_cash_delta = gross_amount - commission - stamp_tax

    return Fill(
        security_id=order.security_id,
        side=order.side,
        quantity=order.quantity,
        signal_date=order.signal_date,
        trade_date=bar.trade_date,
        raw_close=close,
        fill_price=fill_price,
        gross_amount=gross_amount,
        commission=commission,
        stamp_tax=stamp_tax,
        net_cash_delta=net_cash_delta,
    )


__all__ = [
    "Fill",
    "Order",
    "Position",
    "Rejection",
    "RejectionReason",
    "simulate_fill",
]
