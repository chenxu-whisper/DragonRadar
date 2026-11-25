"""Exact PUB-only, licensed archived index history reads."""

from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.domain.quality import state_at
from dragon_radar.http.contracts import snapshot_envelope
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.market import _published_payload, _repository
from dragon_radar.http.selection import query_date, query_values, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.readmodels.index_history import (
    IndexHistoryError,
    parse_archived_index_history,
    project_index_history,
)
from dragon_radar.storage.repository import NotFound

router = APIRouter()


@router.get("/api/index/{security_id}/history")
def index_history(request: Request, security_id: str):
    query = query_values(request, {"date", "snapshot_id", "start", "end", "adj"})
    if (
        not {"date", "snapshot_id", "start", "end"} <= query.keys()
        or query.get("adj", "raw") != "raw"
        or not security_id.strip()
        or security_id in {".", ".."}
        or any(character in security_id for character in "/\\?#")
    ):
        raise ValueError("Invalid index history selection")
    trade_date = query_date(query["date"])
    start, end = query_date(query["start"]), query_date(query["end"])
    snapshot_id = UUID(query["snapshot_id"])
    if start > end or end > trade_date:
        raise ValueError("Invalid history window")
    # Repository selection rechecks embedded/current grants and withdrawal
    # dependencies. The public facade applies sharing, never local display.
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if not isinstance(snapshot, PublishedSnapshot):
        raise NotFound("Published snapshot required")
    payload, _coverage, _versioned = _published_payload(snapshot)
    identity = state_at(snapshot.batch, security_id, snapshot.trade_date)
    if identity is None or identity.security_type != "index":
        raise NotFound("Index not found")
    reason = None
    data = None
    if "index_history" not in payload:
        reason = "materialization_unavailable"
    else:
        try:
            # The shared market decoder restores Decimal strings permissively.
            # This block must validate original archived representations instead.
            block = parse_archived_index_history(
                snapshot.result("market").payload["index_history"]
            )
            if (
                block.market_snapshot_id != snapshot.market_snapshot.id
                or block.source_archive_id != snapshot.batch_id
                or block.source_archive_digest != snapshot.manifest.get("payload_hash")
                or block.trade_date != snapshot.trade_date
                or block.as_of != snapshot.batch.as_of
                or block.source_id != snapshot.batch.source_id
                or block.source_version != snapshot.batch.source_version
                or block.pool_version != snapshot.batch.pool_version
                or block.calendar_version != snapshot.batch.calendar_version
                or block.exchange != snapshot.batch.calendar.exchange
                or block.provenance != snapshot.batch.provenance
                or block.calendar_sessions
                != tuple(
                    day
                    for day in snapshot.batch.calendar.sessions
                    if day <= snapshot.trade_date
                )
                or any(
                    entry.identity
                    != state_at(snapshot.batch, entry.security_id, snapshot.trade_date)
                    for entry in block.indexes
                )
            ):
                raise IndexHistoryError("History belongs to a different input")
            if not any(entry.security_id == security_id for entry in block.indexes):
                reason = "index_history_unavailable"
            else:
                data, reason = project_index_history(block, security_id, start, end)
        except IndexHistoryError:
            raise DataUnavailable from None
    envelope = snapshot_envelope(
        snapshot,
        data,
        unit="point",
        coverage=data["coverage"] if data else None,
    )
    # Stock currencies cannot establish index amount currency.
    envelope["basis"]["currency"] = (
        data["index_basis"]["amount_currency"] if data else None
    )
    if data is None:
        envelope["coverage"] = None
    if reason:
        envelope["quality_flags"] = sorted({*envelope["quality_flags"], reason})
        envelope["reason"] = reason
    return DecimalJSONResponse(envelope)
