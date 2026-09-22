"""Published fixed-window ranking and comparison routes."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.analysis_jobs.models import AnalysisJobCommand
from dragon_radar.http.contracts import coverage_view, empty_envelope
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.market import _restore_decimals
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
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    if not histories:
        raise DataUnavailable
    return max(histories, key=len)


def _window(analysis, sessions: tuple[date, ...], benchmark: str):
    expected = [day.isoformat() for day in sessions]
    try:
        windows = analysis["windows"]
        if not isinstance(windows, dict):
            raise DataUnavailable
        for key in ("20", "60", "120"):
            candidate = windows[key]
            if (
                not isinstance(candidate, dict)
                or type(candidate.get("available")) is not bool
            ):
                raise DataUnavailable
            if not candidate["available"]:
                continue
            stored_sessions = candidate["sessions"]
            if not isinstance(stored_sessions, list):
                raise DataUnavailable
            tuple(date.fromisoformat(day) for day in stored_sessions)
            if (
                candidate.get("benchmark_id") == benchmark
                and stored_sessions == expected
            ):
                return int(key), candidate
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
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
        through_date=values["end"],
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


def _string_list(value, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _published_eligible_ids(analysis, stocks) -> tuple[str, ...]:
    try:
        values = analysis["eligible_security_ids"]
        stock_rows = stocks["stocks"]
        if (
            not isinstance(values, list)
            or not isinstance(stock_rows, dict)
            or any(not isinstance(item, str) or not item for item in values)
            or values != sorted(set(values))
            or not set(values) <= set(stock_rows)
        ):
            raise DataUnavailable
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    return tuple(values)


def _decimal(value, *, nullable: bool = True) -> bool:
    return (nullable and value is None) or (
        isinstance(value, Decimal) and value.is_finite()
    )


def _ranking_ids(rows, allowed_ids: set[str]) -> set[str]:
    expected_fields = {
        "rank",
        "security_id",
        "name",
        "topics",
        "topic_status",
        "topic_reasons",
        "return_ratio",
        "excess_return_ratio",
        "max_drawdown_ratio",
        "amount_change_ratio",
        "reasons",
    }
    identifiers = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise DataUnavailable
        security_id = row["security_id"]
        topics = row["topics"]
        if (
            not isinstance(security_id, str)
            or security_id not in allowed_ids
            or (
                row["rank"] is not None
                and (type(row["rank"]) is not int or row["rank"] < 1)
            )
            or (row["name"] is not None and not isinstance(row["name"], str))
            or not isinstance(topics, list)
            or any(
                not isinstance(topic, dict)
                or set(topic) != {"topic_id", "name"}
                or not isinstance(topic["topic_id"], str)
                or not topic["topic_id"]
                or not isinstance(topic["name"], str)
                for topic in topics
            )
            or row["topic_status"] not in {"available", "unavailable"}
            or not _string_list(row["topic_reasons"])
            or not _decimal(row["return_ratio"], nullable=False)
            or not _decimal(row["excess_return_ratio"])
            or not _decimal(row["max_drawdown_ratio"], nullable=False)
            or not _decimal(row["amount_change_ratio"])
            or not _string_list(row["reasons"])
        ):
            raise DataUnavailable
        identifiers.append(security_id)
    if len(set(identifiers)) != len(identifiers):
        raise DataUnavailable
    return set(identifiers)


def _exclusion_ids(rows, allowed_ids: set[str]) -> set[str]:
    identifiers = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"security_id", "reasons"}
            or not isinstance(row["security_id"], str)
            or row["security_id"] not in allowed_ids
            or not _string_list(row["reasons"], nonempty=True)
        ):
            raise DataUnavailable
        identifiers.append(row["security_id"])
    if len(set(identifiers)) != len(identifiers):
        raise DataUnavailable
    return set(identifiers)


def _curve_ids(rows, allowed_ids: set[str], sessions: tuple[date, ...]) -> set[str]:
    expected_dates = [day.isoformat() for day in sessions]
    identifiers = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"security_id", "points", "reasons"}
            or not isinstance(row["security_id"], str)
            or row["security_id"] not in allowed_ids
            or not isinstance(row["points"], list)
            or len(row["points"]) != len(sessions)
            or not _string_list(row["reasons"])
        ):
            raise DataUnavailable
        point_dates = []
        has_value = False
        for point in row["points"]:
            if (
                not isinstance(point, dict)
                or set(point) != {"trade_date", "normalized", "drawdown"}
                or not isinstance(point["trade_date"], str)
                or not _decimal(point["normalized"])
                or not _decimal(point["drawdown"])
                or (point["normalized"] is None) != (point["drawdown"] is None)
            ):
                raise DataUnavailable
            point_dates.append(point["trade_date"])
            has_value = has_value or point["normalized"] is not None
        if point_dates != expected_dates or not has_value:
            raise DataUnavailable
        identifiers.append(row["security_id"])
    if len(set(identifiers)) != len(identifiers):
        raise DataUnavailable
    return set(identifiers)


def _validate_cached_result(
    result,
    command,
    sessions,
    stocks,
    eligible_security_ids: tuple[str, ...],
):
    available_fields = {
        "schema",
        "schema_version",
        "algorithm_version",
        "snapshot_id",
        "requested_start",
        "requested_end",
        "adjusted_start",
        "adjusted_end",
        "benchmark",
        "adjustment",
        "eligible_security_ids",
        "available",
        "reason",
        "ranking",
        "exclusions",
        "benchmark_return_ratio",
        "benchmark_reasons",
        "curves",
    }
    unavailable_fields = available_fields - {
        "benchmark_return_ratio",
        "benchmark_reasons",
    }
    try:
        stock_rows = stocks["stocks"]
        stock_ids = set(stock_rows)
        result_fields = frozenset(result)
        if (
            not isinstance(result, dict)
            or result_fields
            not in {
                frozenset(available_fields),
                frozenset(unavailable_fields),
            }
            or result.get("schema") != "dragon-radar.custom-analysis"
            or type(result.get("schema_version")) is not int
            or result["schema_version"] != 1
            or result.get("algorithm_version") != command.algorithm_version
            or result.get("snapshot_id") != str(command.snapshot_id)
            or result.get("requested_start") != command.start.isoformat()
            or result.get("requested_end") != command.end.isoformat()
            or result.get("adjusted_start") != sessions[0].isoformat()
            or result.get("adjusted_end") != sessions[-1].isoformat()
            or result.get("benchmark") != command.benchmark
            or result.get("adjustment") != command.adjustment
            or result.get("eligible_security_ids") != list(eligible_security_ids)
            or type(result.get("available")) is not bool
            or not isinstance(stock_rows, dict)
            or any(not isinstance(item, str) or not item for item in stock_ids)
            or not isinstance(result.get("ranking"), list)
            or not isinstance(result.get("exclusions"), list)
            or not isinstance(result.get("curves"), list)
        ):
            raise DataUnavailable
        if not result["available"]:
            if (
                result_fields != unavailable_fields
                or not isinstance(result.get("reason"), str)
                or not result["reason"]
                or result["ranking"]
                or result["exclusions"]
                or result["curves"]
            ):
                raise DataUnavailable
            return result
        if (
            result_fields != available_fields
            or result.get("reason") is not None
            or not _decimal(result["benchmark_return_ratio"])
            or not _string_list(result["benchmark_reasons"])
        ):
            raise DataUnavailable
        allowed_ids = (
            set(eligible_security_ids) if not command.symbols else set(command.symbols)
        )
        if not allowed_ids <= stock_ids:
            raise DataUnavailable
        ranking_ids = _ranking_ids(result["ranking"], allowed_ids)
        exclusion_ids = _exclusion_ids(result["exclusions"], allowed_ids)
        curve_ids = _curve_ids(result["curves"], allowed_ids, sessions)
        if command.symbols:
            if (
                curve_ids & exclusion_ids
                or curve_ids | exclusion_ids != allowed_ids
                or not ranking_ids <= curve_ids
            ):
                raise DataUnavailable
        elif (
            ranking_ids & exclusion_ids
            or ranking_ids | exclusion_ids != allowed_ids
            or curve_ids
        ):
            raise DataUnavailable
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    return result


def _cached_result(
    request: Request,
    snapshot,
    values,
    symbols,
    sessions,
    stocks,
    eligible_security_ids: tuple[str, ...],
):
    repository = request.app.state.analysis_repository
    if repository is None:
        return None
    command = AnalysisJobCommand(
        snapshot_id=snapshot.id,
        start=values["start"],
        end=values["end"],
        benchmark=values["benchmark"],
        adjustment=values["adjustment"],
        symbols=symbols,
    )
    result = repository.get_cache(command)
    if result is None:
        return None
    result = _restore_decimals(result)
    return _validate_cached_result(
        result,
        command,
        sessions,
        stocks,
        eligible_security_ids,
    )


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
    eligible_security_ids = _published_eligible_ids(analysis, stocks)
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
        cached = _cached_result(
            request,
            snapshot,
            values,
            (),
            sessions,
            stocks,
            eligible_security_ids,
        )
        if cached is None:
            return _unavailable(
                snapshot,
                "analysis_not_precomputed",
                values["adjustment"],
                benchmark=values["benchmark"],
                rule_version=analysis.get("rule_version"),
            )
        result = cached
        if not result["available"]:
            return _unavailable(
                snapshot,
                result["reason"],
                values["adjustment"],
                benchmark=values["benchmark"],
                rule_version=analysis.get("rule_version"),
            )
        window_days = len(sessions)
        window = {"adjustments": {values["adjustment"]: result}}
    try:
        projected = window["adjustments"][values["adjustment"]]
        projected_rows = projected["ranking"]
        exclusions = projected["exclusions"]
        benchmark_return = projected["benchmark_return_ratio"]
        allowed_ids = set(eligible_security_ids)
        ranking_ids = _ranking_ids(projected_rows, allowed_ids)
        exclusion_ids = _exclusion_ids(exclusions, allowed_ids)
        if ranking_ids & exclusion_ids or ranking_ids | exclusion_ids != allowed_ids:
            raise DataUnavailable
        rows = _sorted_ranking(projected_rows, values["sort"])
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    if not isinstance(exclusions, list):
        raise DataUnavailable
    field = _SORT_FIELDS[values["sort"]][0]
    valid = sum(row.get(field) is not None for row in rows)
    expected = len(eligible_security_ids)
    response = _response(
        snapshot,
        {
            "window_days": window_days,
            "adjusted_start": sessions[0],
            "adjusted_end": sessions[-1],
            "benchmark_return": benchmark_return,
            "ranking": rows,
            "exclusions": exclusions,
        },
        coverage_view(expected, valid, 0, expected - valid),
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
    eligible_security_ids = _published_eligible_ids(analysis, stocks)
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
        symbols = values["symbols"]
        assert symbols is not None
        cached = _cached_result(
            request,
            snapshot,
            values,
            symbols,
            sessions,
            stocks,
            eligible_security_ids,
        )
        if cached is None:
            return _unavailable(
                snapshot,
                "analysis_not_precomputed",
                values["adjustment"],
                benchmark=values["benchmark"],
                rule_version=analysis.get("rule_version"),
            )
        result = cached
        if not result["available"]:
            return _unavailable(
                snapshot,
                result["reason"],
                values["adjustment"],
                benchmark=values["benchmark"],
                rule_version=analysis.get("rule_version"),
            )
        return _response(
            snapshot,
            {
                "adjusted_start": sessions[0],
                "adjusted_end": sessions[-1],
                "benchmark": values["benchmark"],
                "curves": result["curves"],
                "exclusions": result["exclusions"],
            },
            coverage_view(
                len(symbols),
                len(result["curves"]),
                0,
                len(result["exclusions"]),
            ),
            adjustment=values["adjustment"],
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
                curves.append(
                    {
                        "security_id": symbol,
                        "points": points,
                        "reasons": reasons,
                    }
                )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise DataUnavailable from None
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
