"""Published stock detail and raw K-line routes."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import coverage_view, empty_envelope, snapshot_envelope
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.routes.market import _published_payload
from dragon_radar.http.selection import query_date, query_values, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.roles.rules import ROLE_NAMES
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_ADJUSTMENTS = frozenset({"raw", "qfq", "hfq"})


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _snapshot(
    request: Request,
    trade_date: date | None,
    snapshot_id: UUID | None,
):
    selected = select_snapshot(
        _repository(request),
        trade_date,
        snapshot_id,
    )
    if selected is None or not isinstance(selected, PublishedSnapshot):
        return None
    return selected


def _market_extensions(snapshot: PublishedSnapshot):
    payload, coverage, materialized = _published_payload(snapshot)
    analysis = payload.get("analysis")
    stocks = payload.get("stocks")
    if (
        not materialized
        or not isinstance(analysis, dict)
        or analysis.get("schema") != "dragon-radar.analysis"
        or analysis.get("schema_version") != 1
        or not isinstance(stocks, dict)
        or stocks.get("schema") != "dragon-radar.stocks"
        or stocks.get("schema_version") != 1
        or not isinstance(stocks.get("stocks"), dict)
    ):
        return None, None, coverage
    return analysis, stocks, coverage


def _response(
    snapshot: PublishedSnapshot,
    data,
    coverage,
    *,
    adjustment: str = "raw",
    benchmark: str | None = None,
    reason: str | None = None,
    rule_version: str | None = None,
    unit: str | None = "ratio",
    quality_flags=(),
):
    payload = snapshot_envelope(snapshot, data, unit=unit, coverage=coverage)
    payload["basis"]["price_type"] = adjustment
    payload["basis"]["benchmark"] = benchmark
    if rule_version is not None:
        payload["rule_version"] = rule_version
    payload["quality_flags"] = sorted({*payload["quality_flags"], *quality_flags})
    if reason is not None:
        payload["reason"] = reason
    return DecimalJSONResponse(payload)


def _detail_query(request: Request):
    query = query_values(request, {"date", "snapshot_id"})
    trade_date = query_date(query.get("date"))
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    if trade_date is None and snapshot_id is None:
        raise ValueError("A date or snapshot ID is required")
    return trade_date, snapshot_id


def _range_query(request: Request):
    query = query_values(
        request,
        {"start", "end", "adj", "date", "snapshot_id"},
    )
    if "start" not in query or "end" not in query:
        raise ValueError("A start and end date are required")
    start = query_date(query["start"])
    end = query_date(query["end"])
    assert start is not None and end is not None
    if start > end:
        raise ValueError("Invalid window")
    adjustment = query.get("adj", "qfq")
    if adjustment not in _ADJUSTMENTS:
        raise ValueError("Invalid adjustment")
    trade_date = query_date(query.get("date"))
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    return start, end, adjustment, trade_date, snapshot_id


def _adjust_window(history, start: date, end: date):
    try:
        sessions = sorted(
            {
                date.fromisoformat(point["trade_date"])
                for point in history
                if isinstance(point, dict)
            }
        )
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    selected = tuple(day for day in sessions if start <= day <= end)
    if not selected:
        raise ValueError("Invalid window")
    return selected


def _validate_snapshot_window(
    snapshot: PublishedSnapshot,
    explicit_date: date | None,
    adjusted_end: date,
) -> None:
    if snapshot.trade_date != adjusted_end:
        raise ValueError("Snapshot date mismatch")
    if explicit_date is not None and explicit_date != adjusted_end:
        raise ValueError("Date does not match adjusted end")


def _roles_and_seal(snapshot: PublishedSnapshot, security_id: str):
    result = snapshot.result("role")
    if result.status != "ready":
        reason = f"role_{result.status}"
        return {"items": None, "reason": reason}, {
            "closing_limit_streak": None,
            "reason": reason,
        }
    try:
        topics = result.payload["topics"]
        items = []
        seals = set()
        for topic in topics:
            topic_id = topic["topic_id"]
            for row in topic.get("results", ()):
                if row.get("security_id") != security_id:
                    continue
                external_role = ROLE_NAMES.get(row.get("role"))
                if external_role is not None:
                    items.append(
                        {
                            "role": external_role,
                            "sector_id": topic_id,
                            "status": row.get("internal_status"),
                        }
                    )
            for row in topic.get("ladder", ()):
                if (
                    row.get("security_id") == security_id
                    and row.get("trade_date") == snapshot.trade_date.isoformat()
                    and row.get("closing_limit_streak") is not None
                ):
                    seals.add(row["closing_limit_streak"])
    except (KeyError, TypeError):
        raise DataUnavailable from None
    items.sort(key=lambda item: (item["role"], item["sector_id"]))
    unique_items = list(dict.fromkeys(tuple(item.items()) for item in items))
    projected_items = [dict(item) for item in unique_items]
    if len(seals) > 1:
        raise DataUnavailable
    seal = next(iter(seals), None)
    return {"items": projected_items, "reason": None}, {
        "closing_limit_streak": seal,
        "reason": None if seal is not None else "seal_unavailable",
    }


def _regulation(snapshot: PublishedSnapshot):
    result = snapshot.result("regulation")
    return {
        "active_events": None,
        "reason": (
            "regulation_not_implemented"
            if result.status == "ready"
            else f"regulation_{result.status}"
        ),
    }


@router.get("/api/stock/{security_id}")
def stock_detail(security_id: str, request: Request):
    trade_date, snapshot_id = _detail_query(request)
    snapshot = _snapshot(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    _analysis, stocks, coverage = _market_extensions(snapshot)
    if stocks is None:
        return _response(
            snapshot,
            None,
            coverage,
            reason="materialization_unavailable",
            quality_flags=("materialization_unavailable",),
        )
    stock = stocks["stocks"].get(security_id)
    if stock is None:
        raise NotFound("Stock not found")
    quote = stock.get("quote")
    if not isinstance(quote, dict):
        raise DataUnavailable
    roles, seal = _roles_and_seal(snapshot, security_id)
    return _response(
        snapshot,
        {
            "security_id": security_id,
            "name": quote.get("name"),
            "quote": quote,
            "roles": roles,
            "seal": seal,
            "regulation": _regulation(snapshot),
        },
        coverage_view(
            1, int(quote.get("last") is not None), 0, int(quote.get("last") is None)
        ),
        rule_version=stocks.get("rule_version"),
        unit="currency/share",
    )


@router.get("/api/stock/{security_id}/kline")
def stock_kline(security_id: str, request: Request):
    start, end, adjustment, trade_date, snapshot_id = _range_query(request)
    snapshot = _snapshot(
        request,
        trade_date,
        snapshot_id,
    )
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date or end))
    analysis, stocks, _market_coverage = _market_extensions(snapshot)
    if analysis is None or stocks is None:
        return _response(
            snapshot,
            None,
            None,
            adjustment=adjustment,
            reason="materialization_unavailable",
            quality_flags=("materialization_unavailable",),
        )
    stock = stocks["stocks"].get(security_id)
    if stock is None:
        raise NotFound("Stock not found")
    history = stock.get("history")
    if not isinstance(history, list):
        raise DataUnavailable
    sessions = _adjust_window(history, start, end)
    _validate_snapshot_window(snapshot, trade_date, sessions[-1])
    adjustment_contract = analysis.get("adjustments", {}).get(adjustment)
    if not isinstance(adjustment_contract, dict):
        raise DataUnavailable
    if not adjustment_contract.get("available"):
        reason = adjustment_contract.get("reason")
        if not isinstance(reason, str) or not reason:
            raise DataUnavailable
        return _response(
            snapshot,
            None,
            coverage_view(1, 0, 0, 1),
            adjustment=adjustment,
            reason=reason,
            rule_version=stocks.get("rule_version"),
            unit="currency/share",
            quality_flags=("adjustment_unavailable",),
        )
    points = [
        point
        for point in history
        if date.fromisoformat(point["trade_date"]) in sessions
    ]
    valid = int(any(point.get("close") is not None for point in points))
    return _response(
        snapshot,
        {
            "security_id": security_id,
            "adjustment": adjustment,
            "adjusted_start": sessions[0],
            "adjusted_end": sessions[-1],
            "chart_type": stock.get("chart_type"),
            "points": points,
        },
        coverage_view(1, valid, 0, 1 - valid),
        adjustment=adjustment,
        rule_version=stocks.get("rule_version"),
        unit="currency/share",
    )
