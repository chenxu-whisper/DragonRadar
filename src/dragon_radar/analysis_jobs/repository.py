"""Transactional owner-scoped analysis job persistence."""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from dragon_radar.analysis_jobs.models import (
    AnalysisJobCommand,
    AnalysisJobLease,
    AnalysisJobRecord,
    analysis_job_cache,
    analysis_jobs,
)
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.tables import publications
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound, _identifier


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
        raise ValueError("Analysis result must be an object")
    return stored


def _record(row) -> AnalysisJobRecord:
    manifest = row["input_manifest"]
    command = manifest.get("command", manifest)
    return AnalysisJobRecord(
        id=row["id"],
        owner_id=row["owner_id"],
        command=AnalysisJobCommand.model_validate_json(
            json.dumps(command, ensure_ascii=True)
        ),
        adjustment_version=manifest["adjustment_version"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        result=row["result"],
        failure=row["failure"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _cache_identity(
    command: AnalysisJobCommand,
    adjustment_version: str,
) -> tuple[dict, str]:
    manifest = {
        "command": command.model_dump(mode="json"),
        "adjustment_version": adjustment_version,
    }
    canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return manifest, hashlib.sha256(canonical.encode("ascii")).hexdigest()


class AnalysisJobRepository:
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
            raise ValueError("Analysis job clock must be aware")
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
        command: AnalysisJobCommand,
        *,
        key: str,
    ) -> AnalysisJobRecord:
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
                command,
                key=key,
            )

    def submit_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        command: AnalysisJobCommand,
        *,
        key: str,
    ) -> AnalysisJobRecord:
        """Submit after the caller locked/authorized principal in the same transaction.

        This internal entry point never authenticates a caller. The caller must
        already have locked and authorized ``owner_id`` through OwnerRepository
        on this exact READ COMMITTED transaction. HTTP handlers must call
        :meth:`submit` and must never call this method directly.
        """
        if not connection.in_transaction():
            raise ValueError("Analysis submission requires a caller transaction")
        if (
            connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
            or connection.get_isolation_level() != "READ COMMITTED"
        ):
            raise ValueError("Analysis submission requires READ COMMITTED")
        _identifier(key)
        if not isinstance(owner_id, UUID):
            raise ValueError("Invalid owner ID")
        command = AnalysisJobCommand.model_validate(command.model_dump())
        request_hash = command.cache_key

        # Authorization precedes every idempotency replay or cache lookup.
        snapshot = self._authorized_input(connection, command.snapshot_id)
        manifest, cache_key = _cache_identity(
            command,
            snapshot.batch.adjustment_version,
        )
        self._lock(connection, f"analysis-job:{owner_id}:{key}")
        existing = (
            connection.execute(
                select(analysis_jobs).where(
                    analysis_jobs.c.owner_id == owner_id,
                    analysis_jobs.c.idempotency_key == key,
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
                select(analysis_job_cache).where(
                    analysis_job_cache.c.cache_key == cache_key,
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
            "cache_key": cache_key,
            "snapshot_id": command.snapshot_id,
            "input_manifest": manifest,
            "status": "succeeded" if cached is not None else "queued",
            "attempt_count": 0,
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
                insert(analysis_jobs).values(**values).returning(analysis_jobs)
            )
            .mappings()
            .one()
        )
        return _record(row)

    def get_cache(self, command: AnalysisJobCommand) -> dict | None:
        command = AnalysisJobCommand.model_validate(command.model_dump())
        with self.database.engine.begin() as connection:
            snapshot = self._authorized_input(connection, command.snapshot_id)
            _manifest, cache_key = _cache_identity(
                command,
                snapshot.batch.adjustment_version,
            )
            return connection.scalar(
                select(analysis_job_cache.c.result).where(
                    analysis_job_cache.c.cache_key == cache_key,
                )
            )

    def get(self, token: str, job_id: UUID | str) -> AnalysisJobRecord:
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        try:
            job_id = UUID(str(job_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid job ID") from None
        with self.database.engine.begin() as connection:
            owner_id = self.owners.authorize_connection(connection, token).owner_id
            row = (
                connection.execute(
                    select(analysis_jobs).where(
                        analysis_jobs.c.id == job_id,
                        analysis_jobs.c.owner_id == owner_id,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Analysis job not found")
            self._authorized_input(connection, row["snapshot_id"])
            return _record(row)

    def claim(
        self,
        worker_id: str,
        *,
        lease_for: timedelta = timedelta(seconds=30),
        max_attempts: int = 3,
    ) -> AnalysisJobLease | None:
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
                        select(analysis_jobs)
                        .where(
                            analysis_jobs.c.available_at <= now,
                            or_(
                                and_(
                                    analysis_jobs.c.status == "queued",
                                    analysis_jobs.c.attempt_count < max_attempts,
                                ),
                                and_(
                                    analysis_jobs.c.status == "running",
                                    analysis_jobs.c.lease_expires_at <= now,
                                ),
                            ),
                        )
                        .order_by(
                            analysis_jobs.c.available_at,
                            analysis_jobs.c.created_at,
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
                        update(analysis_jobs)
                        .where(analysis_jobs.c.id == row["id"])
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
            token = uuid4()
            expires_at = now + lease_for
            claimed = (
                connection.execute(
                    update(analysis_jobs)
                    .where(analysis_jobs.c.id == row["id"])
                    .values(
                        status="running",
                        attempt_count=row["attempt_count"] + 1,
                        lease_token=token,
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        started_at=row["started_at"] or now,
                    )
                    .returning(analysis_jobs)
                )
                .mappings()
                .one()
            )
            return AnalysisJobLease(
                job=_record(claimed),
                token=token,
                worker_id=worker_id,
                expires_at=expires_at,
            )

    def load_input(self, lease: AnalysisJobLease):
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            return self._authorized_input(connection, row["snapshot_id"])

    def renew(
        self,
        lease: AnalysisJobLease,
        *,
        lease_for: timedelta = timedelta(seconds=30),
    ) -> AnalysisJobLease:
        if not isinstance(lease_for, timedelta) or not (
            timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
        ):
            raise ValueError("Invalid lease duration")
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            expires_at = self._now() + lease_for
            renewed = (
                connection.execute(
                    update(analysis_jobs)
                    .where(analysis_jobs.c.id == row["id"])
                    .values(lease_expires_at=expires_at)
                    .returning(analysis_jobs)
                )
                .mappings()
                .one()
            )
            return AnalysisJobLease(
                job=_record(renewed),
                token=lease.token,
                worker_id=lease.worker_id,
                expires_at=expires_at,
            )

    def _active_lease(self, connection: Connection, lease: AnalysisJobLease):
        row = (
            connection.execute(
                select(analysis_jobs)
                .where(analysis_jobs.c.id == lease.job.id)
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
            raise Conflict("Analysis job lease is no longer active")
        return row

    def succeed(self, lease: AnalysisJobLease, result: Mapping) -> AnalysisJobRecord:
        result = _json_object(result)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            self._authorized_input(connection, row["snapshot_id"])
            now = self._now()
            connection.execute(
                pg_insert(analysis_job_cache)
                .values(
                    id=uuid4(),
                    cache_key=row["cache_key"],
                    algorithm_version=row["input_manifest"]["command"][
                        "algorithm_version"
                    ],
                    input_manifest=row["input_manifest"],
                    result=result,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[analysis_job_cache.c.cache_key])
            )
            authoritative = connection.scalar(
                select(analysis_job_cache.c.result).where(
                    analysis_job_cache.c.cache_key == row["cache_key"],
                )
            )
            completed = (
                connection.execute(
                    update(analysis_jobs)
                    .where(analysis_jobs.c.id == row["id"])
                    .values(
                        status="succeeded",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        result=authoritative,
                        failure=None,
                        completed_at=now,
                    )
                    .returning(analysis_jobs)
                )
                .mappings()
                .one()
            )
            return _record(completed)

    def fail(self, lease: AnalysisJobLease, failure: Mapping) -> AnalysisJobRecord:
        failure = _json_object(failure)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            completed = (
                connection.execute(
                    update(analysis_jobs)
                    .where(analysis_jobs.c.id == row["id"])
                    .values(
                        status="failed",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        result=None,
                        failure=failure,
                        completed_at=self._now(),
                    )
                    .returning(analysis_jobs)
                )
                .mappings()
                .one()
            )
            return _record(completed)


__all__ = ["AnalysisJobRepository"]
