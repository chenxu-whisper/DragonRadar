"""Authorized point-in-time realtime reads."""

import re
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Request

from dragon_radar.http.errors import StorageUnavailable
from dragon_radar.http.selection import query_date, query_integer, query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.realtime.models import SourceCompleteness
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_SIGNED_CURSOR = re.compile(
    r"(?P<payload>[A-Za-z0-9_-]+)\.(?P<signature>[A-Za-z0-9_-]{43})"
)
_MAX_CURSOR_LENGTH = 4096


def _repository(request: Request):
    repository = request.app.state.realtime_repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _identifier(value: str | None, name: str) -> str:
    if value is None or not value or value.strip() != value or len(value) > 200:
        raise ValueError(f"Invalid {name}")
    return value


def _cursor(value: str) -> str:
    match = _SIGNED_CURSOR.fullmatch(value)
    if (
        not value
        or len(value) > _MAX_CURSOR_LENGTH
        or not value.isascii()
        or match is None
        or len(match["payload"]) % 4 == 1
    ):
        raise ValueError("Invalid cursor")
    return value


def _as_of(value: str | None, repository) -> datetime:
    if value is None:
        current = repository.clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Invalid repository clock")
        return current.astimezone(UTC)
    if _DATETIME.fullmatch(value) is None:
        raise ValueError("Invalid datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Datetime timezone is required")
    return parsed.astimezone(UTC)


def _required_date(value: str | None) -> date:
    parsed = query_date(value)
    if parsed is None:
        raise ValueError("Date is required")
    return parsed


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _batch_selection(
    query: dict[str, str],
    batch_name: str,
) -> tuple[str | None, str | None]:
    source_id = query.get("source_id")
    batch_id = query.get(batch_name)
    if source_id is not None:
        source_id = _identifier(source_id, "source_id")
    if batch_id is not None:
        batch_id = _identifier(batch_id, batch_name)
        if source_id is None:
            raise ValueError("Explicit batch selection requires source_id")
    return source_id, batch_id


def _security_ids(value: str | None) -> tuple[str, ...]:
    if value is None:
        raise ValueError("security_ids is required")
    values = tuple(value.split(","))
    if (
        not 1 <= len(values) <= 100
        or len(set(values)) != len(values)
        or any(not item or item.strip() != item or len(item) > 200 for item in values)
    ):
        raise ValueError("security_ids must be 1-100 canonical unique IDs")
    return values


def _coverage(completeness: SourceCompleteness):
    expected = completeness.expected_count
    if expected is None:
        return None
    return {
        "expected": expected,
        "valid": completeness.received_count,
        "excluded": 0,
        "missing": expected - completeness.received_count,
    }


def _identity(items) -> tuple[str | None, str | None]:
    sources = {item.source_id for item in items}
    versions = {item.source_version for item in items}
    return (
        next(iter(sources)) if len(sources) == 1 else None,
        next(iter(versions)) if len(versions) == 1 else None,
    )


def _envelope(
    result,
    *,
    trade_date: date | None,
    data: dict | None,
    source: str | None,
    source_version: str | None,
    unit: str | None = None,
    currency: str | None = None,
) -> dict:
    completeness = result.completeness
    flags = []
    if completeness.status != "complete":
        flags.append(completeness.reason or completeness.status)
    return {
        "trade_date": trade_date,
        "as_of": result.as_of,
        "snapshot_id": None,
        "source": source,
        "source_version": source_version,
        "coverage": _coverage(completeness),
        "quality_flags": flags,
        "rule_version": None,
        "basis": {
            "unit": unit,
            "currency": currency,
            "price_type": "raw",
            "benchmark": None,
        },
        "data": data,
    }


@router.get("/api/realtime/quotes")
def realtime_quotes(request: Request):
    query = query_values(
        request,
        {"security_ids", "as_of", "source_id", "quote_batch_id"},
    )
    security_ids = _security_ids(query.get("security_ids"))
    source_id, batch_id = _batch_selection(query, "quote_batch_id")
    repository = _repository(request)
    result = repository.list_quotes(
        security_ids=security_ids,
        as_of=_as_of(query.get("as_of"), repository),
        source_id=source_id,
        batch_id=batch_id,
    )
    if not result.items:
        if batch_id is not None or result.batch_id is not None:
            raise NotFound("Realtime quote entity not found")
        return DecimalJSONResponse(
            _envelope(
                result,
                trade_date=None,
                data=None,
                source=source_id,
                source_version=None,
            )
        )
    source, source_version = _identity(result.items)
    first = result.items[0]
    delay_ms = int((first.received_at - first.source_event_at).total_seconds() * 1000)
    if delay_ms < 0:
        raise ArithmeticError("Negative realtime delay")
    data = {
        "quote_batch_id": result.batch_id,
        "source_event_at": first.source_event_at,
        "received_at": first.received_at,
        "delay_ms": delay_ms,
        "status": result.completeness.status,
        "quotes": [item.quote.model_dump() for item in result.items],
    }
    return DecimalJSONResponse(
        _envelope(
            result,
            trade_date=None,
            data=data,
            source=source,
            source_version=source_version,
        )
    )


@router.get("/api/realtime/minutes")
def realtime_minutes(request: Request):
    query = query_values(
        request,
        {"security_id", "date", "as_of", "source_id", "batch_id"},
    )
    security_id = _identifier(query.get("security_id"), "security_id")
    trade_date = _required_date(query.get("date"))
    source_id, batch_id = _batch_selection(query, "batch_id")
    repository = _repository(request)
    session_start, session_end = _day_window(trade_date)
    result = repository.list_minutes(
        security_id=security_id,
        session_start=session_start,
        session_end=session_end,
        as_of=_as_of(query.get("as_of"), repository),
        source_id=source_id,
        batch_id=batch_id,
    )
    if not result.items:
        if batch_id is not None or result.batch_id is not None:
            raise NotFound("Realtime minute entity not found")
        return DecimalJSONResponse(
            _envelope(
                result,
                trade_date=trade_date,
                data=None,
                source=source_id,
                source_version=None,
            )
        )
    source, source_version = _identity(result.items)
    bars = []
    for item in result.items:
        bar = item.bar
        bars.append(
            {
                "batch_id": item.batch_id,
                "source_id": item.source_id,
                "source_version": item.source_version,
                "security_id": bar.security_id,
                "event_at": bar.event_at,
                "received_at": bar.received_at,
                "available_at": bar.available_at,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "revision": bar.revision,
                "final": bar.final,
            }
        )
    return DecimalJSONResponse(
        _envelope(
            result,
            trade_date=trade_date,
            data={"status": result.completeness.status, "bars": bars},
            source=source,
            source_version=source_version,
        )
    )


@router.get("/api/realtime/anomalies")
def realtime_anomalies(request: Request):
    query = query_values(
        request,
        {
            "date",
            "cursor",
            "limit",
            "source_id",
            "security_id",
            "as_of",
        },
    )
    trade_date = _required_date(query.get("date"))
    source_id = query.get("source_id")
    security_id = query.get("security_id")
    if source_id is not None:
        source_id = _identifier(source_id, "source_id")
    if security_id is not None:
        security_id = _identifier(security_id, "security_id")
    cursor = query.get("cursor")
    if cursor is not None:
        cursor = _cursor(cursor)
    repository = _repository(request)
    result = repository.list_anomalies(
        date=trade_date,
        as_of=_as_of(query.get("as_of"), repository),
        limit=query_integer(query.get("limit", "50"), 1, 100),
        cursor=cursor,
        source_id=source_id,
        security_id=security_id,
    )
    if not result.items and result.completeness.status != "complete":
        return DecimalJSONResponse(
            _envelope(
                result,
                trade_date=trade_date,
                data=None,
                source=source_id,
                source_version=None,
            )
        )
    events = [
        {
            "event_id": item.event_id,
            "batch_id": item.batch_id,
            "security_id": item.security_id,
            "rule_version": item.rule_version,
            "event_at": item.event_at,
            "available_at": item.available_at,
            "metric": item.result.metric,
            "window": item.result.window,
            "value": item.result.value,
            "threshold": item.result.threshold,
            "quality": item.quality,
        }
        for item in result.items
    ]
    return DecimalJSONResponse(
        _envelope(
            result,
            trade_date=trade_date,
            data={
                "status": result.completeness.status,
                "events": events,
                "next_cursor": result.next_cursor,
            },
            source=source_id,
            source_version=None,
        )
    )


@router.get("/api/market/sector-flow/intraday")
def realtime_sector_flow(request: Request):
    query = query_values(
        request,
        {"sector", "date", "as_of", "source_id", "batch_id"},
    )
    sector = _identifier(query.get("sector"), "sector")
    trade_date = _required_date(query.get("date"))
    source_id, batch_id = _batch_selection(query, "batch_id")
    repository = _repository(request)
    session_start, session_end = _day_window(trade_date)
    result = repository.list_sector_flow(
        sector_id=sector,
        session_start=session_start,
        session_end=session_end,
        as_of=_as_of(query.get("as_of"), repository),
        source_id=source_id,
        batch_id=batch_id,
    )
    if not result.items:
        if batch_id is not None or result.batch_id is not None:
            raise NotFound("Realtime sector entity not found")
        return DecimalJSONResponse(
            _envelope(
                result,
                trade_date=trade_date,
                data=None,
                source=source_id,
                source_version=None,
            )
        )
    source, source_version = _identity(result.items)
    points = [
        {
            "batch_id": item.batch_id,
            "source_id": item.source_id,
            "source_version": item.source_version,
            "sector": item.point.sector_id,
            "event_at": item.point.event_at,
            "available_at": item.available_at,
            "net_inflow": item.point.net_inflow,
            "unit": item.point.unit,
            "unavailable_reason": item.point.unavailable_reason,
        }
        for item in result.items
    ]
    units = {item.point.unit for item in result.items}
    unit = next(iter(units)) if len(units) == 1 else None
    return DecimalJSONResponse(
        _envelope(
            result,
            trade_date=trade_date,
            data={"status": result.completeness.status, "points": points},
            source=source,
            source_version=source_version,
            unit=unit,
            currency=unit if unit == "CNY" else None,
        )
    )


__all__ = ["router"]
