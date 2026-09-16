"""Persistence for quality-gated immutable topic snapshots."""

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import grant_allows_local
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
    _identifier,
    _uuid,
)
from dragon_radar.topics.models import (
    TopicAttempt,
    TopicBatch,
    TopicImportRecord,
    TopicSnapshot,
    TopicSnapshotPage,
)
from dragon_radar.topics.quality import validate_topic_batch
from dragon_radar.topics.tables import (
    topic_import_attempts,
    topic_import_batches,
    topic_snapshots,
)


def _quality_report(report) -> dict[str, Any]:
    return {
        "issues": [issue.model_dump(mode="json") for issue in report.issues],
        "coverage": report.coverage.model_dump(mode="json"),
        "publishable": report.publishable,
    }


def _import_record(row: Mapping[str, Any]) -> TopicImportRecord:
    return TopicImportRecord(
        id=row["id"],
        canonical_json=row["canonical_json"],
        payload_hash=row["payload_hash"],
        quality_report=row["quality_report"],
    )


def _attempt(row: Mapping[str, Any]) -> TopicAttempt:
    return TopicAttempt(
        id=row["id"],
        status=row["status"],
        stage=row["stage"],
        missing_items=tuple(row["missing_items"]),
        failed_cursor=row["failed_cursor"],
        retry_from=row["retry_from"],
        retry_to=row["retry_to"],
        details=tuple(row["details"]),
        created_at=row["created_at"],
    )


def _page_gap(batch: TopicBatch) -> tuple[bool, str | None]:
    expected = None
    for page in batch.page_manifest.pages:
        if page.request_cursor != expected:
            return True, expected
        expected = page.next_cursor
    if expected != batch.page_manifest.terminal_cursor:
        return True, expected
    return False, None


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (
        binascii.Error,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise ValueError("Invalid topic snapshot cursor") from None


class TopicRepository(Repository):
    """Topic archive using the market repository's source lock and grants."""

    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] | None = None
    ):
        super().__init__(database, clock=clock)

    @staticmethod
    def _authorized(
        current: SourceGrant, embedded: SourceGrant, source_id: str, now: datetime
    ) -> bool:
        return all(
            grant_allows_local(grant, source_id=source_id, now=now)
            for grant in (current, embedded)
        )

    def import_batch(self, batch: TopicBatch, *, key: str) -> TopicImportRecord:
        _identifier(key)
        batch = TopicBatch.model_validate(batch.model_dump())
        _identifier(batch.source_id)
        canonical = json.dumps(
            batch.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        rejected = False
        with self.database.engine.begin() as connection:
            self._lock(connection, "topic-import-key:" + key)
            current = self._source(connection, batch.source_id)
            now = self._authorize(current, batch)
            existing = (
                connection.execute(
                    select(topic_import_batches).where(
                        topic_import_batches.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["payload_hash"] != digest:
                    raise Conflict("Idempotency key already belongs to another payload")
                return _import_record(existing)

            failed_attempt = (
                connection.execute(
                    select(topic_import_attempts).where(
                        topic_import_attempts.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if failed_attempt:
                if failed_attempt["payload_hash"] != digest:
                    raise Conflict("Idempotency key already belongs to another payload")
                rejected = True
            else:
                report = validate_topic_batch(batch, now=now)
                quality = _quality_report(report)
                rejected = not report.publishable
                if rejected:
                    has_page_gap, failed_cursor = _page_gap(batch)
                    missing_page = (
                        "expected_cursor:"
                        + (failed_cursor if failed_cursor is not None else "<initial>")
                    )
                    connection.execute(
                        insert(topic_import_attempts).values(
                            id=uuid4(),
                            batch_id=None,
                            idempotency_key=key,
                            payload_hash=digest,
                            source_id=batch.source_id,
                            embedded_grant=batch.grant.model_dump(mode="json"),
                            status="failed",
                            stage="quality",
                            missing_items=[
                                missing_page
                                if issue.code == "page_manifest" and has_page_gap
                                else issue.message
                                for issue in report.issues
                                if issue.code
                                in {
                                    "missing_coverage",
                                    "invalid_coverage",
                                    "page_manifest",
                                }
                            ],
                            failed_cursor=failed_cursor,
                            retry_from=batch.coverage_from,
                            retry_to=batch.coverage_to,
                            details=[
                                f"{issue.code}: {issue.message}"
                                for issue in report.issues
                            ],
                            created_at=now,
                        )
                    )
                else:
                    row = (
                        connection.execute(
                            insert(topic_import_batches)
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
                            .returning(topic_import_batches)
                        )
                        .mappings()
                        .one()
                    )
                    connection.execute(
                        insert(topic_import_attempts).values(
                            id=uuid4(),
                            batch_id=row["id"],
                            idempotency_key=key,
                            payload_hash=digest,
                            source_id=batch.source_id,
                            embedded_grant=batch.grant.model_dump(mode="json"),
                            status="succeeded",
                            stage="import",
                            missing_items=[],
                            failed_cursor=None,
                            retry_from=None,
                            retry_to=None,
                            details=[],
                            created_at=now,
                        )
                    )
                    result = _import_record(row)
        if rejected:
            raise QualityRejected("Topic batch failed the quality gate")
        return result

    @staticmethod
    def _snapshot(
        row: Mapping[str, Any], archive: Mapping[str, Any]
    ) -> TopicSnapshot:
        return TopicSnapshot(
            id=row["id"],
            batch_id=row["batch_id"],
            revision=row["revision"],
            supersedes=row["supersedes"],
            published_at=row["published_at"],
            manifest=row["manifest"],
            quality_report=archive["quality_report"],
            batch=TopicBatch.model_validate_json(archive["canonical_json"]),
        )

    def publish(self, batch_id: UUID | str) -> TopicSnapshot:
        batch_id = _uuid(batch_id)
        with self.database.engine.begin() as connection:
            archive = (
                connection.execute(
                    select(topic_import_batches).where(
                        topic_import_batches.c.id == batch_id
                    )
                )
                .mappings()
                .first()
            )
            if archive is None:
                raise NotFound("Topic batch not found")
            current = self._source(connection, archive["source_id"])
            batch = TopicBatch.model_validate_json(archive["canonical_json"])
            now = self._authorize(current, batch)
            self._lock(
                connection,
                f"topic-snapshot:{batch.source_id}:{batch.trade_date.isoformat()}",
            )
            if (
                not archive["quality_report"]["publishable"]
                or not validate_topic_batch(batch, now=now).publishable
            ):
                raise QualityRejected("Topic batch failed the quality gate")
            existing = (
                connection.execute(
                    select(topic_snapshots).where(
                        topic_snapshots.c.batch_id == batch_id
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                return self._snapshot(existing, archive)
            previous = (
                connection.execute(
                    select(topic_snapshots)
                    .where(
                        topic_snapshots.c.source_id == batch.source_id,
                        topic_snapshots.c.trade_date == batch.trade_date,
                    )
                    .order_by(topic_snapshots.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            manifest = {
                "domain_version": "topic-batch-v1",
                "source_version": batch.source_version,
                "calendar_version": batch.calendar_version,
                "taxonomy_version": batch.taxonomy_version,
                "membership_version": batch.membership_version,
                "payload_hash": archive["payload_hash"],
            }
            row = (
                connection.execute(
                    insert(topic_snapshots)
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
                    .returning(topic_snapshots)
                )
                .mappings()
                .one()
            )
            return self._snapshot(row, archive)

    def get_snapshot(self, snapshot_id: UUID | str) -> TopicSnapshot:
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(topic_snapshots).where(topic_snapshots.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Topic snapshot not found")
            current = self._source(connection, row["source_id"])
            archive = (
                connection.execute(
                    select(topic_import_batches).where(
                        topic_import_batches.c.id == row["batch_id"]
                    )
                )
                .mappings()
                .one()
            )
            result = self._snapshot(row, archive)
            self._authorize(current, result.batch)
            return result

    @staticmethod
    def _sort_key(row: Mapping[str, Any]) -> tuple[int, str, int, str]:
        return (
            -row["trade_date"].toordinal(),
            row["source_id"],
            -row["revision"],
            str(row["id"]),
        )

    def list_snapshots(
        self,
        *,
        trade_date: date | None = None,
        source_id: str | None = None,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> TopicSnapshotPage:
        if type(page_size) is not int or page_size not in (20, 50, 100):
            raise ValueError("page_size must be 20, 50, or 100")
        if source_id is not None:
            _identifier(source_id)
        operation_now = self.clock()
        cursor_data = None
        if cursor is not None:
            cursor_data = _decode_cursor(cursor)
            expected_filters = {
                "trade_date": trade_date.isoformat() if trade_date else None,
                "source_id": source_id,
            }
            if (
                cursor_data.get("version") != 1
                or cursor_data.get("filters") != expected_filters
                or not isinstance(cursor_data.get("last"), list)
            ):
                raise ValueError("Invalid topic snapshot cursor")
            try:
                watermark = datetime.fromisoformat(cursor_data["watermark"])
                transaction_snapshot = cursor_data["transaction_snapshot"]
                if not isinstance(transaction_snapshot, str):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ValueError("Invalid topic snapshot cursor") from None
        else:
            watermark = operation_now
            transaction_snapshot = None

        with self.database.engine.begin() as connection:
            if transaction_snapshot is None:
                transaction_snapshot = connection.scalar(
                    text("SELECT txid_current_snapshot()::text")
                )
            source_query = select(topic_snapshots.c.source_id).distinct()
            if source_id is not None:
                source_query = source_query.where(
                    topic_snapshots.c.source_id == source_id
                )
            source_ids = sorted(connection.execute(source_query).scalars().all())
            grants: dict[str, SourceGrant] = {}
            for candidate in source_ids:
                try:
                    current = self._source(connection, candidate)
                except Forbidden:
                    continue
                if grant_allows_local(
                    current, source_id=candidate, now=operation_now
                ):
                    grants[candidate] = current
            if not grants:
                return TopicSnapshotPage(
                    items=(), page_size=page_size, next_cursor=None
                )

            query = select(topic_snapshots).where(
                topic_snapshots.c.source_id.in_(grants),
                topic_snapshots.c.published_at <= watermark,
                text(
                    "txid_visible_in_snapshot("
                    "topic_snapshots.xmin::text::bigint, "
                    "CAST(:transaction_snapshot AS txid_snapshot))"
                ),
            )
            if trade_date is not None:
                query = query.where(topic_snapshots.c.trade_date == trade_date)
            rows = (
                connection.execute(
                    query.order_by(
                        topic_snapshots.c.trade_date.desc(),
                        topic_snapshots.c.source_id,
                        topic_snapshots.c.revision.desc(),
                        topic_snapshots.c.id,
                    ),
                    {"transaction_snapshot": transaction_snapshot},
                )
                .mappings()
                .all()
            )
            last_key = None
            if cursor_data is not None:
                try:
                    last = cursor_data["last"]
                    last_key = (
                        -date.fromisoformat(last[0]).toordinal(),
                        str(last[1]),
                        -int(last[2]),
                        str(UUID(last[3])),
                    )
                except (IndexError, TypeError, ValueError):
                    raise ValueError("Invalid topic snapshot cursor") from None

            visible: list[tuple[TopicSnapshot, Mapping[str, Any]]] = []
            for row in rows:
                if last_key is not None and self._sort_key(row) <= last_key:
                    continue
                archive = (
                    connection.execute(
                        select(topic_import_batches).where(
                            topic_import_batches.c.id == row["batch_id"]
                        )
                    )
                    .mappings()
                    .one()
                )
                result = self._snapshot(row, archive)
                if not self._authorized(
                    grants[row["source_id"]],
                    result.batch.grant,
                    row["source_id"],
                    operation_now,
                ):
                    continue
                visible.append((result, row))
                if len(visible) > page_size:
                    break

        items = tuple(item for item, _ in visible[:page_size])
        next_cursor = None
        if len(visible) > page_size:
            last_row = visible[page_size - 1][1]
            next_cursor = _encode_cursor(
                {
                    "version": 1,
                    "watermark": watermark.isoformat(),
                    "transaction_snapshot": transaction_snapshot,
                    "filters": {
                        "trade_date": trade_date.isoformat() if trade_date else None,
                        "source_id": source_id,
                    },
                    "last": [
                        last_row["trade_date"].isoformat(),
                        last_row["source_id"],
                        last_row["revision"],
                        str(last_row["id"]),
                    ],
                }
            )
        return TopicSnapshotPage(
            items=items, page_size=page_size, next_cursor=next_cursor
        )

    def list_attempts(self, *, status: str | None = None) -> tuple[TopicAttempt, ...]:
        if status not in (None, "failed", "succeeded"):
            raise ValueError("Invalid topic attempt status")
        with self.database.engine.begin() as connection:
            query = select(topic_import_attempts)
            if status is not None:
                query = query.where(topic_import_attempts.c.status == status)
            rows = (
                connection.execute(
                    query.order_by(
                        topic_import_attempts.c.created_at.desc(),
                        topic_import_attempts.c.id,
                    )
                )
                .mappings()
                .all()
            )
            source_ids = sorted({row["source_id"] for row in rows})
            now = self.clock()
            grants: dict[str, SourceGrant] = {}
            for source_id in source_ids:
                current = self._source(connection, source_id)
                if grant_allows_local(current, source_id=source_id, now=now):
                    grants[source_id] = current
            return tuple(
                _attempt(row)
                for row in rows
                if row["source_id"] in grants
                and self._authorized(
                    grants[row["source_id"]],
                    SourceGrant.model_validate(row["embedded_grant"]),
                    row["source_id"],
                    now,
                )
            )

    def get_attempt(self, attempt_id: UUID | str) -> TopicAttempt:
        attempt_id = _uuid(attempt_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(topic_import_attempts).where(
                        topic_import_attempts.c.id == attempt_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Topic import attempt not found")
            current = self._source(connection, row["source_id"])
            embedded = SourceGrant.model_validate(row["embedded_grant"])
            if not self._authorized(
                current, embedded, row["source_id"], self.clock()
            ):
                raise Forbidden("Source is not locally authorized")
            return _attempt(row)
