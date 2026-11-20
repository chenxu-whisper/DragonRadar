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
from dragon_radar.research.ablation import (
    ABLATION_AI_DISABLED_REASON,
    RESEARCH_ABLATION_SCHEMA_VERSION,
    AblationLeg,
    AblationReport,
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
from dragon_radar.research.walk_forward import PhasePublication

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
    # Task 9: the default experiment requests the ai_variant leg, which is
    # fixed disabled and carries no numbers; the baseline leg runs normally.
    assert result.ablation is not None
    assert result.ablation.schema_version == RESEARCH_ABLATION_SCHEMA_VERSION
    assert result.ablation.ai_variant.status == "disabled"
    assert result.ablation.ai_variant.reason == ABLATION_AI_DISABLED_REASON
    assert result.ablation.ai_variant.metrics is None
    assert result.ablation.baseline.status == "completed"
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


# ---------------------------------------------------------------------------
# Task 8 — walk-forward fold partition, frozen train-only regime weighting
# and EX16 five-phase PIT conditioning
# ---------------------------------------------------------------------------
#
# Eight business sessions (d0..d7) split into one anchored train prefix
# (d0..d3) plus two strictly tiled out-of-sample folds (d4..d5, d6..d7).
# The canonical market only moves inside the train prefix: d0 10 -> d1 9 and
# d2 10 -> d3 10.5, so the hand-computed regime means stay tiny and auditable.

WF_IDS = ("X1", "X2")


def _wf_business_days(count: int) -> tuple[date, ...]:
    days: list[date] = []
    cursor = date(2025, 3, 3)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


WF_DAYS = _wf_business_days(8)
WF_CAL = ExchangeCalendar(exchange="TEST", version="wf1", sessions=WF_DAYS)
WF_SPEC = WalkForwardSpec(folds=2, train_sessions=4, test_sessions=2)


def _wf_state(sid: str, first_day: date) -> SecurityState:
    return SecurityState(
        security_id=sid,
        code=sid,
        name=f"WalkForward {sid}",
        board="main",
        board_category="synthetic_test",
        exchange="TEST",
        effective_from=first_day,
        effective_to=None,
        available_at=datetime(
            first_day.year, first_day.month, first_day.day, 9, tzinfo=UTC
        ),
        listing_date=first_day,
        suspended=False,
        source_id="synthetic",
        source_version="s1",
        pool_version="p1",
    )


def _wf_market(day: date, closes: dict[str, str]) -> MarketBatch:
    from factories import grant

    return MarketBatch(
        trade_date=day,
        as_of=_eng_dt(day, 16, 30),
        source_id="synthetic",
        source_version="s1",
        adjustment_version="a1",
        calendar_version="wf1",
        pool_version="p1",
        grant=grant(),
        calendar=WF_CAL,
        expected_security_ids=WF_IDS,
        security_states=tuple(_wf_state(sid, WF_DAYS[0]) for sid in WF_IDS),
        bars=tuple(_eng_bar(sid, day, close=closes[sid]) for sid in WF_IDS),
        complete_pages=True,
        provenance="contemporaneous",
    )


def _wf_closes() -> dict[date, dict[str, str]]:
    closes = {day: {sid: "10" for sid in WF_IDS} for day in WF_DAYS}
    closes[WF_DAYS[1]] = {sid: "9" for sid in WF_IDS}
    closes[WF_DAYS[3]] = {sid: "10.5" for sid in WF_IDS}
    return closes


def _wf_frames(
    closes_by_day: dict[date, dict[str, str]],
) -> tuple[PitFrame, ...]:
    return tuple(
        PitFrame(
            trade_date=day,
            publication_sequence=(index + 1) * 100,
            market=_wf_market(day, closes_by_day[day]),
        )
        for index, day in enumerate(WF_DAYS)
    )


def _wf_factor_frames() -> tuple[FactorFrame, ...]:
    values = {"X1": Decimal("40"), "X2": Decimal("30")}
    return tuple(_factor_frame(day, values, ids=WF_IDS) for day in WF_DAYS)


def _wf_anchor() -> PitAnchor:
    return PitAnchor(
        publication_sequence=900,
        trade_date=WF_DAYS[-1],
        status="published",
    )


def _wf_config(**overrides: object) -> BacktestConfig:
    values = {
        "period": period(start=WF_DAYS[0], end=WF_DAYS[-1]),
        "parameters": parameters(top_n=2, rebalance_period=2),
        "walk_forward": WF_SPEC,
    }
    values.update(overrides)
    return config(**values)  # type: ignore[arg-type]


def _phase(day: date, phase: str, sequence: int) -> PhasePublication:
    return PhasePublication(
        trade_date=day,
        publication_sequence=sequence,
        phase=phase,  # type: ignore[arg-type]
    )


def _wf_phases() -> tuple[PhasePublication, ...]:
    # d0 sees ice, d2 sees repair; both fit windows stay inside the train
    # prefix. The first OOS decision day d4 republishes ice in its own frame
    # (seq 500), so under latest-visible PIT lookup the frozen ice weight --
    # not the older repair publication -- governs fold 0.
    return (
        _phase(WF_DAYS[0], "ice", 100),
        _phase(WF_DAYS[2], "repair", 300),
        _phase(WF_DAYS[4], "ice", 500),
    )


def _run_wf(
    closes_by_day: dict[date, dict[str, str]] | None = None,
    phases: tuple[PhasePublication, ...] | None = None,
    *,
    spec: WalkForwardSpec | None = WF_SPEC,
) -> object:
    cfg = _wf_config() if spec is WF_SPEC else _wf_config(walk_forward=spec)
    return run_backtest(
        cfg,
        _wf_frames(closes_by_day or _wf_closes()),
        _wf_factor_frames(),
        anchor=_wf_anchor(),
        policy=UniversePolicy(),
        phase_publications=_wf_phases() if phases is None else phases,
    )


def _wf_weight_map(report: object) -> dict[str, object]:
    return {entry.phase: entry for entry in report.regime_map.weights}


def _wf_fold_trades(result: object, fold_index: int) -> dict[str, int]:
    report = result.walk_forward
    fold = report.folds[fold_index]
    bought: dict[str, int] = {}
    for fill in fold.trades:
        if fill.side == "buy":
            bought[fill.security_id] = fill.quantity
    return bought


def test_wf_fold_partition_is_disjoint_and_covers_test_region() -> None:
    report = _run_wf().walk_forward
    assert report.folds_count == 2
    assert report.train_sessions == 4
    assert report.test_sessions == 2
    train = report.regime_map
    assert (train.train_start, train.train_end) == (WF_DAYS[0], WF_DAYS[3])
    bounds = [(fold.test_start, fold.test_end) for fold in report.folds]
    assert bounds == [(WF_DAYS[4], WF_DAYS[5]), (WF_DAYS[6], WF_DAYS[7])]
    for fold in report.folds:
        assert (fold.train_start, fold.train_end) == (WF_DAYS[0], WF_DAYS[3])
        assert fold.test_start > fold.train_end
    # The tiled test folds share no session and cover sessions d4..d7 wholly.
    first, second = report.folds
    assert first.test_end < second.test_start
    fold_equity_days = [
        point.trade_date for fold in report.folds for point in fold.equity
    ]
    assert fold_equity_days == list(WF_DAYS[4:])
    assert report.aggregate_metrics.initial_equity == first.metrics.initial_equity


def test_wf_report_pins_schema_spec_folds_and_aggregate_metrics() -> None:
    result = _run_wf()
    report = result.walk_forward
    assert report.schema_version == "dragon-radar.research-walk-forward/v1"
    assert report.research_only is True
    assert result.folds == report.folds
    assert [fold.fold_index for fold in report.folds] == [0, 1]
    for fold in report.folds:
        assert fold.metrics.initial_equity == Decimal("100000.00")
        assert fold.equity[0].equity == Decimal("100000.00")
    # The aggregate OOS run spans the full tiled test region continuously.
    aggregate_days = [point.trade_date for point in report.aggregate_equity]
    assert aggregate_days == list(WF_DAYS[4:])
    assert report.aggregate_metrics.final_equity == report.aggregate_equity[-1].equity


def test_wf_fitted_weights_match_hand_computed_regime_means() -> None:
    report = _run_wf().walk_forward
    weights = _wf_weight_map(report)
    ice = weights["ice"]
    assert ice.status == "fitted"
    assert ice.observations == 1
    assert ice.mean_next_session_return == Decimal("-0.100000")
    assert ice.weight == Decimal("0.900000")
    repair = weights["repair"]
    assert repair.status == "fitted"
    assert repair.observations == 1
    assert repair.mean_next_session_return == Decimal("0.050000")
    assert repair.weight == Decimal("1.000000")  # never levered above unit weight
    for phase_name in ("ebb", "divergent", "climax"):
        entry = weights[phase_name]
        assert entry.status == "unobserved_in_training"
        assert entry.observations == 0
        assert entry.mean_next_session_return is None
        assert entry.weight == Decimal("1.000000")
    assert report.regime_map.missing_phase_decision_sessions == ()


def test_wf_fitted_weights_freeze_against_test_region_mutations() -> None:
    baseline = _run_wf().walk_forward

    mutated_test = _wf_closes()
    for day in WF_DAYS[4:]:
        mutated_test[day] = {sid: "12" for sid in WF_IDS}
    test_mutated = _run_wf(mutated_test).walk_forward
    assert test_mutated.regime_map.model_dump() == baseline.regime_map.model_dump()

    mutated_train = _wf_closes()
    mutated_train[WF_DAYS[1]] = {sid: "9.5" for sid in WF_IDS}
    train_mutated = _run_wf(mutated_train).walk_forward
    ice = _wf_weight_map(train_mutated)["ice"]
    assert ice.mean_next_session_return == Decimal("-0.050000")
    assert ice.weight == Decimal("0.950000")


def test_wf_regime_weight_scales_oos_order_quantities() -> None:
    weighted = _run_wf()
    # ice weight 0.900000: 50,000 budget -> 45,000 -> floor(44.96 lots) = 4,400.
    assert _wf_fold_trades(weighted, 0) == {"X1": 4400, "X2": 4400}

    # No phase series at all degrades every decision to unit weight: 4,900.
    unweighted = _run_wf(phases=())
    assert _wf_fold_trades(unweighted, 0) == {"X1": 4900, "X2": 4900}
    missing_events = [
        event.code for fold in unweighted.walk_forward.folds for event in fold.quality
    ]
    assert missing_events == ["regime_phase_missing", "regime_phase_missing"]


def test_wf_phase_as_of_never_reads_future_or_late_publications() -> None:
    # A late high-sequence revision of d0 must not reach any historical day;
    # the d6 climax publication must not leak into the train prefix.
    phases = (
        _phase(WF_DAYS[0], "ice", 100),
        _phase(WF_DAYS[0], "ebb", 900),
        _phase(WF_DAYS[6], "climax", 550),
    )
    report = _run_wf(phases=phases).walk_forward
    weights = _wf_weight_map(report)
    ice = weights["ice"]
    assert ice.observations == 2
    assert ice.mean_next_session_return == Decimal("-0.025000")
    assert ice.weight == Decimal("0.975000")
    assert weights["ebb"].status == "unobserved_in_training"
    assert weights["repair"].status == "unobserved_in_training"
    assert weights["climax"].status == "unobserved_in_training"
    # d4 still sees the contemporaneous ice phase visible on d0.
    assert _wf_fold_trades(_run_wf(phases=phases), 0) == {"X1": 4800, "X2": 4800}
    # d6 sees climax for the first time; it was never fitted, so unit weight.
    events = {event.code: event.phase for event in report.folds[1].quality}
    assert events == {"regime_unobserved_in_training": "climax"}
    assert _wf_fold_trades(_run_wf(phases=phases), 1) == {"X1": 4900, "X2": 4900}


def test_wf_missing_phase_degrades_to_unit_weight_with_quality_reason() -> None:
    # The first publication lands on d5: train decisions and fold-0's d4
    # decision have no visible phase; no state may be fabricated.
    phases = (_phase(WF_DAYS[5], "ebb", 600),)
    report = _run_wf(phases=phases).walk_forward
    assert report.regime_map.missing_phase_decision_sessions == (
        WF_DAYS[0],
        WF_DAYS[2],
    )
    assert all(
        entry.status == "unobserved_in_training" for entry in report.regime_map.weights
    )
    codes = [
        (event.trade_date, event.code, event.phase)
        for fold in report.folds
        for event in fold.quality
    ]
    assert codes == [
        (WF_DAYS[4], "regime_phase_missing", None),
        (WF_DAYS[6], "regime_unobserved_in_training", "ebb"),
    ]


def test_wf_unobserved_regime_on_test_day_applies_unit_weight() -> None:
    # Train sessions are all ice; fold 1 meets a never-fitted climax phase.
    phases = (
        _phase(WF_DAYS[0], "ice", 100),
        _phase(WF_DAYS[6], "climax", 700),
    )
    report = _run_wf(phases=phases).walk_forward
    assert _wf_weight_map(report)["ice"].observations == 2
    events = tuple(report.folds[1].quality)
    assert len(events) == 1
    assert events[0].trade_date == WF_DAYS[6]
    assert events[0].code == "regime_unobserved_in_training"
    assert events[0].phase == "climax"
    assert _wf_fold_trades(_run_wf(phases=phases), 1) == {"X1": 4900, "X2": 4900}


def test_wf_same_inputs_produce_field_identical_reports() -> None:
    first = _result_json(_run_wf())
    second = _result_json(_run_wf())
    assert first == second


def test_wf_validation_fails_closed() -> None:
    frames = _wf_frames(_wf_closes())
    factor_frames = _wf_factor_frames()

    with pytest.raises(ValueError, match="phase"):
        run_backtest(
            _wf_config(),
            frames,
            factor_frames,
            anchor=_wf_anchor(),
            policy=UniversePolicy(),
            phase_publications=None,
        )
    with pytest.raises(ValueError, match="phase"):
        run_backtest(
            _wf_config(walk_forward=None),
            frames,
            factor_frames,
            anchor=_wf_anchor(),
            policy=UniversePolicy(),
            phase_publications=_wf_phases(),
        )
    with pytest.raises(ValueError, match="session"):
        _run_wf(spec=WalkForwardSpec(folds=3, train_sessions=4, test_sessions=2))
    with pytest.raises(ValueError, match="duplicate"):
        _run_wf(
            phases=(
                _phase(WF_DAYS[0], "ice", 100),
                _phase(WF_DAYS[0], "ebb", 100),
            )
        )
    with pytest.raises(ValueError, match="order"):
        _run_wf(
            phases=(
                _phase(WF_DAYS[2], "repair", 300),
                _phase(WF_DAYS[0], "ice", 100),
            )
        )
    with pytest.raises(ValueError, match="anchor"):
        _run_wf(phases=(_phase(WF_DAYS[0], "ice", 950),))
    with pytest.raises(ValueError, match="anchor"):
        _run_wf(phases=(_phase(date(2025, 3, 13), "ice", 100),))
    with pytest.raises(ValidationError):
        _phase(WF_DAYS[0], "bull_market", 100)


# ---------------------------------------------------------------------------
# Task 9 — AI-uplift ablation frame fixed disabled (AC-11, TR-9.1/9.2)
# ---------------------------------------------------------------------------


def _ablation_result(
    *,
    legs: tuple[str, ...] = ("baseline", "ai_variant"),
    random_seed: int = 20250106,
) -> tuple[BacktestConfig, object]:
    cfg = _eng_config(
        experiment=experiment(legs=legs, random_seed=random_seed)  # type: ignore[arg-type]
    )
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    return cfg, _run_engine(cfg, frames, _factor_frames(ENG_VALUES_BY_DAY))


def test_ai_variant_leg_is_disabled_with_reason_and_no_numbers() -> None:
    _, result = _ablation_result()
    report = result.ablation
    assert isinstance(report, AblationReport)
    assert report.schema_version == "dragon-radar.research-ablation/v1"
    assert report.research_only is True

    ai_leg = report.ai_variant
    assert ai_leg.leg == "ai_variant"
    assert ai_leg.status == "disabled"
    assert ai_leg.reason == "real_evaluation_inputs_unavailable"
    # TR-9.1: a disabled leg produces no performance numbers of any kind.
    assert ai_leg.metrics is None
    ai_dump = ai_leg.model_dump(mode="json")
    assert ai_dump["metrics"] is None
    assert "equity" not in ai_dump
    assert "trades" not in ai_dump
    assert "uplift" not in ai_dump


def test_baseline_leg_repeats_the_unchanged_run() -> None:
    _, result = _ablation_result()
    base_leg = result.ablation.baseline
    assert base_leg.leg == "baseline"
    assert base_leg.status == "completed"
    assert base_leg.reason is None
    assert base_leg.metrics == result.metrics
    assert base_leg.metrics == result.baseline.metrics


def test_ablation_legs_share_identical_pool_window_cost_seed() -> None:
    cfg, result = _ablation_result(random_seed=424242)
    report = result.ablation
    baseline_inputs = report.baseline.inputs
    ai_inputs = report.ai_variant.inputs
    # TR-9.2: the controlled comparison freezes identical inputs on both legs.
    assert baseline_inputs == ai_inputs
    assert baseline_inputs.snapshot_id == cfg.snapshot_id
    assert baseline_inputs.input_manifests == cfg.input_manifests
    assert baseline_inputs.period == cfg.period
    assert baseline_inputs.cost_model == cfg.cost_model
    assert baseline_inputs.random_seed == 424242
    assert baseline_inputs.random_seed == cfg.experiment.random_seed


def test_baseline_only_experiment_emits_no_ablation() -> None:
    _, result = _ablation_result(legs=("baseline",))
    assert result.ablation is None
    # The baseline leg and headline run are unaffected by the absent variant.
    assert result.baseline.status == "completed"
    assert result.baseline.metrics == result.metrics


def test_ablation_leg_models_fail_closed() -> None:
    _, result = _ablation_result()
    shared = result.ablation.baseline.inputs
    metrics = result.metrics
    reason = "real_evaluation_inputs_unavailable"

    # A disabled leg must carry the structured reason and no metrics.
    with pytest.raises(ValidationError):
        AblationLeg(leg="ai_variant", status="disabled", inputs=shared, metrics=metrics)
    with pytest.raises(ValidationError):
        AblationLeg(
            leg="ai_variant",
            status="disabled",
            inputs=shared,
            metrics=metrics,
            reason=reason,
        )
    with pytest.raises(ValidationError):
        AblationLeg(leg="ai_variant", status="disabled", inputs=shared)
    # A completed leg must carry metrics and no disabled reason.
    with pytest.raises(ValidationError):
        AblationLeg(leg="baseline", status="completed", inputs=shared)
    with pytest.raises(ValidationError):
        AblationLeg(
            leg="baseline",
            status="completed",
            inputs=shared,
            metrics=metrics,
            reason=reason,
        )
    # The well-formed legs construct.
    AblationLeg(leg="ai_variant", status="disabled", inputs=shared, reason=reason)
    AblationLeg(leg="baseline", status="completed", inputs=shared, metrics=metrics)


def test_ablation_report_rejects_mismatched_leg_inputs() -> None:
    _, result = _ablation_result()
    shared = result.ablation.baseline.inputs
    changed = shared.model_copy(update={"random_seed": shared.random_seed + 1})
    completed = AblationLeg(
        leg="baseline", status="completed", inputs=shared, metrics=result.metrics
    )
    disabled = AblationLeg(
        leg="ai_variant",
        status="disabled",
        inputs=changed,
        reason=ABLATION_AI_DISABLED_REASON,
    )
    with pytest.raises(ValidationError):
        AblationReport(baseline=completed, ai_variant=disabled)


def test_ablation_report_is_field_identical_across_runs() -> None:
    _, first = _ablation_result()
    _, second = _ablation_result()
    assert first.ablation.model_dump(mode="json") == second.ablation.model_dump(
        mode="json"
    )


# ---------------------------------------------------------------------------
# Task 10 — migration 0014_research: owner-scoped run ledger and the global
# immutable config_hash cache. The five-state machine, input immutability and
# the forbidden downgrade are real PostgreSQL contracts (TR-10.1/10.2).
# ---------------------------------------------------------------------------

REQUEST_HASH_64 = "a" * 64
CONFIG_HASH_64 = "c" * 64
OTHER_HASH_64 = "b" * 64
T10_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
T10_ENGINE_VERSION = "dragon-radar.research-backtest/v1"


def _research_publish(database, repository, *, suffix: str = ""):
    from factories import NOW, batch

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    market = repository.publish(
        repository.import_batch(
            batch(),
            key=f"research-migration-market{suffix}",
        ).id
    )
    return PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key=f"research-migration-publication{suffix}",
    )


@pytest.fixture
def backtest_schema(database, repository):
    """Head schema with one owner principal and one published publication."""
    from sqlalchemy import text

    owner_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO owner_principals (id, local_key, created_at) "
                "VALUES (:owner_id, NULL, :now)"
            ),
            {"owner_id": owner_id, "now": T10_NOW},
        )
    publication = _research_publish(database, repository, suffix="-t10")
    return database, owner_id, publication.id


_RUN_INSERT_SQL = """
    INSERT INTO research_backtest_runs (
        id, owner_id, idempotency_key, request_hash, config_hash, snapshot_id,
        input_manifest, status, attempt_count, max_wall_clock_ms, available_at,
        lease_token, lease_owner, lease_expires_at, result, failure,
        created_at, started_at, completed_at
    ) VALUES (
        :run_id, :owner_id, :idempotency_key, :request_hash, :config_hash,
        :snapshot_id, CAST(:input_manifest AS JSONB), :status, :attempt_count,
        :max_wall_clock_ms, :available_at, :lease_token, :lease_owner,
        :lease_expires_at, CAST(:result AS JSONB), CAST(:failure AS JSONB),
        :created_at, :started_at, :completed_at
    )
"""

_CACHE_INSERT_SQL = """
    INSERT INTO research_backtest_cache (
        id, config_hash, engine_version, input_manifest, result, created_at
    ) VALUES (
        :cache_id, :config_hash, :engine_version,
        CAST(:input_manifest AS JSONB), CAST(:result AS JSONB), :created_at
    )
"""


def _run_values(owner_id, snapshot_id, *, status: str = "queued", **overrides):
    values = {
        "run_id": uuid4(),
        "owner_id": owner_id,
        "idempotency_key": f"research-key-{uuid4().hex}",
        "request_hash": REQUEST_HASH_64,
        "config_hash": CONFIG_HASH_64,
        "snapshot_id": snapshot_id,
        "input_manifest": json.dumps({"domains": ["market"]}),
        "status": status,
        "attempt_count": 0,
        "max_wall_clock_ms": 60_000,
        "available_at": T10_NOW,
        "lease_token": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "result": None,
        "failure": None,
        "created_at": T10_NOW,
        "started_at": None,
        "completed_at": None,
    }
    values.update(overrides)
    return values


def _state_overrides(status: str) -> dict[str, object]:
    if status == "queued":
        return {}
    if status == "running":
        return {
            "lease_token": uuid4(),
            "lease_owner": "research-worker-1",
            "lease_expires_at": T10_NOW,
            "started_at": T10_NOW,
        }
    if status == "succeeded":
        return {
            "result": json.dumps({"schema_version": T10_ENGINE_VERSION}),
            "completed_at": T10_NOW,
        }
    if status == "failed":
        return {
            "failure": json.dumps({"code": "attempts_exhausted"}),
            "completed_at": T10_NOW,
        }
    if status == "cancelled":
        return {"completed_at": T10_NOW}
    raise AssertionError(f"unknown lifecycle state: {status}")


def _insert_run(connection, owner_id, snapshot_id, **overrides):
    from sqlalchemy import text

    values = _run_values(owner_id, snapshot_id, **overrides)
    connection.execute(text(_RUN_INSERT_SQL), values)
    return values


def _insert_cache(connection, *, config_hash: str = CONFIG_HASH_64):
    from sqlalchemy import text

    values = {
        "cache_id": uuid4(),
        "config_hash": config_hash,
        "engine_version": T10_ENGINE_VERSION,
        "input_manifest": json.dumps({"domains": ["market"]}),
        "result": json.dumps({"schema_version": T10_ENGINE_VERSION}),
        "created_at": T10_NOW,
    }
    connection.execute(text(_CACHE_INSERT_SQL), values)
    return values


def test_0014_migration_is_unique_head_and_database_is_ready(database) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect, text

    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0014_research"
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0014_research"]
    assert script.get_revision("0014_research").down_revision == "0013_query"
    assert database.ready()
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0014_research"
        )
    inspector = inspect(database.engine)
    assert {"research_backtest_runs", "research_backtest_cache"} <= set(
        inspector.get_table_names()
    )
    run_columns = {
        column["name"] for column in inspector.get_columns("research_backtest_runs")
    }
    assert {
        "owner_id",
        "idempotency_key",
        "request_hash",
        "config_hash",
        "snapshot_id",
        "input_manifest",
        "status",
        "attempt_count",
        "max_wall_clock_ms",
        "available_at",
        "lease_token",
        "lease_owner",
        "lease_expires_at",
        "result",
        "failure",
        "created_at",
        "started_at",
        "completed_at",
    } <= run_columns
    cache_columns = {
        column["name"] for column in inspector.get_columns("research_backtest_cache")
    }
    assert {
        "id",
        "config_hash",
        "engine_version",
        "input_manifest",
        "result",
        "created_at",
    } <= cache_columns


def test_0014_registers_input_guard_and_cache_immutability_triggers(
    backtest_schema,
) -> None:
    from sqlalchemy import text

    database, _owner_id, _publication_id = backtest_schema
    with database.engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT relation.relname, trigger_record.tgname,
                       function_record.proname,
                       pg_get_triggerdef(trigger_record.oid, true)
                FROM pg_trigger trigger_record
                JOIN pg_class relation
                  ON relation.oid = trigger_record.tgrelid
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_proc function_record
                  ON function_record.oid = trigger_record.tgfoid
                WHERE namespace.nspname = current_schema()
                  AND relation.relname IN (
                      'research_backtest_runs', 'research_backtest_cache'
                  )
                  AND NOT trigger_record.tgisinternal
                """
            )
        ).all()
    triggers = {
        (relation, name): (function, definition)
        for (
            relation,
            name,
            function,
            definition,
        ) in rows
    }

    runs_function, runs_definition = triggers[
        ("research_backtest_runs", "research_backtest_runs_immutable_inputs")
    ]
    assert runs_function == "guard_research_backtest_inputs"
    assert "BEFORE UPDATE ON research_backtest_runs" in runs_definition
    assert "FOR EACH ROW" in runs_definition

    cache_function, cache_definition = triggers[
        ("research_backtest_cache", "research_backtest_cache_immutable")
    ]
    assert cache_function == "reject_immutable_mutation"
    assert "BEFORE DELETE OR UPDATE ON research_backtest_cache" in cache_definition
    assert "FOR EACH STATEMENT" in cache_definition

    truncate_function, truncate_definition = triggers[
        ("research_backtest_cache", "research_backtest_cache_no_truncate")
    ]
    assert truncate_function == "reject_immutable_mutation"
    assert "BEFORE TRUNCATE ON research_backtest_cache" in truncate_definition
    assert "FOR EACH STATEMENT" in truncate_definition


_IMMUTABLE_INPUT_CASES = (
    ("id", "id = :other"),
    ("owner_id", "owner_id = :other"),
    ("idempotency_key", "idempotency_key = 'changed-by-test'"),
    ("request_hash", f"request_hash = '{OTHER_HASH_64}'"),
    ("config_hash", f"config_hash = '{OTHER_HASH_64}'"),
    ("snapshot_id", "snapshot_id = :other"),
    ("input_manifest", "input_manifest = jsonb_build_object('changed', true)"),
    ("created_at", "created_at = created_at + INTERVAL '1 hour'"),
    ("max_wall_clock_ms", "max_wall_clock_ms = 1"),
)


@pytest.mark.parametrize(
    ("column", "fragment"),
    _IMMUTABLE_INPUT_CASES,
    ids=[case[0] for case in _IMMUTABLE_INPUT_CASES],
)
def test_research_run_identity_and_input_columns_are_immutable(
    backtest_schema, column: str, fragment: str
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database, owner_id, publication_id = backtest_schema
    with database.engine.begin() as connection:
        row = _insert_run(connection, owner_id, publication_id)
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE research_backtest_runs SET {fragment} WHERE id = :run_id"
                ),
                {"run_id": row["run_id"], "other": uuid4()},
            )
    assert "research backtest inputs are immutable" in str(exc_info.value)


def test_lifecycle_columns_remain_mutable_through_all_states(backtest_schema) -> None:
    from sqlalchemy import text

    database, owner_id, publication_id = backtest_schema
    with database.engine.begin() as connection:
        row = _insert_run(connection, owner_id, publication_id)
        # queued -> running: claim metadata, lease and started_at are mutable.
        connection.execute(
            text(
                """
                UPDATE research_backtest_runs
                SET status = 'running', attempt_count = 1,
                    available_at = available_at + INTERVAL '1 minute',
                    lease_token = :lease_token, lease_owner = 'worker-1',
                    lease_expires_at = :now, started_at = :now
                WHERE id = :run_id
                """
            ),
            {"run_id": row["run_id"], "lease_token": uuid4(), "now": T10_NOW},
        )
        # running -> succeeded: lease cleared, result and completion recorded.
        connection.execute(
            text(
                """
                UPDATE research_backtest_runs
                SET status = 'succeeded', lease_token = NULL, lease_owner = NULL,
                    lease_expires_at = NULL, result = CAST(:result AS JSONB),
                    completed_at = :now
                WHERE id = :run_id
                """
            ),
            {
                "run_id": row["run_id"],
                "result": json.dumps({"schema_version": T10_ENGINE_VERSION}),
                "now": T10_NOW,
            },
        )
    with database.engine.connect() as connection:
        record = connection.execute(
            text(
                """
                SELECT status, attempt_count, result IS NOT NULL AS has_result,
                       failure IS NULL AS no_failure,
                       lease_token IS NULL AS lease_clear,
                       started_at IS NOT NULL AS has_started,
                       completed_at IS NOT NULL AS has_completed
                FROM research_backtest_runs WHERE id = :run_id
                """
            ),
            {"run_id": row["run_id"]},
        ).one()
    assert tuple(record) == ("succeeded", 1, True, True, True, True, True)

    # queued -> cancelled is an allowed terminal transition without result.
    with database.engine.begin() as connection:
        cancelled = _insert_run(connection, owner_id, publication_id)
        connection.execute(
            text(
                """
                UPDATE research_backtest_runs
                SET status = 'cancelled', completed_at = :now
                WHERE id = :run_id
                """
            ),
            {"run_id": cancelled["run_id"], "now": T10_NOW},
        )


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE research_backtest_cache "
        "SET result = jsonb_build_object('changed', true)",
        "DELETE FROM research_backtest_cache",
        "TRUNCATE TABLE research_backtest_cache",
    ),
    ids=("update", "delete", "truncate"),
)
def test_research_cache_rejects_every_mutation(backtest_schema, statement: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database, _owner_id, _publication_id = backtest_schema
    with database.engine.begin() as connection:
        _insert_cache(connection)
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            connection.execute(text(statement))
    assert "immutable records cannot be changed" in str(exc_info.value)
    with database.engine.connect() as connection:
        preserved = connection.scalar(
            text("SELECT count(*) FROM research_backtest_cache")
        )
    assert preserved == 1


@pytest.mark.parametrize(
    "status",
    ("queued", "running", "succeeded", "failed", "cancelled"),
)
def test_five_lifecycle_states_are_accepted(backtest_schema, status: str) -> None:
    from sqlalchemy import text

    database, owner_id, publication_id = backtest_schema
    with database.engine.begin() as connection:
        row = _insert_run(
            connection,
            owner_id,
            publication_id,
            status=status,
            **_state_overrides(status),
        )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM research_backtest_runs WHERE id = :run_id"),
                {"run_id": row["run_id"]},
            )
            == status
        )


_INVALID_STATE_CASES = (
    ("queued_with_lease", "queued", {"lease_token": uuid4()}),
    ("queued_with_completed_at", "queued", {"completed_at": T10_NOW}),
    (
        "queued_with_result",
        "queued",
        {"result": json.dumps({"unexpected": True})},
    ),
    (
        "running_without_lease_owner",
        "running",
        {
            "lease_token": uuid4(),
            "lease_expires_at": T10_NOW,
            "started_at": T10_NOW,
        },
    ),
    ("succeeded_without_result", "succeeded", {"completed_at": T10_NOW}),
    (
        "succeeded_with_failure",
        "succeeded",
        {
            "result": json.dumps({"ok": True}),
            "failure": json.dumps({"code": "x"}),
            "completed_at": T10_NOW,
        },
    ),
    (
        "failed_with_result",
        "failed",
        {
            "result": json.dumps({"ok": True}),
            "failure": json.dumps({"code": "x"}),
            "completed_at": T10_NOW,
        },
    ),
    (
        "cancelled_with_result",
        "cancelled",
        {
            "result": json.dumps({"ok": True}),
            "completed_at": T10_NOW,
        },
    ),
    (
        "cancelled_with_lease",
        "cancelled",
        {"lease_token": uuid4(), "completed_at": T10_NOW},
    ),
    ("unknown_status", "discarded", {"completed_at": T10_NOW}),
)


@pytest.mark.parametrize(
    ("case_name", "status", "overrides"),
    _INVALID_STATE_CASES,
    ids=[case[0] for case in _INVALID_STATE_CASES],
)
def test_invalid_lifecycle_combinations_are_rejected(
    backtest_schema, case_name: str, status: str, overrides: dict[str, object]
) -> None:
    from sqlalchemy.exc import DBAPIError

    database, owner_id, publication_id = backtest_schema
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            _insert_run(
                connection,
                owner_id,
                publication_id,
                status=status,
                **overrides,
            )
    assert "violates check constraint" in str(exc_info.value)


@pytest.mark.parametrize(
    ("column", "value", "constraint_fragment"),
    (
        ("max_wall_clock_ms", -1, "max_wall_clock_ms"),
        ("attempt_count", -1, "attempt_count"),
    ),
)
def test_budget_and_attempt_checks_reject_negative_values(
    backtest_schema, column: str, value: int, constraint_fragment: str
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database, owner_id, publication_id = backtest_schema
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            connection.execute(
                text(_RUN_INSERT_SQL),
                _run_values(owner_id, publication_id, **{column: value}),
            )
    assert constraint_fragment in str(exc_info.value)


def test_owner_idempotency_pair_is_unique(backtest_schema) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database, owner_id, publication_id = backtest_schema
    with database.engine.begin() as connection:
        first = _insert_run(connection, owner_id, publication_id)
        second = _run_values(
            owner_id,
            publication_id,
            idempotency_key=first["idempotency_key"],
            config_hash=OTHER_HASH_64,
        )
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(text(_RUN_INSERT_SQL), second)
    assert "violates unique constraint" in str(exc_info.value)


def test_run_requires_existing_owner_and_publication(backtest_schema) -> None:
    from sqlalchemy.exc import DBAPIError

    database, owner_id, publication_id = backtest_schema
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            _insert_run(connection, uuid4(), publication_id)
    assert "violates foreign key constraint" in str(exc_info.value)
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            _insert_run(connection, owner_id, uuid4())
    assert "violates foreign key constraint" in str(exc_info.value)


def test_cache_config_hash_is_unique(backtest_schema) -> None:
    from sqlalchemy.exc import DBAPIError

    database, _owner_id, _publication_id = backtest_schema
    with pytest.raises(DBAPIError) as exc_info:
        with database.engine.begin() as connection:
            _insert_cache(connection)
            _insert_cache(connection)
    assert "violates unique constraint" in str(exc_info.value)


def test_0014_research_downgrade_is_forbidden(database_url) -> None:
    from alembic import command
    from alembic.config import Config

    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with (
            pytest.raises(
                RuntimeError,
                match="Research backtest history cannot be downgraded",
            ),
            database.engine.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0013_query")
    finally:
        database.close()


# ---------------------------------------------------------------------------
# Task 11 — ResearchRunRepository: owner isolation, idempotency replay and
# conflict, config-hash cache reuse, lease claim/renew, cooperative cancel and
# structured budget failures. Every lifecycle rule runs on real PostgreSQL.
# ---------------------------------------------------------------------------

T11_BUDGET_MS = 300_000
T11_RESULT = {
    "schema_version": T10_ENGINE_VERSION,
    "research_only": True,
    "summary": {"total_return": "0.1250"},
}


def _research_session(owners):
    code = owners.issue_pairing()
    return owners.exchange_pairing(code, "https://localhost:8030")


@pytest.fixture
def run_repo(database, repository):
    from types import SimpleNamespace

    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.research.repository import ResearchRunRepository

    owners = OwnerRepository(database, clock=lambda: T10_NOW)
    owners.initialize()
    session = _research_session(owners)
    publication = _research_publish(database, repository, suffix="-t11")
    runs = ResearchRunRepository(
        database,
        owners,
        PublicationRepository(database, repository, clock=lambda: T10_NOW),
        clock=lambda: T10_NOW,
    )
    return SimpleNamespace(
        runs=runs,
        owners=owners,
        session=session,
        publication=publication,
    )


def _submission(snapshot_id, *, key=None, **config_changes):
    values = {"snapshot_id": snapshot_id}
    values.update(config_changes)
    return BacktestSubmission(
        config=config(**values),
        idempotency_key=key or uuid4(),
    )


def _second_owner_session(database, *, now=T10_NOW):
    import secrets
    from types import SimpleNamespace

    from sqlalchemy import insert

    from dragon_radar.owner.models import owner_principals, owner_sessions
    from dragon_radar.owner.repository import _hash

    owner_id = uuid4()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(id=owner_id, local_key=None, created_at=now)
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=owner_id,
                token_hash=_hash(token),
                csrf_hash=_hash(csrf),
                origin="https://localhost:8030",
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
    return SimpleNamespace(owner_id=owner_id, token=token, csrf=csrf)


# -- TR-11.1: idempotency, conflict and owner isolation ---------------------


def test_research_submit_persists_queued_run_and_replays_same_submission(
    run_repo,
) -> None:
    from sqlalchemy import select, text

    from dragon_radar.research.repository import DEFAULT_MAX_WALL_CLOCK_MS
    from dragon_radar.research.tables import research_backtest_runs

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    record = ctx.runs.submit(ctx.session.token, ctx.session.csrf, submission)

    assert record.status == "queued"
    assert record.attempt_count == 0
    assert record.max_wall_clock_ms == DEFAULT_MAX_WALL_CLOCK_MS == T11_BUDGET_MS
    assert record.owner_id == ctx.session.owner_id
    assert record.config == submission.config
    assert record.result is None
    assert record.failure is None
    assert record.started_at is None
    assert record.completed_at is None
    assert record.created_at == T10_NOW

    replay = ctx.runs.submit(ctx.session.token, ctx.session.csrf, submission)
    assert replay == record

    with ctx.runs.database.engine.connect() as connection:
        row = connection.execute(
            select(
                research_backtest_runs.c.request_hash,
                research_backtest_runs.c.config_hash,
                research_backtest_runs.c.idempotency_key,
                research_backtest_runs.c.max_wall_clock_ms,
            ).where(research_backtest_runs.c.id == record.id)
        ).one()
        assert row.request_hash == row.config_hash == submission.config.config_hash
        assert row.idempotency_key == str(submission.idempotency_key)
        assert row.max_wall_clock_ms == T11_BUDGET_MS
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 1
        )


def test_research_submit_conflicts_when_same_key_changes_config(run_repo) -> None:
    from sqlalchemy import text

    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    key = uuid4()
    first = ctx.runs.submit(
        ctx.session.token,
        ctx.session.csrf,
        _submission(ctx.publication.id, key=key),
    )
    changed = BacktestSubmission(
        config=config(
            snapshot_id=ctx.publication.id,
            parameters=parameters(top_n=11),
        ),
        idempotency_key=key,
    )
    with pytest.raises(Conflict):
        ctx.runs.submit(ctx.session.token, ctx.session.csrf, changed)

    stored = ctx.runs.get(ctx.session.token, first.id)
    assert stored.config.parameters.top_n == 10
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 1
        )


def test_research_get_is_owner_scoped_and_unknown_run_is_not_found(run_repo) -> None:
    from dragon_radar.storage.repository import NotFound

    ctx = run_repo
    record = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    assert ctx.runs.get(ctx.session.token, record.id) == record

    other = _second_owner_session(ctx.runs.database)
    with pytest.raises(NotFound):
        ctx.runs.get(other.token, record.id)
    with pytest.raises(NotFound):
        ctx.runs.get(ctx.session.token, uuid4())
    with pytest.raises(ValueError, match="Invalid record ID"):
        ctx.runs.get(ctx.session.token, "not-a-uuid")


@pytest.mark.parametrize("bad_csrf", (None, "", "wrong"))
def test_research_submit_requires_session_bound_csrf(run_repo, bad_csrf) -> None:
    from sqlalchemy import text

    from dragon_radar.owner import CsrfForbidden

    ctx = run_repo
    with pytest.raises(CsrfForbidden):
        ctx.runs.submit(
            ctx.session.token,
            bad_csrf,
            _submission(ctx.publication.id),
        )
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 0
        )


def test_research_readonly_repository_fails_closed_for_owner_methods(run_repo) -> None:
    from dragon_radar.research.repository import ResearchRunRepository
    from dragon_radar.storage.repository import Forbidden

    ctx = run_repo
    readonly = ResearchRunRepository(
        ctx.runs.database,
        None,
        ctx.runs.publications,
        clock=lambda: T10_NOW,
    )
    with pytest.raises(Forbidden):
        readonly.submit(
            ctx.session.token,
            ctx.session.csrf,
            _submission(ctx.publication.id),
        )
    with pytest.raises(Forbidden):
        readonly.get(ctx.session.token, uuid4())
    with pytest.raises(Forbidden):
        readonly.cancel(ctx.session.token, ctx.session.csrf, uuid4())


def test_research_submit_rechecks_publication_is_published(run_repo) -> None:
    from sqlalchemy import update

    from dragon_radar.publication.tables import publications as publications_table
    from dragon_radar.storage.repository import Forbidden, NotFound

    ctx = run_repo
    with pytest.raises(NotFound):
        ctx.runs.submit(
            ctx.session.token,
            ctx.session.csrf,
            _submission(uuid4()),
        )

    with ctx.runs.database.engine.begin() as connection:
        connection.execute(
            update(publications_table)
            .where(publications_table.c.id == ctx.publication.id)
            .values(status="revoked")
        )
    with pytest.raises(Forbidden):
        ctx.runs.submit(
            ctx.session.token,
            ctx.session.csrf,
            _submission(ctx.publication.id),
        )


def test_research_config_hash_cache_reuses_result_across_owners(run_repo) -> None:
    from sqlalchemy import inspect, text

    from dragon_radar.storage.repository import NotFound

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    first = ctx.runs.submit(ctx.session.token, ctx.session.csrf, submission)
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None
    assert lease.run.id == first.id
    ctx.runs.succeed(lease, T11_RESULT)

    other = _second_owner_session(ctx.runs.database)
    cached = ctx.runs.submit(
        other.token,
        other.csrf,
        _submission(ctx.publication.id),
    )
    assert cached.owner_id == other.owner_id
    assert cached.id != first.id
    assert cached.status == "succeeded"
    assert cached.result == T11_RESULT
    assert cached.completed_at == T10_NOW
    with pytest.raises(NotFound):
        ctx.runs.get(ctx.session.token, cached.id)

    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 1
        )
        cache_columns = {
            column["name"]
            for column in inspect(connection).get_columns("research_backtest_cache")
        }
        assert "owner_id" not in cache_columns


def test_research_submit_for_owner_requires_caller_transaction(run_repo) -> None:
    ctx = run_repo
    submission = _submission(ctx.publication.id)
    with ctx.runs.database.engine.connect() as connection:
        with pytest.raises(ValueError, match="caller transaction"):
            ctx.runs.submit_for_owner(
                connection,
                ctx.session.owner_id,
                submission,
            )


# -- TR-11.2: cooperative cancellation --------------------------------------


def test_research_cancel_queued_run_is_immediate_without_result(run_repo) -> None:
    from sqlalchemy import select

    from dragon_radar.research.tables import research_backtest_runs

    ctx = run_repo
    record = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    cancelled = ctx.runs.cancel(ctx.session.token, ctx.session.csrf, record.id)

    assert cancelled.status == "cancelled"
    assert cancelled.result is None
    assert cancelled.failure is None
    assert cancelled.started_at is None
    assert cancelled.completed_at == T10_NOW

    with ctx.runs.database.engine.connect() as connection:
        row = connection.execute(
            select(
                research_backtest_runs.c.status,
                research_backtest_runs.c.lease_token,
                research_backtest_runs.c.lease_owner,
                research_backtest_runs.c.lease_expires_at,
                research_backtest_runs.c.result,
                research_backtest_runs.c.failure,
            ).where(research_backtest_runs.c.id == record.id)
        ).one()
        assert tuple(row) == ("cancelled", None, None, None, None, None)


def test_research_cancel_running_run_invalidates_lease_and_blocks_completion(
    run_repo,
) -> None:
    from sqlalchemy import select, text

    from dragon_radar.research.tables import research_backtest_runs
    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None
    cancelled = ctx.runs.cancel(ctx.session.token, ctx.session.csrf, lease.run.id)
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at == T10_NOW

    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.succeed(lease, T11_RESULT)
    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.renew(lease)
    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.fail(lease, {"code": "engine_failed"})

    with ctx.runs.database.engine.connect() as connection:
        row = connection.execute(
            select(
                research_backtest_runs.c.status,
                research_backtest_runs.c.result,
                research_backtest_runs.c.failure,
                research_backtest_runs.c.lease_token,
            ).where(research_backtest_runs.c.id == lease.run.id)
        ).one()
        assert tuple(row) == ("cancelled", None, None, None)
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 0
        )


@pytest.mark.parametrize("terminal", ("succeeded", "failed", "cancelled"))
def test_research_cancel_rejects_terminal_runs(run_repo, terminal) -> None:
    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    record = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    if terminal == "succeeded":
        lease = ctx.runs.claim("research-worker-1")
        ctx.runs.succeed(lease, T11_RESULT)
    elif terminal == "failed":
        lease = ctx.runs.claim("research-worker-1")
        ctx.runs.fail(lease, {"code": "engine_failed"})
    else:
        ctx.runs.cancel(ctx.session.token, ctx.session.csrf, record.id)

    with pytest.raises(Conflict):
        ctx.runs.cancel(ctx.session.token, ctx.session.csrf, record.id)


def test_research_cancel_is_owner_scoped_and_requires_csrf(run_repo) -> None:
    from dragon_radar.owner import CsrfForbidden
    from dragon_radar.storage.repository import NotFound

    ctx = run_repo
    record = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    other = _second_owner_session(ctx.runs.database)
    with pytest.raises(NotFound):
        ctx.runs.cancel(other.token, other.csrf, record.id)
    with pytest.raises(CsrfForbidden):
        ctx.runs.cancel(ctx.session.token, "", record.id)
    with pytest.raises(NotFound):
        ctx.runs.cancel(ctx.session.token, ctx.session.csrf, uuid4())

    assert ctx.runs.get(ctx.session.token, record.id).status == "queued"


# -- TR-11.3: leases, attempt limits and structured budget failure ----------


def test_research_claim_issues_lease_and_empties_pool(run_repo) -> None:
    ctx = run_repo
    assert ctx.runs.claim("research-worker-1") is None

    record = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None
    assert isinstance(lease.token, UUID)
    assert lease.worker_id == "research-worker-1"
    assert lease.expires_at == T10_NOW + timedelta(seconds=30)
    assert lease.run.id == record.id
    assert lease.run.status == "running"
    assert lease.run.attempt_count == 1
    assert lease.run.started_at == T10_NOW
    assert lease.run.max_wall_clock_ms == T11_BUDGET_MS
    assert lease.run.result is None
    assert ctx.runs.claim("research-worker-2") is None

    with pytest.raises(ValueError, match="Identifier"):
        ctx.runs.claim("bad worker id")


def test_research_expired_lease_is_reclaimable_but_stale_worker_cannot_finish(
    run_repo,
) -> None:
    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    first = ctx.runs.claim("research-worker-1", lease_for=timedelta(seconds=10))
    assert first is not None
    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=11)
    second = ctx.runs.claim("research-worker-2", lease_for=timedelta(seconds=10))
    assert second is not None
    assert second.run.id == first.run.id
    assert second.token != first.token
    assert second.run.attempt_count == 2

    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.succeed(first, T11_RESULT)
    completed = ctx.runs.succeed(second, T11_RESULT)
    assert completed.status == "succeeded"


def test_research_claim_validates_policy_and_fails_exhausted_run(run_repo) -> None:
    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    submitted = ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    for invalid in (True, 0, 11, "3"):
        with pytest.raises(ValueError, match="max attempts"):
            ctx.runs.claim("research-worker", max_attempts=invalid)
    for invalid_lease in (timedelta(seconds=0), timedelta(hours=2), 30):
        with pytest.raises(ValueError, match="lease duration"):
            ctx.runs.claim("research-worker", lease_for=invalid_lease)

    first = ctx.runs.claim(
        "research-worker-1",
        lease_for=timedelta(seconds=10),
        max_attempts=2,
    )
    assert first is not None
    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=11)
    second = ctx.runs.claim(
        "research-worker-2",
        lease_for=timedelta(seconds=10),
        max_attempts=2,
    )
    assert second is not None
    assert second.run.attempt_count == 2
    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=22)
    assert (
        ctx.runs.claim(
            "research-worker-3",
            lease_for=timedelta(seconds=10),
            max_attempts=2,
        )
        is None
    )

    exhausted = ctx.runs.get(ctx.session.token, submitted.id)
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 2
    assert exhausted.failure == {"code": "attempts_exhausted"}
    assert exhausted.result is None
    assert exhausted.completed_at == T10_NOW + timedelta(seconds=22)
    with pytest.raises(Conflict):
        ctx.runs.succeed(second, T11_RESULT)


def test_research_renew_keeps_active_lease_and_rejects_expired(run_repo) -> None:
    from dragon_radar.storage.repository import Conflict

    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1", lease_for=timedelta(seconds=10))
    assert lease is not None

    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=6)
    renewed = ctx.runs.renew(lease, lease_for=timedelta(seconds=10))
    assert renewed.token == lease.token
    assert renewed.worker_id == lease.worker_id
    assert renewed.expires_at == T10_NOW + timedelta(seconds=16)

    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=17)
    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.renew(renewed, lease_for=timedelta(seconds=10))


def test_research_budget_failure_is_structured_without_half_result(run_repo) -> None:
    from sqlalchemy import select

    from dragon_radar.research.tables import research_backtest_runs

    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None
    failure = {
        "code": "budget_exceeded",
        "budget_ms": T11_BUDGET_MS,
        "elapsed_ms": T11_BUDGET_MS + 1,
    }
    failed = ctx.runs.fail(lease, failure)

    assert failed.status == "failed"
    assert failed.result is None
    assert failed.failure == failure
    assert failed.completed_at == T10_NOW
    with ctx.runs.database.engine.connect() as connection:
        row = connection.execute(
            select(
                research_backtest_runs.c.result,
                research_backtest_runs.c.failure,
                research_backtest_runs.c.lease_token,
                research_backtest_runs.c.lease_owner,
                research_backtest_runs.c.lease_expires_at,
            ).where(research_backtest_runs.c.id == lease.run.id)
        ).one()
        assert tuple(row) == (None, failure, None, None, None)
    assert ctx.runs.claim("research-worker-2") is None


def test_research_load_input_returns_authorized_publication(run_repo) -> None:
    from sqlalchemy import update

    from dragon_radar.publication.tables import publications as publications_table
    from dragon_radar.storage.repository import Conflict, Forbidden

    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None
    snapshot = ctx.runs.load_input(lease)
    assert snapshot.id == ctx.publication.id

    ctx.runs.cancel(ctx.session.token, ctx.session.csrf, lease.run.id)
    with pytest.raises(Conflict, match="no longer active"):
        ctx.runs.load_input(lease)

    ctx.runs.submit(
        ctx.session.token,
        ctx.session.csrf,
        _submission(ctx.publication.id, key=uuid4()),
    )
    second_lease = ctx.runs.claim("research-worker-2")
    assert second_lease is not None
    with ctx.runs.database.engine.begin() as connection:
        connection.execute(
            update(publications_table)
            .where(publications_table.c.id == ctx.publication.id)
            .values(status="revoked")
        )
    with pytest.raises(Forbidden):
        ctx.runs.load_input(second_lease)


def test_research_succeed_pins_immutable_cache_and_authoritative_result(
    run_repo,
) -> None:
    from sqlalchemy import select, text

    from dragon_radar.research.tables import (
        research_backtest_cache,
        research_backtest_runs,
    )

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    first = ctx.runs.submit(ctx.session.token, ctx.session.csrf, submission)
    second = ctx.runs.submit(
        ctx.session.token,
        ctx.session.csrf,
        BacktestSubmission(
            config=submission.config,
            idempotency_key=uuid4(),
        ),
    )
    assert first.id != second.id

    first_lease = ctx.runs.claim("research-worker-1")
    assert first_lease is not None
    completed_first = ctx.runs.succeed(first_lease, T11_RESULT)
    assert completed_first.result == T11_RESULT

    second_lease = ctx.runs.claim("research-worker-2")
    assert second_lease is not None
    competing_result = {
        "schema_version": T10_ENGINE_VERSION,
        "summary": {"total_return": "9.9999"},
    }
    completed_second = ctx.runs.succeed(second_lease, competing_result)
    assert completed_second.result == T11_RESULT

    with ctx.runs.database.engine.connect() as connection:
        cache_row = (
            connection.execute(
                select(research_backtest_cache).where(
                    research_backtest_cache.c.config_hash
                    == submission.config.config_hash
                )
            )
            .mappings()
            .one()
        )
        assert cache_row.engine_version == T10_ENGINE_VERSION
        assert cache_row.result == T11_RESULT
        assert cache_row.input_manifest == {
            "config": submission.config.model_dump(mode="json")
        }
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 1
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 2
        )
        run_statuses = (
            connection.execute(select(research_backtest_runs.c.status)).scalars().all()
        )
        assert run_statuses == ["succeeded", "succeeded"]


def test_research_result_and_failure_must_be_json_objects(run_repo) -> None:
    ctx = run_repo
    ctx.runs.submit(
        ctx.session.token, ctx.session.csrf, _submission(ctx.publication.id)
    )
    lease = ctx.runs.claim("research-worker-1")
    assert lease is not None

    with pytest.raises(ValueError, match="object"):
        ctx.runs.succeed(lease, ["not", "an", "object"])
    with pytest.raises(ValueError, match="object"):
        ctx.runs.fail(lease, "not-an-object")


# ---------------------------------------------------------------------------
# Task 12 — EX14/EX15 owner-only HTTP routes: mount boundary, session/CSRF
# gates, field whitelist, idempotency/conflict, principal isolation and the
# full EX15 research_only envelope. Every case runs on real PostgreSQL.
# ---------------------------------------------------------------------------

T12_ORIGIN = "https://localhost:8030"
T12_PATH = "/api/research/backtests"
T12_SESSION_COOKIE = "__Host-dragon-private-session"
T12_CSRF_COOKIE = "__Host-dragon-private-csrf"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _t12_headers(session) -> dict[str, str]:
    return {
        "Origin": T12_ORIGIN,
        "X-CSRF-Token": session.csrf,
        "Cookie": (
            f"{T12_SESSION_COOKIE}={session.token}; {T12_CSRF_COOKIE}={session.csrf}"
        ),
    }


def _ex14_body(snapshot_id, *, key=None, **config_changes) -> dict:
    submission = _submission(snapshot_id, key=key, **config_changes)
    body = submission.config.model_dump(mode="json")
    body["idempotency_key"] = str(submission.idempotency_key)
    return body


def _ex15_result(submission) -> dict:
    # Minimal object that still carries every EX15 envelope key; the route is
    # a thin materialized-result reader and must validate transport, not the
    # engine schema (Task 13 owns producing the real BacktestResult).
    return {
        "schema_version": T10_ENGINE_VERSION,
        "research_only": True,
        "config_hash": submission.config.config_hash,
        "snapshot_id": str(submission.config.snapshot_id),
        "manifests": submission.config.input_manifests.model_dump(mode="json"),
        "versions": {"strategy_id": submission.config.strategy_id},
        "metrics": {"total_return": "0.1250"},
        "equity": [{"trade_date": "2025-01-06", "equity": "1000000.00"}],
        "trades": [],
        "rejections": [],
        "quality": [],
        "folds": None,
        "walk_forward": None,
        "baseline": {
            "status": "completed",
            "metrics": {"total_return": "0.1250"},
            "cost_sensitivity": [],
        },
        "ablation": None,
    }


def _research_owner_app(ctx):
    from dragon_radar.owner.app import create_owner_app

    return create_owner_app(
        repository=ctx.runs.publications.market,
        owner_repository=ctx.owners,
        publication_repository=ctx.runs.publications,
        research_repository=ctx.runs,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_research_http_requires_real_session(run_repo, method) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        if method == "POST":
            # A browser-shaped write carries Origin; with no session cookie the
            # gate must still fail closed at session authorization (401), not
            # at CSRF/origin enforcement (403).
            response = await client.post(
                T12_PATH,
                json=_ex14_body(ctx.publication.id),
                headers={"Origin": T12_ORIGIN},
            )
        else:
            response = await client.get(f"{T12_PATH}/{uuid4()}")
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        "missing_csrf_header",
        "wrong_csrf_header",
    ],
)
async def test_research_http_post_requires_matching_csrf(run_repo, headers) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    session = ctx.session
    if headers == "missing_csrf_header":
        bad_headers = {
            "Origin": T12_ORIGIN,
            "Cookie": (
                f"{T12_SESSION_COOKIE}={session.token}; "
                f"{T12_CSRF_COOKIE}={session.csrf}"
            ),
        }
    else:
        bad_headers = {
            "Origin": T12_ORIGIN,
            "X-CSRF-Token": "other-csrf-token",
            "Cookie": (
                f"{T12_SESSION_COOKIE}={session.token}; "
                f"{T12_CSRF_COOKIE}={session.csrf}"
            ),
        }
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.post(
            T12_PATH,
            json=_ex14_body(ctx.publication.id),
            headers=bad_headers,
        )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_research_http_post_submits_queued_run_and_replays(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        assert created.status_code == 200, created.text
        assert set(created.json()) == {"run_id", "status"}
        assert created.json()["status"] == "queued"

        replay = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        assert replay.status_code == 200
        assert replay.json() == created.json()

    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 1
        )


@pytest.mark.anyio
async def test_research_http_post_conflicts_on_same_key_changed_config(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    changed = {
        **body,
        "parameters": {**body["parameters"], "top_n": 11},
    }
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        assert created.status_code == 200
        conflict = await client.post(
            T12_PATH, json=changed, headers=_t12_headers(ctx.session)
        )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "VERSION_CONFLICT",
        "message": "Version conflict",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    [
        "unknown_field",
        "query_string",
        "top_n_out_of_bounds",
        "bad_idempotency_key",
        "missing_snapshot_id",
    ],
)
async def test_research_http_post_rejects_invalid_requests(run_repo, case) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    path = T12_PATH
    if case == "unknown_field":
        payload = {**body, "unexpected": "x"}
    elif case == "query_string":
        payload = body
        path = f"{T12_PATH}?unexpected=1"
    elif case == "top_n_out_of_bounds":
        payload = {
            **body,
            "parameters": {**body["parameters"], "top_n": 101},
        }
    elif case == "bad_idempotency_key":
        payload = {**body, "idempotency_key": "not-a-uuid"}
    else:
        payload = dict(body)
        payload.pop("snapshot_id")
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.post(
            path, json=payload, headers=_t12_headers(ctx.session)
        )
    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_PARAM",
        "message": "Invalid request",
    }


@pytest.mark.anyio
async def test_research_http_get_returns_ex15_envelope_with_research_only(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    body = _ex14_body(ctx.publication.id)
    body = {
        **submission.config.model_dump(mode="json"),
        "idempotency_key": str(submission.idempotency_key),
    }
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]

        lease = ctx.runs.claim("research-worker-1")
        assert lease is not None
        ctx.runs.succeed(lease, _ex15_result(submission))

        fetched = await client.get(
            f"{T12_PATH}/{run_id}", headers=_t12_headers(ctx.session)
        )
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()
    assert set(payload) == {
        "run_id",
        "status",
        "config_hash",
        "research_only",
        "result",
        "failure",
    }
    assert payload["run_id"] == run_id
    assert payload["status"] == "succeeded"
    assert payload["research_only"] is True
    assert payload["config_hash"] == submission.config.config_hash
    assert payload["failure"] is None

    result = payload["result"]
    for key in (
        "config_hash",
        "manifests",
        "metrics",
        "trades",
        "rejections",
        "equity",
        "folds",
        "baseline",
        "ablation",
        "quality",
    ):
        assert key in result, f"EX15 field missing: {key}"
    assert result["research_only"] is True
    assert result["config_hash"] == submission.config.config_hash
    # Monetary values stay Decimal strings through the JSONB round trip.
    assert result["metrics"]["total_return"] == "0.1250"
    assert isinstance(result["equity"][0]["equity"], str)

    for secret in (
        ctx.session.token,
        ctx.session.csrf,
        str(ctx.session.owner_id),
        "lease",
        "request_hash",
        "input_manifest",
        "attempt_count",
    ):
        assert secret not in fetched.text


@pytest.mark.anyio
async def test_research_http_get_queued_run_returns_null_result(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        run_id = created.json()["run_id"]
        first = await client.get(
            f"{T12_PATH}/{run_id}", headers=_t12_headers(ctx.session)
        )
        assert first.status_code == 200
        payload = first.json()
        assert payload["status"] == "queued"
        assert payload["result"] is None
        assert payload["failure"] is None
        assert payload["research_only"] is True
        assert isinstance(payload["config_hash"], str)

        # Lightweight read: no worker exists, polling must never recompute.
        second = await client.get(
            f"{T12_PATH}/{run_id}", headers=_t12_headers(ctx.session)
        )
        assert second.json() == payload


@pytest.mark.anyio
async def test_research_http_get_isolates_owners_and_maps_unknown_ids(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    other = _second_owner_session(ctx.runs.database)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        run_id = created.json()["run_id"]

        isolated = await client.get(f"{T12_PATH}/{run_id}", headers=_t12_headers(other))
        assert isolated.status_code == 404
        assert isolated.json() == {
            "code": "NOT_FOUND",
            "message": "Resource not found",
        }

        missing = await client.get(
            f"{T12_PATH}/{uuid4()}", headers=_t12_headers(ctx.session)
        )
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "NOT_FOUND",
            "message": "Resource not found",
        }

        bad_id = await client.get(
            f"{T12_PATH}/not-a-uuid", headers=_t12_headers(ctx.session)
        )
        assert bad_id.status_code == 400
        assert bad_id.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }


# -- Task 16 — cooperative cancel over HTTP (EX14 command surface) ----------


def _cancel_url(run_id) -> str:
    return f"{T12_PATH}/{run_id}/cancel"


@pytest.mark.anyio
async def test_research_http_cancel_queued_run_returns_cancelled_envelope(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH,
            json=_ex14_body(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["run_id"]

        cancelled = await client.post(
            _cancel_url(run_id),
            json={"idempotency_key": str(uuid4())},
            headers=_t12_headers(ctx.session),
        )
    assert cancelled.status_code == 200, cancelled.text
    payload = cancelled.json()
    assert set(payload) == {
        "run_id",
        "status",
        "config_hash",
        "research_only",
        "result",
        "failure",
    }
    assert payload["run_id"] == run_id
    assert payload["status"] == "cancelled"
    assert payload["research_only"] is True
    assert payload["result"] is None
    assert payload["failure"] is None

    # The next polling read observes the terminal cancelled state without any
    # recomputation, which is what closes the Task 17 submit/poll/cancel loop.
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        fetched = await client.get(
            f"{T12_PATH}/{run_id}", headers=_t12_headers(ctx.session)
        )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "cancelled"
    assert fetched.json()["result"] is None


@pytest.mark.anyio
async def test_research_http_cancel_requires_real_session(run_repo) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.post(
            _cancel_url(uuid4()),
            json={"idempotency_key": str(uuid4())},
            headers={"Origin": T12_ORIGIN},
        )
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    ["missing_csrf_header", "wrong_csrf_header"],
)
async def test_research_http_cancel_requires_matching_csrf(run_repo, headers) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    body = _ex14_body(ctx.publication.id)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH, json=body, headers=_t12_headers(ctx.session)
        )
        run_id = created.json()["run_id"]
        if headers == "missing_csrf_header":
            bad_headers = {
                "Origin": T12_ORIGIN,
                "Cookie": (
                    f"{T12_SESSION_COOKIE}={ctx.session.token}; "
                    f"{T12_CSRF_COOKIE}={ctx.session.csrf}"
                ),
            }
        else:
            bad_headers = {
                "Origin": T12_ORIGIN,
                "X-CSRF-Token": "other-csrf-token",
                "Cookie": (
                    f"{T12_SESSION_COOKIE}={ctx.session.token}; "
                    f"{T12_CSRF_COOKIE}={ctx.session.csrf}"
                ),
            }
        response = await client.post(
            _cancel_url(run_id),
            json={"idempotency_key": str(uuid4())},
            headers=bad_headers,
        )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_research_http_cancel_rejects_terminal_run_with_conflict(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    submission = _submission(ctx.publication.id)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH,
            json=_ex14_body(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
        run_id = created.json()["run_id"]
        lease = ctx.runs.claim("research-worker-1")
        assert lease is not None
        ctx.runs.succeed(lease, _ex15_result(submission))

        conflict = await client.post(
            _cancel_url(run_id),
            json={"idempotency_key": str(uuid4())},
            headers=_t12_headers(ctx.session),
        )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "VERSION_CONFLICT",
        "message": "Version conflict",
    }


@pytest.mark.anyio
async def test_research_http_cancel_unknown_id_returns_not_found(run_repo) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.post(
            _cancel_url(uuid4()),
            json={"idempotency_key": str(uuid4())},
            headers=_t12_headers(ctx.session),
        )
    assert response.status_code == 404
    assert response.json() == {
        "code": "NOT_FOUND",
        "message": "Resource not found",
    }


@pytest.mark.anyio
async def test_research_http_cancel_isolates_owners(run_repo) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    other = _second_owner_session(ctx.runs.database)
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH,
            json=_ex14_body(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
        run_id = created.json()["run_id"]
        isolated = await client.post(
            _cancel_url(run_id),
            json={"idempotency_key": str(uuid4())},
            headers=_t12_headers(other),
        )
    assert isolated.status_code == 404
    assert ctx.runs.get(ctx.session.token, run_id).status == "queued"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case",
    ["missing_key", "unknown_field", "query_string", "non_string_key"],
)
async def test_research_http_cancel_rejects_malformed_requests(run_repo, case) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        created = await client.post(
            T12_PATH,
            json=_ex14_body(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
        run_id = created.json()["run_id"]
        url = _cancel_url(run_id)
        if case == "missing_key":
            payload: object = {}
        elif case == "unknown_field":
            payload = {"idempotency_key": str(uuid4()), "unexpected": "x"}
        elif case == "non_string_key":
            payload = {"idempotency_key": 123}
        else:
            payload = {"idempotency_key": str(uuid4())}
            url = f"{url}?unexpected=1"
        response = await client.post(
            url, json=payload, headers=_t12_headers(ctx.session)
        )
    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_PARAM",
        "message": "Invalid request",
    }


@pytest.mark.anyio
async def test_research_owner_app_factory_constructs_research_repository(
    database_url,
) -> None:
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.research.repository import ResearchRunRepository

    owner = create_owner_app(database_url=database_url)
    try:
        assert isinstance(owner.state.research_repository, ResearchRunRepository)
        assert owner.state.research_repository.owners is owner.state.owner_repository
        owner_paths = {getattr(route, "path", None) for route in owner.routes}
        assert T12_PATH in owner_paths
        assert f"{T12_PATH}/{{run_id}}" in owner_paths
        assert T17_MANIFEST_PATH in owner_paths
        async with owner.router.lifespan_context(owner):
            pass
    finally:
        pass

    workbench = create_workbench_app(database_url=database_url)
    try:
        assert getattr(workbench.state, "research_repository", None) is None
        workbench_paths = {getattr(route, "path", None) for route in workbench.routes}
        assert T12_PATH not in workbench_paths
        assert f"{T12_PATH}/{{run_id}}" not in workbench_paths
        assert T17_MANIFEST_PATH not in workbench_paths
        async with workbench.router.lifespan_context(workbench):
            pass
    finally:
        pass


# ---------------------------------------------------------------------------
# Task 13 — asynchronous research backtest worker. Part A pins the pure-kernel
# cooperative checkpoint hooks (no IO, no clock): run_backtest/run_walk_forward
# gain a keyword-only, default-noop checkpoint callable threaded through every
# cost-tier simulation and every fold boundary. Part B pins the lease-driven
# ResearchBacktestWorker on real PostgreSQL: end-to-end success, heartbeat
# renewal, cooperative cancellation, wall-clock/computation budget gates,
# structured failures and pool-level retry isolation.
# ---------------------------------------------------------------------------


class _T13CooperativeStop(RuntimeError):
    """Synthetic checkpoint raise used to prove cooperative mid-run stopping."""


# -- Part A: pure kernel checkpoint hooks -----------------------------------


def test_research_engine_checkpoint_fires_once_per_decision_iteration_per_tier() -> (
    None
):
    from dragon_radar.research import models as research_models

    calls: list[None] = []
    cfg = _eng_config()
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    factor_frames = _factor_frames(ENG_VALUES_BY_DAY)

    run_backtest(
        cfg,
        frames,
        factor_frames,
        anchor=_eng_anchor(),
        policy=UniversePolicy(),
        checkpoint=lambda: calls.append(None),
    )

    decision_iterations = len(ENG_DAYS) - 1
    assert len(calls) == decision_iterations * len(
        research_models.COST_SENSITIVITY_TIERS
    )
    assert len(calls) == 12


def test_research_engine_checkpoint_defaults_to_noop_and_keeps_result_identical() -> (
    None
):
    cfg = _eng_config()
    frames = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    factor_frames = _factor_frames(ENG_VALUES_BY_DAY)
    kwargs = {
        "anchor": _eng_anchor(),
        "policy": UniversePolicy(),
    }

    baseline = _result_json(run_backtest(cfg, frames, factor_frames, **kwargs))
    explicit_noop = _result_json(
        run_backtest(
            cfg,
            frames,
            factor_frames,
            checkpoint=lambda: None,
            **kwargs,
        )
    )

    assert explicit_noop == baseline


def test_research_engine_checkpoint_propagates_cooperative_stop() -> None:
    calls: list[None] = []

    def checkpoint() -> None:
        calls.append(None)
        if len(calls) == 5:
            raise _T13CooperativeStop

    with pytest.raises(_T13CooperativeStop):
        run_backtest(
            _eng_config(),
            _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
            _factor_frames(ENG_VALUES_BY_DAY),
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
            checkpoint=checkpoint,
        )

    # The raise must stop the engine immediately: no later tier runs.
    assert len(calls) == 5


def test_research_walk_forward_checkpoint_covers_fold_boundaries_and_decisions() -> (
    None
):
    from dragon_radar.research.models import COST_SENSITIVITY_TIERS

    cfg = _wf_config()
    frames = _wf_frames(_wf_closes())
    factor_frames = _wf_factor_frames()
    calls: list[None] = []

    run_backtest(
        cfg,
        frames,
        factor_frames,
        anchor=_wf_anchor(),
        policy=UniversePolicy(),
        phase_publications=_wf_phases(),
        checkpoint=lambda: calls.append(None),
    )

    spec = WF_SPEC
    fold_decisions = spec.folds * (spec.test_sessions - 1)
    aggregate_decisions = spec.folds * spec.test_sessions - 1
    walk_forward_decisions = fold_decisions + aggregate_decisions
    tier_decisions = len(WF_DAYS) - 1
    expected = (
        spec.folds
        + walk_forward_decisions
        + tier_decisions * len(COST_SENSITIVITY_TIERS)
    )
    # 2 fold boundaries + (2 fold decisions + 3 aggregate decisions)
    # + 3 tiers x 7 full-window decisions = 28.
    assert expected == 28
    assert len(calls) == expected

    # Raising at the fold-1 boundary (3rd call) prevents that fold's
    # simulation and every later stage from running.
    stopping: list[None] = []

    def stop_at_fold_one() -> None:
        stopping.append(None)
        if len(stopping) == 3:
            raise _T13CooperativeStop

    with pytest.raises(_T13CooperativeStop):
        run_backtest(
            cfg,
            frames,
            factor_frames,
            anchor=_wf_anchor(),
            policy=UniversePolicy(),
            phase_publications=_wf_phases(),
            checkpoint=stop_at_fold_one,
        )
    assert len(stopping) == 3


def test_research_compute_budget_accepts_legal_configs_and_rejects_drift(
    monkeypatch,
) -> None:
    from dragon_radar.research import models as research_models
    from dragon_radar.research.worker import (
        ComputeBudgetExceeded,
        assert_compute_budget,
    )

    assert assert_compute_budget(_eng_config()) is None

    # The maximal walk-forward needs 50 + 10 x 20 = 250 sessions (the model
    # cap), so the default 172-day fixture period cannot host it; widen the
    # span to the exact boundary while every frozen constant stays at max.
    maximal = config(
        parameters=parameters(top_n=100, rebalance_period=60),
        period=period(start=date(2025, 1, 6), end=date(2025, 9, 13)),
        walk_forward=walk_forward(folds=10, train_sessions=50, test_sessions=20),
    )
    assert assert_compute_budget(maximal) is None

    monkeypatch.setattr(research_models, "MAX_TOP_N", 1)
    with pytest.raises(ComputeBudgetExceeded) as caught:
        assert_compute_budget(_eng_config())
    assert caught.value.dimension == "top_n"
    assert caught.value.limit == 1
    assert caught.value.actual == 2

    monkeypatch.setattr(research_models, "MAX_REBALANCE_PERIOD_SESSIONS", 1)
    with pytest.raises(ComputeBudgetExceeded) as caught:
        assert_compute_budget(_eng_config())
    assert caught.value.dimension == "rebalance_period"

    monkeypatch.setattr(research_models, "MAX_WALK_FORWARD_FOLDS", 1)
    with pytest.raises(ComputeBudgetExceeded) as caught:
        assert_compute_budget(
            config(
                walk_forward=walk_forward(folds=2, train_sessions=4, test_sessions=2)
            )
        )
    assert caught.value.dimension == "walk_forward_folds"


# -- Part B: lease-driven worker on real PostgreSQL -------------------------


def _t13_submit(ctx, **changes):
    return ctx.runs.submit(
        ctx.session.token,
        ctx.session.csrf,
        _submission(ctx.publication.id, **changes),
    )


def _t13_worker(ctx, compute, **kwargs):
    from dragon_radar.research.worker import ResearchBacktestWorker

    kwargs.setdefault("worker_id", "research-worker-1")
    return ResearchBacktestWorker(ctx.runs, compute=compute, **kwargs)


def _t13_ok_compute():
    def compute(snapshot, config, checkpoint):
        assert snapshot is not None
        assert callable(checkpoint)
        checkpoint()
        return dict(T11_RESULT)

    return compute


def test_research_worker_run_once_returns_none_when_queue_empty(run_repo) -> None:
    worker = _t13_worker(run_repo, _t13_ok_compute())
    assert worker.run_once() is None


def test_research_worker_run_forever_only_sleeps_when_idle(
    run_repo, monkeypatch
) -> None:
    worker = _t13_worker(run_repo, _t13_ok_compute())
    results = iter((object(), None, object()))
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "run_once", lambda: next(results))

    worker.run_forever(poll_seconds=0.25, sleep=sleeps.append, max_iterations=3)

    assert sleeps == [0.25]


@pytest.mark.parametrize("interrupt_at", ("run", "sleep"))
def test_research_worker_run_forever_returns_cleanly_on_keyboard_interrupt(
    run_repo, monkeypatch, interrupt_at
) -> None:
    worker = _t13_worker(run_repo, _t13_ok_compute())

    def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        worker,
        "run_once",
        interrupt if interrupt_at == "run" else lambda: None,
    )

    worker.run_forever(
        sleep=interrupt if interrupt_at == "sleep" else lambda seconds: None,
    )


def test_research_worker_succeeds_queued_run_end_to_end(run_repo) -> None:
    from sqlalchemy import text

    ctx = run_repo
    submitted = _t13_submit(ctx)
    seen = {}

    def compute(snapshot, config, checkpoint):
        seen["snapshot_id"] = snapshot.id
        seen["config_hash"] = config.config_hash
        assert callable(checkpoint)
        checkpoint()
        return dict(T11_RESULT)

    record = _t13_worker(ctx, compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "succeeded"
    assert record.attempt_count == 1
    assert record.result == T11_RESULT
    assert record.failure is None
    assert record.completed_at == T10_NOW
    assert seen == {
        "snapshot_id": ctx.publication.id,
        "config_hash": submitted.config.config_hash,
    }
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 1
        )
    assert ctx.runs.get(ctx.session.token, submitted.id).status == "succeeded"


def test_research_worker_runs_real_engine_and_persists_json_mapping(
    run_repo,
) -> None:
    ctx = run_repo
    # The run's own config must drive the engine so the persisted config_hash
    # stays traceable to the submitted BacktestConfig rather than a fixture
    # config fabricated inside the compute seam.
    submitted = _t13_submit(
        ctx,
        period=period(start=E_D1, end=E_D5),
        parameters=parameters(top_n=2, rebalance_period=2),
    )

    def compute(snapshot, config, checkpoint):
        assert snapshot.id == ctx.publication.id
        result = run_backtest(
            config,
            _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS}),
            _factor_frames(ENG_VALUES_BY_DAY),
            anchor=_eng_anchor(),
            policy=UniversePolicy(),
            checkpoint=checkpoint,
        )
        return result.model_dump(mode="json")

    record = _t13_worker(ctx, compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "succeeded"
    assert record.result["schema_version"] == T10_ENGINE_VERSION
    assert record.result["research_only"] is True
    assert record.result["config_hash"] == submitted.config.config_hash
    # JSONB hydration returns immutable tuples/mappings (ImmutableJsonObject);
    # assert ledger shape and contents rather than in-memory list types.
    equity = record.result["equity"]
    assert len(equity) == len(ENG_DAYS)
    assert tuple(point["trade_date"] for point in equity) == tuple(
        day.isoformat() for day in ENG_DAYS
    )
    assert record.result["baseline"]["status"] == "completed"


def test_research_worker_renews_lease_while_long_compute_is_running(
    run_repo,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import select

    from dragon_radar.research.tables import research_backtest_runs

    ctx = run_repo
    ctx.runs.clock = lambda: datetime.now(UTC)
    submitted = _t13_submit(ctx)
    compute_started = Event()
    release_compute = Event()

    def slow_compute(snapshot, config, checkpoint):
        compute_started.set()
        assert release_compute.wait(5)
        checkpoint()
        return dict(T11_RESULT)

    worker = _t13_worker(
        ctx,
        slow_compute,
        worker_id="heartbeat-worker",
        lease_for=timedelta(seconds=1),
    )
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(worker.run_once)
    try:
        assert compute_started.wait(5)
        with ctx.runs.database.engine.begin() as connection:
            original_expiry = connection.scalar(
                select(research_backtest_runs.c.lease_expires_at).where(
                    research_backtest_runs.c.id == submitted.id
                )
            )

        deadline = monotonic() + 2
        renewed_expiry = original_expiry
        while renewed_expiry <= original_expiry and monotonic() < deadline:
            sleep(0.05)
            with ctx.runs.database.engine.begin() as connection:
                renewed_expiry = connection.scalar(
                    select(research_backtest_runs.c.lease_expires_at).where(
                        research_backtest_runs.c.id == submitted.id
                    )
                )
        assert renewed_expiry > original_expiry
        while datetime.now(UTC) <= original_expiry:
            sleep(0.01)
        assert (
            ctx.runs.claim("competing-worker", lease_for=timedelta(seconds=1)) is None
        )
        release_compute.set()
        completed = future.result(timeout=5)
    finally:
        release_compute.set()
        pool.shutdown(wait=True)

    assert completed.status == "succeeded"
    assert completed.attempt_count == 1
    assert completed.result == T11_RESULT


def test_research_worker_cancel_takes_effect_at_next_checkpoint_without_result(
    run_repo,
) -> None:
    from sqlalchemy import text

    from dragon_radar.research.worker import ResearchRunCancelled

    ctx = run_repo
    submitted = _t13_submit(ctx)

    def compute(snapshot, config, checkpoint):
        cancelled = ctx.runs.cancel(ctx.session.token, ctx.session.csrf, submitted.id)
        assert cancelled.status == "cancelled"
        with pytest.raises(ResearchRunCancelled):
            checkpoint()
        raise AssertionError("compute must not continue after cancellation")

    record = _t13_worker(ctx, compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "cancelled"
    assert record.result is None
    assert record.failure is None
    assert record.completed_at == T10_NOW
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 0
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_runs")) == 1
        )


def test_research_worker_wall_clock_budget_gate_fails_and_keeps_pool_open(
    run_repo,
) -> None:
    from dragon_radar.research.worker import ResearchBudgetExceeded

    ctx = run_repo
    submitted = _t13_submit(ctx)
    ticks = [0.0, 0.0, 300.001]

    def fake_clock() -> float:
        return ticks.pop(0)

    def over_budget_compute(snapshot, config, checkpoint):
        checkpoint()
        with pytest.raises(ResearchBudgetExceeded) as caught:
            checkpoint()
        assert caught.value.budget_ms == T11_BUDGET_MS
        assert caught.value.elapsed_ms == T11_BUDGET_MS + 1
        return dict(T11_RESULT)

    record = _t13_worker(ctx, over_budget_compute, clock=fake_clock).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "failed"
    assert record.result is None
    assert record.failure == {
        "code": "budget_exceeded",
        "budget_ms": T11_BUDGET_MS,
        "elapsed_ms": T11_BUDGET_MS + 1,
    }
    assert ticks == []

    follow = _t13_submit(ctx)
    follower = _t13_worker(ctx, _t13_ok_compute(), worker_id="research-worker-2")
    completed = follower.run_once()
    assert completed is not None
    assert completed.id == follow.id
    assert completed.status == "succeeded"


def test_research_worker_compute_budget_gate_fails_before_compute(
    run_repo, monkeypatch
) -> None:
    from dragon_radar.research import worker as worker_module

    ctx = run_repo
    submitted = _t13_submit(ctx)

    def reject(_config):
        raise worker_module.ComputeBudgetExceeded("top_n", limit=1, actual=10)

    monkeypatch.setattr(worker_module, "assert_compute_budget", reject)

    def compute(snapshot, config, checkpoint):
        raise AssertionError("compute must not run past the budget gate")

    record = _t13_worker(ctx, compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "failed"
    assert record.result is None
    assert record.failure == {
        "code": "budget_exceeded",
        "reason": "compute_budget",
        "dimension": "top_n",
        "limit": 1,
        "actual": 10,
    }

    monkeypatch.undo()
    follow = _t13_submit(ctx)
    completed = _t13_worker(
        ctx, _t13_ok_compute(), worker_id="research-worker-2"
    ).run_once()
    assert completed is not None
    assert completed.id == follow.id
    assert completed.status == "succeeded"


def test_research_worker_engine_exception_fails_structured_without_blocking_claim(
    run_repo,
) -> None:
    ctx = run_repo
    submitted = _t13_submit(ctx)

    def exploding_compute(snapshot, config, checkpoint):
        raise RuntimeError("engine blew up")

    record = _t13_worker(ctx, exploding_compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "failed"
    assert record.result is None
    assert record.failure == {
        "code": "backtest_failed",
        "error_type": "RuntimeError",
    }
    assert ctx.runs.claim("research-worker-9") is None

    follow = _t13_submit(ctx)
    completed = _t13_worker(
        ctx, _t13_ok_compute(), worker_id="research-worker-2"
    ).run_once()
    assert completed is not None
    assert completed.id == follow.id
    assert completed.status == "succeeded"


def test_research_worker_revoked_publication_maps_to_input_forbidden(
    run_repo,
) -> None:
    from sqlalchemy import update

    from dragon_radar.publication.tables import publications as publications_table

    ctx = run_repo
    submitted = _t13_submit(ctx)
    with ctx.runs.database.engine.begin() as connection:
        connection.execute(
            update(publications_table)
            .where(publications_table.c.id == ctx.publication.id)
            .values(status="revoked")
        )

    record = _t13_worker(ctx, _t13_ok_compute()).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "failed"
    assert record.result is None
    assert record.failure == {"code": "input_forbidden"}


def test_research_worker_fail_closed_compute_maps_to_input_unavailable(
    run_repo,
) -> None:
    from dragon_radar.research.worker import (
        ResearchInputsUnavailable,
        fail_closed_research_compute,
    )

    with pytest.raises(ResearchInputsUnavailable):
        fail_closed_research_compute(None, None, lambda: None)

    ctx = run_repo
    submitted = _t13_submit(ctx)
    record = _t13_worker(ctx, fail_closed_research_compute).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "failed"
    assert record.result is None
    assert record.failure == {"code": "input_unavailable"}


def test_research_worker_checkpoint_detects_lease_takeover_and_skips_finish(
    run_repo,
) -> None:
    from dragon_radar.research.worker import ResearchRunCancelled

    ctx = run_repo
    submitted = _t13_submit(ctx)

    def compute(snapshot, config, checkpoint):
        ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=11)
        takeover = ctx.runs.claim("research-worker-2", lease_for=timedelta(seconds=10))
        assert takeover is not None
        assert takeover.attempt_count == 2
        with pytest.raises(ResearchRunCancelled):
            checkpoint()
        raise AssertionError("stale worker must stop at the checkpoint")

    record = _t13_worker(ctx, compute, lease_for=timedelta(seconds=10)).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "running"
    assert record.attempt_count == 2
    assert record.result is None
    assert record.completed_at is None

    takeover_lease = ctx.runs.claim(
        "research-worker-2", lease_for=timedelta(seconds=10)
    )
    assert takeover_lease is None
    active = ctx.runs.get(ctx.session.token, submitted.id)
    assert active.status == "running"
    assert active.attempt_count == 2


def test_research_worker_stale_succeed_conflict_leaves_no_half_result(
    run_repo,
) -> None:
    from sqlalchemy import text

    ctx = run_repo
    submitted = _t13_submit(ctx)

    def compute(snapshot, config, checkpoint):
        ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=11)
        takeover = ctx.runs.claim("research-worker-2", lease_for=timedelta(seconds=10))
        assert takeover is not None
        return dict(T11_RESULT)

    record = _t13_worker(ctx, compute, lease_for=timedelta(seconds=10)).run_once()

    assert record is not None
    assert record.id == submitted.id
    assert record.status == "running"
    assert record.attempt_count == 2
    assert record.result is None
    with ctx.runs.database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM research_backtest_cache")) == 0
        )

    # The takeover lease claimed at T10_NOW+11s expires ten seconds later;
    # advance the repository clock past it before worker-2 can reclaim.
    ctx.runs.clock = lambda: T10_NOW + timedelta(seconds=22)
    second_lease = ctx.runs.claim("research-worker-2", lease_for=timedelta(seconds=10))
    assert second_lease is not None
    completed = ctx.runs.succeed(second_lease, T11_RESULT)
    assert completed.status == "succeeded"
    assert completed.result == T11_RESULT


def test_research_worker_retry_limit_fails_run_but_other_runs_stay_claimable(
    run_repo,
) -> None:
    ctx = run_repo
    exhausted = _t13_submit(ctx)

    def exploding_compute(snapshot, config, checkpoint):
        raise RuntimeError("boom")

    record = _t13_worker(ctx, exploding_compute, max_attempts=1).run_once()
    assert record is not None
    assert record.id == exhausted.id
    assert record.status == "failed"
    assert record.attempt_count == 1
    assert record.result is None
    assert ctx.runs.claim("research-worker-1", max_attempts=1) is None

    follow = _t13_submit(ctx)
    completed = _t13_worker(
        ctx, _t13_ok_compute(), worker_id="research-worker-2", max_attempts=1
    ).run_once()
    assert completed is not None
    assert completed.id == follow.id
    assert completed.status == "succeeded"
    assert completed.attempt_count == 1


# ---------------------------------------------------------------------------
# Task 17 — owner-only backtest input manifest channel. A pure builder maps
# an authorized PublishedSnapshot onto the strict kernel InputManifests
# contract (domain UUIDs from the publication, version tokens from the
# MarketBatch); the repository reuses the EX14 authorization path and the
# HTTP route stays a thin OwnerRead adapter. Every IO case runs on real
# PostgreSQL and never recomputes published material.
# ---------------------------------------------------------------------------

T17_MANIFEST_PATH = "/api/research/manifests/{snapshot_id}"


def _manifest_snapshot(*, topic_id=None, role_id=None):
    from factories import NOW, batch

    from dragon_radar.publication.models import Publication, PublishedSnapshot
    from dragon_radar.storage.models import Snapshot

    market_batch = batch()
    market_snapshot = Snapshot(
        id=uuid4(),
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest={},
        batch=market_batch,
        quality_report={},
    )
    publication = Publication(
        id=uuid4(),
        trade_date=market_batch.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market_snapshot.id,
        topic_snapshot_id=topic_id,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=role_id,
        domains={},
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=NOW,
    )
    return PublishedSnapshot(publication, market_snapshot, ())


def _manifest_url(snapshot_id) -> str:
    return f"/api/research/manifests/{snapshot_id}"


# -- Part A: pure PublishedSnapshot -> BacktestManifestContext builder ------


def test_manifest_from_snapshot_marks_market_domain_and_batch_versions() -> None:
    from dragon_radar.research.repository import manifest_from_snapshot

    snapshot = _manifest_snapshot()

    payload = manifest_from_snapshot(snapshot)

    assert set(payload) == {"snapshot_id", "domains", "versions"}
    assert payload["snapshot_id"] == str(snapshot.publication.id)
    assert payload["domains"] == [
        {
            "domain": "market",
            "snapshot_id": str(snapshot.publication.market_snapshot_id),
        }
    ]
    assert payload["versions"] == {
        "adjustment": "a1",
        "calendar": "c1",
        "pool": "p1",
    }
    # The wire payload must satisfy the same strict kernel contract as EX14.
    InputManifests.model_validate_json(
        json.dumps({"domains": payload["domains"], "versions": payload["versions"]})
    )


def test_manifest_from_snapshot_includes_topic_without_role_when_role_absent() -> None:
    from dragon_radar.research.repository import manifest_from_snapshot

    topic_id = uuid4()
    snapshot = _manifest_snapshot(topic_id=topic_id)

    payload = manifest_from_snapshot(snapshot)

    assert [entry["domain"] for entry in payload["domains"]] == ["market", "topic"]
    assert payload["domains"][1] == {
        "domain": "topic",
        "snapshot_id": str(topic_id),
    }
    InputManifests.model_validate_json(
        json.dumps({"domains": payload["domains"], "versions": payload["versions"]})
    )


def test_manifest_from_snapshot_appends_topic_and_role_domains_in_order() -> None:
    from dragon_radar.research.repository import manifest_from_snapshot

    topic_id = uuid4()
    role_id = uuid4()
    snapshot = _manifest_snapshot(topic_id=topic_id, role_id=role_id)

    payload = manifest_from_snapshot(snapshot)

    assert [entry["domain"] for entry in payload["domains"]] == [
        "market",
        "topic",
        "role",
    ]
    assert payload["domains"][1]["snapshot_id"] == str(topic_id)
    assert payload["domains"][2]["snapshot_id"] == str(role_id)
    InputManifests.model_validate_json(
        json.dumps({"domains": payload["domains"], "versions": payload["versions"]})
    )


# -- Part B: repository authorization on real PostgreSQL --------------------


def test_research_input_manifest_returns_authorized_publication_manifest(
    run_repo,
) -> None:
    ctx = run_repo

    payload = ctx.runs.input_manifest(ctx.session.token, ctx.publication.id)

    assert payload == {
        "snapshot_id": str(ctx.publication.id),
        "domains": [
            {
                "domain": "market",
                "snapshot_id": str(ctx.publication.market_snapshot_id),
            }
        ],
        "versions": {"adjustment": "a1", "calendar": "c1", "pool": "p1"},
    }


def test_research_input_manifest_includes_topic_domain_when_published(
    run_repo,
) -> None:
    from factories import NOW, batch
    from topic_factories import topic_batch

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.topics.repository import TopicRepository

    ctx = run_repo
    database = ctx.runs.database
    market_repo = ctx.runs.publications.market
    topic_repo = TopicRepository(database, clock=lambda: NOW)
    market = market_repo.publish(
        market_repo.import_batch(batch(), key="manifest-topic-market").id
    )
    topic = topic_repo.publish(
        topic_repo.import_batch(topic_batch(), key="manifest-topic").id
    )
    publication = PublicationService(database, market_repo, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            disabled_domains=("news", "regulation", "role"),
        ),
        key="manifest-topic-publication",
    )

    payload = ctx.runs.input_manifest(ctx.session.token, publication.id)

    assert payload["snapshot_id"] == str(publication.id)
    assert [entry["domain"] for entry in payload["domains"]] == ["market", "topic"]
    assert payload["domains"][1] == {
        "domain": "topic",
        "snapshot_id": str(topic.id),
    }
    assert payload["versions"] == {
        "adjustment": "a1",
        "calendar": "c1",
        "pool": "p1",
    }


def test_research_input_manifest_rejects_unknown_revoked_and_malformed(
    run_repo,
) -> None:
    from sqlalchemy import update

    from dragon_radar.publication.tables import publications as publications_table
    from dragon_radar.storage.repository import Forbidden, NotFound

    ctx = run_repo
    with pytest.raises(NotFound):
        ctx.runs.input_manifest(ctx.session.token, uuid4())
    with pytest.raises(ValueError, match="Invalid record ID"):
        ctx.runs.input_manifest(ctx.session.token, "not-a-uuid")

    with ctx.runs.database.engine.begin() as connection:
        connection.execute(
            update(publications_table)
            .where(publications_table.c.id == ctx.publication.id)
            .values(status="revoked")
        )
    with pytest.raises(Forbidden):
        ctx.runs.input_manifest(ctx.session.token, ctx.publication.id)


def test_research_input_manifest_requires_authorized_session(run_repo) -> None:
    from dragon_radar.owner import SessionUnauthorized

    ctx = run_repo
    with pytest.raises(SessionUnauthorized):
        ctx.runs.input_manifest("not-a-real-session-token", ctx.publication.id)


# -- Part C: thin OwnerRead HTTP route --------------------------------------


@pytest.mark.anyio
async def test_research_http_manifest_requires_real_session(run_repo) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.get(
            _manifest_url(ctx.publication.id),
            headers={"Origin": T12_ORIGIN},
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_research_http_manifest_returns_backtest_manifest_context(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        response = await client.get(
            _manifest_url(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"snapshot_id", "domains", "versions"}
    assert payload == {
        "snapshot_id": str(ctx.publication.id),
        "domains": [
            {
                "domain": "market",
                "snapshot_id": str(ctx.publication.market_snapshot_id),
            }
        ],
        "versions": {"adjustment": "a1", "calendar": "c1", "pool": "p1"},
    }


@pytest.mark.anyio
async def test_research_http_manifest_maps_not_found_forbidden_and_bad_request(
    run_repo,
) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import update

    from dragon_radar.publication.tables import publications as publications_table

    ctx = run_repo
    app = _research_owner_app(ctx)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        missing = await client.get(
            _manifest_url(uuid4()),
            headers=_t12_headers(ctx.session),
        )
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "NOT_FOUND",
            "message": "Resource not found",
        }

        queried = await client.get(
            f"{_manifest_url(ctx.publication.id)}?unexpected=1",
            headers=_t12_headers(ctx.session),
        )
        assert queried.status_code == 400
        assert queried.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }

        malformed = await client.get(
            "/api/research/manifests/not-a-uuid",
            headers=_t12_headers(ctx.session),
        )
        assert malformed.status_code == 400
        assert malformed.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }

    with ctx.runs.database.engine.begin() as connection:
        connection.execute(
            update(publications_table)
            .where(publications_table.c.id == ctx.publication.id)
            .values(status="revoked")
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=T12_ORIGIN
    ) as client:
        revoked = await client.get(
            _manifest_url(ctx.publication.id),
            headers=_t12_headers(ctx.session),
        )
    assert revoked.status_code == 403
    assert revoked.json() == {
        "code": "FORBIDDEN",
        "message": "Source access denied",
    }
