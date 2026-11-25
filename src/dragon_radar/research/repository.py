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

from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.tables import publications
from dragon_radar.research.cache_contract import SCHEMAS, compute_contract
from dragon_radar.research.models import (
    BacktestConfig,
    BacktestPeriod,
    BacktestSubmission,
    DomainManifest,
    InputManifests,
    ManifestVersions,
    StrictFrozenModel,
)
from dragon_radar.research.result_contracts import (
    ResearchDataUnavailable,
    canonical,
    manifests_equal,
    validate_envelope,
    validate_result,
)
from dragon_radar.research.tables import (
    research_backtest_cache,
    research_backtest_cache_v2,
    research_backtest_runs,
)
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


class ResearchCacheConflict(Conflict):
    """Deterministic facts differ; not a stale lease or first-writer success."""


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


def validated_cache_result(cached, config, *, candidate=None):
    """Never trust a cache key in place of its immutable input/result binding."""
    frozen = BacktestConfig.model_validate_json(
        canonical(cached["input_manifest"]["config"])
    )
    if frozen.canonical_json() != config.canonical_json():
        raise ValueError("cached frozen config mismatch")
    payload = cached["result"]
    validate_result(payload, config)
    if payload["schema_version"] == SCHEMAS["v2"]:
        history = cached["input_manifest"].get("history")
        if history is None or canonical(history) != canonical(
            payload["lineage"]["frames"]
        ):
            raise ValueError("cached authoritative frame mapping mismatch")

    def facts(value):
        value = json.loads(canonical(value))
        if value.get("schema_version") == SCHEMAS["v2"]:
            # Completion is the immutable first computation's clock, not a
            # deterministic input fact. Cache reuse retains that original clock.
            value["lineage"].pop("computed_at", None)
        return canonical(value)

    if candidate is not None and facts(payload) != facts(candidate):
        raise ValueError("conflicting deterministic materialized result")
    return payload


def manifest_from_snapshot(snapshot: PublishedSnapshot) -> dict:
    """Project an authorized publication onto the owner BacktestManifestContext.

    Domain UUIDs come from the immutable publication row while adjustment,
    calendar and pool version tokens come from the published MarketBatch; the
    strict kernel models re-validate every value so the wire payload can be
    round-tripped straight into an EX14 ``input_manifests`` field. Only
    market/topic/role are research domains, and optional domains stay absent
    until their publication input exists.
    """
    publication = snapshot.publication
    domains = [
        DomainManifest(
            domain="market",
            snapshot_id=publication.market_snapshot_id,
        )
    ]
    if publication.topic_snapshot_id is not None:
        domains.append(
            DomainManifest(
                domain="topic",
                snapshot_id=publication.topic_snapshot_id,
            )
        )
    if publication.role_input_id is not None:
        domains.append(
            DomainManifest(
                domain="role",
                snapshot_id=publication.role_input_id,
            )
        )
    batch = snapshot.market_snapshot.batch
    manifests = InputManifests(
        domains=tuple(domains),
        versions=ManifestVersions(
            adjustment=batch.adjustment_version,
            calendar=batch.calendar_version,
            pool=batch.pool_version,
        ),
    )
    return {
        "snapshot_id": str(publication.id),
        **manifests.model_dump(mode="json"),
    }


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
    result_schema_version: str = SCHEMAS["v1"]
    compute_contract_sha256: str = "0" * 64


class ResearchRunLease(StrictFrozenModel):
    """Exclusive worker claim; a stale token loses every finish operation."""

    run: ResearchRunRecord
    token: UUID
    worker_id: WorkerIdentifier
    expires_at: datetime


def _record_body(row) -> ResearchRunRecord:
    # JSONB hydration returns strings/arrays; JSON-mode validation rebuilds
    # UUID, date and tuple fields under the strict model.
    config = BacktestConfig.model_validate_json(
        json.dumps(row["input_manifest"]["config"], ensure_ascii=True)
    )
    record = ResearchRunRecord(
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
        result_schema_version=row.get("result_schema_version", SCHEMAS["v1"]),
        compute_contract_sha256=row.get("compute_contract_sha256", "0" * 64),
    )
    try:
        validate_envelope(
            {
                "run_id": str(record.id),
                "status": record.status,
                "config_hash": config.config_hash,
                "research_only": True,
                "result": record.result,
                "failure": record.failure,
            },
            config,
            run_id=record.id,
        )
        if record.result is not None:
            if record.result["schema_version"] != record.result_schema_version:
                raise ValueError("frozen result contract mismatch")
    except (ValueError, TypeError, KeyError) as exc:
        raise ResearchDataUnavailable("materialized research data unavailable") from exc
    return record


def _record(row) -> ResearchRunRecord:
    try:
        return _record_body(row)
    except (ValueError, TypeError, KeyError) as exc:
        raise ResearchDataUnavailable("materialized research data unavailable") from exc


class ResearchRunRepository:
    def __init__(
        self,
        database,
        owner_repository,
        publication_repository: PublicationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        result_version: str = "v1",
        approved_descriptor: Mapping | None = None,
    ):
        self.database = database
        self.owners = owner_repository
        self.publications = publication_repository
        self.clock = clock or (lambda: datetime.now(UTC))
        if result_version not in SCHEMAS:
            raise ValueError("unsupported deployment research contract")
        self.result_schema_version = SCHEMAS[result_version]
        self.compute_contract_sha256 = compute_contract(
            result_version,
            approved_descriptor=approved_descriptor,
        )

    @staticmethod
    def _cache_table(schema):
        return (
            research_backtest_cache_v2
            if schema == SCHEMAS["v2"]
            else research_backtest_cache
        )

    @staticmethod
    def _cache_where(table, config_hash, schema, contract):
        predicate = table.c.config_hash == config_hash
        if table is research_backtest_cache_v2:
            predicate = and_(
                predicate,
                table.c.result_schema_version == schema,
                table.c.compute_contract_sha256 == contract,
            )
        return predicate

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

    def _validate_history(
        self,
        connection: Connection,
        result,
        *,
        config=None,
        contract=None,
        trusted_history=None,
    ) -> None:
        if result is None or result.get("schema_version") != SCHEMAS["v2"]:
            return
        try:
            frozen = json.loads(canonical(result))
            frames = frozen["lineage"]["frames"]
            if trusted_history is None:
                if config is None or contract is None:
                    raise ValueError("trusted historical mapping unavailable")
                cached = (
                    connection.execute(
                        select(research_backtest_cache_v2).where(
                            self._cache_where(
                                research_backtest_cache_v2,
                                config.config_hash,
                                SCHEMAS["v2"],
                                contract,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if cached is None:
                    raise ValueError("trusted historical mapping unavailable")
                validated_cache_result(cached, config)
                trusted_history = cached["input_manifest"]["history"]
            if canonical(frames) != canonical(trusted_history):
                raise ValueError("historical archive digest/cutoff mismatch")
            identities = [UUID(frame["publication_id"]) for frame in frames]
            rows = (
                connection.execute(
                    select(publications).where(publications.c.id.in_(identities))
                )
                .mappings()
                .all()
            )
            by_id = {row["id"]: row for row in rows}
            if len(by_id) != len(identities):
                raise ValueError("historical publications unavailable")
            completed = datetime.fromisoformat(
                frozen["lineage"]["computed_at"].replace("Z", "+00:00")
            )
            for frame, identity in zip(frames, identities, strict=True):
                row = by_id[identity]
                if row["status"] != "published":
                    raise Forbidden("Historical publication access denied")
                expected = (
                    row["publication_sequence"],
                    row["trade_date"].isoformat(),
                    str(row["market_snapshot_id"]),
                    str(row["topic_snapshot_id"]) if row["topic_snapshot_id"] else None,
                    str(row["role_input_id"]) if row["role_input_id"] else None,
                )
                actual = tuple(
                    frame[key]
                    for key in (
                        "publication_sequence",
                        "trade_date",
                        "market_snapshot_id",
                        "topic_snapshot_id",
                        "role_snapshot_id",
                    )
                )
                cutoff = datetime.fromisoformat(frame["as_of"].replace("Z", "+00:00"))
                if actual != expected or not cutoff <= row["published_at"] <= completed:
                    raise ValueError("historical publication binding mismatch")
            self.publications._authorized_records(connection, rows)
        except (ValueError, TypeError, KeyError) as exc:
            raise ResearchDataUnavailable(
                "materialized research history unavailable"
            ) from exc

    def input_manifest(self, token: str, snapshot_id: UUID | str) -> dict:
        """Authorize an owner session, then project the publication manifest.

        Read-only and fail-closed: unknown publications map to NotFound and
        non-published rows to Forbidden through the shared
        :meth:`_authorized_input` path. No published material is recomputed.
        """
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            self.owners.authorize_connection(connection, token)
            snapshot = self._authorized_input(connection, snapshot_id)
            return manifest_from_snapshot(snapshot)

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
        snapshot = self._authorized_input(connection, config.snapshot_id)
        manifest_context = manifest_from_snapshot(snapshot)
        authoritative = InputManifests.model_validate_json(
            canonical(
                {
                    key: value
                    for key, value in manifest_context.items()
                    if key != "snapshot_id"
                }
            )
        )
        if not manifests_equal(config.input_manifests, authoritative):
            raise Conflict("Research input manifests do not match publication")
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
            record = _record(existing)
            self._validate_history(
                connection,
                record.result,
                config=record.config,
                contract=record.compute_contract_sha256,
            )
            return record

        cache = self._cache_table(self.result_schema_version)
        cached = (
            connection.execute(
                select(cache).where(
                    self._cache_where(
                        cache,
                        config.config_hash,
                        self.result_schema_version,
                        self.compute_contract_sha256,
                    )
                )
            )
            .mappings()
            .first()
        )
        if cached is not None:
            try:
                validated_cache_result(cached, config)
            except (ValueError, TypeError, KeyError) as exc:
                raise ResearchDataUnavailable(
                    "materialized research cache unavailable"
                ) from exc
            self._validate_history(
                connection,
                cached["result"],
                config=config,
                contract=self.compute_contract_sha256,
            )
        now = self._now()
        values = {
            "id": uuid4(),
            "owner_id": owner_id,
            "idempotency_key": key,
            "request_hash": request_hash,
            "config_hash": config.config_hash,
            "result_schema_version": self.result_schema_version,
            "compute_contract_sha256": self.compute_contract_sha256,
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
            record = _record(row)
            self._validate_history(
                connection,
                record.result,
                config=record.config,
                contract=record.compute_contract_sha256,
            )
            return record

    def run_config(self, token: str, run_id: UUID | str) -> dict:
        record = self.get(token, run_id)
        return {
            "run_id": str(record.id),
            "config_hash": record.config.config_hash,
            "config": record.config.model_dump(mode="json"),
        }

    def capabilities(
        self, token: str, snapshot_id: UUID | str, period: BacktestPeriod
    ) -> dict:
        from dragon_radar.research.capabilities import disabled_capabilities

        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            self.owners.authorize_connection(connection, token)
            snapshot = self._authorized_input(connection, snapshot_id)
            sessions = tuple(
                day
                for day in snapshot.market_snapshot.batch.calendar.sessions
                if period.start <= day <= period.end
            )
            if (
                not sessions
                or len(sessions) > 250
                or sessions[0] != period.start
                or sessions[-1] != period.end
                or period.end > snapshot.trade_date
            ):
                raise ValueError("Research capability window unavailable")
            # No package registry is supplied by this deployment. Enumerated
            # kernel IDs alone are not evidence of production readiness.
            return disabled_capabilities(snapshot_id, period)

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

    def check_lease(self, lease: ResearchRunLease) -> None:
        """Read-only liveness probe backing worker cooperative checkpoints.

        Raises :class:`Conflict` when the run was cancelled, the lease was
        taken over by another worker, or the lease expired. Performs no write
        and never fabricates a state transition.
        """
        with self.database.engine.begin() as connection:
            self._active_lease(connection, lease)

    def fetch_run(self, run_id: UUID | str) -> ResearchRunRecord:
        """Hydrate the authoritative row after a stale worker loses its lease.

        System-level (no owner authorization): the worker only reads runs it
        already claimed, and uses this to return the current terminal or
        take-over state instead of writing through a dead token.
        """
        run_id = _uuid(run_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(research_backtest_runs).where(
                        research_backtest_runs.c.id == run_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Research run not found")
            return _record(row)

    def succeed(
        self,
        lease: ResearchRunLease,
        result: Mapping,
        *,
        executing_contract=None,
        trusted_history=None,
    ) -> ResearchRunRecord:
        result = _json_object(result)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            from dragon_radar.research.worker import contract_matches

            if not contract_matches(_record(row), executing_contract):
                raise ValueError("executing compute differs from frozen run contract")
            self._authorized_input(connection, row["snapshot_id"])
            config = BacktestConfig.model_validate_json(
                canonical(row["input_manifest"]["config"])
            )
            validate_result(result, config)
            if (
                row["result_schema_version"] == SCHEMAS["v2"]
                and trusted_history is None
            ):
                raise ResearchDataUnavailable("trusted historical mapping unavailable")
            self._validate_history(connection, result, trusted_history=trusted_history)
            if result["schema_version"] != row["result_schema_version"]:
                raise ValueError(
                    "worker result differs from frozen deployment contract"
                )
            now = self._now()
            cache = self._cache_table(row["result_schema_version"])
            cache_manifest = dict(row["input_manifest"])
            if row["result_schema_version"] == SCHEMAS["v2"]:
                cache_manifest["history"] = json.loads(canonical(trusted_history))
            cache_values = dict(
                id=uuid4(),
                config_hash=row["config_hash"],
                input_manifest=cache_manifest,
                result=result,
                created_at=now,
            )
            if cache is research_backtest_cache_v2:
                cache_values.update(
                    result_schema_version=row["result_schema_version"],
                    compute_contract_sha256=row["compute_contract_sha256"],
                )
                unique = [
                    cache.c.config_hash,
                    cache.c.result_schema_version,
                    cache.c.compute_contract_sha256,
                ]
            else:
                cache_values["engine_version"] = row["input_manifest"]["config"][
                    "schema_version"
                ]
                unique = [cache.c.config_hash]
            connection.execute(
                pg_insert(cache)
                .values(**cache_values)
                .on_conflict_do_nothing(index_elements=unique)
            )
            cached = (
                connection.execute(
                    select(cache).where(
                        self._cache_where(
                            cache,
                            row["config_hash"],
                            row["result_schema_version"],
                            row["compute_contract_sha256"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            try:
                authoritative = validated_cache_result(cached, config, candidate=result)
            except (ValueError, TypeError, KeyError) as exc:
                raise ResearchCacheConflict(
                    "Conflicting immutable research cache"
                ) from exc
            self._validate_history(
                connection,
                authoritative,
                config=config,
                contract=row["compute_contract_sha256"],
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
    "manifest_from_snapshot",
]
