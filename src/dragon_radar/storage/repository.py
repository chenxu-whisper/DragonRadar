"""Local operator writes and currently authorized published-snapshot reads."""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.domain.quality import grant_allows_local, validate_batch
from dragon_radar.storage.database import Database
from dragon_radar.storage.models import (
    ImportRecord,
    Snapshot,
    audit_events,
    import_batches,
    snapshots,
    source_grants,
)


class Conflict(Exception):
    pass


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class QualityRejected(Exception):
    pass


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ValueError("Identifier must contain 1-200 safe ASCII characters")
    return value


def _uuid(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid record ID") from None


def _record(row) -> ImportRecord:
    return ImportRecord(
        row["id"], row["canonical_json"], row["payload_hash"], row["quality_report"]
    )


class Repository:
    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] | None = None
    ):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _lock(connection, key: str) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def _source(self, connection, source_id: str) -> SourceGrant:
        # All source operations share this lock, including first registration.
        self._lock(connection, "source:" + source_id)
        row = (
            connection.execute(
                select(source_grants)
                .where(source_grants.c.source_id == source_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise Forbidden("Source is not locally authorized")
        return SourceGrant.model_validate(row["grant"])

    def _authorize(self, current: SourceGrant, batch: MarketBatch) -> datetime:
        now = self.clock()
        if not all(
            grant_allows_local(grant, source_id=batch.source_id, now=now)
            for grant in (current, batch.grant)
        ):
            raise Forbidden("Source is not locally authorized")
        return now

    def register_source(self, grant: SourceGrant) -> None:
        grant = SourceGrant.model_validate(grant.model_dump())
        _identifier(grant.source_id)
        if grant.reference is not None and (
            len(grant.reference) > 1000
            or any(ord(char) < 32 or ord(char) == 127 for char in grant.reference)
        ):
            raise ValueError("Invalid grant reference")
        with self.database.engine.begin() as connection:
            self._lock(connection, "source:" + grant.source_id)
            values = {
                "source_id": grant.source_id,
                "grant": grant.model_dump(mode="json"),
                "updated_at": self.clock(),
            }
            connection.execute(
                pg_insert(source_grants)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[source_grants.c.source_id], set_=values
                )
            )

    def revoke_source(self, source_id: str) -> None:
        _identifier(source_id)
        with self.database.engine.begin() as connection:
            current = self._source(connection, source_id)
            values = current.model_dump(mode="json")
            values["approved"] = False
            connection.execute(
                update(source_grants)
                .where(source_grants.c.source_id == source_id)
                .values(grant=values, updated_at=self.clock())
            )

    @staticmethod
    def _audit(connection, action, source_id, object_id, now, details=None):
        connection.execute(
            insert(audit_events).values(
                id=uuid4(),
                action=action,
                source_id=source_id,
                object_id=str(object_id),
                details=details or {},
                occurred_at=now,
            )
        )

    def import_batch(self, batch: MarketBatch, *, key: str) -> ImportRecord:
        _identifier(key)
        batch = MarketBatch.model_validate(batch.model_dump())
        _identifier(batch.source_id)
        canonical = json.dumps(
            batch.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.database.engine.begin() as connection:
            self._lock(connection, "import-key:" + key)
            current = self._source(connection, batch.source_id)
            now = self._authorize(current, batch)
            existing = (
                connection.execute(
                    select(import_batches).where(
                        import_batches.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["payload_hash"] != digest:
                    raise Conflict("Idempotency key already belongs to another payload")
                return _record(existing)
            report = validate_batch(batch, now=now)
            quality = {
                "issues": [issue.model_dump(mode="json") for issue in report.issues],
                "coverage": report.coverage.model_dump(mode="json"),
                "publishable": report.publishable,
            }
            row = (
                connection.execute(
                    insert(import_batches)
                    .values(
                        id=uuid4(),
                        idempotency_key=key,
                        source_id=batch.source_id,
                        trade_date=batch.trade_date,
                        canonical_json=canonical,
                        payload_hash=digest,
                        quality_report=quality,
                        imported_at=now,
                    )
                    .returning(import_batches)
                )
                .mappings()
                .one()
            )
            self._audit(
                connection,
                "import",
                batch.source_id,
                row["id"],
                now,
                {"payload_hash": digest, "publishable": report.publishable},
            )
            return _record(row)

    @staticmethod
    def _snapshot(row, archive) -> Snapshot:
        return Snapshot(
            id=row["id"],
            batch_id=row["batch_id"],
            revision=row["revision"],
            supersedes=row["supersedes"],
            published_at=row["published_at"],
            manifest=row["manifest"],
            quality_report=archive["quality_report"],
            batch=MarketBatch.model_validate_json(archive["canonical_json"]),
        )

    def publish(self, batch_id: UUID | str) -> Snapshot:
        batch_id = _uuid(batch_id)
        with self.database.engine.begin() as connection:
            archive = (
                connection.execute(
                    select(import_batches).where(import_batches.c.id == batch_id)
                )
                .mappings()
                .first()
            )
            if archive is None:
                raise NotFound("Batch not found")
            current = self._source(connection, archive["source_id"])
            batch = MarketBatch.model_validate_json(archive["canonical_json"])
            now = self._authorize(current, batch)
            if (
                not archive["quality_report"]["publishable"]
                or not validate_batch(batch, now=now).publishable
            ):
                raise QualityRejected("Batch failed the quality gate")
            existing = (
                connection.execute(
                    select(snapshots).where(snapshots.c.batch_id == batch_id)
                )
                .mappings()
                .first()
            )
            if existing:
                return self._snapshot(existing, archive)
            previous = (
                connection.execute(
                    select(snapshots)
                    .where(
                        snapshots.c.source_id == batch.source_id,
                        snapshots.c.trade_date == batch.trade_date,
                    )
                    .order_by(snapshots.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            manifest = {
                "domain_version": "market-batch-v1",
                "rule_version": "analytics-v1-decimal34-half-even",
                "source_version": batch.source_version,
                "adjustment_version": batch.adjustment_version,
                "calendar_version": batch.calendar_version,
                "pool_version": batch.pool_version,
                "payload_hash": archive["payload_hash"],
            }
            row = (
                connection.execute(
                    insert(snapshots)
                    .values(
                        id=uuid4(),
                        batch_id=batch_id,
                        source_id=batch.source_id,
                        trade_date=batch.trade_date,
                        revision=previous["revision"] + 1 if previous else 1,
                        supersedes=previous["id"] if previous else None,
                        manifest=manifest,
                        published_at=now,
                    )
                    .returning(snapshots)
                )
                .mappings()
                .one()
            )
            self._audit(
                connection,
                "publish",
                batch.source_id,
                row["id"],
                now,
                {"batch_id": str(batch_id), "revision": row["revision"]},
            )
            return self._snapshot(row, archive)

    def get_snapshot(self, snapshot_id: UUID | str) -> Snapshot:
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(snapshots).where(snapshots.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Snapshot not found")
            current = self._source(connection, row["source_id"])
            archive = (
                connection.execute(
                    select(import_batches).where(import_batches.c.id == row["batch_id"])
                )
                .mappings()
                .one()
            )
            result = self._snapshot(row, archive)
            self._authorize(current, result.batch)
            return result

    def list_snapshots(
        self, *, trade_date: date | None = None, limit: int = 50, offset: int = 0
    ) -> list[Snapshot]:
        """Development-scale listing, paginated after current authorization.

        Source locks serialize reads with registry changes. Each candidate archive
        is loaded to check its embedded grant; expired history can require scanning
        more than one page. This is not a high-throughput public listing service.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError("offset must be from 0 to 10000")
        with self.database.engine.begin() as connection:
            source_ids = (
                connection.execute(
                    select(source_grants.c.source_id).order_by(
                        source_grants.c.source_id
                    )
                )
                .scalars()
                .all()
            )
            grants = {}
            for source_id in source_ids:
                current = self._source(connection, source_id)
                if grant_allows_local(current, source_id=source_id, now=self.clock()):
                    grants[source_id] = current
            if not grants:
                return []
            query = select(snapshots).where(snapshots.c.source_id.in_(grants))
            if trade_date is not None:
                query = query.where(snapshots.c.trade_date == trade_date)
            query = query.order_by(
                snapshots.c.trade_date.desc(),
                snapshots.c.source_id,
                snapshots.c.revision.desc(),
                snapshots.c.id,
            )
            results = []
            skipped = scanned = 0
            # Apply pagination after authorization, including embedded expiry.
            while len(results) < limit:
                rows = (
                    connection.execute(query.offset(scanned).limit(100))
                    .mappings()
                    .all()
                )
                if not rows:
                    break
                scanned += len(rows)
                for row in rows:
                    archive = (
                        connection.execute(
                            select(import_batches).where(
                                import_batches.c.id == row["batch_id"]
                            )
                        )
                        .mappings()
                        .one()
                    )
                    result = self._snapshot(row, archive)
                    try:
                        self._authorize(grants[row["source_id"]], result.batch)
                    except Forbidden:
                        continue
                    if skipped < offset:
                        skipped += 1
                    else:
                        results.append(result)
                        if len(results) == limit:
                            break
            return results
