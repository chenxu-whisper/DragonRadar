"""Strict immutable contracts for 龙虎榜 (LHB) disclosure evidence.

LHB owns its permission and seat contracts: nothing here is imported from the
realtime package. ``FrozenModel`` and ``UTCDateTime`` are reused from
``dragon_radar.domain.models``; the identifier, text, and Decimal annotated
helpers below are LHB-owned copies of the realtime trust-boundary pattern.
Unknown amounts stay ``None`` and are never coerced to zero.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from dragon_radar.domain.models import FrozenModel, UTCDateTime


def _trim_identifier(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("identifier must not be blank")
    return trimmed


def _decimal_input(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("Decimal inputs must not be float or bool")
    return value


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Decimal values must be finite")
    return value


def _nonnegative(value: Decimal) -> Decimal:
    value = _finite(value)
    if value < 0:
        raise ValueError("Decimal values must be nonnegative")
    return value


SafeIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
NonBlankText = Annotated[str, AfterValidator(_trim_identifier)]
FiniteDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal_input),
    AfterValidator(_finite),
]
NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal_input),
    AfterValidator(_nonnegative),
]
SeatSide = Literal["buy", "sell", "net"]
PermissionState = Literal["authorized", "revoked", "unavailable"]
SeatCompleteness = Literal["complete", "buy_unknown", "sell_unknown", "both_unknown"]
ImportStatus = Literal["accepted", "duplicate", "rejected", "incomplete"]


class LHBModel(FrozenModel):
    """Base contract for trusted, immutable LHB evidence."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class _TimestampOrdered(LHBModel):
    """Shared source-event, receipt, availability, and permission ordering."""

    @model_validator(mode="after")
    def validate_timestamp_order_and_permission(self) -> Self:
        if self.received_at < self.source_event_at:
            raise ValueError("received_at must be at or after source_event_at")
        if self.available_at < self.received_at:
            raise ValueError("available_at must be at or after received_at")
        if self.permission.state != "authorized":
            raise ValueError("LHB input requires an authorized permission")
        return self


class PermissionSnapshot(LHBModel):
    """LHB-owned source permission; read-side revoked states arrive later."""

    permission_id: SafeIdentifier
    state: PermissionState
    captured_at: UTCDateTime


class DisclosureSeatInput(LHBModel):
    """One merged seat row within a disclosure revision.

    The canonical input adapter merges the source's separate buy/sell top-5
    lists upstream, so one ``seat_id`` appears exactly once per revision with
    both amounts (either of which may still be unknown). ``side`` is display-only
    metadata for that merged row: it never changes null semantics, and a missing
    amount stays unknown regardless of ``side``.

    ``source_seat_name`` is an arbitrary non-blank display string. It is never a
    natural-person or fund attribution, and no such extra keys are accepted.
    """

    source_row_id: SafeIdentifier
    seat_id: SafeIdentifier
    source_seat_name: NonBlankText
    mapping_version: SafeIdentifier
    buy_amount: NonNegativeDecimal | None
    sell_amount: NonNegativeDecimal | None
    currency: NonBlankText
    unit: NonBlankText
    rank: Annotated[int, Field(ge=1)] | None = None
    side: SeatSide | None = None


class DisclosureRevisionInput(_TimestampOrdered):
    """An immutable, point-in-time revision of one LHB disclosure.

    A revision always carries at least one seat row.
    """

    disclosure_id: SafeIdentifier
    revision: Annotated[int, Field(ge=1)]
    source_id: SafeIdentifier
    source_version: SafeIdentifier
    security_id: SafeIdentifier
    disclosure_date: date
    reason: NonBlankText
    source_event_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    permission: PermissionSnapshot
    seats: tuple[DisclosureSeatInput, ...] = Field(min_length=1)


class LHBImportBatch(_TimestampOrdered):
    """Authorized import envelope carrying one or more disclosure revisions."""

    batch_id: SafeIdentifier
    source_id: SafeIdentifier
    source_version: SafeIdentifier
    source_event_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    permission: PermissionSnapshot
    disclosures: tuple[DisclosureRevisionInput, ...] = Field(min_length=1)


class LHBImportResult(LHBModel):
    import_id: SafeIdentifier
    batch_id: SafeIdentifier
    status: ImportStatus
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    imported_at: UTCDateTime
    reason: NonBlankText | None = None


class SeatIdentity(LHBModel):
    """Stable, source-scoped seat catalog identity."""

    source_id: SafeIdentifier
    seat_id: SafeIdentifier
    source_seat_name: NonBlankText
    mapping_version: SafeIdentifier


class SeatContribution(LHBModel):
    """The disclosure revision row that contributed amounts to one seat.

    Per-row source name, mapping version, and permission state stay traceable
    even when the seat was renamed or remapped after the fact.

    ``permission_state`` is the *captured* state copied verbatim from that
    revision's permission snapshot at import time. Aggregation only records
    evidence: it cannot evaluate read-time revocation (which the read-side
    repository enforces separately), so a captured non-authorized state is
    propagated rather than filtered or raised on here.
    """

    disclosure_id: SafeIdentifier
    security_id: SafeIdentifier
    revision: Annotated[int, Field(ge=1)]
    disclosure_date: date
    reason: NonBlankText
    source_row_id: SafeIdentifier
    source_seat_name: NonBlankText
    mapping_version: SafeIdentifier
    permission_state: PermissionState


class AggregatedSeat(LHBModel):
    """Null-safe aggregation of one stable seat across selected revisions."""

    seat_id: SafeIdentifier
    source_id: SafeIdentifier
    source_version: SafeIdentifier
    mapping_version: SafeIdentifier
    source_seat_name: NonBlankText
    currency: NonBlankText
    unit: NonBlankText
    buy_amount: NonNegativeDecimal | None
    sell_amount: NonNegativeDecimal | None
    net_amount: FiniteDecimal | None
    completeness: SeatCompleteness
    contributions: tuple[SeatContribution, ...]


class AggregationCoverage(LHBModel):
    """Deterministic counts describing how the aggregation was produced."""

    total_disclosures: Annotated[int, Field(ge=0)]
    visible_disclosures: Annotated[int, Field(ge=0)]
    late_disclosures: Annotated[int, Field(ge=0)]
    selected_disclosures: Annotated[int, Field(ge=0)]
    seat_count: Annotated[int, Field(ge=0)]
    complete_seats: Annotated[int, Field(ge=0)]
    unknown_seats: Annotated[int, Field(ge=0)]


class SeatAggregation(LHBModel):
    as_of: UTCDateTime
    seats: tuple[AggregatedSeat, ...]
    coverage: AggregationCoverage


class SelectedDisclosure(LHBModel):
    """One revision selected for a point-in-time disclosure read."""

    as_of: UTCDateTime
    revision: DisclosureRevisionInput


class DisclosureSelection(LHBModel):
    """Bounded selection for the disclosure list endpoint."""

    date: date
    security_id: SafeIdentifier | None = None
    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=100)] = 20
    as_of: UTCDateTime | None = None


class DisclosurePage(LHBModel):
    """Paginated disclosure-list envelope."""

    as_of: UTCDateTime
    items: tuple[SelectedDisclosure, ...]
    total: Annotated[int, Field(ge=0)]
    page: Annotated[int, Field(ge=1)]
    size: Annotated[int, Field(ge=1, le=100)]
    coverage: AggregationCoverage


class SeatHistorySelection(LHBModel):
    """Bounded selection for the seat-history endpoint."""

    seat_id: SafeIdentifier
    date: date
    days: Annotated[int, Field(ge=1, le=250)] = 30
    as_of: UTCDateTime | None = None


class SeatHistory(LHBModel):
    """Seat-history envelope keyed by stable seat identity."""

    as_of: UTCDateTime
    seat: SeatIdentity
    items: tuple[AggregatedSeat, ...]
    total: Annotated[int, Field(ge=0)]
    coverage: AggregationCoverage


__all__ = [
    "AggregatedSeat",
    "AggregationCoverage",
    "DisclosurePage",
    "DisclosureRevisionInput",
    "DisclosureSeatInput",
    "DisclosureSelection",
    "LHBImportBatch",
    "LHBImportResult",
    "PermissionSnapshot",
    "SeatAggregation",
    "SeatContribution",
    "SeatHistory",
    "SeatHistorySelection",
    "SeatIdentity",
    "SelectedDisclosure",
]
