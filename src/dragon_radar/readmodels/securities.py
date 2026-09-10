"""Safe projections of an already authorized, quality-gated snapshot batch."""

from datetime import date
from typing import Literal

from pydantic import ConfigDict

from dragon_radar.analytics.metrics import (
    BreadthAnalysis,
    _index,
    _observation,
    market_breadth,
)
from dragon_radar.domain.models import DailyBar, ExactDecimal, FrozenModel, MarketBatch
from dragon_radar.domain.quality import state_at
from dragon_radar.storage.repository import NotFound

SecuritySort = Literal["security_id", "name", "close", "change", "amount"]
SortOrder = Literal["asc", "desc"]


class Units(FrozenModel):
    model_config = ConfigDict(allow_inf_nan=False)

    currency: str | None = None
    price_unit: Literal["currency/share"] | None = None
    volume_unit: Literal["share", "lot"] | None = None
    amount_unit: Literal["currency"] | None = None


class SecurityRow(Units):
    security_id: str
    name: str | None
    exchange: str | None
    board: str | None
    close: ExactDecimal | None
    change: ExactDecimal | None
    amount: ExactDecimal | None
    suspended: bool | None
    missing_reasons: tuple[str, ...]
    change_unit: Literal["fraction"] = "fraction"


class SecuritiesPage(FrozenModel):
    items: tuple[SecurityRow, ...]
    total: int
    page: int
    page_size: Literal[20, 50, 100]
    basis: Literal["raw_close"] = "raw_close"


class HistoryPoint(Units):
    trade_date: date
    open: ExactDecimal | None
    high: ExactDecimal | None
    low: ExactDecimal | None
    close: ExactDecimal | None
    volume: ExactDecimal | None
    amount: ExactDecimal | None
    suspended: bool | None
    missing_reasons: tuple[str, ...]


class SecurityDetail(FrozenModel):
    security: SecurityRow
    history: tuple[HistoryPoint, ...]
    chart_type: Literal["candlestick", "line"]
    price_basis: Literal["raw_close"] = "raw_close"
    basis: Literal["raw_close"] = "raw_close"


BarIndex = dict[tuple[str, date], list[DailyBar]]


def _units(bar: DailyBar | None) -> dict:
    return {
        field: getattr(bar, field) if bar is not None else None
        for field in Units.model_fields
    }


def _row(
    batch: MarketBatch, index: BarIndex, sid: str, breadth: BreadthAnalysis
) -> SecurityRow:
    state = state_at(batch, sid, batch.trade_date)
    bar, errors = _observation(batch, index, sid, batch.trade_date)
    daily_return = next((r for r in breadth.returns if r.security_id == sid), None)
    reasons = set(errors)
    for exclusion in breadth.exclusions:
        if exclusion.security_id == sid:
            reasons.update(exclusion.reasons)
    if bar is not None and bar.amount is None:
        reasons.add("missing_amount")
    return SecurityRow(
        security_id=sid,
        name=state.name if state is not None else None,
        exchange=state.exchange if state is not None else None,
        board=state.board if state is not None else None,
        close=bar.close if bar is not None else None,
        change=daily_return.value if daily_return is not None else None,
        amount=bar.amount if bar is not None else None,
        suspended=state.suspended if state is not None else None,
        missing_reasons=tuple(sorted(reasons)),
        **_units(bar),
    )


def list_securities(
    batch: MarketBatch,
    *,
    q: str = "",
    sort: SecuritySort = "security_id",
    order: SortOrder = "asc",
    page: int = 1,
    page_size: int = 50,
) -> SecuritiesPage:
    if page_size not in (20, 50, 100):
        raise ValueError("Invalid page size")
    index, breadth = _index(batch), market_breadth(batch)
    query = q.strip().casefold()
    rows = [
        _row(batch, index, sid, breadth)
        for sid in sorted(set(batch.expected_security_ids))
    ]
    rows = [
        row
        for row in rows
        if query in row.security_id.casefold() or query in (row.name or "").casefold()
    ]
    # Stable sorting preserves ascending IDs for ties, even in descending order.
    present = [row for row in rows if getattr(row, sort) is not None]
    missing = [row for row in rows if getattr(row, sort) is None]
    ordered = (
        sorted(present, key=lambda row: getattr(row, sort), reverse=order == "desc")
        + missing
    )
    offset = (page - 1) * page_size
    return SecuritiesPage(
        items=tuple(ordered[offset : offset + page_size]),
        total=len(rows),
        page=page,
        page_size=page_size,
    )


def _history_point(
    batch: MarketBatch, index: BarIndex, sid: str, day: date
) -> HistoryPoint:
    state = state_at(batch, sid, day)
    bar, errors = _observation(batch, index, sid, day)
    reasons = set(errors)
    if bar is not None:
        if any(value is None for value in (bar.open, bar.high, bar.low)):
            reasons.add("missing_ohlc")
        for field in ("volume", "amount"):
            if getattr(bar, field) is None:
                reasons.add("missing_" + field)
    return HistoryPoint(
        trade_date=day,
        suspended=state.suspended if state is not None else None,
        missing_reasons=tuple(sorted(reasons)),
        **{
            field: getattr(bar, field) if bar is not None else None
            for field in ("open", "high", "low", "close", "volume", "amount")
        },
        **_units(bar),
    )


def security_detail(batch: MarketBatch, security_id: str) -> SecurityDetail:
    if security_id not in batch.expected_security_ids:
        raise NotFound
    index = _index(batch)
    history = tuple(
        _history_point(batch, index, security_id, day)
        for day in batch.calendar.sessions
        if day <= batch.trade_date
    )
    observations = [point for point in history if point.close is not None]
    full_ohlc = bool(observations) and all(
        point.open is not None and point.high is not None and point.low is not None
        for point in observations
    )
    return SecurityDetail(
        security=_row(batch, index, security_id, market_breadth(batch)),
        history=history,
        chart_type="candlestick" if full_ohlc else "line",
    )
