"""Frozen input models and contract constants for research validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)

STREAM_COMPONENTS = "components"
STREAM_TARGET_CLOSES = "target_closes"
STREAM_BENCHMARK_CLOSES = "benchmark_closes"
STREAM_NAMES = (
    STREAM_COMPONENTS,
    STREAM_TARGET_CLOSES,
    STREAM_BENCHMARK_CLOSES,
)

ALLOWED_HORIZONS = frozenset({1, 5, 20})

# Degradation reason vocabulary (closed set).
REASON_INSUFFICIENT_SAMPLE = "insufficient_sample"
REASON_ZERO_VARIANCE = "zero_variance"
REASON_INSUFFICIENT_FOLDS = "insufficient_folds"
REASON_EMPTY_BUCKET = "empty_bucket"
REASON_POOLED_IC_UNAVAILABLE = "pooled_ic_unavailable"

# Label accounting reasons.
REASON_OUT_OF_WINDOW = "out_of_window"
REASON_MISSING_CLOSE = "missing_close"
REASON_SLOT_WITHOUT_SIGNAL = "slot_without_signal"

# Sufficiency gate codes.
GATE_COVERAGE_BELOW_THRESHOLD = "coverage_below_threshold"
GATE_SCORE_ZERO_VARIANCE = "score_zero_variance"
GATE_INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
GATE_INSUFFICIENT_FOLDS = "insufficient_folds"
GATE_QUINTILE_TOO_SMALL = "quintile_too_small"
GATE_POOLED_IC_UNAVAILABLE = "pooled_ic_unavailable"
GATE_CODES = frozenset(
    {
        GATE_COVERAGE_BELOW_THRESHOLD,
        GATE_SCORE_ZERO_VARIANCE,
        GATE_INSUFFICIENT_OBSERVATIONS,
        GATE_INSUFFICIENT_FOLDS,
        GATE_QUINTILE_TOO_SMALL,
        GATE_POOLED_IC_UNAVAILABLE,
    }
)

# Evidence status vocabulary.
EVIDENCE_SYNTHETIC_ONLY = "synthetic_only"
EVIDENCE_INSUFFICIENT_REAL_SAMPLE = "insufficient_real_sample"
EVIDENCE_REAL_SAMPLE_DESCRIPTIVE = "real_sample_descriptive"
EVIDENCE_STATUSES = frozenset(
    {
        EVIDENCE_SYNTHETIC_ONLY,
        EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
        EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
    }
)

REPORT_TYPE = "research-method-validation"
VALIDATOR_VERSION = "research-validation-v1"
EVALUATION_KIND = "time_serial_non_overlapping"

REPORT_SCOPES = frozenset({"global", "h1", "h5", "h20"})


class ResearchValidationError(ValueError):
    """A frozen sample or report violates the validation contract."""


@dataclass(frozen=True, slots=True)
class ComponentCounts:
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class DailyComponents:
    trade_date: date
    breadth: ComponentCounts
    limit_balance: ComponentCounts
    promotion: ComponentCounts


@dataclass(frozen=True, slots=True)
class InputManifest:
    name: str
    source_version: str
    frozen_at: str
    as_of: date
    row_count: int
    adjustment: str
    sha256: str


@dataclass(frozen=True, slots=True)
class SamplePack:
    pack_id: str
    synthetic: bool
    generator: str
    seed: int | None
    as_of: date
    calendar: tuple[date, ...]
    components: tuple[DailyComponents, ...]
    target_name: str
    benchmark_name: str
    target_closes: Mapping[date, Decimal]
    benchmark_closes: Mapping[date, Decimal]
    manifests: tuple[InputManifest, ...]


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    horizons: tuple[int, ...] = (1, 5, 20)
    min_coverage: Decimal = Decimal("0.80")
    min_signals_per_fold: int = 8
    min_folds: int = 5
    min_quintile_size: int = 5


DEFAULT_CONFIG = ValidationConfig()


def validate_config(config: ValidationConfig) -> ValidationConfig:
    """Fail-closed config validation; defaults are frozen constants."""
    if type(config) is not ValidationConfig:
        raise ResearchValidationError("config must be a ValidationConfig")
    if type(config.horizons) is not tuple or not config.horizons:
        raise ResearchValidationError("horizons must be a non-empty tuple")
    seen: set[int] = set()
    for horizon in config.horizons:
        if type(horizon) is not int or horizon not in ALLOWED_HORIZONS:
            raise ResearchValidationError("horizon must be one of {1, 5, 20}")
        if horizon in seen:
            raise ResearchValidationError("horizons must be unique")
        seen.add(horizon)
    for attr in ("min_signals_per_fold", "min_folds", "min_quintile_size"):
        value = getattr(config, attr)
        if type(value) is not int or value <= 0:
            raise ResearchValidationError(f"{attr} must be a positive int")
    coverage = config.min_coverage
    if isinstance(coverage, bool) or not isinstance(coverage, Decimal):
        raise ResearchValidationError("min_coverage must be a Decimal")
    if not coverage.is_finite() or not ZERO < coverage <= ONE:
        raise ResearchValidationError("min_coverage must be within (0, 1]")
    return config
