"""Task 27 PIT backtest suite (EX14 engine slices).

Task 1 scaffold: the research package modules exist and stay importable so
later slices can extend them without changing the package boundary. The PIT
assembly, ``run_backtest`` engine, factor health check and walk-forward
behaviour tests arrive in Tasks 5-8.
"""

from __future__ import annotations

import importlib
import re
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
