"""Deterministic Decimal34 primitives shared by role evaluators."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)


class AnalysisUnavailable(ValueError):
    """A required role-factor observation cannot be computed."""


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def _finite(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AnalysisUnavailable(f"{name} must be a finite Decimal")
    return value


def _positive(value: Decimal, name: str) -> Decimal:
    value = _finite(value, name)
    if value <= 0:
        raise AnalysisUnavailable(f"{name} must be positive")
    return value


def _values(
    values: Sequence[Decimal],
    name: str,
    *,
    nonnegative: bool = False,
) -> tuple[Decimal, ...]:
    if not isinstance(values, Sequence) or not values:
        raise AnalysisUnavailable(f"{name} must be a nonempty sequence")
    result = tuple(_finite(value, name) for value in values)
    if nonnegative and any(value < 0 for value in result):
        raise AnalysisUnavailable(f"{name} must be nonnegative")
    return result


def adjusted_price(close: Decimal, adjustment_factor: Decimal) -> Decimal:
    """Return raw close multiplied by its same-snapshot adjustment factor."""
    close = _positive(close, "close")
    adjustment_factor = _positive(adjustment_factor, "adjustment factor")
    with localcontext(_decimal_context()):
        return close * adjustment_factor


def period_return(start: Decimal, end: Decimal) -> Decimal:
    """Return end/start - 1 for two adjusted prices."""
    start = _positive(start, "start price")
    end = _positive(end, "end price")
    with localcontext(_decimal_context()):
        return end / start - 1


def decimal_mean(values: Sequence[Decimal]) -> Decimal:
    """Return the Decimal34 arithmetic mean of a complete nonempty sample."""
    sample = _values(values, "values")
    with localcontext(_decimal_context()):
        return sum(sample, start=Decimal(0)) / Decimal(len(sample))


def decimal_median(values: Sequence[Decimal]) -> Decimal:
    """Return the exact ordered median, averaging the middle pair when even."""
    sample = tuple(sorted(_values(values, "values")))
    midpoint = len(sample) // 2
    if len(sample) % 2:
        return sample[midpoint]
    with localcontext(_decimal_context()):
        return (sample[midpoint - 1] + sample[midpoint]) / Decimal(2)


def relative_return(
    security_return: Decimal,
    benchmark_return: Decimal,
) -> Decimal:
    """Return security return minus benchmark return."""
    security_return = _finite(security_return, "security return")
    benchmark_return = _finite(benchmark_return, "benchmark return")
    with localcontext(_decimal_context()):
        return security_return - benchmark_return


def maximum_drawdown(prices: Sequence[Decimal]) -> Decimal:
    """Return the maximum loss from each running adjusted-price peak."""
    sample = _values(prices, "prices")
    if any(price <= 0 for price in sample):
        raise AnalysisUnavailable("prices must be positive")
    peak = sample[0]
    worst = Decimal(0)
    with localcontext(_decimal_context()):
        for price in sample:
            peak = max(peak, price)
            worst = max(worst, Decimal(1) - price / peak)
    return worst


def topic_amount_share(
    amount: Decimal,
    eligible_amounts: Sequence[Decimal],
    *,
    currency: str | None = None,
    eligible_currencies: Sequence[str] | None = None,
) -> Decimal:
    """Return one security's raw-currency amount share of eligible members."""
    amount = _finite(amount, "amount")
    if amount < 0:
        raise AnalysisUnavailable("amount must be nonnegative")
    amounts = _values(eligible_amounts, "eligible amounts", nonnegative=True)
    if not isinstance(currency, str) or not currency:
        raise AnalysisUnavailable("currency metadata is required")
    if eligible_currencies is None:
        raise AnalysisUnavailable("currency metadata is required")
    currencies = tuple(eligible_currencies)
    if len(currencies) != len(amounts) or any(
        not isinstance(member_currency, str)
        or not member_currency
        or member_currency != currency
        for member_currency in currencies
    ):
        raise AnalysisUnavailable("currency mismatch in topic amounts")
    with localcontext(_decimal_context()):
        amount = +amount
        normalized_amounts = tuple(+member_amount for member_amount in amounts)
        denominator = sum(normalized_amounts, start=Decimal(0))
        _positive(denominator, "topic amount denominator")
        if amount > denominator:
            raise AnalysisUnavailable(
                "amount must not exceed eligible topic amount denominator"
            )
        return amount / denominator


def mean_absolute_deviation_rate(values: Sequence[Decimal]) -> Decimal:
    """Return mean absolute deviation divided by the sample mean."""
    sample = _values(values, "values")
    mean = decimal_mean(sample)
    _positive(mean, "mean")
    with localcontext(_decimal_context()):
        deviation = sum(
            (abs(value - mean) for value in sample),
            start=Decimal(0),
        ) / Decimal(len(sample))
        return deviation / mean


def knowledge_cutoff(market_as_of: datetime, topic_as_of: datetime) -> datetime:
    """Return the earlier aware snapshot cutoff, normalized to UTC."""
    for value in (market_as_of, topic_as_of):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("knowledge cutoff requires timezone-aware datetimes")
    return min(market_as_of, topic_as_of).astimezone(UTC)
