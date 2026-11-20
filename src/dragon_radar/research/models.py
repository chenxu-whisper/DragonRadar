"""Immutable backtest configuration, bounded validation and config hash.

All monetary inputs are explicit finite Decimals — there are no silent cost
defaults. Bounds live in module-level constants so the HTTP layer (Task 12)
and worker budget checks reuse one source of truth. Models are strict,
frozen and forbid extra fields.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from dragon_radar.domain.models import FrozenModel

RESEARCH_BACKTEST_SCHEMA_VERSION = "dragon-radar.research-backtest/v1"
RESEARCH_HEALTH_SCHEMA_VERSION = "dragon-radar.research-health/v1"

STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1 = "factor-cross-sectional-topn-v1"
STRATEGY_IDS = frozenset({STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1})

# Conservative configuration bounds (spec assumption A4; runtime budget in
# Tasks 10-13 is enforced separately).
MAX_PERIOD_SESSIONS = 250
MIN_PERIOD_SPAN_DAYS = 2
MAX_PERIOD_CALENDAR_DAYS = 400
MAX_TOP_N = 100
MIN_REBALANCE_PERIOD_SESSIONS = 1
MAX_REBALANCE_PERIOD_SESSIONS = 60
MIN_WALK_FORWARD_FOLDS = 2
MAX_WALK_FORWARD_FOLDS = 10
MAX_COST_RATE = Decimal("0.01")
MAX_MIN_COMMISSION = Decimal("100")

# Deterministic, disclosed sensitivity multipliers applied to every cost
# component in Task 4; part of the cost-method version, never a user override.
COST_SENSITIVITY_TIERS = ("low", "base", "high")
COST_SENSITIVITY_MULTIPLIERS = {
    "low": Decimal("0.5"),
    "base": Decimal("1"),
    "high": Decimal("2"),
}

CostTier = Literal["low", "base", "high"]
SlippageModelName = Literal["fixed_rate"]
StampTaxSide = Literal["sell"]
FillTiming = Literal["next_tradable_point"]
UnknownLimitPolicy = Literal["reject_untradable"]
Weighting = Literal["equal_weight"]
ManifestDomainName = Literal["market", "topic", "role"]
ExperimentLegName = Literal["baseline", "ai_variant"]
ResearchFactorId = Literal["FAC-R01", "FAC-R02", "FAC-R03", "FAC-C01", "FAC-C02"]


def _decimal_input(value: object) -> object:
    """Accept Decimal/int/exact decimal strings; never float or bool."""
    if isinstance(value, (float, bool)):
        raise ValueError("Decimal inputs must not be float or bool")
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal string") from exc
    return value


PositiveRate = Annotated[
    Decimal, BeforeValidator(_decimal_input), Field(gt=Decimal("0"), le=MAX_COST_RATE)
]
BoundedFee = Annotated[
    Decimal,
    BeforeValidator(_decimal_input),
    Field(gt=Decimal("0"), le=MAX_MIN_COMMISSION),
]
VersionToken = Annotated[
    StrictStr,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


class StrictFrozenModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class CostModel(StrictFrozenModel):
    """Explicit, finite positive trading-cost assumptions."""

    commission_rate: PositiveRate
    stamp_tax_rate: PositiveRate
    slippage_model: SlippageModelName = "fixed_rate"
    slippage_rate: PositiveRate
    stamp_tax_side: StampTaxSide = "sell"
    min_commission: BoundedFee


class ExecutionModel(StrictFrozenModel):
    """Execution timing; unknown limit metadata is never assumed tradable."""

    fill_timing: FillTiming = "next_tradable_point"
    unknown_limit_policy: UnknownLimitPolicy = "reject_untradable"


class StrategyParameters(StrictFrozenModel):
    factor_id: ResearchFactorId = "FAC-R01"
    top_n: Annotated[int, Field(ge=1, le=MAX_TOP_N)]
    rebalance_period: Annotated[
        int,
        Field(ge=MIN_REBALANCE_PERIOD_SESSIONS, le=MAX_REBALANCE_PERIOD_SESSIONS),
    ]
    weighting: Weighting = "equal_weight"


class BacktestPeriod(StrictFrozenModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _bounded_span(self) -> Self:
        span_days = (self.end - self.start).days
        if not MIN_PERIOD_SPAN_DAYS <= span_days <= MAX_PERIOD_CALENDAR_DAYS:
            raise ValueError(
                "period span must be between "
                f"{MIN_PERIOD_SPAN_DAYS} and {MAX_PERIOD_CALENDAR_DAYS} days"
            )
        return self


class WalkForwardSpec(StrictFrozenModel):
    folds: Annotated[int, Field(ge=MIN_WALK_FORWARD_FOLDS, le=MAX_WALK_FORWARD_FOLDS)]
    train_sessions: Annotated[int, Field(ge=1)]
    test_sessions: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _fits_session_cap(self) -> Self:
        required = self.train_sessions + self.folds * self.test_sessions
        if required > MAX_PERIOD_SESSIONS:
            raise ValueError(
                f"walk-forward requires {required} sessions; cap is "
                f"{MAX_PERIOD_SESSIONS}"
            )
        return self


class DomainManifest(StrictFrozenModel):
    domain: ManifestDomainName
    snapshot_id: UUID


class ManifestVersions(StrictFrozenModel):
    adjustment: VersionToken
    calendar: VersionToken
    pool: VersionToken


class InputManifests(StrictFrozenModel):
    domains: Annotated[tuple[DomainManifest, ...], Field(min_length=1, max_length=3)]
    versions: ManifestVersions

    @model_validator(mode="after")
    def _market_required_and_unique(self) -> Self:
        names = [entry.domain for entry in self.domains]
        if len(set(names)) != len(names):
            raise ValueError("input manifest domains must be unique")
        if "market" not in names:
            raise ValueError("market domain manifest is required")
        return self


class ExperimentSpec(StrictFrozenModel):
    random_seed: Annotated[int, Field(ge=0, le=0xFFFFFFFF)]
    legs: Annotated[tuple[ExperimentLegName, ...], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def _baseline_required_without_duplicates(self) -> Self:
        if len(set(self.legs)) != len(self.legs):
            raise ValueError("experiment legs must be unique")
        if "baseline" not in self.legs:
            raise ValueError("experiment must include the baseline leg")
        return self


class BacktestConfig(StrictFrozenModel):
    schema_version: Literal[RESEARCH_BACKTEST_SCHEMA_VERSION] = (
        RESEARCH_BACKTEST_SCHEMA_VERSION
    )
    snapshot_id: UUID
    strategy_id: StrictStr
    parameters: StrategyParameters
    period: BacktestPeriod
    cost_model: CostModel
    execution_model: ExecutionModel
    walk_forward: WalkForwardSpec | None = None
    experiment: ExperimentSpec
    input_manifests: InputManifests

    @field_validator("strategy_id")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        if value not in STRATEGY_IDS:
            raise ValueError(f"unknown strategy_id: {value}")
        return value

    @model_validator(mode="after")
    def _walk_forward_fits_period(self) -> Self:
        if self.walk_forward is None:
            return self
        required_sessions = (
            self.walk_forward.train_sessions
            + self.walk_forward.folds * self.walk_forward.test_sessions
        )
        span_days = (self.period.end - self.period.start).days
        if span_days < required_sessions:
            raise ValueError(
                f"period spans {span_days} days but walk-forward requires "
                f"{required_sessions} sessions"
            )
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("ascii")).hexdigest()


class BacktestSubmission(StrictFrozenModel):
    """EX14 envelope; the idempotency key never participates in the hash."""

    config: BacktestConfig
    idempotency_key: UUID


__all__ = [
    "COST_SENSITIVITY_MULTIPLIERS",
    "COST_SENSITIVITY_TIERS",
    "MAX_COST_RATE",
    "MAX_MIN_COMMISSION",
    "MAX_PERIOD_CALENDAR_DAYS",
    "MAX_PERIOD_SESSIONS",
    "MAX_REBALANCE_PERIOD_SESSIONS",
    "MAX_TOP_N",
    "MAX_WALK_FORWARD_FOLDS",
    "MIN_REBALANCE_PERIOD_SESSIONS",
    "MIN_PERIOD_SPAN_DAYS",
    "MIN_WALK_FORWARD_FOLDS",
    "RESEARCH_BACKTEST_SCHEMA_VERSION",
    "RESEARCH_HEALTH_SCHEMA_VERSION",
    "STRATEGY_FACTOR_CROSS_SECTIONAL_TOPN_V1",
    "STRATEGY_IDS",
    "StrictFrozenModel",
    "BacktestConfig",
    "BacktestPeriod",
    "BacktestSubmission",
    "CostModel",
    "CostTier",
    "DomainManifest",
    "ExecutionModel",
    "ExperimentLegName",
    "ExperimentSpec",
    "InputManifests",
    "ManifestVersions",
    "ResearchFactorId",
    "StrategyParameters",
    "VersionToken",
    "WalkForwardSpec",
]
