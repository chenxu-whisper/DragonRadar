"""Immutable import contracts; business data errors belong in quality reports.

Decimal inputs accept Decimal, integer, or exact decimal strings, never floats.
Nonfinite Decimal inputs are retained for quarantine, NOT eligible analytics.
JSON serialization preserves them as strings. Use model_validate[_json] at the
trust boundary, not Pydantic's unchecked model_construct/model_copy(update=...).
All effective intervals are [effective_from, effective_to), with None unbounded.
Availability and grant timestamps normalize to UTC. Unknown state is never False.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)


def _decimal_input(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("Decimal inputs must not be float or bool")
    return value


def _datetime_input(value: object) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError(
            "Timestamp must be an aware datetime or ISO string with timezone"
        )
    return value


ExactDecimal = Annotated[Decimal, BeforeValidator(_decimal_input)]
UTCDateTime = Annotated[
    AwareDatetime,
    BeforeValidator(_datetime_input),
    AfterValidator(lambda v: v.astimezone(UTC)),
]
Identifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
Provenance = Literal["contemporaneous", "retrospective"]


class FrozenModel(BaseModel):
    """Frozen nested contracts. Collections must be tuples, not mutable lists."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=True)


class SourceGrant(FrozenModel):
    """Fail-closed rights; check again with operation-time UTC before any use."""

    source_id: Identifier
    approved: StrictBool = False
    reference: Identifier | None = None
    starts_at: UTCDateTime | None = None
    expires_at: UTCDateTime | None = None
    local_storage: StrictBool = False
    local_display: StrictBool = False
    export: StrictBool = False
    ai: StrictBool = False
    sharing: StrictBool = False


class ExchangeCalendar(FrozenModel):
    """Explicit exchange sessions; no weekend/holiday inference or sorting."""

    exchange: Identifier
    version: Identifier
    sessions: tuple[date, ...]

    @field_validator("sessions")
    @classmethod
    def ordered_sessions(cls, sessions: tuple[date, ...]) -> tuple[date, ...]:
        if not sessions or tuple(sorted(set(sessions))) != sessions:
            raise ValueError("sessions must be nonempty, sorted and unique")
        return sessions


class SecurityState(FrozenModel):
    """Versioned, point-in-time identity/state; None means not known."""

    security_id: Identifier
    name: Identifier
    board: Identifier
    exchange: Identifier
    effective_from: date
    effective_to: date | None = None
    available_at: UTCDateTime
    listing_date: date | None = None
    suspended: StrictBool | None = None
    is_st: StrictBool | None = None
    source_id: Identifier
    source_version: Identifier
    pool_version: Identifier


class DailyBar(FrozenModel):
    """Raw daily prices, not adjusted OHLC. Missing close is quarantinable.

    Supply all open/high/low or none for close-only data. Prices are currency
    per share; volume and turnover retain declared units. The provided factor
    multiplies raw close. Limits apply ONLY to limit_date == trade_date;
    unknown limit metadata is not equivalent to an explicit no-limit session.
    previous_close is the provider's raw daily-return reference, when supplied.
    """

    security_id: Identifier
    trade_date: date
    available_at: UTCDateTime
    source_id: Identifier
    source_version: Identifier
    close: ExactDecimal | None
    open: ExactDecimal | None = None
    high: ExactDecimal | None = None
    low: ExactDecimal | None = None
    previous_close: ExactDecimal | None = None
    adjustment_factor: ExactDecimal
    adjustment_version: Identifier
    currency: Identifier
    price_unit: Literal["currency/share"]
    volume_unit: Literal["share", "lot"]
    amount_unit: Literal["currency"]
    volume: ExactDecimal | None = None
    amount: ExactDecimal | None = None
    limit_status: Literal["unknown", "limited", "no_limit"] = "unknown"
    limit_date: date | None = None
    limit_up: ExactDecimal | None = None
    limit_down: ExactDecimal | None = None


class MarketBatch(FrozenModel):
    """Self-contained snapshot archive input, including all supplied history.

    provenance is mandatory. A retrospective backfill normally uses its actual
    collection as_of; it does not authorize using observations after as_of.
    expected_security_ids defines the current denominator, not inferred bars.
    """

    trade_date: date
    as_of: UTCDateTime
    source_time: UTCDateTime | None = None
    source_id: Identifier
    source_version: Identifier
    adjustment_version: Identifier
    calendar_version: Identifier
    pool_version: Identifier
    grant: SourceGrant
    calendar: ExchangeCalendar
    expected_security_ids: tuple[Identifier, ...]
    security_states: tuple[SecurityState, ...]
    bars: tuple[DailyBar, ...]
    complete_pages: StrictBool
    provenance: Provenance
