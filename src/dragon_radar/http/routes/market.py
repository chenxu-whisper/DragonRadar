"""Published market read-model routes."""

from decimal import Decimal

from fastapi import APIRouter, Request

from dragon_radar.analytics.metrics import market_breadth
from dragon_radar.http.contracts import (
    archived_coverage,
    empty_envelope,
    snapshot_envelope,
)
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.selection import (
    query_date,
    query_integer,
    query_values,
    select_snapshot,
)
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.http.serialization import restore_decimal_fields as _restore_decimals
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.readmodels.market import (
    MARKET_READ_MODEL_SCHEMA,
    MARKET_READ_MODEL_SCHEMA_VERSION,
)

router = APIRouter()

_LEGACY_OVERVIEW_FIELDS = {
    "advancing",
    "declining",
    "unchanged",
    "returns",
    "exclusions",
}
_MATERIALIZED_VIEW_FIELDS = {
    "overview",
    "sector_flow",
    "sentiment",
    "heatmap",
    "search",
}


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _selection(request: Request, extra: set[str] | None = None):
    query = query_values(request, {"date", "snapshot_id"} | (extra or set()))
    trade_date = query_date(query.get("date"))
    snapshot_id = None
    if "snapshot_id" in query:
        from uuid import UUID

        snapshot_id = UUID(query["snapshot_id"])
    if trade_date is None and snapshot_id is None:
        raise ValueError("A date or snapshot ID is required")
    return query, trade_date, snapshot_id


def _published_payload(snapshot: PublishedSnapshot) -> tuple[dict, dict, bool]:
    stored = snapshot.result("market")
    if stored.status != "ready":
        raise DataUnavailable
    payload = _restore_decimals(dict(stored.payload))
    versioned = (
        payload.get("schema") == MARKET_READ_MODEL_SCHEMA
        and payload.get("schema_version") == MARKET_READ_MODEL_SCHEMA_VERSION
        and _MATERIALIZED_VIEW_FIELDS <= payload.keys()
    )
    unversioned_materialized = (
        "schema" not in payload
        and "schema_version" not in payload
        and _MATERIALIZED_VIEW_FIELDS <= payload.keys()
    )
    legacy_overview = (
        "schema" not in payload
        and "schema_version" not in payload
        and _LEGACY_OVERVIEW_FIELDS <= payload.keys()
        and not _MATERIALIZED_VIEW_FIELDS.intersection(payload)
    )
    if not (versioned or unversioned_materialized or legacy_overview):
        raise DataUnavailable
    return (
        payload,
        archived_coverage(dict(stored.coverage)),
        versioned,
    )


def _published_response(
    snapshot: PublishedSnapshot,
    data: dict | None,
    coverage: dict,
    *,
    materialization_unavailable: bool = False,
):
    payload = snapshot_envelope(snapshot, data, unit="ratio", coverage=coverage)
    if materialization_unavailable:
        payload["quality_flags"] = sorted(
            {*payload["quality_flags"], "materialization_unavailable"}
        )
    return DecimalJSONResponse(payload)


@router.get("/api/market/overview")
def overview(request: Request):
    _query, trade_date, snapshot_id = _selection(request)
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    if isinstance(snapshot, PublishedSnapshot):
        payload, coverage, materialized = _published_payload(snapshot)
        data = (
            payload["overview"]
            if materialized or "overview" in payload
            else {
                **{field: payload[field] for field in _LEGACY_OVERVIEW_FIELDS},
                "coverage": coverage,
            }
        )
        return _published_response(snapshot, data, coverage)

    result = market_breadth(snapshot.batch)
    coverage = archived_coverage(result.coverage.model_dump())
    data = result.model_dump(
        mode="python",
        include={"advancing", "declining", "unchanged", "returns", "exclusions"},
    )
    data["coverage"] = coverage
    return DecimalJSONResponse(
        snapshot_envelope(snapshot, data, unit="ratio", coverage=coverage)
    )


@router.get("/api/market/sector-flow")
def sector_flow(request: Request):
    query, trade_date, snapshot_id = _selection(request, {"top_n"})
    top_n = query_integer(query.get("top_n", "10"), 1, 50)
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, materialized = _published_payload(snapshot)
    if not materialized:
        return _published_response(
            snapshot, None, coverage, materialization_unavailable=True
        )
    sectors = list(payload["sector_flow"]["sectors"])
    sectors.sort(
        key=lambda item: (
            item["change_ratio"] is None,
            -(item["change_ratio"] or Decimal(0)),
            item["sector_id"],
        )
    )
    return _published_response(
        snapshot,
        {
            "sectors": sectors[:top_n],
            "topic_basis": payload["sector_flow"]["topic_basis"],
        },
        coverage,
    )


@router.get("/api/market/sentiment")
def sentiment(request: Request):
    query, trade_date, snapshot_id = _selection(request, {"days"})
    days = query_integer(query.get("days", "20"), 1, 120)
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, materialized = _published_payload(snapshot)
    if not materialized:
        return _published_response(
            snapshot, None, coverage, materialization_unavailable=True
        )
    series = list(payload["sentiment"]["series"])[-days:]
    return _published_response(snapshot, {"series": series}, coverage)


@router.get("/api/market/heatmap")
def heatmap(request: Request):
    query, trade_date, snapshot_id = _selection(request, {"days"})
    days = query_integer(query.get("days", "60"), 1, 250)
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, materialized = _published_payload(snapshot)
    if not materialized:
        return _published_response(
            snapshot, None, coverage, materialization_unavailable=True
        )
    cells = list(payload["heatmap"]["cells"])
    selected_dates = sorted({cell["trade_date"] for cell in cells})[-days:]
    selected = [cell for cell in cells if cell["trade_date"] in selected_dates]
    data = {
        "columns": sorted({cell["boards"] for cell in selected}),
        "cells": selected,
    }
    return _published_response(snapshot, data, coverage)


@router.get("/api/search")
def search(request: Request):
    query = query_values(request, {"q", "date", "snapshot_id"})
    term = query.get("q", "").strip()
    if not 1 <= len(term) <= 50:
        raise ValueError("Search query must be from 1 to 50 characters")
    has_date = "date" in query
    has_snapshot = "snapshot_id" in query
    if has_date != has_snapshot:
        raise ValueError("Historical search requires date and snapshot ID")
    repository = _repository(request)
    if has_date:
        trade_date = query_date(query["date"])
        from uuid import UUID

        snapshot = select_snapshot(repository, trade_date, UUID(query["snapshot_id"]))
    else:
        selected = repository.list_snapshots(limit=1)
        snapshot = selected[0] if selected else None
        trade_date = snapshot.trade_date if snapshot is not None else None
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, materialized = _published_payload(snapshot)
    if not materialized:
        return _published_response(
            snapshot, None, coverage, materialization_unavailable=True
        )
    folded = term.casefold()
    results = []
    for item in payload["search"]["securities"]:
        match_field = next(
            (
                field
                for field in ("security_id", "code", "name")
                if item.get(field) is not None and folded in str(item[field]).casefold()
            ),
            None,
        )
        if match_field is not None:
            results.append({**item, "match_field": match_field})
    data = {
        "query": term,
        "groups": {
            "securities": results,
            "topics": [],
            "messages": [],
        },
        "total": len(results),
    }
    return _published_response(snapshot, data, coverage)
