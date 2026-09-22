"""Versioned owner-only analysis jobs on real PostgreSQL."""

import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import DAYS, NOW, bar, batch, state
from pydantic import ValidationError
from sqlalchemy import insert, inspect, text, update

ALGORITHM_VERSION = "analysis-custom-v1-decimal34"


def _publish(database, repository, market_batch=None, *, suffix=""):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    market = repository.publish(
        repository.import_batch(
            market_batch or batch(),
            key=f"analysis-job-market{suffix}",
        ).id
    )
    return PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key=f"analysis-job-publication{suffix}",
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


def test_analysis_job_command_allows_empty_symbols_for_market_ranking():
    from dragon_radar.analysis_jobs.models import AnalysisJobCommand

    command = _command(uuid4(), symbols=())

    assert command.symbols == ()
    assert AnalysisJobCommand.model_validate(command.model_dump()) == command


def test_analysis_jobs_package_exports_http_and_repository_surface():
    from dragon_radar.analysis_jobs import (
        AnalysisJobRepository,
        AnalysisJobWorker,
        install_analysis_job_routes,
        materialize_custom_analysis,
    )

    assert AnalysisJobRepository
    assert AnalysisJobWorker
    assert install_analysis_job_routes
    assert materialize_custom_analysis


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


def test_cache_identity_binds_authorized_snapshot_adjustment_version(job_setup):
    jobs, _owners, session, first_publication = job_setup
    second_publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            adjustment_version="a2",
            bars=tuple(bar(trade_date=day, adjustment_version="a2") for day in DAYS),
        ),
        suffix="-a2",
    )
    first_command = _command(first_publication.id)
    second_command = _command(second_publication.id)

    first = jobs.submit(
        session.token,
        session.csrf,
        first_command,
        key="adjustment-a1",
    )
    second = jobs.submit(
        session.token,
        session.csrf,
        second_command,
        key="adjustment-a2",
    )

    with jobs.database.engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT id, request_hash, cache_key, input_manifest "
                    "FROM analysis_jobs ORDER BY input_manifest->>'adjustment_version'"
                )
            )
            .mappings()
            .all()
        )
    assert [row["input_manifest"]["adjustment_version"] for row in rows] == [
        "a1",
        "a2",
    ]
    assert rows[0]["input_manifest"]["command"] == first_command.model_dump(mode="json")
    assert rows[1]["input_manifest"]["command"] == second_command.model_dump(
        mode="json"
    )
    assert rows[0]["request_hash"] == first_command.cache_key
    assert rows[1]["request_hash"] == second_command.cache_key
    for row in rows:
        canonical = json.dumps(
            row["input_manifest"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        assert row["cache_key"] == hashlib.sha256(canonical.encode("ascii")).hexdigest()
    assert rows[0]["cache_key"] != rows[1]["cache_key"]
    assert first.command == first_command
    assert second.command == second_command


def test_global_cache_reuses_result_across_owners_but_jobs_remain_isolated(job_setup):
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
    assert second.status == "succeeded"
    assert second.result == {"value": "1.25"}
    with pytest.raises(NotFound):
        jobs.get(first_session.token, second.id)
    with jobs.database.engine.connect() as connection:
        cache_columns = {
            column["name"]
            for column in inspect(connection).get_columns("analysis_job_cache")
        }
        assert "owner_id" not in cache_columns
        assert connection.scalar(text("SELECT count(*) FROM analysis_job_cache")) == 1


def test_get_cache_reauthorizes_publication_and_source(job_setup):
    from dragon_radar.storage.repository import Forbidden

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id)
    jobs.submit(session.token, session.csrf, command, key="cache-reader")
    lease = jobs.claim("cache-worker")
    jobs.succeed(lease, {"cached": True})

    assert jobs.get_cache(command) == {"cached": True}

    jobs.publications.market.revoke_source("synthetic")
    with pytest.raises(Forbidden):
        jobs.get_cache(command)


def test_succeed_preserves_high_precision_decimal_in_job_and_cache(job_setup):
    jobs, _owners, session, publication = job_setup
    command = _command(publication.id)
    submitted = jobs.submit(
        session.token,
        session.csrf,
        command,
        key="decimal-cache",
    )
    lease = jobs.claim("decimal-worker")
    precise = Decimal("9007199254740993.01")

    completed = jobs.succeed(lease, {"value": precise})

    assert completed.id == submitted.id
    assert jobs.get(session.token, submitted.id).result["value"] == (
        "9007199254740993.01"
    )
    assert jobs.get_cache(command)["value"] == "9007199254740993.01"


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


def test_claim_enforces_attempt_limit_and_fails_expired_exhausted_job(job_setup):
    from dragon_radar.storage.repository import Conflict

    jobs, _owners, session, publication = job_setup
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="attempt-limit",
    )

    for invalid in (True, 0, 11, "3"):
        with pytest.raises(ValueError, match="max attempts"):
            jobs.claim("worker", max_attempts=invalid)

    first = jobs.claim("worker-1", lease_for=timedelta(seconds=10))
    jobs.clock = lambda: NOW + timedelta(seconds=11)
    second = jobs.claim("worker-2", lease_for=timedelta(seconds=10))
    jobs.clock = lambda: NOW + timedelta(seconds=22)
    third = jobs.claim("worker-3", lease_for=timedelta(seconds=10))
    jobs.clock = lambda: NOW + timedelta(seconds=33)

    assert first.job.id == submitted.id
    assert second.job.attempt_count == 2
    assert third.job.attempt_count == 3
    assert jobs.claim("worker-4") is None
    exhausted = jobs.get(session.token, submitted.id)
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 3
    assert exhausted.failure == {"code": "attempts_exhausted"}
    assert exhausted.completed_at == NOW + timedelta(seconds=33)
    with pytest.raises(Conflict):
        jobs.succeed(third, {"stale": True})


def test_claim_skips_locked_exhausted_job_and_claims_other_work(job_setup):
    jobs, _owners, session, publication = job_setup
    exhausted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="locked-exhausted",
    )
    jobs.claim("worker-1", lease_for=timedelta(seconds=10))
    jobs.clock = lambda: NOW + timedelta(seconds=11)
    jobs.claim("worker-2", lease_for=timedelta(seconds=10))
    jobs.clock = lambda: NOW + timedelta(seconds=22)
    jobs.claim("worker-3", lease_for=timedelta(seconds=10))
    available = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id, symbols=("A",), benchmark="OTHER"),
        key="available",
    )
    jobs.clock = lambda: NOW + timedelta(seconds=33)

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with jobs.database.engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM analysis_jobs WHERE id = :id FOR UPDATE"),
                {"id": exhausted.id},
            )
            claimed = pool.submit(
                jobs.claim,
                "other-worker",
                max_attempts=3,
            ).result(timeout=2)
    finally:
        pool.shutdown(wait=True)

    assert claimed.job.id == available.id
    assert claimed.job.attempt_count == 1


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


def test_worker_limits_rankings_exclusions_and_curves_to_requested_symbols(job_setup):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, _publication = job_setup
    security_ids = ("A", "B", "C", "D", "INDEX")
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=security_ids,
            security_states=tuple(
                state(
                    security_id,
                    security_type=(
                        "index" if security_id == "INDEX" else "common_stock"
                    ),
                )
                for security_id in security_ids
            ),
            bars=tuple(
                bar(
                    security_id,
                    trade_date=day,
                    close=Decimal("10"),
                )
                for security_id in security_ids
                for day in DAYS
                if not (security_id == "C" and day == DAYS[0])
            ),
        ),
        suffix="-symbols",
    )
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(
            publication.id,
            benchmark="INDEX",
            symbols=("C", "B"),
        ),
        key="symbol-filter",
    )

    completed = AnalysisJobWorker(jobs, worker_id="symbol-worker").run_once()

    assert completed.id == submitted.id
    assert [row["security_id"] for row in completed.result["ranking"]] == ["B"]
    assert [row["security_id"] for row in completed.result["exclusions"]] == ["C"]
    assert completed.result["exclusions"][0]["reasons"]
    assert [curve["security_id"] for curve in completed.result["curves"]] == ["B"]
    assert completed.result["benchmark"] == "INDEX"
    assert completed.result["eligible_security_ids"] == ("A", "B", "C", "D")


def test_worker_keeps_partial_curve_without_treating_it_as_compare_exclusion(
    job_setup,
):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, _publication = job_setup
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=("A",),
            security_states=(state("A"),),
            bars=tuple(bar("A", trade_date=day) for day in DAYS if day != DAYS[1]),
        ),
        suffix="-partial-curve",
    )
    jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id, symbols=("A",)),
        key="partial-curve",
    )

    completed = AnalysisJobWorker(jobs, worker_id="partial-worker").run_once()

    assert [curve["security_id"] for curve in completed.result["curves"]] == ["A"]
    assert not completed.result["exclusions"]


def test_worker_empty_symbols_returns_full_market_ranking_without_curves(job_setup):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, _publication = job_setup
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=("A", "B", "C"),
            security_states=tuple(
                state(security_id) for security_id in ("A", "B", "C")
            ),
            bars=tuple(
                bar(
                    security_id,
                    trade_date=day,
                    close=Decimal("10"),
                )
                for security_id in ("A", "B", "C")
                for day in DAYS
                if not (security_id == "C" and day == DAYS[0])
            ),
        ),
        suffix="-market-ranking",
    )
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id, symbols=()),
        key="market-ranking",
    )

    completed = AnalysisJobWorker(jobs, worker_id="ranking-worker").run_once()

    assert completed.id == submitted.id
    assert [row["security_id"] for row in completed.result["ranking"]] == ["A", "B"]
    assert [row["security_id"] for row in completed.result["exclusions"]] == ["C"]
    assert not completed.result["curves"]


@pytest.mark.anyio
async def test_cached_rank_uses_eligible_stock_universe_when_pool_contains_index(
    job_setup,
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.http.app import create_workbench_app

    jobs, _owners, session, _publication = job_setup
    security_ids = ("A", "B", "C", "INDEX")
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=security_ids,
            security_states=tuple(
                state(
                    security_id,
                    security_type=(
                        "index" if security_id == "INDEX" else "common_stock"
                    ),
                )
                for security_id in security_ids
            ),
            bars=tuple(
                bar(security_id, trade_date=day)
                for security_id in security_ids
                for day in DAYS
                if not (security_id == "C" and day == DAYS[0])
            ),
        ),
        suffix="-eligible-rank",
    )
    jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id, benchmark="INDEX", symbols=()),
        key="eligible-rank",
    )
    completed = AnalysisJobWorker(jobs, worker_id="eligible-rank-worker").run_once()
    assert completed.status == "succeeded"
    app = create_workbench_app(
        repository=jobs.publications,
        analysis_repository=jobs,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "snapshot_id": str(publication.id),
                "start": DAYS[0].isoformat(),
                "end": DAYS[-1].isoformat(),
                "benchmark": "INDEX",
                "adj": "raw",
            },
        )

    assert response.status_code == 200
    assert {row["security_id"] for row in response.json()["data"]["ranking"]} == {
        "A",
        "B",
    }
    exclusions = response.json()["data"]["exclusions"]
    assert [row["security_id"] for row in exclusions] == ["C"]
    assert "missing_price" in exclusions[0]["reasons"]
    assert response.json()["coverage"] == {
        "expected": 3,
        "valid": 2,
        "excluded": 0,
        "missing": 1,
    }


@pytest.mark.anyio
async def test_cached_rank_counts_suspension_as_legal_exclusion(job_setup):
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id, symbols=())
    jobs.submit(
        session.token,
        session.csrf,
        command,
        key="suspended-rank",
    )
    snapshot = jobs.publications.get_snapshot(publication.id)
    result = materialize_custom_analysis(snapshot, command)
    result["ranking"] = []
    result["exclusions"] = [{"security_id": "A", "reasons": ["suspension"]}]
    response = await _request_cached_result(
        jobs,
        result,
        "/api/rank/range",
        {
            "snapshot_id": str(publication.id),
            "start": DAYS[0].isoformat(),
            "end": DAYS[-1].isoformat(),
            "adj": "raw",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["exclusions"] == [
        {"security_id": "A", "reasons": ["suspension"]}
    ]
    assert response.json()["coverage"] == {
        "expected": 1,
        "valid": 0,
        "excluded": 1,
        "missing": 0,
    }


def test_worker_rejects_index_in_expected_security_ids_as_analysis_symbol(job_setup):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker

    jobs, _owners, session, _publication = job_setup
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=("A", "OTHER_INDEX"),
            security_states=(
                state("A"),
                state("BENCHMARK", security_type="index"),
                state("OTHER_INDEX", security_type="index"),
            ),
            bars=tuple(
                bar(security_id, trade_date=day)
                for security_id in ("A", "BENCHMARK", "OTHER_INDEX")
                for day in DAYS
            ),
        ),
        suffix="-index-symbol",
    )
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(
            publication.id,
            benchmark="BENCHMARK",
            symbols=("OTHER_INDEX",),
        ),
        key="index-symbol",
    )

    failed = AnalysisJobWorker(jobs, worker_id="index-symbol-worker").run_once()

    assert failed.id == submitted.id
    assert failed.status == "failed"
    assert failed.failure == {"code": "input_unavailable"}
    assert failed.result is None


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


class _WorkerRepository:
    def __init__(self, *, succeed_error=None, fail_error=None):
        self.lease = SimpleNamespace(job=SimpleNamespace(command=object()))
        self.succeed_error = succeed_error
        self.fail_error = fail_error
        self.fail_calls = 0

    def claim(self, worker_id, *, lease_for):
        return self.lease

    def load_input(self, lease):
        return object()

    def succeed(self, lease, result):
        raise self.succeed_error

    def fail(self, lease, failure):
        self.fail_calls += 1
        if self.fail_error is not None:
            raise self.fail_error
        return failure


@pytest.mark.parametrize(
    "error_kind",
    [
        pytest.param("database", id="database-error"),
        pytest.param("lease", id="lease-conflict"),
    ],
)
def test_worker_propagates_succeed_error_without_failing_job(error_kind):
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.storage.repository import Conflict

    error = (
        RuntimeError("database unavailable")
        if error_kind == "database"
        else Conflict("Analysis job lease is no longer active")
    )
    repository = _WorkerRepository(succeed_error=error)

    with pytest.raises(type(error)) as caught:
        AnalysisJobWorker(
            repository,
            worker_id="worker",
            compute=lambda snapshot, command: {"value": "computed"},
        ).run_once()

    assert caught.value is error
    assert repository.fail_calls == 0


def test_worker_propagates_fail_conflict_without_retrying_fail():
    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.storage.repository import Conflict

    conflict = Conflict("Analysis job lease is no longer active")
    repository = _WorkerRepository(fail_error=conflict)

    def fail_compute(snapshot, command):
        raise ValueError("invalid analysis")

    with pytest.raises(Conflict) as caught:
        AnalysisJobWorker(
            repository,
            worker_id="worker",
            compute=fail_compute,
        ).run_once()

    assert caught.value is conflict
    assert repository.fail_calls == 1


def _owner_headers(session):
    return {
        "Origin": "https://localhost:8030",
        "X-CSRF-Token": session.csrf,
        "Cookie": (
            f"__Host-dragon-private-session={session.token}; "
            f"__Host-dragon-private-csrf={session.csrf}"
        ),
    }


@pytest.mark.anyio
async def test_owner_analysis_http_submit_get_idempotency_and_isolation(job_setup):
    import secrets

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import insert, text

    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.owner.models import owner_principals, owner_sessions
    from dragon_radar.owner.repository import _hash

    jobs, owners, session, publication = job_setup
    app = create_owner_app(
        repository=jobs.publications.market,
        owner_repository=owners,
        publication_repository=jobs.publications,
        analysis_repository=jobs,
    )
    body = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "idempotency_key": "http-job",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://localhost:8030",
    ) as client:
        missing_csrf = await client.post(
            "/api/analysis/jobs",
            json=body,
            headers={
                "Origin": "https://localhost:8030",
                "Cookie": (
                    f"__Host-dragon-private-session={session.token}; "
                    f"__Host-dragon-private-csrf={session.csrf}"
                ),
            },
        )
        assert missing_csrf.status_code == 403

        created = await client.post(
            "/api/analysis/jobs",
            json=body,
            headers=_owner_headers(session),
        )
        assert created.status_code == 200
        assert set(created.json()) == {"job_id", "status"}
        assert created.json()["status"] == "queued"

        replay = await client.post(
            "/api/analysis/jobs",
            json=body,
            headers=_owner_headers(session),
        )
        assert replay.status_code == 200
        assert replay.json() == created.json()

        conflict = await client.post(
            "/api/analysis/jobs",
            json={**body, "benchmark": "OTHER"},
            headers=_owner_headers(session),
        )
        assert conflict.status_code == 409
        assert conflict.json() == {
            "code": "VERSION_CONFLICT",
            "message": "Version conflict",
        }

        fetched = await client.get(
            f"/api/analysis/jobs/{created.json()['job_id']}",
            headers=_owner_headers(session),
        )
        assert fetched.status_code == 200
        assert fetched.json() == {
            "job_id": created.json()["job_id"],
            "status": "queued",
            "result": None,
            "failure": None,
            "input": {
                "snapshot_id": str(publication.id),
                "start": DAYS[0].isoformat(),
                "end": DAYS[-1].isoformat(),
                "benchmark": "B000300",
                "adj": "raw",
                "symbols": [],
            },
        }
        assert all(
            secret not in fetched.text
            for secret in (str(session.owner_id), session.token, session.csrf, "lease")
        )

        completed = AnalysisJobWorker(jobs, worker_id="http-worker").run_once()
        assert completed.status == "succeeded"
        succeeded = await client.get(
            f"/api/analysis/jobs/{created.json()['job_id']}",
            headers=_owner_headers(session),
        )
        assert succeeded.status_code == 200
        assert succeeded.json()["status"] == "succeeded"
        assert succeeded.json()["result"]["schema"] == "dragon-radar.custom-analysis"
        assert succeeded.json()["failure"] is None
        assert "lease" not in succeeded.text

        other_owner_id = uuid4()
        other_token = secrets.token_urlsafe(32)
        other_csrf = secrets.token_urlsafe(32)
        with jobs.database.engine.begin() as connection:
            connection.execute(
                insert(owner_principals).values(
                    id=other_owner_id,
                    local_key=None,
                    created_at=NOW,
                )
            )
            connection.execute(
                insert(owner_sessions).values(
                    id=uuid4(),
                    owner_id=other_owner_id,
                    token_hash=_hash(other_token),
                    csrf_hash=_hash(other_csrf),
                    origin="https://localhost:8030",
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=1),
                )
            )
        other = SimpleNamespace(token=other_token, csrf=other_csrf)
        isolated = await client.get(
            f"/api/analysis/jobs/{created.json()['job_id']}",
            headers=_owner_headers(other),
        )
        assert isolated.status_code == 404
        assert isolated.json() == {
            "code": "NOT_FOUND",
            "message": "Resource not found",
        }

    with jobs.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == 1


@pytest.mark.anyio
async def test_owner_analysis_http_result_emits_declared_decimals_as_json_numbers(
    job_setup,
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    jobs, owners, session, publication = job_setup
    submitted = jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id),
        key="decimal-http-job",
    )
    lease = jobs.claim("decimal-http-worker")
    precise = Decimal("9007199254740993.01")
    jobs.succeed(
        lease,
        {
            "snapshot_id": "9007199254740993.01",
            "requested_start": "9007199254740993.01",
            "benchmark_return_ratio": precise,
            "ranking": [
                {
                    "security_id": "9007199254740993.01",
                    "return_ratio": precise,
                }
            ],
            "curves": [
                {
                    "points": [
                        {
                            "trade_date": "9007199254740993.01",
                            "normalized": precise,
                            "drawdown": Decimal("0.01"),
                        }
                    ]
                }
            ],
        },
    )
    app = create_owner_app(
        repository=jobs.publications.market,
        owner_repository=owners,
        publication_repository=jobs.publications,
        analysis_repository=jobs,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://localhost:8030",
    ) as client:
        response = await client.get(
            f"/api/analysis/jobs/{submitted.id}",
            headers=_owner_headers(session),
        )

    assert response.status_code == 200
    assert b'"return_ratio":9007199254740993.01' in response.content
    assert b'"normalized":9007199254740993.01' in response.content
    assert b'"benchmark_return_ratio":9007199254740993.01' in response.content
    parsed = json.loads(response.content, parse_float=Decimal)
    assert parsed["result"]["ranking"][0]["return_ratio"] == precise
    assert parsed["result"]["curves"][0]["points"][0]["normalized"] == precise
    assert parsed["result"]["snapshot_id"] == "9007199254740993.01"
    assert parsed["result"]["requested_start"] == "9007199254740993.01"
    assert parsed["result"]["ranking"][0]["security_id"] == "9007199254740993.01"
    assert parsed["result"]["curves"][0]["points"][0]["trade_date"] == (
        "9007199254740993.01"
    )


@pytest.mark.anyio
async def test_owner_analysis_http_strict_body_and_revoked_source(
    job_setup, monkeypatch
):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.exc import SQLAlchemyError

    from dragon_radar.owner.app import create_owner_app

    jobs, owners, session, publication = job_setup
    app = create_owner_app(
        repository=jobs.publications.market,
        owner_repository=owners,
        publication_repository=jobs.publications,
        analysis_repository=jobs,
    )
    body = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "symbols": ["A"],
        "idempotency_key": "strict-http-job",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://localhost:8030",
    ) as client:
        for invalid in (
            {**body, "unknown": True},
            {**body, "snapshot_id": None},
            {**body, "symbols": ["A", "B", "C", "D", "E", "F"]},
            {**body, "symbols": ["A", "A"]},
            {**body, "start": DAYS[-1].isoformat(), "end": DAYS[0].isoformat()},
        ):
            response = await client.post(
                "/api/analysis/jobs",
                json=invalid,
                headers=_owner_headers(session),
            )
            assert response.status_code == 400
            assert response.json() == {
                "code": "INVALID_PARAM",
                "message": "Invalid request",
            }
            assert "A,A" not in response.text
        malformed = await client.post(
            "/api/analysis/jobs",
            content=b"{",
            headers={
                **_owner_headers(session),
                "Content-Type": "application/json",
            },
        )
        assert malformed.status_code == 400
        assert malformed.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }
        query = await client.post(
            "/api/analysis/jobs?unexpected=1",
            json=body,
            headers=_owner_headers(session),
        )
        assert query.status_code == 400
        assert query.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }
        invalid_job_id = await client.get(
            "/api/analysis/jobs/not-a-uuid",
            headers=_owner_headers(session),
        )
        assert invalid_job_id.status_code == 400
        assert invalid_job_id.json() == {
            "code": "INVALID_PARAM",
            "message": "Invalid request",
        }
        missing = await client.get(
            f"/api/analysis/jobs/{uuid4()}",
            headers=_owner_headers(session),
        )
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "NOT_FOUND",
            "message": "Resource not found",
        }

        created = await client.post(
            "/api/analysis/jobs",
            json=body,
            headers=_owner_headers(session),
        )

        def unavailable(token, job_id):
            raise SQLAlchemyError("secret-marker")

        original_get = jobs.get
        monkeypatch.setattr(jobs, "get", unavailable)
        unavailable_response = await client.get(
            f"/api/analysis/jobs/{created.json()['job_id']}",
            headers=_owner_headers(session),
        )
        assert unavailable_response.status_code == 503
        assert unavailable_response.json() == {
            "code": "STORAGE_UNAVAILABLE",
            "message": "Storage unavailable",
        }
        assert "secret-marker" not in unavailable_response.text
        monkeypatch.setattr(jobs, "get", original_get)

        jobs.publications.market.revoke_source("synthetic")
        denied = await client.get(
            f"/api/analysis/jobs/{created.json()['job_id']}",
            headers=_owner_headers(session),
        )
        assert denied.status_code == 403
        assert denied.json() == {
            "code": "FORBIDDEN",
            "message": "Source access denied",
        }


@pytest.mark.anyio
async def test_public_analysis_gets_use_cache_without_writes(job_setup):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.http.app import create_workbench_app

    jobs, _owners, session, publication = job_setup
    rank_command = _command(publication.id, symbols=())
    compare_command = _command(publication.id, symbols=("A",))
    jobs.submit(session.token, session.csrf, rank_command, key="public-rank-cache")
    jobs.submit(session.token, session.csrf, compare_command, key="public-curve-cache")
    worker = AnalysisJobWorker(jobs, worker_id="public-cache-worker")
    assert worker.run_once().status == "succeeded"
    assert worker.run_once().status == "succeeded"
    with jobs.database.engine.connect() as connection:
        before = connection.scalar(text("SELECT count(*) FROM analysis_jobs"))
        cache_before = connection.scalar(
            text("SELECT count(*) FROM analysis_job_cache")
        )

    app = create_workbench_app(
        repository=jobs.publications,
        analysis_repository=jobs,
    )
    assert app.state.analysis_repository is jobs
    common = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        rank = await client.get("/api/rank/range", params=common)
        curves = await client.get(
            "/api/compare/curves",
            params={**common, "symbols": "A"},
        )
        miss = await client.get(
            "/api/rank/range",
            params={**common, "benchmark": "OTHER"},
        )
        public_post = await client.post("/api/analysis/jobs", json={})

    assert (
        rank.status_code,
        curves.status_code,
        miss.status_code,
    ) == (200, 200, 200), (rank.json(), curves.json(), miss.json())
    assert rank.json()["data"]["ranking"]
    assert curves.json()["data"]["curves"][0]["security_id"] == "A"
    assert miss.json()["data"] is None
    assert miss.json()["reason"] == "analysis_not_precomputed"
    assert public_post.status_code == 403
    assert not any(
        getattr(route, "path", None) == "/api/analysis/jobs"
        and "POST" in (getattr(route, "methods", None) or ())
        for route in app.routes
    )
    with jobs.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM analysis_jobs")) == before
        assert (
            connection.scalar(text("SELECT count(*) FROM analysis_job_cache"))
            == cache_before
        )


@pytest.mark.anyio
async def test_cached_compare_counts_all_empty_curve_as_missing(job_setup):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.http.app import create_workbench_app

    jobs, _owners, session, _publication = job_setup
    publication = _publish(
        jobs.database,
        jobs.publications.market,
        batch(
            expected_security_ids=("A", "B"),
            security_states=(state("A"), state("B")),
            bars=tuple(
                bar(security_id, trade_date=day)
                for security_id in ("A", "B")
                for day in DAYS
                if not (security_id == "B" and day == DAYS[0])
            ),
        ),
        suffix="-compare-coverage",
    )
    jobs.submit(
        session.token,
        session.csrf,
        _command(publication.id, symbols=("B", "A")),
        key="compare-coverage",
    )
    assert (
        AnalysisJobWorker(jobs, worker_id="coverage-worker").run_once().status
        == "succeeded"
    )
    app = create_workbench_app(
        repository=jobs.publications,
        analysis_repository=jobs,
    )
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
        "symbols": "B,A",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/compare/curves", params=params)

    assert response.status_code == 200
    assert [row["security_id"] for row in response.json()["data"]["curves"]] == ["A"]
    assert [row["security_id"] for row in response.json()["data"]["exclusions"]] == [
        "B"
    ]
    assert "missing_price" in response.json()["data"]["exclusions"][0]["reasons"]
    assert response.json()["coverage"] == {
        "expected": 2,
        "valid": 1,
        "excluded": 0,
        "missing": 1,
    }


async def _request_cached_result(jobs, result, path, params):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.http.app import create_workbench_app

    lease = jobs.claim("invalid-cache-worker")
    jobs.succeed(lease, result)
    app = create_workbench_app(
        repository=jobs.publications,
        analysis_repository=jobs,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(path, params=params)


@pytest.mark.anyio
async def test_cached_rank_requires_full_stock_partition(job_setup):
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id, symbols=())
    jobs.submit(session.token, session.csrf, command, key="rank-partition")
    snapshot = jobs.publications.get_snapshot(publication.id)
    result = materialize_custom_analysis(snapshot, command)
    result["ranking"] = []
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
    }

    response = await _request_cached_result(
        jobs,
        result,
        "/api/rank/range",
        params,
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_cached_compare_requires_exact_requested_partition(job_setup):
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id, symbols=("A",))
    jobs.submit(session.token, session.csrf, command, key="compare-partition")
    snapshot = jobs.publications.get_snapshot(publication.id)
    result = materialize_custom_analysis(snapshot, command)
    result["curves"] = []
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
        "symbols": "A",
    }

    response = await _request_cached_result(
        jobs,
        result,
        "/api/compare/curves",
        params,
    )

    assert response.status_code == 503


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("corruption", "point_index", "field", "value"),
    (
        ("null_baseline", 0, "both", None),
        ("wrong_normalized_baseline", 0, "normalized", Decimal("99")),
        ("wrong_drawdown_baseline", 0, "drawdown", Decimal("0.1")),
        ("nonpositive_normalized", 1, "normalized", Decimal("0")),
        ("negative_drawdown", 1, "drawdown", Decimal("-0.1")),
    ),
)
async def test_cached_compare_rejects_invalid_curve_baseline_and_values(
    job_setup, corruption, point_index, field, value
):
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id, symbols=("A",))
    jobs.submit(session.token, session.csrf, command, key=f"curve-{corruption}")
    snapshot = jobs.publications.get_snapshot(publication.id)
    result = materialize_custom_analysis(snapshot, command)
    point = result["curves"][0]["points"][point_index]
    if field == "both":
        point["normalized"] = None
        point["drawdown"] = None
    else:
        point[field] = value
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
        "symbols": "A",
    }

    response = await _request_cached_result(
        jobs,
        result,
        "/api/compare/curves",
        params,
    )

    assert response.status_code == 503, corruption


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", ("ranking", "curve_sessions"))
async def test_cached_result_deeply_validates_nested_rows(job_setup, corruption):
    from dragon_radar.analysis_jobs.worker import materialize_custom_analysis

    jobs, _owners, session, publication = job_setup
    symbols = () if corruption == "ranking" else ("A",)
    command = _command(publication.id, symbols=symbols)
    jobs.submit(
        session.token,
        session.csrf,
        command,
        key=f"nested-{corruption}",
    )
    snapshot = jobs.publications.get_snapshot(publication.id)
    result = materialize_custom_analysis(snapshot, command)
    if corruption == "ranking":
        result["ranking"][0]["topics"] = [{"topic_id": "incomplete"}]
        path = "/api/rank/range"
        extra = {}
    else:
        result["curves"][0]["points"][0]["trade_date"] = DAYS[1].isoformat()
        path = "/api/compare/curves"
        extra = {"symbols": "A"}
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
        **extra,
    }

    response = await _request_cached_result(
        jobs,
        result,
        path,
        params,
    )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_public_cache_reauthorizes_and_rejects_mismatched_result(
    job_setup, monkeypatch
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
    from dragon_radar.http.app import create_workbench_app

    jobs, _owners, session, publication = job_setup
    command = _command(publication.id, symbols=())
    jobs.submit(session.token, session.csrf, command, key="public-validation")
    assert (
        AnalysisJobWorker(jobs, worker_id="public-validation-worker").run_once().status
        == "succeeded"
    )
    original_get_cache = jobs.get_cache

    def mismatched(command):
        result = dict(original_get_cache(command))
        result["snapshot_id"] = str(uuid4())
        return result

    monkeypatch.setattr(jobs, "get_cache", mismatched)
    app = create_workbench_app(
        repository=jobs.publications,
        analysis_repository=jobs,
    )
    params = {
        "snapshot_id": str(publication.id),
        "start": DAYS[0].isoformat(),
        "end": DAYS[-1].isoformat(),
        "adj": "raw",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        invalid = await client.get("/api/rank/range", params=params)
    assert invalid.status_code == 503

    def revoke_then_get(command):
        jobs.publications.market.revoke_source("synthetic")
        return original_get_cache(command)

    monkeypatch.setattr(jobs, "get_cache", revoke_then_get)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        denied = await client.get("/api/rank/range", params=params)
    assert denied.status_code == 403


def test_readonly_analysis_repository_fails_closed_for_owner_methods(job_setup):
    from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
    from dragon_radar.storage.repository import Forbidden

    jobs, _owners, session, publication = job_setup
    readonly = AnalysisJobRepository(
        jobs.database,
        None,
        jobs.publications,
        clock=lambda: NOW,
    )

    with pytest.raises(Forbidden):
        readonly.submit(
            session.token,
            session.csrf,
            _command(publication.id),
            key="forbidden-owner-write",
        )
    with pytest.raises(Forbidden):
        readonly.get(session.token, uuid4())


@pytest.mark.anyio
async def test_app_factories_construct_analysis_repositories(database_url):
    from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.publication.repository import PublicationRepository

    public = create_workbench_app(database_url=database_url)
    assert isinstance(public.state.repository, PublicationRepository)
    assert isinstance(public.state.analysis_repository, AnalysisJobRepository)
    assert public.state.analysis_repository.owners is None
    async with public.router.lifespan_context(public):
        pass

    owner = create_owner_app(database_url=database_url)
    assert isinstance(owner.state.publication_repository, PublicationRepository)
    assert isinstance(owner.state.analysis_repository, AnalysisJobRepository)
    assert owner.state.analysis_repository.owners is owner.state.owner_repository
    assert not any(
        getattr(route, "path", None) in {"/api/rank/range", "/api/compare/curves"}
        for route in owner.routes
    )
    async with owner.router.lifespan_context(owner):
        pass
