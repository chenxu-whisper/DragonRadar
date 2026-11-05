"""Factor health checks (Task 7).

Pure Decimal functions: sample size, FAC coverage partition
(expected/valid/excluded/missing), distribution quantiles and Spearman rank
correlation matrices, with explicit data-window and source versions.

Everything is computed from the same anchored PIT window the backtest uses:
partition counts are scoped to each day's visible pool, observations never
fabricate missing factor values, and statistical estimates are withheld
(never invented) when a factor has too few valid observations. No IO, clock
or randomness is touched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal, Self

from pydantic import model_validator

from dragon_radar.domain.models import Identifier
from dragon_radar.research.engine import FactorFrame, FactorPartition
from dragon_radar.research.models import (
    MAX_PERIOD_SESSIONS,
    RESEARCH_HEALTH_SCHEMA_VERSION,
    BacktestConfig,
    InputManifests,
    ResearchFactorId,
    StrictFrozenModel,
)
from dragon_radar.research.pit import (
    PitAnchor,
    PitFrame,
    PitWindow,
    UniversePolicy,
    VisibleDay,
    assemble_pit_window,
)

# Conservative minimum pooled valid observations before distribution or
# correlation statistics are reported. Bounded to the session cap because the
# threshold itself is a disclosed statistical assumption, never a free knob.
DEFAULT_HEALTH_MIN_SAMPLE_SIZE = 5
DEFAULT_HEALTH_QUANTILES = (
    Decimal("0"),
    Decimal("0.25"),
    Decimal("0.5"),
    Decimal("0.75"),
    Decimal("1"),
)

RATIO_QUANTUM = Decimal("0.000001")
ZERO_RATIO = Decimal("0.000000")

HealthStatus = Literal["ok", "insufficient_sample"]
HealthReason = Literal[
    "insufficient_sample_size",
    "insufficient_paired_sample",
    "no_eligible_sessions",
]


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )


def _ratio(numerator: int, denominator: int) -> Decimal:
    """Closed ratio: a zero denominator reports zero instead of raising."""
    if denominator == 0:
        return ZERO_RATIO
    with localcontext(_decimal_context()):
        return Decimal(numerator) / Decimal(denominator)


class HealthQuantiles(StrictFrozenModel):
    """Requested fraction points and their linearly interpolated values."""

    points: tuple[Decimal, ...]
    values: tuple[Decimal, ...]

    @model_validator(mode="after")
    def _points_valid_and_aligned(self) -> Self:
        if not self.points:
            raise ValueError("quantile points must not be empty")
        if len(self.points) != len(self.values):
            raise ValueError("quantile points and values must align")
        previous: Decimal | None = None
        for point in self.points:
            if point < 0 or point > 1:
                raise ValueError("quantile points must lie within [0, 1]")
            if previous is not None and point <= previous:
                raise ValueError("quantile points must be strictly ascending")
            previous = point
        return self


class FactorHealth(StrictFrozenModel):
    """One factor's window-level check; exact counts survive even when the
    statistical sample is insufficient, estimates do not."""

    factor_id: ResearchFactorId
    metric: Identifier
    window: Identifier
    unit: Identifier
    sessions: int
    coverage: FactorPartition
    sample_size: int
    valid_rate: Decimal
    excluded_rate: Decimal
    missing_rate: Decimal
    quantiles: HealthQuantiles | None
    status: HealthStatus
    reasons: tuple[HealthReason, ...]

    @model_validator(mode="after")
    def _status_coherent(self) -> Self:
        if self.sample_size != self.coverage.valid:
            raise ValueError("sample_size must equal coverage.valid")
        if self.status == "ok":
            if self.quantiles is None or self.reasons:
                raise ValueError("ok health requires quantiles and no reasons")
        elif self.quantiles is not None or not self.reasons:
            raise ValueError("insufficient health withholds quantiles and reasons")
        return self


class FactorRankCorrelation(StrictFrozenModel):
    left: ResearchFactorId
    right: ResearchFactorId
    paired_sample_size: int
    eligible_sessions: int
    value: Decimal | None
    status: HealthStatus
    reasons: tuple[HealthReason, ...]

    @model_validator(mode="after")
    def _status_coherent(self) -> Self:
        if self.status == "ok":
            if self.value is None or self.reasons or self.eligible_sessions == 0:
                raise ValueError("ok correlation needs a value and eligible sessions")
        elif self.value is not None or not self.reasons:
            raise ValueError("insufficient correlation withholds its value")
        return self


class FactorHealthReport(StrictFrozenModel):
    schema_version: Literal["dragon-radar.research-health/v1"]
    research_only: Literal[True] = True
    anchor_sequence: int
    window_start: date
    window_end: date
    sessions: int
    primary_factor_id: ResearchFactorId
    manifests: InputManifests
    factors: tuple[FactorHealth, ...]
    rank_correlations: tuple[FactorRankCorrelation, ...]

    @model_validator(mode="after")
    def _factors_cover_primary(self) -> Self:
        if not self.factors:
            raise ValueError("health report requires at least one factor")
        ids = {factor.factor_id for factor in self.factors}
        if self.primary_factor_id not in ids:
            raise ValueError("health report must cover the configured primary factor")
        return self


def _average_ranks(values: Sequence[Decimal]) -> list[Decimal]:
    """Competition-style average ranks (1-based); ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks: list[Decimal | None] = [None] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end + 1)) / Decimal(2)
        for position in range(index, end + 1):
            ranks[order[position]] = average_rank
        index = end + 1
    return [rank for rank in ranks if rank is not None]


def _pearson(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> Decimal | None:
    """Pearson correlation in fixed-point Decimal; zero variance closes None."""
    count = Decimal(len(xs))
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x <= 0 or variance_y <= 0:
        return None
    with localcontext(_decimal_context()) as context:
        return covariance / context.sqrt(variance_x * variance_y)


def _linear_quantiles(
    values: Sequence[Decimal], points: Sequence[Decimal]
) -> tuple[Decimal, ...]:
    """Type-7 linear interpolation (h = (n-1)*p), the median convention."""
    ordered = sorted(values)
    result: list[Decimal] = []
    last_index = len(ordered) - 1
    for point in points:
        position = point * last_index
        lower = int(position.to_integral_value(rounding=ROUND_FLOOR))
        fraction = position - lower
        if lower + 1 <= last_index and fraction != 0:
            value = ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])
        else:
            value = ordered[lower]
        result.append(value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN))
    return tuple(result)


def _valid_points(
    day: VisibleDay, frame: FactorFrame
) -> tuple[dict[str, Decimal], int, int]:
    """Partition one session scoped to the visible pool; never impute."""
    points = {point.security_id: point for point in frame.points}
    valid: dict[str, Decimal] = {}
    excluded = 0
    missing = 0
    for security in day.securities:
        point = points.get(security.security_id)
        if point is None or point.value is None:
            missing += 1
        elif isinstance(point.value, bool):
            excluded += 1
        else:
            valid[security.security_id] = point.value
    return valid, excluded, missing


def _assess_factor(
    factor_id: ResearchFactorId,
    frames: Sequence[FactorFrame],
    days: Sequence[VisibleDay],
    min_sample_size: int,
    quantile_points: Sequence[Decimal],
) -> FactorHealth:
    expected = valid_total = excluded_total = missing_total = 0
    pooled: list[Decimal] = []
    for day, frame in zip(days, frames, strict=True):
        valid, excluded, missing = _valid_points(day, frame)
        expected += len(day.securities)
        valid_total += len(valid)
        excluded_total += excluded
        missing_total += missing
        pooled.extend(
            valid[security.security_id]
            for security in day.securities
            if security.security_id in valid
        )

    coverage = FactorPartition(
        expected=expected,
        valid=valid_total,
        excluded=excluded_total,
        missing=missing_total,
    )
    reasons: tuple[HealthReason, ...] = ()
    quantiles: HealthQuantiles | None = None
    if valid_total < min_sample_size:
        status: HealthStatus = "insufficient_sample"
        reasons = ("insufficient_sample_size",)
    else:
        status = "ok"
        quantiles = HealthQuantiles(
            points=tuple(quantile_points),
            values=_linear_quantiles(pooled, quantile_points),
        )
    first = frames[0]
    return FactorHealth(
        factor_id=factor_id,
        metric=first.metric,
        window=first.window,
        unit=first.unit,
        sessions=len(frames),
        coverage=coverage,
        sample_size=valid_total,
        valid_rate=_ratio(valid_total, expected).quantize(
            RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
        ),
        excluded_rate=_ratio(excluded_total, expected).quantize(
            RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
        ),
        missing_rate=_ratio(missing_total, expected).quantize(
            RATIO_QUANTUM, rounding=ROUND_HALF_EVEN
        ),
        quantiles=quantiles,
        status=status,
        reasons=reasons,
    )


def _assess_correlation(
    left_id: ResearchFactorId,
    right_id: ResearchFactorId,
    left_frames: Sequence[FactorFrame],
    right_frames: Sequence[FactorFrame],
    days: Sequence[VisibleDay],
    min_sample_size: int,
) -> FactorRankCorrelation:
    """Per-session cross-sectional Spearman, equal-weighted across sessions."""
    paired_sample_size = 0
    session_rhos: list[Decimal] = []
    for day, left_frame, right_frame in zip(
        days, left_frames, right_frames, strict=True
    ):
        left_valid, _, _ = _valid_points(day, left_frame)
        right_valid, _, _ = _valid_points(day, right_frame)
        common = sorted(set(left_valid) & set(right_valid))
        if len(common) < 2:
            # A single pair cannot expose a cross-sectional rank relationship.
            continue
        paired_sample_size += len(common)
        left_ranks = _average_ranks([left_valid[sid] for sid in common])
        right_ranks = _average_ranks([right_valid[sid] for sid in common])
        rho = _pearson(left_ranks, right_ranks)
        if rho is not None:
            session_rhos.append(rho)

    reasons: list[HealthReason] = []
    if paired_sample_size < min_sample_size:
        reasons.append("insufficient_paired_sample")
    if not session_rhos:
        reasons.append("no_eligible_sessions")
    if reasons:
        return FactorRankCorrelation(
            left=left_id,
            right=right_id,
            paired_sample_size=paired_sample_size,
            eligible_sessions=0,
            value=None,
            status="insufficient_sample",
            reasons=tuple(reasons),
        )
    with localcontext(_decimal_context()):
        aggregate = sum(session_rhos) / Decimal(len(session_rhos))
    return FactorRankCorrelation(
        left=left_id,
        right=right_id,
        paired_sample_size=paired_sample_size,
        eligible_sessions=len(session_rhos),
        value=aggregate.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN),
        status="ok",
        reasons=(),
    )


def _validate_stream(
    factor_id: ResearchFactorId,
    frames: Sequence[FactorFrame],
    days: Sequence[VisibleDay],
) -> None:
    if len(frames) != len(days):
        raise ValueError("factor frames must align one-to-one with PIT window sessions")
    metric = frames[0].metric
    window = frames[0].window
    unit = frames[0].unit
    for frame, day in zip(frames, days, strict=True):
        if frame.trade_date != day.trade_date:
            raise ValueError(
                f"factor frame trade_date {frame.trade_date} does not align "
                f"with PIT session {day.trade_date}"
            )
        if frame.factor_id != factor_id:
            raise ValueError(
                f"factor_id mismatch: frame declares {frame.factor_id} but "
                f"stream is keyed {factor_id}"
            )
        if frame.metric != metric:
            raise ValueError("factor metric must stay constant across the window")
        if frame.window != window:
            raise ValueError("factor window must stay constant across the window")
        if frame.unit != unit:
            raise ValueError("factor unit must stay constant across the window")


def _validate_quantile_points(points: Sequence[Decimal]) -> None:
    if not points:
        raise ValueError("quantile points must not be empty")
    previous: Decimal | None = None
    for point in points:
        if point < 0 or point > 1:
            raise ValueError("quantile points must lie within [0, 1]")
        if previous is not None and point <= previous:
            raise ValueError("quantile points must be strictly ascending")
        previous = point


def assess_factor_health(
    config: BacktestConfig,
    frames: Sequence[PitFrame],
    factor_frames: Mapping[ResearchFactorId, Sequence[FactorFrame]],
    *,
    anchor: PitAnchor,
    policy: UniversePolicy | None = None,
    min_sample_size: int = DEFAULT_HEALTH_MIN_SAMPLE_SIZE,
    quantile_points: Sequence[Decimal] = DEFAULT_HEALTH_QUANTILES,
) -> FactorHealthReport:
    """Assess every supplied factor over the anchored PIT window.

    The config's pinned factor must be present (it is the strategy's primary
    factor); additional factor streams are accepted for the rank matrix. All
    streams must align one-to-one with the same window.
    """
    if not 2 <= min_sample_size <= MAX_PERIOD_SESSIONS:
        raise ValueError(f"min_sample_size must lie within [2, {MAX_PERIOD_SESSIONS}]")
    _validate_quantile_points(quantile_points)
    window: PitWindow = assemble_pit_window(frames, anchor=anchor, policy=policy)
    days = window.days

    if not factor_frames:
        raise ValueError("factor health requires at least one factor stream")
    primary = config.parameters.factor_id
    if primary not in factor_frames:
        raise ValueError(f"primary factor_id {primary} is missing from health streams")
    factor_ids = sorted(factor_frames)
    streams: dict[ResearchFactorId, Sequence[FactorFrame]] = {
        factor_id: tuple(factor_frames[factor_id]) for factor_id in factor_ids
    }
    for factor_id in factor_ids:
        _validate_stream(factor_id, streams[factor_id], days)

    factor_results = tuple(
        _assess_factor(
            factor_id,
            streams[factor_id],
            days,
            min_sample_size,
            tuple(quantile_points),
        )
        for factor_id in factor_ids
    )
    correlations = tuple(
        _assess_correlation(
            factor_ids[left_index],
            factor_ids[right_index],
            streams[factor_ids[left_index]],
            streams[factor_ids[right_index]],
            days,
            min_sample_size,
        )
        for left_index in range(len(factor_ids))
        for right_index in range(left_index + 1, len(factor_ids))
    )

    return FactorHealthReport(
        schema_version=RESEARCH_HEALTH_SCHEMA_VERSION,
        anchor_sequence=window.anchor_sequence,
        window_start=days[0].trade_date,
        window_end=days[-1].trade_date,
        sessions=len(days),
        primary_factor_id=primary,
        manifests=config.input_manifests,
        factors=factor_results,
        rank_correlations=correlations,
    )


__all__ = [
    "DEFAULT_HEALTH_MIN_SAMPLE_SIZE",
    "DEFAULT_HEALTH_QUANTILES",
    "FactorHealth",
    "FactorHealthReport",
    "FactorRankCorrelation",
    "HealthQuantiles",
    "assess_factor_health",
]
