"""Task 27 PIT backtest suite (EX14 engine slices).

Task 1 scaffold: the research package modules exist and stay importable so
later slices can extend them without changing the package boundary. The PIT
assembly, ``run_backtest`` engine, factor health check and walk-forward
behaviour tests arrive in Tasks 5-8.
"""

from __future__ import annotations

import importlib
import re
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
