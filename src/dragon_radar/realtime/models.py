"""Strict immutable contracts for licensed realtime market evidence."""

from datetime import UTC
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


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


Identifier = Annotated[str, AfterValidator(_trim_identifier)]
UTCDateTime = Annotated[
    AwareDatetime,
    AfterValidator(lambda value: value.astimezone(UTC)),
]
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


class RealtimeModel(BaseModel):
    """Base contract for trusted, immutable realtime evidence."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class PermissionSnapshot(RealtimeModel):
    permission_id: Identifier
    state: Literal["authorized", "revoked", "unavailable"]
    captured_at: UTCDateTime


class _TimestampedBatch(RealtimeModel):
    batch_id: Identifier
    source_id: Identifier
    source_version: Identifier
    source_event_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    permission: PermissionSnapshot

    @model_validator(mode="after")
    def validate_timestamp_order_and_permission(self) -> Self:
        if self.received_at < self.source_event_at:
            raise ValueError("received_at must be at or after source_event_at")
        if self.available_at < self.received_at:
            raise ValueError("available_at must be at or after received_at")
        if self.permission.state != "authorized":
            raise ValueError("realtime batch requires an authorized permission")
        return self


class QuoteInput(RealtimeModel):
    security_id: Identifier
    last: NonNegativeDecimal | None = None
    bid: NonNegativeDecimal | None = None
    ask: NonNegativeDecimal | None = None
    bid_size: NonNegativeDecimal | None = None
    ask_size: NonNegativeDecimal | None = None
    volume: NonNegativeDecimal | None = None
    amount: NonNegativeDecimal | None = None
    order_book_status: Literal["available", "disabled", "unavailable"]
    order_book_unavailable_reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_order_book_availability(self) -> Self:
        order_book = (self.bid, self.ask, self.bid_size, self.ask_size)
        if self.order_book_status == "available":
            if any(value is None for value in order_book):
                raise ValueError("available order book requires bid, ask, and sizes")
            if self.order_book_unavailable_reason is not None:
                raise ValueError("available order book must not have a reason")
            return self
        if any(value is not None for value in order_book):
            raise ValueError("nonavailable order book must not contain book values")
        if self.order_book_unavailable_reason is None:
            raise ValueError("nonavailable order book requires a reason")
        return self


class QuoteBatchInput(_TimestampedBatch):
    quotes: tuple[QuoteInput, ...]
    source_sequence: Annotated[int, Field(ge=0)] | None = None


class MinuteBarInput(RealtimeModel):
    security_id: Identifier
    event_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    revision: Annotated[int, Field(ge=1)]
    open: NonNegativeDecimal
    high: NonNegativeDecimal
    low: NonNegativeDecimal
    close: NonNegativeDecimal
    volume: NonNegativeDecimal | None
    amount: NonNegativeDecimal | None
    final: bool

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        if self.received_at < self.event_at:
            raise ValueError("received_at must be at or after event_at")
        if self.available_at < self.received_at:
            raise ValueError("available_at must be at or after received_at")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be the greatest OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be the least OHLC value")
        return self


class _SessionBatch(_TimestampedBatch):
    session_start: UTCDateTime
    session_end: UTCDateTime

    @model_validator(mode="after")
    def validate_session_interval(self) -> Self:
        if self.session_end <= self.session_start:
            raise ValueError("trading session must be nonempty")
        return self


class MinuteBatchInput(_SessionBatch):
    bars: tuple[MinuteBarInput, ...]

    @model_validator(mode="after")
    def validate_bar_sessions(self) -> Self:
        if any(
            bar.event_at < self.session_start or bar.event_at >= self.session_end
            for bar in self.bars
        ):
            raise ValueError("minute bar lies outside the declared trading session")
        return self


class SectorFlowPoint(RealtimeModel):
    sector_id: Identifier
    event_at: UTCDateTime
    net_inflow: FiniteDecimal | None
    unit: Identifier
    unavailable_reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.net_inflow is None and self.unavailable_reason is None:
            raise ValueError("missing flow requires unavailable_reason")
        if self.net_inflow is not None and self.unavailable_reason is not None:
            raise ValueError("available flow must not have unavailable_reason")
        return self


class SectorFlowBatchInput(_SessionBatch):
    points: tuple[SectorFlowPoint, ...]

    @model_validator(mode="after")
    def validate_point_sessions(self) -> Self:
        if any(
            point.event_at < self.session_start or point.event_at >= self.session_end
            for point in self.points
        ):
            raise ValueError("sector flow lies outside the declared trading session")
        return self


class AnomalyRule(RealtimeModel):
    rule_version: Identifier
    metric: Literal[
        "window_return",
        "relative_volume",
        "order_book_imbalance",
    ]
    window: Annotated[int, Field(ge=2, le=120)]
    threshold: FiniteDecimal
    cooldown_seconds: Annotated[int, Field(ge=0, le=86400)]


class AnomalyResult(RealtimeModel):
    triggered: bool
    metric: Literal[
        "window_return",
        "relative_volume",
        "order_book_imbalance",
    ]
    window: Annotated[int, Field(ge=2, le=120)]
    value: FiniteDecimal | None
    threshold: FiniteDecimal
    reason: Identifier | None


class SourceCompleteness(RealtimeModel):
    status: Literal["complete", "delayed", "incomplete", "unavailable"]
    expected_count: Annotated[int, Field(ge=0)] | None = None
    received_count: Annotated[int, Field(ge=0)]
    reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_counts_and_reason(self) -> Self:
        if (
            self.expected_count is not None
            and self.received_count > self.expected_count
        ):
            raise ValueError("received_count cannot exceed expected_count")
        if self.status == "complete" and self.reason is not None:
            raise ValueError("complete evidence must not have a reason")
        if self.status != "complete" and self.reason is None:
            raise ValueError("non-complete evidence requires a reason")
        return self


class QuoteRecord(RealtimeModel):
    batch_id: Identifier
    source_id: Identifier
    source_version: Identifier
    source_event_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    quote: QuoteInput

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> Self:
        if self.received_at < self.source_event_at:
            raise ValueError("received_at must be at or after source_event_at")
        if self.available_at < self.received_at:
            raise ValueError("available_at must be at or after received_at")
        return self


class MinuteBarRecord(RealtimeModel):
    batch_id: Identifier
    source_id: Identifier
    source_version: Identifier
    bar: MinuteBarInput


class AnomalyEvent(RealtimeModel):
    event_id: Identifier
    batch_id: Identifier
    security_id: Identifier
    rule_version: Identifier
    event_at: UTCDateTime
    available_at: UTCDateTime
    result: AnomalyResult
    quality: Literal["complete", "delayed", "incomplete"]


class SectorFlowRecord(RealtimeModel):
    batch_id: Identifier
    source_id: Identifier
    source_version: Identifier
    available_at: UTCDateTime
    point: SectorFlowPoint


class QuoteReadResult(RealtimeModel):
    as_of: UTCDateTime
    batch_id: Identifier | None
    items: tuple[QuoteRecord, ...]
    completeness: SourceCompleteness


class MinuteReadResult(RealtimeModel):
    as_of: UTCDateTime
    batch_id: Identifier | None
    items: tuple[MinuteBarRecord, ...]
    completeness: SourceCompleteness


class AnomalyReadResult(RealtimeModel):
    as_of: UTCDateTime
    items: tuple[AnomalyEvent, ...]
    next_cursor: str | None
    completeness: SourceCompleteness


class SectorFlowReadResult(RealtimeModel):
    as_of: UTCDateTime
    batch_id: Identifier | None
    items: tuple[SectorFlowRecord, ...]
    completeness: SourceCompleteness


class RealtimeImportResult(RealtimeModel):
    import_id: Identifier
    batch_id: Identifier
    status: Literal["accepted", "duplicate", "rejected", "incomplete"]
    payload_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    imported_at: UTCDateTime
    reason: Identifier | None = None


__all__ = [
    "AnomalyEvent",
    "AnomalyReadResult",
    "AnomalyResult",
    "AnomalyRule",
    "MinuteBarInput",
    "MinuteBarRecord",
    "MinuteBatchInput",
    "MinuteReadResult",
    "PermissionSnapshot",
    "QuoteBatchInput",
    "QuoteInput",
    "QuoteReadResult",
    "QuoteRecord",
    "RealtimeImportResult",
    "SectorFlowBatchInput",
    "SectorFlowPoint",
    "SectorFlowReadResult",
    "SectorFlowRecord",
    "SourceCompleteness",
]
