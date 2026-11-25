"""Backtest engine and pluggable strategy registry (Task 6).

``run_backtest`` wires PIT inputs, T-day signals, T+1 fills, the ledger and
cost-sensitivity legs into the frozen ``dragon-radar.research-backtest/v1``
result schema. Pure and deterministic: no clock, network or randomness.

The only registered strategy, ``factor-cross-sectional-topn-v1``, ranks the
day's visible pool by one already-materialised FAC research factor, picks the
Top-N names and targets equal weight at fixed-period rebalances. Sizing uses
signal-day raw closes with a disclosed cost-aware board-lot rule; execution
happens exclusively at the next tradable session through ``simulate_fill``.
Factor values are never fabricated: missing or non-numeric observations fall
out of the ranking into a strict coverage partition and a quality record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from dragon_radar.domain.models import Identifier
from dragon_radar.domain.quality import bar_issues, state_at
from dragon_radar.research.execution import (
    Fill,
    Order,
    Rejection,
    simulate_fill,
)
from dragon_radar.research.ledger import (
    EquityPoint,
    LedgerState,
    Metrics,
    OrderOutcome,
    apply_fill,
    cost_sensitivity_models,
    mark_equity,
    open_ledger,
    roll_overnight,
    summarize_run,
)
from dragon_radar.research.models import (
    COST_SENSITIVITY_MULTIPLIERS,
    COST_SENSITIVITY_TIERS,
    RESEARCH_BACKTEST_SCHEMA_VERSION,
    STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1,
    BacktestConfig,
    CostModel,
    CostTier,
    InputManifests,
    ResearchFactorId,
    StrictFrozenModel,
)
from dragon_radar.research.pit import (
    DegradationCode,
    PitAnchor,
    PitFrame,
    PitWindow,
    UniversePolicy,
    VisibleDay,
    assemble_pit_window,
)

# Disclosed, fixed simulation assumptions. The EX14 contract carries no
# initial-cash field, so every research run starts from one disclosed virtual
# principal; sizing rounds to A-share board lots.
SIMULATED_INITIAL_CASH = Decimal("100000.00")
LOT_SIZE = 100

MONEY_QUANTUM = Decimal("0.01")

SIZING_METHOD = "equal-weight-cost-aware-board-lot-v1"
EXECUTION_METHOD = "next-tradable-session-raw-close-v1"
LEDGER_METHOD = "decimal-t1-settlement-v1"
VALUATION_METHOD = "session-close-last-visible-carry-v1"
COST_METHOD = "explicit-fixed-rate-three-tier-v1"

# Cooperative cancellation/budget seam. The kernel stays pure: the default is
# a no-op and the callable itself performs no IO; the IO layer (worker) passes
# a closure that probes the lease and wall-clock budget at these checkpoints.
CheckpointFn = Callable[[], None]


def _noop_checkpoint() -> None:
    return None


FactorInputValue = Decimal | bool | None

QualityCode = Literal[
    "factor_value_missing",
    "factor_value_excluded",
    "factor_section_below_top_n",
    "factor_section_empty",
    "mark_price_carried",
    "decision_without_next_session",
]


class FactorPoint(StrictFrozenModel):
    """One materialised factor observation for one security."""

    security_id: Identifier
    value: FactorInputValue = None
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _value_and_reasons_agree(self) -> Self:
        if self.value is None and not self.reasons:
            raise ValueError("missing factor observations require reasons")
        if self.value is not None and self.reasons:
            raise ValueError("numeric factor observations must not carry reasons")
        return self


class FactorFrame(StrictFrozenModel):
    """Materialised cross-section for one decision session."""

    trade_date: date
    factor_id: ResearchFactorId
    metric: Identifier
    window: Identifier
    unit: Identifier
    points: Annotated[tuple[FactorPoint, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _points_unique_per_security(self) -> Self:
        seen: set[str] = set()
        for point in self.points:
            if point.security_id in seen:
                raise ValueError(
                    f"duplicate factor point for {point.security_id} on "
                    f"{self.trade_date}"
                )
            seen.add(point.security_id)
        return self


class FactorPartition(StrictFrozenModel):
    """expected = valid + excluded + missing, scoped to the visible pool."""

    expected: Annotated[int, Field(ge=0)]
    valid: Annotated[int, Field(ge=0)]
    excluded: Annotated[int, Field(ge=0)]
    missing: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _complete_partition(self) -> Self:
        if self.expected != self.valid + self.excluded + self.missing:
            raise ValueError("factor coverage must be a complete partition")
        return self


class RankedSecurity(StrictFrozenModel):
    security_id: Identifier
    factor_value: Decimal
    rank: Annotated[int, Field(ge=1)]


class StrategySignal(StrictFrozenModel):
    trade_date: date
    selected: tuple[RankedSecurity, ...]
    coverage: FactorPartition
    missing_ids: tuple[Identifier, ...]
    excluded_ids: tuple[Identifier, ...]


class QualityEvent(StrictFrozenModel):
    trade_date: date
    code: QualityCode | DegradationCode
    security_id: Identifier | None = None


class DayQuality(StrictFrozenModel):
    trade_date: date
    factor_coverage: FactorPartition | None = None
    events: tuple[QualityEvent, ...] = ()


class RunVersions(StrictFrozenModel):
    strategy_id: Identifier
    strategy_method: Identifier
    factor_id: ResearchFactorId
    factor_metric: Identifier
    factor_window: Identifier
    factor_unit: Identifier
    sizing_method: Identifier
    execution_method: Identifier
    ledger_method: Identifier
    valuation_method: Identifier
    cost_method: Identifier
    cost_sensitivity_multipliers: Mapping[CostTier, Decimal]
    adjustment: Identifier
    calendar: Identifier
    pool: Identifier


class CostSensitivityResult(StrictFrozenModel):
    tier: CostTier
    cost_multiplier: Decimal
    metrics: Metrics


class BaselineLeg(StrictFrozenModel):
    status: Literal["completed"] = "completed"
    metrics: Metrics
    cost_sensitivity: tuple[CostSensitivityResult, ...]


class BacktestResult(StrictFrozenModel):
    schema_version: Literal[RESEARCH_BACKTEST_SCHEMA_VERSION]
    research_only: Literal[True] = True
    config_hash: str
    snapshot_id: UUID
    manifests: InputManifests
    versions: RunVersions
    metrics: Metrics
    equity: tuple[EquityPoint, ...]
    trades: tuple[Fill, ...]
    rejections: tuple[Rejection, ...]
    quality: tuple[DayQuality, ...]
    folds: tuple[object, ...] | None = None
    walk_forward: object | None = None
    baseline: BaselineLeg
    ablation: object | None = None


StrategyFn = Callable[[VisibleDay, FactorFrame, BacktestConfig], StrategySignal]


def _factor_cross_sectional_topn(
    day: VisibleDay, frame: FactorFrame, config: BacktestConfig
) -> StrategySignal:
    """Rank numeric observations; never impute a missing factor value."""
    points = {point.security_id: point for point in frame.points}
    valid: list[tuple[Decimal, str]] = []
    missing_ids: list[str] = []
    excluded_ids: list[str] = []
    for security in day.securities:
        point = points.get(security.security_id)
        if point is None or point.value is None:
            missing_ids.append(security.security_id)
        elif isinstance(point.value, bool):
            excluded_ids.append(security.security_id)
        else:
            valid.append((point.value, security.security_id))

    valid.sort(key=lambda item: (-item[0], item[1]))
    chosen = valid[: config.parameters.top_n]
    coverage = FactorPartition(
        expected=len(day.securities),
        valid=len(valid),
        excluded=len(excluded_ids),
        missing=len(missing_ids),
    )
    return StrategySignal(
        trade_date=day.trade_date,
        selected=tuple(
            RankedSecurity(security_id=security_id, factor_value=value, rank=rank)
            for rank, (value, security_id) in enumerate(chosen, start=1)
        ),
        coverage=coverage,
        missing_ids=tuple(missing_ids),
        excluded_ids=tuple(excluded_ids),
    )


_STRATEGY_REGISTRY: Mapping[str, StrategyFn] = {
    STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1: _factor_cross_sectional_topn,
}


def get_strategy(strategy_id: str) -> StrategyFn:
    """Return the registered strategy or fail closed on an unknown id."""
    strategy = _STRATEGY_REGISTRY.get(strategy_id)
    if strategy is None:
        raise ValueError(f"unknown strategy_id: {strategy_id}")
    return strategy


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)


def _validate_factor_frames(
    config: BacktestConfig,
    days: Sequence[VisibleDay],
    factor_frames: Sequence[FactorFrame],
) -> None:
    if len(factor_frames) != len(days):
        raise ValueError("factor frames must align one-to-one with PIT window sessions")
    declared = config.parameters.factor_id
    metric = factor_frames[0].metric
    window = factor_frames[0].window
    unit = factor_frames[0].unit
    for frame, day in zip(factor_frames, days, strict=True):
        if frame.trade_date != day.trade_date:
            raise ValueError(
                f"factor frame trade_date {frame.trade_date} does not align "
                f"with PIT session {day.trade_date}"
            )
        if frame.factor_id != declared:
            raise ValueError(
                f"factor_id mismatch: frame declares {frame.factor_id} but "
                f"config pins {declared}"
            )
        if frame.metric != metric:
            raise ValueError("factor metric must stay constant across the window")
        if frame.window != window:
            raise ValueError("factor window must stay constant across the window")
        if frame.unit != unit:
            raise ValueError("factor unit must stay constant across the window")


def _target_quantities(
    signal: StrategySignal,
    ledger: LedgerState,
    signal_close: Mapping[str, Decimal],
    marks: Mapping[str, Decimal],
    config: BacktestConfig,
    regime_weight: Decimal = Decimal("1"),
) -> dict[str, int]:
    """Equal-weight, cost-aware board-lot targets sized at signal close."""
    selected = tuple(item.security_id for item in signal.selected)
    if not selected:
        return {}
    equity = mark_equity(ledger, marks)
    budget = ((equity / Decimal(len(selected))) * regime_weight).quantize(
        MONEY_QUANTUM, rounding=ROUND_FLOOR
    )
    slippage = config.cost_model.slippage_rate
    commission_rate = config.cost_model.commission_rate
    targets: dict[str, int] = {}
    for security_id in selected:
        close = signal_close[security_id]
        estimated_cost_per_share = (
            close * (Decimal("1") + slippage) * (Decimal("1") + commission_rate)
        )
        lots = int(
            (budget / estimated_cost_per_share / Decimal(LOT_SIZE)).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        targets[security_id] = max(lots, 0) * LOT_SIZE
    return targets


def _plan_orders(
    signal: StrategySignal,
    ledger: LedgerState,
    signal_close: Mapping[str, Decimal],
    marks: Mapping[str, Decimal],
    config: BacktestConfig,
    regime_weight: Decimal = Decimal("1"),
) -> list[Order]:
    """Sell exits/rebalances first (sorted) so proceeds can fund buys."""
    targets = _target_quantities(
        signal, ledger, signal_close, marks, config, regime_weight
    )
    signal_day = signal.trade_date
    orders: list[Order] = []

    for security_id in sorted(ledger.holdings):
        holding = ledger.holdings[security_id]
        target = targets.get(security_id, 0)
        sell_quantity = (
            holding.available_quantity
            if target == 0
            else holding.available_quantity - Decimal(target)
        )
        if sell_quantity > 0:
            orders.append(
                Order(
                    security_id=security_id,
                    side="sell",
                    quantity=int(sell_quantity),
                    signal_date=signal_day,
                )
            )

    for security_id in tuple(item.security_id for item in signal.selected):
        held = ledger.holdings.get(security_id)
        current = held.quantity if held is not None else Decimal("0")
        buy_quantity = Decimal(targets[security_id]) - current
        if buy_quantity > 0:
            orders.append(
                Order(
                    security_id=security_id,
                    side="buy",
                    quantity=int(buy_quantity),
                    signal_date=signal_day,
                )
            )
    return orders


def _direct_rejection(order: Order, trade_date: date, reason: str) -> Rejection:
    return Rejection(
        security_id=order.security_id,
        side=order.side,
        quantity=order.quantity,
        signal_date=order.signal_date,
        trade_date=trade_date,
        reason=reason,  # type: ignore[arg-type]
    )


def _session_marks(
    day: VisibleDay,
    held_ids: Sequence[str],
    last_visible_close: dict[str, Decimal],
) -> tuple[dict[str, Decimal], list[QualityEvent]]:
    """Value holdings at the session close; carry the last visible mark."""
    marks: dict[str, Decimal] = {}
    events: list[QualityEvent] = []
    for security_id in held_ids:
        price = last_visible_close.get(security_id)
        if price is None:
            raise ValueError(
                f"no visible mark price exists for holding {security_id} on "
                f"{day.trade_date}"
            )
        visible = next(
            (
                security.close
                for security in day.securities
                if security.security_id == security_id
            ),
            None,
        )
        if visible is None:
            events.append(
                QualityEvent(
                    trade_date=day.trade_date,
                    code="mark_price_carried",
                    security_id=security_id,
                )
            )
        marks[security_id] = visible if visible is not None else price
    return marks, events


def _quality_for_decision_day(
    day: VisibleDay, signal: StrategySignal, top_n: int
) -> DayQuality:
    events: list[QualityEvent] = [
        QualityEvent(
            trade_date=day.trade_date,
            code="factor_value_missing",
            security_id=security_id,
        )
        for security_id in signal.missing_ids
    ]
    events.extend(
        QualityEvent(
            trade_date=day.trade_date,
            code="factor_value_excluded",
            security_id=security_id,
        )
        for security_id in signal.excluded_ids
    )
    if signal.coverage.valid == 0:
        events.append(
            QualityEvent(trade_date=day.trade_date, code="factor_section_empty")
        )
    elif signal.coverage.valid < top_n:
        events.append(
            QualityEvent(
                trade_date=day.trade_date,
                code="factor_section_below_top_n",
            )
        )
    events.extend(
        QualityEvent(trade_date=day.trade_date, code=code) for code in day.degradations
    )
    return DayQuality(
        trade_date=day.trade_date,
        factor_coverage=signal.coverage,
        events=tuple(events),
    )


def _simulate(
    config: BacktestConfig,
    window: PitWindow,
    frames_by_date: Mapping[date, PitFrame],
    factor_by_date: Mapping[date, FactorFrame],
    cost_model: CostModel,
    benchmark_closes: Mapping[date, Decimal] | None,
    regime_weights: Mapping[date, Decimal] | None = None,
    checkpoint: CheckpointFn = _noop_checkpoint,
) -> object:
    days = window.days
    strategy = get_strategy(config.strategy_id)
    period = config.parameters.rebalance_period

    ledger = open_ledger(SIMULATED_INITIAL_CASH, days[0].trade_date)
    last_visible_close = {
        security.security_id: security.close for security in days[0].securities
    }
    equity_points: list[EquityPoint] = [
        EquityPoint(trade_date=days[0].trade_date, equity=SIMULATED_INITIAL_CASH)
    ]
    fills: list[Fill] = []
    rejections: list[Rejection] = []
    outcomes: list[OrderOutcome] = []
    quality_by_day: dict[date, DayQuality] = {
        day.trade_date: DayQuality(
            trade_date=day.trade_date,
            events=tuple(
                QualityEvent(trade_date=day.trade_date, code=code)
                for code in day.degradations
            ),
        )
        for day in days
    }

    for index in range(len(days) - 1):
        # Cooperative checkpoint: one probe per decision iteration so a
        # cancellation or budget breach stops the run before the next fill.
        checkpoint()
        decision_day = days[index]
        execution_date = days[index + 1].trade_date
        market = frames_by_date[execution_date].market

        if index % period == 0:
            signal = strategy(
                decision_day, factor_by_date[decision_day.trade_date], config
            )
            decision_quality = _quality_for_decision_day(
                decision_day, signal, config.parameters.top_n
            )
            # A decision session is also the previous signal's execution
            # session: keep the carry marks already attached that morning.
            carried_events = tuple(
                event
                for event in quality_by_day[decision_day.trade_date].events
                if event.code == "mark_price_carried"
            )
            quality_by_day[decision_day.trade_date] = DayQuality(
                trade_date=decision_day.trade_date,
                factor_coverage=decision_quality.factor_coverage,
                events=decision_quality.events + carried_events,
            )
            decision_marks = {
                security.security_id: security.close
                for security in decision_day.securities
            }
            decision_marks.update(
                {
                    security_id: last_visible_close[security_id]
                    for security_id in ledger.holdings
                    if security_id not in decision_marks
                    and security_id in last_visible_close
                }
            )
            regime_weight = (
                regime_weights.get(decision_day.trade_date, Decimal("1"))
                if regime_weights is not None
                else Decimal("1")
            )
            orders = _plan_orders(
                signal,
                ledger,
                {
                    security.security_id: security.close
                    for security in decision_day.securities
                },
                decision_marks,
                config,
                regime_weight,
            )
        else:
            orders = []

        for order in orders:
            state = state_at(market, order.security_id, execution_date)
            if state is None:
                result = _direct_rejection(order, execution_date, "no_tradable_price")
            elif state.suspended is True:
                result = _direct_rejection(order, execution_date, "suspended")
            elif state.is_delisting is True:
                result = _direct_rejection(order, execution_date, "delisted")
            else:
                day_bars = tuple(
                    bar
                    for bar in market.bars
                    if bar.security_id == order.security_id
                    and bar.trade_date == execution_date
                )
                if len(day_bars) != 1 or bar_issues(day_bars[0], market):
                    result = _direct_rejection(
                        order, execution_date, "no_tradable_price"
                    )
                else:
                    holding = ledger.holdings.get(order.security_id)
                    result = simulate_fill(
                        order,
                        day_bars[0],
                        state,
                        cost_model,
                        ledger.cash,
                        holding,
                    )

            intended_price = next(
                (
                    security.close
                    for security in decision_day.securities
                    if security.security_id == order.security_id
                ),
                None,
            )
            if isinstance(result, Fill):
                intended_price = intended_price or result.raw_close
                fills.append(result)
                ledger = apply_fill(ledger, result)
                filled = True
            else:
                if intended_price is None:
                    intended_price = Decimal("0")
                rejections.append(result)
                filled = False
            outcomes.append(
                OrderOutcome(
                    security_id=order.security_id,
                    side=order.side,
                    quantity=order.quantity,
                    intended_amount=_quantize_money(
                        Decimal(order.quantity) * intended_price
                    ),
                    filled=filled,
                )
            )

        ledger = roll_overnight(ledger, execution_date)
        execution_visible = days[index + 1]
        for security in execution_visible.securities:
            last_visible_close[security.security_id] = security.close
        marks, carry_events = _session_marks(
            execution_visible, tuple(ledger.holdings), last_visible_close
        )
        if carry_events:
            current = quality_by_day[execution_date]
            quality_by_day[execution_date] = DayQuality(
                trade_date=execution_date,
                factor_coverage=current.factor_coverage,
                events=current.events + tuple(carry_events),
            )
        equity_points.append(
            EquityPoint(
                trade_date=execution_date,
                equity=_quantize_money(mark_equity(ledger, marks)),
            )
        )

    final_index = len(days) - 1
    if final_index % period == 0:
        final_day = days[final_index]
        current = quality_by_day[final_day.trade_date]
        quality_by_day[final_day.trade_date] = DayQuality(
            trade_date=final_day.trade_date,
            factor_coverage=current.factor_coverage,
            events=current.events
            + (
                QualityEvent(
                    trade_date=final_day.trade_date,
                    code="decision_without_next_session",
                ),
            ),
        )

    benchmark_prices = None
    if benchmark_closes is not None:
        benchmark_prices = tuple(benchmark_closes[day.trade_date] for day in days)
    metrics = summarize_run(
        equity_points=equity_points,
        fills=tuple(fills),
        outcomes=tuple(outcomes),
        benchmark_prices=benchmark_prices,
    )
    return _SimulationArtifacts(
        ledger=ledger,
        equity=tuple(equity_points),
        trades=tuple(fills),
        rejections=tuple(rejections),
        quality=tuple(quality_by_day[day.trade_date] for day in days),
        metrics=metrics,
    )


class _SimulationArtifacts:
    def __init__(
        self,
        *,
        ledger: LedgerState,
        equity: tuple[EquityPoint, ...],
        trades: tuple[Fill, ...],
        rejections: tuple[Rejection, ...],
        quality: tuple[DayQuality, ...],
        metrics: object,
    ) -> None:
        self.ledger = ledger
        self.equity = equity
        self.trades = trades
        self.rejections = rejections
        self.quality = quality
        self.metrics = metrics


def _build_versions(
    config: BacktestConfig,
    factor_frames: Sequence[FactorFrame],
) -> RunVersions:
    versions = config.input_manifests.versions
    return RunVersions(
        strategy_id=config.strategy_id,
        strategy_method=STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1,
        factor_id=config.parameters.factor_id,
        factor_metric=factor_frames[0].metric,
        factor_window=factor_frames[0].window,
        factor_unit=factor_frames[0].unit,
        sizing_method=SIZING_METHOD,
        execution_method=EXECUTION_METHOD,
        ledger_method=LEDGER_METHOD,
        valuation_method=VALUATION_METHOD,
        cost_method=COST_METHOD,
        cost_sensitivity_multipliers=dict(COST_SENSITIVITY_MULTIPLIERS),
        adjustment=versions.adjustment,
        calendar=versions.calendar,
        pool=versions.pool,
    )


def run_backtest(
    config: BacktestConfig,
    frames: Sequence[PitFrame],
    factor_frames: Sequence[FactorFrame],
    *,
    anchor: PitAnchor,
    policy: UniversePolicy | None = None,
    benchmark_closes: Mapping[date, Decimal] | None = None,
    phase_publications: Sequence[object] | None = None,
    checkpoint: CheckpointFn = _noop_checkpoint,
) -> BacktestResult:
    """Run the deterministic PIT backtest and all cost-sensitivity legs.

    ``checkpoint`` is a zero-argument cooperative seam invoked at every fold
    boundary and every simulated decision iteration; it defaults to a no-op so
    the pure kernel result is byte-identical when no worker drives the run.
    """
    if config.walk_forward is None and phase_publications is not None:
        raise ValueError("phase publications require a walk_forward spec")
    if config.walk_forward is not None and phase_publications is None:
        raise ValueError(
            "walk-forward runs require an explicit phase publication series "
            "(an empty tuple is allowed)"
        )

    window = assemble_pit_window(frames, anchor=anchor, policy=policy)
    _validate_factor_frames(config, window.days, factor_frames)
    if benchmark_closes is not None and any(
        day.trade_date not in benchmark_closes for day in window.days
    ):
        raise ValueError("benchmark closes must cover every PIT session")

    frames_by_date = {frame.trade_date: frame for frame in frames}
    factor_by_date = {frame.trade_date: frame for frame in factor_frames}

    # Imported lazily: walk_forward imports this module at its top level.
    walk_forward_report = None
    if config.walk_forward is not None:
        from dragon_radar.research.walk_forward import run_walk_forward

        walk_forward_report = run_walk_forward(
            config,
            window,
            phase_publications,  # type: ignore[arg-type]
            frames_by_date=frames_by_date,
            factor_by_date=factor_by_date,
            benchmark_closes=benchmark_closes,
            checkpoint=checkpoint,
        )

    tiers = cost_sensitivity_models(config.cost_model)
    base_artifacts: _SimulationArtifacts | None = None
    sensitivity: list[CostSensitivityResult] = []
    for tier_name in COST_SENSITIVITY_TIERS:
        artifacts = _simulate(
            config,
            window,
            frames_by_date,
            factor_by_date,
            tiers[tier_name],
            benchmark_closes,
            checkpoint=checkpoint,
        )
        if tier_name == "base":
            base_artifacts = artifacts
        sensitivity.append(
            CostSensitivityResult(
                tier=tier_name,
                cost_multiplier=COST_SENSITIVITY_MULTIPLIERS[tier_name],
                metrics=artifacts.metrics,
            )
        )

    assert base_artifacts is not None

    ablation_report = None
    if "ai_variant" in config.experiment.legs:
        # Imported lazily to keep ablation out of the baseline-only import path.
        from dragon_radar.research.ablation import build_ablation_report

        ablation_report = build_ablation_report(config, base_artifacts.metrics)

    return BacktestResult(
        schema_version=RESEARCH_BACKTEST_SCHEMA_VERSION,
        config_hash=config.config_hash,
        snapshot_id=config.snapshot_id,
        manifests=config.input_manifests,
        versions=_build_versions(config, factor_frames),
        metrics=base_artifacts.metrics,
        equity=base_artifacts.equity,
        trades=base_artifacts.trades,
        rejections=base_artifacts.rejections,
        quality=base_artifacts.quality,
        folds=(None if walk_forward_report is None else walk_forward_report.folds),
        walk_forward=walk_forward_report,
        baseline=BaselineLeg(
            metrics=base_artifacts.metrics,
            cost_sensitivity=tuple(sensitivity),
        ),
        ablation=ablation_report,
    )


__all__ = [
    "BacktestResult",
    "BaselineLeg",
    "CostSensitivityResult",
    "DayQuality",
    "FactorFrame",
    "FactorPartition",
    "FactorPoint",
    "QualityEvent",
    "RankedSecurity",
    "RunVersions",
    "SIMULATED_INITIAL_CASH",
    "LOT_SIZE",
    "StrategySignal",
    "get_strategy",
    "run_backtest",
]
