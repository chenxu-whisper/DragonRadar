"""Safe projections of an already authorized, quality-gated snapshot batch."""

from collections import defaultdict
from datetime import date
from typing import Literal

from pydantic import ConfigDict

from dragon_radar.analytics.metrics import (
    _batch_reasons,
    _index,
    _observation,
    interval_return,
)
from dragon_radar.domain.models import (
    DailyBar,
    ExactDecimal,
    FrozenModel,
    MarketBatch,
    SecurityState,
)
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


class _Projection:
    """Request-local indexes; retain the original pool and quality context."""

    def __init__(self, batch: MarketBatch):
        self.batch = batch
        self.bars: BarIndex = _index(batch)
        self.states: dict[str, list[SecurityState]] = defaultdict(list)
        for state in batch.security_states:
            self.states[state.security_id].append(state)
        self.batches: dict[str, MarketBatch] = {}
        self.effective: dict[tuple[str, date], SecurityState | None] = {}
        self.observations: dict[
            tuple[str, date], tuple[DailyBar | None, tuple[str, ...]]
        ] = {}
        self.batch_reasons = _batch_reasons(batch)
        sessions = batch.calendar.sessions
        position = (
            sessions.index(batch.trade_date) if batch.trade_date in sessions else -1
        )
        self.previous_day = sessions[position - 1] if position > 0 else None

    def security_batch(self, sid: str) -> MarketBatch:
        if sid not in self.batches:
            # Only narrow state traversal, never the declared quality denominator.
            self.batches[sid] = self.batch.model_copy(
                update={"security_states": tuple(self.states.get(sid, ()))}
            )
        return self.batches[sid]

    def state(self, sid: str, day: date) -> SecurityState | None:
        key = (sid, day)
        if key not in self.effective:
            self.effective[key] = state_at(self.security_batch(sid), sid, day)
        return self.effective[key]

    def observation(
        self, sid: str, day: date
    ) -> tuple[DailyBar | None, tuple[str, ...]]:
        key = (sid, day)
        if key not in self.observations:
            self.observations[key] = _observation(
                self.security_batch(sid), self.bars, sid, day
            )
        return self.observations[key]

    def daily_return(self, sid: str) -> tuple[ExactDecimal | None, tuple[str, ...]]:
        current, errors = self.observation(sid, self.batch.trade_date)
        reasons = set(self.batch_reasons) | set(errors)
        reference = current.previous_close if current is not None else None
        if current is not None and reference is None:
            if self.previous_day is None:
                reasons.add("missing_previous_close")
            else:
                previous, errors = self.observation(sid, self.previous_day)
                reasons.update(errors)
                if previous is not None:
                    if (current.currency, current.price_unit) != (
                        previous.currency,
                        previous.price_unit,
                    ):
                        reasons.add("unit_mismatch")
                    reference = previous.close
        if reasons:
            return None, tuple(sorted(reasons))
        return interval_return((reference, current.close)), ()


def _units(bar: DailyBar | None) -> dict:
    return {
        field: getattr(bar, field) if bar is not None else None
        for field in Units.model_fields
    }


def _row(projection: _Projection, sid: str) -> SecurityRow:
    state = projection.state(sid, projection.batch.trade_date)
    bar, errors = projection.observation(sid, projection.batch.trade_date)
    daily_return, exclusions = projection.daily_return(sid)
    reasons = set(errors) | set(exclusions)
    if bar is not None and bar.amount is None:
        reasons.add("missing_amount")
    return SecurityRow(
        security_id=sid,
        name=state.name if state is not None else None,
        exchange=state.exchange if state is not None else None,
        board=state.board if state is not None else None,
        close=bar.close if bar is not None else None,
        change=daily_return,
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
    projection = _Projection(batch)
    query = q.strip().casefold()
    ids = sorted(set(batch.expected_security_ids))
    if query:
        ids = [
            sid
            for sid in ids
            if query in sid.casefold()
            or (
                (state := projection.state(sid, batch.trade_date)) is not None
                and query in (state.name or "").casefold()
            )
        ]
    rows = [_row(projection, sid) for sid in ids]
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


def _history_point(projection: _Projection, sid: str, day: date) -> HistoryPoint:
    state = projection.state(sid, day)
    bar, errors = projection.observation(sid, day)
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
    projection = _Projection(batch)
    history = tuple(
        _history_point(projection, security_id, day)
        for day in batch.calendar.sessions
        if day <= batch.trade_date
    )
    observations = [point for point in history if point.close is not None]
    full_ohlc = bool(observations) and all(
        point.open is not None and point.high is not None and point.low is not None
        for point in observations
    )
    return SecurityDetail(
        security=_row(projection, security_id),
        history=history,
        chart_type="candlestick" if full_ohlc else "line",
    )
