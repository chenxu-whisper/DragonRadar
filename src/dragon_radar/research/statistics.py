"""Exact Decimal statistics for research validation (no float)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, localcontext

from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.research.models import (
    REASON_INSUFFICIENT_SAMPLE,
    REASON_ZERO_VARIANCE,
    ResearchValidationError,
)

ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)
HALF = Decimal("0.5")
FIVE = Decimal(5)
_UNIT_SNAP = Decimal("1e-30")


@dataclass(frozen=True, slots=True)
class RankCorrelation:
    value: Decimal | None
    reason: str | None


def _decimals(values: object, label: str) -> list[Decimal]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ResearchValidationError(f"{label} must be a non-empty sequence")
    cleaned: list[Decimal] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Decimal):
            raise ResearchValidationError(f"{label} must contain Decimal values")
        if not value.is_finite():
            raise ResearchValidationError(f"{label} must contain finite values")
        cleaned.append(value)
    if not cleaned:
        raise ResearchValidationError(f"{label} must be non-empty")
    return cleaned


def _paired(
    x: Sequence[Decimal], y: Sequence[Decimal]
) -> tuple[list[Decimal], list[Decimal]]:
    xs = _decimals(x, "x")
    ys = _decimals(y, "y")
    if len(xs) != len(ys):
        raise ResearchValidationError("paired sequences must have equal length")
    return xs, ys


def average_ranks(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Ascending 1-based ranks; tied values share their average position."""
    data = _decimals(values, "values")
    order = sorted(range(len(data)), key=data.__getitem__)
    result: list[Decimal] = [ZERO] * len(data)
    start = 0
    with localcontext(_decimal_context()):
        while start < len(order):
            end = start + 1
            while end < len(order) and data[order[end]] == data[order[start]]:
                end += 1
            average = (Decimal(start + 1) + Decimal(end)) / TWO
            for position in range(start, end):
                result[order[position]] = average
            start = end
    return tuple(result)


def _snap_unit(value: Decimal) -> Decimal:
    with localcontext(_decimal_context()):
        gap = abs(value.copy_abs() - ONE)
        if gap <= _UNIT_SNAP:
            return ONE if value > 0 else -ONE
    return value


def _pearson(xs: list[Decimal], ys: list[Decimal]) -> Decimal | None:
    count = Decimal(len(xs))
    with localcontext(_decimal_context()):
        mean_x = sum(xs, ZERO) / count
        mean_y = sum(ys, ZERO) / count
        var_x = ZERO
        var_y = ZERO
        covariance = ZERO
        for x_value, y_value in zip(xs, ys, strict=True):
            dx = x_value - mean_x
            dy = y_value - mean_y
            var_x += dx * dx
            var_y += dy * dy
            covariance += dx * dy
        if var_x == 0 or var_y == 0:
            return None
        value = covariance / (var_x.sqrt() * var_y.sqrt())
    return _snap_unit(value)


def spearman(x: Sequence[Decimal], y: Sequence[Decimal]) -> RankCorrelation:
    xs, ys = _paired(x, y)
    if len(xs) < 2:
        return RankCorrelation(None, REASON_INSUFFICIENT_SAMPLE)
    value = _pearson(list(average_ranks(xs)), list(average_ranks(ys)))
    if value is None:
        return RankCorrelation(None, REASON_ZERO_VARIANCE)
    return RankCorrelation(value, None)


def decimal_mean(values: Sequence[Decimal]) -> Decimal:
    data = _decimals(values, "values")
    with localcontext(_decimal_context()):
        return sum(data, ZERO) / Decimal(len(data))


def decimal_sample_std(values: Sequence[Decimal]) -> Decimal | None:
    data = _decimals(values, "values")
    if len(data) < 2:
        return None
    with localcontext(_decimal_context()):
        mean = sum(data, ZERO) / Decimal(len(data))
        squared = ZERO
        for value in data:
            delta = value - mean
            squared += delta * delta
        return (squared / Decimal(len(data) - 1)).sqrt()


def decimal_median(values: Sequence[Decimal]) -> Decimal:
    data = sorted(_decimals(values, "values"))
    middle = len(data) // 2
    if len(data) % 2:
        return data[middle]
    with localcontext(_decimal_context()):
        return (data[middle - 1] + data[middle]) / TWO


def decimal_quantile(values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Type-7 linear interpolation quantile (median matches even-n pair avg)."""
    data = sorted(_decimals(values, "values"))
    if isinstance(q, bool) or not isinstance(q, Decimal) or not q.is_finite():
        raise ResearchValidationError("q must be a finite Decimal")
    if not ZERO <= q <= ONE:
        raise ResearchValidationError("q must be within [0, 1]")
    with localcontext(_decimal_context()):
        position = q * Decimal(len(data) - 1)
        low = int(position)
        fraction = position - Decimal(low)
        if fraction == 0 or low == len(data) - 1:
            return data[low]
        return data[low] + fraction * (data[low + 1] - data[low])


def decimal_distribution(values: Sequence[Decimal]) -> dict[str, object]:
    data = _decimals(values, "values")
    return {
        "n": len(data),
        "min": min(data),
        "max": max(data),
        "mean": decimal_mean(data),
        "median": decimal_median(data),
        "p25": decimal_quantile(data, Decimal("0.25")),
        "p75": decimal_quantile(data, Decimal("0.75")),
    }


def quintile_buckets(ranks: Sequence[Decimal]) -> tuple[int, ...]:
    """Map 1-based average ranks to quintile index 1..5 (ties share a bucket)."""
    data = _decimals(ranks, "ranks")
    count = Decimal(len(data))
    buckets: list[int] = []
    with localcontext(_decimal_context()):
        for rank in data:
            position = (rank - HALF) / count
            raw = FIVE * position
            bucket = int(raw.to_integral_value(rounding=ROUND_CEILING))
            if not 1 <= bucket <= 5:
                raise ResearchValidationError("quintile bucket outside 1..5")
            buckets.append(bucket)
    return tuple(buckets)
