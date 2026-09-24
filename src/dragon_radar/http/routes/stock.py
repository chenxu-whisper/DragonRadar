"""Published stock detail and raw K-line routes."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import coverage_view, empty_envelope, snapshot_envelope
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.routes.market import _published_payload
from dragon_radar.http.routes.regulation import _regulation_payload
from dragon_radar.http.selection import query_date, query_values, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.roles.adapters import adapt_role_evaluation
from dragon_radar.roles.models import RankedCandidate, RoleEvaluation, RoleTopicStatus
from dragon_radar.roles.rules import RESET_APPROVED_RULE_VERSIONS, ROLE_NAMES
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_ADJUSTMENTS = frozenset({"raw", "qfq", "hfq"})
_SEAL_FIELDS = (
    "is_limit_up",
    "touched_limit",
    "first_seal_time",
    "last_seal_time",
    "break_count",
    "seal_amount",
    "seal_amount_unit",
    "continuous_boards",
)


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _snapshot(
    request: Request,
    trade_date: date | None,
    snapshot_id: UUID | None,
    *,
    through_date: date | None = None,
):
    selection_date = (
        None if through_date is not None and snapshot_id is None else trade_date
    )
    selected = select_snapshot(
        _repository(request),
        selection_date,
        snapshot_id,
        through_date=through_date,
    )
    if selected is None or not isinstance(selected, PublishedSnapshot):
        return None
    return selected


def _market_extensions(snapshot: PublishedSnapshot):
    payload, coverage, materialized = _published_payload(snapshot)
    if not materialized:
        return None, None, coverage
    analysis = payload.get("analysis")
    stocks = payload.get("stocks")
    expected_trade_date = snapshot.trade_date.isoformat()
    if (
        not isinstance(analysis, dict)
        or analysis.get("schema") != "dragon-radar.analysis"
        or type(analysis.get("schema_version")) is not int
        or analysis["schema_version"] != 1
        or not isinstance(analysis.get("rule_version"), str)
        or not analysis["rule_version"]
        or analysis.get("trade_date") != expected_trade_date
        or not isinstance(stocks, dict)
        or stocks.get("schema") != "dragon-radar.stocks"
        or type(stocks.get("schema_version")) is not int
        or stocks["schema_version"] != 1
        or not isinstance(stocks.get("rule_version"), str)
        or not stocks["rule_version"]
        or stocks.get("trade_date") != expected_trade_date
        or not isinstance(stocks.get("stocks"), dict)
    ):
        raise DataUnavailable
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


def _roles(snapshot: PublishedSnapshot, security_id: str):
    result = snapshot.result("role")
    if result.status != "ready":
        return [], f"role_{result.status}"
    try:
        topics = result.payload["topics"]
        items = []
        for topic in topics:
            topic_id = topic["topic_id"]
            for internal_role, external_role in ROLE_NAMES.items():
                status = next(
                    (
                        item
                        for item in topic.get("statuses", ())
                        if item.get("role") == internal_role
                    ),
                    None,
                )
                if status is None:
                    continue
                evaluation = RoleEvaluation(
                    topic_status=RoleTopicStatus(
                        topic_id=topic_id,
                        role=internal_role,
                        capability=status["capability"],
                        status=status["status"],
                        expected=status["expected"],
                        valid=status["valid"],
                        legal_excluded=status["legal_excluded"],
                        missing=status["missing"],
                        reasons=tuple(status.get("reasons", ())),
                    ),
                    candidates=tuple(
                        RankedCandidate(
                            topic_id=topic_id,
                            security_id=row["security_id"],
                            role=internal_role,
                            status=row["internal_status"],
                            factors=(),
                            reasons=tuple(row.get("reasons", ())),
                            tie_rank=row["tie_rank"],
                        )
                        for row in topic.get("results", ())
                        if row.get("role") == internal_role
                    ),
                )
                for evidence in adapt_role_evaluation(
                    evaluation,
                    rule_version=result.payload["rule_version"],
                    approved_rule_versions=RESET_APPROVED_RULE_VERSIONS,
                ):
                    if evidence.security_id != security_id:
                        continue
                    items.append(
                        {
                            "role_type": external_role,
                            "evidence_status": evidence.status,
                            "sector_id": topic_id,
                        }
                    )
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    items.sort(key=lambda item: (item["role_type"], item["sector_id"]))
    unique_items = list(dict.fromkeys(tuple(item.items()) for item in items))
    projected_items = [dict(item) for item in unique_items]
    return projected_items, None


def _seal(stock):
    seal = stock.get("seal")
    if seal is None:
        return {**dict.fromkeys(_SEAL_FIELDS), "reason": "seal_event_missing"}
    if not isinstance(seal, dict) or any(field not in seal for field in _SEAL_FIELDS):
        raise DataUnavailable
    reason = seal.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise DataUnavailable
    if reason is not None and any(seal[field] is not None for field in _SEAL_FIELDS):
        raise DataUnavailable
    return {field: seal[field] for field in (*_SEAL_FIELDS, "reason")}


def _published_stock(stocks: dict, security_id: str) -> dict:
    published = stocks["stocks"]
    if security_id not in published:
        raise NotFound("Stock not found")
    stock = published[security_id]
    if (
        not isinstance(stock, dict)
        or not isinstance(stock.get("quote"), dict)
        or not isinstance(stock.get("history"), list)
        or any(not isinstance(point, dict) for point in stock["history"])
        or not isinstance(stock.get("chart_type"), str)
        or stock["chart_type"] not in {"candlestick", "line"}
    ):
        raise DataUnavailable
    return stock


def _regulation(snapshot: PublishedSnapshot, security_id: str):
    payload, result = _regulation_payload(snapshot)
    if payload is not None:
        count = sum(
            event.security_id == security_id for event in payload["events"]
        )
        return {
            "formal_event_count": count,
            "event_source_completeness": "complete",
            "reason": None,
        }
    return {
        "formal_event_count": None,
        "event_source_completeness": "unknown",
        "reason": f"regulation_{result.status}",
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
    stock = _published_stock(stocks, security_id)
    quote = stock["quote"]
    roles, role_reason = _roles(snapshot, security_id)
    data = {
        "security_id": security_id,
        "name": quote.get("name"),
        "quote": quote,
        "roles": roles,
        "seal": _seal(stock),
        "regulation": _regulation(snapshot, security_id),
    }
    if role_reason is not None:
        data["role_reason"] = role_reason
    return _response(
        snapshot,
        data,
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
        through_date=end,
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
    stock = _published_stock(stocks, security_id)
    history = stock["history"]
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
            "chart_type": stock["chart_type"],
            "points": points,
        },
        coverage_view(1, valid, 0, 1 - valid),
        adjustment=adjustment,
        rule_version=stocks.get("rule_version"),
        unit="currency/share",
    )
