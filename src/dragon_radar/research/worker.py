"""Research backtest asynchronous worker loop (Task 13).

Reuses the existing analysis-worker process (no new container): claim PIT
inputs, run the pure engine, renew the heartbeat lease, and succeed or fail
with structured reasons. Cooperative cancellation checkpoints and wall-clock
/ computation budgets close the run without half-published results.
"""
