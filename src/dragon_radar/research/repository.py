"""Research backtest run repository (Task 11).

IO layer modelled on ``analysis_jobs``: owner isolation, idempotency replay
and conflict, config-hash result reuse, advisory locks, lease claim/renew,
cooperative cancel and budget enforcement. This module is the only research
component besides ``worker`` permitted database access.
"""
