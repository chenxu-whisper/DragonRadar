"""Authorized, point-in-time, read-only 龙虎榜 (LHB) contracts."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Request

from dragon_radar.http.errors import StorageUnavailable
from dragon_radar.http.selection import query_date, query_integer, query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.lhb.models import (
    DisclosurePage,
    DisclosureSeatInput,
    DisclosureSelection,
    SeatHistory,
    SeatHistorySelection,
    SelectedDisclosure,
)

router = APIRouter()

_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
_MAX_PAGE = 10_000


def _repository(request: Request):
    repository = request.app.state.lhb_repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _identifier(value: str, name: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"Invalid {name}")
    return value


def _as_of(value: str | None) -> datetime | None:
    # Absent as_of stays None: the repository fixes the point in time from
    # its own clock. Present values must be strict ISO-8601 with a timezone.
    if value is None:
        return None
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


def _row_net(buy_amount: Decimal | None, sell_amount: Decimal | None) -> Decimal | None:
    # Unknown amounts stay null: null never collapses into a numeric zero.
    if buy_amount is None or sell_amount is None:
        return None
    return buy_amount - sell_amount


def _row_completeness(buy_amount: Decimal | None, sell_amount: Decimal | None) -> str:
    if buy_amount is None and sell_amount is None:
        return "both_unknown"
    if buy_amount is None:
        return "buy_unknown"
    if sell_amount is None:
        return "sell_unknown"
    return "complete"


def _seat_row(row: DisclosureSeatInput) -> dict:
    return {
        "source_row_id": row.source_row_id,
        "seat_id": row.seat_id,
        "source_seat_name": row.source_seat_name,
        "mapping_version": row.mapping_version,
        "side": row.side,
        "rank": row.rank,
        "buy_amount": row.buy_amount,
        "sell_amount": row.sell_amount,
        "currency": row.currency,
        "unit": row.unit,
        "completeness": _row_completeness(row.buy_amount, row.sell_amount),
        "net_amount": _row_net(row.buy_amount, row.sell_amount),
    }


def _disclosure_item(selected: SelectedDisclosure) -> dict:
    revision = selected.revision
    return {
        "disclosure_id": revision.disclosure_id,
        "security_id": revision.security_id,
        "disclosure_date": revision.disclosure_date,
        "reason": revision.reason,
        "revision": revision.revision,
        "source_id": revision.source_id,
        "source_version": revision.source_version,
        "source_event_at": revision.source_event_at,
        "received_at": revision.received_at,
        "available_at": revision.available_at,
        "seats": [_seat_row(row) for row in revision.seats],
    }


def _single(values: set) -> str | None:
    return next(iter(values)) if len(values) == 1 else None


def _basis(currencies: set[str], units: set[str]) -> dict:
    return {"unit": _single(units), "currency": _single(currencies)}


def _empty_basis() -> dict:
    return {"unit": None, "currency": None}


def serialize_disclosure_page(page: DisclosurePage, *, date: date) -> dict:
    """Flatten a repository ``DisclosurePage`` into the locked EX08 wire form.

    Only a typed model instance is accepted; a plain dict is a programming
    error, not an empty result.
    """
    if not isinstance(page, DisclosurePage):
        raise TypeError("page must be a DisclosurePage")
    items = [_disclosure_item(selected) for selected in page.items]
    if page.total == 0:
        source = None
        source_version = None
        basis = _empty_basis()
        quality_flags = ["no_data"]
    else:
        revisions = tuple(selected.revision for selected in page.items)
        source = _single({revision.source_id for revision in revisions})
        source_version = _single({revision.source_version for revision in revisions})
        rows = [row for item in items for row in item["seats"]]
        basis = _basis(
            {row["currency"] for row in rows},
            {row["unit"] for row in rows},
        )
        quality_flags = []
    return {
        "date": date,
        "as_of": page.as_of,
        "source": source,
        "source_version": source_version,
        "quality_flags": quality_flags,
        "basis": basis,
        "coverage": page.coverage.model_dump(),
        "data": {
            "items": items,
            "total": page.total,
            "page": page.page,
            "size": page.size,
        },
    }


def serialize_seat_history(history: SeatHistory, *, date: date) -> dict:
    """Flatten a repository ``SeatHistory`` into the locked EX09 wire form."""
    if not isinstance(history, SeatHistory):
        raise TypeError("history must be a SeatHistory")
    if history.total == 0:
        source = None
        source_version = None
        basis = _empty_basis()
        quality_flags = ["no_data"]
    else:
        source = _single({item.source_id for item in history.items})
        source_version = _single({item.source_version for item in history.items})
        basis = _basis(
            {item.currency for item in history.items},
            {item.unit for item in history.items},
        )
        quality_flags = []
    return {
        "date": date,
        "as_of": history.as_of,
        "source": source,
        "source_version": source_version,
        "quality_flags": quality_flags,
        "basis": basis,
        "coverage": history.coverage.model_dump(),
        "data": {
            "seat": history.seat.model_dump(),
            "items": [item.model_dump() for item in history.items],
            "total": history.total,
        },
    }


@router.get("/api/lhb")
def lhb_disclosures(request: Request):
    query = query_values(
        request,
        {"date", "security_id", "page", "size", "as_of"},
    )
    trade_date = _required_date(query.get("date"))
    security_id = query.get("security_id")
    if security_id is not None:
        security_id = _identifier(security_id, "security_id")
    page = query_integer(query.get("page", "1"), 1, _MAX_PAGE)
    size = query_integer(query.get("size", "20"), 1, 100)
    as_of = _as_of(query.get("as_of"))
    repository = _repository(request)
    result = repository.list_disclosures(
        DisclosureSelection(
            date=trade_date,
            security_id=security_id,
            page=page,
            size=size,
            as_of=as_of,
        )
    )
    return DecimalJSONResponse(serialize_disclosure_page(result, date=trade_date))


@router.get("/api/lhb/seats/{seat_id}")
def lhb_seat_history(seat_id: str, request: Request):
    seat_id = _identifier(seat_id, "seat_id")
    query = query_values(request, {"date", "days", "as_of"})
    trade_date = _required_date(query.get("date"))
    days = query_integer(query.get("days", "30"), 1, 250)
    as_of = _as_of(query.get("as_of"))
    repository = _repository(request)
    result = repository.get_seat_history(
        SeatHistorySelection(
            seat_id=seat_id,
            date=trade_date,
            days=days,
            as_of=as_of,
        )
    )
    return DecimalJSONResponse(serialize_seat_history(result, date=trade_date))


__all__ = ["router", "serialize_disclosure_page", "serialize_seat_history"]
