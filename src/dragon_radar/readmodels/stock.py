"""Pure publication-time stock quote and history read models."""

from datetime import date
from typing import Any

from dragon_radar.analytics.metrics import interval_return
from dragon_radar.domain.models import MarketBatch
from dragon_radar.readmodels.projection import MarketProjection

STOCK_SCHEMA = "dragon-radar.stocks"
STOCK_SCHEMA_VERSION = 1
STOCK_RULE_VERSION = "stocks-v1-raw"
_SEAL_FIELDS = (
    "first_seal_time",
    "last_seal_time",
    "break_count",
    "seal_amount",
    "seal_amount_unit",
    "continuous_boards",
)


def _unavailable_seal(reason: str) -> dict[str, Any]:
    return {**dict.fromkeys(_SEAL_FIELDS), "reason": reason}


def _seal(batch: MarketBatch, security_id: str) -> dict[str, Any]:
    events = [
        event
        for event in batch.limit_up_events
        if event.security_id == security_id
        and event.trade_date == batch.trade_date
        and event.available_at <= batch.as_of
        and event.source_id == batch.source_id
        and event.source_version == batch.source_version
    ]
    if len(events) != 1:
        return _unavailable_seal(
            "seal_event_conflict" if len(events) > 1 else "seal_event_missing"
        )
    event = events[0]
    return {
        "first_seal_time": (
            event.first_seal_time.isoformat()
            if event.first_seal_time is not None
            else None
        ),
        "last_seal_time": (
            event.last_seal_time.isoformat()
            if event.last_seal_time is not None
            else None
        ),
        "break_count": event.break_count,
        "seal_amount": event.seal_amount,
        "seal_amount_unit": event.seal_amount_unit,
        "continuous_boards": event.continuous_boards,
        "reason": None,
    }


def _history_point(
    projection: MarketProjection,
    security_id: str,
    day: date,
) -> dict[str, Any]:
    state = projection.state(security_id, day)
    observation, reasons = projection.observation(security_id, day)
    return {
        "trade_date": day.isoformat(),
        "open": observation.open if observation is not None else None,
        "high": observation.high if observation is not None else None,
        "low": observation.low if observation is not None else None,
        "close": observation.close if observation is not None else None,
        "volume": observation.volume if observation is not None else None,
        "amount": observation.amount if observation is not None else None,
        "currency": observation.currency if observation is not None else None,
        "price_unit": observation.price_unit if observation is not None else None,
        "volume_unit": observation.volume_unit if observation is not None else None,
        "amount_unit": observation.amount_unit if observation is not None else None,
        "suspended": state.suspended if state is not None else None,
        "reasons": list(reasons),
    }


def _quote(
    projection: MarketProjection,
    security_id: str,
) -> dict[str, Any]:
    batch = projection.batch
    state = projection.state(security_id, batch.trade_date)
    current, observation_reasons = projection.observation(
        security_id,
        batch.trade_date,
    )
    reasons = set(observation_reasons)
    previous_close = current.previous_close if current is not None else None
    if current is not None and previous_close is None:
        sessions = batch.calendar.sessions
        position = sessions.index(batch.trade_date)
        if position:
            previous, previous_reasons = projection.observation(
                security_id,
                sessions[position - 1],
            )
            reasons.update(previous_reasons)
            if previous is not None and (
                previous.currency,
                previous.price_unit,
            ) != (
                current.currency,
                current.price_unit,
            ):
                reasons.add("unit_mismatch")
            elif previous is not None:
                previous_close = previous.close
        else:
            reasons.add("missing_previous_close")
    change = None
    if current is not None and previous_close is not None and not reasons:
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
        "currency": current.currency if current is not None else None,
        "price_unit": current.price_unit if current is not None else None,
        "volume_unit": current.volume_unit if current is not None else None,
        "amount_unit": current.amount_unit if current is not None else None,
        "suspended": state.suspended if state is not None else None,
        "reasons": sorted(reasons),
    }


def materialize_stocks(batch: MarketBatch) -> dict[str, Any]:
    """Build raw quotes and supplied history without synthesizing OHLC values."""
    projection = MarketProjection(batch)
    sessions = tuple(day for day in batch.calendar.sessions if day <= batch.trade_date)
    stocks = {}
    for security_id in dict.fromkeys(batch.expected_security_ids):
        history = [_history_point(projection, security_id, day) for day in sessions]
        observations = [point for point in history if point["close"] is not None]
        has_complete_ohlc = bool(observations) and all(
            all(point[field] is not None for field in ("open", "high", "low"))
            for point in observations
        )
        stocks[security_id] = {
            "quote": _quote(projection, security_id),
            "history": history,
            "seal": _seal(batch, security_id),
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
