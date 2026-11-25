"""Approved bounded analysis engineering; synthetic inputs, no supplier claims."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import DAYS, batch


def test_analysis_limits_capacity_boundary_and_no_truncation():
    from dragon_radar.analysis_jobs.limits import (
        AnalysisCapacityExceeded,
        validate_dimensions,
    )

    validate_dimensions(sessions=250, securities=5000, observations=1250000)
    for dimensions in (
        {"sessions": 251, "securities": 1, "observations": 251},
        {"sessions": 1, "securities": 5001, "observations": 5001},
        {"sessions": 250, "securities": 5000, "observations": 1250001},
    ):
        with pytest.raises(AnalysisCapacityExceeded) as error:
            validate_dimensions(**dimensions)
        assert error.value.code == "analysis_capacity_exceeded"


@pytest.mark.parametrize("bad", [True, -1, 1.5])
def test_analysis_dimensions_reject_noninteger_or_negative(bad):
    from dragon_radar.analysis_jobs.limits import validate_dimensions

    with pytest.raises(ValueError):
        validate_dimensions(sessions=bad, securities=1, observations=1)


def test_analysis_result_bytes_use_serialized_decimal_and_exact_limit():
    from dragon_radar.analysis_jobs.limits import (
        AnalysisCapacityExceeded,
        validate_result_size,
    )

    result = {"value": Decimal("1.000000000000000000000000000000001")}
    import json

    size = len(
        json.dumps(
            result,
            default=str,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    )
    validate_result_size(result, max_bytes=size)
    with pytest.raises(AnalysisCapacityExceeded):
        validate_result_size(result, max_bytes=size - 1)
    with pytest.raises(ValueError):
        validate_result_size({"bad": float("nan")})


def test_analysis_budget_boundary_uses_monotonic_elapsed_time():
    from dragon_radar.analysis_jobs.limits import (
        AnalysisBudget,
        AnalysisBudgetExceeded,
    )

    tick = [12.0]
    budget = AnalysisBudget(300000, clock=lambda: tick[0])
    tick[0] = 311.999
    budget.checkpoint()
    tick[0] = 312.0
    with pytest.raises(AnalysisBudgetExceeded) as error:
        budget.checkpoint()
    assert error.value.code == "analysis_budget_exceeded"


def test_analysis_window_checkpoints_inside_actual_security_session_loops():
    from dragon_radar.analysis_jobs.limits import AnalysisBudgetExceeded
    from dragon_radar.analysis_jobs.models import AnalysisJobCommand
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 6:
            raise AnalysisBudgetExceeded()

    snapshot = SimpleNamespace(id=uuid4(), batch=batch())
    command = AnalysisJobCommand(
        snapshot_id=snapshot.id,
        start=DAYS[0],
        end=DAYS[-1],
        symbols=("A",),
        adjustment="raw",
    )
    with pytest.raises(AnalysisBudgetExceeded):
        materialize_custom_analysis(snapshot, command, checkpoint=checkpoint)
    assert len(calls) == 6


def test_analysis_worker_budget_failure_never_calls_success():
    from dragon_radar.analysis_jobs.limits import AnalysisBudgetExceeded
    from dragon_radar.analysis_jobs.models import AnalysisJobCommand
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    snapshot = SimpleNamespace(id=uuid4(), batch=batch())
    command = AnalysisJobCommand(
        snapshot_id=snapshot.id,
        start=DAYS[0],
        end=DAYS[-1],
        symbols=("A",),
    )
    lease = SimpleNamespace(
        job=SimpleNamespace(command=command, max_wall_clock_ms=300000),
    )
    failures = []
    successes = []
    repository = SimpleNamespace(
        claim=lambda *args, **kwargs: lease,
        load_input=lambda job: snapshot,
        fail=lambda job, failure: failures.append(failure) or failure,
        succeed=lambda job, result: successes.append(result),
    )

    def compute(snapshot, command):
        raise AnalysisBudgetExceeded()

    outcome = AnalysisJobWorker(
        repository,
        worker_id="limit-test",
        compute=compute,
        lease_for=timedelta(seconds=30),
    ).run_once()
    assert outcome == {"code": "analysis_budget_exceeded"}
    assert failures == [outcome]
    assert successes == []


def test_analysis_command_identity_and_safe_http_input_exclude_budget():
    from factories import NOW

    from dragon_radar.analysis_jobs.models import AnalysisJobCommand, AnalysisJobRecord
    from dragon_radar.analysis_jobs.routes import _safe_record

    command = AnalysisJobCommand(
        snapshot_id=uuid4(),
        start=DAYS[0],
        end=DAYS[-1],
        symbols=("A",),
    )
    before = command.cache_key
    record = AnalysisJobRecord(
        id=uuid4(),
        owner_id=uuid4(),
        command=command,
        adjustment_version="a1",
        status="queued",
        attempt_count=0,
        created_at=NOW,
        max_wall_clock_ms=300000,
    )
    assert record.max_wall_clock_ms == 300000
    assert command.cache_key == before
    assert "max_wall_clock_ms" not in command.model_dump()
    assert set(_safe_record(record)["input"]) == {
        "snapshot_id",
        "start",
        "end",
        "benchmark",
        "adj",
        "symbols",
        "algorithm_version",
        "adjustment_version",
    }
