"""Real PostgreSQL admission and immutable budget checks in disposable schemas."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from test_analysis_jobs import _command
from test_analysis_jobs import job_setup as job_setup


def _submit(jobs, session, command, key):
    return jobs.submit(session.token, session.csrf, command, key=key)


def test_analysis_admission_twenty_pending_replay_and_cache_hit(job_setup):
    from dragon_radar.analysis_jobs.limits import AnalysisCapacityExceeded
    from dragon_radar.analysis_jobs.models import analysis_jobs
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, owners, session, publication = job_setup
    command = _command(publication.id)
    records = [
        _submit(jobs, session, command, f"admission-{index}") for index in range(20)
    ]
    assert all(record.status == "queued" for record in records)
    assert all(record.max_wall_clock_ms == 300000 for record in records)
    assert _submit(jobs, session, command, "admission-0").id == records[0].id
    with pytest.raises(AnalysisCapacityExceeded):
        _submit(jobs, session, command, "admission-overflow")
    with jobs.database.engine.connect() as connection:
        assert len(connection.execute(select(analysis_jobs.c.id)).all()) == 20
    completed = AnalysisJobWorker(jobs, worker_id="capacity-test").run_once()
    assert completed.status == "succeeded"
    cached = _submit(jobs, session, command, "admission-cache")
    assert cached.id != completed.id
    assert cached.status == "succeeded"


def test_analysis_admission_budget_cannot_be_changed_after_submission(job_setup):
    from dragon_radar.analysis_jobs.models import analysis_jobs

    jobs, owners, session, publication = job_setup
    record = _submit(jobs, session, _command(publication.id), "immutable-budget")
    with pytest.raises(DBAPIError):
        with jobs.database.engine.begin() as connection:
            connection.execute(
                update(analysis_jobs)
                .where(analysis_jobs.c.id == record.id)
                .values(max_wall_clock_ms=1)
            )
    assert jobs.get(session.token, record.id).max_wall_clock_ms == 300000


def test_analysis_worker_oversized_result_publishes_no_cache(job_setup):
    from dragon_radar.analysis_jobs.limits import MAX_RESULT_BYTES
    from dragon_radar.analysis_jobs.models import analysis_job_cache
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, owners, session, publication = job_setup
    _submit(jobs, session, _command(publication.id), "oversized-result")
    completed = AnalysisJobWorker(
        jobs,
        worker_id="oversized-test",
        compute=lambda snapshot, command: {"large": "x" * MAX_RESULT_BYTES},
    ).run_once()
    assert completed.status == "failed"
    assert completed.failure == {"code": "analysis_capacity_exceeded"}
    assert completed.result is None
    with jobs.database.engine.connect() as connection:
        assert connection.execute(select(analysis_job_cache.c.id)).first() is None


def test_analysis_worker_fixed_clock_budget_publishes_no_cache(job_setup):
    from dragon_radar.analysis_jobs.models import analysis_job_cache
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, owners, session, publication = job_setup
    _submit(jobs, session, _command(publication.id), "budget-exceeded")
    ticks = iter((10.0, 10.0, 311.0))
    completed = AnalysisJobWorker(
        jobs,
        worker_id="budget-test",
        monotonic=lambda: next(ticks),
        lease_for=timedelta(seconds=30),
        compute=lambda snapshot, command: {"small": "value"},
    ).run_once()
    assert completed.status == "failed"
    assert completed.failure == {"code": "analysis_budget_exceeded"}
    with jobs.database.engine.connect() as connection:
        assert connection.execute(select(analysis_job_cache.c.id)).first() is None


def test_analysis_admission_database_is_dedicated_test_database(job_setup):
    jobs, owners, session, publication = job_setup
    with jobs.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "dragon_test"
        assert connection.scalar(text("SELECT current_schema()")).startswith("test_")
