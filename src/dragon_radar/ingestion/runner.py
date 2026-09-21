"""Durable licensed-local ingestion, quarantine, retry, and materialization."""

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.domain.quality import (
    QualityIssue,
    grant_allows_display,
    grant_allows_storage,
    state_at,
)
from dragon_radar.ingestion.contracts import (
    LicensedMarketBatch,
    MarketImportAttempt,
    MarketImportTask,
    MarketIngestionResult,
    MarketPageManifest,
)
from dragon_radar.readmodels.tables import (
    daily_kline,
    index_daily_kline,
    limit_up_record,
    market_import_attempts,
    market_import_tasks,
    security,
    security_state_history,
)
from dragon_radar.storage.models import import_batches
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    Repository,
    _identifier,
    _uuid,
)


def _page_gap(manifest: MarketPageManifest) -> tuple[str, str | None] | None:
    if manifest.request_from > manifest.request_to:
        return ("invalid_request_range", None)
    expected = None
    if not manifest.pages:
        return ("expected_cursor:<initial>", None)
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, page in enumerate(manifest.pages):
        if page.page_id in seen_ids or page.page_hash in seen_hashes:
            return (f"duplicate_page:{page.page_id}", page.request_cursor)
        seen_ids.add(page.page_id)
        seen_hashes.add(page.page_hash)
        if page.request_cursor != expected:
            return (
                "expected_cursor:"
                + (expected if expected is not None else "<initial>"),
                page.request_cursor,
            )
        if index < len(manifest.pages) - 1 and page.next_cursor is None:
            return (f"missing_after_page:{page.page_id}", None)
        expected = page.next_cursor
    if expected != manifest.terminal_cursor:
        return (
            "expected_cursor:" + (expected if expected is not None else "<terminal>"),
            manifest.terminal_cursor,
        )
    received_total = sum(page.received_row_count for page in manifest.pages)
    if received_total != manifest.declared_total:
        return ("declared_total_mismatch", None)
    return None


def _envelope_quality_issues(
    envelope: LicensedMarketBatch,
) -> tuple[QualityIssue, ...]:
    issues = []

    def check_bound(
        value: str | None,
        maximum: int,
        field: str,
        *,
        security_id: str | None = None,
        trade_date=None,
    ) -> None:
        if value is not None and len(value) > maximum:
            issues.append(
                QualityIssue(
                    code="normalized_bounds",
                    message=f"{field} exceeds normalized limit {maximum}",
                    security_id=security_id,
                    trade_date=trade_date,
                )
            )

    if _page_gap(envelope.page_manifest) is not None:
        issues.append(
            QualityIssue(
                code="page_manifest",
                message="Page manifest is not contiguous and self-consistent",
            )
        )
    if (
        envelope.page_manifest.request_from > envelope.batch.trade_date
        or envelope.page_manifest.request_to < envelope.batch.trade_date
    ):
        issues.append(
            QualityIssue(
                code="request_range",
                message="Trade date is outside the requested range",
                trade_date=envelope.batch.trade_date,
            )
        )
    batch = envelope.batch
    check_bound(batch.source_id, 50, "source_id")
    check_bound(batch.source_version, 100, "source_version")
    required_identity = (
        "code",
        "security_type",
        "listing_date",
        "suspended",
        "is_st",
    )
    for state in envelope.batch.security_states:
        missing = [
            field for field in required_identity if getattr(state, field) is None
        ]
        if missing:
            issues.append(
                QualityIssue(
                    code="security_identity",
                    message="Normalized identity requires " + ", ".join(missing),
                    security_id=state.security_id,
                )
            )
        for field, maximum in (
            ("security_id", 32),
            ("code", 10),
            ("name", 50),
            ("security_type", 30),
            ("exchange", 10),
            ("board", 20),
            ("source_id", 50),
            ("source_version", 100),
        ):
            check_bound(
                getattr(state, field),
                maximum,
                field,
                security_id=state.security_id,
            )
    for bar in batch.bars:
        for field, maximum in (
            ("security_id", 32),
            ("currency", 10),
            ("volume_unit", 20),
            ("source_id", 50),
            ("source_version", 100),
        ):
            check_bound(
                getattr(bar, field),
                maximum,
                field,
                security_id=bar.security_id,
                trade_date=bar.trade_date,
            )
    for event in batch.limit_up_events:
        for field, maximum in (
            ("security_id", 32),
            ("source_id", 50),
            ("source_version", 100),
        ):
            check_bound(
                getattr(event, field),
                maximum,
                field,
                security_id=event.security_id,
                trade_date=event.trade_date,
            )
    return tuple(issues)


def _envelope_hash(envelope: LicensedMarketBatch) -> str:
    canonical = json.dumps(
        envelope.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_hash(envelope_hash: str, retry_from_attempt_id: UUID | None) -> str:
    if retry_from_attempt_id is None:
        return envelope_hash
    command = f"market-retry:{retry_from_attempt_id}:{envelope_hash}"
    return hashlib.sha256(command.encode("ascii")).hexdigest()


def _attempt(row: Mapping[str, object]) -> MarketImportAttempt:
    return MarketImportAttempt(
        id=row["id"],
        task_id=row["task_id"],
        batch_id=row["batch_id"],
        status=row["status"],
        stage=row["stage"],
        missing_items=tuple(row["missing_items"]),
        failed_cursor=row["failed_cursor"],
        request_from=row["request_from"],
        request_to=row["request_to"],
        retry_from=row["retry_from"],
        retry_to=row["retry_to"],
        created_at=row["created_at"],
    )


def _task(row: Mapping[str, object]) -> MarketImportTask:
    return MarketImportTask(
        id=row["id"],
        status=row["status"],
        source_id=row["source_id"],
        request_from=row["request_from"],
        request_to=row["request_to"],
        created_at=row["created_at"],
    )


def _result(row: Mapping[str, object]) -> MarketIngestionResult:
    return MarketIngestionResult(
        task_id=row["task_id"],
        attempt_id=row["id"],
        batch_id=row["batch_id"],
        status=row["status"],
    )


def _state_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _insert_immutable_row(
    connection,
    table,
    values: Mapping[str, object],
    key_fields: tuple[str, ...],
    *,
    description: str,
) -> None:
    existing = (
        connection.execute(
            select(table).where(
                *(table.c[field] == values[field] for field in key_fields)
            )
        )
        .mappings()
        .first()
    )
    if existing is None:
        connection.execute(insert(table).values(**values))
    elif any(existing[field] != value for field, value in values.items()):
        raise Conflict(f"Conflicting {description} payload or lineage")


class MarketIngestionRunner:
    """Coordinates canonical Repository archives with normalized local read rows."""

    def __init__(self, repository: Repository):
        self.repository = repository
        self.database = repository.database

    def import_batch(
        self, envelope: LicensedMarketBatch, *, key: str
    ) -> MarketIngestionResult:
        _identifier(key)
        envelope = LicensedMarketBatch.model_validate(envelope.model_dump())
        with self.database.engine.begin() as connection:
            return self._record_attempt(connection, envelope, key=key)

    def _lock_request(self, connection, envelope, key):
        self.repository._lock(connection, "market-ingestion-key:" + key)
        self.repository._lock(connection, "import-key:" + key)
        current = self.repository._source(connection, envelope.batch.source_id)
        now = self.repository._authorize_storage(current, envelope.batch)
        return current, now

    def retry_attempt(
        self,
        attempt_id: UUID | str,
        envelope: LicensedMarketBatch,
        *,
        key: str,
    ) -> MarketIngestionResult:
        attempt_id = _uuid(attempt_id)
        _identifier(key)
        envelope = LicensedMarketBatch.model_validate(envelope.model_dump())
        with self.database.engine.begin() as connection:
            current, now = self._lock_request(connection, envelope, key)
            self.repository._lock(
                connection, "market-import-attempt:" + str(attempt_id)
            )
            previous = (
                connection.execute(
                    select(market_import_attempts)
                    .where(market_import_attempts.c.id == attempt_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if previous is None:
                raise NotFound("Market import attempt not found")
            self.repository._lock(
                connection, "market-import-task:" + str(previous["task_id"])
            )
            task = (
                connection.execute(
                    select(market_import_tasks)
                    .where(market_import_tasks.c.id == previous["task_id"])
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                envelope.batch.source_id != previous["source_id"]
                or envelope.page_manifest.request_from != previous["retry_from"]
                or envelope.page_manifest.request_to != previous["retry_to"]
            ):
                raise Conflict("Market retry does not match the failed attempt")
            old_grant = SourceGrant.model_validate(previous["embedded_grant"])
            if not all(
                grant_allows_storage(
                    grant, source_id=envelope.batch.source_id, now=now
                )
                for grant in (current, old_grant)
            ):
                raise Forbidden("Source is not locally authorized")
            existing = (
                connection.execute(
                    select(market_import_attempts).where(
                        market_import_attempts.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                existing_request_hash = connection.scalar(
                    select(import_batches.c.idempotency_hash).where(
                        import_batches.c.id == existing["batch_id"]
                    )
                )
                if (
                    existing["envelope_hash"] != _envelope_hash(envelope)
                    or existing["task_id"] != previous["task_id"]
                    or existing_request_hash
                    != _request_hash(_envelope_hash(envelope), attempt_id)
                ):
                    raise Conflict("Idempotency key already belongs to another payload")
                return _result(existing)
            if (
                previous["status"] != "quarantined"
                or previous["retry_from"] is None
                or previous["retry_to"] is None
                or task["status"] != "quarantined"
            ):
                raise ValueError("Market attempt is not retryable")
            return self._record_attempt(
                connection,
                envelope,
                key=key,
                task_id=previous["task_id"],
                retry_from_attempt_id=attempt_id,
                current=current,
                now=now,
            )

    def _record_attempt(
        self,
        connection,
        envelope: LicensedMarketBatch,
        *,
        key: str,
        task_id: UUID | None = None,
        retry_from_attempt_id: UUID | None = None,
        current: SourceGrant | None = None,
        now=None,
    ) -> MarketIngestionResult:
        batch = envelope.batch
        envelope_hash = _envelope_hash(envelope)
        request_hash = _request_hash(envelope_hash, retry_from_attempt_id)
        if current is None:
            current, now = self._lock_request(connection, envelope, key)
        existing = (
            connection.execute(
                select(market_import_attempts).where(
                    market_import_attempts.c.idempotency_key == key
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            existing_request_hash = connection.scalar(
                select(import_batches.c.idempotency_hash).where(
                    import_batches.c.id == existing["batch_id"]
                )
            )
            if (
                existing["envelope_hash"] != envelope_hash
                or existing_request_hash != request_hash
                or task_id is not None
                and existing["task_id"] != task_id
            ):
                raise Conflict("Idempotency key already belongs to another payload")
            return _result(existing)

        additional_issues = list(_envelope_quality_issues(envelope))
        if (
            grant_allows_display(
                batch.grant,
                source_id=batch.source_id,
                now=now,
            )
            and not grant_allows_display(
                current,
                source_id=batch.source_id,
                now=now,
            )
        ):
            additional_issues.append(
                QualityIssue(
                    code="grant_denied",
                    message="Current grant does not authorize local display",
                )
            )
        record = self.repository._import_batch(
            connection,
            batch,
            key=key,
            additional_issues=additional_issues,
            idempotency_hash=request_hash,
            current=current,
            now=now,
        )
        if task_id is None:
            task_id = uuid4()
            connection.execute(
                insert(market_import_tasks).values(
                    id=task_id,
                    idempotency_key=key,
                    source_id=batch.source_id,
                    embedded_grant=batch.grant.model_dump(mode="json"),
                    status="quarantined",
                    request_from=envelope.page_manifest.request_from,
                    request_to=envelope.page_manifest.request_to,
                    created_at=now,
                    updated_at=now,
                )
            )

        archive_issues = record.quality_report["issues"]
        issues = [f"{item['code']}: {item['message']}" for item in archive_issues]
        missing_items: list[str] = []
        failed_cursor = None
        if any(item["code"] == "incomplete_pages" for item in archive_issues):
            missing_items.append("batch_complete_pages:false")
        gap = _page_gap(envelope.page_manifest)
        if gap is not None:
            missing_items.append(gap[0])
            failed_cursor = gap[1]
        if (
            envelope.page_manifest.request_from > batch.trade_date
            or envelope.page_manifest.request_to < batch.trade_date
        ):
            missing_items.append(f"trade_date:{batch.trade_date.isoformat()}")
        for issue in archive_issues:
            if issue["code"] == "security_identity":
                missing_items.append(f"identity:{issue['security_id']}")

        status = "quarantined" if issues else "succeeded"
        attempt_id = uuid4()
        row = (
            connection.execute(
                insert(market_import_attempts)
                .values(
                    id=attempt_id,
                    task_id=task_id,
                    batch_id=record.id,
                    idempotency_key=key,
                    envelope_hash=envelope_hash,
                    source_id=batch.source_id,
                    embedded_grant=batch.grant.model_dump(mode="json"),
                    status=status,
                    stage="quality" if issues else "materialized",
                    page_manifest=envelope.page_manifest.model_dump(mode="json"),
                    missing_items=missing_items,
                    failed_cursor=failed_cursor,
                    request_from=envelope.page_manifest.request_from,
                    request_to=envelope.page_manifest.request_to,
                    retry_from=(
                        envelope.page_manifest.request_from if issues else None
                    ),
                    retry_to=envelope.page_manifest.request_to if issues else None,
                    details=issues,
                    created_at=now,
                )
                .returning(market_import_attempts)
            )
            .mappings()
            .one()
        )
        if status == "succeeded":
            self._materialize(connection, batch, record.id)
        connection.execute(
            update(market_import_tasks)
            .where(market_import_tasks.c.id == task_id)
            .values(status=status, updated_at=now)
        )
        return _result(row)

    def _materialize(
        self, connection, batch: MarketBatch, archive_id: UUID
    ) -> None:
        security_ids = {item.security_id for item in batch.security_states}
        security_ids.update(item.security_id for item in batch.bars)
        security_ids.update(item.security_id for item in batch.limit_up_events)
        for security_id in sorted(security_ids):
            self.repository._lock(connection, "market-security:" + security_id)

        states_by_security = {}
        for item in batch.security_states:
            states_by_security.setdefault(item.security_id, []).append(item)

        identity_states = {}
        for security_id, states in states_by_security.items():
            identity_states[security_id] = state_at(
                batch, security_id, batch.trade_date
            ) or max(states, key=lambda item: item.effective_from)

        for item in identity_states.values():
            values = {
                "security_id": item.security_id,
                "code": item.code,
                "name": item.name,
                "security_type": item.security_type,
                "exchange": item.exchange,
                "board": item.board,
                "list_date": item.listing_date,
                "delist_date": (
                    item.effective_to if item.is_delisting is True else None
                ),
                "is_st": item.is_st,
                "is_suspended": item.suspended,
                "source": item.source_id,
                "source_version": item.source_version,
                "available_at": item.available_at,
                "source_archive_id": archive_id,
            }
            existing = (
                connection.execute(
                    select(
                        security,
                        import_batches.c.trade_date.label("archive_trade_date"),
                    )
                    .select_from(
                        security.join(
                            import_batches,
                            security.c.source_archive_id == import_batches.c.id,
                        )
                    )
                    .where(security.c.security_id == item.security_id)
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(insert(security).values(**values))
                continue

            existing_precedence = (
                existing["archive_trade_date"],
                existing["available_at"],
                existing["source"],
                existing["source_version"],
            )
            # Source metadata is a stable lexical tie-breaker, not chronology.
            incoming_precedence = (
                batch.trade_date,
                item.available_at,
                item.source_id,
                item.source_version,
            )
            identity_fields = (
                "code",
                "name",
                "security_type",
                "exchange",
                "board",
                "list_date",
                "delist_date",
                "is_st",
                "is_suspended",
            )
            if existing_precedence == incoming_precedence and any(
                existing[field] != values[field] for field in identity_fields
            ):
                raise Conflict(
                    "Conflicting security identity has the same precedence key"
                )
            if incoming_precedence > existing_precedence:
                connection.execute(
                    update(security)
                    .where(security.c.security_id == item.security_id)
                    .values(
                        **{
                            key: value
                            for key, value in values.items()
                            if key != "security_id"
                        }
                    )
                )

        state_fields = {
            "code": "code",
            "name": "name",
            "is_st": "is_st",
            "is_suspended": "suspended",
            "board": "board",
        }
        for item in batch.security_states:
            for state_type, attribute in state_fields.items():
                value = getattr(item, attribute)
                if value is None:
                    continue
                values = {
                    "security_id": item.security_id,
                    "state_type": state_type,
                    "old_value": None,
                    "new_value": _state_value(value),
                    "valid_from": item.effective_from,
                    "valid_to": item.effective_to,
                    "source": item.source_id,
                    "source_version": item.source_version,
                    "available_at": item.available_at,
                    "source_archive_id": archive_id,
                }
                _insert_immutable_row(
                    connection,
                    security_state_history,
                    values,
                    ("security_id", "state_type", "valid_from"),
                    description="security history",
                )

        for item in batch.bars:
            item_state = state_at(batch, item.security_id, item.trade_date)
            if item_state is None or item_state.suspended:
                continue
            if item_state.security_type == "index":
                target = index_daily_kline
            elif item_state.security_type == "common_stock":
                target = daily_kline
            else:
                continue
            change_ratio = None
            if item.previous_close is not None and item.close is not None:
                change_ratio = item.close / item.previous_close - Decimal(1)
            values = {
                "security_id": item.security_id,
                "trade_date": item.trade_date,
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
                "amount": item.amount,
                "change_ratio": change_ratio,
                "adj_factor": item.adjustment_factor,
                "limit_up_price": item.limit_up,
                "limit_down_price": item.limit_down,
                "turnover_rate": item.turnover_rate,
                "float_mv": item.float_mv,
                "total_mv": item.total_mv,
                "currency": item.currency,
                "volume_unit": item.volume_unit,
                "source": item.source_id,
                "source_version": item.source_version,
                "available_at": item.available_at,
                "source_archive_id": archive_id,
            }
            _insert_immutable_row(
                connection,
                target,
                values,
                ("security_id", "trade_date", "source", "source_version"),
                description="daily kline",
            )

        for item in batch.limit_up_events:
            item_state = state_at(batch, item.security_id, item.trade_date)
            if item_state is None or item_state.suspended:
                continue
            values = {
                "security_id": item.security_id,
                "trade_date": item.trade_date,
                "is_limit_up": item.is_limit_up,
                "touched_limit": item.touched_limit,
                "first_seal_time": item.first_seal_time,
                "last_seal_time": item.last_seal_time,
                "break_count": item.break_count,
                "seal_amount": item.seal_amount,
                "continuous_boards": item.continuous_boards,
                "source": item.source_id,
                "source_version": item.source_version,
                "available_at": item.available_at,
                "source_archive_id": archive_id,
            }
            existing = (
                connection.execute(
                    select(limit_up_record).where(
                        limit_up_record.c.security_id == item.security_id,
                        limit_up_record.c.trade_date == item.trade_date,
                        limit_up_record.c.source == item.source_id,
                        limit_up_record.c.source_version
                        == item.source_version,
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                connection.execute(insert(limit_up_record).values(**values))
            elif any(existing[key] != value for key, value in values.items()):
                raise Conflict(
                    "Conflicting limit-up payload or lineage for source version"
                )

    def list_tasks(self) -> tuple[MarketImportTask, ...]:
        with self.database.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(market_import_tasks).order_by(
                        market_import_tasks.c.created_at.desc(),
                        market_import_tasks.c.id,
                    )
                )
                .mappings()
                .all()
            )
            return tuple(
                _task(row) for row in rows if self._row_authorized(connection, row)
            )

    def get_task(self, task_id: UUID | str) -> MarketImportTask:
        task_id = _uuid(task_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(market_import_tasks).where(
                        market_import_tasks.c.id == task_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Market import task not found")
            if not self._row_authorized(connection, row):
                raise Forbidden("Source is not locally authorized")
            return _task(row)

    def list_attempts(
        self, *, status: str | None = None
    ) -> tuple[MarketImportAttempt, ...]:
        if status not in (None, "quarantined", "succeeded"):
            raise ValueError("Invalid market attempt status")
        with self.database.engine.begin() as connection:
            query = select(market_import_attempts)
            if status is not None:
                query = query.where(market_import_attempts.c.status == status)
            rows = (
                connection.execute(
                    query.order_by(
                        market_import_attempts.c.attempt_sequence.desc()
                    )
                )
                .mappings()
                .all()
            )
            return tuple(
                _attempt(row) for row in rows if self._row_authorized(connection, row)
            )

    def get_attempt(self, attempt_id: UUID | str) -> MarketImportAttempt:
        attempt_id = _uuid(attempt_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(market_import_attempts).where(
                        market_import_attempts.c.id == attempt_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Market import attempt not found")
            if not self._row_authorized(connection, row):
                raise Forbidden("Source is not locally authorized")
            return _attempt(row)

    def _row_authorized(self, connection, row: Mapping[str, object]) -> bool:
        try:
            current = self.repository._source(connection, row["source_id"])
        except Forbidden:
            return False
        embedded = SourceGrant.model_validate(row["embedded_grant"])
        now = self.repository.clock()
        return all(
            grant_allows_display(grant, source_id=row["source_id"], now=now)
            for grant in (current, embedded)
        )
