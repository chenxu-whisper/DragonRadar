"""Withdrawal ledger, dependency-aware retention, and recovery gates."""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dragon_radar.publication.models import (
    DOMAIN_NAMES,
    RetentionPolicy,
    WithdrawalRecord,
)
from dragon_radar.publication.tables import (
    publication_module_controls,
    publication_results,
    source_dependencies,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.models import import_batches, source_grants
from dragon_radar.storage.repository import Conflict, Repository, _identifier
from dragon_radar.topics.tables import topic_import_batches


class PublicationCache:
    """Small process-local cache with source-scoped invalidation."""

    def __init__(self):
        self._lock = RLock()
        self._values = {}
        self._sources = {}

    def put(self, source_id: str, key: UUID, value) -> None:
        _identifier(source_id)
        with self._lock:
            self._values[key] = value
            self._sources[key] = source_id

    def get(self, key: UUID):
        with self._lock:
            return self._values.get(key)

    def clear_source(self, source_id: str) -> None:
        with self._lock:
            keys = [
                key
                for key, candidate in self._sources.items()
                if candidate == source_id
            ]
            for key in keys:
                self._values.pop(key, None)
                self._sources.pop(key, None)


def _record(row) -> WithdrawalRecord:
    return WithdrawalRecord(
        id=row["id"],
        sequence=row["sequence"],
        source_id=row["source_id"],
        scope=row["scope"],
        action=row["action"],
        policy_reference=row["policy_reference"],
        cutoff_dependency_sequence=row["cutoff_dependency_sequence"],
        details=row["details"],
        occurred_at=row["occurred_at"],
    )


class RetentionService:
    def __init__(
        self,
        database,
        repository: Repository | None = None,
        *,
        cache: PublicationCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.repository = repository or Repository(database, clock=clock)
        self.cache = cache or PublicationCache()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _append(
        self,
        connection,
        *,
        source_id,
        scope,
        action,
        policy_reference=None,
        cutoff_dependency_sequence=None,
        details=None,
    ):
        return (
            connection.execute(
                insert(withdrawal_records)
                .values(
                    id=uuid4(),
                    source_id=source_id,
                    scope=scope,
                    action=action,
                    policy_reference=policy_reference,
                    cutoff_dependency_sequence=cutoff_dependency_sequence,
                    details=details or {},
                    occurred_at=self.clock(),
                )
                .returning(withdrawal_records)
            )
            .mappings()
            .one()
        )

    def _disable_modules(self, connection, source_id):
        domains = set(
            connection.execute(
                select(source_dependencies.c.derived_type).where(
                    source_dependencies.c.source_id == source_id
                )
            ).scalars()
        )
        for domain in sorted(domains or DOMAIN_NAMES):
            values = {
                "source_id": source_id,
                "module": domain,
                "enabled": False,
                "blocked_by_withdrawal": True,
                "updated_at": self.clock(),
            }
            connection.execute(
                pg_insert(publication_module_controls)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        publication_module_controls.c.source_id,
                        publication_module_controls.c.module,
                    ],
                    set_=values,
                )
            )

    def _bind_withdrawal(self, connection, withdrawal) -> None:
        dependencies = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.source_id == withdrawal["source_id"],
                    source_dependencies.c.dependency_sequence
                    <= withdrawal["cutoff_dependency_sequence"],
                )
            )
            .mappings()
            .all()
        )
        bound_archives = set()
        for dependency in dependencies:
            bound_archives.add((dependency["archive_type"], dependency["archive_id"]))
            connection.execute(
                insert(withdrawal_dependencies).values(
                    id=uuid4(),
                    withdrawal_id=withdrawal["id"],
                    source_dependency_id=dependency["id"],
                    created_at=self.clock(),
                )
            )
        archive_tables = (
            ("market_import_batch", import_batches),
            ("topic_import_batch", topic_import_batches),
        )
        for archive_type, table in archive_tables:
            archive_ids = connection.execute(
                select(table.c.id).where(table.c.source_id == withdrawal["source_id"])
            ).scalars()
            for archive_id in archive_ids:
                if (archive_type, archive_id) in bound_archives:
                    continue
                connection.execute(
                    insert(withdrawal_dependencies).values(
                        id=uuid4(),
                        withdrawal_id=withdrawal["id"],
                        source_dependency_id=None,
                        archive_type=archive_type,
                        archive_id=archive_id,
                        created_at=self.clock(),
                    )
                )

    def _revoke_grant(self, connection, source_id, *, storage):
        current = self.repository._source(connection, source_id)
        grant = current.model_dump(mode="json")
        grant["local_display"] = False
        if storage:
            grant["local_storage"] = False
        connection.execute(
            update(source_grants)
            .where(source_grants.c.source_id == source_id)
            .values(grant=grant, updated_at=self.clock())
        )

    def withdraw_display(self, source_id: str) -> WithdrawalRecord:
        _identifier(source_id)
        with self.database.engine.begin() as connection:
            self._revoke_grant(connection, source_id, storage=False)
            row = self._append(
                connection,
                source_id=source_id,
                scope="display",
                action="blocked",
                details={"cache_invalidated": True},
            )
        self.cache.clear_source(source_id)
        return _record(row)

    def withdraw_storage(
        self, source_id: str, *, policy: RetentionPolicy | None = None
    ) -> WithdrawalRecord:
        _identifier(source_id)
        if policy is not None:
            policy = RetentionPolicy.model_validate(policy.model_dump())
            if not policy.approved:
                raise ValueError("Retention policy is not approved")
        with self.database.engine.begin() as connection:
            self._revoke_grant(connection, source_id, storage=True)
            self._disable_modules(connection, source_id)
            self._append(
                connection,
                source_id=source_id,
                scope="storage",
                action="blocked",
                details={"policy_required": policy is None},
            )
            if policy is None:
                row = (
                    connection.execute(
                        select(withdrawal_records)
                        .where(withdrawal_records.c.source_id == source_id)
                        .order_by(withdrawal_records.c.sequence.desc())
                        .limit(1)
                    )
                    .mappings()
                    .one()
                )
            else:
                action = "deidentified" if policy.action == "deidentify" else "deleted"
                cutoff = (
                    connection.scalar(
                        select(
                            func.max(source_dependencies.c.dependency_sequence)
                        ).where(source_dependencies.c.source_id == source_id)
                    )
                    or 0
                )
                row = self._append(
                    connection,
                    source_id=source_id,
                    scope="storage",
                    action=action,
                    policy_reference=policy.reference,
                    cutoff_dependency_sequence=cutoff,
                    details={"derived_content": policy.action},
                )
                self._bind_withdrawal(connection, row)
                affected = (
                    select(
                        source_dependencies.c.publication_id,
                        source_dependencies.c.derived_type,
                    )
                    .join(
                        withdrawal_dependencies,
                        withdrawal_dependencies.c.source_dependency_id
                        == source_dependencies.c.id,
                    )
                    .where(withdrawal_dependencies.c.withdrawal_id == row["id"])
                )
                pairs = set(connection.execute(affected).tuples())
                for publication_id, domain in sorted(
                    pairs, key=lambda pair: (str(pair[0]), pair[1])
                ):
                    predicate = (
                        publication_results.c.publication_id == publication_id,
                        publication_results.c.domain == domain,
                    )
                    if policy.action == "deidentify":
                        connection.execute(
                            update(publication_results)
                            .where(*predicate)
                            .values(
                                status="disabled",
                                payload={},
                                coverage={},
                                quality_flags=["withdrawn"],
                            )
                        )
                    else:
                        connection.execute(
                            delete(publication_results).where(*predicate)
                        )
        self.cache.clear_source(source_id)
        return _record(row)

    def replay_withdrawals(self, source_id: str) -> WithdrawalRecord:
        _identifier(source_id)
        with self.database.engine.begin() as connection:
            self.repository._source(connection, source_id)
            rows = (
                connection.execute(
                    select(withdrawal_records)
                    .where(withdrawal_records.c.source_id == source_id)
                    .order_by(withdrawal_records.c.sequence)
                )
                .mappings()
                .all()
            )
            restored_sequence = max(
                (row["sequence"] for row in rows if row["action"] == "restored"),
                default=0,
            )
            pending_blocks = [
                row
                for row in rows
                if row["sequence"] > restored_sequence and row["action"] == "blocked"
            ]
            if not pending_blocks:
                raise Conflict("No withdrawal records to replay")
            latest_block = pending_blocks[-1]
            through_sequence = max(
                row["sequence"]
                for row in rows
                if row["sequence"] > restored_sequence
                and row["action"] in ("blocked", "deidentified", "deleted")
            )
            row = self._append(
                connection,
                source_id=source_id,
                scope=latest_block["scope"],
                action="replayed",
                details={"through_sequence": through_sequence},
            )
        self.cache.clear_source(source_id)
        return _record(row)

    def restore_source(self, source_id: str, grant) -> WithdrawalRecord:
        _identifier(source_id)
        if (
            grant.source_id != source_id
            or not grant.approved
            or not grant.local_display
        ):
            raise ValueError("Restored grant is not usable")
        with self.database.engine.begin() as connection:
            self.repository._source(connection, source_id)
            rows = (
                connection.execute(
                    select(withdrawal_records)
                    .where(withdrawal_records.c.source_id == source_id)
                    .order_by(withdrawal_records.c.sequence)
                )
                .mappings()
                .all()
            )
            restored_sequence = max(
                (row["sequence"] for row in rows if row["action"] == "restored"),
                default=0,
            )
            pending = [row for row in rows if row["sequence"] > restored_sequence]
            blocked = [row for row in pending if row["action"] == "blocked"]
            if not blocked:
                raise Conflict("Withdrawal ledger must be replayed before restore")
            latest_block = blocked[-1]
            required_replay_sequence = max(
                row["sequence"]
                for row in pending
                if row["action"] in ("blocked", "deidentified", "deleted")
            )
            replayed = [
                row
                for row in pending
                if row["action"] == "replayed"
                and row["sequence"] > required_replay_sequence
                and type(row["details"].get("through_sequence")) is int
                and row["details"]["through_sequence"] >= required_replay_sequence
            ]
            if not replayed:
                raise Conflict("Withdrawal ledger must be replayed before restore")
            storage_blocks = [row for row in blocked if row["scope"] == "storage"]
            if storage_blocks:
                latest_storage_block = storage_blocks[-1]
                applied = [
                    row
                    for row in pending
                    if row["action"] in ("deidentified", "deleted")
                    and row["scope"] == "storage"
                    and row["sequence"] > latest_storage_block["sequence"]
                ]
                if not applied or not grant.local_storage:
                    raise Conflict("Approved storage retention action is required")
            connection.execute(
                update(source_grants)
                .where(source_grants.c.source_id == source_id)
                .values(grant=grant.model_dump(mode="json"), updated_at=self.clock())
            )
            connection.execute(
                update(publication_module_controls)
                .where(publication_module_controls.c.source_id == source_id)
                .values(
                    enabled=True,
                    blocked_by_withdrawal=False,
                    updated_at=self.clock(),
                )
            )
            row = self._append(
                connection,
                source_id=source_id,
                scope=latest_block["scope"],
                action="restored",
                details={},
            )
        self.cache.clear_source(source_id)
        return _record(row)
