"""Published fixed-window ranking and comparison routes."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import coverage_view, empty_envelope
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.stock import (
    _adjust_window,
    _market_extensions,
    _response,
    _snapshot,
    _validate_snapshot_window,
)
from dragon_radar.http.selection import query_date, query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_ADJUSTMENTS = frozenset({"raw", "qfq", "hfq"})
_SORT_FIELDS = {
    "return": ("return_ratio", True),
    "excess_return": ("excess_return_ratio", True),
    "drawdown": ("max_drawdown_ratio", False),
    "amount_change": ("amount_change_ratio", True),
}


def _query(request: Request, *, comparison: bool):
    allowed = {
        "start",
        "end",
        "benchmark",
        "adj",
        "date",
        "snapshot_id",
    }
    if comparison:
        allowed.add("symbols")
    else:
        allowed.add("sort")
    query = query_values(request, allowed)
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
    benchmark = query.get("benchmark", "B000300")
    if not benchmark or len(benchmark) > 100:
        raise ValueError("Invalid benchmark")
    sort = query.get("sort", "return")
    if not comparison and sort not in _SORT_FIELDS:
        raise ValueError("Invalid sort")
    symbols = None
    if comparison:
        raw_symbols = query.get("symbols")
        if raw_symbols is None:
            raise ValueError("Symbols are required")
        symbols = tuple(raw_symbols.split(","))
        if (
            not 1 <= len(symbols) <= 5
            or any(not symbol or symbol.strip() != symbol for symbol in symbols)
            or len(set(symbols)) != len(symbols)
            or any(len(symbol) > 100 for symbol in symbols)
        ):
            raise ValueError("Symbols must contain one to five unique IDs")
    trade_date = query_date(query.get("date"))
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    return {
        "start": start,
        "end": end,
        "adjustment": adjustment,
        "benchmark": benchmark,
        "sort": sort,
        "symbols": symbols,
        "trade_date": trade_date,
        "snapshot_id": snapshot_id,
    }


def _sessions(stocks):
    try:
        histories = [
            stock["history"]
            for stock in stocks["stocks"].values()
            if isinstance(stock, dict) and isinstance(stock.get("history"), list)
        ]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    if not histories:
        raise DataUnavailable
    return max(histories, key=len)


def _window(analysis, sessions: tuple[date, ...], benchmark: str):
    expected = [day.isoformat() for day in sessions]
    try:
        windows = analysis["windows"]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    for key in ("20", "60", "120"):
        candidate = windows.get(key)
        if (
            isinstance(candidate, dict)
            and candidate.get("available") is True
            and candidate.get("benchmark_id") == benchmark
            and candidate.get("sessions") == expected
        ):
            return int(key), candidate
    return None, None


def _unavailable(
    snapshot,
    reason: str,
    adjustment: str,
    *,
    benchmark: str | None = None,
    rule_version: str | None = None,
):
    flag = (
        reason
        if reason in {"analysis_not_precomputed", "materialization_unavailable"}
        else "adjustment_unavailable"
    )
    return _response(
        snapshot,
        None,
        None,
        adjustment=adjustment,
        benchmark=benchmark,
        reason=reason,
        rule_version=rule_version,
        quality_flags=(flag,),
    )


def _selection(request: Request, values):
    snapshot = _snapshot(
        request,
        values["trade_date"],
        values["snapshot_id"],
    )
    if snapshot is None:
        return None, None, None, None
    analysis, stocks, _coverage = _market_extensions(snapshot)
    if analysis is None or stocks is None:
        return snapshot, None, None, None
    sessions = _adjust_window(_sessions(stocks), values["start"], values["end"])
    _validate_snapshot_window(snapshot, values["trade_date"], sessions[-1])
    return snapshot, analysis, stocks, sessions


def _adjustment(analysis, name: str):
    try:
        contract = analysis["adjustments"][name]
        available = contract["available"]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    if type(available) is not bool:
        raise DataUnavailable
    if available:
        return None
    reason = contract.get("reason")
    if not isinstance(reason, str) or not reason:
        raise DataUnavailable
    return reason


def _sorted_ranking(rows, sort: str):
    field, descending = _SORT_FIELDS[sort]
    ranked = [dict(row) for row in rows]
    ranked.sort(key=lambda row: row["security_id"])
    ranked.sort(
        key=lambda row: (
            row.get(field) is None,
            (
                -row[field]
                if descending and row.get(field) is not None
                else row.get(field)
                if row.get(field) is not None
                else 0
            ),
        )
    )
    previous = object()
    current_rank = 0
    for position, row in enumerate(ranked, start=1):
        value = row.get(field)
        row["rank"] = None
        if value is None:
            continue
        if value != previous:
            current_rank = position
            previous = value
        row["rank"] = current_rank
    return ranked


@router.get("/api/rank/range")
def rank_range(request: Request):
    values = _query(request, comparison=False)
    snapshot, analysis, stocks, sessions = _selection(request, values)
    if snapshot is None:
        return DecimalJSONResponse(
            empty_envelope(values["trade_date"] or values["end"])
        )
    if analysis is None or stocks is None or sessions is None:
        return _unavailable(
            snapshot,
            "materialization_unavailable",
            values["adjustment"],
            benchmark=values["benchmark"],
        )
    reason = _adjustment(analysis, values["adjustment"])
    if reason is not None:
        return _unavailable(
            snapshot,
            reason,
            values["adjustment"],
            benchmark=values["benchmark"],
            rule_version=analysis.get("rule_version"),
        )
    window_days, window = _window(analysis, sessions, values["benchmark"])
    if window is None or window_days is None:
        return _unavailable(
            snapshot,
            "analysis_not_precomputed",
            values["adjustment"],
            benchmark=values["benchmark"],
            rule_version=analysis.get("rule_version"),
        )
    try:
        projected = window["adjustments"][values["adjustment"]]
        rows = _sorted_ranking(projected["ranking"], values["sort"])
        benchmark_return = projected["benchmark_return_ratio"]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    field = _SORT_FIELDS[values["sort"]][0]
    valid = sum(row.get(field) is not None for row in rows)
    response = _response(
        snapshot,
        {
            "window_days": window_days,
            "adjusted_start": sessions[0],
            "adjusted_end": sessions[-1],
            "benchmark_return": benchmark_return,
            "ranking": rows,
        },
        coverage_view(len(rows), valid, 0, len(rows) - valid),
        adjustment=values["adjustment"],
        benchmark=values["benchmark"],
        rule_version=analysis.get("rule_version"),
    )
    return response


@router.get("/api/compare/curves")
def compare_curves(request: Request):
    values = _query(request, comparison=True)
    snapshot, analysis, stocks, sessions = _selection(request, values)
    if snapshot is None:
        return DecimalJSONResponse(
            empty_envelope(values["trade_date"] or values["end"])
        )
    if analysis is None or stocks is None or sessions is None:
        return _unavailable(
            snapshot,
            "materialization_unavailable",
            values["adjustment"],
            benchmark=values["benchmark"],
        )
    reason = _adjustment(analysis, values["adjustment"])
    if reason is not None:
        return _unavailable(
            snapshot,
            reason,
            values["adjustment"],
            benchmark=values["benchmark"],
            rule_version=analysis.get("rule_version"),
        )
    _window_days, window = _window(analysis, sessions, values["benchmark"])
    if window is None:
        return _unavailable(
            snapshot,
            "analysis_not_precomputed",
            values["adjustment"],
            benchmark=values["benchmark"],
            rule_version=analysis.get("rule_version"),
        )
    symbols = values["symbols"]
    assert symbols is not None
    known = stocks["stocks"]
    if any(symbol not in known for symbol in symbols):
        raise NotFound("Stock not found")
    try:
        stored_curves = window["adjustments"][values["adjustment"]]["curves"]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    curves = []
    exclusions = []
    for symbol in symbols:
        curve = stored_curves.get(symbol)
        if not isinstance(curve, dict):
            raise DataUnavailable
        normalized = curve.get("normalized")
        drawdown = curve.get("drawdown")
        reasons = curve.get("reasons")
        if (
            not isinstance(normalized, list)
            or not isinstance(drawdown, list)
            or not isinstance(reasons, list)
            or len(normalized) != len(sessions)
            or len(drawdown) != len(sessions)
        ):
            raise DataUnavailable
        points = [
            {
                "trade_date": day,
                "normalized": normalized[index],
                "drawdown": drawdown[index],
            }
            for index, day in enumerate(sessions)
        ]
        if all(point["normalized"] is None for point in points):
            exclusions.append({"security_id": symbol, "reasons": reasons})
        else:
            curves.append({"security_id": symbol, "points": points})
    return _response(
        snapshot,
        {
            "adjusted_start": sessions[0],
            "adjusted_end": sessions[-1],
            "benchmark": values["benchmark"],
            "curves": curves,
            "exclusions": exclusions,
        },
        coverage_view(
            len(symbols),
            len(curves),
            0,
            len(symbols) - len(curves),
        ),
        adjustment=values["adjustment"],
        benchmark=values["benchmark"],
        rule_version=analysis.get("rule_version"),
    )
