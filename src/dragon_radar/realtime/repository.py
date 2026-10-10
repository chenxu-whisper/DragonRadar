"""Transactional ingestion and point-in-time reads for realtime evidence."""

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from datetime import date as Date
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, and_, cast, func, insert, or_, select
from sqlalchemy.exc import IntegrityError

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import grant_allows_display, grant_allows_storage
from dragon_radar.realtime.anomalies import detect_anomalies
from dragon_radar.realtime.models import (
    AnomalyEvent,
    AnomalyReadResult,
    AnomalyResult,
    AnomalyRule,
    MinuteBarInput,
    MinuteBarRecord,
    MinuteBatchInput,
    MinuteReadResult,
    PermissionSnapshot,
    QuoteBatchInput,
    QuoteInput,
    QuoteReadResult,
    QuoteRecord,
    RealtimeImportResult,
    SectorFlowBatchInput,
    SectorFlowPoint,
    SectorFlowReadResult,
    SectorFlowRecord,
    SourceCompleteness,
)
from dragon_radar.realtime.tables import (
    realtime_anomaly_events,
    realtime_import_attempts,
    realtime_minute_bars,
    realtime_quote_batches,
    realtime_quotes,
    realtime_sector_flow_points,
)
from dragon_radar.storage.database import Database
from dragon_radar.storage.models import source_grants
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    Repository,
    _identifier,
)
from dragon_radar.topics.repository import _decode_cursor, _encode_cursor

ANOMALY_CURSOR_TTL = timedelta(minutes=15)
REALTIME_CURSOR_ORDER_LOCK = "dragon-radar:realtime:cursor-order:v1"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CURSOR_FIELDS = {
    "version",
    "sort_version",
    "issued_at",
    "expires_at",
    "watermark",
    "filters",
    "last",
}
_CURSOR_FILTER_FIELDS = {"date", "as_of", "source_id", "security_id"}


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != value
    ):
        raise ValueError
    return parsed


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Canonical decimals must be finite")
        if value == 0:
            return {"__decimal__": [0, "0", 0]}
        sign, digits, exponent = value.as_tuple()
        end = len(digits)
        while end > 1 and digits[end - 1] == 0:
            end -= 1
            exponent += 1
        coefficient = "".join(str(digit) for digit in digits[:end])
        return {"__decimal__": [sign, coefficient, exponent]}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_payload(model: Any) -> dict[str, Any]:
    return model.model_dump()


def _sorted_relation_payload(
    batch: Any,
    field: str,
    identity: Callable[[Any], tuple[Any, ...]],
) -> tuple[dict[str, Any], bool]:
    payload = _model_payload(batch)
    items = tuple(getattr(batch, field))
    identities = tuple(identity(item) for item in items)
    payload[field] = [_model_payload(item) for item in sorted(items, key=identity)]
    return payload, len(identities) != len(set(identities))


class _RejectedImport(Exception):
    def __init__(
        self,
        primary: Exception,
        *,
        reason: str,
        details: Mapping[str, Any],
    ):
        super().__init__(reason)
        self.primary = primary
        self.reason = reason
        self.details = details


def _quote(row: Mapping[str, Any]) -> QuoteInput:
    return QuoteInput(
        security_id=row["security_id"],
        last=row["last"],
        bid=row["bid"],
        ask=row["ask"],
        bid_size=row["bid_size"],
        ask_size=row["ask_size"],
        volume=row["volume"],
        amount=row["amount"],
        order_book_status=row["order_book_status"],
        order_book_unavailable_reason=row["order_book_unavailable_reason"],
    )


def _minute(row: Mapping[str, Any]) -> MinuteBarInput:
    return MinuteBarInput(
        security_id=row["security_id"],
        event_at=row["event_at"],
        received_at=row["received_at"],
        available_at=row["available_at"],
        revision=row["revision"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        amount=row["amount"],
        final=row["final"],
    )


def _sector_point(row: Mapping[str, Any]) -> SectorFlowPoint:
    return SectorFlowPoint(
        sector_id=row["sector_id"],
        event_at=row["event_at"],
        net_inflow=row["net_inflow"],
        unit=row["unit"],
        unavailable_reason=row["unavailable_reason"],
    )


def _result(
    attempt: Mapping[str, Any], batch: Mapping[str, Any]
) -> RealtimeImportResult:
    return RealtimeImportResult(
        import_id=str(attempt["id"]),
        batch_id=batch["quote_batch_id"],
        status=attempt["status"],
        payload_hash=attempt["payload_hash"],
        imported_at=attempt["created_at"],
        reason=attempt["reason"],
    )


def _expected_minutes(session_start: datetime, session_end: datetime) -> int:
    seconds = (session_end - session_start).total_seconds()
    if seconds <= 0 or seconds % 60:
        raise ValueError("session interval must contain whole minutes")
    return int(seconds // 60)


def _trading_day_windows(day: Date) -> tuple[tuple[datetime, datetime], ...]:
    return tuple(
        (
            datetime.combine(day, start, tzinfo=_SHANGHAI).astimezone(UTC),
            datetime.combine(day, end, tzinfo=_SHANGHAI).astimezone(UTC),
        )
        for start, end in (
            (time(9, 30), time(11, 30)),
            (time(13), time(15)),
        )
    )


def _session_window_filter(column, windows):
    return or_(
        *(
            and_(column >= window_start, column < window_end)
            for window_start, window_end in windows
        )
    )


def _session_windows(
    session_start: datetime,
    session_end: datetime,
    windows: tuple[tuple[datetime, datetime], ...] | None,
) -> tuple[tuple[datetime, datetime], ...]:
    session_start = _utc(session_start, "session_start")
    session_end = _utc(session_end, "session_end")
    if windows is None:
        _expected_minutes(session_start, session_end)
        trading_day = _trading_day_windows(session_start.astimezone(_SHANGHAI).date())
        if session_start == trading_day[0][0] and session_end == trading_day[-1][1]:
            return trading_day
        return ((session_start, session_end),)
    if not isinstance(windows, tuple) or not windows:
        raise ValueError("session_windows must be a nonempty tuple")
    normalized = tuple(
        (
            _utc(window_start, "session window start"),
            _utc(window_end, "session window end"),
        )
        for window_start, window_end in windows
    )
    if (
        normalized[0][0] != session_start
        or normalized[-1][1] != session_end
        or any(
            current_start < previous_end
            for (_, previous_end), (current_start, _) in zip(
                normalized, normalized[1:], strict=False
            )
        )
    ):
        raise ValueError("session_windows must be ordered within the session")
    for window_start, window_end in normalized:
        _expected_minutes(window_start, window_end)
    return normalized


def _completeness(
    *,
    expected: int | None,
    received: int,
    unavailable_reason: str | None = None,
) -> SourceCompleteness:
    if unavailable_reason is not None:
        return SourceCompleteness(
            status="unavailable",
            expected_count=expected,
            received_count=received,
            reason=unavailable_reason,
        )
    if expected is None:
        raise ValueError("Available completeness requires an expected count")
    if received < expected:
        return SourceCompleteness(
            status="incomplete",
            expected_count=expected,
            received_count=received,
            reason="stream_gap",
        )
    return SourceCompleteness(
        status="complete",
        expected_count=expected,
        received_count=received,
    )


def _trading_minute_completeness(
    records: Sequence[tuple[str, str, datetime]],
    windows: tuple[tuple[datetime, datetime], ...],
) -> SourceCompleteness:
    identities = {(source_id, security_id) for source_id, security_id, _ in records}
    expected = len(identities) * sum(
        _expected_minutes(window_start, window_end)
        for window_start, window_end in windows
    )
    received = len(
        {
            (
                source_id,
                security_id,
                event_at.replace(second=0, microsecond=0),
            )
            for source_id, security_id, event_at in records
            if any(
                window_start <= event_at < window_end
                for window_start, window_end in windows
            )
        }
    )
    return _completeness(expected=expected, received=received)


def _validate_batch_selection(
    batch_id: str | None,
    source_id: str | None,
) -> tuple[str | None, str | None]:
    if batch_id is not None:
        batch_id = _identifier(batch_id)
        if source_id is None:
            raise ValueError("source_id is required with batch_id")
    if source_id is not None:
        source_id = _identifier(source_id)
    return batch_id, source_id


class RealtimeRepository(Repository):
    """Immutable realtime writes and permission-checked point-in-time reads."""

    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] | None = None
    ):
        super().__init__(database, clock=clock)
        secret = os.environ.get("DRAGON_REALTIME_CURSOR_SECRET")
        if secret is None:
            secret = database.engine.url.password
        if not secret:
            raise ValueError(
                "DRAGON_REALTIME_CURSOR_SECRET is required when "
                "DRAGON_DATABASE_URL has no password"
            )
        database_identity = (
            database.engine.url.set(password=None)
            .render_as_string(hide_password=False)
            .encode()
        )
        self._cursor_signing_key = hashlib.sha256(
            b"dragon-radar:realtime-anomaly-cursor:v1\0"
            + secret.encode()
            + b"\0"
            + database_identity
        ).digest()

    @staticmethod
    def _validate_type(value: object, expected_type: type, label: str):
        if not isinstance(value, expected_type):
            raise TypeError(f"{label} must be a {expected_type.__name__}")
        return expected_type.model_validate(value.model_dump())

    def _authorize_import(
        self,
        current: SourceGrant,
        source_id: str,
        permission: PermissionSnapshot,
        now: datetime,
    ) -> None:
        if (
            permission.state != "authorized"
            or permission.captured_at > now
            or not grant_allows_storage(current, source_id=source_id, now=now)
        ):
            raise Forbidden("Source is not authorized for realtime storage")

    def _authorize_reads(
        self,
        connection,
        batches: Sequence[Mapping[str, Any]],
        now: datetime,
        *,
        filter_unauthorized: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        source_ids = sorted({batch["source_id"] for batch in batches})
        if not source_ids:
            return ()
        current_rows = (
            connection.execute(
                select(source_grants.c.source_id, source_grants.c.grant)
                .where(source_grants.c.source_id.in_(source_ids))
                .order_by(source_grants.c.source_id)
                .with_for_update(read=True)
            )
            .mappings()
            .all()
        )
        current_by_source = {
            row["source_id"]: SourceGrant.model_validate(row["grant"])
            for row in current_rows
        }
        if not filter_unauthorized and current_by_source.keys() != set(source_ids):
            raise Forbidden("Source is not locally authorized")
        authorized = []
        for batch in batches:
            source_id = batch["source_id"]
            permission = PermissionSnapshot.model_validate_json(
                json.dumps(batch["permission_snapshot"])
            )
            current = current_by_source.get(source_id)
            if (
                current is None
                or permission.state != "authorized"
                or permission.captured_at > now
                or not grant_allows_display(
                    current,
                    source_id=source_id,
                    now=now,
                )
            ):
                if filter_unauthorized:
                    continue
                raise Forbidden("Source is not authorized for realtime display")
            authorized.append(batch)
        if batches and not authorized:
            raise Forbidden("Source is not authorized for realtime display")
        return tuple(authorized)

    @staticmethod
    def _batch_values(batch, digest: str, imported_at: datetime) -> dict[str, Any]:
        return {
            "id": uuid4(),
            "quote_batch_id": batch.batch_id,
            "source_id": batch.source_id,
            "source_version": batch.source_version,
            "source_event_at": batch.source_event_at,
            "received_at": batch.received_at,
            "available_at": batch.available_at,
            "source_sequence": getattr(batch, "source_sequence", None),
            "permission_snapshot": batch.permission.model_dump(mode="json"),
            "payload_hash": digest,
            "imported_at": imported_at,
        }

    @staticmethod
    def _existing_attempt(connection, batch_row):
        return (
            connection.execute(
                select(realtime_import_attempts)
                .where(
                    realtime_import_attempts.c.source_id == batch_row["source_id"],
                    realtime_import_attempts.c.batch_id == batch_row["quote_batch_id"],
                    realtime_import_attempts.c.status.in_(("accepted", "incomplete")),
                )
                .order_by(
                    (realtime_import_attempts.c.status == "accepted").desc(),
                    realtime_import_attempts.c.created_at,
                    realtime_import_attempts.c.id,
                )
            )
            .mappings()
            .first()
        )

    def _begin_ingest(
        self,
        connection,
        batch,
        *,
        key: str,
        digest: str,
        kind: str,
    ) -> tuple[
        datetime,
        Mapping[str, Any] | None,
        RealtimeImportResult | None,
        str | None,
    ]:
        _identifier(key)
        _identifier(batch.source_id)
        _identifier(batch.batch_id)
        current = self._source(connection, batch.source_id)
        self._lock(
            connection,
            f"realtime-batch:{batch.source_id}:{batch.batch_id}",
        )
        self._lock(connection, f"realtime-import-key:{batch.source_id}:{key}")
        now = _utc(self.clock(), "operation time")
        self._authorize_import(
            current,
            batch.source_id,
            batch.permission,
            now,
        )
        used_key = (
            connection.execute(
                select(realtime_import_attempts).where(
                    realtime_import_attempts.c.source_id == batch.source_id,
                    realtime_import_attempts.c.idempotency_key == key,
                    realtime_import_attempts.c.status.in_(("accepted", "incomplete")),
                )
            )
            .mappings()
            .first()
        )
        if used_key is not None:
            if (
                used_key["source_id"] != batch.source_id
                or used_key["batch_id"] != batch.batch_id
                or used_key["payload_hash"] != digest
            ):
                raise _RejectedImport(
                    Conflict("Realtime idempotency key belongs to another import"),
                    reason="idempotency_key_conflict",
                    details={"kind": kind},
                )
            original = (
                connection.execute(
                    select(realtime_quote_batches).where(
                        realtime_quote_batches.c.source_id == used_key["source_id"],
                        realtime_quote_batches.c.quote_batch_id == used_key["batch_id"],
                    )
                )
                .mappings()
                .first()
            )
            if original is None or original["payload_hash"] != used_key["payload_hash"]:
                raise _RejectedImport(
                    Conflict("Realtime idempotency key owner is invalid"),
                    reason="idempotency_key_owner_invalid",
                    details={"kind": kind},
                )
            duplicate = self._finish_ingest(
                connection,
                batch,
                key=key,
                digest=digest,
                imported_at=now,
                status="duplicate",
                reason=None,
                details={"original_import_id": str(used_key["id"])},
            )
            return now, original, duplicate, None
        existing = (
            connection.execute(
                select(realtime_quote_batches).where(
                    realtime_quote_batches.c.source_id == batch.source_id,
                    realtime_quote_batches.c.quote_batch_id == batch.batch_id,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise _RejectedImport(
                    Conflict("Realtime batch identity belongs to another payload"),
                    reason="batch_identity_conflict",
                    details={"kind": kind},
                )
            attempt = self._existing_attempt(connection, existing)
            if attempt is None:
                raise _RejectedImport(
                    Conflict("Realtime batch exists without an import attempt"),
                    reason="batch_attempt_missing",
                    details={"kind": kind},
                )
            duplicate = self._finish_ingest(
                connection,
                batch,
                key=key,
                digest=digest,
                imported_at=now,
                status="duplicate",
                reason=None,
                details={"original_import_id": str(attempt["id"])},
            )
            return now, existing, duplicate, None
        # New parents always lock source/batch, import key, then cursor order.
        self._lock(connection, REALTIME_CURSOR_ORDER_LOCK)
        return now, None, None, None

    @staticmethod
    def _finish_ingest(
        connection,
        batch,
        *,
        key: str,
        digest: str,
        imported_at: datetime,
        status: str,
        reason: str | None,
        details: Mapping[str, Any],
    ) -> RealtimeImportResult:
        attempt = (
            connection.execute(
                insert(realtime_import_attempts)
                .values(
                    id=uuid4(),
                    source_id=batch.source_id,
                    batch_id=batch.batch_id,
                    idempotency_key=key,
                    payload_hash=digest,
                    status=status,
                    reason=reason,
                    details=dict(details),
                    created_at=imported_at,
                )
                .returning(realtime_import_attempts)
            )
            .mappings()
            .one()
        )
        parent = {
            "quote_batch_id": batch.batch_id,
            "payload_hash": digest,
            "imported_at": imported_at,
        }
        return _result(attempt, parent)

    def _raise_rejected(
        self,
        batch,
        *,
        key: str,
        digest: str,
        primary: Exception,
        reason: str,
        details: Mapping[str, Any],
    ) -> None:
        try:
            with self.database.engine.begin() as connection:
                self._finish_ingest(
                    connection,
                    batch,
                    key=key,
                    digest=digest,
                    imported_at=_utc(self.clock(), "operation time"),
                    status="rejected",
                    reason=reason,
                    details=details,
                )
        except Exception as cleanup_error:
            primary.add_note(
                f"Rejected attempt persistence failed ({type(cleanup_error).__name__})"
            )
        raise primary from None

    def ingest_quote_batch(
        self,
        batch: QuoteBatchInput,
        *,
        key: str,
    ) -> RealtimeImportResult:
        batch = self._validate_type(batch, QuoteBatchInput, "batch")
        _identifier(key)
        payload, duplicate_identity = _sorted_relation_payload(
            batch,
            "quotes",
            lambda quote: (quote.security_id,),
        )
        digest = _canonical_hash({"kind": "quote", "batch": payload})
        if duplicate_identity:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=ValueError("duplicate quote identity"),
                reason="duplicate_quote_identity",
                details={"kind": "quote"},
            )
        try:
            with self.database.engine.begin() as connection:
                now, _existing, replay, conflict_message = self._begin_ingest(
                    connection,
                    batch,
                    key=key,
                    digest=digest,
                    kind="quote",
                )
                if replay is not None:
                    return replay
                if conflict_message is None:
                    connection.execute(
                        insert(realtime_quote_batches).values(
                            **self._batch_values(batch, digest, now)
                        )
                    )
                    if batch.quotes:
                        connection.execute(
                            insert(realtime_quotes),
                            [
                                {
                                    "id": uuid4(),
                                    "source_id": batch.source_id,
                                    "batch_id": batch.batch_id,
                                    **quote.model_dump(),
                                }
                                for quote in batch.quotes
                            ],
                        )
                    return self._finish_ingest(
                        connection,
                        batch,
                        key=key,
                        digest=digest,
                        imported_at=now,
                        status="accepted",
                        reason=None,
                        details={"kind": "quote", "row_count": len(batch.quotes)},
                    )
            if conflict_message is None:
                raise RuntimeError("realtime quote ingestion produced no result")
            raise Conflict(conflict_message)
        except _RejectedImport as rejected:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=rejected.primary,
                reason=rejected.reason,
                details=rejected.details,
            )
        except IntegrityError:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=Conflict("Conflicting realtime quote batch"),
                reason="integrity_error",
                details={"error_type": "IntegrityError", "kind": "quote"},
            )
        except Forbidden as error:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=error,
                reason="authorization_rejected",
                details={"kind": "quote"},
            )

    @staticmethod
    def _cooldown_bucket(event_at: datetime, seconds: int) -> datetime:
        if seconds == 0:
            return event_at
        epoch_seconds = int(event_at.timestamp())
        return datetime.fromtimestamp(
            epoch_seconds - epoch_seconds % seconds,
            tz=UTC,
        )

    @classmethod
    def _anomaly_rows(
        cls,
        batch: MinuteBatchInput,
        rules: Sequence[AnomalyRule],
        *,
        quality: str,
    ) -> tuple[dict[str, Any], ...]:
        grouped: dict[str, list[MinuteBarInput]] = defaultdict(list)
        for bar in batch.bars:
            grouped[bar.security_id].append(bar)
        rows = []
        for security_id in sorted(grouped):
            bars = tuple(grouped[security_id])
            input_identity = [
                {
                    "event_at": bar.event_at.isoformat(),
                    "revision": bar.revision,
                }
                for bar in sorted(
                    bars,
                    key=lambda item: (item.event_at, item.revision),
                )
            ]
            for rule in sorted(
                rules,
                key=lambda item: (
                    item.rule_version,
                    item.metric,
                    item.window,
                    str(item.threshold),
                    item.cooldown_seconds,
                ),
            ):
                result = detect_anomalies(bars=bars, rule=rule)
                if not result.triggered:
                    continue
                event_at = max(bar.event_at for bar in bars)
                available_at = max(bar.available_at for bar in bars)
                bucket = cls._cooldown_bucket(event_at, rule.cooldown_seconds)
                identity = {
                    "source_id": batch.source_id,
                    "batch_id": batch.batch_id,
                    "security_id": security_id,
                    "rule_version": rule.rule_version,
                    "metric": rule.metric,
                    "window": rule.window,
                    "cooldown_bucket": bucket.isoformat(),
                    "input": input_identity,
                }
                rows.append(
                    {
                        "event_id": _canonical_hash(identity),
                        "source_id": batch.source_id,
                        "batch_id": batch.batch_id,
                        "security_id": security_id,
                        "rule_version": rule.rule_version,
                        "metric": result.metric,
                        "window": result.window,
                        "value": result.value,
                        "threshold": result.threshold,
                        "event_at": event_at,
                        "available_at": available_at,
                        "quality": quality,
                        "cooldown_bucket": bucket,
                    }
                )
        return tuple(rows)

    def ingest_minute_batch(
        self,
        batch: MinuteBatchInput,
        *,
        key: str,
        rules: Sequence[AnomalyRule] = (),
    ) -> RealtimeImportResult:
        batch = self._validate_type(batch, MinuteBatchInput, "batch")
        _identifier(key)
        if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
            raise TypeError("rules must be a sequence of AnomalyRule")
        validated_rules = tuple(
            self._validate_type(rule, AnomalyRule, "rule") for rule in rules
        )
        batch_payload, duplicate_bar_identity = _sorted_relation_payload(
            batch,
            "bars",
            lambda bar: (bar.security_id, bar.event_at, bar.revision),
        )
        rule_identities = [
            (rule.rule_version, rule.metric, rule.window) for rule in validated_rules
        ]
        ordered_rules = tuple(
            sorted(
                validated_rules,
                key=lambda rule: (
                    rule.rule_version,
                    rule.metric,
                    rule.window,
                ),
            )
        )
        windows = _session_windows(
            batch.session_start,
            batch.session_end,
            None,
        )
        completeness = _trading_minute_completeness(
            tuple(
                (batch.source_id, bar.security_id, bar.event_at) for bar in batch.bars
            ),
            windows,
        )
        incomplete = completeness.status == "incomplete"
        anomaly_rows = self._anomaly_rows(
            batch,
            validated_rules,
            quality="incomplete" if incomplete else "complete",
        )
        digest = _canonical_hash(
            {
                "kind": "minute",
                "batch": batch_payload,
                "rules": [_model_payload(rule) for rule in ordered_rules],
                "anomalies": anomaly_rows,
            }
        )
        if duplicate_bar_identity:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=ValueError("duplicate minute identity"),
                reason="duplicate_minute_identity",
                details={"kind": "minute"},
            )
        if len(rule_identities) != len(set(rule_identities)):
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=ValueError("duplicate anomaly rule identity"),
                reason="duplicate_rule_identity",
                details={"kind": "minute"},
            )
        try:
            with self.database.engine.begin() as connection:
                now, _existing, replay, conflict_message = self._begin_ingest(
                    connection,
                    batch,
                    key=key,
                    digest=digest,
                    kind="minute",
                )
                if replay is not None:
                    return replay
                if conflict_message is None:
                    connection.execute(
                        insert(realtime_quote_batches).values(
                            **self._batch_values(batch, digest, now)
                        )
                    )
                    if batch.bars:
                        connection.execute(
                            insert(realtime_minute_bars),
                            [
                                {
                                    "id": uuid4(),
                                    "source_id": batch.source_id,
                                    "batch_id": batch.batch_id,
                                    "source_version": batch.source_version,
                                    **bar.model_dump(),
                                    "source_event_at": batch.source_event_at,
                                }
                                for bar in batch.bars
                            ],
                        )
                    if anomaly_rows:
                        connection.execute(
                            insert(realtime_anomaly_events), anomaly_rows
                        )
                    return self._finish_ingest(
                        connection,
                        batch,
                        key=key,
                        digest=digest,
                        imported_at=now,
                        status="incomplete" if incomplete else "accepted",
                        reason="stream_gap" if incomplete else None,
                        details={
                            "kind": "minute",
                            "expected_count": completeness.expected_count,
                            "received_count": completeness.received_count,
                            "event_count": len(anomaly_rows),
                        },
                    )
            if conflict_message is None:
                raise RuntimeError("realtime minute ingestion produced no result")
            raise Conflict(conflict_message)
        except _RejectedImport as rejected:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=rejected.primary,
                reason=rejected.reason,
                details=rejected.details,
            )
        except IntegrityError:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=Conflict("Conflicting realtime minute batch or revision"),
                reason="integrity_error",
                details={"error_type": "IntegrityError", "kind": "minute"},
            )
        except Forbidden as error:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=error,
                reason="authorization_rejected",
                details={"kind": "minute"},
            )

    def ingest_sector_flow_batch(
        self,
        batch: SectorFlowBatchInput,
        *,
        key: str,
    ) -> RealtimeImportResult:
        batch = self._validate_type(batch, SectorFlowBatchInput, "batch")
        _identifier(key)
        payload, duplicate_identity = _sorted_relation_payload(
            batch,
            "points",
            lambda point: (point.sector_id, point.event_at),
        )
        digest = _canonical_hash({"kind": "sector_flow", "batch": payload})
        if duplicate_identity:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=ValueError("duplicate sector flow identity"),
                reason="duplicate_sector_flow_identity",
                details={"kind": "sector_flow"},
            )
        try:
            with self.database.engine.begin() as connection:
                now, _existing, replay, conflict_message = self._begin_ingest(
                    connection,
                    batch,
                    key=key,
                    digest=digest,
                    kind="sector_flow",
                )
                if replay is not None:
                    return replay
                if conflict_message is None:
                    connection.execute(
                        insert(realtime_quote_batches).values(
                            **self._batch_values(batch, digest, now)
                        )
                    )
                    if batch.points:
                        connection.execute(
                            insert(realtime_sector_flow_points),
                            [
                                {
                                    "id": uuid4(),
                                    "source_id": batch.source_id,
                                    "batch_id": batch.batch_id,
                                    **point.model_dump(),
                                }
                                for point in batch.points
                            ],
                        )
                    return self._finish_ingest(
                        connection,
                        batch,
                        key=key,
                        digest=digest,
                        imported_at=now,
                        status="accepted",
                        reason=None,
                        details={
                            "kind": "sector_flow",
                            "row_count": len(batch.points),
                        },
                    )
            if conflict_message is None:
                raise RuntimeError("realtime sector flow ingestion produced no result")
            raise Conflict(conflict_message)
        except _RejectedImport as rejected:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=rejected.primary,
                reason=rejected.reason,
                details=rejected.details,
            )
        except IntegrityError:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=Conflict("Conflicting realtime sector flow batch"),
                reason="integrity_error",
                details={"error_type": "IntegrityError", "kind": "sector_flow"},
            )
        except Forbidden as error:
            self._raise_rejected(
                batch,
                key=key,
                digest=digest,
                primary=error,
                reason="authorization_rejected",
                details={"kind": "sector_flow"},
            )

    def _batch_candidates(
        self,
        connection,
        *,
        as_of: datetime,
        operation_now: datetime,
        batch_id: str | None,
        source_id: str | None,
        child_table=None,
        child_filter=(),
    ):
        query = select(realtime_quote_batches)
        if batch_id is None and source_id is None:
            grant = source_grants.c.grant
            permission = realtime_quote_batches.c.permission_snapshot
            query = query.join(
                source_grants,
                source_grants.c.source_id == realtime_quote_batches.c.source_id,
            ).where(
                grant["source_id"].as_string() == source_grants.c.source_id,
                grant["approved"].as_boolean().is_(True),
                grant["reference"].as_string().is_not(None),
                cast(grant["starts_at"].as_string(), DateTime(timezone=True))
                <= operation_now,
                cast(grant["expires_at"].as_string(), DateTime(timezone=True))
                > operation_now,
                grant["local_display"].as_boolean().is_(True),
                permission["state"].as_string() == "authorized",
                cast(
                    permission["captured_at"].as_string(),
                    DateTime(timezone=True),
                )
                <= operation_now,
            )
        if child_table is not None:
            query = query.where(
                select(1)
                .select_from(child_table)
                .where(
                    child_table.c.source_id == realtime_quote_batches.c.source_id,
                    child_table.c.batch_id == realtime_quote_batches.c.quote_batch_id,
                    *child_filter,
                )
                .offset(0)
                .exists()
            )
        query = query.where(realtime_quote_batches.c.available_at <= as_of)
        if batch_id is not None:
            query = query.where(realtime_quote_batches.c.quote_batch_id == batch_id)
        if source_id is not None:
            query = query.where(realtime_quote_batches.c.source_id == source_id)
        if batch_id is None and source_id is None:
            query = query.with_for_update(of=source_grants, read=True)
        return (
            connection.execute(
                query.order_by(
                    realtime_quote_batches.c.available_at.desc(),
                    realtime_quote_batches.c.ingest_order.desc(),
                    realtime_quote_batches.c.id.desc(),
                ).limit(1)
            )
            .mappings()
            .first()
        )

    def _select_authorized_batch(
        self,
        connection,
        *,
        as_of: datetime,
        batch_id: str | None,
        source_id: str | None,
        child_table=None,
        child_filter=(),
    ):
        operation_now = _utc(self.clock(), "operation time")
        candidates = self._batch_candidates(
            connection,
            as_of=as_of,
            operation_now=operation_now,
            batch_id=batch_id,
            source_id=source_id,
            child_table=child_table,
            child_filter=child_filter,
        )
        if candidates is None:
            return None
        self._authorize_reads(
            connection,
            (candidates,),
            operation_now,
        )
        return candidates

    def list_quotes(
        self,
        *,
        security_ids: tuple[str, ...],
        as_of: datetime,
        batch_id: str | None = None,
        source_id: str | None = None,
    ) -> QuoteReadResult:
        if (
            not isinstance(security_ids, tuple)
            or not 1 <= len(security_ids) <= 100
            or len(set(security_ids)) != len(security_ids)
        ):
            raise ValueError("security_ids must contain 1-100 unique identifiers")
        security_ids = tuple(_identifier(value) for value in security_ids)
        as_of = _utc(as_of, "as_of")
        batch_id, source_id = _validate_batch_selection(batch_id, source_id)
        with self.database.engine.begin() as connection:
            batch = self._select_authorized_batch(
                connection,
                as_of=as_of,
                batch_id=batch_id,
                source_id=source_id,
                child_table=realtime_quotes,
                child_filter=(realtime_quotes.c.security_id.in_(security_ids),),
            )
            if batch is None:
                kind_batch = self._select_authorized_batch(
                    connection,
                    as_of=as_of,
                    batch_id=batch_id,
                    source_id=source_id,
                    child_table=realtime_quotes,
                )
                return QuoteReadResult(
                    as_of=as_of,
                    batch_id=(
                        kind_batch["quote_batch_id"] if kind_batch is not None else None
                    ),
                    items=(),
                    completeness=_completeness(
                        expected=len(security_ids),
                        received=0,
                        unavailable_reason="no_data",
                    ),
                )
            rows = (
                connection.execute(
                    select(realtime_quotes)
                    .where(
                        realtime_quotes.c.source_id == batch["source_id"],
                        realtime_quotes.c.batch_id == batch["quote_batch_id"],
                        realtime_quotes.c.security_id.in_(security_ids),
                    )
                    .order_by(realtime_quotes.c.security_id, realtime_quotes.c.id)
                )
                .mappings()
                .all()
            )
        return QuoteReadResult(
            as_of=as_of,
            batch_id=batch["quote_batch_id"],
            items=tuple(
                QuoteRecord(
                    batch_id=batch["quote_batch_id"],
                    source_id=batch["source_id"],
                    source_version=batch["source_version"],
                    source_event_at=batch["source_event_at"],
                    received_at=batch["received_at"],
                    available_at=batch["available_at"],
                    quote=_quote(row),
                )
                for row in rows
            ),
            completeness=_completeness(
                expected=len(security_ids),
                received=len(rows),
            ),
        )

    def list_minutes(
        self,
        *,
        security_id: str,
        session_start: datetime,
        session_end: datetime,
        as_of: datetime,
        session_windows: tuple[tuple[datetime, datetime], ...] | None = None,
        batch_id: str | None = None,
        source_id: str | None = None,
    ) -> MinuteReadResult:
        security_id = _identifier(security_id)
        windows = _session_windows(session_start, session_end, session_windows)
        expected_per_source = sum(
            _expected_minutes(window_start, window_end)
            for window_start, window_end in windows
        )
        window_filter = _session_window_filter(
            realtime_minute_bars.c.event_at,
            windows,
        )
        as_of = _utc(as_of, "as_of")
        batch_id, source_id = _validate_batch_selection(batch_id, source_id)
        with self.database.engine.begin() as connection:
            parent_query = (
                select(
                    realtime_quote_batches.c.quote_batch_id,
                    realtime_quote_batches.c.source_id,
                    realtime_quote_batches.c.source_version,
                    realtime_quote_batches.c.permission_snapshot,
                )
                .join(
                    realtime_minute_bars,
                    and_(
                        realtime_minute_bars.c.source_id
                        == realtime_quote_batches.c.source_id,
                        realtime_minute_bars.c.batch_id
                        == realtime_quote_batches.c.quote_batch_id,
                        realtime_minute_bars.c.source_version
                        == realtime_quote_batches.c.source_version,
                    ),
                )
                .where(
                    realtime_minute_bars.c.security_id == security_id,
                    window_filter,
                    realtime_minute_bars.c.available_at <= as_of,
                    realtime_quote_batches.c.available_at <= as_of,
                )
            )
            if batch_id is not None:
                parent_query = parent_query.where(
                    realtime_minute_bars.c.batch_id == batch_id
                )
            if source_id is not None:
                parent_query = parent_query.where(
                    realtime_minute_bars.c.source_id == source_id
                )
            parents = connection.execute(parent_query.distinct()).mappings().all()
            parents = self._authorize_reads(
                connection,
                parents,
                _utc(self.clock(), "operation time"),
                filter_unauthorized=source_id is None,
            )
            if not parents:
                kind_batch = self._select_authorized_batch(
                    connection,
                    as_of=as_of,
                    batch_id=batch_id,
                    source_id=source_id,
                    child_table=realtime_minute_bars,
                    child_filter=(
                        window_filter,
                        realtime_minute_bars.c.available_at <= as_of,
                    ),
                )
                return MinuteReadResult(
                    as_of=as_of,
                    batch_id=(
                        kind_batch["quote_batch_id"]
                        if kind_batch is not None
                        else batch_id
                    ),
                    items=(),
                    completeness=_completeness(
                        expected=expected_per_source,
                        received=0,
                        unavailable_reason="no_data",
                    ),
                )
            authorized_sources = {parent["source_id"] for parent in parents}
            query = (
                select(realtime_minute_bars)
                .join(
                    realtime_quote_batches,
                    and_(
                        realtime_quote_batches.c.source_id
                        == realtime_minute_bars.c.source_id,
                        realtime_quote_batches.c.quote_batch_id
                        == realtime_minute_bars.c.batch_id,
                        realtime_quote_batches.c.source_version
                        == realtime_minute_bars.c.source_version,
                    ),
                )
                .where(
                    realtime_minute_bars.c.source_id.in_(authorized_sources),
                    realtime_minute_bars.c.security_id == security_id,
                    window_filter,
                    realtime_minute_bars.c.available_at <= as_of,
                    realtime_quote_batches.c.available_at <= as_of,
                )
            )
            if batch_id is not None:
                query = query.where(realtime_minute_bars.c.batch_id == batch_id)
            rows = (
                connection.execute(
                    query.distinct(
                        realtime_minute_bars.c.source_id,
                        realtime_minute_bars.c.security_id,
                        realtime_minute_bars.c.event_at,
                    ).order_by(
                        realtime_minute_bars.c.source_id,
                        realtime_minute_bars.c.security_id,
                        realtime_minute_bars.c.event_at,
                        realtime_minute_bars.c.revision.desc(),
                        realtime_minute_bars.c.id.desc(),
                    )
                )
                .mappings()
                .all()
            )
        completeness = _trading_minute_completeness(
            tuple(
                (row["source_id"], row["security_id"], row["event_at"]) for row in rows
            ),
            windows,
        )
        return MinuteReadResult(
            as_of=as_of,
            batch_id=batch_id,
            items=tuple(
                MinuteBarRecord(
                    batch_id=row["batch_id"],
                    source_id=row["source_id"],
                    source_version=row["source_version"],
                    bar=_minute(row),
                )
                for row in rows
            ),
            completeness=completeness,
        )

    def list_sector_flow(
        self,
        *,
        sector_id: str,
        session_start: datetime,
        session_end: datetime,
        as_of: datetime,
        session_windows: tuple[tuple[datetime, datetime], ...] | None = None,
        batch_id: str | None = None,
        source_id: str | None = None,
    ) -> SectorFlowReadResult:
        sector_id = _identifier(sector_id)
        windows = _session_windows(session_start, session_end, session_windows)
        window_filter = _session_window_filter(
            realtime_sector_flow_points.c.event_at,
            windows,
        )
        as_of = _utc(as_of, "as_of")
        batch_id, source_id = _validate_batch_selection(batch_id, source_id)
        with self.database.engine.begin() as connection:
            batch = self._select_authorized_batch(
                connection,
                as_of=as_of,
                batch_id=batch_id,
                source_id=source_id,
                child_table=realtime_sector_flow_points,
                child_filter=(
                    realtime_sector_flow_points.c.sector_id == sector_id,
                    window_filter,
                ),
            )
            if batch is None:
                kind_batch = self._select_authorized_batch(
                    connection,
                    as_of=as_of,
                    batch_id=batch_id,
                    source_id=source_id,
                    child_table=realtime_sector_flow_points,
                    child_filter=(window_filter,),
                )
                return SectorFlowReadResult(
                    as_of=as_of,
                    batch_id=(
                        kind_batch["quote_batch_id"] if kind_batch is not None else None
                    ),
                    items=(),
                    completeness=_completeness(
                        expected=0,
                        received=0,
                        unavailable_reason="no_data",
                    ),
                )
            rows = (
                connection.execute(
                    select(realtime_sector_flow_points)
                    .where(
                        realtime_sector_flow_points.c.source_id == batch["source_id"],
                        realtime_sector_flow_points.c.batch_id
                        == batch["quote_batch_id"],
                        realtime_sector_flow_points.c.sector_id == sector_id,
                        window_filter,
                    )
                    .order_by(
                        realtime_sector_flow_points.c.event_at,
                        realtime_sector_flow_points.c.id,
                    )
                )
                .mappings()
                .all()
            )
        unavailable = next(
            (row["unavailable_reason"] for row in rows if row["net_inflow"] is None),
            None,
        )
        return SectorFlowReadResult(
            as_of=as_of,
            batch_id=batch["quote_batch_id"],
            items=tuple(
                SectorFlowRecord(
                    batch_id=row["batch_id"],
                    source_id=row["source_id"],
                    source_version=batch["source_version"],
                    available_at=batch["available_at"],
                    point=_sector_point(row),
                )
                for row in rows
            ),
            completeness=_completeness(
                expected=len(rows),
                received=len(rows),
                unavailable_reason=unavailable,
            ),
        )

    def _decode_anomaly_cursor(
        self,
        cursor: str,
        *,
        filters: Mapping[str, Any],
        operation_now: datetime,
    ) -> tuple[datetime, int, tuple[datetime, str]]:
        try:
            payload = _decode_cursor(cursor, self._cursor_signing_key)
            if (
                _encode_cursor(payload, self._cursor_signing_key) != cursor
                or set(payload) != _CURSOR_FIELDS
                or type(payload["version"]) is not int
                or payload["version"] != 1
                or type(payload["sort_version"]) is not int
                or payload["sort_version"] != 1
                or not isinstance(payload["filters"], dict)
                or set(payload["filters"]) != _CURSOR_FILTER_FIELDS
                or payload["filters"] != filters
            ):
                raise ValueError
            issued_at = _canonical_datetime(payload["issued_at"])
            expires_at = _canonical_datetime(payload["expires_at"])
            watermark = payload["watermark"]
            if (
                expires_at != issued_at + ANOMALY_CURSOR_TTL
                or issued_at > operation_now
                or operation_now >= expires_at
                or type(watermark) is not int
                or watermark < 0
            ):
                raise ValueError
            last = payload["last"]
            if (
                not isinstance(last, list)
                or len(last) != 2
                or not isinstance(last[1], str)
            ):
                raise ValueError
            last_available = _canonical_datetime(last[0])
            _identifier(last[1])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Invalid realtime anomaly cursor") from None
        return issued_at, watermark, (last_available, last[1])

    def list_anomalies(
        self,
        *,
        date: Date,
        as_of: datetime,
        limit: int = 50,
        cursor: str | None = None,
        source_id: str | None = None,
        security_id: str | None = None,
    ) -> AnomalyReadResult:
        if not isinstance(date, Date):
            raise TypeError("date must be a date")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        as_of = _utc(as_of, "as_of")
        if source_id is not None:
            source_id = _identifier(source_id)
        if security_id is not None:
            security_id = _identifier(security_id)
        operation_now = _utc(self.clock(), "operation time")
        filters = {
            "date": date.isoformat(),
            "as_of": as_of.isoformat(),
            "source_id": source_id,
            "security_id": security_id,
        }
        last = None
        if cursor is None:
            issued_at = operation_now
            watermark = None
        else:
            issued_at, watermark, last = self._decode_anomaly_cursor(
                cursor,
                filters=filters,
                operation_now=operation_now,
            )
        day_start = datetime.combine(date, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        with self.database.engine.begin() as connection:
            if watermark is None:
                watermark = connection.scalar(
                    select(
                        func.coalesce(
                            func.max(realtime_quote_batches.c.ingest_order),
                            0,
                        )
                    )
                )
            visible_minutes = (
                select(1)
                .select_from(realtime_minute_bars)
                .where(
                    realtime_minute_bars.c.source_id
                    == realtime_quote_batches.c.source_id,
                    realtime_minute_bars.c.batch_id
                    == realtime_quote_batches.c.quote_batch_id,
                    realtime_minute_bars.c.event_at >= day_start,
                    realtime_minute_bars.c.event_at < day_end,
                    realtime_minute_bars.c.available_at <= as_of,
                )
            )
            if security_id is not None:
                visible_minutes = visible_minutes.where(
                    realtime_minute_bars.c.security_id == security_id
                )
            source_query = select(
                realtime_quote_batches.c.quote_batch_id,
                realtime_quote_batches.c.source_id,
                realtime_quote_batches.c.source_version,
                realtime_quote_batches.c.permission_snapshot,
            ).where(
                visible_minutes.offset(0).exists(),
                realtime_quote_batches.c.available_at <= as_of,
                realtime_quote_batches.c.ingest_order <= watermark,
            )
            if source_id is not None:
                source_query = source_query.where(
                    realtime_quote_batches.c.source_id == source_id
                )
            parents = connection.execute(source_query.distinct()).mappings().all()
            parents = self._authorize_reads(
                connection,
                parents,
                operation_now,
                filter_unauthorized=source_id is None,
            )
            authorized_sources = {parent["source_id"] for parent in parents}
            if not authorized_sources:
                return AnomalyReadResult(
                    as_of=as_of,
                    items=(),
                    next_cursor=None,
                    completeness=_completeness(
                        expected=None,
                        received=0,
                        unavailable_reason="no_data",
                    ),
                )
            query = (
                select(realtime_anomaly_events)
                .join(
                    realtime_quote_batches,
                    and_(
                        realtime_quote_batches.c.source_id
                        == realtime_anomaly_events.c.source_id,
                        realtime_quote_batches.c.quote_batch_id
                        == realtime_anomaly_events.c.batch_id,
                    ),
                )
                .where(
                    realtime_anomaly_events.c.source_id.in_(authorized_sources),
                    realtime_anomaly_events.c.event_at >= day_start,
                    realtime_anomaly_events.c.event_at < day_end,
                    realtime_anomaly_events.c.available_at <= as_of,
                    realtime_quote_batches.c.available_at <= as_of,
                    realtime_quote_batches.c.ingest_order <= watermark,
                )
            )
            if security_id is not None:
                query = query.where(
                    realtime_anomaly_events.c.security_id == security_id
                )
            if last is not None:
                last_available, last_event_id = last
                query = query.where(
                    or_(
                        realtime_anomaly_events.c.available_at < last_available,
                        and_(
                            realtime_anomaly_events.c.available_at == last_available,
                            realtime_anomaly_events.c.event_id < last_event_id,
                        ),
                    )
                )
            rows = (
                connection.execute(
                    query.order_by(
                        realtime_anomaly_events.c.available_at.desc(),
                        realtime_anomaly_events.c.event_id.desc(),
                    ).limit(limit + 1)
                )
                .mappings()
                .all()
            )
        visible = rows[:limit]
        items = tuple(
            AnomalyEvent(
                event_id=row["event_id"],
                batch_id=row["batch_id"],
                security_id=row["security_id"],
                rule_version=row["rule_version"],
                event_at=row["event_at"],
                available_at=row["available_at"],
                result=AnomalyResult(
                    triggered=True,
                    metric=row["metric"],
                    window=row["window"],
                    value=row["value"],
                    threshold=row["threshold"],
                    reason=None,
                ),
                quality=row["quality"],
            )
            for row in visible
        )
        next_cursor = None
        if len(rows) > limit:
            final = visible[-1]
            next_cursor = _encode_cursor(
                {
                    "version": 1,
                    "sort_version": 1,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": (issued_at + ANOMALY_CURSOR_TTL).isoformat(),
                    "watermark": watermark,
                    "filters": filters,
                    "last": [
                        final["available_at"].isoformat(),
                        final["event_id"],
                    ],
                },
                self._cursor_signing_key,
            )
        return AnomalyReadResult(
            as_of=as_of,
            items=items,
            next_cursor=next_cursor,
            completeness=_completeness(
                expected=len(items),
                received=len(items),
            ),
        )


__all__ = [
    "ANOMALY_CURSOR_TTL",
    "REALTIME_CURSOR_ORDER_LOCK",
    "RealtimeRepository",
]
