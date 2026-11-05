"""Task 27 trading-rule, ledger and run-lifecycle validation suite.

Task 1 scaffold: pins the research package boundary. The ``simulate_fill``
pure-function tests and Decimal ledger invariants arrive in Tasks 3-4; the
run repository and worker lifecycle tests arrive in Tasks 11/13.
"""

from __future__ import annotations

import importlib

import pytest

VALIDATION_MODULES = (
    "execution",
    "ledger",
    "repository",
    "worker",
)


@pytest.mark.parametrize("module_name", VALIDATION_MODULES)
def test_research_validation_module_importable(module_name: str) -> None:
    module = importlib.import_module(f"dragon_radar.research.{module_name}")
    assert module is not None
