"""Deterministic Decimal34 primitives shared by role evaluators."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)


class AnalysisUnavailable(ValueError):
    """A required role-factor observation cannot be computed."""


DecimalNormalizer = Callable[..., tuple[Decimal, ...]]


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


@contextmanager
def _decimal34_operation(operation: str) -> Iterator[DecimalNormalizer]:
    try:
        with localcontext(_decimal_context()):
            yield lambda *operands: tuple(+operand for operand in operands)
    except DecimalException as exc:
        raise AnalysisUnavailable(
            f"{operation}: Decimal34 arithmetic unavailable"
        ) from exc


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
    with _decimal34_operation("adjusted_price") as normalize:
        close, adjustment_factor = normalize(close, adjustment_factor)
        _positive(close, "close")
        _positive(adjustment_factor, "adjustment factor")
        return close * adjustment_factor


def period_return(start: Decimal, end: Decimal) -> Decimal:
    """Return end/start - 1 for two adjusted prices."""
    start = _positive(start, "start price")
    end = _positive(end, "end price")
    with _decimal34_operation("period_return") as normalize:
        start, end, one = normalize(start, end, Decimal(1))
        _positive(start, "start price")
        _positive(end, "end price")
        return end / start - one


def decimal_mean(values: Sequence[Decimal]) -> Decimal:
    """Return the Decimal34 arithmetic mean of a complete nonempty sample."""
    sample = _values(values, "values")
    with _decimal34_operation("decimal_mean") as normalize:
        *sample, zero, count = normalize(
            *sample,
            Decimal(0),
            Decimal(len(sample)),
        )
        return sum(sample, start=zero) / count


def decimal_median(values: Sequence[Decimal]) -> Decimal:
    """Return the exact ordered median, averaging the middle pair when even."""
    sample = _values(values, "values")
    with _decimal34_operation("decimal_median") as normalize:
        *sample, two = normalize(*sample, Decimal(2))
        sample.sort()
        midpoint = len(sample) // 2
        if len(sample) % 2:
            return sample[midpoint]
        return (sample[midpoint - 1] + sample[midpoint]) / two


def relative_return(
    security_return: Decimal,
    benchmark_return: Decimal,
) -> Decimal:
    """Return security return minus benchmark return."""
    security_return = _finite(security_return, "security return")
    benchmark_return = _finite(benchmark_return, "benchmark return")
    with _decimal34_operation("relative_return") as normalize:
        security_return, benchmark_return = normalize(
            security_return,
            benchmark_return,
        )
        return security_return - benchmark_return


def maximum_drawdown(prices: Sequence[Decimal]) -> Decimal:
    """Return the maximum loss from each running adjusted-price peak."""
    sample = _values(prices, "prices")
    with _decimal34_operation("maximum_drawdown") as normalize:
        *sample, one, worst = normalize(*sample, Decimal(1), Decimal(0))
        if any(price <= 0 for price in sample):
            raise AnalysisUnavailable("prices must be positive")
        peak = sample[0]
        for price in sample:
            peak = max(peak, price)
            worst = max(worst, one - price / peak)
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
    with _decimal34_operation("topic_amount_share") as normalize:
        normalized = normalize(amount, *amounts, Decimal(0))
        amount = normalized[0]
        amounts = normalized[1:-1]
        zero = normalized[-1]
        if amount < 0 or any(member_amount < 0 for member_amount in amounts):
            raise AnalysisUnavailable("amounts must be nonnegative")
        denominator = sum(amounts, start=zero)
        _positive(denominator, "topic amount denominator")
        if amount > denominator:
            raise AnalysisUnavailable(
                "amount must not exceed eligible topic amount denominator"
            )
        return amount / denominator


def mean_absolute_deviation_rate(values: Sequence[Decimal]) -> Decimal:
    """Return mean absolute deviation divided by the sample mean."""
    sample = _values(values, "values")
    with _decimal34_operation("mean_absolute_deviation_rate") as normalize:
        *sample, zero, count = normalize(
            *sample,
            Decimal(0),
            Decimal(len(sample)),
        )
        mean = sum(sample, start=zero) / count
        _positive(mean, "mean")
        deviation = (
            sum(
                (abs(value - mean) for value in sample),
                start=zero,
            )
            / count
        )
        return deviation / mean


def knowledge_cutoff(market_as_of: datetime, topic_as_of: datetime) -> datetime:
    """Return the earlier aware snapshot cutoff, normalized to UTC."""
    for value in (market_as_of, topic_as_of):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("knowledge cutoff requires timezone-aware datetimes")
    return min(market_as_of, topic_as_of).astimezone(UTC)
