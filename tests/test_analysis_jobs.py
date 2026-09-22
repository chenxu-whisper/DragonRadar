"""Versioned owner-only analysis jobs on real PostgreSQL."""

import hashlib
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch
from pydantic import ValidationError
from sqlalchemy import insert, inspect, text, update

ALGORITHM_VERSION = "analysis-custom-v1-decimal34"


def _publish(database, repository):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    market = repository.publish(
        repository.import_batch(batch(), key="analysis-job-market").id
    )
    return PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="analysis-job-publication",
    )


def _session(owners):
    code = owners.issue_pairing()
    return owners.exchange_pairing(code, "https://localhost:8030")


def _command(snapshot_id, **changes):
    from dragon_radar.analysis_jobs.models import AnalysisJobCommand

    values = {
        "snapshot_id": snapshot_id,
        "start": DAYS[0],
        "end": DAYS[-1],
        "benchmark": "B000300",
        "adjustment": "raw",
        "symbols": ("A",),
        **changes,
    }
    return AnalysisJobCommand(**values)


@pytest.fixture
def job_setup(database, repository):
    from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = _session(owners)
    publication = _publish(database, repository)
    jobs = AnalysisJobRepository(
        database,
        owners,
        PublicationRepository(database, repository, clock=lambda: NOW),
        clock=lambda: NOW,
    )
    return jobs, owners, session, publication


def test_analysis_job_command_is_strict_canonical_and_versioned():
    command = _command(uuid4())

    assert command.algorithm_version == ALGORITHM_VERSION
    assert command.canonical_json() == command.canonical_json()
    assert (
        command.cache_key
        == hashlib.sha256(command.canonical_json().encode("ascii")).hexdigest()
    )

    with pytest.raises(ValidationError):
        _command(command.snapshot_id, start=DAYS[-1], end=DAYS[0])
    with pytest.raises(ValidationError):
        _command(
            command.snapshot_id,
            symbols=("A", "B", "C", "D", "E", "F"),
        )
    with pytest.raises(ValidationError):
        _command(command.snapshot_id, symbols=("A", "A"))
    with pytest.raises(ValidationError):
        _command(command.snapshot_id, algorithm_version="future")


def test_0007c_migration_creates_analysis_schema_and_readiness(database):
    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0007c_analysis_jobs"
    assert database.ready()
    inspector = inspect(database.engine)
    assert {"analysis_jobs", "analysis_job_cache"} <= set(inspector.get_table_names())
    assert {
        "owner_id",
        "idempotency_key",
        "request_hash",
        "cache_key",
        "input_manifest",
        "status",
        "lease_token",
        "lease_expires_at",
        "result",
        "failure",
    } <= {column["name"] for column in inspector.get_columns("analysis_jobs")}
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == SCHEMA_REVISION
        )


def test_submit_reauthorizes_then_delegates_to_locked_owner_entry(
    job_setup, monkeypatch
):
    jobs, owners, session, publication = job_setup
    calls = []
    original_authorize = owners.authorize_connection
    original_internal = jobs.submit_for_owner

    def authorize(connection, token, csrf=None):
        identity = original_authorize(connection, token, csrf)
        calls.append(("authorize", connection, identity.owner_id))
        return identity

    def internal(connection, owner_id, command, *, key):
        calls.append(("internal", connection, owner_id))
        return original_internal(connection, owner_id, command, key=key)

    monkeypatch.setattr(owners, "authorize_connection", authorize)
    monkeypatch.setattr(jobs, "submit_for_owner", internal)

    record = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="submit-1",
    )

    assert record.owner_id == session.owner_id
    assert [call[0] for call in calls] == ["authorize", "internal"]
    assert calls[0][1] is calls[1][1]
    docstring = type(jobs).submit_for_owner.__doc__
    assert docstring is not None
    assert "principal" in docstring
    assert "same transaction" in docstring


@pytest.mark.parametrize("csrf", (None, "", "wrong"))
def test_submit_requires_session_bound_csrf(job_setup, csrf):
    from dragon_radar.owner import CsrfForbidden

    jobs, _owners, session, publication = job_setup

    with pytest.raises(CsrfForbidden):
        jobs.submit(
            session.token,
            csrf,
            _command(publication.id),
            key="csrf",
        )
    with jobs.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 0


def test_submit_is_owner_scoped_idempotent_and_rechecks_published_input(job_setup):
    from dragon_radar.publication.tables import publications
    from dragon_radar.storage.repository import Conflict, Forbidden

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id)
    first = jobs.submit(session.token, session.csrf, command, key="same")
    replay = jobs.submit(session.token, session.csrf, command, key="same")

    assert replay == first
    with pytest.raises(Conflict):
        jobs.submit(
            session.token,
            session.csrf,
            command.model_copy(update={"benchmark": "OTHER"}),
            key="same",
        )

    with jobs.database.engine.begin() as connection:
        connection.execute(
            update(publications)
            .where(publications.c.id == publication.id)
            .values(status="revoked")
        )
    with pytest.raises(Forbidden):
        jobs.submit(session.token, session.csrf, command, key="same")


def test_jobs_cache_and_reads_never_cross_owner(job_setup):
    from dragon_radar.owner.models import owner_principals, owner_sessions
    from dragon_radar.owner.repository import _hash
    from dragon_radar.storage.repository import NotFound

    jobs, _owners, first_session, publication = job_setup
    command = _command(publication.id)
    first = jobs.submit(first_session.token, first_session.csrf, command, key="first")
    lease = jobs.claim("worker-1", lease_for=timedelta(seconds=30))
    assert lease.job.id == first.id
    jobs.succeed(lease, {"value": "1.25"})
    cached = jobs.submit(
        first_session.token,
        first_session.csrf,
        command,
        key="cached",
    )
    assert cached.status == "succeeded"
    assert cached.result == {"value": "1.25"}

    second_owner = uuid4()
    second_token, second_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with jobs.database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(
                id=second_owner,
                local_key=None,
                created_at=NOW,
            )
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=second_owner,
                token_hash=_hash(second_token),
                csrf_hash=_hash(second_csrf),
                origin="https://localhost:8030",
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )

    with pytest.raises(NotFound):
        jobs.get(second_token, first.id)
    second = jobs.submit(second_token, second_csrf, command, key="first")
    assert second.owner_id == second_owner
    assert second.id != first.id
    assert second.status == "queued"


def test_get_reauthorizes_session_and_input(job_setup):
    from dragon_radar.owner import SessionUnauthorized
    from dragon_radar.storage.repository import Forbidden

    jobs, owners, session, publication = job_setup
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="read",
    )
    assert jobs.get(session.token, submitted.id) == submitted

    owners.logout(session.token)
    with pytest.raises(SessionUnauthorized):
        jobs.get(session.token, submitted.id)

    replacement = _session(owners)
    jobs.publications.market.revoke_source("synthetic")
    with pytest.raises(Forbidden):
        jobs.get(replacement.token, submitted.id)


def test_expired_lease_can_be_reclaimed_but_stale_worker_cannot_finish(job_setup):
    from dragon_radar.storage.repository import Conflict

    jobs, _owners, session, publication = job_setup
    jobs.submit(session.token, session.csrf, _command(publication.id), key="lease")
    first = jobs.claim("worker-1", lease_for=timedelta(seconds=10))
    assert first is not None
    jobs.clock = lambda: NOW + timedelta(seconds=11)
    second = jobs.claim("worker-2", lease_for=timedelta(seconds=10))
    assert second is not None
    assert second.job.id == first.job.id
    assert second.token != first.token
    assert second.job.attempt_count == 2
    with pytest.raises(Conflict):
        jobs.succeed(first, {"stale": True})
    completed = jobs.succeed(second, {"ok": True})
    assert completed.status == "succeeded"


def test_worker_computes_custom_window_and_reuses_versioned_cache(job_setup):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, publication = job_setup
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="compute",
    )

    completed = AnalysisJobWorker(jobs, worker_id="worker").run_once()

    assert completed.id == submitted.id
    assert completed.status == "succeeded"
    assert completed.result["schema"] == "dragon-radar.custom-analysis"
    assert completed.result["algorithm_version"] == ALGORITHM_VERSION
    assert completed.result["snapshot_id"] == str(publication.id)
    assert completed.result["adjusted_start"] == DAYS[0].isoformat()
    assert completed.result["adjusted_end"] == DAYS[-1].isoformat()
    assert [item["security_id"] for item in completed.result["curves"]] == ["A"]


def test_worker_rechecks_source_authorization_after_submit(job_setup):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, publication = job_setup
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="revoked-before-run",
    )
    jobs.publications.market.revoke_source("synthetic")

    failed = AnalysisJobWorker(jobs, worker_id="worker").run_once()

    assert failed.id == submitted.id
    assert failed.status == "failed"
    assert failed.failure == {"code": "input_forbidden"}
    assert failed.result is None
