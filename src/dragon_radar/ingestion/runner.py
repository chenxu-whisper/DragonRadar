"""Durable licensed-local ingestion, quarantine, retry, and materialization."""

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.domain.quality import grant_allows_local, state_at
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
        record = self.repository.import_batch(envelope.batch, key=key)
        return self._record_attempt(envelope, record.id, record.payload_hash, key=key)

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
            if (
                previous["status"] != "quarantined"
                or previous["retry_from"] is None
                or previous["retry_to"] is None
            ):
                raise ValueError("Market attempt is not retryable")
            if (
                envelope.batch.source_id != previous["source_id"]
                or envelope.page_manifest.request_from != previous["retry_from"]
                or envelope.page_manifest.request_to != previous["retry_to"]
            ):
                raise Conflict("Market retry does not match the failed attempt")
            current = self.repository._source(connection, envelope.batch.source_id)
            old_grant = SourceGrant.model_validate(previous["embedded_grant"])
            now = self.repository.clock()
            if not all(
                grant_allows_local(
                    grant, source_id=envelope.batch.source_id, now=now
                )
                for grant in (current, old_grant)
            ):
                raise Forbidden("Source is not locally authorized")
            task_id = previous["task_id"]

        record = self.repository.import_batch(envelope.batch, key=key)
        return self._record_attempt(
            envelope,
            record.id,
            record.payload_hash,
            key=key,
            task_id=task_id,
        )

    def _record_attempt(
        self,
        envelope: LicensedMarketBatch,
        batch_id: UUID,
        payload_hash: str,
        *,
        key: str,
        task_id: UUID | None = None,
    ) -> MarketIngestionResult:
        batch = envelope.batch
        with self.database.engine.begin() as connection:
            self.repository._lock(connection, "market-ingestion-key:" + key)
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
                if (
                    existing["payload_hash"] != payload_hash
                    or existing["batch_id"] != batch_id
                ):
                    raise Conflict("Idempotency key already belongs to another payload")
                return _result(existing)

            current = self.repository._source(connection, batch.source_id)
            now = self.repository._authorize(current, batch)
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

            archive_issues = self._archive_quality(connection, batch_id)["issues"]
            issues = [
                f"{item['code']}: {item['message']}"
                for item in archive_issues
            ]
            missing_items: list[str] = []
            failed_cursor = None
            if any(item["code"] == "incomplete_pages" for item in archive_issues):
                missing_items.append("batch_complete_pages:false")
            gap = _page_gap(envelope.page_manifest)
            if gap is not None:
                missing_items.append(gap[0])
                failed_cursor = gap[1]
                issues.append("page_manifest: pagination is incomplete")
            if (
                envelope.page_manifest.request_from > batch.trade_date
                or envelope.page_manifest.request_to < batch.trade_date
            ):
                missing_items.append(f"trade_date:{batch.trade_date.isoformat()}")
                issues.append("request_range: trade date is outside the request")
            missing_codes = sorted(
                state.security_id
                for state in batch.security_states
                if state.code is None
            )
            if missing_codes:
                missing_items.extend(
                    f"code:{security_id}" for security_id in missing_codes
                )
                issues.append("security_code: current display code is required")

            status = "quarantined" if issues else "succeeded"
            attempt_id = uuid4()
            row = (
                connection.execute(
                    insert(market_import_attempts)
                    .values(
                        id=attempt_id,
                        task_id=task_id,
                        batch_id=batch_id,
                        idempotency_key=key,
                        payload_hash=payload_hash,
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
                self._materialize(connection, batch, batch_id)
            connection.execute(
                update(market_import_tasks)
                .where(market_import_tasks.c.id == task_id)
                .values(status=status, updated_at=now)
            )
            return _result(row)

    @staticmethod
    def _archive_quality(connection, batch_id: UUID) -> Mapping[str, object]:
        from dragon_radar.storage.models import import_batches

        return connection.execute(
            select(import_batches.c.quality_report).where(
                import_batches.c.id == batch_id
            )
        ).scalar_one()

    @staticmethod
    def _materialize(connection, batch: MarketBatch, archive_id: UUID) -> None:
        current_states = {}
        for security_id in batch.expected_security_ids:
            current = state_at(batch, security_id, batch.trade_date)
            if current is not None:
                current_states[security_id] = current

        for item in current_states.values():
            values = {
                "security_id": item.security_id,
                "code": item.code,
                "name": item.name,
                "security_type": item.security_type or "unknown",
                "exchange": item.exchange,
                "board": item.board,
                "list_date": item.listing_date,
                "delist_date": (
                    item.effective_to if item.is_delisting is True else None
                ),
                "is_st": item.is_st if item.is_st is not None else False,
                "is_suspended": item.suspended,
                "source": item.source_id,
                "source_version": item.source_version,
                "available_at": item.available_at,
                "source_archive_id": archive_id,
            }
            connection.execute(
                pg_insert(security)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[security.c.security_id],
                    set_={
                        key: value
                        for key, value in values.items()
                        if key != "security_id"
                    },
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
                connection.execute(
                    pg_insert(security_state_history)
                    .values(
                        security_id=item.security_id,
                        state_type=state_type,
                        old_value=None,
                        new_value=_state_value(value),
                        valid_from=item.effective_from,
                        valid_to=item.effective_to,
                        source=item.source_id,
                        source_version=item.source_version,
                        available_at=item.available_at,
                        source_archive_id=archive_id,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            security_state_history.c.security_id,
                            security_state_history.c.state_type,
                            security_state_history.c.valid_from,
                        ]
                    )
                )

        for item in batch.bars:
            item_state = state_at(batch, item.security_id, item.trade_date)
            if item_state is None or item_state.suspended:
                continue
            target = (
                index_daily_kline
                if item_state.security_type == "index"
                else daily_kline
            )
            change_ratio = None
            if item.previous_close is not None and item.close is not None:
                change_ratio = item.close / item.previous_close - Decimal(1)
            connection.execute(
                pg_insert(target)
                .values(
                    security_id=item.security_id,
                    trade_date=item.trade_date,
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    volume=item.volume,
                    amount=item.amount,
                    change_ratio=change_ratio,
                    adj_factor=item.adjustment_factor,
                    limit_up_price=item.limit_up,
                    limit_down_price=item.limit_down,
                    turnover_rate=item.turnover_rate,
                    float_mv=item.float_mv,
                    total_mv=item.total_mv,
                    currency=item.currency,
                    volume_unit=item.volume_unit,
                    source=item.source_id,
                    source_version=item.source_version,
                    available_at=item.available_at,
                    source_archive_id=archive_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        target.c.security_id,
                        target.c.trade_date,
                        target.c.source,
                        target.c.source_version,
                    ]
                )
            )

        for item in batch.limit_up_events:
            connection.execute(
                pg_insert(limit_up_record)
                .values(
                    security_id=item.security_id,
                    trade_date=item.trade_date,
                    is_limit_up=item.is_limit_up,
                    touched_limit=item.touched_limit,
                    first_seal_time=item.first_seal_time,
                    last_seal_time=item.last_seal_time,
                    break_count=item.break_count,
                    seal_amount=item.seal_amount,
                    continuous_boards=item.continuous_boards,
                    source=item.source_id,
                    source_version=item.source_version,
                    available_at=item.available_at,
                    source_archive_id=archive_id,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        limit_up_record.c.security_id,
                        limit_up_record.c.trade_date,
                    ]
                )
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
            grant_allows_local(grant, source_id=row["source_id"], now=now)
            for grant in (current, embedded)
        )
