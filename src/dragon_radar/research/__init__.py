"""Task 27 research-only backtesting and simulation (EX14/EX15).

This package is owner-only and never mounted on the public or read-only
workbench applications. Its computational core is deliberately split into
pure, side-effect-free modules:

- :mod:`dragon_radar.research.execution` — ``simulate_fill`` trading rules
- :mod:`dragon_radar.research.ledger` — Decimal settlement and metrics
- :mod:`dragon_radar.research.pit` — point-in-time visible-information sets
- :mod:`dragon_radar.research.engine` — ``run_backtest`` and strategy registry
- :mod:`dragon_radar.research.health` — factor health checks
- :mod:`dragon_radar.research.walk_forward` — rolling folds and conditional
  regime weighting
- :mod:`dragon_radar.research.ablation` — baseline/AI uplift comparison frame

The kernel modules perform no network, database or clock access; all
monetary arithmetic uses :class:`decimal.Decimal`. Only ``repository`` and
``worker`` may touch IO, and they merely assemble the pure functions.

Task 1 introduces the package boundary; behaviour arrives in Tasks 2-13.
"""

__all__: tuple[str, ...] = ()
