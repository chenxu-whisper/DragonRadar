"""Backtest engine and pluggable strategy registry (Task 6).

``run_backtest`` wires PIT inputs, T-day signals, T+1 fills, the ledger and
cost-sensitivity legs into the frozen ``dragon-radar.research-backtest/v1``
result schema. Pure and deterministic (no clock, network or randomness beyond
the configured fixed seed).
"""
