"""Task 27 PIT backtest suite (EX14 engine slices).

Task 1 scaffold: the research package modules exist and stay importable so
later slices can extend them without changing the package boundary. The PIT
assembly, ``run_backtest`` engine, factor health check and walk-forward
behaviour tests arrive in Tasks 5-8.
"""

from __future__ import annotations

import importlib

import pytest

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
