"""Lease-based asynchronous execution of research backtests (Task 13).

The worker shares the existing analysis-worker process (no new container or
dependency): claim a queued run, load its authorized point-in-time inputs,
drive the pure engine through cooperative checkpoints, renew the heartbeat
lease while the compute thread runs, and finish with an immutable
``succeed``/``fail`` transition. Two independent budget gates close runaway
runs without half-published results:

- a static computation-volume pre-check (:func:`assert_compute_budget`) runs
  before any engine work and rejects configurations drifting past the frozen
  model constants;
- a wall-clock gate lives inside the checkpoint closure and uses an injected
  monotonic clock compared against the run's immutable ``max_wall_clock_ms``.

Cancellation is cooperative only: there is no cancel flag column and no thread
is killed. A cancelled or taken-over lease makes the next checkpoint raise
:class:`ResearchRunCancelled`; the worker then returns the authoritative row
without writing a result or failure. The default compute is deliberately
fail-closed because no production factor materializer exists yet — real engine
assembly is injected by the owning deployment seam instead of being hard-wired
into this IO layer.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from typing import Any

from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.research import models as research_models
from dragon_radar.research.models import BacktestConfig
from dragon_radar.research.repository import (
    ResearchRunLease,
    ResearchRunRecord,
    ResearchRunRepository,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

# Checkpoint ordering is folds -> rebalance -> top_n so a single drifted
# constant surfaces the tightest breached dimension in the tests below.
Checkpoint = Callable[[], None]
ResearchCompute = Callable[
    [PublishedSnapshot, BacktestConfig, Checkpoint], Mapping[str, Any]
]


class ResearchRunCancelled(RuntimeError):
    """Raised at a checkpoint once the lease is cancelled, taken or expired."""


class ResearchInputsUnavailable(RuntimeError):
    """Raised when point-in-time inputs cannot be materialized for a run."""


class ComputeBudgetExceeded(RuntimeError):
    """Static configuration volume drifts past a frozen model constant."""

    def __init__(self, dimension: str, *, limit: int, actual: int) -> None:
        super().__init__(f"compute budget exceeded for {dimension}: {actual} > {limit}")
        self.dimension = dimension
        self.limit = limit
        self.actual = actual


class ResearchBudgetExceeded(RuntimeError):
    """Wall-clock budget breached at a cooperative checkpoint."""

    def __init__(self, budget_ms: int, elapsed_ms: int) -> None:
        super().__init__(
            f"research wall-clock budget exceeded: {elapsed_ms} > {budget_ms} ms"
        )
        self.budget_ms = budget_ms
        self.elapsed_ms = elapsed_ms


def assert_compute_budget(config: BacktestConfig) -> None:
    """Reject configurations whose volume exceeds the frozen model constants.

    Pure and side-effect free: the worker calls it before touching the engine
    so an oversized run fails fast with a structured reason instead of burning
    the wall-clock budget.
    """
    # Read the frozen caps through the module object (not import-time
    # bindings) so a deliberate monkeypatch of research_models is observed at
    # call time while production keeps importing the single source of truth.
    if config.walk_forward is not None:
        folds = config.walk_forward.folds
        if folds > research_models.MAX_WALK_FORWARD_FOLDS:
            raise ComputeBudgetExceeded(
                "walk_forward_folds",
                limit=research_models.MAX_WALK_FORWARD_FOLDS,
                actual=folds,
            )
    rebalance_period = config.parameters.rebalance_period
    if rebalance_period > research_models.MAX_REBALANCE_PERIOD_SESSIONS:
        raise ComputeBudgetExceeded(
            "rebalance_period",
            limit=research_models.MAX_REBALANCE_PERIOD_SESSIONS,
            actual=rebalance_period,
        )
    top_n = config.parameters.top_n
    if top_n > research_models.MAX_TOP_N:
        raise ComputeBudgetExceeded(
            "top_n", limit=research_models.MAX_TOP_N, actual=top_n
        )


def fail_closed_research_compute(
    snapshot: object,
    config: object,
    checkpoint: Checkpoint,
) -> Mapping[str, Any]:
    """Default compute seam: never fabricate factor inputs.

    The production factor materializer does not exist
    (``market_source_not_materializable``), so an unconfigured worker fails the
    run with ``input_unavailable`` rather than inventing observations.
    """
    del snapshot, config, checkpoint
    raise ResearchInputsUnavailable(
        "research factor inputs are not materializable in this build"
    )


class ResearchBacktestWorker:
    def __init__(
        self,
        repository: ResearchRunRepository,
        *,
        worker_id: str,
        lease_for: timedelta = timedelta(seconds=30),
        max_attempts: int = 3,
        compute: ResearchCompute = fail_closed_research_compute,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repository = repository
        self.worker_id = worker_id
        self.lease_for = lease_for
        self.max_attempts = max_attempts
        self.compute = compute
        self.clock = clock

    def _finish_or_rehydrate(
        self,
        lease: ResearchRunLease,
        finish: Callable[[ResearchRunLease], ResearchRunRecord],
    ) -> ResearchRunRecord:
        try:
            return finish(lease)
        except Conflict:
            # Another worker owns the lease now; never write through a dead
            # token and never leave a half-published result.
            return self.repository.fetch_run(lease.run.id)

    def run_once(self) -> ResearchRunRecord | None:
        lease = self.repository.claim(
            self.worker_id,
            lease_for=self.lease_for,
            max_attempts=self.max_attempts,
        )
        if lease is None:
            return None
        try:
            snapshot = self.repository.load_input(lease)
        except Forbidden:
            return self.repository.fail(lease, {"code": "input_forbidden"})
        except NotFound:
            return self.repository.fail(lease, {"code": "input_unavailable"})
        except Exception as error:
            return self.repository.fail(
                lease,
                {"code": "backtest_failed", "error_type": type(error).__name__},
            )

        try:
            assert_compute_budget(lease.run.config)
        except ComputeBudgetExceeded as error:
            return self.repository.fail(
                lease,
                {
                    "code": "budget_exceeded",
                    "reason": "compute_budget",
                    "dimension": error.dimension,
                    "limit": error.limit,
                    "actual": error.actual,
                },
            )

        holder: dict[str, Any] = {"lease": lease, "cancelled": False, "breach": None}
        started_at = self.clock()

        def checkpoint() -> None:
            elapsed_ms = int(round((self.clock() - started_at) * 1000))
            active_lease: ResearchRunLease = holder["lease"]
            budget_ms = active_lease.run.max_wall_clock_ms
            if elapsed_ms > budget_ms:
                breach = ResearchBudgetExceeded(budget_ms, elapsed_ms)
                holder["breach"] = breach
                raise breach
            try:
                self.repository.check_lease(active_lease)
            except Conflict:
                holder["cancelled"] = True
                raise ResearchRunCancelled(
                    "Research run lease is no longer active"
                ) from None

        heartbeat_seconds = self.lease_for.total_seconds() / 3
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self.compute, snapshot, lease.run.config, checkpoint
            )
            while not future.done():
                try:
                    future.result(timeout=heartbeat_seconds)
                except FutureTimeoutError:
                    if not future.done():
                        try:
                            holder["lease"] = self.repository.renew(
                                holder["lease"],
                                lease_for=self.lease_for,
                            )
                        except Conflict:
                            # The compute thread observes the dead lease at its
                            # next checkpoint and stops cooperatively.
                            break
                except Exception:
                    break
            try:
                result = future.result()
            except ResearchRunCancelled:
                return self.repository.fetch_run(lease.run.id)
            except ResearchBudgetExceeded as error:
                # Except-bound names are deleted when the block exits; freeze
                # the structured payload before the finish hook closes over it.
                payload = {
                    "code": "budget_exceeded",
                    "budget_ms": error.budget_ms,
                    "elapsed_ms": error.elapsed_ms,
                }
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(active, payload),
                )
            except ComputeBudgetExceeded as error:
                payload = {
                    "code": "budget_exceeded",
                    "reason": "compute_budget",
                    "dimension": error.dimension,
                    "limit": error.limit,
                    "actual": error.actual,
                }
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(active, payload),
                )
            except ResearchInputsUnavailable:
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(
                        active, {"code": "input_unavailable"}
                    ),
                )
            except Forbidden:
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(
                        active, {"code": "input_forbidden"}
                    ),
                )
            except NotFound:
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(
                        active, {"code": "input_unavailable"}
                    ),
                )
            except Exception as error:
                payload = {
                    "code": "backtest_failed",
                    "error_type": type(error).__name__,
                }
                return self._finish_or_rehydrate(
                    holder["lease"],
                    lambda active: self.repository.fail(active, payload),
                )

        # A checkpoint may have recorded a budget breach even if the compute
        # function swallowed the exception and returned a result.
        breach: ResearchBudgetExceeded | None = holder["breach"]
        if breach is not None:
            return self._finish_or_rehydrate(
                holder["lease"],
                lambda active: self.repository.fail(
                    active,
                    {
                        "code": "budget_exceeded",
                        "budget_ms": breach.budget_ms,
                        "elapsed_ms": breach.elapsed_ms,
                    },
                ),
            )
        if holder["cancelled"]:
            return self.repository.fetch_run(lease.run.id)
        return self._finish_or_rehydrate(
            holder["lease"],
            lambda active: self.repository.succeed(active, result),
        )

    def run_forever(
        self,
        *,
        poll_seconds: float = 1.0,
        sleep: Callable[[float], object] = time.sleep,
        stop: Callable[[], bool] = lambda: False,
        max_iterations: int | None = None,
    ) -> None:
        iterations = 0
        try:
            while not stop():
                if max_iterations is not None and iterations >= max_iterations:
                    return
                completed = self.run_once()
                iterations += 1
                if completed is None:
                    sleep(poll_seconds)
        except KeyboardInterrupt:
            return


__all__ = [
    "ComputeBudgetExceeded",
    "ResearchBacktestWorker",
    "ResearchBudgetExceeded",
    "ResearchInputsUnavailable",
    "ResearchRunCancelled",
    "assert_compute_budget",
    "fail_closed_research_compute",
]
