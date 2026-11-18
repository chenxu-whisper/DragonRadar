"""Publication-bound, archived topic details only."""

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import empty_envelope
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.market import (
    _published_payload,
    _published_response,
    _repository,
    _selection,
)
from dragon_radar.http.selection import query_integer, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.readmodels.topic_details import parse_archived_topic_details
from dragon_radar.storage.repository import NotFound

router = APIRouter()


@router.get("/api/topics/{sector_id}")
def topic_detail(sector_id: str, request: Request):
    # Rotation owns this literal path, including when it is not publicly mounted.
    if sector_id == "rotation":
        raise NotFound("Reserved topic path")
    query, trade_date, snapshot_id = _selection(request, {"page", "size"})
    page = query_integer(query.get("page", "1"), 1, 2**31 - 1)
    size = query_integer(query.get("size", "50"), 1, 100)
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, materialized = _published_payload(snapshot)
    if "topic_details" not in payload:
        return _published_response(
            snapshot, None, coverage, materialization_unavailable=True
        )
    if not materialized:
        raise DataUnavailable
    try:
        details = parse_archived_topic_details(payload, snapshot)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DataUnavailable from None
    if sector_id not in details:
        raise NotFound("Topic not found")
    detail = details[sector_id]
    offset = (page - 1) * size
    return _published_response(
        snapshot,
        {
            **detail,
            "members": detail["members"][offset : offset + size],
            "page": page,
            "size": size,
        },
        coverage,
    )
