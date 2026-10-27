"""Task 27 Slice A tests: research-method validation (synthetic only)."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from dragon_radar.research import statistics as stats
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    REASON_INSUFFICIENT_SAMPLE,
    REASON_ZERO_VARIANCE,
    ComponentCounts,
    ResearchValidationError,
    ValidationConfig,
    validate_config,
)

D = Decimal

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "dragon_radar" / "research"
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
                assert (
                    name == "dragon_radar"
                    or name.startswith("dragon_radar.research")
                    or name.startswith(ALLOWED_DRAGON_IMPORTS)
                ), (path.name, name)
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


def test_validate_config_is_fail_closed_for_bool_nan_infinity_and_boundary():
    # bool must never sneak through the exact-type int/Decimal checks.
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(True,)))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_signals_per_fold=True))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=True))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("NaN")))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("Infinity")))
    # Upper boundary is inclusive: coverage of exactly one is accepted.
    accepted = validate_config(ValidationConfig(min_coverage=Decimal("1")))
    assert accepted.min_coverage == Decimal("1")


def test_research_package_rejects_relative_imports_beyond_package_root():
    # Level 1 stays inside dragon_radar.research; level >= 2 escapes the
    # package boundary (e.g. ``from ..storage import x``) and is forbidden.
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level >= 2
        ]
        assert offenders == [], (path.name, [node.level for node in offenders])


def test_research_package_has_no_nested_modules():
    assert {
        path.relative_to(PACKAGE_ROOT).as_posix() for path in PACKAGE_ROOT.rglob("*.py")
    } == {
        "__init__.py",
        "models.py",
        "samples.py",
        "statistics.py",
        "factor_health.py",
        "forward_eval.py",
        "report.py",
    }


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
    assert stats.spearman(
        [D("1"), D("2"), D("3")], [D("4"), D("5"), D("6")]
    ).value == D("1")
    assert stats.spearman(
        [D("1"), D("2"), D("3")], [D("6"), D("5"), D("4")]
    ).value == D("-1")
    # ranks x = 1,2,3 ; ranks y = 2,1,3 -> covariance form yields 0.5 exactly.
    result = stats.spearman([D("1"), D("2"), D("3")], [D("2"), D("1"), D("3")])
    assert result.value == D("0.5")
    # 34-digit sqrt(3)/2 pinned verbatim: 34-context rank Pearson lands
    # 2.9e-34 below the 80-digit reference 0.8660...2936183... (plan note 3).
    tied = stats.spearman([D("1"), D("1"), D("2")], [D("1"), D("2"), D("3")])
    assert tied.value == D("0.8660254037844386467637231707529359")


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
