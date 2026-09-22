"""Strict query parsing before any storage access, with immutable ID selection."""

import re
from datetime import date
from uuid import UUID

from fastapi import Request

from dragon_radar.storage.models import Snapshot
from dragon_radar.storage.repository import Repository


def query_values(request: Request, allowed: set[str]) -> dict[str, str]:
    query = request.query_params
    if any(key not in allowed or len(query.getlist(key)) != 1 for key in query):
        raise ValueError("Invalid query")
    return dict(query)


def query_date(value: str | None) -> date | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Invalid date")
    return date.fromisoformat(value)


def query_integer(value: str, minimum: int, maximum: int) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Invalid integer")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError("Integer outside bounds")
    return result


def selectors(request: Request) -> tuple[date | None, UUID | None]:
    query = query_values(request, {"date", "snapshot_id"})
    trade_date = query_date(query.get("date"))
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    if trade_date is None and snapshot_id is None:
        raise ValueError("A date or snapshot ID is required")
    return trade_date, snapshot_id


def select_snapshot(
    repository: Repository,
    trade_date: date | None,
    snapshot_id: UUID | None,
    *,
    through_date: date | None = None,
) -> Snapshot | None:
    if snapshot_id is not None:
        snapshot = repository.get_snapshot(snapshot_id)
        if snapshot is None:
            return None
        if trade_date is not None and snapshot.trade_date != trade_date:
            raise ValueError("Snapshot date mismatch")
        return snapshot
    filters = {"trade_date": trade_date, "limit": 1}
    if through_date is not None:
        filters["through_date"] = through_date
    snapshots = repository.list_snapshots(**filters)
    return snapshots[0] if snapshots else None
