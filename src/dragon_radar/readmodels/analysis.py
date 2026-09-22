"""Pure publication-time analysis read models."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, localcontext
from typing import Any, Literal

from dragon_radar.analytics.calendar import TradingCalendar
from dragon_radar.analytics.metrics import (
    _batch_reasons,
    _decimal_context,
    _index,
    _observation,
    drawdown_curve,
    excess_return,
    interval_return,
    maximum_drawdown,
    normalized_curve,
)
from dragon_radar.domain.models import DailyBar, MarketBatch
from dragon_radar.domain.quality import state_at
from dragon_radar.topics.models import TopicBatch

ANALYSIS_SCHEMA = "dragon-radar.analysis"
ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_RULE_VERSION = "analysis-v1-decimal34"
Adjustment = Literal["raw", "qfq"]
BarIndex = dict[tuple[str, date], list[DailyBar]]


def _topic_memberships(
    batch: MarketBatch, topic_batch: TopicBatch | None
) -> dict[str, list[dict[str, str]]]:
    if topic_batch is None:
        return {}
    cutoff = min(batch.as_of, topic_batch.as_of)
    definitions = {
        topic.topic_id: topic
        for topic in topic_batch.topics
        if topic.available_at <= cutoff
        and topic.effective_from <= batch.trade_date
        and (topic.effective_to is None or batch.trade_date < topic.effective_to)
    }
    memberships: dict[str, list[dict[str, str]]] = defaultdict(list)
    for membership in topic_batch.memberships:
        topic = definitions.get(membership.topic_id)
        if (
            topic is not None
            and membership.available_at <= cutoff
            and membership.effective_from <= batch.trade_date
            and (
                membership.effective_to is None
                or batch.trade_date < membership.effective_to
            )
        ):
            memberships[membership.security_id].append(
                {"topic_id": topic.topic_id, "name": topic.name}
            )
    for values in memberships.values():
        values.sort(key=lambda item: item["topic_id"])
    return dict(memberships)


def _price(bar: DailyBar, adjustment: Adjustment) -> Decimal:
    if adjustment == "raw":
        return bar.close
    with localcontext(_decimal_context()):
        return bar.close * bar.adjustment_factor


def _amount_change(bars: Sequence[DailyBar]) -> tuple[Decimal | None, str | None]:
    start, end = bars[0].amount, bars[-1].amount
    if (
        start is None
        or end is None
        or not start.is_finite()
        or not end.is_finite()
        or start <= 0
        or end < 0
    ):
        return None, "amount_change_unavailable"
    return interval_return((start, end)), None


def _series(
    batch: MarketBatch,
    index: BarIndex,
    security_id: str,
    sessions: tuple[date, ...],
    adjustment: Adjustment,
) -> dict[str, Any]:
    reasons = set(_batch_reasons(batch))
    bars = []
    for day in sessions:
        observation, errors = _observation(batch, index, security_id, day)
        reasons.update(errors)
        if observation is not None:
            bars.append(observation)
    if len({(item.currency, item.price_unit) for item in bars}) > 1:
        reasons.add("unit_mismatch")

    complete = not reasons and len(bars) == len(sessions)
    if not complete:
        return {
            "return_ratio": None,
            "max_drawdown_ratio": None,
            "amount_change_ratio": None,
            "normalized": [None] * len(sessions),
            "drawdown": [None] * len(sessions),
            "reasons": sorted(reasons),
        }

    prices = tuple(_price(item, adjustment) for item in bars)
    amount_change, amount_reason = _amount_change(bars)
    if amount_reason is not None:
        reasons.add(amount_reason)
    return {
        "return_ratio": interval_return(prices),
        "max_drawdown_ratio": maximum_drawdown(prices),
        "amount_change_ratio": amount_change,
        "normalized": list(normalized_curve(prices)),
        "drawdown": list(drawdown_curve(prices)),
        "reasons": sorted(reasons),
    }


def _window(
    batch: MarketBatch,
    topic_batch: TopicBatch | None,
    sessions: tuple[date, ...],
    benchmark_id: str,
) -> dict[str, Any]:
    index = _index(batch)
    stock_ids = tuple(dict.fromkeys(batch.expected_security_ids))
    topics = _topic_memberships(batch, topic_batch)
    identities = {
        security_id: state_at(batch, security_id, batch.trade_date)
        for security_id in stock_ids
    }
    result: dict[str, Any] = {
        "offsets": len(sessions) - 1,
        "sessions": [day.isoformat() for day in sessions],
        "benchmark_id": benchmark_id,
        "adjustments": {},
    }
    for adjustment in ("raw", "qfq"):
        stock_series = {
            security_id: _series(
                batch, index, security_id, sessions, adjustment
            )
            for security_id in stock_ids
        }
        benchmark = _series(batch, index, benchmark_id, sessions, adjustment)
        benchmark_return = benchmark["return_ratio"]
        rows = []
        curves = {}
        for security_id in stock_ids:
            current = stock_series[security_id]
            reasons = list(current["reasons"])
            value = current["return_ratio"]
            relative = None
            if benchmark_return is None:
                reasons.append("benchmark_unavailable")
            elif value is not None:
                relative = excess_return(value, benchmark_return)
            identity = identities[security_id]
            rows.append(
                {
                    "rank": None,
                    "security_id": security_id,
                    "name": identity.name if identity is not None else None,
                    "topics": topics.get(security_id, []),
                    "return_ratio": value,
                    "excess_return_ratio": relative,
                    "max_drawdown_ratio": current["max_drawdown_ratio"],
                    "amount_change_ratio": current["amount_change_ratio"],
                    "reasons": sorted(set(reasons)),
                }
            )
            curves[security_id] = {
                "normalized": current["normalized"],
                "drawdown": current["drawdown"],
                "reasons": current["reasons"],
            }
        rows.sort(key=lambda item: item["security_id"])
        present = sorted(
            (item for item in rows if item["return_ratio"] is not None),
            key=lambda item: item["return_ratio"],
            reverse=True,
        )
        missing = [item for item in rows if item["return_ratio"] is None]
        rows = present + missing
        rank = 0
        for row in rows:
            if row["return_ratio"] is not None:
                rank += 1
                row["rank"] = rank
        curves[benchmark_id] = {
            "normalized": benchmark["normalized"],
            "drawdown": benchmark["drawdown"],
            "reasons": benchmark["reasons"],
        }
        result["adjustments"][adjustment] = {
            "ranking": rows,
            "curves": curves,
            "benchmark_return_ratio": benchmark_return,
            "benchmark_reasons": benchmark["reasons"],
        }
    return result


def materialize_analysis(
    batch: MarketBatch,
    topic_batch: TopicBatch | None = None,
    windows: Sequence[int] = (20, 60, 120),
    benchmark_id: str = "B000300",
) -> dict[str, Any]:
    """Build fixed N-offset windows from exactly N+1 supplied sessions."""
    calendar = TradingCalendar(batch.calendar)
    materialized = {}
    for size in windows:
        sessions = calendar.n_day_window(batch.trade_date, size)
        materialized[str(size)] = _window(
            batch, topic_batch, sessions, benchmark_id
        )
    return {
        "schema": ANALYSIS_SCHEMA,
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "rule_version": ANALYSIS_RULE_VERSION,
        "trade_date": batch.trade_date.isoformat(),
        "adjustment_version": batch.adjustment_version,
        "adjustments": {
            "raw": {
                "available": True,
                "price_basis": "raw_close",
                "reason": None,
            },
            "qfq": {
                "available": True,
                "price_basis": "close_times_adjustment_factor",
                "reason": None,
            },
            "hfq": {
                "available": False,
                "price_basis": None,
                "reason": "hfq_unavailable_from_single_adjustment_factor",
            },
        },
        "windows": materialized,
    }
