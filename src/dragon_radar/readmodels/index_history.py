"""Publication-only index history. No database or quote reconstruction."""

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BeforeValidator,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)

from dragon_radar.domain.models import (
    FrozenModel,
    Identifier,
    IndexBasis,
    MarketBatch,
    Provenance,
    SecurityState,
    UTCDateTime,
)
from dragon_radar.domain.quality import bar_issues, state_at


class IndexHistoryError(ValueError):
    """Invalid present history must fail closed, never become legacy."""


def _day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("History date must be ISO calendar date")
    return date.fromisoformat(value)


def _number(value):
    if not isinstance(value, (str, Decimal)):
        raise ValueError("History numbers require exact decimal strings")
    if isinstance(value, str) and not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        raise ValueError("Invalid decimal representation")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("History numbers must be finite")
    return number


HistoryDate = Annotated[date, BeforeValidator(_day)]
HistoryDecimal = Annotated[Decimal, BeforeValidator(_number)]
Count = Annotated[StrictInt, Field(ge=0)]
ChartType = Literal["candle", "line", "gap"]


def _timestamp(value):
    if isinstance(value, str):
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
            value,
        ):
            raise ValueError("History timestamp must be ISO with timezone")
        _day(value[:10])
    return value


HistoryTimestamp = Annotated[UTCDateTime, BeforeValidator(_timestamp)]


class HistoryCoverage(FrozenModel):
    expected: Count
    valid: Count
    excluded: Annotated[StrictInt, Field(ge=0, le=0)]
    missing: Count

    @model_validator(mode="after")
    def partition(self):
        if self.expected != self.valid + self.excluded + self.missing:
            raise ValueError("Invalid history coverage")
        return self


class HistoryPoint(FrozenModel):
    trade_date: HistoryDate
    open: HistoryDecimal | None
    high: HistoryDecimal | None
    low: HistoryDecimal | None
    close: HistoryDecimal | None
    volume: HistoryDecimal | None
    amount: HistoryDecimal | None
    available_at: HistoryTimestamp | None
    source_id: Identifier
    source_version: Identifier
    reason: Literal["observation_missing"] | None
    chart_type: ChartType
    provenance: Provenance

    @field_serializer(
        "open", "high", "low", "close", "volume", "amount", when_used="json"
    )
    def exact_fixed_point(self, value: Decimal | None):
        return format(value, "f") if value is not None else None

    @model_validator(mode="after")
    def valid_point(self):
        prices = (self.open, self.high, self.low, self.close)
        if any(value is not None and value <= 0 for value in prices):
            raise ValueError("Prices must be positive")
        if any(value is not None and value < 0 for value in (self.volume, self.amount)):
            raise ValueError("Quantity and amount must be nonnegative")
        if self.close is None:
            if (
                any(value is not None for value in (*prices, self.volume, self.amount))
                or self.available_at is not None
                or self.reason != "observation_missing"
                or self.chart_type != "gap"
            ):
                raise ValueError("Invalid missing observation")
        else:
            complete = all(value is not None for value in prices)
            if (
                self.reason is not None
                or self.available_at is None
                or self.chart_type != ("candle" if complete else "line")
                or (not complete and any(value is not None for value in prices[:3]))
            ):
                raise ValueError("Invalid close observation")
            if complete and not (
                self.low
                <= min(self.open, self.close)
                <= max(self.open, self.close)
                <= self.high
            ):
                raise ValueError("Inverted OHLC")
            if (
                self.available_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
                < self.trade_date
            ):
                raise ValueError("Availability precedes session")
        return self


class IndexHistoryEntry(FrozenModel):
    security_id: Identifier
    identity: SecurityState
    index_basis: IndexBasis | None
    source_version: Identifier
    price_type: Literal["raw"]
    adjustment_version: Literal["raw-index-v1"] | None
    archive_start: HistoryDate
    archive_end: HistoryDate
    points: tuple[HistoryPoint, ...]
    coverage: HistoryCoverage | None
    reason: Literal["index_basis_unavailable", "index_history_unavailable"] | None

    @field_validator("identity", mode="before")
    @classmethod
    def strict_identity_dates(cls, value):
        if isinstance(value, Mapping):
            for key in ("effective_from", "effective_to", "listing_date"):
                if value.get(key) is not None:
                    _day(value[key])
            _timestamp(value.get("available_at"))
        return value


class IndexHistoryBlock(FrozenModel):
    schema_name: Literal["dragon-radar.index-history"] = Field(alias="schema")
    schema_version: StrictInt
    market_snapshot_id: UUID
    source_archive_id: UUID
    source_archive_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    trade_date: HistoryDate
    as_of: HistoryTimestamp
    source_id: Identifier
    source_version: Identifier
    pool_version: Identifier
    calendar_version: Identifier
    exchange: Identifier
    calendar_sessions: tuple[HistoryDate, ...]
    archive_start: HistoryDate
    archive_end: HistoryDate
    provenance: Provenance
    indexes: tuple[IndexHistoryEntry, ...]

    @model_validator(mode="after")
    def archive_contract(self):
        sessions = self.calendar_sessions
        if (
            self.schema_version != 1
            or not sessions
            or tuple(sorted(set(sessions))) != sessions
            or sessions[-1] != self.trade_date
            or self.trade_date > self.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
            or self.archive_start != sessions[-250:][0]
            or self.archive_end != self.trade_date
        ):
            raise ValueError("Invalid canonical calendar or archive boundaries")
        ids = tuple(entry.security_id for entry in self.indexes)
        if tuple(sorted(set(ids))) != ids:
            raise ValueError("Index identities must be sorted and unique")
        for entry in self.indexes:
            identity = entry.identity
            if (
                identity.security_id != entry.security_id
                or identity.security_type != "index"
                or identity.source_id != self.source_id
                or identity.source_version != self.source_version
                or identity.pool_version != self.pool_version
                or identity.exchange != self.exchange
                or identity.available_at > self.as_of
                or identity.effective_from > self.trade_date
                or (
                    identity.effective_to is not None
                    and identity.effective_to <= self.trade_date
                )
                or identity.listing_date is None
                or identity.listing_date > self.trade_date
                or identity.suspended is None
                or entry.source_version != self.source_version
                or entry.archive_start != self.archive_start
                or entry.archive_end != self.archive_end
            ):
                raise ValueError("Invalid index identity")
            if entry.index_basis is None:
                if (
                    entry.points
                    or entry.coverage is not None
                    or entry.reason
                    not in {"index_basis_unavailable", "index_history_unavailable"}
                    or entry.adjustment_version is not None
                ):
                    raise ValueError("Invalid unavailable index history")
                continue
            if (
                entry.reason is not None
                or entry.adjustment_version != "raw-index-v1"
                or tuple(point.trade_date for point in entry.points) != sessions[-250:]
                or entry.coverage is None
            ):
                raise ValueError("Index points must partition retained calendar")
            valid = sum(point.close is not None for point in entry.points)
            if entry.coverage != HistoryCoverage(
                expected=len(entry.points),
                valid=valid,
                excluded=0,
                missing=len(entry.points) - valid,
            ):
                raise ValueError("Coverage differs from actual points")
            for point in entry.points:
                if (
                    point.source_id != self.source_id
                    or point.source_version != self.source_version
                    or point.provenance != self.provenance
                    or (
                        point.available_at is not None
                        and point.available_at > self.as_of
                    )
                    or (
                        entry.index_basis.volume_unit == "not_applicable"
                        and point.volume is not None
                    )
                    or (
                        point.amount is not None
                        and entry.index_basis.amount_currency is None
                    )
                ):
                    raise ValueError("Invalid point provenance or units")
        return self


def parse_archived_index_history(value: object) -> IndexHistoryBlock:
    """Validate the entire present block, including unrequested securities."""
    try:
        return IndexHistoryBlock.model_validate(value)
    except (ValueError, TypeError, ArithmeticError):
        raise IndexHistoryError("Invalid archived index history") from None


def materialize_index_history(
    batch: MarketBatch,
    *,
    market_snapshot_id: UUID,
    source_archive_id: UUID,
    source_archive_digest: str,
) -> dict:
    """Build once from the authorized immutable input, retaining calendar gaps."""
    sessions = tuple(day for day in batch.calendar.sessions if day <= batch.trade_date)
    if (
        not sessions
        or sessions[-1] != batch.trade_date
        or batch.calendar_version != batch.calendar.version
    ):
        raise IndexHistoryError("Canonical calendar unavailable")
    days = sessions[-250:]
    identities = []
    for sid in sorted({state.security_id for state in batch.security_states}):
        identity = state_at(batch, sid, batch.trade_date)
        if identity is not None and identity.security_type == "index":
            identities.append(identity)
    entries = []
    for identity in identities:
        observations = {}
        bases = set()
        for bar in batch.bars:
            if bar.security_id != identity.security_id:
                continue
            bar_identity = state_at(batch, bar.security_id, bar.trade_date)
            if (
                bar.trade_date in observations
                or bar_issues(bar, batch)
                or bar_identity is None
                or bar_identity.security_type != "index"
                or bar_identity.suspended
            ):
                raise IndexHistoryError("Invalid supplied index observation")
            observations[bar.trade_date] = bar
            bases.add(bar.index_basis)
        if len(bases) > 1:
            raise IndexHistoryError("Mixed index series")
        basis = next(iter(bases)) if bases else None
        points = []
        if basis is not None:
            for day in days:
                bar = observations.get(day)
                points.append(
                    {
                        "trade_date": day,
                        **{
                            field: getattr(bar, field) if bar else None
                            for field in (
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "amount",
                            )
                        },
                        "available_at": bar.available_at if bar else None,
                        "source_id": batch.source_id,
                        "source_version": batch.source_version,
                        "reason": None if bar else "observation_missing",
                        "chart_type": "candle"
                        if bar and bar.open is not None
                        else "line"
                        if bar
                        else "gap",
                        "provenance": batch.provenance,
                    }
                )
        valid = sum(point["close"] is not None for point in points)
        entries.append(
            {
                "security_id": identity.security_id,
                "identity": identity,
                "index_basis": basis,
                "source_version": batch.source_version,
                "price_type": "raw",
                "adjustment_version": "raw-index-v1" if basis else None,
                "archive_start": days[0],
                "archive_end": days[-1],
                "points": points,
                "coverage": HistoryCoverage(
                    expected=len(points),
                    valid=valid,
                    excluded=0,
                    missing=len(points) - valid,
                )
                if basis
                else None,
                "reason": None
                if basis
                else "index_basis_unavailable"
                if bases
                else "index_history_unavailable",
            }
        )
    block = parse_archived_index_history(
        {
            "schema": "dragon-radar.index-history",
            "schema_version": 1,
            "market_snapshot_id": market_snapshot_id,
            "source_archive_id": source_archive_id,
            "source_archive_digest": source_archive_digest,
            "trade_date": batch.trade_date,
            "as_of": batch.as_of,
            "source_id": batch.source_id,
            "source_version": batch.source_version,
            "pool_version": batch.pool_version,
            "calendar_version": batch.calendar_version,
            "exchange": batch.calendar.exchange,
            "calendar_sessions": sessions,
            "archive_start": days[0],
            "archive_end": days[-1],
            "provenance": batch.provenance,
            "indexes": entries,
        }
    )
    return block.model_dump(mode="json", by_alias=True)


def project_index_history(
    block: IndexHistoryBlock,
    security_id: str,
    start: date,
    end: date,
) -> tuple[dict | None, str | None]:
    """Slice only archived points, snapping bounds on the original PUB calendar."""
    if start > end or end > block.trade_date:
        raise ValueError("Invalid requested boundaries")
    entry = next(
        (item for item in block.indexes if item.security_id == security_id), None
    )
    if entry is None:
        raise KeyError(security_id)
    sessions = tuple(day for day in block.calendar_sessions if start <= day <= end)
    if len(sessions) > 250:
        raise ValueError("History window exceeds 250 sessions")
    if not sessions:
        return None, "no_sessions"
    if entry.index_basis is None:
        return None, "index_history_unavailable"
    points = tuple(
        point
        for point in entry.points
        if sessions[0] <= point.trade_date <= sessions[-1]
    )
    if not points:
        return None, "index_history_unavailable"
    valid = sum(point.close is not None for point in points)
    return {
        "security_id": security_id,
        "identity": entry.identity.model_dump(mode="json"),
        "index_basis": entry.index_basis.model_dump(),
        "price_type": entry.price_type,
        "adjustment_version": entry.adjustment_version,
        "requested": {"start": start, "end": end},
        "adjusted": {"start": sessions[0], "end": sessions[-1]},
        "available": {"start": points[0].trade_date, "end": points[-1].trade_date},
        "archive": {"start": entry.archive_start, "end": entry.archive_end},
        "points": [point.model_dump(mode="json") for point in points],
        "chart_type": "gap"
        if not valid
        else "candle"
        if all(point.chart_type == "candle" for point in points)
        else "line",
        "coverage": HistoryCoverage(
            expected=len(points), valid=valid, excluded=0, missing=len(points) - valid
        ).model_dump(),
        "source": {
            "source_id": block.source_id,
            "source_version": block.source_version,
            "market_snapshot_id": block.market_snapshot_id,
            "source_archive_id": block.source_archive_id,
            "source_archive_digest": block.source_archive_digest,
            "calendar_version": block.calendar_version,
            "as_of": block.as_of,
            "provenance": block.provenance,
        },
    }, None
