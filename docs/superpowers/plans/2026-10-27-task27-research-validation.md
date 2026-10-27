# Task 27 Slice A — Research Score Method Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure-function validation subdomain that rebuilds `sentiment-research-v1` scores from frozen integer counts and reports contemporaneous factor health plus PIT forward descriptive evidence (non-overlapping RankIC/ICIR/quintiles), never claiming effectiveness.

**Architecture:** New isolated package `src/dragon_radar/research/` (models → samples → statistics → factor_health → forward_eval → report), zero I/O, Decimal-only math, versioned closed-shape reports with a hash identity and a three-value evidence status. The existing Task 29 absent-artifact guard is refined to allow exactly this subdomain while backtest/simulation/migration/route artifacts stay forbidden.

**Tech Stack:** Python 3.12, stdlib + existing `dragon_radar.readmodels.sentiment_research` and `dragon_radar.analytics.metrics._decimal_context`; pytest; ruff (E/F/I/UP/B). No new dependency, no database, no HTTP, no frontend.

**Spec:** `docs/superpowers/specs/2026-10-27-task27-research-validation-design.md` (approved 2026-10-27). The spec is the authority; if code below and the spec conflict, stop and reconcile before implementing.

**Worktree/branch:** `.worktrees/task27-research-validation` on `feature/task27-research-validation` (already created from `4d08fcf`; spec committed as `0d50cd2`).

**Hard rules for every task**

- Stage only with explicit paths: `git add -- <path>`. Never `git add -A`. Never commit `.trae/`.
- No push, merge, tag, version bump, release, deployment. Do not touch `docs/acceptance/reset-final.md`, README, CI, `scripts/check_prd_contracts.py`, migrations, or anything under `web/`.
- No float in financial math; no numpy/scipy/new packages.
- Run all Python commands from the worktree root with `uv run`. Set `$env:PYTHONPYCACHEPREFIX = "$PWD\.temp\pycache"` once per shell session (PowerShell).
- PowerShell: use `;` between commands, never `&&`.
- The single test module grows by appending test functions/fixtures only.
  Every `import` shown in an append block is hoisted into the one top import
  group in isort order (stdlib, third-party, first-party) with no duplicated
  names; ruff enables `I` (isort), so a mid-file or duplicate import fails
  `ruff check` and `ruff format` will not move it for you.
- All input dataclasses are `slots=True` (no `__dict__`); copy variants in
  tests with `dataclasses.replace(...)`, never `**obj.__dict__`.

---

## File Structure

Create one test module and one seven-file package; modify one existing guard test.

| File | Responsibility |
|---|---|
| `tests/test_reset_research_validation.py` | All synthetic TDD tests for the slice |
| `src/dragon_radar/research/__init__.py` | Public API surface |
| `src/dragon_radar/research/models.py` | Frozen dataclasses, closed vocabulary constants, `ResearchValidationError`, `validate_config` |
| `src/dragon_radar/research/samples.py` | Pack validation, stream hashes, manifest verification, method score rebuilding |
| `src/dragon_radar/research/statistics.py` | Average ranks, Spearman, mean/std/median/quantile/distribution, quintile buckets |
| `src/dragon_radar/research/factor_health.py` | Contemporaneous coverage/distribution/correlation block |
| `src/dragon_radar/research/forward_eval.py` | PIT labels, non-overlapping grid, folds, RankIC/ICIR/quintiles/gates |
| `src/dragon_radar/research/report.py` | Report assembly, quantization, hash identity, archive serializer/parser, shape validation |
| `tests/test_reset_full_scope.py` (modify) | Refine `test_task27_artifacts_remain_absent`; add closed-set subdomain test |

---

## Task 1: Skeleton, Contract Constants, Frozen Models, Guard Refinement

**Files:**
- Modify: `tests/test_reset_full_scope.py` (the Task 27 artifact boundary section)
- Create: `src/dragon_radar/research/__init__.py`
- Create: `src/dragon_radar/research/models.py`
- Create: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Refine the Task 29 guard first (RED).**

In `tests/test_reset_full_scope.py`, replace the whole function `test_task27_artifacts_remain_absent` with two tests (keep the surrounding section and the `ROOT` import exactly as-is):

```python
def test_task27_backtest_and_simulation_artifacts_remain_absent():
    absent = (
        "src/dragon_radar/http/routes/research.py",
        "src/dragon_radar/migrations/versions/0014_research.py",
        "web/src/repositories/local/simulation.ts",
        "web/src/features/research/BacktestPanel.tsx",
        "web/src/features/research/SimulationPanel.tsx",
        "tests/test_reset_backtest.py",
        "web/tests/unit/reset-simulation.test.ts",
        "web/tests/e2e/reset-research.spec.ts",
    )
    for relative in absent:
        assert not (ROOT / relative).exists(), f"Task 27 artifact appeared: {relative}"

    # The existing research feature directory holds only Task 28 sentiment.
    research_feature = ROOT / "web/src/features/research"
    assert {path.name for path in research_feature.glob("*.tsx")} == {
        "SentimentPanel.tsx"
    }


def test_task27_research_validation_is_a_closed_pure_subdomain():
    package = ROOT / "src/dragon_radar/research"
    assert package.is_dir(), "Slice A adds the research validation package"
    assert {path.name for path in package.glob("*.py")} == {
        "__init__.py",
        "models.py",
        "samples.py",
        "statistics.py",
        "factor_health.py",
        "forward_eval.py",
        "report.py",
    }
    for forbidden in (
        "repository.py",
        "backtest.py",
        "execution.py",
        "walk_forward.py",
        "ablation.py",
        "routes.py",
        "cli.py",
        "worker.py",
        "models_db.py",
    ):
        assert not (package / forbidden).exists(), f"forbidden module: {forbidden}"
```

- [ ] **Step 2: Run the guard to verify RED.**

Run: `uv run pytest tests/test_reset_full_scope.py::test_task27_research_validation_is_a_closed_pure_subdomain tests/test_reset_full_scope.py::test_task27_backtest_and_simulation_artifacts_remain_absent -q`

Expected: one FAIL (package directory missing), the backtest test PASS.

- [ ] **Step 3: Create `__init__.py`.**

```python
"""Task 27 research-method validation subdomain (Slice A).

Pure functions only: no database, HTTP, filesystem, clock, or global state.
See docs/superpowers/specs/2026-10-27-task27-research-validation-design.md.
"""
```

- [ ] **Step 4: Create `models.py` with the complete closed vocabulary.**

```python
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
```

- [ ] **Step 5: Create the test module with model, config, and purity tests.**

```python
"""Task 27 Slice A tests: research-method validation (synthetic only)."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    ComponentCounts,
    ResearchValidationError,
    ValidationConfig,
    validate_config,
)

PACKAGE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dragon_radar"
    / "research"
)
FORBIDDEN_IMPORTS = ("fastapi", "sqlalchemy", "httpx", "requests")
FORBIDDEN_DRAGON_SUBPACKAGES = (
    "http",
    "storage",
    "owner",
    "publication",
    "analysis_jobs",
    "query",
    "realtime",
    "ai",
    "lhb",
    "news",
    "review",
    "regulation",
    "topics",
    "roles",
    "ingestion",
)
ALLOWED_DRAGON_IMPORTS = (
    "dragon_radar.analytics.metrics",
    "dragon_radar.readmodels.sentiment_research",
)


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_research_package_imports_stay_pure():
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        for name in _imported_modules(path):
            assert not name.startswith(FORBIDDEN_IMPORTS), (path.name, name)
            if name.startswith("dragon_radar."):
                assert name == "dragon_radar" or name.startswith(
                    "dragon_radar.research"
                ) or name.startswith(ALLOWED_DRAGON_IMPORTS), (path.name, name)
                root = ".".join(name.split(".")[:2])
                assert root not in {
                    f"dragon_radar.{sub}" for sub in FORBIDDEN_DRAGON_SUBPACKAGES
                }, (path.name, name)


def test_default_config_is_valid_and_frozen():
    assert validate_config(DEFAULT_CONFIG) is DEFAULT_CONFIG
    assert DEFAULT_CONFIG.horizons == (1, 5, 20)
    assert DEFAULT_CONFIG.min_coverage == Decimal("0.80")
    with pytest.raises(AttributeError):
        DEFAULT_CONFIG.min_folds = 9  # type: ignore[misc]


def test_validate_config_rejects_bad_horizons_and_thresholds():
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(3,)))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(1, 1)))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=()))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("0")))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_signals_per_fold=0))


def test_component_counts_are_frozen_value_holders():
    counts = ComponentCounts(3, 5)
    assert (counts.numerator, counts.denominator) == (3, 5)
    with pytest.raises(AttributeError):
        counts.numerator = 4  # type: ignore[misc]
```

Note: `date`/`timedelta`/`ALLOWED_HORIZONS` are intentionally not imported in
Task 1 (nothing in this task uses them); they are added to the top import
block in the tasks that need them. Subsequent "append" blocks below include
only names not already imported — merge new stdlib/third-party imports into
the top import block when appending, keeping a single ruff/isort-clean block.

- [ ] **Step 6: Create the five docstring-only placeholder modules, then run tests green.**

The closed-set test expects all seven files, so before running anything create
the five remaining modules containing only their module docstrings (no
placeholders/TODOs beyond the docstring):

- `samples.py`: `"""Fail-closed frozen sample validation and method score rebuilding."""`
- `statistics.py`: `"""Exact Decimal statistics for research validation (no float)."""`
- `factor_health.py`: `"""Contemporaneous factor health block."""`
- `forward_eval.py`: `"""Point-in-time forward evaluation over a non-overlapping grid."""`
- `report.py`: `"""Versioned, closed-shape validation reports."""`

Run: `uv run pytest tests/test_reset_research_validation.py tests/test_reset_full_scope.py::test_task27_research_validation_is_a_closed_pure_subdomain tests/test_reset_full_scope.py::test_task27_backtest_and_simulation_artifacts_remain_absent -q`

Expected: all PASS (the package now exists with the exact closed file set).

Run: `uv run ruff check src/dragon_radar/research tests/test_reset_research_validation.py tests/test_reset_full_scope.py; uv run ruff format --check src/dragon_radar/research tests/test_reset_research_validation.py`

Expected: all clean.

```powershell
git add -- src/dragon_radar/research tests/test_reset_research_validation.py tests/test_reset_full_scope.py
git commit -m "feat(research): add validation subdomain skeleton, frozen models, refined Task27 guard"
```

---

## Task 2: Exact Decimal Statistics Primitives

**Files:**
- Modify: `src/dragon_radar/research/statistics.py`
- Test: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append failing statistics tests.**

Append to the test file (imports added at the top of the file in the final form):

```python
from dragon_radar.research import statistics as stats
from dragon_radar.research.models import (
    REASON_INSUFFICIENT_SAMPLE,
    REASON_ZERO_VARIANCE,
)

D = Decimal


def test_average_ranks_handles_ties_exactly():
    assert stats.average_ranks([D("3"), D("1"), D("2"), D("3")]) == (
        D("3.5"),
        D("1"),
        D("2"),
        D("3.5"),
    )
    assert stats.average_ranks([D("5"), D("5"), D("5")]) == (
        D("2"),
        D("2"),
        D("2"),
    )


def test_spearman_extremes_and_hand_computed_tie_case():
    assert stats.spearman([D("1"), D("2"), D("3")], [D("4"), D("5"), D("6")]).value == D(
        "1"
    )
    assert stats.spearman([D("1"), D("2"), D("3")], [D("6"), D("5"), D("4")]).value == D(
        "-1"
    )
    # ranks x = 1,2,3 ; ranks y = 2,1,3 -> covariance form yields 0.5 exactly.
    result = stats.spearman([D("1"), D("2"), D("3")], [D("2"), D("1"), D("3")])
    assert result.value == D("0.5")
    tied = stats.spearman([D("1"), D("1"), D("2")], [D("1"), D("2"), D("3")])
    assert tied.value == D("0.866025403784438646763723170752936") or tied.value == D(
        "0.8660254037844386467637231707529362"
    )


def test_spearman_degenerate_inputs_report_closed_reasons():
    assert stats.spearman([D("1")], [D("1")]).reason == REASON_INSUFFICIENT_SAMPLE
    assert stats.spearman([D("1"), D("1")], [D("1"), D("2")]).reason == (
        REASON_ZERO_VARIANCE
    )
    assert stats.spearman([D("1"), D("2")], [D("3"), D("3")]).reason == (
        REASON_ZERO_VARIANCE
    )


def test_statistics_reject_bad_types_and_lengths():
    for bad in ([1.0], [True], [float("nan")], [D("NaN")]):
        with pytest.raises(ResearchValidationError):
            stats.average_ranks(bad)
    with pytest.raises(ResearchValidationError):
        stats.spearman([D("1")], [D("1"), D("2")])
    with pytest.raises(ResearchValidationError):
        stats.average_ranks([])


def test_mean_std_median_quantile_distribution():
    assert stats.decimal_mean([D("1"), D("2"), D("3"), D("4")]) == D("2.5")
    assert stats.decimal_median([D("4"), D("1"), D("3"), D("2")]) == D("2.5")
    assert stats.decimal_median([D("3"), D("1"), D("2")]) == D("2")
    assert stats.decimal_sample_std([D("1")]) is None
    # Sample (n-1) std: mean 2, squared deviations 4+0+4=8, /2=4, sqrt=2.
    assert stats.decimal_sample_std([D("0"), D("2"), D("4")]) == D("2")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("0")) == D("1")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("1")) == D("4")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("0.5")) == D(
        "2.5"
    )
    distribution = stats.decimal_distribution([D("1"), D("3")])
    assert distribution["n"] == 2
    assert distribution["p25"] == D("1.5")
    assert distribution["p75"] == D("2.5")


def test_quintile_buckets_never_split_ties():
    # 10 distinct ranks -> two observations per bucket.
    ranks = tuple(D(i) for i in range(1, 11))
    assert stats.quintile_buckets(ranks) == (1, 1, 2, 2, 3, 3, 4, 4, 5, 5)
    # Two equal ranks share one bucket.
    tied = stats.quintile_buckets([D("1.5"), D("1.5"), D("3"), D("4"), D("5")])
    assert tied[0] == tied[1]
    assert 1 <= tied[0] <= 5
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: FAIL with `AttributeError: module 'dragon_radar.research.statistics' has no attribute 'average_ranks'`.

- [ ] **Step 3: Implement `statistics.py` fully.**

```python
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


def spearman(
    x: Sequence[Decimal], y: Sequence[Decimal]
) -> RankCorrelation:
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
    position = q * Decimal(len(data) - 1)
    low = int(position)
    fraction = position - Decimal(low)
    if fraction == 0 or low == len(data) - 1:
        return data[low]
    with localcontext(_decimal_context()):
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
```

Note: the hand-computed tied Spearman constant in the test is the
34-digit sqrt form; the assertion accepts the last-digit half-even
variants. If neither literal matches, record the exact produced value and
use it verbatim (it must be positive and within `1e-30` of
`sqrt(3)/2 ≈ 0.8660254...`).

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: PASS.

Run: `uv run ruff check src/dragon_radar/research/statistics.py; uv run ruff format src/dragon_radar/research/statistics.py`

```powershell
git add -- src/dragon_radar/research/statistics.py tests/test_reset_research_validation.py
git commit -m "feat(research): add exact Decimal ranks, Spearman, and distribution primitives"
```

---

## Task 3: Frozen Sample Validation, Manifest Hashes, Method Score Rebuilding

**Files:**
- Create content: `src/dragon_radar/research/samples.py`
- Test: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append fixture builders and failing sample tests.**

Append to the test file:

First merge these additions into the single top import block (stdlib group
stays alphabetical; do not duplicate names already imported in Task 1):

```python
import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
```

Then append:

```python
from dragon_radar.readmodels import sentiment_research as method
from dragon_radar.research.models import (
    ComponentCounts as Counts,
    DailyComponents,
    InputManifest,
    SamplePack,
)
from dragon_radar.research.samples import (
    ValidatedPack,
    canonical_payload_hash,
    validate_pack,
)


def calendar_days(start: date, n: int) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(n))


def daily(day: date, breadth: tuple[int, int], limit_balance: tuple[int, int],
          promotion: tuple[int, int]) -> DailyComponents:
    return DailyComponents(
        day,
        Counts(*breadth),
        Counts(*limit_balance),
        Counts(*promotion),
    )


def _canonical_hash(payload) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _components_payload(components):
    return [
        [
            item.trade_date.isoformat(),
            item.breadth.numerator,
            item.breadth.denominator,
            item.limit_balance.numerator,
            item.limit_balance.denominator,
            item.promotion.numerator,
            item.promotion.denominator,
        ]
        for item in components
    ]


def _closes_payload(closes):
    return [
        [day.isoformat(), str(price.normalize())]
        for day, price in sorted(closes.items())
    ]


def _manifest(name, rows, as_of, *, adjustment="raw",
              frozen_at="2026-10-27T00:00:00+00:00"):
    return InputManifest(
        name=name,
        source_version="fixture-v1",
        frozen_at=frozen_at,
        as_of=as_of,
        row_count=len(rows),
        adjustment=adjustment,
        sha256=_canonical_hash(rows),
    )


def make_manifests(components, target_closes, benchmark_closes, as_of):
    return (
        _manifest("components", _components_payload(components), as_of),
        _manifest("target_closes", _closes_payload(target_closes), as_of),
        _manifest("benchmark_closes", _closes_payload(benchmark_closes), as_of),
    )


def make_pack(*, calendar, components, target_closes, benchmark_closes,
              as_of=None, synthetic=True, seed=None, pack_id="fixture-pack",
              generator="test-fixture:hand_built", target_name="fixture-target",
              benchmark_name="csi300"):
    if as_of is None:
        as_of = calendar[-1]
    return SamplePack(
        pack_id=pack_id,
        synthetic=synthetic,
        generator=generator,
        seed=seed,
        as_of=as_of,
        calendar=calendar,
        components=tuple(components),
        target_name=target_name,
        benchmark_name=benchmark_name,
        target_closes=dict(target_closes),
        benchmark_closes=dict(benchmark_closes),
        manifests=make_manifests(
            tuple(components), target_closes, benchmark_closes, as_of
        ),
    )


def const_closes(calendar, value="100.00"):
    return {day: Decimal(value) for day in calendar}


def test_validate_pack_rebuilds_method_scores():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 4)
    components = [
        daily(calendar[0], (60, 100), (100, 100), (50, 100)),
        # Zero DENOMINATOR on every component -> unavailable -> null score.
        # (Numerator 0 with a positive denominator would be a valid ratio 0.)
        daily(calendar[1], (0, 0), (0, 0), (0, 0)),
        daily(calendar[2], (20, 40), (2, 8), (3, 6)),
        daily(calendar[3], (100, 100), (100, 100), (100, 100)),
    ]
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=const_closes(calendar),
                     benchmark_closes=const_closes(calendar))
    validated = validate_pack(pack)
    assert isinstance(validated, ValidatedPack)
    assert validated.valid_dates == tuple(
        point["trade_date"]
        for point in validated.points
        if point["score"] is not None
    )
    first = validated.points_by_date[calendar[0]]
    # (0.6 + 1.0 + 0.5) / 3 * 100 = 70.00 -> repair phase from the real method.
    assert first["score"] == Decimal("70.00")
    assert first["phase"] == method.PHASE_REPAIR
    missing = validated.points_by_date[calendar[1]]
    assert missing["score"] is None
    assert set(missing["reasons"]) == {
        method.REASON_NO_VALID_DAILY_RETURNS,
        method.REASON_NO_LIMIT_EVENTS,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    }
    assert validated.points_by_date[calendar[3]]["score"] == Decimal("100.00")
    assert validated.as_of_index == len(calendar) - 1


def test_manifest_tampering_and_row_count_mismatch_raise():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=closes, benchmark_closes=closes)
    tampered = InputManifest(
        name="components",
        source_version="fixture-v1",
        frozen_at="2026-10-27T00:00:00+00:00",
        as_of=pack.as_of,
        row_count=3,
        adjustment="raw",
        sha256="a" * 64,
    )
    # Slotted frozen dataclasses expose no __dict__: copy with replace().
    with pytest.raises(ResearchValidationError, match="sha256"):
        validate_pack(
            replace(pack, manifests=(tampered, *pack.manifests[1:]))
        )
    wrong_rows = replace(pack.manifests[0], row_count=9)
    with pytest.raises(ResearchValidationError, match="row_count"):
        validate_pack(
            replace(pack, manifests=(wrong_rows, *pack.manifests[1:]))
        )


def test_pack_structure_and_pit_violations_raise():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    good = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)

    # Non-ascending calendar.
    with pytest.raises(ResearchValidationError):
        validate_pack(make_pack(calendar=(calendar[1], calendar[0]),
                                components=good[:2],
                                target_closes=closes, benchmark_closes=closes,
                                as_of=calendar[1]))
    # Components after as_of are a look-ahead violation.
    with pytest.raises(ResearchValidationError, match="components_after_as_of"):
        validate_pack(make_pack(calendar=calendar, components=good,
                                target_closes=closes, benchmark_closes=closes,
                                as_of=calendar[1]))
    # Duplicate component dates.
    dup = [good[0], good[0]]
    with pytest.raises(ResearchValidationError):
        validate_pack(make_pack(calendar=calendar, components=dup,
                                target_closes=closes, benchmark_closes=closes))
    # Zero or negative close rejected.
    bad_closes = {day: Decimal("0") for day in calendar}
    with pytest.raises(ResearchValidationError):
        validate_pack(make_pack(calendar=calendar, components=good,
                                target_closes=bad_closes, benchmark_closes=closes))
    # Counts violating 0 <= numerator <= denominator.
    bad_counts = [daily(calendar[0], (3, 2), (1, 2), (1, 2))]
    with pytest.raises(ResearchValidationError):
        validate_pack(make_pack(calendar=calendar, components=bad_counts,
                                target_closes=closes, benchmark_closes=closes))
    # Naive frozen_at rejected.
    base = make_pack(
        calendar=calendar,
        components=good,
        target_closes=closes,
        benchmark_closes=closes,
    )
    manifests = tuple(
        replace(m, frozen_at="2026-10-27T00:00:00")
        if m.name == "components"
        else m
        for m in base.manifests
    )
    with pytest.raises(ResearchValidationError, match="timezone"):
        validate_pack(replace(base, manifests=manifests))


def test_canonical_payload_hash_matches_analysis_jobs_convention():
    assert canonical_payload_hash({"b": 2, "a": 1}) == _canonical_hash({"b": 2, "a": 1})
    assert len(canonical_payload_hash([])) == 64
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: FAIL on `ModuleNotFoundError: dragon_radar.research.samples` (`ValidatedPack` import).

- [ ] **Step 3: Implement `samples.py` fully.**

```python
"""Fail-closed frozen sample validation and method score rebuilding."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import dragon_radar.readmodels.sentiment_research as method
from dragon_radar.research.models import (
    STREAM_BENCHMARK_CLOSES,
    STREAM_COMPONENTS,
    STREAM_NAMES,
    STREAM_TARGET_CLOSES,
    ComponentCounts,
    DailyComponents,
    InputManifest,
    ResearchValidationError,
    SamplePack,
)

_COMPONENT_UNAVAILABLE_REASONS = {
    method.COMPONENT_BREADTH: method.REASON_NO_VALID_DAILY_RETURNS,
    method.COMPONENT_LIMIT_BALANCE: method.REASON_NO_LIMIT_EVENTS,
    method.COMPONENT_PROMOTION: (
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET
    ),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ValidatedPack:
    pack: SamplePack
    calendar: tuple[date, ...]
    points: tuple[dict[str, object], ...]
    points_by_date: dict[date, dict[str, object]]
    valid_dates: tuple[date, ...]
    target_closes: dict[date, Decimal]
    benchmark_closes: dict[date, Decimal]
    as_of_index: int


def canonical_payload_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_token(value: Decimal) -> str:
    return str(value.normalize())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchValidationError(message)


def _require_text(value: object, label: str) -> None:
    _require(type(value) is str and value != "", f"{label} must be a non-empty string")


def _components_payload(
    components: tuple[DailyComponents, ...],
) -> list[list[object]]:
    return [
        [
            item.trade_date.isoformat(),
            item.breadth.numerator,
            item.breadth.denominator,
            item.limit_balance.numerator,
            item.limit_balance.denominator,
            item.promotion.numerator,
            item.promotion.denominator,
        ]
        for item in components
    ]


def _closes_payload(closes: dict[date, Decimal]) -> list[list[str]]:
    return [
        [day.isoformat(), _decimal_token(price)]
        for day, price in sorted(closes.items())
    ]


def _validate_closes(
    mapping: object, calendar_set: set[date], label: str
) -> dict[date, Decimal]:
    _require(isinstance(mapping, Mapping), f"{label} must be a mapping")
    result: dict[date, Decimal] = {}
    for day, price in mapping.items():
        _require(type(day) is date, f"{label} keys must be dates")
        _require(day in calendar_set, f"{label} dates must be calendar dates")
        _require(
            isinstance(price, Decimal) and not isinstance(price, bool),
            f"{label} values must be Decimal",
        )
        _require(
            price.is_finite() and price > 0,
            f"{label} values must be finite and positive",
        )
        result[day] = price
    return result


def _validate_manifests(
    manifests: object,
    as_of: date,
    components: tuple[DailyComponents, ...],
    target: dict[date, Decimal],
    benchmark: dict[date, Decimal],
) -> tuple[InputManifest, ...]:
    _require(
        type(manifests) is tuple and len(manifests) == len(STREAM_NAMES),
        "manifests must contain exactly three entries",
    )
    by_name: dict[str, InputManifest] = {}
    for manifest in manifests:
        _require(type(manifest) is InputManifest, "manifest must be InputManifest")
        _require(manifest.name in STREAM_NAMES, "manifest name is unknown")
        _require(manifest.name not in by_name, "manifest names must be unique")
        _require_text(manifest.source_version, "source_version")
        _require_text(manifest.adjustment, "adjustment")
        _require(type(manifest.sha256) is str, "sha256 must be a string")
        _require(
            _SHA256_RE.fullmatch(manifest.sha256) is not None,
            "sha256 must be 64 lowercase hex characters",
        )
        _require(
            type(manifest.row_count) is int and manifest.row_count >= 0,
            "row_count must be a non-negative int",
        )
        _require(
            type(manifest.as_of) is date and manifest.as_of == as_of,
            "manifest as_of must equal pack as_of",
        )
        _require(type(manifest.frozen_at) is str, "frozen_at must be a string")
        parsed = datetime.fromisoformat(manifest.frozen_at)
        _require(
            parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "frozen_at must be timezone aware",
        )
        by_name[manifest.name] = manifest
    payloads = {
        STREAM_COMPONENTS: _components_payload(components),
        STREAM_TARGET_CLOSES: _closes_payload(target),
        STREAM_BENCHMARK_CLOSES: _closes_payload(benchmark),
    }
    for name in STREAM_NAMES:
        manifest = by_name[name]
        _require(
            manifest.row_count == len(payloads[name]),
            f"{name} row_count mismatch",
        )
        _require(
            manifest.sha256 == canonical_payload_hash(payloads[name]),
            f"{name} sha256 mismatch",
        )
    return tuple(by_name[name] for name in STREAM_NAMES)


def _rebuild_point(item: DailyComponents) -> dict[str, object]:
    return method.build_research_point(
        item.trade_date,
        {
            method.COMPONENT_BREADTH: method.build_component(
                item.breadth.numerator,
                item.breadth.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_BREADTH],
            ),
            method.COMPONENT_LIMIT_BALANCE: method.build_component(
                item.limit_balance.numerator,
                item.limit_balance.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_LIMIT_BALANCE],
            ),
            method.COMPONENT_PROMOTION: method.build_component(
                item.promotion.numerator,
                item.promotion.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_PROMOTION],
            ),
        },
    )


def validate_pack(pack: SamplePack) -> ValidatedPack:
    _require(type(pack) is SamplePack, "pack must be a SamplePack")
    _require_text(pack.pack_id, "pack_id")
    _require_text(pack.generator, "generator")
    _require_text(pack.target_name, "target_name")
    _require_text(pack.benchmark_name, "benchmark_name")
    _require(type(pack.synthetic) is bool, "synthetic must be a bool")
    _require(
        pack.seed is None
        or (type(pack.seed) is int and not isinstance(pack.seed, bool)),
        "seed must be int or None",
    )

    calendar = pack.calendar
    _require(type(calendar) is tuple and calendar, "calendar must be a non-empty tuple")
    for day in calendar:
        _require(type(day) is date, "calendar dates must be dates")
    _require(
        len(set(calendar)) == len(calendar)
        and calendar == tuple(sorted(calendar)),
        "calendar must be strictly ascending dates",
    )
    as_of = pack.as_of
    _require(type(as_of) is date, "as_of must be a date")
    _require(as_of in calendar, "as_of must be a calendar date")

    components = pack.components
    _require(type(components) is tuple, "components must be a tuple")
    component_dates: list[date] = []
    for item in components:
        _require(type(item) is DailyComponents, "components must hold DailyComponents")
        _require(type(item.trade_date) is date, "component trade_date must be a date")
        component_dates.append(item.trade_date)
        for counts in (item.breadth, item.limit_balance, item.promotion):
            _require(type(counts) is ComponentCounts, "component must hold counts")
            _require(
                type(counts.numerator) is int and type(counts.denominator) is int,
                "component counts must be ints",
            )
            _require(
                0 <= counts.numerator <= counts.denominator,
                "component counts must satisfy 0 <= numerator <= denominator",
            )
    _require(
        component_dates == sorted(component_dates)
        and len(set(component_dates)) == len(component_dates),
        "component dates must be strictly ascending and unique",
    )
    calendar_set = set(calendar)
    _require(
        all(day in calendar_set for day in component_dates),
        "component dates must be calendar dates",
    )
    _require(
        all(day <= as_of for day in component_dates),
        "components_after_as_of",
    )

    target = _validate_closes(pack.target_closes, calendar_set, "target_closes")
    benchmark = _validate_closes(
        pack.benchmark_closes, calendar_set, "benchmark_closes"
    )
    _validate_manifests(pack.manifests, as_of, components, target, benchmark)

    points = tuple(_rebuild_point(item) for item in components)
    method.materialize_sentiment_research(list(points))
    points_by_date = {
        point["trade_date"]: point for point in points  # type: ignore[index]
    }
    valid_dates = tuple(
        point["trade_date"]  # type: ignore[index]
        for point in points
        if point["score"] is not None  # type: ignore[index]
    )
    return ValidatedPack(
        pack=pack,
        calendar=calendar,
        points=points,
        points_by_date=points_by_date,
        valid_dates=valid_dates,
        target_closes=target,
        benchmark_closes=benchmark,
        as_of_index=calendar.index(as_of),
    )
```

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: PASS (including the purity scan, since `samples.py` imports only stdlib and `readmodels.sentiment_research`).

Run: `uv run ruff check src/dragon_radar/research/samples.py; uv run ruff format src/dragon_radar/research/samples.py`

```powershell
git add -- src/dragon_radar/research/samples.py tests/test_reset_research_validation.py
git commit -m "feat(research): validate frozen packs, verify manifest hashes, rebuild method scores"
```

---

## Task 4: Contemporaneous Factor Health Block

**Files:**
- Create content: `src/dragon_radar/research/factor_health.py`
- Test: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append failing health tests.**

```python
from dragon_radar.research.factor_health import build_factor_health


def test_factor_health_counts_coverage_reasons_and_holes():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 6)
    components = [
        daily(calendar[0], (50, 100), (50, 100), (50, 100)),
        # calendar[1] absent entirely -> present gap
        daily(calendar[2], (50, 100), (0, 0), (50, 100)),  # null score day
        daily(calendar[3], (50, 100), (50, 100), (50, 100)),
        daily(calendar[4], (50, 100), (50, 100), (50, 100)),
        # calendar[5] absent tail (not a hole between present days)
    ]
    closes = const_closes(calendar)
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=closes, benchmark_closes=closes)
    health = build_factor_health(validate_pack(pack))
    assert health["calendar_days"] == 6
    assert health["present_days"] == 4
    assert health["valid_days"] == 3
    assert health["absent_days"] == 2
    assert health["null_days"] == 1
    assert health["coverage_ratio"] == Decimal("3") / Decimal("6")
    assert health["null_reasons"] == {
        method.REASON_NO_VALID_DAILY_RETURNS: 0,
        method.REASON_NO_LIMIT_EVENTS: 1,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET: 0,
    }
    assert health["calendar_holes"] == [calendar[1]]
    assert health["sample_window"] == {
        "first": calendar[0],
        "last": calendar[4],
    }


def test_factor_health_distribution_and_perfect_rank_correlations():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 5)
    ratios = ((1, 10), (2, 10), (3, 10), (4, 10), (5, 10))
    components = [
        daily(day, ratios[i], ratios[i], ratios[i]) for i, day in enumerate(calendar)
    ]
    closes = const_closes(calendar)
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=closes, benchmark_closes=closes)
    health = build_factor_health(validate_pack(pack))
    distribution = health["score_distribution"]
    assert distribution["n"] == 5
    assert distribution["zero_count"] == 0
    assert distribution["hundred_count"] == 0
    # scores are 10,20,30,40,50
    assert distribution["min"] == Decimal("10.00")
    assert distribution["max"] == Decimal("50.00")
    assert distribution["median"] == Decimal("30.00")
    assert distribution["top_tie_share"] == Decimal("1") / Decimal("5")
    for key in method.COMPONENT_KEYS:
        block = health["components"][key]
        assert block["available_days"] == 5
        assert block["zero_denominator_days"] == 0
        assert block["distribution"]["n"] == 5
        assert block["distribution"]["min"] == Decimal("0.1")
        assert block["distribution"]["max"] == Decimal("0.5")
    correlations = health["rank_correlations"]
    for name in (
        "breadth_limit_balance",
        "breadth_promotion",
        "limit_balance_promotion",
        "breadth_score",
        "limit_balance_score",
        "promotion_score",
    ):
        assert correlations[name] == {"value": Decimal("1"), "reason": None}


def test_factor_health_empty_sample_degrades_without_raising():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 3)
    closes = const_closes(calendar)
    pack = make_pack(calendar=calendar, components=[],
                     target_closes=closes, benchmark_closes=closes)
    health = build_factor_health(validate_pack(pack))
    assert health["valid_days"] == 0
    assert health["coverage_ratio"] == Decimal("0")
    assert health["score_distribution"]["n"] == 0
    assert health["sample_window"] == {"first": None, "last": None}
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: FAIL on import of `dragon_radar.research.factor_health`.

- [ ] **Step 3: Implement `factor_health.py` fully.**

```python
"""Contemporaneous factor health: coverage, distribution, rank correlation."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from typing import cast

from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.readmodels import sentiment_research as method
from dragon_radar.research import statistics as stats
from dragon_radar.research.samples import ValidatedPack

_NULL_REASON_KEYS = (
    method.REASON_NO_VALID_DAILY_RETURNS,
    method.REASON_NO_LIMIT_EVENTS,
    method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
)
_PAIRS = (
    (method.COMPONENT_BREADTH, method.COMPONENT_LIMIT_BALANCE),
    (method.COMPONENT_BREADTH, method.COMPONENT_PROMOTION),
    (method.COMPONENT_LIMIT_BALANCE, method.COMPONENT_PROMOTION),
)
_DISTRIBUTION_KEYS = ("n", "min", "max", "mean", "median", "p25", "p75")


def _ratio(component: dict[str, object]) -> Decimal:
    # Callers guarantee denominator > 0 (zero-denominator days are counted,
    # never ratioed), so no defensive guard is needed here.
    numerator = cast(int, component["numerator"])
    denominator = cast(int, component["denominator"])
    with localcontext(_decimal_context()):
        return Decimal(numerator) / Decimal(denominator)


def _empty_distribution() -> dict[str, None]:
    return {key: None for key in _DISTRIBUTION_KEYS}


def _score_distribution(scores: list[Decimal]) -> dict[str, object]:
    if scores:
        base = stats.decimal_distribution(scores)
        multiplicity = Counter(scores)
        top_tie_share = Decimal(max(multiplicity.values())) / Decimal(len(scores))
    else:
        base = _empty_distribution()
        top_tie_share = None
    base.update(
        {
            "zero_count": sum(1 for score in scores if score == 0),
            "hundred_count": sum(1 for score in scores if score == 100),
            "top_tie_share": top_tie_share,
        }
    )
    return base


def _component_blocks(points: tuple[dict[str, object], ...]) -> dict[str, object]:
    blocks: dict[str, object] = {}
    for key in method.COMPONENT_KEYS:
        ratios: list[Decimal] = []
        zero_denominator = 0
        for point in points:
            component = cast(dict[str, object], cast(dict, point["components"])[key])
            if cast(int, component["denominator"]) == 0:
                zero_denominator += 1
            else:
                ratios.append(_ratio(component))
        blocks[key] = {
            "available_days": len(ratios),
            "zero_denominator_days": zero_denominator,
            "distribution": (
                stats.decimal_distribution(ratios) if ratios else _empty_distribution()
            ),
        }
    return blocks


def _rank_correlations(valid_points: list[dict[str, object]]) -> dict[str, object]:
    series: dict[str, list[Decimal]] = {key: [] for key in method.COMPONENT_KEYS}
    scores: list[Decimal] = []
    for point in valid_points:
        components = cast(dict[str, dict[str, object]], point["components"])
        for key in method.COMPONENT_KEYS:
            series[key].append(_ratio(components[key]))
        scores.append(cast(Decimal, point["score"]))

    def correlation(name: str, left: list[Decimal], right: list[Decimal]):
        result = stats.spearman(left, right)
        return {"value": result.value, "reason": result.reason, "__name": name}

    raw = [
        correlation(f"{left}_{right}", series[left], series[right])
        for left, right in _PAIRS
    ]
    raw += [
        correlation(f"{key}_score", series[key], scores)
        for key in method.COMPONENT_KEYS
    ]
    return {cast(str, item.pop("__name")): item for item in raw}


def build_factor_health(validated: ValidatedPack) -> dict[str, object]:
    calendar = validated.calendar
    points = validated.points
    present_dates = [cast(date, point["trade_date"]) for point in points]
    present = set(present_dates)
    valid_points = [
        point
        for point in points
        if cast(Decimal | None, point["score"]) is not None
    ]
    scores = [cast(Decimal, point["score"]) for point in valid_points]

    null_reasons = {key: 0 for key in _NULL_REASON_KEYS}
    for point in points:
        if cast(Decimal | None, point["score"]) is None:
            for reason in cast(list[str], point["reasons"]):
                null_reasons[reason] += 1

    holes: list[date] = []
    if present_dates:
        low = calendar.index(min(present_dates))
        high = calendar.index(max(present_dates))
        holes = [
            calendar[index]
            for index in range(low + 1, high)
            if calendar[index] not in present
        ]

    with localcontext(_decimal_context()):
        coverage_ratio = Decimal(len(valid_points)) / Decimal(len(calendar))

    return {
        "calendar_days": len(calendar),
        "present_days": len(points),
        "valid_days": len(valid_points),
        "absent_days": len(calendar) - len(points),
        "null_days": len(points) - len(valid_points),
        "coverage_ratio": coverage_ratio,
        "null_reasons": null_reasons,
        "calendar_holes": holes,
        "score_distribution": _score_distribution(scores),
        "components": _component_blocks(points),
        "rank_correlations": _rank_correlations(cast(list, valid_points)),
        "sample_window": {
            "first": valid_points[0]["trade_date"] if valid_points else None,
            "last": valid_points[-1]["trade_date"] if valid_points else None,
        },
    }
```

- [ ] **Step 3b: Confirm reason-key totals can only be the method's three codes.**

The health test asserts the full `null_reasons` mapping; do not add any other
key. If the method ever raises a reason outside `_NULL_REASON_KEYS`,
`null_reasons[reason] += 1` must fail loudly with `KeyError` — that is
intended fail-closed behavior, do not switch to `.get(reason, 0)`.

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: PASS.

Run: `uv run ruff check src/dragon_radar/research/factor_health.py; uv run ruff format src/dragon_radar/research/factor_health.py`

```powershell
git add -- src/dragon_radar/research/factor_health.py tests/test_reset_research_validation.py
git commit -m "feat(research): add contemporaneous factor health block"
```

---

## Task 5: PIT Labels, Non-Overlapping Grid, Folds, Forward Metrics

**Files:**
- Create content: `src/dragon_radar/research/forward_eval.py`
- Test: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append failing forward-evaluation tests.**

`DEFAULT_CONFIG` and `ValidationConfig` are already imported at the top of
the file (Task 1); merge only the new first-party import
(`from dragon_radar.research.forward_eval import evaluate_all, evaluate_horizon`)
into the import group, then append:

```python
def _closes_from_values(calendar, values):
    assert len(values) == len(calendar)
    return {day: Decimal(str(value)) for day, value in zip(calendar, values)}


def _monotone_forward_pack(n_days=45, *, horizon=1):
    """Closes engineered so every on-grid h-step forward return strictly ascends.

    PIT discipline: component records stop at ``as_of``; the calendar (and
    closes) extend past it solely to provide forward labels.
    """
    start = date(2026, 3, 2)
    calendar = calendar_days(start, n_days)
    as_of_index = n_days - horizon - 2
    as_of = calendar[as_of_index]
    components = [
        daily(day, (i + 1, 100), (i + 1, 100), (i + 1, 100))
        for i, day in enumerate(calendar[: as_of_index + 1])
    ]
    benchmark = const_closes(calendar, "100.00")
    # Every close exists (no None -> Decimal tokens). Only on-grid entry/exit
    # closes matter; flat 100 elsewhere keeps the mapping complete.
    values = [100.0] * n_days
    # On the h-spaced grid: slot i enters at C[i+1], exits at C[i+1+h], and
    # r = C[i+1+h]/C[i+1] - 1 = 0.0005 * (i+1), strictly ascending in slot i.
    for i in range(0, n_days - horizon - 1, horizon):
        entry = values[i + 1]
        values[i + 1 + horizon] = round(entry * (1 + 0.0005 * (i + 1)), 12)
    target = _closes_from_values(calendar, values)
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=target, benchmark_closes=benchmark,
                     as_of=as_of)
    return pack, calendar


def test_monotone_construction_yields_unit_rank_ic_and_strict_quintiles():
    pack, _calendar = _monotone_forward_pack()
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    assert block["horizon"] == 1
    assert block["evaluation_kind"] == "time_serial_non_overlapping"
    assert block["pooled"]["rank_ic"] == Decimal("1")
    assert block["direction_hit_rate"] == Decimal("1")
    buckets = block["quintiles"]["buckets"]
    means = [bucket["mean_excess"] for bucket in buckets]
    assert all(bucket["n"] > 0 for bucket in buckets)
    assert all(means[i + 1] > means[i] for i in range(4))
    assert block["quintiles"]["monotonic"] is True
    assert block["quintiles"]["adjacent_agreement"] == Decimal("1")
    assert block["quintiles"]["spread"] == means[4] - means[0]
    for fold in block["folds"]:
        assert fold["rank_ic"] == Decimal("1")
    assert block["fold_summary"]["positive_share"] == Decimal("1")
    assert block["fold_summary"]["icir"] is None
    assert block["fold_summary"]["icir_reason"] == "zero_variance"


def test_hand_computed_returns_and_entry_at_next_tradable_point():
    start = date(2026, 3, 2)
    calendar = calendar_days(start, 5)
    # Strictly increasing scores (40, 41, 42) so the pooled rank IC is defined.
    components = [
        daily(day, (40 + i, 100), (40 + i, 100), (40 + i, 100))
        for i, day in enumerate(calendar[:3])
    ]
    # Signal calendar[0] enters at C1=100, exits at C2=101   -> r = +0.01
    # Signal calendar[1] enters at C2=101, exits at C3=98.98 -> r = -0.02
    # Signal calendar[2] enters at C3=98.98, exits at C4=100.9596 -> r = +0.02
    target = _closes_from_values(calendar, [100, 100, 101, 98.98, 100.9596])
    benchmark = const_closes(calendar, "100.00")
    pack = make_pack(calendar=calendar, components=components,
                     target_closes=target, benchmark_closes=benchmark,
                     as_of=calendar[2])
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    first = block["observations"][0]
    assert first["trade_date"] == calendar[0]
    assert first["entry_date"] == calendar[1]
    assert first["exit_date"] == calendar[2]
    assert first["r_target"] == Decimal("101") / Decimal("100") - 1
    assert first["r_benchmark"] == 0
    assert first["r_excess"] == first["r_target"]
    # Off-by-one reversal: shifting the signal one day changes the label.
    second = block["observations"][1]
    assert second["r_target"] == Decimal("98.98") / Decimal("101") - 1
    assert second["r_target"] != first["r_target"]
    # Score ranks 1,2,3 vs excess ranks [0.01,-0.02,0.02] -> [2,1,3] -> 0.5.
    assert block["pooled"]["rank_ic"] == Decimal("0.5")


def test_grid_windows_are_interior_disjoint_for_all_horizons():
    pack, calendar = _monotone_forward_pack(n_days=120)
    validated = validate_pack(pack)
    for horizon in (1, 5, 20):
        block = evaluate_horizon(validated, DEFAULT_CONFIG, horizon)
        intervals = [
            (
                calendar.index(obs["entry_date"]),
                calendar.index(obs["exit_date"]),
            )
            for obs in block["observations"]
        ]
        for earlier, later in zip(intervals, intervals[1:], strict=True):
            assert later[0] >= earlier[1], (horizon, earlier, later)


def test_drops_accounting_out_of_window_missing_close_and_skips():
    start = date(2026, 3, 2)
    calendar = calendar_days(start, 8)
    # Signals exist every day except index 2 (slot gap when h=2-ish via h=1 grid);
    # h=1 grid origin is index 0; index 2 slot has no signal.
    components = [
        daily(calendar[i], (50, 100), (50, 100), (50, 100))
        for i in (0, 1, 3, 4, 5)
    ]
    target = dict(const_closes(calendar, "100"))
    benchmark = dict(const_closes(calendar, "100"))
    # Remove the exit close needed for the signal at index 5 (exit index 7? h=1
    # needs index 6): drop target close at index 6 to force missing_close.
    del target[calendar[6]]
    # as_of at the final day: signals at 5..7 that would need future closes are
    # out-of-window; give components at 6 and 7 too.
    components += [
        daily(calendar[6], (50, 100), (50, 100), (50, 100)),
        daily(calendar[7], (50, 100), (50, 100), (50, 100)),
    ]
    pack = make_pack(calendar=calendar, components=sorted(components, key=lambda d: d.trade_date),
                     target_closes=target, benchmark_closes=benchmark,
                     as_of=calendar[7])
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    assert block["grid"]["slots_skipped"]["slot_without_signal"] == 1
    assert block["grid"]["drops"]["missing_close"] >= 1
    assert block["grid"]["drops"]["out_of_window"] >= 1
    assert block["grid"]["signals_total"] == 7
    assert block["grid"]["slots_total"] == block["grid"]["slots_accepted"] + block[
        "grid"
    ]["slots_skipped"]["slot_without_signal"] + block["grid"]["drops"]["missing_close"]


def test_balanced_fold_sizes_and_insufficiency_gates():
    pack, _ = _monotone_forward_pack(n_days=45)
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    sizes = [fold["n"] for fold in block["folds"]]
    assert sum(sizes) == len(block["observations"])
    assert max(sizes) - min(sizes) <= 1
    # n_days=45 -> as_of index 42 -> 43 accepted observations; 43 >= 40 and
    # K = 43 // 8 = 5 folds, so no observation/fold gate fires.
    assert block["gate_failures"] == []
    short = evaluate_horizon(
        validated, ValidationConfig(horizons=(1,), min_signals_per_fold=8), 1
    )
    assert short["fold_summary"]["folds_total"] == 5
    tiny_pack, _ = _monotone_forward_pack(n_days=10)
    tiny = evaluate_horizon(validate_pack(tiny_pack), DEFAULT_CONFIG, 1)
    assert "insufficient_observations" in tiny["gate_failures"]
    assert "insufficient_folds" in tiny["gate_failures"]
    assert "quintile_too_small" in tiny["gate_failures"]
    # Gate failures never suppress computable statistics (spec): the pooled
    # RankIC stays exactly 1; G6 fires only when the pooled IC itself is null.
    assert tiny["pooled"]["rank_ic"] == Decimal("1")


def test_evaluate_all_respects_horizon_subset_and_constant_score_degrades():
    pack, _ = _monotone_forward_pack(n_days=60)
    validated = validate_pack(pack)
    subset = ValidationConfig(horizons=(1, 5))
    evaluation = evaluate_all(validated, subset)
    assert set(evaluation) == {"1", "5"}
    constant = [
        daily(day, (50, 100), (50, 100), (50, 100))
        for day in pack.calendar[: pack.as_of_index + 1]
    ]
    flat_pack = make_pack(calendar=pack.calendar, components=constant,
                          target_closes=pack.target_closes,
                          benchmark_closes=pack.benchmark_closes,
                          as_of=pack.as_of)
    block = evaluate_horizon(validate_pack(flat_pack), DEFAULT_CONFIG, 1)
    assert block["pooled"]["rank_ic"] is None
    assert block["pooled"]["reason"] == "zero_variance"
    assert block["quintiles"]["monotonic"] is None
    assert block["direction_hit_rate"] is None
    assert "pooled_ic_unavailable" in block["gate_failures"]
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: FAIL on importing `evaluate_all`/`evaluate_horizon`.

- [ ] **Step 3: Implement `forward_eval.py` fully.**

```python
"""Point-in-time forward evaluation over a strictly non-overlapping grid."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import cast

from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.research import statistics as stats
from dragon_radar.research.models import (
    EVALUATION_KIND,
    GATE_INSUFFICIENT_FOLDS,
    GATE_INSUFFICIENT_OBSERVATIONS,
    GATE_POOLED_IC_UNAVAILABLE,
    GATE_QUINTILE_TOO_SMALL,
    REASON_EMPTY_BUCKET,
    REASON_INSUFFICIENT_FOLDS,
    REASON_INSUFFICIENT_SAMPLE,
    REASON_MISSING_CLOSE,
    REASON_OUT_OF_WINDOW,
    REASON_POOLED_IC_UNAVAILABLE,
    REASON_SLOT_WITHOUT_SIGNAL,
    REASON_ZERO_VARIANCE,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.samples import ValidatedPack
from dragon_radar.research.statistics import RankCorrelation

_ZERO = Decimal(0)
_ONE = Decimal(1)
_FOUR = Decimal(4)


def evaluate_all(
    validated: ValidatedPack, config: ValidationConfig
) -> dict[str, dict[str, object]]:
    cfg = validate_config(config)
    return {
        str(horizon): evaluate_horizon(validated, cfg, horizon)
        for horizon in cfg.horizons
    }


def _entry_exit_return(closes, calendar, entry_index: int, exit_index: int):
    # ``closes`` is keyed by calendar date, so map grid indices through the
    # calendar; labels_ready() already guaranteed both dates are present.
    with localcontext(_decimal_context()):
        return (
            closes[calendar[exit_index]] / closes[calendar[entry_index]] - _ONE
        )


def _fold_partition(n: int, min_size: int) -> list[tuple[int, int]]:
    if n < min_size:
        return []
    fold_count = n // min_size
    base = n // fold_count
    extra = n % fold_count
    sizes = [base + 1] * extra + [base] * (fold_count - extra)
    bounds: list[tuple[int, int]] = []
    start = 0
    for size in sizes:
        bounds.append((start, start + size))
        start += size
    return bounds


def _fold_records(observations, min_size: int):
    folds = []
    for index, (start, end) in enumerate(_fold_partition(len(observations), min_size)):
        members = observations[start:end]
        if not members:
            continue
        scores = [cast(Decimal, item["score"]) for item in members]
        excess = [cast(Decimal, item["r_excess"]) for item in members]
        correlation = (
            stats.spearman(scores, excess) if len(members) >= 2
            else RankCorrelation(None, REASON_INSUFFICIENT_SAMPLE)
        )
        folds.append(
            {
                "fold_index": index,
                "start_date": members[0]["trade_date"],
                "end_date": members[-1]["trade_date"],
                "n": len(members),
                "rank_ic": correlation.value,
                "reason": correlation.reason,
            }
        )
    return folds


def _fold_summary(folds) -> dict[str, object]:
    values = [cast(Decimal, fold["rank_ic"]) for fold in folds if fold["rank_ic"] is not None]
    if not values:
        mean = None
        std = None
        icir = None
        icir_reason = REASON_INSUFFICIENT_FOLDS
        positive_share = None
    else:
        mean = stats.decimal_mean(values)
        if len(values) < 2:
            std = None
            icir = None
            icir_reason = REASON_INSUFFICIENT_FOLDS
        else:
            std = cast(Decimal, stats.decimal_sample_std(values))
            if std == 0:
                icir = None
                icir_reason = REASON_ZERO_VARIANCE
            else:
                icir = mean / std
                icir_reason = None
        positive_share = Decimal(sum(1 for value in values if value > 0)) / Decimal(
            len(values)
        )
    return {
        "folds_total": len(folds),
        "nonnull_ic": len(values),
        "ic_mean": mean,
        "ic_std": std,
        "icir": icir,
        "icir_reason": icir_reason,
        "positive_share": positive_share,
    }


def _quintile_block(observations, pooled_ic, min_quintile_size: int) -> dict[str, object]:
    buckets = [
        {"index": index, "n": 0, "sum_excess": _ZERO, "sum_target": _ZERO}
        for index in range(1, 6)
    ]
    if observations:
        scores = [cast(Decimal, item["score"]) for item in observations]
        memberships = stats.quintile_buckets(stats.average_ranks(scores))
        for item, bucket_index in zip(observations, memberships, strict=True):
            bucket = buckets[bucket_index - 1]
            bucket["n"] += 1
            bucket["sum_excess"] += cast(Decimal, item["r_excess"])
            bucket["sum_target"] += cast(Decimal, item["r_target"])
    finalized = []
    for bucket in buckets:
        n = cast(int, bucket["n"])
        finalized.append(
            {
                "index": cast(int, bucket["index"]),
                "n": n,
                "mean_excess": bucket["sum_excess"] / Decimal(n) if n else None,
                "mean_target": bucket["sum_target"] / Decimal(n) if n else None,
            }
        )
    means = [cast(Decimal | None, bucket["mean_excess"]) for bucket in finalized]
    if any(mean is None for mean in means):
        spread = None
        monotonic = None
        monotonic_reason = REASON_EMPTY_BUCKET
        agreement = None
    elif pooled_ic is None or pooled_ic == 0:
        spread = cast(Decimal, means[4]) - cast(Decimal, means[0])
        monotonic = None
        monotonic_reason = REASON_POOLED_IC_UNAVAILABLE
        agreement = None
    else:
        means_decimal = cast(list[Decimal], means)
        spread = means_decimal[4] - means_decimal[0]
        differences = [
            means_decimal[i + 1] - means_decimal[i] for i in range(4)
        ]
        positive = pooled_ic > 0
        monotonic = all(
            difference > 0 if positive else difference < 0
            for difference in differences
        )
        agreement = Decimal(
            sum(
                1
                for difference in differences
                if (difference > 0) == positive
            )
        ) / _FOUR
        monotonic_reason = None
    return {
        "buckets": finalized,
        "spread": spread,
        "monotonic": monotonic,
        "monotonic_reason": monotonic_reason,
        "adjacent_agreement": agreement,
    }


def evaluate_horizon(
    validated: ValidatedPack, config: ValidationConfig, horizon: int
) -> dict[str, object]:
    cfg = validate_config(config)
    calendar = validated.calendar
    as_of_index = validated.as_of_index
    points_by_date = validated.points_by_date
    target = validated.target_closes
    benchmark = validated.benchmark_closes
    total_days = len(calendar)

    signal_indices = [
        index
        for index in range(as_of_index + 1)
        if (point := points_by_date.get(calendar[index])) is not None
        and point["score"] is not None
    ]

    def labels_ready(index: int) -> bool:
        entry = index + 1
        exit_index = index + 1 + horizon
        return (
            exit_index < total_days
            and calendar[entry] in target
            and calendar[exit_index] in target
            and calendar[entry] in benchmark
            and calendar[exit_index] in benchmark
        )

    origin = next(
        (
            index
            for index in signal_indices
            if index + 1 + horizon < total_days and labels_ready(index)
        ),
        None,
    )

    slot_indices: list[int] = []
    if origin is not None:
        slot = origin
        while slot <= as_of_index and slot + 1 + horizon < total_days:
            slot_indices.append(slot)
            slot += horizon

    on_slots = set(slot_indices)
    out_of_window = sum(
        1 for index in signal_indices if index + 1 + horizon >= total_days
    )

    observations: list[dict[str, object]] = []
    slot_without_signal = 0
    missing_close = 0
    signaled_slots = 0
    for index in slot_indices:
        point = points_by_date.get(calendar[index])
        if point is None or point["score"] is None:
            slot_without_signal += 1
            continue
        signaled_slots += 1
        if not labels_ready(index):
            missing_close += 1
            continue
        entry_index = index + 1
        exit_index = index + 1 + horizon
        r_target = _entry_exit_return(target, calendar, entry_index, exit_index)
        r_benchmark = _entry_exit_return(
            benchmark, calendar, entry_index, exit_index
        )
        with localcontext(_decimal_context()):
            r_excess = r_target - r_benchmark
        observations.append(
            {
                "trade_date": calendar[index],
                "entry_date": calendar[entry_index],
                "exit_date": calendar[exit_index],
                "score": point["score"],
                "r_target": r_target,
                "r_benchmark": r_benchmark,
                "r_excess": r_excess,
            }
        )

    n = len(observations)
    off_grid = len(signal_indices) - signaled_slots - out_of_window
    grid = {
        "step_trading_days": horizon,
        "slots_total": len(slot_indices),
        "slots_accepted": n,
        "slots_skipped": {REASON_SLOT_WITHOUT_SIGNAL: slot_without_signal},
        "drops": {
            REASON_MISSING_CLOSE: missing_close,
            REASON_OUT_OF_WINDOW: out_of_window,
        },
        "signals_total": len(signal_indices),
        "off_grid_signals": off_grid,
    }

    folds = _fold_records(observations, cfg.min_signals_per_fold)
    if n >= 2:
        pooled_correlation = stats.spearman(
            [cast(Decimal, item["score"]) for item in observations],
            [cast(Decimal, item["r_excess"]) for item in observations],
        )
    else:
        pooled_correlation = RankCorrelation(None, REASON_INSUFFICIENT_SAMPLE)
    pooled = {
        "n": n,
        "rank_ic": pooled_correlation.value,
        "reason": pooled_correlation.reason,
    }
    quintiles = _quintile_block(
        observations, pooled_correlation.value, cfg.min_quintile_size
    )

    if pooled_correlation.value is None or pooled_correlation.value == 0:
        direction_hit_rate = None
    else:
        positive = pooled_correlation.value > 0
        direction_hit_rate = Decimal(
            sum(
                1
                for item in observations
                if (cast(Decimal, item["r_excess"]) > 0) == positive
            )
        ) / Decimal(n) if n else None

    gate_failures = []
    if n < cfg.min_folds * cfg.min_signals_per_fold:
        gate_failures.append(GATE_INSUFFICIENT_OBSERVATIONS)
    if len(folds) < cfg.min_folds:
        gate_failures.append(GATE_INSUFFICIENT_FOLDS)
    if any(
        cast(int, bucket["n"]) < cfg.min_quintile_size
        for bucket in cast(list, quintiles["buckets"])
    ):
        gate_failures.append(GATE_QUINTILE_TOO_SMALL)
    if pooled["rank_ic"] is None:
        gate_failures.append(GATE_POOLED_IC_UNAVAILABLE)

    return {
        "horizon": horizon,
        "evaluation_kind": EVALUATION_KIND,
        "grid_origin_date": calendar[origin] if origin is not None else None,
        "as_of": validated.pack.as_of,
        "grid": grid,
        "observations": observations,
        "folds": folds,
        "fold_summary": _fold_summary(folds),
        "pooled": pooled,
        "quintiles": quintiles,
        "direction_hit_rate": direction_hit_rate,
        "gate_failures": gate_failures,
    }
```

Important construction detail for the accounting test: the sum
`slots_total == accepted + slot_without_signal + missing_close` is an
invariant of the slot loop — keep it true for every pack. `out_of_window`
signals are never slots (the loop stops at the in-window bound), which is
why they are counted separately.

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: PASS. If the hand-built monotone fixture produces a pooled IC that
snaps to exactly 1 but a fold IC differs, keep the fixture deterministic and
debug the close construction (`values[i+1+h]` from `values[i+1]`); do not
relax any assertion.

Run: `uv run ruff check src/dragon_radar/research/forward_eval.py; uv run ruff format src/dragon_radar/research/forward_eval.py`

```powershell
git add -- src/dragon_radar/research/forward_eval.py tests/test_reset_research_validation.py
git commit -m "feat(research): add PIT non-overlapping forward evaluation with RankIC gates"
```

---

## Task 6: Versioned Report, Evidence State Machine, Archive Roundtrip

**Files:**
- Create content: `src/dragon_radar/research/report.py`
- Test: `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append failing report tests.**

```python
from dragon_radar.research.report import (
    assemble_report,
    parse_archived_report,
    serialize_report,
)


def _varying_long_pack(n_days=830, *, synthetic):
    start = date(2024, 1, 1)
    calendar = calendar_days(start, n_days)
    as_of = calendar[-22]
    # Components (signals) stop at as_of; the 21 remaining calendar days and
    # their closes exist solely to build h=20 forward labels.
    history_end = calendar.index(as_of) + 1
    components = [
        daily(
            day,
            (20 + (i % 61), 100),
            (20 + ((i * 3) % 61), 100),
            (20 + ((i * 7) % 61), 100),
        )
        for i, day in enumerate(calendar[:history_end])
    ]
    target = {
        day: Decimal(10000 + (i % 7) * 100 + (i * 13) % 97)
        for i, day in enumerate(calendar)
    }
    benchmark = const_closes(calendar, "10000")
    return make_pack(
        calendar=calendar,
        components=components,
        target_closes=target,
        benchmark_closes=benchmark,
        synthetic=synthetic,
        generator="test-fixture:deterministic_pattern",
        as_of=as_of,
    )


def test_report_shape_versions_and_research_only():
    pack, _ = _monotone_forward_pack()
    report = assemble_report(pack, generated_at="2026-10-27T12:00:00+00:00")
    assert report["report_type"] == "research-method-validation"
    assert report["method_version"] == method.METHOD_VERSION
    assert report["validator_version"] == "research-validation-v1"
    assert report["research_only"] is True
    assert report["evidence_status"] == "synthetic_only"
    assert set(report) == {
        "report_type", "method_version", "validator_version", "research_only",
        "config", "pack", "input_manifests", "health", "evaluation",
        "evidence_status", "evidence_reasons", "report_id", "generated_at",
    }
    serialized = serialize_report(report)
    forbidden = (
        "effective", "validated", "profitable", "profit", "win_probability",
        "forecast", "prediction", "recommendation", "guaranteed",
    )
    blob = json.dumps(serialized, sort_keys=True, ensure_ascii=True).lower()
    assert not any(token in blob for token in forbidden)


def test_report_id_is_deterministic_and_clock_excluded():
    pack, _ = _monotone_forward_pack()
    first = assemble_report(pack, generated_at="2026-10-27T12:00:00+00:00")
    second = assemble_report(pack, generated_at="2026-10-28T08:30:00+00:00")
    third = assemble_report(pack, generated_at=None)
    assert first["report_id"] == second["report_id"] == third["report_id"]
    assert len(first["report_id"]) == 64


def test_archive_roundtrip_restores_types_and_recomputes_id():
    pack, _ = _monotone_forward_pack()
    report = assemble_report(pack, generated_at="2026-10-27T12:00:00+00:00")
    serialized = serialize_report(report)
    json_blob = json.dumps(serialized, sort_keys=True)  # archive form is JSON
    restored = parse_archived_report(json.loads(json_blob))
    assert restored["report_id"] == report["report_id"]
    observation = restored["evaluation"]["1"]["observations"][0]
    assert type(observation["trade_date"]) is date
    assert isinstance(observation["score"], Decimal)
    assert isinstance(observation["r_excess"], Decimal)
    assert restored["pack"]["as_of"] == pack.as_of


def test_tampered_archive_id_is_rejected():
    pack, _ = _monotone_forward_pack()
    archived = serialize_report(assemble_report(pack))
    archived["health"]["calendar_days"] = 999
    with pytest.raises(ResearchValidationError, match="report_id"):
        parse_archived_report(archived)


def test_research_only_false_and_unknown_status_cannot_enter_shape():
    pack, _ = _monotone_forward_pack()
    report = assemble_report(pack)
    report["research_only"] = False
    with pytest.raises(ResearchValidationError):
        serialize_report(report)
    report["research_only"] = True
    report["evidence_status"] = "effective"
    with pytest.raises(ResearchValidationError):
        serialize_report(report)


def test_evidence_statuses_are_reachable_for_real_packs():
    short = _varying_long_pack(n_days=30, synthetic=False)
    short_report = assemble_report(short)
    assert short_report["evidence_status"] == "insufficient_real_sample"
    codes_by_scope = {
        item["scope"]: item["code"] for item in short_report["evidence_reasons"]
    }
    scopes = set(codes_by_scope)
    # Spec denominator is valid_days / calendar_days: the 21-day label tail
    # makes 9/30 < 0.80, so G1 honestly fires at global scope; G2 (>=2 distinct
    # scores) does not. Every per-horizon gate family also enumerates.
    assert codes_by_scope["global"] == "coverage_below_threshold"
    assert {"h1", "h5", "h20"} <= scopes
    assert "score_zero_variance" not in codes_by_scope.values()

    long_report = assemble_report(_varying_long_pack(n_days=830, synthetic=False))
    assert long_report["evidence_status"] == "real_sample_descriptive"
    assert long_report["evidence_reasons"] == []

    synthetic_long = assemble_report(_varying_long_pack(n_days=830, synthetic=True))
    assert synthetic_long["evidence_status"] == "synthetic_only"
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: FAIL on importing `dragon_radar.research.report`.

- [ ] **Step 3: Implement `report.py` fully.**

```python
"""Versioned closed-shape research validation reports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from dragon_radar.readmodels import sentiment_research as method
from dragon_radar.research.factor_health import build_factor_health
from dragon_radar.research.forward_eval import evaluate_all
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
    EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
    EVIDENCE_STATUSES,
    EVIDENCE_SYNTHETIC_ONLY,
    GATE_COVERAGE_BELOW_THRESHOLD,
    GATE_CODES,
    GATE_SCORE_ZERO_VARIANCE,
    REPORT_SCOPES,
    REPORT_TYPE,
    ResearchValidationError,
    SamplePack,
    VALIDATOR_VERSION,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.samples import canonical_payload_hash, validate_pack

METRIC_SCALE = Decimal("0.000001")

_TOP_KEYS = frozenset(
    {
        "report_type", "method_version", "validator_version", "research_only",
        "config", "pack", "input_manifests", "health", "evaluation",
        "evidence_status", "evidence_reasons", "report_id", "generated_at",
    }
)
_DATE_KEYS = frozenset(
    {
        "as_of", "first", "last", "trade_date", "entry_date", "exit_date",
        "start_date", "end_date", "grid_origin_date",
    }
)
_DECIMAL_KEYS = frozenset(
    {
        "min_coverage", "coverage_ratio", "score", "r_target", "r_benchmark",
        "r_excess", "rank_ic", "ic_mean", "ic_std", "icir", "positive_share",
        "mean_excess", "mean_target", "spread", "adjacent_agreement",
        "direction_hit_rate", "value", "min", "max", "mean", "median", "p25",
        "p75", "top_tie_share",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchValidationError(message)


def _require_keys(obj: object, expected: frozenset[str], label: str) -> None:
    _require(isinstance(obj, Mapping), f"{label} must be an object")
    _require(set(obj) == expected, f"{label} has unexpected keys")  # type: ignore[arg-type]


def _config_block(config: ValidationConfig) -> dict[str, object]:
    return {
        "horizons": list(config.horizons),
        "min_coverage": config.min_coverage,
        "min_signals_per_fold": config.min_signals_per_fold,
        "min_folds": config.min_folds,
        "min_quintile_size": config.min_quintile_size,
    }


def _pack_block(validated) -> dict[str, object]:
    pack = validated.pack
    calendar = validated.calendar
    return {
        "pack_id": pack.pack_id,
        "synthetic": pack.synthetic,
        "generator": pack.generator,
        "seed": pack.seed,
        "as_of": pack.as_of,
        "target_name": pack.target_name,
        "benchmark_name": pack.benchmark_name,
        "component_rows": len(pack.components),
        "target_rows": len(validated.target_closes),
        "benchmark_rows": len(validated.benchmark_closes),
        "sample_window": {"first": calendar[0], "last": calendar[-1]},
    }


def _manifest_block(manifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "source_version": manifest.source_version,
        "frozen_at": manifest.frozen_at,
        "as_of": manifest.as_of,
        "row_count": manifest.row_count,
        "adjustment": manifest.adjustment,
        "sha256": manifest.sha256,
    }


def _quantize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _quantize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_quantize(item) for item in value]
    if isinstance(value, Decimal):
        return value.quantize(METRIC_SCALE, rounding=ROUND_HALF_UP)
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ResearchValidationError("report contains a non-serializable value")


def serialize_report(report: Mapping[str, object]) -> dict[str, object]:
    _validate_report_shape(report)
    return _jsonable(report)


def assemble_report(
    pack: SamplePack,
    config: ValidationConfig = DEFAULT_CONFIG,
    *,
    generated_at: str | None = None,
) -> dict[str, object]:
    _require(
        generated_at is None or (type(generated_at) is str and generated_at),
        "generated_at must be a non-empty string or None",
    )
    validated = validate_pack(pack)
    cfg = validate_config(config)
    health = build_factor_health(validated)
    evaluation = evaluate_all(validated, cfg)

    reasons: list[dict[str, str]] = []
    if health["coverage_ratio"] < cfg.min_coverage:
        reasons.append({"scope": "global", "code": GATE_COVERAGE_BELOW_THRESHOLD})
    distinct_scores = len({point["score"] for point in validated.points if point["score"] is not None})
    if distinct_scores < 2:
        reasons.append({"scope": "global", "code": GATE_SCORE_ZERO_VARIANCE})
    for horizon in cfg.horizons:
        for code in evaluation[str(horizon)]["gate_failures"]:
            reasons.append({"scope": f"h{horizon}", "code": code})

    if pack.synthetic:
        status = EVIDENCE_SYNTHETIC_ONLY
    elif reasons:
        status = EVIDENCE_INSUFFICIENT_REAL_SAMPLE
    else:
        status = EVIDENCE_REAL_SAMPLE_DESCRIPTIVE

    report: dict[str, object] = {
        "report_type": REPORT_TYPE,
        "method_version": method.METHOD_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "research_only": True,
        "config": _config_block(cfg),
        "pack": _pack_block(validated),
        "input_manifests": [
            _manifest_block(manifest)
            for manifest in sorted(pack.manifests, key=lambda item: item.name)
        ],
        "health": health,
        "evaluation": evaluation,
        "evidence_status": status,
        "evidence_reasons": reasons,
        "report_id": None,
        "generated_at": generated_at,
    }
    report = _quantize(report)
    identity = {
        key: value for key, value in report.items()
        if key not in ("report_id", "generated_at")
    }
    report["report_id"] = canonical_payload_hash(_jsonable(identity))
    _validate_report_shape(report)
    return report


def _restore(value: Any, key: str | None) -> Any:
    if isinstance(value, Mapping):
        return {item_key: _restore(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_restore(item, key) for item in value]
    if key in _DATE_KEYS and isinstance(value, str):
        return date.fromisoformat(value)
    if key in _DECIMAL_KEYS and isinstance(value, str):
        return Decimal(value)
    return value


def parse_archived_report(archived: Mapping[str, object]) -> dict[str, object]:
    _require(isinstance(archived, Mapping), "archived report must be an object")
    restored = _restore(archived, None)
    restored = _quantize(restored)
    _validate_report_shape(restored)
    identity = {
        key: value
        for key, value in restored.items()
        if key not in ("report_id", "generated_at")
    }
    expected = canonical_payload_hash(_jsonable(identity))
    _require(restored["report_id"] == expected, "report_id mismatch")
    return restored


# ---------------------------------------------------------------------------
# Closed-shape validation
# ---------------------------------------------------------------------------

_DISTRIBUTION_KEYS = frozenset(
    {"n", "min", "max", "mean", "median", "p25", "p75"}
)
_SCORE_DISTRIBUTION_KEYS = _DISTRIBUTION_KEYS | {
    "zero_count", "hundred_count", "top_tie_share"
}
_NULL_REASON_KEYS = frozenset(
    {
        method.REASON_NO_VALID_DAILY_RETURNS,
        method.REASON_NO_LIMIT_EVENTS,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    }
)


def _is_date_or_none(value: object) -> bool:
    return value is None or type(value) is date


def _is_decimal_or_none(value: object) -> bool:
    return value is None or isinstance(value, Decimal)


def _validate_report_shape(report: Mapping[str, object]) -> None:
    _require_keys(report, _TOP_KEYS, "report")
    _require(report["report_type"] == REPORT_TYPE, "report_type mismatch")
    _require(
        report["method_version"] == method.METHOD_VERSION,
        "method_version mismatch",
    )
    _require(
        report["validator_version"] == VALIDATOR_VERSION,
        "validator_version mismatch",
    )
    _require(report["research_only"] is True, "research_only must be true")
    _require(
        report["evidence_status"] in EVIDENCE_STATUSES,
        "unknown evidence_status",
    )
    _require(isinstance(report["report_id"], str), "report_id must be a string")
    _require(
        report["generated_at"] is None or type(report["generated_at"]) is str,
        "generated_at must be string or None",
    )
    _validate_config_block(report["config"])
    _validate_pack_block(report["pack"])
    _validate_manifests_block(report["input_manifests"])
    _validate_health(report["health"])
    _validate_evaluation(report["evaluation"], report["config"])
    _validate_reasons(report["evidence_reasons"])


def _validate_config_block(config: object) -> None:
    _require_keys(
        config,
        frozenset(
            {
                "horizons", "min_coverage", "min_signals_per_fold", "min_folds",
                "min_quintile_size",
            }
        ),
        "config",
    )
    horizons = config["horizons"]  # type: ignore[index]
    _require(isinstance(horizons, list) and horizons, "config horizons")
    _require(all(type(h) is int and h in {1, 5, 20} for h in horizons), "bad horizon")
    _require(isinstance(config["min_coverage"], Decimal), "min_coverage Decimal")  # type: ignore[index]
    for key in ("min_signals_per_fold", "min_folds", "min_quintile_size"):
        _require(type(config[key]) is int and config[key] > 0, f"{key} int")  # type: ignore[index]


def _validate_pack_block(pack: object) -> None:
    _require_keys(
        pack,
        frozenset(
            {
                "pack_id", "synthetic", "generator", "seed", "as_of",
                "target_name", "benchmark_name", "component_rows", "target_rows",
                "benchmark_rows", "sample_window",
            }
        ),
        "pack",
    )
    _require(type(pack["synthetic"]) is bool, "pack synthetic bool")  # type: ignore[index]
    _require(type(pack["as_of"]) is date, "pack as_of date")  # type: ignore[index]
    _require_keys(pack["sample_window"], frozenset({"first", "last"}), "sample_window")  # type: ignore[index]
    _require(_is_date_or_none(pack["sample_window"]["first"]), "sample_window first")  # type: ignore[index]
    _require(type(pack["sample_window"]["last"]) is date, "sample_window last")  # type: ignore[index]


def _validate_manifests_block(manifests: object) -> None:
    _require(isinstance(manifests, list) and len(manifests) == 3, "three manifests")
    names = []
    for manifest in manifests:
        _require_keys(
            manifest,
            frozenset(
                {
                    "name", "source_version", "frozen_at", "as_of", "row_count",
                    "adjustment", "sha256",
                }
            ),
            "manifest",
        )
        names.append(manifest["name"])  # type: ignore[index]
        _require(type(manifest["as_of"]) is date, "manifest as_of")  # type: ignore[index]
    _require(sorted(names) == names and len(set(names)) == 3, "manifest names")


def _validate_health(health: object) -> None:
    _require_keys(
        health,
        frozenset(
            {
                "calendar_days", "present_days", "valid_days", "absent_days",
                "null_days", "coverage_ratio", "null_reasons", "calendar_holes",
                "score_distribution", "components", "rank_correlations",
                "sample_window",
            }
        ),
        "health",
    )
    _require(isinstance(health["coverage_ratio"], Decimal), "coverage Decimal")  # type: ignore[index]
    _require_keys(health["null_reasons"], _NULL_REASON_KEYS, "null_reasons")  # type: ignore[index]
    _require_keys(
        health["score_distribution"], _SCORE_DISTRIBUTION_KEYS, "score_distribution"  # type: ignore[index]
    )
    _require_keys(
        health["components"], frozenset(method.COMPONENT_KEYS), "components"  # type: ignore[index]
    )
    for block in health["components"].values():  # type: ignore[union-attr]
        _require_keys(
            block,
            frozenset({"available_days", "zero_denominator_days", "distribution"}),
            "component block",
        )
        _require_keys(
            block["distribution"], _DISTRIBUTION_KEYS, "component distribution"
        )  # type: ignore[index]
    expected_correlations = {
        "breadth_limit_balance", "breadth_promotion", "limit_balance_promotion",
        "breadth_score", "limit_balance_score", "promotion_score",
    }
    _require_keys(health["rank_correlations"], expected_correlations, "rank_correlations")  # type: ignore[index]
    for pair in health["rank_correlations"].values():  # type: ignore[union-attr]
        _require_keys(pair, frozenset({"value", "reason"}), "correlation")
        _require(_is_decimal_or_none(pair["value"]), "correlation value")
    _require_keys(health["sample_window"], frozenset({"first", "last"}), "health window")  # type: ignore[index]
    _require(_is_date_or_none(health["sample_window"]["first"]), "health window first")  # type: ignore[index]
    _require(_is_date_or_none(health["sample_window"]["last"]), "health window last")
    _require(isinstance(health["calendar_holes"], list), "calendar holes list")
    _require(all(type(day) is date for day in health["calendar_holes"]), "holes dates")


def _validate_evaluation(evaluation: object, config: object) -> None:
    _require(isinstance(evaluation, Mapping), "evaluation mapping")
    horizons = config["horizons"]  # type: ignore[index]
    _require(set(evaluation) == {str(h) for h in horizons}, "evaluation horizons")  # type: ignore[arg-type]
    for horizon in horizons:
        block = evaluation[str(horizon)]  # type: ignore[index]
        _require_keys(
            block,
            frozenset(
                {
                    "horizon", "evaluation_kind", "grid_origin_date", "as_of",
                    "grid", "observations", "folds", "fold_summary", "pooled",
                    "quintiles", "direction_hit_rate", "gate_failures",
                }
            ),
            "evaluation block",
        )
        _require(type(block["horizon"]) is int, "horizon int")
        _require(_is_date_or_none(block["grid_origin_date"]), "grid origin")
        _require(type(block["as_of"]) is date, "block as_of")
        _validate_grid(block["grid"])
        for observation in block["observations"]:
            _require_keys(
                observation,
                frozenset(
                    {
                        "trade_date", "entry_date", "exit_date", "score",
                        "r_target", "r_benchmark", "r_excess",
                    }
                ),
                "observation",
            )
            _require(type(observation["trade_date"]) is date, "obs date")
        for fold in block["folds"]:
            _require_keys(
                fold,
                frozenset(
                    {
                        "fold_index", "start_date", "end_date", "n", "rank_ic",
                        "reason",
                    }
                ),
                "fold",
            )
        _require_keys(
            block["fold_summary"],
            frozenset(
                {
                    "folds_total", "nonnull_ic", "ic_mean", "ic_std", "icir",
                    "icir_reason", "positive_share",
                }
            ),
            "fold summary",
        )
        _require_keys(block["pooled"], frozenset({"n", "rank_ic", "reason"}), "pooled")
        _require_keys(
            block["quintiles"],
            frozenset(
                {"buckets", "spread", "monotonic", "monotonic_reason",
                 "adjacent_agreement"}
            ),
            "quintiles",
        )
        for bucket in block["quintiles"]["buckets"]:
            _require_keys(
                bucket,
                frozenset({"index", "n", "mean_excess", "mean_target"}),
                "quintile bucket",
            )
        _require(
            isinstance(block["gate_failures"], list)
            and all(code in GATE_CODES for code in block["gate_failures"]),
            "gate codes",
        )
        _require(
            _is_decimal_or_none(block["direction_hit_rate"]),
            "hit rate",
        )


def _validate_grid(grid: object) -> None:
    _require_keys(
        grid,
        frozenset(
            {
                "step_trading_days", "slots_total", "slots_accepted",
                "slots_skipped", "drops", "signals_total", "off_grid_signals",
            }
        ),
        "grid",
    )
    _require_keys(grid["slots_skipped"], frozenset({"slot_without_signal"}), "skips")  # type: ignore[index]
    _require_keys(
        grid["drops"], frozenset({"missing_close", "out_of_window"}), "drops"  # type: ignore[index]
    )


def _validate_reasons(reasons: object) -> None:
    _require(isinstance(reasons, list), "evidence reasons list")
    for reason in reasons:
        _require(isinstance(reason, Mapping), "evidence reason")
        _require(
            set(reason) in ({"scope", "code"}, {"scope", "code", "detail"}),  # type: ignore[arg-type]
            "reason keys",
        )
        _require(reason["scope"] in REPORT_SCOPES, "reason scope")  # type: ignore[index]
        _require(reason["code"] in GATE_CODES, "reason code")  # type: ignore[index]
```

The code above is already in final form (no dead helpers, no unused imports):
assembly inlines the global gate logic, `validate_config` is imported from
`models` at the top and applied directly to the passed `config` argument, and
component distributions are checked against the plain `_DISTRIBUTION_KEYS`
frozenset. Still run `uv run ruff check` and let `ruff format` reflow long
lines before committing.

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py -q`

Expected: PASS.

Run: `uv run ruff check src/dragon_radar/research/report.py; uv run ruff format src/dragon_radar/research/report.py`

```powershell
git add -- src/dragon_radar/research/report.py tests/test_reset_research_validation.py
git commit -m "feat(research): add versioned hash-stamped reports with evidence state machine"
```

---

## Task 7: Public API Surface and Whole-Slice Guard Verification

**Files:**
- Modify: `src/dragon_radar/research/__init__.py`
- Test: append to `tests/test_reset_research_validation.py`

- [ ] **Step 1: Append the public-API smoke test (RED).**

```python
def test_public_api_surface():
    from dragon_radar.research import (  # noqa: F401
        DEFAULT_CONFIG,
        ComponentCounts,
        DailyComponents,
        EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
        EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
        EVIDENCE_SYNTHETIC_ONLY,
        InputManifest,
        ResearchValidationError,
        SamplePack,
        ValidationConfig,
        assemble_report,
        parse_archived_report,
        serialize_report,
        validate_config,
        validate_pack,
    )


def test_reset_scope_guard_tests_remain_collected():
    # The refined Task 29 guard functions must coexist with the new slice
    # tests. Read the sibling source and inspect it with AST: tests/ has no
    # __init__.py and the repo root is not on sys.path under the pytest
    # entry point, so a package import (``import tests.x``) is unreliable.
    guard_path = Path(__file__).resolve().parent / "test_reset_full_scope.py"
    tree = ast.parse(guard_path.read_text(encoding="utf-8"))
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "test_task27_backtest_and_simulation_artifacts_remain_absent" in function_names
    assert "test_task27_research_validation_is_a_closed_pure_subdomain" in function_names
```

- [ ] **Step 2: Run and verify RED.**

Run: `uv run pytest tests/test_reset_research_validation.py::test_public_api_surface -q`

Expected: FAIL (import error from package `__init__`).

- [ ] **Step 3: Replace `__init__.py` with the public surface.**

```python
"""Task 27 research-method validation subdomain (Slice A).

Pure functions only: no database, HTTP, filesystem, clock, or global state.
See docs/superpowers/specs/2026-10-27-task27-research-validation-design.md.
"""

from dragon_radar.research.factor_health import build_factor_health
from dragon_radar.research.forward_eval import evaluate_all, evaluate_horizon
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    ComponentCounts,
    DailyComponents,
    EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
    EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
    EVIDENCE_SYNTHETIC_ONLY,
    InputManifest,
    ResearchValidationError,
    SamplePack,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.report import (
    assemble_report,
    parse_archived_report,
    serialize_report,
)
from dragon_radar.research.samples import ValidatedPack, validate_pack

__all__ = [
    "DEFAULT_CONFIG",
    "ComponentCounts",
    "DailyComponents",
    "EVIDENCE_INSUFFICIENT_REAL_SAMPLE",
    "EVIDENCE_REAL_SAMPLE_DESCRIPTIVE",
    "EVIDENCE_SYNTHETIC_ONLY",
    "InputManifest",
    "ResearchValidationError",
    "SamplePack",
    "ValidatedPack",
    "ValidationConfig",
    "assemble_report",
    "build_factor_health",
    "evaluate_all",
    "evaluate_horizon",
    "parse_archived_report",
    "serialize_report",
    "validate_config",
    "validate_pack",
]
```

- [ ] **Step 4: Verify green, lint, commit.**

Run: `uv run pytest tests/test_reset_research_validation.py tests/test_reset_full_scope.py -q`

Expected: all PASS, including both refined boundary tests and the purity scan.

Run: `uv run ruff check src/dragon_radar/research; uv run ruff format --check src/dragon_radar/research tests/test_reset_research_validation.py`

```powershell
git add -- src/dragon_radar/research/__init__.py tests/test_reset_research_validation.py
git commit -m "feat(research): export validation public API and verify coexistence with Task29 guards"
```

---

## Task 8: Static Gates and Full Regression

**Files:** none (verification only).

- [ ] **Step 1: Compile and lint the whole tree.**

Run:

```powershell
uv run python -m compileall -q src tests
uv run ruff check .
uv run ruff format --check .
```

Expected: zero errors/warnings. Fix only slice files (`src/dragon_radar/research/*`,
the new test module, and the modified guard test); never reformat unrelated files.

- [ ] **Step 2: Run the focused slice suites once more.**

Run: `uv run pytest tests/test_reset_research_validation.py tests/test_reset_full_scope.py -q`

Expected: all PASS. Record exact counts.

- [ ] **Step 3: Verify no forbidden surface appeared.**

Run: `uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config(); c.set_main_option('script_location','dragon_radar:migrations'); print(ScriptDirectory.from_config(c).get_heads())"`

Expected: `['0013_query']`.

Run: `uv run python -c "from dragon_radar.http.public_app import create_public_app; routes=create_public_app().routes; print(any(getattr(r,'path','')=='/api/research/backtests' for r in routes))"`

Expected: `False`.

- [ ] **Step 4: Run the full suite once (background; ~55 min locally).**

Run in background:

```powershell
$env:PYTHONPYCACHEPREFIX = "$PWD\.temp\pycache"; uv run pytest -q -rs
```

Expected: record the real summary line (`NNNN passed, NN skipped`). Timing
gates are authoritative on CI Ubuntu under the documented local CPU frequency
lock; if the reset performance gate fails locally with P95 above 2.0 s while
all logic tests pass, record it as the known environmental issue, do not
modify thresholds, and do not claim production readiness.

- [ ] **Step 5: Final index hygiene and handoff summary.**

Run: `git --no-pager diff --name-only 4d08fcf..HEAD; git status --short`

Expected: the diff contains exactly the seven research modules, the two test
files (`tests/test_reset_research_validation.py`, modified
`tests/test_reset_full_scope.py`), the spec, and this plan — nothing under
`web/`, migrations, CI, README, or `docs/acceptance/`. Working tree only
shows allowed untracked cache (`.temp/`, `__pycache__` redirects); `.trae/`
never tracked.

Report: per-task commit hashes, exact pytest/ruff/compileall results, the
migration head, and confirmation that B01/B02/EX14/EX15 remain blocked with
no reset-final.md edits.

---

## Self-Review Notes (plan vs spec)

- Spec modules 1:1 with Tasks 1/2/3/4/5/6/7; report hash/state machine in
  Task 6; boundary refinement in Task 1 and Task 7.
- PIT contract, grid disjointness, balanced folds, three-value evidence,
  closed shape + archive recompute, forbidden-token scan, and import purity
  each have a named test.
- No task adds a dependency, migration, route, frontend file, or doc beyond
  spec+plan.
- Known deliberate nuance: fold ICIR is `null/zero_variance` for a perfectly
  stable fold-IC series (unit-RankIC fixture) — stability is reported via
  `ic_mean=1, ic_std=0, positive_share=1`; ICIR measures fold-to-fold
  dispersion, which is honestly zero-denominator there.

### Pre-implementation self-review pass (2026-10-27)

The full plan was re-read against the spec and the real method source
(`src/dragon_radar/readmodels/sentiment_research.py`); eleven defects where the
RED tests would have beaten their own implementations were found and fixed
inline before execution:

1. **Task 1 test imports** — removed unused `date`/`timedelta`/
   `ALLOWED_HORIZONS` (ruff F401); replaced inline `__import__("pathlib")`
   with a top-level `from pathlib import Path`.
2. **Task 1 awkward constructs** — `pytest.raises(FrozenInstanceError if
   False else AttributeError)` simplified to plain `AttributeError`; removed
   two redundant trailing assertions from the frozen-value-holders test.
3. **Task 1 step ordering** — the five docstring-only placeholder modules
   (samples/statistics/factor_health/forward_eval/report) are created before
   the closed-set test is run green.
4. **Task 2 `sample_std` fixture** — `[2,4,4,4,5,5,7,9]` (sample std ≈ 2.138,
   not 2) replaced with `[D("0"), D("2"), D("4")] == D("2")`.
5. **Slotted dataclass copies** — all four `Pack(**{**pack.__dict__, ...})`
   sites replaced with `dataclasses.replace(...)`; `replace`, `date`,
   `timedelta`, `hashlib`, `json` hoisted into the one top import block.
6. **Task 3 null-score fixture** — `(0,100),(0,10),(0,20)` yields a valid
   zero ratio (positive denominator), not an unavailable score; corrected to
   three `(0, 0)` zero-denominator components.
7. **Task 4 missing import** — `factor_health.py` gains
   `from datetime import date` (runtime annotations / holes use it).
8. **Task 4 `_ratio`** — plain numerator/denominator Decimal division written
   directly as final code; the defensive `cast(...)` variant and duplicated
   prose removed; the three-reason fail-closed `KeyError` step retained.
9. **Task 5 label lookup bug** — `_entry_exit_return` indexed a date-keyed
   dict with integer positions; signature is now
   `(closes, calendar, entry_index, exit_index)` and both call sites pass
   `calendar`.
10. **Task 5 test batch** — dropped a duplicate F811 import block (merge
    instruction only); `_monotone_forward_pack` now truncates components at
    `as_of` (PIT), uses dense `100.0` closes, and strides
    `range(0, n-h-1, horizon)` so on-grid returns strictly ascend
    `0.0005*(i+1)`; the hand-computed test uses strictly ascending scores
    (40/41/42) and closes `[100,100,101,98.98,100.9596]` giving pooled IC 0.5
    with corrected entry/exit assertions and `trade_date == calendar[0]`;
    the fold-gate comment is fixed to n_days=45 → 43 observations, K=5, and
    the tiny-pack assertion is `pooled.rank_ic == Decimal("1")` because gate
    failure never suppresses computable statistics (G6 fires only when the
    pooled IC itself is null); the constant-score degradation test is
    unchanged.
11. **Task 6 fixes** — `_varying_long_pack` truncates components at `as_of`
    (`history_end = calendar.index(as_of) + 1`); the n=30 real pack now
    correctly asserts global `coverage_below_threshold` (9/30 < 0.80 via the
    calendar-day denominator) with h1/h5/h20 present and no
    `score_zero_variance`; `report.py` drops the unused `datetime` and
    `REASON_POOLED_IC_UNAVAILABLE` imports, imports `validate_config` at
    module top, removes dead `_global_gate_reasons`/`_valid_points`/
    `NotImplementedError`, and calls `cfg = validate_config(config)` inline;
    distribution key checks use plain multi-line `_require_keys`; the Task 7
    coexistence test reads the sibling guard file via `ast.walk` instead of
    `import tests.test_reset_full_scope` (the `tests/` tree has no
    `__init__.py`, root `conftest.py`, or `pythonpath` setting — none may be
    added in this slice).

Two global hard rules were added to the header as a result: single test
module grows by appending only, with every import merged into the one isort
top group (no duplicated names); and slotted-dataclass copies always use
`dataclasses.replace`, never `**obj.__dict__`.

Final mechanical scan after the edits: no TBD/TODO/placeholder remains (only
the five intentional docstring-only modules created in Task 1); cross-task
names (`validate_config`, `validate_pack`/`ValidatedPack`,
`canonical_payload_hash`, `build_factor_health`, `evaluate_all`/
`evaluate_horizon`, `assemble_report`/`serialize_report`/
`parse_archived_report`) and the two guard test names are consistent at every
definition, call, and assertion site.

### Execution log — Task 1 and Task 2 review outcomes

Task 1 (commits `ac6cdbd`, guard hardening `f7f76b8`): two-stage review added
three test-only hardenings beyond the plan text — bool/NaN/Infinity/coverage=1
boundary assertions for `validate_config`; a `level >= 2` relative-import
escape guard for the AST purity scan (level 1 within-package stays legal);
and an `rglob` nested-module closed-set guard. All were proven to discriminate
by semantic mutation.

Task 2 (commits `f3170ee`, context fix `eb94b66`, discriminating probe
`d3a4633`): the tied Spearman hand case resolves to
`0.8660254037844386467637231707529359` under the fixed 34-digit context
(2.8347e-34 below the 80-digit `sqrt(3)/2`), so the plan's two-literal `or`
assertion was replaced by that single verbatim pin per plan note 3. Review
found `decimal_quantile` position/fraction arithmetic and `_snap_unit`
running outside the fixed context (caller-precision dependent); both are now
wrapped in `localcontext(_decimal_context())` — the outer `abs(...)` in
`_snap_unit` is load-bearing and must stay. The context-independence test
uses quantile probes `q = 0.25/0.34/0.75` expecting `(1.75, 2.02, 3.25)`;
`q = 0.25` alone is exact at `prec=2` and does not discriminate. Additional
appended tests pin bad-`q`/quintile-out-of-range rejection, n=2 extremes and
RankCorrelation value/reason shapes, the closed distribution key set, and
generator/str input rejection. A typo in this plan's Task 2 test line (one
extra closing parenthesis in the `for bad in ...` loop) was corrected
in-place.

### Execution log — Task 3 review outcomes

Task 3 (implementation `2670093`, review fixes `89acb8b`, missing regression
test `e2486a1`): the plan's production import
`from dragon_radar.readmodels import sentiment_research as method` is a
controlled deviation in `samples.py` — the AST module name of that
from-import is `dragon_radar.readmodels`, which the Task 1 purity guard's
exact-prefix allowlist (`dragon_radar.readmodels.sentiment_research`)
rejects, so the production module uses
`import dragon_radar.readmodels.sentiment_research as method` (the Task 3
code block at line 1004 is updated in place). The guard is not weakened;
test files keep from-imports because the AST scan only covers
`src/dragon_radar/research/`.

Quality review of `2670093` required six production fixes, all landed in
`89acb8b`: `frozen_at` parsing is wrapped to raise `ResearchValidationError`
with `from None` (the built-in `ValueError` must never leak); `_require_text`
rejects whitespace-only strings via `.strip()`; unknown manifest names echo
the offending value; manifests must appear in `STREAM_NAMES` order
(out-of-order input is rejected, never silently reordered); the
`materialize_sentiment_research` self-check result is explicitly discarded
with a comment; `_rebuild_point` documents that rebuilt points carry no
`coverage` field (T4+ must not assert it). Re-review of `89acb8b` found the
`.strip()` guard lacked a discriminating test (mutation left all 22 tests
green); `e2486a1` appended
`test_validate_pack_rejects_whitespace_only_text_fields` — one test with an
internal 6 reachable text fields × 2 whitespace values (`"   "`, `"\t"`)
matrix. A flat double `@pytest.mark.parametrize` would add 12 collected
items and break the planned count gate; the matrix adds exactly one while
keeping identical coverage. All three new guards (frozen_at wrap, manifest
ordering, strip) were proven to discriminate by semantic mutation. Downstream
memo from review: pack-level metadata fields are intentionally not bound by
the manifest payload hashes; the T6 report block cross-checks them. Suite at
T3 close: research 23 passed; full-scope 13 passed / 2 skipped; ruff clean.
