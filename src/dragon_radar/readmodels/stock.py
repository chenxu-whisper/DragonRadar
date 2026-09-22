"""Pure publication-time stock quote and history read models."""

from datetime import date
from typing import Any

from dragon_radar.analytics.metrics import _index, _observation, interval_return
from dragon_radar.domain.models import DailyBar, MarketBatch
from dragon_radar.domain.quality import state_at

STOCK_SCHEMA = "dragon-radar.stocks"
STOCK_SCHEMA_VERSION = 1
STOCK_RULE_VERSION = "stocks-v1-raw"


def _history_point(
    batch: MarketBatch,
    index: dict[tuple[str, date], list[DailyBar]],
    security_id: str,
    day: date,
) -> dict[str, Any]:
    state = state_at(batch, security_id, day)
    observation, reasons = _observation(batch, index, security_id, day)
    return {
        "trade_date": day.isoformat(),
        "open": observation.open if observation is not None else None,
        "high": observation.high if observation is not None else None,
        "low": observation.low if observation is not None else None,
        "close": observation.close if observation is not None else None,
        "volume": observation.volume if observation is not None else None,
        "amount": observation.amount if observation is not None else None,
        "suspended": state.suspended if state is not None else None,
        "reasons": list(reasons),
    }


def _quote(
    batch: MarketBatch,
    index: dict[tuple[str, date], list[DailyBar]],
    security_id: str,
) -> dict[str, Any]:
    state = state_at(batch, security_id, batch.trade_date)
    current, reasons = _observation(
        batch, index, security_id, batch.trade_date
    )
    previous_close = current.previous_close if current is not None else None
    if current is not None and previous_close is None:
        sessions = batch.calendar.sessions
        position = sessions.index(batch.trade_date)
        if position:
            previous, previous_reasons = _observation(
                batch, index, security_id, sessions[position - 1]
            )
            if previous is not None and not previous_reasons:
                previous_close = previous.close
    change = None
    if current is not None and previous_close is not None:
        change = interval_return((previous_close, current.close))
    return {
        "security_id": security_id,
        "code": state.code if state is not None else None,
        "name": state.name if state is not None else None,
        "trade_date": batch.trade_date.isoformat(),
        "last": current.close if current is not None else None,
        "previous_close": previous_close,
        "change_ratio": change,
        "volume": current.volume if current is not None else None,
        "amount": current.amount if current is not None else None,
        "suspended": state.suspended if state is not None else None,
        "reasons": list(reasons),
    }


def materialize_stocks(batch: MarketBatch) -> dict[str, Any]:
    """Build raw quotes and supplied history without synthesizing OHLC values."""
    index = _index(batch)
    sessions = tuple(day for day in batch.calendar.sessions if day <= batch.trade_date)
    stocks = {}
    for security_id in dict.fromkeys(batch.expected_security_ids):
        history = [
            _history_point(batch, index, security_id, day) for day in sessions
        ]
        observations = [point for point in history if point["close"] is not None]
        has_complete_ohlc = bool(observations) and all(
            all(point[field] is not None for field in ("open", "high", "low"))
            for point in observations
        )
        stocks[security_id] = {
            "quote": _quote(batch, index, security_id),
            "history": history,
            "chart_type": "candlestick" if has_complete_ohlc else "line",
            "price_basis": "raw_close",
        }
    return {
        "schema": STOCK_SCHEMA,
        "schema_version": STOCK_SCHEMA_VERSION,
        "rule_version": STOCK_RULE_VERSION,
        "trade_date": batch.trade_date.isoformat(),
        "stocks": stocks,
    }
