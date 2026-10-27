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
