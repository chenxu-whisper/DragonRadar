"""Walk-forward rolling validation and five-regime conditional weighting.

Pure functions only — no IO, clock or model calls. The fold layout is
``walk-forward-anchored-v1``: one shared train prefix of ``train_sessions``
followed by ``folds`` strictly tiled, non-overlapping out-of-sample blocks of
``test_sessions`` each. The train and every test fold are pairwise disjoint and
the tiled folds cover the declared test region without gaps.

Thresholds/weights fit on the train prefix alone and freeze before any test
session is evaluated: a training observation at decision day *d* uses only the
equal-pool simple return from *d* to *d+1* when both sessions stay inside the
train prefix, so no test-region price can enter a fitted parameter.

Conditioning reuses the EX16 sentiment-research five phases (ice/ebb/divergent/
repair/climax). A phase is visible on decision day *d* only when its
publication carries ``trade_date <= d`` **and** ``publication_sequence`` at or
below that session's market frame sequence — a late revision or a future-dated
publication can never reach a historical fold. The frozen map is the
disclosed de-risk rule

    mean_p  = mean equal-pool next-session return on train decision days
              whose as-of phase is p (six-fractional-digit Decimal)
    weight_p = clamp(1 + mean_p, 0.5, 1.0)

so conditioning can only scale target exposure down (never leverage above the
unweighted target) and never fabricates state: a decision day with no visible
phase publication or a phase never observed in training degrades to unit
weight and records a structured quality reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from dragon_radar.domain.models import Identifier
from dragon_radar.readmodels.sentiment_research import (
    PHASE_CLIMAX,
    PHASE_DIVERGENT,
    PHASE_EBB,
    PHASE_ICE,
    PHASE_REPAIR,
    PHASES,
)
from dragon_radar.research.engine import FactorFrame, _simulate
from dragon_radar.research.execution import Fill, Rejection
from dragon_radar.research.ledger import EquityPoint, Metrics
from dragon_radar.research.models import (
    MIN_WALK_FORWARD_FOLDS,
    BacktestConfig,
    StrictFrozenModel,
)
from dragon_radar.research.pit import (
    PitFrame,
    PitWindow,
    PositiveSequence,
    VisibleDay,
)

RESEARCH_WALK_FORWARD_SCHEMA_VERSION = "dragon-radar.research-walk-forward/v1"

WALK_FORWARD_LAYOUT = "walk-forward-anchored-v1"
REGIME_FIT_METHOD = "equal-pool-mean-next-session-return-v1"

# Conservative de-risk-only band: a regime label can never justify leverage.
REGIME_WEIGHT_FLOOR = Decimal("0.5")
REGIME_WEIGHT_CAP = Decimal("1.0")
REGIME_UNIT_WEIGHT = Decimal("1.000000")
RETURN_QUANTUM = Decimal("0.000001")

PhaseName = Literal[
    PHASE_ICE,
    PHASE_EBB,
    PHASE_DIVERGENT,
    PHASE_REPAIR,
    PHASE_CLIMAX,
]
RegimeFitStatus = Literal["fitted", "unobserved_in_training"]
WalkForwardQualityCode = Literal[
    "regime_phase_missing",
    "regime_unobserved_in_training",
]


class PhasePublication(StrictFrozenModel):
    """One already-materialised EX16 phase publication (PIT input frame)."""

    trade_date: date
    publication_sequence: PositiveSequence
    phase: PhaseName


class RegimeWeightFit(StrictFrozenModel):
    """A frozen per-regime exposure weight and its training evidence."""

    phase: PhaseName
    status: RegimeFitStatus
    observations: Annotated[int, Field(ge=0)]
    mean_next_session_return: Decimal | None
    weight: Decimal

    @model_validator(mode="after")
    def _status_agrees_with_evidence(self) -> Self:
        if self.status == "fitted":
            if self.observations < 1 or self.mean_next_session_return is None:
                raise ValueError("fitted regime weight requires observations")
        else:
            if self.observations != 0 or self.mean_next_session_return is not None:
                raise ValueError("unobserved regime must carry no estimate")
            if self.weight != REGIME_UNIT_WEIGHT:
                raise ValueError("unobserved regime must fall back to unit weight")
        if not REGIME_WEIGHT_FLOOR <= self.weight <= REGIME_WEIGHT_CAP:
            raise ValueError("regime weight must stay within the disclosed band")
        return self


class FrozenRegimeMap(StrictFrozenModel):
    """The train-only fit, frozen before test folds are evaluated."""

    layout: Identifier
    fit_method: Identifier
    train_start: date
    train_end: date
    train_sessions: Annotated[int, Field(ge=1)]
    rebalance_period: Annotated[int, Field(ge=1)]
    missing_phase_decision_sessions: tuple[date, ...]
    weights: Annotated[tuple[RegimeWeightFit, ...], Field(min_length=len(PHASES))]

    @model_validator(mode="after")
    def _weights_cover_every_phase_in_order(self) -> Self:
        if tuple(entry.phase for entry in self.weights) != PHASES:
            raise ValueError("regime weights must cover all five phases in order")
        if len(set(self.missing_phase_decision_sessions)) != len(
            self.missing_phase_decision_sessions
        ):
            raise ValueError("missing phase decision sessions must be unique")
        return self


class WalkForwardQualityEvent(StrictFrozenModel):
    trade_date: date
    code: WalkForwardQualityCode
    phase: PhaseName | None = None

    @model_validator(mode="after")
    def _code_and_phase_agree(self) -> Self:
        if self.code == "regime_phase_missing" and self.phase is not None:
            raise ValueError("a missing-phase event must not name a phase")
        if self.code == "regime_unobserved_in_training" and self.phase is None:
            raise ValueError("an unobserved-regime event must name the phase")
        return self


class FoldResult(StrictFrozenModel):
    """One independent out-of-sample fold evaluated with the frozen map."""

    fold_index: Annotated[int, Field(ge=0)]
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    metrics: Metrics
    equity: tuple[EquityPoint, ...]
    trades: tuple[Fill, ...]
    rejections: tuple[Rejection, ...]
    quality: tuple[WalkForwardQualityEvent, ...]


class WalkForwardReport(StrictFrozenModel):
    schema_version: Literal[RESEARCH_WALK_FORWARD_SCHEMA_VERSION]
    research_only: Literal[True] = True
    layout: Identifier
    folds_count: Annotated[int, Field(ge=MIN_WALK_FORWARD_FOLDS)]
    train_sessions: Annotated[int, Field(ge=1)]
    test_sessions: Annotated[int, Field(ge=1)]
    regime_map: FrozenRegimeMap
    folds: Annotated[tuple[FoldResult, ...], Field(min_length=MIN_WALK_FORWARD_FOLDS)]
    aggregate_equity: tuple[EquityPoint, ...]
    aggregate_metrics: Metrics


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
    )


def validate_phase_publications(
    points: Sequence[PhasePublication],
    *,
    anchor_sequence: int,
    anchor_date: date,
) -> None:
    """Fail closed on duplicates, disorder, or publications beyond the anchor."""
    previous: tuple[date, int] | None = None
    seen: set[tuple[date, int]] = set()
    for point in points:
        key = (point.trade_date, point.publication_sequence)
        if key in seen:
            raise ValueError(f"duplicate phase publication for {key[0]}")
        if previous is not None and key < previous:
            raise ValueError(
                "phase publications must be in ascending "
                "(trade_date, publication_sequence) order"
            )
        if point.publication_sequence > anchor_sequence:
            raise ValueError(
                f"phase publication sequence {point.publication_sequence} lies "
                f"outside the anchored publication {anchor_sequence}"
            )
        if point.trade_date > anchor_date:
            raise ValueError(
                f"phase publication trade_date {point.trade_date} lies after "
                f"the anchor trade_date {anchor_date}"
            )
        seen.add(key)
        previous = key


def phase_as_of(
    points: Sequence[PhasePublication],
    day: date,
    day_sequence: int,
) -> str | None:
    """Latest phase visible at day's publication cutoff; None when absent."""
    candidate: PhasePublication | None = None
    for point in points:
        if point.trade_date > day or point.publication_sequence > day_sequence:
            continue
        if candidate is None or (point.trade_date, point.publication_sequence) > (
            candidate.trade_date,
            candidate.publication_sequence,
        ):
            candidate = point
    return candidate.phase if candidate is not None else None


def _equal_pool_day_return(decision: VisibleDay, following: VisibleDay) -> Decimal:
    """Equal-weight mean simple return of securities visible on both days."""
    next_close = {
        security.security_id: security.close for security in following.securities
    }
    common = tuple(
        security
        for security in decision.securities
        if security.security_id in next_close
    )
    if not common:
        raise ValueError(
            "no security stays visible across the training decision sessions "
            f"{decision.trade_date} -> {following.trade_date}"
        )
    with localcontext(_decimal_context()):
        total = Decimal(0)
        for security in common:
            total += next_close[security.security_id] / security.close - Decimal(1)
        return (total / Decimal(len(common))).quantize(
            RETURN_QUANTUM, rounding=ROUND_HALF_EVEN
        )


def _clamp_weight(mean_return: Decimal) -> Decimal:
    raw = Decimal(1) + mean_return
    clamped = min(max(raw, REGIME_WEIGHT_FLOOR), REGIME_WEIGHT_CAP)
    return clamped.quantize(RETURN_QUANTUM, rounding=ROUND_HALF_EVEN)


def fit_regime_map(
    days: Sequence[VisibleDay],
    phase_points: Sequence[PhasePublication],
    *,
    train_sessions: int,
    rebalance_period: int,
) -> FrozenRegimeMap:
    """Fit de-risk weights on train-prefix decision days only."""
    train = days[:train_sessions]
    returns_by_phase: dict[str, list[Decimal]] = {phase: [] for phase in PHASES}
    missing_sessions: list[date] = []

    # The terminal train session cannot contribute: its next session is
    # already inside the test region and must never enter a fitted parameter.
    decision_indices = tuple(
        index for index in range(train_sessions - 1) if index % rebalance_period == 0
    )
    for index in decision_indices:
        decision = train[index]
        phase = phase_as_of(
            phase_points,
            decision.trade_date,
            decision.publication_sequence,
        )
        if phase is None:
            missing_sessions.append(decision.trade_date)
            continue
        returns_by_phase[phase].append(
            _equal_pool_day_return(decision, train[index + 1])
        )

    weights: list[RegimeWeightFit] = []
    for phase in PHASES:
        observations = tuple(returns_by_phase[phase])
        if observations:
            with localcontext(_decimal_context()):
                mean_return = (
                    sum(observations, Decimal(0)) / Decimal(len(observations))
                ).quantize(RETURN_QUANTUM, rounding=ROUND_HALF_EVEN)
            weights.append(
                RegimeWeightFit(
                    phase=phase,  # type: ignore[arg-type]
                    status="fitted",
                    observations=len(observations),
                    mean_next_session_return=mean_return,
                    weight=_clamp_weight(mean_return),
                )
            )
        else:
            weights.append(
                RegimeWeightFit(
                    phase=phase,  # type: ignore[arg-type]
                    status="unobserved_in_training",
                    observations=0,
                    mean_next_session_return=None,
                    weight=REGIME_UNIT_WEIGHT,
                )
            )

    return FrozenRegimeMap(
        layout=WALK_FORWARD_LAYOUT,
        fit_method=REGIME_FIT_METHOD,
        train_start=train[0].trade_date,
        train_end=train[-1].trade_date,
        train_sessions=train_sessions,
        rebalance_period=rebalance_period,
        missing_phase_decision_sessions=tuple(missing_sessions),
        weights=tuple(weights),
    )


def _weights_for_block(
    days: Sequence[VisibleDay],
    phase_points: Sequence[PhasePublication],
    regime_map: FrozenRegimeMap,
    *,
    rebalance_period: int,
) -> tuple[dict[date, Decimal], list[WalkForwardQualityEvent]]:
    """Resolve frozen weights for one simulation block; degrade honestly."""
    fit_by_phase = {entry.phase: entry for entry in regime_map.weights}
    weights: dict[date, Decimal] = {}
    events: list[WalkForwardQualityEvent] = []
    # The block's terminal session never raises orders, mirroring the engine.
    for index, day in enumerate(days[:-1]):
        if index % rebalance_period != 0:
            continue
        phase = phase_as_of(phase_points, day.trade_date, day.publication_sequence)
        if phase is None:
            weights[day.trade_date] = REGIME_UNIT_WEIGHT
            events.append(
                WalkForwardQualityEvent(
                    trade_date=day.trade_date,
                    code="regime_phase_missing",
                )
            )
            continue
        fit = fit_by_phase[phase]
        if fit.status == "unobserved_in_training":
            weights[day.trade_date] = REGIME_UNIT_WEIGHT
            events.append(
                WalkForwardQualityEvent(
                    trade_date=day.trade_date,
                    code="regime_unobserved_in_training",
                    phase=phase,  # type: ignore[arg-type]
                )
            )
        else:
            weights[day.trade_date] = fit.weight
    return weights, events


def run_walk_forward(
    config: BacktestConfig,
    window: PitWindow,
    phase_points: Sequence[PhasePublication],
    *,
    frames_by_date: Mapping[date, PitFrame],
    factor_by_date: Mapping[date, FactorFrame],
    benchmark_closes: Mapping[date, Decimal] | None = None,
) -> WalkForwardReport:
    """Fit once on the train prefix, then evaluate every tiled OOS fold."""
    spec = config.walk_forward
    if spec is None:
        raise ValueError("walk_forward spec is required")

    days = window.days
    train_sessions = spec.train_sessions
    test_sessions = spec.test_sessions
    folds_count = spec.folds
    required = train_sessions + folds_count * test_sessions
    if len(days) < required:
        raise ValueError(
            f"walk-forward requires {required} sessions but the PIT window "
            f"provides {len(days)}"
        )

    validate_phase_publications(
        phase_points,
        anchor_sequence=window.anchor_sequence,
        anchor_date=days[-1].trade_date,
    )

    regime_map = fit_regime_map(
        days,
        phase_points,
        train_sessions=train_sessions,
        rebalance_period=config.parameters.rebalance_period,
    )

    train_days = days[:train_sessions]
    fold_results: list[FoldResult] = []
    for fold_index in range(folds_count):
        start = train_sessions + fold_index * test_sessions
        block = days[start : start + test_sessions]
        weights, events = _weights_for_block(
            block,
            phase_points,
            regime_map,
            rebalance_period=config.parameters.rebalance_period,
        )
        block_window = PitWindow(
            anchor_sequence=window.anchor_sequence,
            days=block,
        )
        artifacts = _simulate(
            config,
            block_window,
            frames_by_date,
            factor_by_date,
            config.cost_model,
            benchmark_closes,
            regime_weights=weights,
        )
        fold_results.append(
            FoldResult(
                fold_index=fold_index,
                train_start=train_days[0].trade_date,
                train_end=train_days[-1].trade_date,
                test_start=block[0].trade_date,
                test_end=block[-1].trade_date,
                metrics=artifacts.metrics,
                equity=artifacts.equity,
                trades=artifacts.trades,
                rejections=artifacts.rejections,
                quality=tuple(events),
            )
        )

    region = days[train_sessions:required]
    region_weights, _ = _weights_for_block(
        region,
        phase_points,
        regime_map,
        rebalance_period=config.parameters.rebalance_period,
    )
    aggregate = _simulate(
        config,
        PitWindow(anchor_sequence=window.anchor_sequence, days=region),
        frames_by_date,
        factor_by_date,
        config.cost_model,
        benchmark_closes,
        regime_weights=region_weights,
    )

    return WalkForwardReport(
        schema_version=RESEARCH_WALK_FORWARD_SCHEMA_VERSION,
        layout=WALK_FORWARD_LAYOUT,
        folds_count=folds_count,
        train_sessions=train_sessions,
        test_sessions=test_sessions,
        regime_map=regime_map,
        folds=tuple(fold_results),
        aggregate_equity=aggregate.equity,
        aggregate_metrics=aggregate.metrics,
    )


__all__ = [
    "FoldResult",
    "FrozenRegimeMap",
    "PhasePublication",
    "REGIME_FIT_METHOD",
    "REGIME_UNIT_WEIGHT",
    "REGIME_WEIGHT_CAP",
    "REGIME_WEIGHT_FLOOR",
    "RESEARCH_WALK_FORWARD_SCHEMA_VERSION",
    "RegimeWeightFit",
    "WALK_FORWARD_LAYOUT",
    "WalkForwardQualityEvent",
    "WalkForwardReport",
    "fit_regime_map",
    "phase_as_of",
    "run_walk_forward",
    "validate_phase_publications",
]
