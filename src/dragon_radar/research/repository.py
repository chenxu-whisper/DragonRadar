"""Transactional owner-scoped research backtest run persistence.

IO layer modelled on :mod:`dragon_radar.analysis_jobs.repository`: owner
isolation, idempotency replay and conflict, global config-hash result reuse,
transaction-scoped advisory locks, lease claim/renew, cooperative cancellation
and structured budget failures. Alongside :mod:`dragon_radar.research.worker`
this module is the only research component permitted database access.

The wall-clock budget is captured immutably at submission time from
:data:`DEFAULT_MAX_WALL_CLOCK_MS`; configuration bounds (securities, sessions,
folds) are already enforced by the pure :mod:`dragon_radar.research.models`
kernel, so the repository never accepts caller-supplied budget overrides.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, StrictStr
from sqlalchemy import and_, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.tables import publications
from dragon_radar.research.models import (
    BacktestConfig,
    BacktestSubmission,
    StrictFrozenModel,
)
from dragon_radar.research.tables import research_backtest_cache, research_backtest_runs
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    _identifier,
    _uuid,
)
from dragon_radar.topics.models import ImmutableJsonObject

# Conservative O4 wall-clock budget (5 minutes). The pure-Decimal engine has
# no pandas/numpy fast paths; the bound closes runaway runs without clipping
# the largest legal configuration (250 sessions x 100 names x 10 folds).
DEFAULT_MAX_WALL_CLOCK_MS = 300_000

ResearchRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
WorkerIdentifier = Annotated[
    StrictStr,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


def _json_object(value: Mapping) -> dict:
    stored = json.loads(
        json.dumps(
            value,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    )
    if not isinstance(stored, dict):
        raise ValueError("Research result must be an object")
    return stored


class ResearchRunRecord(StrictFrozenModel):
    """Immutable hydration of one run ledger row."""

    id: UUID
    owner_id: UUID
    config: BacktestConfig
    status: ResearchRunStatus
    attempt_count: Annotated[int, Field(ge=0)]
    max_wall_clock_ms: Annotated[int, Field(ge=0)]
    result: ImmutableJsonObject | None = None
    failure: ImmutableJsonObject | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ResearchRunLease(StrictFrozenModel):
    """Exclusive worker claim; a stale token loses every finish operation."""

    run: ResearchRunRecord
    token: UUID
    worker_id: WorkerIdentifier
    expires_at: datetime


def _record(row) -> ResearchRunRecord:
    # JSONB hydration returns strings/arrays; JSON-mode validation rebuilds
    # UUID, date and tuple fields under the strict model.
    config = BacktestConfig.model_validate_json(
        json.dumps(row["input_manifest"]["config"], ensure_ascii=True)
    )
    return ResearchRunRecord(
        id=row["id"],
        owner_id=row["owner_id"],
        config=config,
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_wall_clock_ms=row["max_wall_clock_ms"],
        result=row["result"],
        failure=row["failure"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


class ResearchRunRepository:
    def __init__(
        self,
        database,
        owner_repository,
        publication_repository: PublicationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.owners = owner_repository
        self.publications = publication_repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Research run clock must be aware")
        return value.astimezone(UTC)

    @staticmethod
    def _lock(connection: Connection, key: str) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def _authorized_input(self, connection: Connection, snapshot_id: UUID):
        row = (
            connection.execute(
                select(publications).where(publications.c.id == snapshot_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Publication not found")
        if row["status"] != "published":
            raise Forbidden("Publication is not available")
        return self.publications._authorized_records(connection, [row])[0]

    def submit(
        self,
        token: str,
        csrf: str,
        submission: BacktestSubmission,
    ) -> ResearchRunRecord:
        """Authorize token and CSRF, then submit in that same transaction."""
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        if not isinstance(csrf, str) or not csrf:
            from dragon_radar.owner import CsrfForbidden

            raise CsrfForbidden()
        with self.database.engine.begin() as connection:
            identity = self.owners.authorize_connection(connection, token, csrf)
            return self.submit_for_owner(
                connection,
                identity.owner_id,
                submission,
            )

    def submit_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        submission: BacktestSubmission,
    ) -> ResearchRunRecord:
        """Submit after the caller locked/authorized principal in the same transaction.

        This internal entry point never authenticates a caller. The caller must
        already have locked and authorized ``owner_id`` through OwnerRepository
        on this exact READ COMMITTED transaction. HTTP handlers must call
        :meth:`submit` and must never call this method directly.
        """
        if not connection.in_transaction():
            raise ValueError("Research submission requires a caller transaction")
        if (
            connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
            or connection.get_isolation_level() != "READ COMMITTED"
        ):
            raise ValueError("Research submission requires READ COMMITTED")
        if not isinstance(owner_id, UUID):
            raise ValueError("Invalid owner ID")
        submission = BacktestSubmission.model_validate(submission.model_dump())
        config = submission.config
        key = str(submission.idempotency_key)
        _identifier(key)
        # The config hash is the complete canonical identity of the request;
        # the idempotency key deliberately never participates in the hash.
        request_hash = config.config_hash

        # Authorization precedes every idempotency replay or cache lookup.
        self._authorized_input(connection, config.snapshot_id)
        manifest = {"config": config.model_dump(mode="json")}
        self._lock(connection, f"research-backtest:{owner_id}:{key}")
        existing = (
            connection.execute(
                select(research_backtest_runs).where(
                    research_backtest_runs.c.owner_id == owner_id,
                    research_backtest_runs.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise Conflict("Idempotency key already belongs to another request")
            return _record(existing)

        cached = (
            connection.execute(
                select(research_backtest_cache).where(
                    research_backtest_cache.c.config_hash == config.config_hash,
                )
            )
            .mappings()
            .first()
        )
        now = self._now()
        values = {
            "id": uuid4(),
            "owner_id": owner_id,
            "idempotency_key": key,
            "request_hash": request_hash,
            "config_hash": config.config_hash,
            "snapshot_id": config.snapshot_id,
            "input_manifest": manifest,
            "status": "succeeded" if cached is not None else "queued",
            "attempt_count": 0,
            "max_wall_clock_ms": DEFAULT_MAX_WALL_CLOCK_MS,
            "available_at": now,
            "lease_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "result": cached["result"] if cached is not None else None,
            "failure": None,
            "created_at": now,
            "started_at": None,
            "completed_at": now if cached is not None else None,
        }
        row = (
            connection.execute(
                insert(research_backtest_runs)
                .values(**values)
                .returning(research_backtest_runs)
            )
            .mappings()
            .one()
        )
        return _record(row)

    def get(self, token: str, run_id: UUID | str) -> ResearchRunRecord:
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        run_id = _uuid(run_id)
        with self.database.engine.begin() as connection:
            owner_id = self.owners.authorize_connection(connection, token).owner_id
            row = (
                connection.execute(
                    select(research_backtest_runs).where(
                        research_backtest_runs.c.id == run_id,
                        research_backtest_runs.c.owner_id == owner_id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Research run not found")
            self._authorized_input(connection, row["snapshot_id"])
            return _record(row)

    def claim(
        self,
        worker_id: str,
        *,
        lease_for: timedelta = timedelta(seconds=30),
        max_attempts: int = 3,
    ) -> ResearchRunLease | None:
        _identifier(worker_id)
        if not isinstance(lease_for, timedelta) or not (
            timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
        ):
            raise ValueError("Invalid lease duration")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid max attempts")
        now = self._now()
        with self.database.engine.begin() as connection:
            while True:
                row = (
                    connection.execute(
                        select(research_backtest_runs)
                        .where(
                            research_backtest_runs.c.available_at <= now,
                            or_(
                                and_(
                                    research_backtest_runs.c.status == "queued",
                                    research_backtest_runs.c.attempt_count
                                    < max_attempts,
                                ),
                                and_(
                                    research_backtest_runs.c.status == "running",
                                    research_backtest_runs.c.lease_expires_at <= now,
                                ),
                            ),
                        )
                        .order_by(
                            research_backtest_runs.c.available_at,
                            research_backtest_runs.c.created_at,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                if row["attempt_count"] >= max_attempts:
                    connection.execute(
                        update(research_backtest_runs)
                        .where(research_backtest_runs.c.id == row["id"])
                        .values(
                            status="failed",
                            lease_token=None,
                            lease_owner=None,
                            lease_expires_at=None,
                            result=None,
                            failure={"code": "attempts_exhausted"},
                            completed_at=now,
                        )
                    )
                    continue
                break
            lease_token = uuid4()
            expires_at = now + lease_for
            claimed = (
                connection.execute(
                    update(research_backtest_runs)
                    .where(research_backtest_runs.c.id == row["id"])
                    .values(
                        status="running",
                        attempt_count=row["attempt_count"] + 1,
                        lease_token=lease_token,
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        started_at=row["started_at"] or now,
                    )
                    .returning(research_backtest_runs)
                )
                .mappings()
                .one()
            )
            return ResearchRunLease(
                run=_record(claimed),
                token=lease_token,
                worker_id=worker_id,
                expires_at=expires_at,
            )

    def load_input(self, lease: ResearchRunLease):
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            return self._authorized_input(connection, row["snapshot_id"])

    def renew(
        self,
        lease: ResearchRunLease,
        *,
        lease_for: timedelta = timedelta(seconds=30),
    ) -> ResearchRunLease:
        if not isinstance(lease_for, timedelta) or not (
            timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
        ):
            raise ValueError("Invalid lease duration")
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            expires_at = self._now() + lease_for
            renewed = (
                connection.execute(
                    update(research_backtest_runs)
                    .where(research_backtest_runs.c.id == row["id"])
                    .values(lease_expires_at=expires_at)
                    .returning(research_backtest_runs)
                )
                .mappings()
                .one()
            )
            return ResearchRunLease(
                run=_record(renewed),
                token=lease.token,
                worker_id=lease.worker_id,
                expires_at=expires_at,
            )

    def _active_lease(self, connection: Connection, lease: ResearchRunLease):
        row = (
            connection.execute(
                select(research_backtest_runs)
                .where(research_backtest_runs.c.id == lease.run.id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        now = self._now()
        if (
            row is None
            or row["status"] != "running"
            or row["lease_token"] != lease.token
            or row["lease_owner"] != lease.worker_id
            or row["lease_expires_at"] <= now
        ):
            raise Conflict("Research run lease is no longer active")
        return row

    def succeed(
        self,
        lease: ResearchRunLease,
        result: Mapping,
    ) -> ResearchRunRecord:
        result = _json_object(result)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            self._authorized_input(connection, row["snapshot_id"])
            now = self._now()
            connection.execute(
                pg_insert(research_backtest_cache)
                .values(
                    id=uuid4(),
                    config_hash=row["config_hash"],
                    engine_version=row["input_manifest"]["config"]["schema_version"],
                    input_manifest=row["input_manifest"],
                    result=result,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[research_backtest_cache.c.config_hash]
                )
            )
            authoritative = connection.scalar(
                select(research_backtest_cache.c.result).where(
                    research_backtest_cache.c.config_hash == row["config_hash"],
                )
            )
            completed = (
                connection.execute(
                    update(research_backtest_runs)
                    .where(research_backtest_runs.c.id == row["id"])
                    .values(
                        status="succeeded",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        result=authoritative,
                        failure=None,
                        completed_at=now,
                    )
                    .returning(research_backtest_runs)
                )
                .mappings()
                .one()
            )
            return _record(completed)

    def fail(
        self,
        lease: ResearchRunLease,
        failure: Mapping,
    ) -> ResearchRunRecord:
        failure = _json_object(failure)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            completed = (
                connection.execute(
                    update(research_backtest_runs)
                    .where(research_backtest_runs.c.id == row["id"])
                    .values(
                        status="failed",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        result=None,
                        failure=failure,
                        completed_at=self._now(),
                    )
                    .returning(research_backtest_runs)
                )
                .mappings()
                .one()
            )
            return _record(completed)

    def cancel(
        self,
        token: str,
        csrf: str,
        run_id: UUID | str,
    ) -> ResearchRunRecord:
        """Owner-scoped cooperative cancellation.

        Queued runs terminate immediately. A running run is flipped to
        ``cancelled`` and its lease is cleared; the worker observes the dead
        lease at its next checkpoint via :meth:`_active_lease` and stops
        without writing a result. No cancel-request flag column is needed and
        no thread is forcibly killed.
        """
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        if not isinstance(csrf, str) or not csrf:
            from dragon_radar.owner import CsrfForbidden

            raise CsrfForbidden()
        run_id = _uuid(run_id)
        with self.database.engine.begin() as connection:
            owner_id = self.owners.authorize_connection(
                connection, token, csrf
            ).owner_id
            row = (
                connection.execute(
                    select(research_backtest_runs)
                    .where(
                        research_backtest_runs.c.id == run_id,
                        research_backtest_runs.c.owner_id == owner_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Research run not found")
            if row["status"] not in ("queued", "running"):
                raise Conflict("Only unfinished research runs can be cancelled")
            cancelled = (
                connection.execute(
                    update(research_backtest_runs)
                    .where(research_backtest_runs.c.id == run_id)
                    .values(
                        status="cancelled",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        completed_at=self._now(),
                    )
                    .returning(research_backtest_runs)
                )
                .mappings()
                .one()
            )
            return _record(cancelled)


__all__ = [
    "DEFAULT_MAX_WALL_CLOCK_MS",
    "ResearchRunLease",
    "ResearchRunRecord",
    "ResearchRunRepository",
]
