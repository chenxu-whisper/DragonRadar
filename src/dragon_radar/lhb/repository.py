"""Transactional ingestion and permission-checked reads for LHB evidence.

The repository owns the trust boundary around the six immutable LHB tables:

* writes strictly re-validate typed batches, canonicalize their payload,
  serialize source/batch/idempotency identity with advisory locks, and persist
  either a full accepted chain or an incomplete one (when the payload itself
  carries a revision with unknown amounts), a duplicate replay, or a rejected
  attempt -- never a partial domain chain;
* reads resolve the point-in-time visibility of each revision, re-check the
  current grant and the captured permission snapshot in SQL (one bounded
  authorization statement per read), reconstruct strictly through the LHB
  pydantic models, and never mutate.

Nothing here imports private symbols from the realtime package; the canonical
encoding and the LATERAL authorization probe are LHB-owned mirrors of the
documented realtime pattern.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import DateTime, and_, cast, insert, select, true

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import grant_allows_display, grant_allows_storage
from dragon_radar.lhb.models import (
    AggregatedSeat,
    AggregationCoverage,
    DisclosurePage,
    DisclosureRevisionInput,
    DisclosureSeatInput,
    DisclosureSelection,
    LHBImportBatch,
    LHBImportResult,
    PermissionSnapshot,
    SeatHistory,
    SeatHistorySelection,
    SeatIdentity,
    SelectedDisclosure,
)
from dragon_radar.lhb.service import aggregate_seats, validate_import_batch
from dragon_radar.lhb.tables import (
    lhb_disclosure_revisions,
    lhb_disclosure_seats,
    lhb_disclosures,
    lhb_import_attempts,
    lhb_import_batches,
    lhb_seats,
)
from dragon_radar.storage.database import Database
from dragon_radar.storage.models import source_grants
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    Repository,
    _identifier,
)

_BOUND_ATTEMPT_STATUSES = ("accepted", "incomplete")


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
    if isinstance(value, date):
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


def _revision_payload(revision: DisclosureRevisionInput) -> dict[str, Any]:
    payload = revision.model_dump()
    payload["seats"] = [
        seat.model_dump()
        for seat in sorted(revision.seats, key=lambda row: row.source_row_id)
    ]
    return payload


def _batch_payload(batch: LHBImportBatch) -> dict[str, Any]:
    payload = batch.model_dump()
    revisions = sorted(
        batch.disclosures,
        key=lambda revision: (
            revision.disclosure_id,
            revision.revision,
        ),
    )
    payload["disclosures"] = [_revision_payload(revision) for revision in revisions]
    return payload


def _row_completeness(buy_amount: Decimal | None, sell_amount: Decimal | None) -> str:
    if buy_amount is None and sell_amount is None:
        return "both_unknown"
    if buy_amount is None:
        return "buy_unknown"
    if sell_amount is None:
        return "sell_unknown"
    return "complete"


def _revision_is_incomplete(revision: DisclosureRevisionInput) -> bool:
    # Completeness is derived solely from the payload amounts: a revision is
    # incomplete iff at least one seat row is missing a buy or sell amount.
    # ``side`` is display-only metadata and never participates in this rule.
    return any(
        _row_completeness(row.buy_amount, row.sell_amount) != "complete"
        for row in revision.seats
    )


class _RejectedImport(Exception):
    """A primary conflict paired with the rejected-attempt bookkeeping."""

    def __init__(
        self,
        primary: Exception,
        *,
        reason: str,
        details: Mapping[str, Any],
        digest: str,
    ):
        super().__init__(reason)
        self.primary = primary
        self.reason = reason
        self.details = details
        self.digest = digest


class LHBRepository(Repository):
    """Immutable LHB writes and permission-checked point-in-time reads."""

    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] | None = None
    ):
        super().__init__(database, clock=clock)

    # -- helpers -----------------------------------------------------------

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("operation time must be a timezone-aware datetime")
        return now.astimezone(UTC)

    @staticmethod
    def _snapshot_allows_display(snapshot, operation_now: datetime):
        return (
            snapshot["state"].as_string() == "authorized",
            cast(
                snapshot["captured_at"].as_string(),
                DateTime(timezone=True),
            )
            <= operation_now,
        )

    @staticmethod
    def _grant_display_predicates(grant, operation_now: datetime):
        return (
            grant["source_id"].as_string() == source_grants.c.source_id,
            grant["approved"].as_boolean().is_(True),
            grant["reference"].as_string().is_not(None),
            cast(grant["starts_at"].as_string(), DateTime(timezone=True))
            <= operation_now,
            cast(grant["expires_at"].as_string(), DateTime(timezone=True))
            > operation_now,
            grant["local_display"].as_boolean().is_(True),
        )

    def _insert_attempt(
        self,
        connection,
        *,
        source_id: str,
        batch_id: str,
        digest: str,
        created_at: datetime,
        status: str,
        reason: str | None,
        details: Mapping[str, Any],
    ):
        return (
            connection.execute(
                insert(lhb_import_attempts)
                .values(
                    id=uuid4(),
                    source_id=source_id,
                    batch_id=batch_id,
                    idempotency_key=batch_id,
                    payload_hash=digest,
                    status=status,
                    reason=reason,
                    details=dict(details),
                    created_at=created_at,
                )
                .returning(lhb_import_attempts)
            )
            .mappings()
            .one()
        )

    @staticmethod
    def _result(attempt: Mapping[str, Any]) -> LHBImportResult:
        return LHBImportResult(
            import_id=str(attempt["id"]),
            batch_id=attempt["batch_id"],
            status=attempt["status"],
            payload_hash=attempt["payload_hash"],
            imported_at=attempt["created_at"],
            reason=attempt["reason"],
        )

    def _persist_rejected(
        self,
        batch: LHBImportBatch,
        rejected: _RejectedImport,
    ) -> None:
        try:
            with self.database.engine.begin() as connection:
                self._insert_attempt(
                    connection,
                    source_id=batch.source_id,
                    batch_id=batch.batch_id,
                    digest=rejected.digest,
                    created_at=self._now(),
                    status="rejected",
                    reason=rejected.reason,
                    details=rejected.details,
                )
        except Exception as cleanup_error:  # pragma: no cover - audit best effort
            rejected.primary.add_note(
                "LHB rejected attempt persistence failed "
                f"({type(cleanup_error).__name__})"
            )
        raise rejected.primary from None

    @staticmethod
    def _fallback_digest(batch: LHBImportBatch) -> str:
        # Validation failures (notably non-finite decimals) cannot be encoded by
        # the strict canonicalizer; the rejected attempt still needs a stable
        # 64-hex identifier.
        raw = json.dumps(
            batch.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # -- ingestion ---------------------------------------------------------

    def ingest_batch(self, batch: LHBImportBatch) -> LHBImportResult:
        if not isinstance(batch, LHBImportBatch):
            raise TypeError("batch must be an LHBImportBatch")
        try:
            with self.database.engine.begin() as connection:
                return self._ingest_transaction(connection, batch)
        except _RejectedImport as rejected:
            self._persist_rejected(batch, rejected)

    def _ingest_transaction(
        self,
        connection,
        batch: LHBImportBatch,
    ) -> LHBImportResult:
        source_id = _identifier(batch.source_id)
        batch_id = _identifier(batch.batch_id)
        current = self._source(connection, source_id)
        self._lock(connection, f"lhb-batch:{source_id}:{batch_id}")
        self._lock(connection, f"lhb-import-key:{source_id}:{batch_id}")
        now = self._now()
        if (
            batch.permission.state != "authorized"
            or batch.permission.captured_at > now
            or not grant_allows_storage(current, source_id=source_id, now=now)
        ):
            # Import authorization failure persists no attempt row at all and
            # short-circuits before any semantic validation.
            raise Forbidden("Source is not authorized for LHB storage")

        try:
            validated = validate_import_batch(batch)
        except (ValidationError, ValueError) as exc:
            attempt = self._insert_attempt(
                connection,
                source_id=source_id,
                batch_id=batch_id,
                digest=self._fallback_digest(batch),
                created_at=now,
                status="rejected",
                reason="validation_failed",
                details={
                    "kind": "lhb",
                    "error_type": type(exc).__name__,
                },
            )
            return self._result(attempt)

        digest = _canonical_hash({"kind": "lhb", "batch": _batch_payload(validated)})

        existing = (
            connection.execute(
                select(lhb_import_batches).where(
                    lhb_import_batches.c.source_id == source_id,
                    lhb_import_batches.c.batch_id == batch_id,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if existing["payload_hash"] != digest:
                raise _RejectedImport(
                    Conflict("LHB batch identity is bound to another payload"),
                    reason="batch_identity_conflict",
                    details={"kind": "lhb"},
                    digest=digest,
                )
            bound = (
                connection.execute(
                    select(lhb_import_attempts)
                    .where(
                        lhb_import_attempts.c.source_id == source_id,
                        lhb_import_attempts.c.batch_id == batch_id,
                        lhb_import_attempts.c.status.in_(_BOUND_ATTEMPT_STATUSES),
                    )
                    .order_by(
                        (lhb_import_attempts.c.status == "accepted").desc(),
                        lhb_import_attempts.c.created_at,
                        lhb_import_attempts.c.id,
                    )
                )
                .mappings()
                .first()
            )
            if bound is None:
                raise _RejectedImport(
                    Conflict("LHB batch exists without an import attempt"),
                    reason="batch_attempt_missing",
                    details={"kind": "lhb"},
                    digest=digest,
                )
            attempt = self._insert_attempt(
                connection,
                source_id=source_id,
                batch_id=batch_id,
                digest=digest,
                created_at=now,
                status="duplicate",
                reason=None,
                details={"original_import_id": str(bound["id"])},
            )
            return self._result(attempt)

        used_key = (
            connection.execute(
                select(lhb_import_attempts).where(
                    lhb_import_attempts.c.source_id == source_id,
                    lhb_import_attempts.c.idempotency_key == batch_id,
                    lhb_import_attempts.c.status.in_(_BOUND_ATTEMPT_STATUSES),
                )
            )
            .mappings()
            .first()
        )
        if used_key is not None:
            raise _RejectedImport(
                Conflict("LHB idempotency key belongs to another import"),
                reason="idempotency_key_conflict",
                details={"kind": "lhb"},
                digest=digest,
            )

        return self._insert_chain(connection, validated, digest, now)

    def _insert_chain(
        self,
        connection,
        batch: LHBImportBatch,
        digest: str,
        now: datetime,
    ) -> LHBImportResult:
        source_id = batch.source_id
        batch_id = batch.batch_id
        incomplete_revisions = [
            f"{revision.disclosure_id}:{revision.revision}"
            for revision in sorted(
                batch.disclosures,
                key=lambda revision: (revision.disclosure_id, revision.revision),
            )
            if _revision_is_incomplete(revision)
        ]
        incomplete = bool(incomplete_revisions)
        status = "incomplete" if incomplete else "accepted"
        reason = "incomplete_revision" if incomplete else None

        connection.execute(
            insert(lhb_import_batches).values(
                id=uuid4(),
                batch_id=batch_id,
                source_id=source_id,
                source_version=batch.source_version,
                source_event_at=batch.source_event_at,
                received_at=batch.received_at,
                available_at=batch.available_at,
                permission_snapshot=batch.permission.model_dump(mode="json"),
                payload_hash=digest,
                status=status,
                reason=reason,
                imported_at=now,
            )
        )

        self._insert_disclosure_identities(connection, batch, digest)
        self._insert_seat_identities(connection, batch, digest)
        self._insert_revisions(connection, batch, batch_digest=digest)

        seat_row_count = sum(len(revision.seats) for revision in batch.disclosures)
        if incomplete:
            details: Mapping[str, Any] = {"incomplete_revisions": incomplete_revisions}
        else:
            details = {
                "kind": "lhb",
                "disclosure_count": len(batch.disclosures),
                "seat_row_count": seat_row_count,
            }
        attempt = self._insert_attempt(
            connection,
            source_id=source_id,
            batch_id=batch_id,
            digest=digest,
            created_at=now,
            status=status,
            reason=reason,
            details=details,
        )
        return self._result(attempt)

    @staticmethod
    def _insert_disclosure_identities(
        connection, batch: LHBImportBatch, digest: str
    ) -> None:
        source_id = batch.source_id
        identities = {}
        for revision in batch.disclosures:
            identities[revision.disclosure_id] = (
                revision.security_id,
                revision.disclosure_date,
                revision.reason,
                revision.permission.permission_id,
            )
        existing = {
            row["disclosure_id"]: row
            for row in connection.execute(
                select(lhb_disclosures).where(
                    lhb_disclosures.c.source_id == source_id,
                    lhb_disclosures.c.disclosure_id.in_(tuple(identities)),
                )
            )
            .mappings()
            .all()
        }
        missing = []
        for revision in sorted(
            batch.disclosures,
            key=lambda revision: (revision.disclosure_id, revision.revision),
        ):
            security_id, disclosure_date, reason_text, permission_id = identities[
                revision.disclosure_id
            ]
            current = existing.get(revision.disclosure_id)
            if current is not None:
                if (
                    current["security_id"] != security_id
                    or current["disclosure_date"] != disclosure_date
                    or current["reason"] != reason_text
                    or current["permission_id"] != permission_id
                ):
                    raise _RejectedImport(
                        Conflict(
                            "divergent LHB disclosure identity for "
                            f"{revision.disclosure_id}"
                        ),
                        reason="disclosure_identity_conflict",
                        details={"kind": "lhb"},
                        digest=digest,
                    )
                continue
            missing.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "disclosure_id": revision.disclosure_id,
                    "security_id": security_id,
                    "disclosure_date": disclosure_date,
                    "reason": reason_text,
                    "permission_id": permission_id,
                }
            )
        if missing:
            connection.execute(insert(lhb_disclosures), missing)

    @staticmethod
    def _insert_seat_identities(connection, batch: LHBImportBatch, digest: str) -> None:
        source_id = batch.source_id
        wanted: dict[tuple[str, str], str] = {}
        for revision in batch.disclosures:
            for row in revision.seats:
                key = (row.seat_id, row.mapping_version)
                previous = wanted.get(key)
                if previous is not None and previous != row.source_seat_name:
                    raise _RejectedImport(
                        Conflict(
                            f"divergent LHB seat identity for {row.seat_id} "
                            f"({row.mapping_version})"
                        ),
                        reason="seat_identity_conflict",
                        details={"kind": "lhb"},
                        digest=digest,
                    )
                wanted.setdefault(key, row.source_seat_name)
        existing = {
            (row["seat_id"], row["mapping_version"]): row
            for row in connection.execute(
                select(lhb_seats).where(lhb_seats.c.source_id == source_id)
            )
            .mappings()
            .all()
        }
        missing = []
        for (seat_id, mapping_version), name in sorted(wanted.items()):
            current = existing.get((seat_id, mapping_version))
            if current is not None:
                if current["source_seat_name"] != name:
                    raise _RejectedImport(
                        Conflict(
                            f"LHB seat identity {seat_id} "
                            f"({mapping_version}) was renamed"
                        ),
                        reason="seat_identity_conflict",
                        details={"kind": "lhb"},
                        digest=digest,
                    )
                continue
            missing.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "seat_id": seat_id,
                    "mapping_version": mapping_version,
                    "source_seat_name": name,
                    "provenance": {
                        "mapping_version": mapping_version,
                        "source_id": source_id,
                    },
                }
            )
        if missing:
            connection.execute(insert(lhb_seats), missing)

    @staticmethod
    def _insert_revisions(
        connection,
        batch: LHBImportBatch,
        *,
        batch_digest: str,
    ) -> None:
        source_id = batch.source_id
        for revision in sorted(
            batch.disclosures,
            key=lambda revision: (revision.disclosure_id, revision.revision),
        ):
            digest = _canonical_hash(
                {"kind": "lhb", "revision": _revision_payload(revision)}
            )
            existing = (
                connection.execute(
                    select(lhb_disclosure_revisions).where(
                        lhb_disclosure_revisions.c.source_id == source_id,
                        lhb_disclosure_revisions.c.disclosure_id
                        == revision.disclosure_id,
                        lhb_disclosure_revisions.c.revision == revision.revision,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise _RejectedImport(
                        Conflict(
                            "LHB revision identity "
                            f"{revision.disclosure_id}:{revision.revision} "
                            "belongs to another payload"
                        ),
                        reason="revision_identity_conflict",
                        details={"kind": "lhb"},
                        digest=batch_digest,
                    )
                # Identical revision carried by a new batch: idempotent.
                continue

            revision_incomplete = _revision_is_incomplete(revision)
            connection.execute(
                insert(lhb_disclosure_revisions).values(
                    id=uuid4(),
                    source_id=source_id,
                    disclosure_id=revision.disclosure_id,
                    batch_id=batch.batch_id,
                    revision=revision.revision,
                    source_event_at=revision.source_event_at,
                    received_at=revision.received_at,
                    available_at=revision.available_at,
                    permission_snapshot=revision.permission.model_dump(mode="json"),
                    completeness="incomplete" if revision_incomplete else "complete",
                    payload_hash=digest,
                )
            )
            seat_rows = [
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "disclosure_id": revision.disclosure_id,
                    "disclosure_date": revision.disclosure_date,
                    "revision": revision.revision,
                    "source_row_id": row.source_row_id,
                    "seat_id": row.seat_id,
                    "mapping_version": row.mapping_version,
                    "side": row.side,
                    "rank": row.rank,
                    "buy_amount": row.buy_amount,
                    "sell_amount": row.sell_amount,
                    "currency": row.currency,
                    "unit": row.unit,
                    "completeness": _row_completeness(row.buy_amount, row.sell_amount),
                }
                for row in sorted(revision.seats, key=lambda row: row.source_row_id)
            ]
            connection.execute(insert(lhb_disclosure_seats), seat_rows)

    # -- reads -------------------------------------------------------------

    def _authorized_read_sources(
        self,
        connection,
        *,
        operation_now: datetime,
        chain,
        correlation,
        filters: Sequence,
        snapshot,
    ) -> tuple[str, ...]:
        # Structural bounds mirror the realtime authorization read:
        #
        # 1. an uncorrelated EXISTS horizon carries exactly the read-window
        #    predicates and is evaluated once behind a One-Time Filter, so a
        #    window-wide miss skips every per-source probe;
        # 2. each source is a LATERAL ... LIMIT 1 candidate probe and an
        #    outer-joined authorized probe (snapshot permission evaluated
        #    inline), so one bounded probe runs per grant row.
        horizon = select(1).select_from(chain).where(*filters).limit(1).exists()

        def probe(columns, extra_filters, alias):
            return (
                select(*columns)
                .select_from(chain)
                .where(
                    correlation == source_grants.c.source_id,
                    *filters,
                    *extra_filters,
                )
                .limit(1)
                .correlate(source_grants)
                .subquery()
                .lateral(alias)
            )

        candidate = probe((1,), (), "lhb_candidate")
        authorized = probe(
            (true().label("lhb_permission_authorized"),),
            self._snapshot_allows_display(snapshot, operation_now),
            "lhb_authorized",
        )
        query = (
            select(
                source_grants.c.source_id,
                source_grants.c.grant,
                authorized.c.lhb_permission_authorized,
            )
            .select_from(source_grants)
            .join(candidate, true())
            .outerjoin(authorized, true())
            .where(horizon)
        )
        rows = (
            connection.execute(
                query.order_by(source_grants.c.source_id)
                .with_for_update(of=source_grants, read=True)
                .prefix_with("/* lhb_source_authorization */")
            )
            .mappings()
            .all()
        )
        authorized_sources = tuple(
            row["source_id"]
            for row in rows
            if row["lhb_permission_authorized"]
            and grant_allows_display(
                SourceGrant.model_validate(row["grant"]),
                source_id=row["source_id"],
                now=operation_now,
            )
        )
        if rows and not authorized_sources:
            raise Forbidden("Source is not authorized for LHB display")
        return authorized_sources

    @staticmethod
    def _revision_chain():
        revision = lhb_disclosure_revisions.alias("lhb_r")
        disclosure = lhb_disclosures.alias("lhb_d")
        batch = lhb_import_batches.alias("lhb_b")
        chain = revision.join(
            disclosure,
            and_(
                revision.c.source_id == disclosure.c.source_id,
                revision.c.disclosure_id == disclosure.c.disclosure_id,
            ),
        ).join(
            batch,
            and_(
                revision.c.source_id == batch.c.source_id,
                revision.c.batch_id == batch.c.batch_id,
            ),
        )
        return revision, disclosure, batch, chain

    def _fetch_revisions(
        self,
        connection,
        *,
        authorized_sources: Sequence[str],
        operation_now: datetime,
        on_date: date | None = None,
        security_id: str | None = None,
        seat_id: str | None = None,
        window: tuple[date, date] | None = None,
    ):
        revision = lhb_disclosure_revisions.alias("lhb_r")
        disclosure = lhb_disclosures.alias("lhb_d")
        batch = lhb_import_batches.alias("lhb_b")
        seat_row = lhb_disclosure_seats.alias("lhb_sr")
        catalog = lhb_seats.alias("lhb_seat")
        chain = (
            revision.join(
                disclosure,
                and_(
                    revision.c.source_id == disclosure.c.source_id,
                    revision.c.disclosure_id == disclosure.c.disclosure_id,
                ),
            )
            .join(
                batch,
                and_(
                    revision.c.source_id == batch.c.source_id,
                    revision.c.batch_id == batch.c.batch_id,
                ),
            )
            .join(
                seat_row,
                and_(
                    seat_row.c.source_id == revision.c.source_id,
                    seat_row.c.disclosure_id == revision.c.disclosure_id,
                    seat_row.c.revision == revision.c.revision,
                ),
            )
            .join(
                catalog,
                and_(
                    catalog.c.source_id == seat_row.c.source_id,
                    catalog.c.seat_id == seat_row.c.seat_id,
                    catalog.c.mapping_version == seat_row.c.mapping_version,
                ),
            )
        )
        filters = [
            revision.c.source_id.in_(tuple(authorized_sources)),
            *self._snapshot_allows_display(
                revision.c.permission_snapshot, operation_now
            ),
        ]
        if on_date is not None:
            filters.append(disclosure.c.disclosure_date == on_date)
        if security_id is not None:
            filters.append(disclosure.c.security_id == security_id)
        if seat_id is not None:
            filters.append(seat_row.c.seat_id == seat_id)
        if window is not None:
            window_start, window_end = window
            filters.extend(
                (
                    disclosure.c.disclosure_date > window_start,
                    disclosure.c.disclosure_date <= window_end,
                )
            )
        query = (
            select(
                revision.c.id.label("revision_id"),
                revision.c.source_id,
                revision.c.disclosure_id,
                revision.c.revision,
                revision.c.source_event_at,
                revision.c.received_at,
                revision.c.available_at,
                revision.c.permission_snapshot,
                batch.c.source_version,
                batch.c.available_at.label("batch_available_at"),
                disclosure.c.security_id,
                disclosure.c.disclosure_date,
                disclosure.c.reason,
                seat_row.c.source_row_id,
                seat_row.c.seat_id,
                seat_row.c.mapping_version,
                seat_row.c.side,
                seat_row.c.rank,
                seat_row.c.buy_amount,
                seat_row.c.sell_amount,
                seat_row.c.currency,
                seat_row.c.unit,
                catalog.c.source_seat_name,
            )
            .select_from(chain)
            .where(*filters)
            .order_by(
                revision.c.source_id,
                revision.c.disclosure_id,
                revision.c.revision.desc(),
                revision.c.available_at.desc(),
                revision.c.id,
                seat_row.c.source_row_id,
            )
        )
        rows = connection.execute(query).mappings().all()

        revisions: dict[tuple[str, str, int], dict[str, Any]] = {}
        for row in rows:
            key = (row["source_id"], row["disclosure_id"], row["revision"])
            record = revisions.get(key)
            if record is None:
                record = {"meta": row, "seats": []}
                revisions[key] = record
            record["seats"].append(row)
        return revisions

    @staticmethod
    def _build_revision(meta, seat_rows) -> DisclosureRevisionInput:
        return DisclosureRevisionInput(
            disclosure_id=meta["disclosure_id"],
            revision=meta["revision"],
            source_id=meta["source_id"],
            source_version=meta["source_version"],
            security_id=meta["security_id"],
            disclosure_date=meta["disclosure_date"],
            reason=meta["reason"],
            source_event_at=meta["source_event_at"],
            received_at=meta["received_at"],
            available_at=meta["available_at"],
            permission=PermissionSnapshot.model_validate(meta["permission_snapshot"]),
            seats=tuple(
                DisclosureSeatInput(
                    source_row_id=row["source_row_id"],
                    seat_id=row["seat_id"],
                    source_seat_name=row["source_seat_name"],
                    mapping_version=row["mapping_version"],
                    buy_amount=row["buy_amount"],
                    sell_amount=row["sell_amount"],
                    currency=row["currency"],
                    unit=row["unit"],
                    rank=row["rank"],
                    side=row["side"],
                )
                for row in seat_rows
            ),
        )

    @staticmethod
    def _revision_rank(record) -> tuple[int, datetime, str]:
        meta = record["meta"]
        return (meta["revision"], meta["available_at"], str(meta["revision_id"]))

    @staticmethod
    def _is_visible(record, as_of: datetime) -> bool:
        # Point-in-time visibility mirrors the pure aggregate exactly: only the
        # revision's own availability decides. Historical reads cannot see late
        # announcements, including same-day timestamps later than ``as_of``;
        # parent-batch availability never widens the window.
        return record["meta"]["available_at"] <= as_of

    def _select_latest(self, revisions) -> tuple[dict, ...]:
        latest: dict[tuple[str, str], dict] = {}
        for record in revisions.values():
            meta = record["meta"]
            key = (meta["source_id"], meta["disclosure_id"])
            current = latest.get(key)
            if current is None or self._revision_rank(record) > self._revision_rank(
                current
            ):
                latest[key] = record
        return tuple(latest.values())

    def _select_latest_visible(self, revisions, as_of: datetime) -> tuple[dict, ...]:
        # Pick the greatest-rank *visible* revision per (source_id,
        # disclosure_id) from ALL candidates -- never by filtering the overall
        # latest first, or a hidden newer revision would discard an otherwise
        # visible older one. Rank is (revision DESC, available_at DESC, id DESC):
        # the revision number is the authoritative correction sequence.
        latest_visible: dict[tuple[str, str], dict] = {}
        for record in revisions.values():
            if not self._is_visible(record, as_of):
                continue
            meta = record["meta"]
            key = (meta["source_id"], meta["disclosure_id"])
            current = latest_visible.get(key)
            if current is None or self._revision_rank(record) > self._revision_rank(
                current
            ):
                latest_visible[key] = record
        return tuple(latest_visible.values())

    def list_disclosures(self, selection: DisclosureSelection) -> DisclosurePage:
        if not isinstance(selection, DisclosureSelection):
            raise TypeError("selection must be a DisclosureSelection")
        operation_now = self._now()
        as_of = selection.as_of
        if as_of is None:
            as_of = operation_now
        else:
            as_of = as_of.astimezone(UTC)

        revision, disclosure, batch, chain = self._revision_chain()
        filters = [
            disclosure.c.disclosure_date == selection.date,
            revision.c.available_at <= as_of,
        ]
        if selection.security_id is not None:
            filters.append(disclosure.c.security_id == selection.security_id)

        empty = self._empty_page(selection, as_of)
        with self.database.engine.begin() as connection:
            authorized_sources = self._authorized_read_sources(
                connection,
                operation_now=operation_now,
                chain=chain,
                correlation=revision.c.source_id,
                filters=filters,
                snapshot=revision.c.permission_snapshot,
            )
            if not authorized_sources:
                return empty
            revisions = self._fetch_revisions(
                connection,
                authorized_sources=authorized_sources,
                operation_now=operation_now,
                on_date=selection.date,
                security_id=selection.security_id,
            )

        latest_overall = self._select_latest(revisions)
        selected_records = sorted(
            self._select_latest_visible(revisions, as_of),
            key=lambda record: (
                record["meta"]["source_id"],
                record["meta"]["disclosure_id"],
            ),
        )
        selected_revisions = tuple(
            self._build_revision(record["meta"], record["seats"])
            for record in selected_records
        )
        aggregation = aggregate_seats(selected_revisions, as_of)
        coverage = AggregationCoverage(
            total_disclosures=len(latest_overall),
            visible_disclosures=len(selected_revisions),
            late_disclosures=len(latest_overall) - len(selected_revisions),
            selected_disclosures=len(selected_revisions),
            seat_count=aggregation.coverage.seat_count,
            complete_seats=aggregation.coverage.complete_seats,
            unknown_seats=aggregation.coverage.unknown_seats,
        )
        start = (selection.page - 1) * selection.size
        page_records = selected_records[start : start + selection.size]
        items = tuple(
            SelectedDisclosure(
                as_of=as_of,
                revision=self._build_revision(record["meta"], record["seats"]),
            )
            for record in page_records
        )
        return DisclosurePage(
            as_of=as_of,
            items=items,
            total=len(selected_revisions),
            page=selection.page,
            size=selection.size,
            coverage=coverage,
        )

    @staticmethod
    def _empty_page(selection: DisclosureSelection, as_of: datetime) -> DisclosurePage:
        zero = AggregationCoverage(
            total_disclosures=0,
            visible_disclosures=0,
            late_disclosures=0,
            selected_disclosures=0,
            seat_count=0,
            complete_seats=0,
            unknown_seats=0,
        )
        return DisclosurePage(
            as_of=as_of,
            items=(),
            total=0,
            page=selection.page,
            size=selection.size,
            coverage=zero,
        )

    def _anchor_activity_exists(
        self,
        connection,
        *,
        as_of: datetime,
        operation_now: datetime,
        anchor_date: date,
    ) -> bool:
        # Follow-up probe (no authorization comment, no row lock): any visible,
        # currently display-authorized seat activity on the anchor date decides
        # "unknown seat" (NotFound) from "no data at all" (empty history).
        revision, disclosure, batch = self._revision_chain()[:3]
        seat_row = lhb_disclosure_seats.alias("lhb_anchor_sr")
        grant = source_grants.c.grant
        chain = (
            revision.join(
                disclosure,
                and_(
                    revision.c.source_id == disclosure.c.source_id,
                    revision.c.disclosure_id == disclosure.c.disclosure_id,
                ),
            )
            .join(
                batch,
                and_(
                    revision.c.source_id == batch.c.source_id,
                    revision.c.batch_id == batch.c.batch_id,
                ),
            )
            .join(
                seat_row,
                and_(
                    seat_row.c.source_id == revision.c.source_id,
                    seat_row.c.disclosure_id == revision.c.disclosure_id,
                    seat_row.c.revision == revision.c.revision,
                ),
            )
            .join(
                source_grants,
                source_grants.c.source_id == revision.c.source_id,
            )
        )
        query = (
            select(1)
            .select_from(chain)
            .where(
                disclosure.c.disclosure_date == anchor_date,
                revision.c.available_at <= as_of,
                *self._grant_display_predicates(grant, operation_now),
                *self._snapshot_allows_display(
                    revision.c.permission_snapshot, operation_now
                ),
            )
            .limit(1)
        )
        return connection.execute(query).first() is not None

    def _seat_header(
        self, connection, records, selection: SeatHistorySelection
    ) -> SeatIdentity:
        if records:
            header_record = max(
                records,
                key=lambda record: (
                    record["meta"]["available_at"],
                    record["meta"]["revision"],
                ),
            )
            meta = header_record["meta"]
            return SeatIdentity(
                source_id=meta["source_id"],
                seat_id=selection.seat_id,
                source_seat_name=header_record["seats"][0]["source_seat_name"],
                mapping_version=header_record["seats"][0]["mapping_version"],
            )
        catalog = (
            connection.execute(
                select(lhb_seats)
                .where(lhb_seats.c.seat_id == selection.seat_id)
                .order_by(lhb_seats.c.mapping_version.desc(), lhb_seats.c.source_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if catalog is not None:
            return SeatIdentity(
                source_id=catalog["source_id"],
                seat_id=catalog["seat_id"],
                source_seat_name=catalog["source_seat_name"],
                mapping_version=catalog["mapping_version"],
            )
        return SeatIdentity(
            source_id=selection.seat_id,
            seat_id=selection.seat_id,
            source_seat_name=selection.seat_id,
            mapping_version="unknown",
        )

    def get_seat_history(self, selection: SeatHistorySelection) -> SeatHistory:
        if not isinstance(selection, SeatHistorySelection):
            raise TypeError("selection must be a SeatHistorySelection")
        operation_now = self._now()
        as_of = selection.as_of
        if as_of is None:
            as_of = operation_now
        else:
            as_of = as_of.astimezone(UTC)
        window_start = selection.date - timedelta(days=selection.days)

        revision, disclosure, batch, chain = self._revision_chain()
        seat_row = lhb_disclosure_seats.alias("lhb_hist_sr")
        history_chain = chain.join(
            seat_row,
            and_(
                seat_row.c.source_id == revision.c.source_id,
                seat_row.c.disclosure_id == revision.c.disclosure_id,
                seat_row.c.revision == revision.c.revision,
            ),
        )
        filters = [
            seat_row.c.seat_id == selection.seat_id,
            disclosure.c.disclosure_date > window_start,
            disclosure.c.disclosure_date <= selection.date,
            revision.c.available_at <= as_of,
        ]

        with self.database.engine.begin() as connection:
            authorized_sources = self._authorized_read_sources(
                connection,
                operation_now=operation_now,
                chain=history_chain,
                correlation=revision.c.source_id,
                filters=filters,
                snapshot=revision.c.permission_snapshot,
            )
            if not authorized_sources:
                anchor_exists = self._anchor_activity_exists(
                    connection,
                    as_of=as_of,
                    operation_now=operation_now,
                    anchor_date=selection.date,
                )
                header = self._seat_header(connection, (), selection)
                if anchor_exists:
                    raise NotFound("Seat has no history in the selected window")
                return self._empty_history(selection, as_of, header)

            revisions = self._fetch_revisions(
                connection,
                authorized_sources=authorized_sources,
                operation_now=operation_now,
                seat_id=selection.seat_id,
                window=(window_start, selection.date),
            )
            latest_overall = self._select_latest(revisions)
            # Same candidate selection as list_disclosures: greatest visible
            # revision per (source_id, disclosure_id) across ALL candidates.
            visible_records = self._select_latest_visible(revisions, as_of)
            grouped: dict[tuple[str, date], list] = {}
            for record in visible_records:
                meta = record["meta"]
                grouped.setdefault(
                    (meta["source_id"], meta["disclosure_date"]), []
                ).append(record)

            items: list[AggregatedSeat] = []
            # One item per (source, disclosure date): source ascending, then
            # disclosure date descending within each source.
            for source_id, disclosure_date in sorted(
                grouped, key=lambda key: (key[0], -key[1].toordinal())
            ):
                group_records = grouped[(source_id, disclosure_date)]
                group_revisions = tuple(
                    self._build_revision(record["meta"], record["seats"])
                    for record in group_records
                )
                aggregated = aggregate_seats(group_revisions, as_of)
                if aggregated.seats:
                    items.append(aggregated.seats[0])

            if not items:
                anchor_exists = self._anchor_activity_exists(
                    connection,
                    as_of=as_of,
                    operation_now=operation_now,
                    anchor_date=selection.date,
                )
                header = self._seat_header(connection, (), selection)
                if anchor_exists:
                    raise NotFound("Seat has no history in the selected window")
                return self._empty_history(selection, as_of, header)

            header = self._seat_header(connection, visible_records, selection)

        complete_seats = sum(item.completeness == "complete" for item in items)
        coverage = AggregationCoverage(
            total_disclosures=len(latest_overall),
            visible_disclosures=len(visible_records),
            late_disclosures=len(latest_overall) - len(visible_records),
            selected_disclosures=len(visible_records),
            seat_count=len(items),
            complete_seats=complete_seats,
            unknown_seats=len(items) - complete_seats,
        )
        return SeatHistory(
            as_of=as_of,
            seat=header,
            items=tuple(items),
            total=len(items),
            coverage=coverage,
        )

    @staticmethod
    def _empty_history(
        selection: SeatHistorySelection, as_of: datetime, header: SeatIdentity
    ) -> SeatHistory:
        return SeatHistory(
            as_of=as_of,
            seat=header,
            items=(),
            total=0,
            coverage=AggregationCoverage(
                total_disclosures=0,
                visible_disclosures=0,
                late_disclosures=0,
                selected_disclosures=0,
                seat_count=0,
                complete_seats=0,
                unknown_seats=0,
            ),
        )
