"""Conservative custom-analysis limits, independent of leases and command identity."""

import json
import math
import time
from collections.abc import Callable, Mapping

MAX_SESSIONS = 250
MAX_SECURITIES = 5000
MAX_OBSERVATIONS = 1250000
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_NONTERMINAL_JOBS = 20
MAX_WALL_CLOCK_MS = 300000


class AnalysisCapacityExceeded(ValueError):
    code = "analysis_capacity_exceeded"

    def __init__(self, dimension: str = "result_bytes"):
        self.dimension = dimension
        super().__init__(self.code)


class AnalysisBudgetExceeded(ValueError):
    code = "analysis_budget_exceeded"

    def __init__(self):
        super().__init__(self.code)


def validate_dimensions(*, sessions: int, securities: int, observations: int) -> None:
    for name, value, limit in (
        ("sessions", sessions, MAX_SESSIONS),
        ("securities", securities, MAX_SECURITIES),
        ("observations", observations, MAX_OBSERVATIONS),
    ):
        if type(value) is not int or value < 0:
            raise ValueError("Analysis dimensions must be nonnegative integers")
        if value > limit:
            raise AnalysisCapacityExceeded(name)


def validate_result_size(
    result: Mapping,
    *,
    max_bytes: int = MAX_RESULT_BYTES,
) -> None:
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("Result budget must be a positive integer")
    encoded = json.dumps(
        result,
        default=str,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    if len(encoded) > max_bytes:
        raise AnalysisCapacityExceeded("result_bytes")


class AnalysisBudget:
    """Cooperative monotonic deadline; does not preempt blocked I/O or threads."""

    def __init__(
        self,
        max_wall_clock_ms: int = MAX_WALL_CLOCK_MS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if (
            type(max_wall_clock_ms) is not int
            or not 1 <= max_wall_clock_ms <= MAX_WALL_CLOCK_MS
        ):
            raise ValueError("Invalid analysis wall-clock budget")
        self.max_wall_clock_ms = max_wall_clock_ms
        self.clock = clock
        self.started_at = clock()
        if not math.isfinite(self.started_at):
            raise ValueError("Invalid monotonic clock")

    def checkpoint(self) -> None:
        now = self.clock()
        if not math.isfinite(now) or now < self.started_at:
            raise ValueError("Invalid monotonic clock")
        if (now - self.started_at) * 1000 >= self.max_wall_clock_ms:
            raise AnalysisBudgetExceeded()
