"""Task 27 PIT backtest suite (EX14 engine slices).

Task 1 scaffold: the research package modules exist and stay importable so
later slices can extend them without changing the package boundary. The PIT
assembly, ``run_backtest`` engine, factor health check and walk-forward
behaviour tests arrive in Tasks 5-8.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from dragon_radar.domain.models import (
    DailyBar,
    ExchangeCalendar,
    MarketBatch,
    SecurityState,
)
from dragon_radar.research.engine import (
    FactorFrame,
    FactorPoint,
    get_strategy,
    run_backtest,
)
from dragon_radar.research.health import (
    FactorHealthReport,
    assess_factor_health,
)
from dragon_radar.research.models import (
    MAX_PERIOD_CALENDAR_DAYS,
    BacktestConfig,
    BacktestPeriod,
    BacktestSubmission,
    CostModel,
    DomainManifest,
    ExecutionModel,
    ExperimentSpec,
    InputManifests,
    ManifestVersions,
    StrategyParameters,
    WalkForwardSpec,
)
from dragon_radar.research.pit import (
    PitAnchor,
    PitFrame,
    UniversePolicy,
    assemble_pit_window,
)

BACKTEST_MODULES = (
    "models",
    "pit",
    "engine",
    "health",
    "walk_forward",
    "ablation",
)


@pytest.mark.parametrize("module_name", BACKTEST_MODULES)
def test_research_backtest_module_importable(module_name: str) -> None:
    module = importlib.import_module(f"dragon_radar.research.{module_name}")
    assert module is not None


# ---------------------------------------------------------------------------
# Task 2 — immutable configuration, bounded validation, config_hash
# ---------------------------------------------------------------------------

ANCHOR_ID = UUID("11111111-1111-4111-8111-111111111111")
TOPIC_ID = UUID("22222222-2222-4222-8222-222222222222")
ROLE_ID = UUID("33333333-3333-4333-8333-333333333333")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def cost_model(**overrides: object) -> CostModel:
    values = {
        "commission_rate": "0.0003",
        "stamp_tax_rate": "0.001",
        "slippage_rate": "0.0005",
        "min_commission": "5",
    }
    values.update(overrides)
    return CostModel(**values)  # type: ignore[arg-type]


def execution_model() -> ExecutionModel:
    return ExecutionModel()


def parameters(**overrides: object) -> StrategyParameters:
    values = {"top_n": 10, "rebalance_period": 5}
    values.update(overrides)
    return StrategyParameters(**values)  # type: ignore[arg-type]


def period(**overrides: object) -> BacktestPeriod:
    values = {"start": date(2025, 1, 6), "end": date(2025, 6, 27)}
    values.update(overrides)
    return BacktestPeriod(**values)  # type: ignore[arg-type]


def manifests(**overrides: object) -> InputManifests:
    values = {
        "domains": (
            DomainManifest(domain="market", snapshot_id=ANCHOR_ID),
            DomainManifest(domain="topic", snapshot_id=TOPIC_ID),
        ),
        "versions": ManifestVersions(
            adjustment="adjustment-v1",
            calendar="calendar-cn-v1",
            pool="topic-pool-v1",
        ),
    }
    values.update(overrides)
    return InputManifests(**values)  # type: ignore[arg-type]


def experiment(**overrides: object) -> ExperimentSpec:
    values = {"random_seed": 20250106, "legs": ("baseline", "ai_variant")}
    values.update(overrides)
    return ExperimentSpec(**values)  # type: ignore[arg-type]


def walk_forward(**overrides: object) -> WalkForwardSpec:
    values = {"folds": 3, "train_sessions": 40, "test_sessions": 10}
    values.update(overrides)
    return WalkForwardSpec(**values)  # type: ignore[arg-type]


def config(**overrides: object) -> BacktestConfig:
    values = {
        "snapshot_id": ANCHOR_ID,
        "strategy_id": "factor-cross-sectional-topn-v1",
        "parameters": parameters(),
        "period": period(),
        "cost_model": cost_model(),
        "execution_model": execution_model(),
        "walk_forward": None,
        "experiment": experiment(),
        "input_manifests": manifests(),
    }
    values.update(overrides)
    return BacktestConfig(**values)  # type: ignore[arg-type]


def test_valid_config_constructs_and_pins_schema_version() -> None:
    backtest = config()
    assert backtest.schema_version == "dragon-radar.research-backtest/v1"
    assert backtest.cost_model.stamp_tax_side == "sell"
    assert backtest.execution_model.fill_timing == "next_tradable_point"
    assert backtest.parameters.weighting == "equal_weight"
    assert SHA256_RE.fullmatch(backtest.config_hash)


def test_extra_fields_are_forbidden_everywhere() -> None:
    with pytest.raises(ValidationError):
        cost_model(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        config(unexpected="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        StrategyParameters(top_n=10, rebalance_period=5, weighting="magic")
    with pytest.raises(ValidationError):
        ExecutionModel(fill_timing="same_day_close")  # type: ignore[arg-type]


def test_missing_required_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CostModel(
            commission_rate="0.0003",
            stamp_tax_rate="0.001",
            slippage_rate="0.0005",
        )
    with pytest.raises(ValidationError):
        BacktestConfig(
            snapshot_id=ANCHOR_ID,
            strategy_id="factor-cross-sectional-topn-v1",
            parameters=parameters(),
            period=period(),
            execution_model=execution_model(),
            experiment=experiment(),
            input_manifests=manifests(),
        )


@pytest.mark.parametrize(
    "rate_value",
    ["0", "-0.0001", "0.02", "NaN", "Infinity", "-Infinity", "not-a-number"],
)
def test_cost_rates_must_be_finite_positive_and_bounded(rate_value: str) -> None:
    with pytest.raises(ValidationError):
        cost_model(commission_rate=rate_value)


def test_float_cost_input_is_rejected_by_strict_decimal() -> None:
    with pytest.raises(ValidationError):
        cost_model(commission_rate=0.0003)  # type: ignore[arg-type]


@pytest.mark.parametrize("min_commission", ["0", "-1", "100.01"])
def test_min_commission_must_be_positive_and_bounded(min_commission: str) -> None:
    with pytest.raises(ValidationError):
        cost_model(min_commission=min_commission)


@pytest.mark.parametrize("top_n", [0, 101])
def test_top_n_is_bounded(top_n: int) -> None:
    with pytest.raises(ValidationError):
        parameters(top_n=top_n)


@pytest.mark.parametrize("rebalance_period", [0, 61])
def test_rebalance_period_is_bounded(rebalance_period: int) -> None:
    with pytest.raises(ValidationError):
        parameters(rebalance_period=rebalance_period)


def test_unknown_strategy_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        config(strategy_id="six-role-signals-v1")


def test_period_must_be_ordered_and_bounded() -> None:
    with pytest.raises(ValidationError):
        period(start=date(2025, 6, 27), end=date(2025, 1, 6))
    with pytest.raises(ValidationError):
        period(start=date(2025, 1, 6), end=date(2025, 1, 6))
    with pytest.raises(ValidationError):
        period(start=date(2024, 1, 1), end=date(2024, 1, 2))
    long_end = date(2025, 1, 6) + timedelta(days=MAX_PERIOD_CALENDAR_DAYS + 1)
    with pytest.raises(ValidationError):
        period(end=long_end)


def test_market_domain_is_required_and_domains_unique() -> None:
    with pytest.raises(ValidationError):
        manifests(
            domains=(DomainManifest(domain="topic", snapshot_id=TOPIC_ID),),
        )
    with pytest.raises(ValidationError):
        manifests(
            domains=(
                DomainManifest(domain="market", snapshot_id=ANCHOR_ID),
                DomainManifest(domain="market", snapshot_id=uuid4()),
            ),
        )


def test_empty_manifest_version_strings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ManifestVersions(adjustment="", calendar="c1", pool="p1")


def test_experiment_legs_require_baseline_without_duplicates() -> None:
    with pytest.raises(ValidationError):
        experiment(legs=("ai_variant",))
    with pytest.raises(ValidationError):
        experiment(legs=("baseline", "baseline"))
    with pytest.raises(ValidationError):
        experiment(legs=("baseline", "mystery"))  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [-1, 0x1_0000_0000])
def test_random_seed_is_unsigned_32bit(seed: int) -> None:
    with pytest.raises(ValidationError):
        experiment(random_seed=seed)


@pytest.mark.parametrize("folds", [1, 11])
def test_walk_forward_folds_are_bounded(folds: int) -> None:
    with pytest.raises(ValidationError):
        walk_forward(folds=folds)


def test_walk_forward_window_total_fits_session_cap() -> None:
    with pytest.raises(ValidationError):
        walk_forward(folds=10, train_sessions=40, test_sessions=30)


def test_walk_forward_window_must_fit_inside_period_calendar_span() -> None:
    with pytest.raises(ValidationError):
        config(
            period=period(start=date(2025, 1, 6), end=date(2025, 2, 6)),
            walk_forward=walk_forward(folds=3, train_sessions=40, test_sessions=10),
        )


def test_configs_are_frozen() -> None:
    with pytest.raises((TypeError, ValidationError)):
        config().parameters.top_n = 20  # type: ignore[misc]


def test_equal_configs_have_equal_canonical_json_and_hash() -> None:
    first = config()
    second = config()
    assert first.canonical_json() == second.canonical_json()
    assert first.config_hash == second.config_hash


def test_hash_is_independent_of_idempotency_key() -> None:
    first = BacktestSubmission(config=config(), idempotency_key=uuid4())
    second = BacktestSubmission(config=config(), idempotency_key=uuid4())
    assert first.idempotency_key != second.idempotency_key
    assert first.config.config_hash == second.config.config_hash


def test_malformed_idempotency_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BacktestSubmission(config=config(), idempotency_key="not-a-uuid")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c(snapshot_id=uuid4()),
        lambda c: c(walk_forward=walk_forward()),
        lambda c: c(parameters=parameters(top_n=20)),
        lambda c: c(period=period(end=date(2025, 6, 26))),
        lambda c: c(cost_model=cost_model(slippage_rate="0.0008")),
        lambda c: c(experiment=experiment(random_seed=20250107)),
        lambda c: c(
            input_manifests=manifests(
                versions=ManifestVersions(
                    adjustment="adjustment-v2",
                    calendar="calendar-cn-v1",
                    pool="topic-pool-v1",
                )
            )
        ),
    ],
)
def test_any_config_field_change_changes_hash(mutation) -> None:
    baseline = config()
    changed = mutation(config)
    assert changed.canonical_json() != baseline.canonical_json()
    assert changed.config_hash != baseline.config_hash


# ---------------------------------------------------------------------------
# Task 5 — point-in-time visible-information assembly
# ---------------------------------------------------------------------------
#
# Synthetic five-session publication sequence. Every frame is one immutable
# publication (one per trade date, ascending sequence). The scenario packs the
# full PIT hazard set into one fixture:
#   A    steady name
#   B    turns ST effective D3
#   S    suspended D3-D4, tradable again D5
#   DEL  delisting state effective D4, gone after D4 (last tradable day)
#   LATE backdated topic member (effective D1) published only on D4 morning
#   L    trades throughout, but its D3 bar arrives *after* the D3 cutoff
#   NEW  already-known identity whose listing date is after the window

D1, D2, D3, D4, D5 = tuple(date(2025, 1, d) for d in range(6, 11))
PIT_DAYS = (D1, D2, D3, D4, D5)


def _pit_dt(day: date, hour: int = 16, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _cutoff(day: date) -> datetime:
    return _pit_dt(day, 16, 30)


PIT_CAL = ExchangeCalendar(exchange="TEST", version="c1", sessions=PIT_DAYS)
PIT_IDS = ("A", "B", "S", "DEL", "LATE", "L", "NEW")
TRADE_DAYS = {
    "A": set(PIT_DAYS),
    "B": set(PIT_DAYS),
    "S": {D1, D2, D5},
    "DEL": {D1, D2, D3, D4},
    "LATE": set(PIT_DAYS),
    "L": set(PIT_DAYS),
    "NEW": set(),
}


def _state(
    sid: str,
    effective_from: date,
    *,
    available_at: datetime,
    effective_to: date | None = None,
    is_st: bool = False,
    suspended: bool = False,
    is_delisting: bool = False,
    listing_date: date = date(2020, 1, 1),
) -> SecurityState:
    return SecurityState(
        security_id=sid,
        code=sid,
        name=f"Synthetic {sid}",
        board="synthetic-board",
        board_category="synthetic_test",
        is_delisting=is_delisting,
        exchange="TEST",
        effective_from=effective_from,
        effective_to=effective_to,
        available_at=available_at,
        listing_date=listing_date,
        suspended=suspended,
        is_st=is_st,
        source_id="synthetic",
        source_version="s1",
        pool_version="p1",
    )


PIT_STATES = (
    _state("A", D1, available_at=_pit_dt(D1, 9)),
    _state("B", D1, effective_to=D3, available_at=_pit_dt(D1, 9)),
    _state("B", D3, available_at=_pit_dt(D3, 9), is_st=True),
    _state("S", D1, effective_to=D3, available_at=_pit_dt(D1, 9)),
    _state(
        "S",
        D3,
        effective_to=D5,
        available_at=_pit_dt(D3, 9),
        suspended=True,
    ),
    _state("S", D5, available_at=_pit_dt(D5, 9)),
    _state("DEL", D1, effective_to=D4, available_at=_pit_dt(D1, 9)),
    _state(
        "DEL",
        D4,
        effective_to=D5,
        available_at=_pit_dt(D4, 9),
        is_delisting=True,
    ),
    _state("LATE", D1, available_at=_pit_dt(D1, 9)),
    _state("L", D1, available_at=_pit_dt(D1, 9)),
    # Known before the window, but only listed after it ends (D6).
    _state(
        "NEW",
        D1,
        available_at=_pit_dt(D1, 9),
        listing_date=D5 + timedelta(days=1),
    ),
)


def _pit_bar(
    sid: str,
    day: date,
    *,
    available_at: datetime | None = None,
    factor: Decimal = Decimal("1"),
) -> DailyBar:
    return DailyBar(
        security_id=sid,
        trade_date=day,
        available_at=available_at or _pit_dt(day, 16),
        source_id="synthetic",
        source_version="s1",
        close=Decimal("10"),
        adjustment_factor=factor,
        adjustment_version="a1",
        currency="CNY",
        price_unit="currency/share",
        volume_unit="share",
        amount_unit="currency",
        volume=Decimal("100"),
        amount=Decimal("1000"),
        limit_status="no_limit",
    )


def _decision_bars(day: date, *, revise_history: bool = False) -> tuple[DailyBar, ...]:
    bars = []
    for sid in PIT_IDS:
        if day not in TRADE_DAYS[sid]:
            continue
        # L's D3 observation lands after the D3 decision cutoff: a late bar.
        available = _pit_dt(day, 18) if sid == "L" and day == D3 else _pit_dt(day, 16)
        bars.append(_pit_bar(sid, day, available_at=available))
    if revise_history and day == D5:
        # Retroactive D3 factor correction, only visible inside the D5 batch.
        bars.append(
            _pit_bar(
                "A",
                D3,
                available_at=_pit_dt(D5, 16),
                factor=Decimal("1.1"),
            )
        )
    return tuple(bars)


def _market(day: date, *, revise_history: bool = False) -> MarketBatch:
    from factories import grant

    return MarketBatch(
        trade_date=day,
        as_of=_cutoff(day),
        source_id="synthetic",
        source_version="s1",
        adjustment_version="a1",
        calendar_version="c1",
        pool_version="p1",
        grant=grant(),
        calendar=PIT_CAL,
        expected_security_ids=PIT_IDS,
        security_states=PIT_STATES,
        bars=_decision_bars(day, revise_history=revise_history),
        complete_pages=True,
        provenance="contemporaneous",
    )


def _membership(sid: str, *, available_at: datetime):
    from dragon_radar.topics.models import TopicMembership

    return TopicMembership(
        topic_id="T1",
        security_id=sid,
        effective_from=D1,
        available_at=available_at,
        evidence_refs=(f"ev-{sid}",),
    )


def _topic(day: date, *, future_member: bool = False):
    from factories import grant

    from dragon_radar.topics.models import (
        ManifestPage,
        PageManifest,
        TopicBatch,
        TopicCoverageCell,
        TopicDefinition,
        TopicMembership,
    )

    memberships = [
        _membership("A", available_at=_pit_dt(D1, 9)),
        _membership("B", available_at=_pit_dt(D1, 9)),
        _membership("S", available_at=_pit_dt(D1, 9)),
        _membership("DEL", available_at=_pit_dt(D1, 9)),
        _membership("L", available_at=_pit_dt(D1, 9)),
        # Backdated constituent: claims effective D1, only known on D4 morning.
        _membership("LATE", available_at=_pit_dt(D4, 9)),
    ]
    if future_member:
        memberships.append(
            TopicMembership(
                topic_id="T1",
                security_id="FUTURE",
                effective_from=D5 + timedelta(days=1),
                available_at=_pit_dt(D5, 18),
                evidence_refs=("ev-future",),
            )
        )
    return TopicBatch(
        trade_date=day,
        as_of=_cutoff(day),
        provenance="contemporaneous",
        source_id="synthetic",
        source_version="s1",
        grant=grant(),
        exchange="TEST",
        calendar=PIT_CAL,
        calendar_version="c1",
        taxonomy_version="tax1",
        membership_version="m1",
        coverage_from=D1,
        coverage_to=D5,
        coverage_cells=(
            TopicCoverageCell(
                topic_id="T1",
                trade_date=day,
                expected_member_count=6,
                received_member_count=6,
                complete_as_of=_cutoff(day),
                status="complete",
            ),
        ),
        topics=(
            TopicDefinition(
                topic_id="T1",
                name="topic-one",
                effective_from=D1,
                available_at=_pit_dt(D1, 9),
                source_reference="topic-one-ref",
            ),
        ),
        memberships=tuple(memberships),
        page_manifest=PageManifest(
            request_from=D1,
            request_to=D5,
            pages=(
                ManifestPage(
                    page_id="p1",
                    request_cursor=None,
                    next_cursor=None,
                    page_hash="0" * 64,
                    received_row_count=len(memberships),
                ),
            ),
            declared_total=len(memberships),
            terminal_cursor=None,
        ),
    )


def _frame(
    day: date,
    *,
    sequence: int | None = None,
    status: str = "published",
    include_topic: bool = True,
    revise_history: bool = False,
    future_member: bool = False,
) -> PitFrame:
    return PitFrame(
        trade_date=day,
        publication_sequence=(PIT_DAYS.index(day) + 1) * 10
        if sequence is None
        else sequence,
        status=status,  # type: ignore[arg-type]
        market=_market(day, revise_history=revise_history),
        topic=(_topic(day, future_member=future_member) if include_topic else None),
    )


def _frames(
    *,
    include_topic: bool = True,
    revise_history: bool = False,
    future_member: bool = False,
) -> tuple[PitFrame, ...]:
    return tuple(
        _frame(
            day,
            include_topic=include_topic,
            revise_history=revise_history and day == D5,
            future_member=future_member and day == D5,
        )
        for day in PIT_DAYS
    )


def _anchor(
    *,
    day: date = D5,
    sequence: int = 50,
    status: str = "published",
) -> PitAnchor:
    return PitAnchor(
        publication_sequence=sequence,
        trade_date=day,
        status=status,  # type: ignore[arg-type]
    )


def _policy(*, topic_ids: tuple[str, ...] = ("T1",)) -> UniversePolicy:
    return UniversePolicy(topic_ids=topic_ids)


def _ids(window, day: date) -> set[str]:
    visible_day = window.days[PIT_DAYS.index(day)]
    return {security.security_id for security in visible_day.securities}


def _security(window, day: date, security_id: str):
    visible_day = window.days[PIT_DAYS.index(day)]
    return next(
        security
        for security in visible_day.securities
        if security.security_id == security_id
    )


def _exclusion_codes(window, day: date) -> dict[str, set[str]]:
    visible_day = window.days[PIT_DAYS.index(day)]
    codes: dict[str, set[str]] = {}
    for exclusion in visible_day.exclusions:
        codes.setdefault(exclusion.security_id, set()).add(exclusion.code)
    return codes


def test_pit_daily_universe_matches_then_visible_state_and_membership() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    assert _ids(window, D1) == {"A", "B", "S", "DEL", "L"}
    assert _ids(window, D2) == {"A", "B", "S", "DEL", "L"}
    # B turned ST, S suspended, L's bar is late, LATE not visible until D4.
    assert _ids(window, D3) == {"A", "DEL"}
    # DEL keeps trading into its delisting flag day; LATE joins at D4.
    assert _ids(window, D4) == {"A", "DEL", "L", "LATE"}
    # DEL is gone after its last effective day; S is back.
    assert _ids(window, D5) == {"A", "L", "S", "LATE"}

    assert _security(window, D4, "DEL").is_delisting is True
    assert _security(window, D3, "A").is_st is False

    # The post-window listing never enters any historical fold.
    assert all(
        "NEW" not in {security.security_id for security in visible_day.securities}
        for visible_day in window.days
    )


def test_late_constituent_is_excluded_until_its_availability_day() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    for day in (D1, D2, D3):
        assert "LATE" in _exclusion_codes(window, day)
        assert "late_topic_member" in _exclusion_codes(window, day)["LATE"]
    assert "LATE" in _ids(window, D4)
    assert "LATE" in _ids(window, D5)


def test_delisted_security_is_retained_until_last_effective_day() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    for day in (D1, D2, D3):
        assert "DEL" in _ids(window, day)
    assert "DEL" in _ids(window, D4)
    assert "DEL" not in _ids(window, D5)
    assert "unobservable_state" in _exclusion_codes(window, D5)["DEL"]


def test_st_change_and_suspension_window_apply_per_day() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    assert "B" in _ids(window, D1)
    for day in (D3, D4, D5):
        assert "st_excluded" in _exclusion_codes(window, day)["B"]
    assert "suspended_excluded" in _exclusion_codes(window, D3)["S"]
    assert "suspended_excluded" in _exclusion_codes(window, D4)["S"]
    assert "S" in _ids(window, D5)


def test_late_bar_is_excluded_and_recorded_in_quality() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    assert "late_bar" in _exclusion_codes(window, D3)["L"]
    assert "L" not in _ids(window, D3)
    assert "L" in _ids(window, D4)


def test_post_window_listing_is_unobservable_every_day() -> None:
    window = assemble_pit_window(_frames(), anchor=_anchor(), policy=_policy())

    for day in PIT_DAYS:
        assert "unobservable_state" in _exclusion_codes(window, day)["NEW"]


def test_adjustment_factor_is_pinned_to_decision_day_frame() -> None:
    window = assemble_pit_window(
        _frames(revise_history=True),
        anchor=_anchor(),
        policy=_policy(),
    )

    decision_security = _security(window, D3, "A")
    assert decision_security.adjustment_factor == Decimal("1")
    assert decision_security.adjustment_version == "a1"


def test_future_revisions_and_constituents_leave_anchored_window_byte_identical() -> (
    None
):
    policy = _policy()
    baseline = assemble_pit_window(_frames(), anchor=_anchor(), policy=policy)
    future = assemble_pit_window(
        _frames(revise_history=True, future_member=True),
        anchor=_anchor(),
        policy=policy,
    )

    assert future.model_dump(mode="json") == baseline.model_dump(mode="json")


def test_market_wide_policy_keeps_non_members_and_applies_state_rules() -> None:
    window = assemble_pit_window(
        _frames(),
        anchor=_anchor(),
        policy=_policy(topic_ids=()),
    )

    assert _ids(window, D1) == {"A", "B", "S", "DEL", "LATE", "L"}
    # L's late bar still excludes it on D3; B (ST) and S (suspended) too.
    assert _ids(window, D3) == {"A", "DEL", "LATE"}


def test_missing_topic_domain_degrades_to_market_wide_pool() -> None:
    window = assemble_pit_window(
        _frames(include_topic=False),
        anchor=_anchor(),
        policy=_policy(),
    )

    assert _ids(window, D1) == {"A", "B", "S", "DEL", "LATE", "L"}
    assert window.days[0].degradations == ("topic_domain_unavailable",)


def test_non_published_anchor_fails_closed() -> None:
    with pytest.raises(ValueError, match="anchor"):
        assemble_pit_window(
            _frames(),
            anchor=_anchor(status="revoked"),
            policy=_policy(),
        )


def test_non_published_or_cross_sequence_frame_fails_closed() -> None:
    revoked = list(_frames())
    revoked[2] = _frame(D3, status="revoked")
    with pytest.raises(ValueError, match="published"):
        assemble_pit_window(revoked, anchor=_anchor(), policy=_policy())

    cross_sequence = list(_frames())
    cross_sequence[4] = _frame(D5, sequence=60)
    with pytest.raises(ValueError, match="sequence"):
        assemble_pit_window(cross_sequence, anchor=_anchor(), policy=_policy())

    after_anchor = list(_frames())
    with pytest.raises(ValueError, match="anchor"):
        assemble_pit_window(
            after_anchor,
            anchor=_anchor(day=D4, sequence=40),
            policy=_policy(),
        )


def test_unsorted_duplicate_or_mismatched_frames_fail_closed() -> None:
    frames = _frames()
    with pytest.raises(ValueError, match="order"):
        assemble_pit_window(
            [frames[1], frames[0], *frames[2:]],
            anchor=_anchor(),
            policy=_policy(),
        )
    with pytest.raises(ValueError, match="duplicate"):
        assemble_pit_window(
            [frames[0], frames[0], *frames[2:]],
            anchor=_anchor(),
            policy=_policy(),
        )

    mismatched = _frame(D1)
    mismatched = PitFrame(
        trade_date=D1,
        publication_sequence=10,
        market=_market(D2),
        topic=None,
    )
    with pytest.raises(ValueError, match="trade_date"):
        assemble_pit_window(
            [mismatched, *frames[1:]],
            anchor=_anchor(),
            policy=_policy(),
        )


def test_empty_frame_sequence_fails_closed() -> None:
    with pytest.raises(ValueError, match="at least one"):
        assemble_pit_window((), anchor=_anchor(), policy=_policy())


def test_window_over_250_sessions_fails_closed() -> None:
    wide_days = tuple(
        date(2025, 1, 6) + timedelta(days=offset) for offset in range(251)
    )
    calendar = ExchangeCalendar(exchange="TEST", version="c1", sessions=wide_days)
    wide_frames = []
    for index, day in enumerate(wide_days, start=1):
        batch = MarketBatch(
            trade_date=day,
            as_of=_cutoff(day),
            source_id="synthetic",
            source_version="s1",
            adjustment_version="a1",
            calendar_version="c1",
            pool_version="p1",
            grant=_market(day).grant,
            calendar=calendar,
            expected_security_ids=(),
            security_states=(),
            bars=(),
            complete_pages=True,
            provenance="contemporaneous",
        )
        wide_frames.append(
            PitFrame(trade_date=day, publication_sequence=index, market=batch)
        )

    with pytest.raises(ValueError, match="250"):
        assemble_pit_window(
            wide_frames,
            anchor=PitAnchor(
                publication_sequence=251,
                trade_date=wide_days[-1],
                status="published",
            ),
            policy=_policy(),
        )


# ---------------------------------------------------------------------------
# Task 6 — run_backtest engine, strategy registry and frozen result schema
# ---------------------------------------------------------------------------
#
# A flat-price five-session market (all closes 10.00) keeps the golden
# arithmetic inspectable. The factor rotates the Top-2 selection at every
# rebalance: on D1 the section ranks X1 > X2 > X3 > X4, on D3 it ranks
# X3 > X4 > X1 > X2. Signals form at the close and fill only next session.

E_D1, E_D2, E_D3, E_D4, E_D5 = tuple(date(2025, 2, d) for d in range(10, 15))
ENG_DAYS = (E_D1, E_D2, E_D3, E_D4, E_D5)
ENG_IDS = ("X1", "X2", "X3", "X4")
ENG_CAL = ExchangeCalendar(exchange="TEST", version="c1", sessions=ENG_DAYS)
ENG_FLAT = {sid: Decimal("10") for sid in ENG_IDS}
ENG_RANK_12 = {
    "X1": Decimal("40"),
    "X2": Decimal("30"),
    "X3": Decimal("20"),
    "X4": Decimal("10"),
}
ENG_RANK_34 = {
    "X1": Decimal("20"),
    "X2": Decimal("10"),
    "X3": Decimal("40"),
    "X4": Decimal("30"),
}
ENG_VALUES_BY_DAY = {
    E_D1: ENG_RANK_12,
    E_D2: ENG_RANK_12,
    E_D3: ENG_RANK_34,
    E_D4: ENG_RANK_34,
    E_D5: ENG_RANK_34,
}


def _eng_dt(day: date, hour: int = 16, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _eng_state(
    sid: str,
    *,
    effective_from: date = E_D1,
    effective_to: date | None = None,
    suspended: bool = False,
) -> SecurityState:
    return SecurityState(
        security_id=sid,
        code=sid,
        name=f"Engine {sid}",
        board="main",
        board_category="synthetic_test",
        exchange="TEST",
        effective_from=effective_from,
        effective_to=effective_to,
        available_at=_eng_dt(E_D1, 9),
        listing_date=E_D1,
        suspended=suspended,
        source_id="synthetic",
        source_version="s1",
        pool_version="p1",
    )


def _eng_steady_states(ids: tuple[str, ...] = ENG_IDS) -> tuple[SecurityState, ...]:
    return tuple(_eng_state(sid) for sid in ids)


def _eng_bar(
    sid: str,
    day: date,
    *,
    close: str = "10",
    limited: bool = False,
) -> DailyBar:
    return DailyBar(
        security_id=sid,
        trade_date=day,
        available_at=_eng_dt(day, 16),
        source_id="synthetic",
        source_version="s1",
        close=Decimal(close),
        adjustment_factor=Decimal("1"),
        adjustment_version="a1",
        currency="CNY",
        price_unit="currency/share",
        volume_unit="share",
        amount_unit="currency",
        volume=Decimal("100"),
        amount=Decimal("1000"),
        limit_status="limited" if limited else "no_limit",
        limit_date=day if limited else None,
        limit_up=Decimal("11") if limited else None,
        limit_down=Decimal("9") if limited else None,
    )


def _eng_market(
    day: date,
    closes: dict[str, str],
    *,
    ids: tuple[str, ...] = ENG_IDS,
    states: tuple[SecurityState, ...] | None = None,
    limited: frozenset[str] = frozenset(),
) -> MarketBatch:
    from factories import grant

    bars = []
    for sid in ids:
        close = closes.get(sid)
        if close is None:
            continue
        bars.append(_eng_bar(sid, day, close=close, limited=sid in limited))
    return MarketBatch(
        trade_date=day,
        as_of=_eng_dt(day, 16, 30),
        source_id="synthetic",
        source_version="s1",
        adjustment_version="a1",
        calendar_version="c1",
        pool_version="p1",
        grant=grant(),
        calendar=ENG_CAL,
        expected_security_ids=ids,
        security_states=states if states is not None else _eng_steady_states(ids),
        bars=tuple(bars),
        complete_pages=True,
        provenance="contemporaneous",
    )


def _eng_frames(
    closes_by_day: dict[date, dict[str, str]],
    *,
    ids: tuple[str, ...] = ENG_IDS,
    states: tuple[SecurityState, ...] | None = None,
    limited: dict[date, frozenset[str]] | None = None,
) -> tuple[PitFrame, ...]:
    limits = limited or {}
    return tuple(
        PitFrame(
            trade_date=day,
            publication_sequence=(index + 1) * 100,
            market=_eng_market(
                day,
                closes_by_day[day],
                ids=ids,
                states=states,
                limited=limits.get(day, frozenset()),
            ),
        )
        for index, day in enumerate(ENG_DAYS)
    )


def _factor_point(sid: str, value: Decimal | bool | None) -> FactorPoint:
    if value is None:
        return FactorPoint(
            security_id=sid,
            value=None,
            reasons=("factor_value_unavailable",),
        )
    return FactorPoint(security_id=sid, value=value)


def _factor_frame(
    day: date,
    values: dict[str, Decimal | bool | None],
    *,
    ids: tuple[str, ...] = ENG_IDS,
    factor_id: str = "FAC-R01",
    metric: str = "relative_strength_20",
    window: str = "20d",
    unit: str = "ratio",
) -> FactorFrame:
    return FactorFrame(
        trade_date=day,
        factor_id=factor_id,  # type: ignore[arg-type]
        metric=metric,
        window=window,
        unit=unit,
        points=tuple(_factor_point(sid, values.get(sid)) for sid in ids),
    )


def _factor_frames(
    values_by_day: dict[date, dict[str, Decimal | bool | None]],
    *,
    ids: tuple[str, ...] = ENG_IDS,
    factor_id: str = "FAC-R01",
) -> tuple[FactorFrame, ...]:
    return tuple(
        _factor_frame(day, values_by_day[day], ids=ids, factor_id=factor_id)
        for day in ENG_DAYS
    )


def _eng_anchor() -> PitAnchor:
    return PitAnchor(
        publication_sequence=500,
        trade_date=E_D5,
        status="published",
    )


def _eng_config(**overrides: object) -> BacktestConfig:
    values = {
        "period": period(start=E_D1, end=E_D5),
        "parameters": parameters(top_n=2, rebalance_period=2),
    }
    values.update(overrides)
    return config(**values)  # type: ignore[arg-type]


def _run_engine(
    cfg: BacktestConfig,
    frames: Sequence[PitFrame],
    factor_frames: Sequence[FactorFrame],
) -> object:
    return run_backtest(
        cfg,
        frames,
        factor_frames,
        anchor=_eng_anchor(),
        policy=UniversePolicy(),
    )


def _result_json(result: object) -> str:
    return json.dumps(
        result.model_dump(mode="json"),  # type: ignore[attr-defined]
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def golden_engine_result_json() -> str:
    """Deterministic primary-scenario builder for the cross-process probe."""
    cfg = _eng_config()
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    factor_frames = _factor_frames(ENG_VALUES_BY_DAY)
    return _result_json(_run_engine(cfg, frames, factor_frames))


def test_strategy_registry_rejects_unknown_strategy_id() -> None:
    assert get_strategy("factor-cross-sectional-topn-v1") is not None
    with pytest.raises(ValueError, match="strategy"):
        get_strategy("six-role-signals-v1")


def test_factor_id_is_a_bounded_strategy_parameter_with_default() -> None:
    assert parameters().factor_id == "FAC-R01"
    assert parameters(factor_id="FAC-C02").factor_id == "FAC-C02"
    with pytest.raises(ValidationError):
        parameters(factor_id="MADE-UP")  # type: ignore[arg-type]
    baseline = _eng_config().config_hash
    changed = _eng_config(
        parameters=parameters(top_n=2, rebalance_period=2, factor_id="FAC-C01")
    ).config_hash
    assert changed != baseline


def test_run_backtest_golden_rotation_economics() -> None:
    result = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(ENG_VALUES_BY_DAY),
    )

    trades = result.trades
    assert len(trades) == 6
    assert [(t.security_id, t.side) for t in trades] == [
        ("X1", "buy"),
        ("X2", "buy"),
        ("X1", "sell"),
        ("X2", "sell"),
        ("X3", "buy"),
        ("X4", "buy"),
    ]
    assert {t.trade_date for t in trades} == {E_D2, E_D4}
    assert all(t.quantity == 4900 for t in trades)
    assert all(t.fill_price == Decimal("10.0050") for t in trades if t.side == "buy")
    assert all(t.fill_price == Decimal("9.9950") for t in trades if t.side == "sell")
    assert result.rejections == ()

    assert [point.trade_date for point in result.equity] == list(ENG_DAYS)
    assert result.equity[0].equity == Decimal("100000.00")
    assert result.equity[-1].equity == Decimal("99666.82")

    metrics = result.metrics
    assert metrics.initial_equity == Decimal("100000.00")
    assert metrics.final_equity == Decimal("99666.82")
    assert metrics.total_return == Decimal("-0.003332")
    assert metrics.max_drawdown == Decimal("0.003332")
    assert metrics.turnover_rate == Decimal("2.940490")
    assert metrics.total_commission == Decimal("88.22")
    assert metrics.total_stamp_tax == Decimal("97.96")
    assert metrics.total_cost == Decimal("186.18")
    assert metrics.unfillable_count_ratio == Decimal("0.000000")
    assert metrics.unfillable_amount_ratio == Decimal("0.000000")


def test_signals_fill_only_next_session_and_terminal_day_never_trades() -> None:
    result = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(ENG_VALUES_BY_DAY),
    )
    for trade in result.trades:
        assert trade.trade_date == trade.signal_date + timedelta(days=1)
    assert all(trade.trade_date != E_D5 for trade in result.trades)
    terminal_events = [
        event
        for day in result.quality
        for event in day.events
        if event.code == "decision_without_next_session"
    ]
    assert [(event.trade_date) for event in terminal_events] == [E_D5]


def test_result_carries_versioned_research_only_skeleton() -> None:
    cfg = _eng_config()
    result = _run_engine(
        cfg,
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(ENG_VALUES_BY_DAY),
    )
    assert result.schema_version == "dragon-radar.research-backtest/v1"
    assert result.research_only is True
    assert result.config_hash == cfg.config_hash
    assert result.snapshot_id == cfg.snapshot_id
    assert result.manifests == cfg.input_manifests
    versions = result.versions
    assert versions.strategy_id == "factor-cross-sectional-topn-v1"
    assert versions.factor_id == "FAC-R01"
    assert versions.factor_metric == "relative_strength_20"
    assert versions.factor_window == "20d"
    assert versions.factor_unit == "ratio"
    assert versions.sizing_method
    assert versions.execution_method
    assert versions.ledger_method
    assert versions.valuation_method
    assert versions.cost_method
    assert versions.adjustment == "adjustment-v1"
    assert versions.calendar == "calendar-cn-v1"
    assert versions.pool == "topic-pool-v1"
    assert result.folds is None
    assert result.ablation is None
    assert result.baseline.status == "completed"
    assert result.baseline.metrics == result.metrics
    assert tuple(entry.tier for entry in result.baseline.cost_sensitivity) == (
        "low",
        "base",
        "high",
    )
    assert [day_quality.trade_date for day_quality in result.quality] == list(ENG_DAYS)
    decision_coverage = {
        day_quality.trade_date: day_quality.factor_coverage
        for day_quality in result.quality
    }
    assert decision_coverage[E_D1].expected == 4
    assert decision_coverage[E_D1].valid == 4
    assert decision_coverage[E_D2] is None
    assert decision_coverage[E_D3].valid == 4
    assert decision_coverage[E_D4] is None


def test_cost_sensitivity_tiers_are_strictly_ordered() -> None:
    result = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(ENG_VALUES_BY_DAY),
    )
    legs = {entry.tier: entry for entry in result.baseline.cost_sensitivity}
    assert legs["base"].metrics == result.metrics
    assert legs["low"].cost_multiplier == Decimal("0.5")
    assert legs["high"].cost_multiplier == Decimal("2")
    assert (
        legs["low"].metrics.final_equity
        > legs["base"].metrics.final_equity
        > legs["high"].metrics.final_equity
    )
    assert (
        legs["low"].metrics.total_cost
        < legs["base"].metrics.total_cost
        < legs["high"].metrics.total_cost
    )


def test_missing_factor_value_is_excluded_never_fabricated() -> None:
    values = {
        day: dict(values_for_day) for day, values_for_day in ENG_VALUES_BY_DAY.items()
    }
    values[E_D1]["X3"] = None
    result = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(values),
    )
    day_one = result.quality[0]
    coverage = day_one.factor_coverage
    assert (coverage.expected, coverage.valid, coverage.excluded, coverage.missing) == (
        4,
        3,
        0,
        1,
    )
    missing_events = [
        event for event in day_one.events if event.code == "factor_value_missing"
    ]
    assert [(event.security_id) for event in missing_events] == ["X3"]
    bought = {t.security_id for t in result.trades if t.trade_date == E_D2}
    assert bought == {"X1", "X2"}


def test_bool_factor_value_counts_as_excluded_coverage() -> None:
    values = {
        E_D1: {
            "X1": Decimal("40"),
            "X2": Decimal("30"),
            "X3": Decimal("20"),
            "X4": True,
        },
        E_D2: dict(ENG_RANK_12),
        E_D3: dict(ENG_RANK_34),
        E_D4: dict(ENG_RANK_34),
        E_D5: dict(ENG_RANK_34),
    }
    result = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(values),
    )
    coverage = result.quality[0].factor_coverage
    assert (coverage.expected, coverage.valid, coverage.excluded, coverage.missing) == (
        4,
        3,
        1,
        0,
    )
    excluded_events = [
        event
        for event in result.quality[0].events
        if event.code == "factor_value_excluded"
    ]
    assert [event.security_id for event in excluded_events] == ["X4"]
    bought = {t.security_id for t in result.trades if t.trade_date == E_D2}
    assert bought == {"X1", "X2"}


def test_section_below_top_n_and_empty_section_fail_open_without_orders() -> None:
    below_values = {
        E_D1: {
            "X1": Decimal("40"),
            "X2": Decimal("30"),
            "X3": None,
            "X4": None,
        },
        E_D2: dict(ENG_RANK_12),
        E_D3: dict(ENG_RANK_34),
        E_D4: dict(ENG_RANK_34),
        E_D5: dict(ENG_RANK_34),
    }
    below = _run_engine(
        _eng_config(parameters=parameters(top_n=3, rebalance_period=2)),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(below_values),
    )
    below_events = [event.code for event in below.quality[0].events]
    assert "factor_section_below_top_n" in below_events
    assert below.quality[0].factor_coverage.valid == 2
    assert {t.security_id for t in below.trades if t.trade_date == E_D2} == {
        "X1",
        "X2",
    }

    empty_values = {day: {sid: None for sid in ENG_IDS} for day in ENG_DAYS}
    empty = _run_engine(
        _eng_config(),
        _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
        _factor_frames(empty_values),
    )
    assert empty.trades == ()
    assert "factor_section_empty" in [event.code for event in empty.quality[0].events]
    assert empty.metrics.total_return == Decimal("0.000000")


def test_limit_up_buy_rejection_feeds_dual_unfillable_ratios() -> None:
    # A sealed limit-up session closes exactly at limit_up (11 vs 10 flat).
    closes = {day: dict(ENG_FLAT) for day in ENG_DAYS}
    closes[E_D2]["X1"] = "11"
    result = _run_engine(
        _eng_config(),
        _eng_frames(
            closes,
            limited={E_D2: frozenset({"X1"})},
        ),
        _factor_frames(ENG_VALUES_BY_DAY),
    )
    assert len(result.rejections) == 1
    rejection = result.rejections[0]
    assert rejection.security_id == "X1"
    assert rejection.side == "buy"
    assert rejection.trade_date == E_D2
    assert rejection.reason == "limit_up_blocked_buy"
    # The rejected buy never settles, so X1 is absent from holdings at the D3
    # rebalance: five intended orders in total, one unfillable (1/5).
    assert result.metrics.unfillable_count_ratio == Decimal("0.200000")
    assert result.metrics.unfillable_amount_ratio == Decimal("0.200000")


def test_suspended_holding_carries_mark_and_closes_as_rejection() -> None:
    ids = ("Y1", "Y2")
    states = (
        _eng_state("Y1"),
        _eng_state("Y2", effective_to=E_D3),
        _eng_state("Y2", effective_from=E_D3, effective_to=E_D5, suspended=True),
        _eng_state("Y2", effective_from=E_D5),
    )
    closes = {
        E_D1: {"Y1": "10", "Y2": "10"},
        E_D2: {"Y1": "10", "Y2": "10"},
        E_D3: {"Y1": "10"},
        E_D4: {"Y1": "10"},
        E_D5: {"Y1": "10", "Y2": "10"},
    }
    values = {day: {"Y1": Decimal("40"), "Y2": Decimal("30")} for day in ENG_DAYS}
    result = _run_engine(
        _eng_config(),
        _eng_frames(closes, ids=ids, states=states),
        _factor_frames(values, ids=ids),
    )
    sell_rejections = [
        rejection
        for rejection in result.rejections
        if rejection.security_id == "Y2" and rejection.side == "sell"
    ]
    assert len(sell_rejections) == 1
    assert sell_rejections[0].reason == "suspended"
    assert sell_rejections[0].trade_date == E_D4
    carry_days = [
        day_quality.trade_date
        for day_quality in result.quality
        for event in day_quality.events
        if event.code == "mark_price_carried" and event.security_id == "Y2"
    ]
    assert carry_days == [E_D3, E_D4]


def test_factor_frames_must_align_with_window_and_declared_factor() -> None:
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    factor_frames = _factor_frames(ENG_VALUES_BY_DAY)

    with pytest.raises(ValueError, match="align"):
        run_backtest(
            _eng_config(),
            frames,
            factor_frames[:-1],
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
        )
    mismatched_date = factor_frames[0].model_copy(update={"trade_date": E_D2})
    with pytest.raises(ValueError, match="align"):
        run_backtest(
            _eng_config(),
            frames,
            (mismatched_date, *factor_frames[1:]),
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
        )
    with pytest.raises(ValueError, match="factor_id"):
        run_backtest(
            _eng_config(),
            frames,
            _factor_frames(ENG_VALUES_BY_DAY, factor_id="FAC-C01"),
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
        )
    inconsistent_metric = factor_frames[2].model_copy(update={"metric": "other-metric"})
    with pytest.raises(ValueError, match="metric"):
        run_backtest(
            _eng_config(),
            frames,
            (*factor_frames[:2], inconsistent_metric, *factor_frames[3:]),
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
        )


def test_factor_frame_validation_is_strict() -> None:
    with pytest.raises(ValidationError):
        FactorPoint(security_id="X1", value=1.2)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FactorPoint(security_id="X1", value=None)
    with pytest.raises(ValidationError):
        FactorFrame(
            trade_date=E_D1,
            factor_id="NOT-A-FACTOR",  # type: ignore[arg-type]
            metric="m",
            window="w",
            unit="u",
            points=(_factor_point("X1", Decimal("1")),),
        )
    with pytest.raises(ValueError, match="duplicate"):
        FactorFrame(
            trade_date=E_D1,
            factor_id="FAC-R01",
            metric="m",
            window="w",
            unit="u",
            points=(
                _factor_point("X1", Decimal("1")),
                _factor_point("X1", Decimal("2")),
            ),
        )


def test_same_inputs_produce_field_identical_results() -> None:
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    factor_frames = _factor_frames(ENG_VALUES_BY_DAY)
    first = _result_json(_run_engine(_eng_config(), frames, factor_frames))
    second = _result_json(_run_engine(_eng_config(), frames, factor_frames))
    assert first == second


def test_cross_process_run_is_byte_identical() -> None:
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = os.path.abspath(".temp/pycache")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, 'tests'); "
                "import test_reset_backtest as t; "
                "print(t.golden_engine_result_json())"
            ),
        ],
        cwd=os.getcwd(),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == golden_engine_result_json()


# ---------------------------------------------------------------------------
# Task 7 — factor health: sample size, coverage partition, Decimal quantiles
# and cross-factor Spearman rank correlations
# ---------------------------------------------------------------------------


def _health_frames(
    closes_by_day: dict[date, dict[str, str]] | None = None,
    *,
    ids: tuple[str, ...] = ENG_IDS,
) -> tuple[PitFrame, ...]:
    closes = closes_by_day or {day: dict(ENG_FLAT) for day in ENG_DAYS}
    return _eng_frames(closes, ids=ids)


def _factor_stream(
    values_by_day: dict[date, dict[str, Decimal | bool | None]],
    *,
    ids: tuple[str, ...] = ENG_IDS,
    factor_id: str = "FAC-R01",
    metric: str = "relative_strength_20",
) -> tuple[FactorFrame, ...]:
    return tuple(
        _factor_frame(
            day,
            values_by_day[day],
            ids=ids,
            factor_id=factor_id,
            metric=metric,
        )
        for day in ENG_DAYS
    )


def _assess_health(
    streams: dict[str, tuple[FactorFrame, ...]],
    *,
    frames: tuple[PitFrame, ...] | None = None,
    min_sample_size: int = 5,
    quantile_points: tuple[Decimal, ...] | None = None,
) -> FactorHealthReport:
    keyword_args: dict[str, object] = {}
    if quantile_points is not None:
        keyword_args["quantile_points"] = quantile_points
    return assess_factor_health(
        _eng_config(),
        frames if frames is not None else _health_frames(),
        streams,
        anchor=_eng_anchor(),
        policy=UniversePolicy(),
        min_sample_size=min_sample_size,
        **keyword_args,
    )


def _health_by_id(
    report: FactorHealthReport,
) -> dict[str, object]:
    return {factor.factor_id: factor for factor in report.factors}


def test_health_report_carries_versions_window_and_primary_factor() -> None:
    report = _assess_health(
        {"FAC-R01": _factor_stream(ENG_VALUES_BY_DAY)},
    )
    assert report.schema_version == "dragon-radar.research-health/v1"
    assert report.research_only is True
    assert report.window_start == E_D1
    assert report.window_end == E_D5
    assert report.sessions == 5
    assert report.anchor_sequence == 500
    assert report.primary_factor_id == "FAC-R01"
    assert report.manifests == _eng_config().input_manifests
    factor = _health_by_id(report)["FAC-R01"]
    assert factor.metric == "relative_strength_20"
    assert factor.window == "20d"
    assert factor.unit == "ratio"
    assert report.rank_correlations == ()


def test_health_pooled_coverage_partition_conserves_with_rates() -> None:
    values = {
        day: dict(values_for_day) for day, values_for_day in ENG_VALUES_BY_DAY.items()
    }
    values[E_D1]["X4"] = None
    values[E_D2]["X4"] = True
    report = _assess_health({"FAC-R01": _factor_stream(values)})
    factor = _health_by_id(report)["FAC-R01"]
    coverage = factor.coverage
    assert (
        coverage.expected,
        coverage.valid,
        coverage.excluded,
        coverage.missing,
    ) == (20, 18, 1, 1)
    assert factor.sample_size == 18
    assert factor.valid_rate == Decimal("0.900000")
    assert factor.excluded_rate == Decimal("0.050000")
    assert factor.missing_rate == Decimal("0.050000")
    assert factor.status == "ok"
    assert factor.quantiles is not None


def test_health_points_outside_visible_pool_never_enter_partition() -> None:
    values = {
        day: dict(values_for_day) for day, values_for_day in ENG_VALUES_BY_DAY.items()
    }
    values[E_D1]["X9"] = Decimal("99")
    stream = tuple(
        frame.model_copy(
            update={
                "points": (
                    *frame.points,
                    _factor_point("X9", Decimal("99")),
                )
            }
        )
        if frame.trade_date == E_D1
        else frame
        for frame in _factor_stream(values)
    )
    report = _assess_health({"FAC-R01": stream})
    coverage = _health_by_id(report)["FAC-R01"].coverage
    assert (coverage.expected, coverage.valid) == (20, 20)


def test_health_quantiles_match_hand_computed_linear_interpolation() -> None:
    ids = ("X1", "X2", "X3", "X4", "X5")
    level_values = {
        day: (
            {
                "X1": Decimal("1"),
                "X2": Decimal("2"),
                "X3": Decimal("3"),
                "X4": Decimal("4"),
                "X5": Decimal("5"),
            }
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    frames = _health_frames(
        {day: {sid: "10" for sid in ids} for day in ENG_DAYS}, ids=ids
    )
    report = _assess_health(
        {"FAC-R01": _factor_stream(level_values, ids=ids)},
        frames=frames,
        quantile_points=(
            Decimal("0"),
            Decimal("0.1"),
            Decimal("0.25"),
            Decimal("0.5"),
            Decimal("0.75"),
            Decimal("1"),
        ),
    )
    factor = _health_by_id(report)["FAC-R01"]
    assert factor.status == "ok"
    assert tuple(str(point) for point in factor.quantiles.points) == (
        "0",
        "0.1",
        "0.25",
        "0.5",
        "0.75",
        "1",
    )
    assert tuple(str(value) for value in factor.quantiles.values) == (
        "1.000000",
        "1.400000",
        "2.000000",
        "3.000000",
        "4.000000",
        "5.000000",
    )


def test_health_spearman_with_ties_matches_hand_computed_value() -> None:
    ids = ("X1", "X2", "X3", "X4", "X5")
    values_a = {
        day: (
            {
                "X1": Decimal("1"),
                "X2": Decimal("2"),
                "X3": Decimal("3"),
                "X4": Decimal("4"),
                "X5": Decimal("5"),
            }
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    # Hand ranks: B values 5,3,3,2,1 -> ascending ranks 5,3.5,3.5,2,1.
    values_b = {
        day: (
            {
                "X1": Decimal("5"),
                "X2": Decimal("3"),
                "X3": Decimal("3"),
                "X4": Decimal("2"),
                "X5": Decimal("1"),
            }
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    frames = _health_frames(
        {day: {sid: "10" for sid in ids} for day in ENG_DAYS}, ids=ids
    )
    report = _assess_health(
        {
            "FAC-R01": _factor_stream(values_a, ids=ids),
            "FAC-C01": _factor_stream(
                values_b, ids=ids, factor_id="FAC-C01", metric="fac_c01_metric"
            ),
        },
        frames=frames,
    )
    assert len(report.rank_correlations) == 1
    correlation = report.rank_correlations[0]
    assert (correlation.left, correlation.right) == ("FAC-C01", "FAC-R01")
    assert correlation.paired_sample_size == 5
    assert correlation.eligible_sessions == 1
    assert correlation.status == "ok"
    assert correlation.value == Decimal("-0.974679")


def test_health_insufficient_sample_gives_reason_not_fabricated_numbers() -> None:
    ids = ("X1", "X2", "X3", "X4")
    sparse_a = {
        day: (
            {sid: Decimal(str(index + 1)) for index, sid in enumerate(ids)}
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    sparse_b = {
        day: (
            {sid: Decimal(str(4 - index)) for index, sid in enumerate(ids)}
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    frames = _health_frames(
        {day: {sid: "10" for sid in ids} for day in ENG_DAYS}, ids=ids
    )
    report = _assess_health(
        {
            "FAC-R01": _factor_stream(sparse_a, ids=ids),
            "FAC-R02": _factor_stream(
                sparse_b, ids=ids, factor_id="FAC-R02", metric="fac_r02_metric"
            ),
        },
        frames=frames,
    )
    weak = _health_by_id(report)["FAC-R01"]
    assert weak.coverage.valid == 4
    assert weak.status == "insufficient_sample"
    assert weak.reasons == ("insufficient_sample_size",)
    assert weak.quantiles is None
    # Exact partition ratios remain facts; statistical estimates are withheld.
    assert weak.valid_rate == Decimal("0.200000")
    correlation = report.rank_correlations[0]
    assert correlation.value is None
    assert correlation.status == "insufficient_sample"
    assert "insufficient_paired_sample" in correlation.reasons


def test_health_constant_ranks_close_to_untradable_correlation() -> None:
    ids = ("X1", "X2", "X3", "X4", "X5")
    values_a = {
        day: (
            {sid: Decimal(str(index + 1)) for index, sid in enumerate(ids)}
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    values_constant = {
        day: (
            {sid: Decimal("7") for sid in ids}
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    frames = _health_frames(
        {day: {sid: "10" for sid in ids} for day in ENG_DAYS}, ids=ids
    )
    report = _assess_health(
        {
            "FAC-R01": _factor_stream(values_a, ids=ids),
            "FAC-R02": _factor_stream(
                values_constant, ids=ids, factor_id="FAC-R02", metric="m2"
            ),
        },
        frames=frames,
    )
    correlation = report.rank_correlations[0]
    assert correlation.paired_sample_size == 5
    assert correlation.eligible_sessions == 0
    assert correlation.value is None
    assert "no_eligible_sessions" in correlation.reasons


def test_health_rank_matrix_pairs_are_sorted_for_three_factors() -> None:
    ids = ("X1", "X2", "X3", "X4", "X5")
    base = {
        day: (
            {
                "X1": Decimal("1"),
                "X2": Decimal("2"),
                "X3": Decimal("3"),
                "X4": Decimal("4"),
                "X5": Decimal("5"),
            }
            if day == E_D1
            else {sid: None for sid in ids}
        )
        for day in ENG_DAYS
    }
    frames = _health_frames(
        {day: {sid: "10" for sid in ids} for day in ENG_DAYS}, ids=ids
    )
    report = _assess_health(
        {
            "FAC-R01": _factor_stream(base, ids=ids),
            "FAC-R02": _factor_stream(base, ids=ids, factor_id="FAC-R02", metric="m2"),
            "FAC-C01": _factor_stream(base, ids=ids, factor_id="FAC-C01", metric="m3"),
        },
        frames=frames,
    )
    assert [(entry.left, entry.right) for entry in report.rank_correlations] == [
        ("FAC-C01", "FAC-R01"),
        ("FAC-C01", "FAC-R02"),
        ("FAC-R01", "FAC-R02"),
    ]
    assert all(entry.value == Decimal("1.000000") for entry in report.rank_correlations)


def test_health_validation_fails_closed() -> None:
    primary_stream = _factor_stream(ENG_VALUES_BY_DAY)

    with pytest.raises(ValueError, match="primary"):
        _assess_health(
            {"FAC-C01": _factor_stream(ENG_VALUES_BY_DAY, factor_id="FAC-C01")}
        )
    with pytest.raises(ValueError, match="align"):
        _assess_health({"FAC-R01": primary_stream[:-1]})
    drifted_metric = primary_stream[2].model_copy(update={"metric": "other-metric"})
    with pytest.raises(ValueError, match="metric"):
        _assess_health(
            {"FAC-R01": (*primary_stream[:2], drifted_metric, *primary_stream[3:])}
        )
    with pytest.raises(ValueError, match="min_sample"):
        _assess_health({"FAC-R01": primary_stream}, min_sample_size=1)
    with pytest.raises(ValueError, match="min_sample"):
        _assess_health({"FAC-R01": primary_stream}, min_sample_size=251)
    with pytest.raises(ValueError, match="quantile"):
        _assess_health(
            {"FAC-R01": primary_stream},
            quantile_points=(Decimal("0.5"), Decimal("1.2")),
        )


def test_health_same_inputs_produce_field_identical_reports() -> None:
    streams = {"FAC-R01": _factor_stream(ENG_VALUES_BY_DAY)}
    first = _assess_health(streams).model_dump_json()
    second = _assess_health(streams).model_dump_json()
    assert first == second
