"""Persistence for quality-gated immutable topic snapshots."""

import base64
import binascii
import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, or_, select

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
        request_from=row["request_from"],
        request_to=row["request_to"],
        retry_from=row["retry_from"],
        retry_to=row["retry_to"],
        details=tuple(row["details"]),
        created_at=row["created_at"],
    )


class _PageGap(NamedTuple):
    missing_item: str
    failed_cursor: str | None


def _page_gap(batch: TopicBatch) -> _PageGap | None:
    expected = None
    pages = batch.page_manifest.pages
    if not pages:
        return _PageGap("expected_cursor:<initial>", None)
    previous_page_id = None
    for index, page in enumerate(pages):
        if page.request_cursor != expected:
            if expected is None and previous_page_id is not None:
                missing_item = f"missing_after_page:{previous_page_id}"
            else:
                missing_item = "expected_cursor:" + (
                    expected if expected is not None else "<initial>"
                )
            return _PageGap(missing_item, page.request_cursor)
        if index > 0 and page.request_cursor is None:
            return _PageGap(f"missing_after_page:{previous_page_id}", None)
        if index < len(pages) - 1 and page.next_cursor is None:
            return _PageGap(
                f"missing_after_page:{page.page_id}",
                pages[index + 1].request_cursor,
            )
        expected = page.next_cursor
        previous_page_id = page.page_id
    if expected != batch.page_manifest.terminal_cursor:
        return _PageGap(
            "expected_cursor:" + (expected if expected is not None else "<terminal>"),
            batch.page_manifest.terminal_cursor,
        )
    return None


TOPIC_SNAPSHOT_CURSOR_TTL = timedelta(minutes=15)
_TOPIC_PUBLICATION_LOCK = "topic-snapshot-publication"
_TOPIC_SNAPSHOT_BATCH_MIN_SIZE = 100
_CURSOR_FIELDS = {
    "version",
    "sort_version",
    "issued_at",
    "expires_at",
    "watermark",
    "filters",
    "last",
}
_CURSOR_FILTER_FIELDS = {"trade_date", "source_id"}


def _base64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _base64_decode(value: str) -> bytes:
    if not value:
        raise ValueError
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def _encode_cursor(payload: dict[str, Any], signing_key: bytes) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.digest(signing_key, raw, "sha256")
    return f"{_base64_encode(raw)}.{_base64_encode(signature)}"


def _decode_cursor(value: str, signing_key: bytes) -> dict[str, Any]:
    try:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError
        encoded_payload, encoded_signature = value.split(".")
        raw = _base64_decode(encoded_payload)
        signature = _base64_decode(encoded_signature)
        if len(signature) != hashlib.sha256().digest_size or not hmac.compare_digest(
            signature,
            hmac.digest(signing_key, raw, "sha256"),
        ):
            raise ValueError
        payload = json.loads(raw.decode())
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


def _canonical_utc_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or value != parsed.isoformat()
    ):
        raise ValueError
    return parsed


class TopicRepository(Repository):
    """Topic archive using the market repository's source lock and grants."""

    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] | None = None
    ):
        super().__init__(database, clock=clock)
        secret = os.environ.get("DRAGON_TOPIC_CURSOR_SECRET")
        if secret is None:
            secret = database.engine.url.password
        if not secret:
            raise ValueError(
                "DRAGON_TOPIC_CURSOR_SECRET is required when "
                "DRAGON_DATABASE_URL has no password"
            )
        database_identity = (
            database.engine.url.set(password=None)
            .render_as_string(hide_password=False)
            .encode()
        )
        self._cursor_signing_key = hashlib.sha256(
            b"dragon-radar:topic-snapshot-cursor:v1\0"
            + secret.encode()
            + b"\0"
            + database_identity
        ).digest()

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
                    page_gap = _page_gap(batch)
                    missing_page = (
                        page_gap.missing_item if page_gap is not None else None
                    )
                    failed_cursor = (
                        page_gap.failed_cursor if page_gap is not None else None
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
                                if issue.code == "page_manifest"
                                and missing_page is not None
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
                            request_from=batch.page_manifest.request_from,
                            request_to=batch.page_manifest.request_to,
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
                            request_from=batch.page_manifest.request_from,
                            request_to=batch.page_manifest.request_to,
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
    def _snapshot(row: Mapping[str, Any], archive: Mapping[str, Any]) -> TopicSnapshot:
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
            batch = TopicBatch.model_validate_json(archive["canonical_json"])
            self._lock(connection, _TOPIC_PUBLICATION_LOCK)
            current = self._source(connection, archive["source_id"])
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
        operation_utc = operation_now.astimezone(UTC)
        cursor_data = None
        scan_key = None
        if cursor is not None:
            cursor_data = _decode_cursor(cursor, self._cursor_signing_key)
            expected_filters = {
                "trade_date": trade_date.isoformat() if trade_date else None,
                "source_id": source_id,
            }
            try:
                if (
                    set(cursor_data) != _CURSOR_FIELDS
                    or type(cursor_data["version"]) is not int
                    or cursor_data["version"] != 1
                    or type(cursor_data["sort_version"]) is not int
                    or cursor_data["sort_version"] != 1
                ):
                    raise ValueError
                issued_at = _canonical_utc_datetime(cursor_data["issued_at"])
                expires_at = _canonical_utc_datetime(cursor_data["expires_at"])
                if (
                    expires_at != issued_at + TOPIC_SNAPSHOT_CURSOR_TTL
                    or issued_at > operation_utc
                    or operation_utc >= expires_at
                ):
                    raise ValueError
                filters = cursor_data["filters"]
                if (
                    not isinstance(filters, dict)
                    or set(filters) != _CURSOR_FILTER_FIELDS
                    or filters != expected_filters
                ):
                    raise ValueError
                watermark = cursor_data["watermark"]
                if type(watermark) is not int or watermark < 0:
                    raise ValueError
                last = cursor_data["last"]
                if (
                    not isinstance(last, list)
                    or len(last) != 4
                    or not isinstance(last[0], str)
                    or not isinstance(last[1], str)
                    or type(last[2]) is not int
                    or last[2] <= 0
                    or not isinstance(last[3], str)
                ):
                    raise ValueError
                last_date = date.fromisoformat(last[0])
                last_id = UUID(last[3])
                if last[0] != last_date.isoformat() or last[3] != str(last_id):
                    raise ValueError
                _identifier(last[1])
                scan_key = (last_date, last[1], last[2], last_id)
            except (KeyError, TypeError, ValueError):
                raise ValueError("Invalid topic snapshot cursor") from None
        else:
            issued_at = operation_utc
            expires_at = issued_at + TOPIC_SNAPSHOT_CURSOR_TTL
            watermark = None

        with self.database.engine.begin() as connection:
            if watermark is None:
                self._lock(connection, _TOPIC_PUBLICATION_LOCK)
                watermark = connection.scalar(
                    select(func.max(topic_snapshots.c.publication_sequence))
                )
                if watermark is None:
                    return TopicSnapshotPage(
                        items=(), page_size=page_size, next_cursor=None
                    )

            source_query = (
                select(topic_snapshots.c.source_id)
                .where(topic_snapshots.c.publication_sequence <= watermark)
                .distinct()
            )
            if source_id is not None:
                source_query = source_query.where(
                    topic_snapshots.c.source_id == source_id
                )
            if trade_date is not None:
                source_query = source_query.where(
                    topic_snapshots.c.trade_date == trade_date
                )
            source_ids = sorted(connection.execute(source_query).scalars().all())
            grants: dict[str, SourceGrant] = {}
            for candidate in source_ids:
                try:
                    current = self._source(connection, candidate)
                except Forbidden:
                    continue
                if grant_allows_local(current, source_id=candidate, now=operation_now):
                    grants[candidate] = current
            if not grants:
                return TopicSnapshotPage(
                    items=(), page_size=page_size, next_cursor=None
                )

            query = (
                select(
                    topic_snapshots,
                    topic_import_batches.c.quality_report.label(
                        "archive_quality_report"
                    ),
                    topic_import_batches.c.canonical_json.label(
                        "archive_canonical_json"
                    ),
                )
                .join(
                    topic_import_batches,
                    topic_import_batches.c.id == topic_snapshots.c.batch_id,
                )
                .where(
                    topic_snapshots.c.source_id.in_(grants),
                    topic_snapshots.c.publication_sequence <= watermark,
                )
            )
            if trade_date is not None:
                query = query.where(topic_snapshots.c.trade_date == trade_date)
            query = query.order_by(
                topic_snapshots.c.trade_date.desc(),
                topic_snapshots.c.source_id,
                topic_snapshots.c.revision.desc(),
                topic_snapshots.c.id,
            )
            batch_size = max(page_size * 2, _TOPIC_SNAPSHOT_BATCH_MIN_SIZE)
            visible: list[tuple[TopicSnapshot, Mapping[str, Any]]] = []
            while len(visible) <= page_size:
                batch_query = query
                if scan_key is not None:
                    last_date, last_source, last_revision, last_id = scan_key
                    batch_query = batch_query.where(
                        or_(
                            topic_snapshots.c.trade_date < last_date,
                            and_(
                                topic_snapshots.c.trade_date == last_date,
                                topic_snapshots.c.source_id > last_source,
                            ),
                            and_(
                                topic_snapshots.c.trade_date == last_date,
                                topic_snapshots.c.source_id == last_source,
                                topic_snapshots.c.revision < last_revision,
                            ),
                            and_(
                                topic_snapshots.c.trade_date == last_date,
                                topic_snapshots.c.source_id == last_source,
                                topic_snapshots.c.revision == last_revision,
                                topic_snapshots.c.id > last_id,
                            ),
                        )
                    )
                rows = (
                    connection.execute(batch_query.limit(batch_size)).mappings().all()
                )
                if not rows:
                    break
                for row in rows:
                    scan_key = (
                        row["trade_date"],
                        row["source_id"],
                        row["revision"],
                        row["id"],
                    )
                    archive = {
                        "quality_report": row["archive_quality_report"],
                        "canonical_json": row["archive_canonical_json"],
                    }
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
                if len(visible) > page_size or len(rows) < batch_size:
                    break

        items = tuple(item for item, _ in visible[:page_size])
        next_cursor = None
        if len(visible) > page_size:
            last_row = visible[page_size - 1][1]
            next_cursor = _encode_cursor(
                {
                    "version": 1,
                    "sort_version": 1,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                    "watermark": watermark,
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
                },
                self._cursor_signing_key,
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
            if not self._authorized(current, embedded, row["source_id"], self.clock()):
                raise Forbidden("Source is not locally authorized")
            return _attempt(row)
