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
    excess_return,
    interval_return,
    maximum_drawdown,
)
from dragon_radar.domain.models import DailyBar, MarketBatch
from dragon_radar.readmodels.projection import MarketProjection
from dragon_radar.topics.models import TopicBatch

ANALYSIS_SCHEMA = "dragon-radar.analysis"
ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_RULE_VERSION = "analysis-v1-decimal34"
Adjustment = Literal["raw", "adjusted"]


def _topic_memberships(
    batch: MarketBatch, topic_batch: TopicBatch | None
) -> tuple[
    dict[str, list[dict[str, str]]],
    Literal["available", "unavailable"],
    tuple[str, ...],
]:
    if topic_batch is None:
        return {}, "unavailable", ("topic_batch_unavailable",)
    if (
        topic_batch.trade_date != batch.trade_date
        or topic_batch.exchange != batch.calendar.exchange
    ):
        return {}, "unavailable", ("topic_binding_mismatch",)
    cutoff = min(batch.as_of, topic_batch.as_of)
    definitions = {
        topic.topic_id: topic
        for topic in topic_batch.topics
        if topic.available_at <= cutoff
        and topic.effective_from <= batch.trade_date
        and (topic.effective_to is None or batch.trade_date < topic.effective_to)
    }
    visible_memberships = {
        topic_id: {
            membership.security_id
            for membership in topic_batch.memberships
            if membership.topic_id == topic_id
            and membership.available_at <= cutoff
            and membership.effective_from <= batch.trade_date
            and (
                membership.effective_to is None
                or batch.trade_date < membership.effective_to
            )
        }
        for topic_id in definitions
    }
    current_cells = [
        cell
        for cell in topic_batch.coverage_cells
        if cell.trade_date == batch.trade_date and cell.complete_as_of <= cutoff
    ]
    if not current_cells or {cell.topic_id for cell in current_cells} != set(
        definitions
    ):
        return {}, "unavailable", ("topic_coverage_unavailable",)
    for topic_id in definitions:
        cells = [cell for cell in current_cells if cell.topic_id == topic_id]
        coverage_ready = (
            len(cells) == 1
            and cells[0].status == "complete"
            and cells[0].received_member_count == cells[0].expected_member_count
            and len(visible_memberships[topic_id]) == cells[0].received_member_count
        )
        if not coverage_ready:
            return {}, "unavailable", ("topic_coverage_unavailable",)

    memberships: dict[str, list[dict[str, str]]] = defaultdict(list)
    for topic_id, topic in definitions.items():
        for security_id in visible_memberships[topic_id]:
            memberships[security_id].append(
                {"topic_id": topic.topic_id, "name": topic.name}
            )
    for values in memberships.values():
        values.sort(key=lambda item: item["topic_id"])
    return dict(memberships), "available", ()


def _price(bar: DailyBar, adjustment: Adjustment) -> Decimal:
    if adjustment == "raw":
        return bar.close
    with localcontext(_decimal_context()):
        return bar.close * bar.adjustment_factor


def _amount_change(bars: Sequence[DailyBar]) -> tuple[Decimal | None, str | None]:
    if len(bars) < 6:
        return None, "amount_change_unavailable"
    amounts = tuple(bar.amount for bar in bars[-6:])
    if any(
        amount is None or not amount.is_finite() or amount < 0 for amount in amounts
    ):
        return None, "amount_change_unavailable"
    current = amounts[-1]
    with localcontext(_decimal_context()):
        baseline = sum(amounts[:-1], start=Decimal(0)) / Decimal(5)
        if baseline == 0:
            return None, "amount_change_unavailable"
        return current / baseline - 1, None


def _display_curves(
    bars: Sequence[DailyBar | None],
    adjustment: Adjustment,
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    if not bars or bars[0] is None:
        return [None] * len(bars), [None] * len(bars)
    baseline = _price(bars[0], adjustment)
    peak = baseline
    normalized = []
    drawdown = []
    with localcontext(_decimal_context()):
        for bar in bars:
            if bar is None:
                normalized.append(None)
                drawdown.append(None)
                continue
            price = _price(bar, adjustment)
            peak = max(peak, price)
            normalized.append(Decimal(100) * price / baseline)
            drawdown.append(1 - price / peak)
    return normalized, drawdown


def _series(
    projection: MarketProjection,
    security_id: str,
    sessions: tuple[date, ...],
    adjustment: Adjustment,
) -> dict[str, Any]:
    batch = projection.batch
    reasons = set(_batch_reasons(batch))
    bars = []
    for day in sessions:
        observation, errors = projection.observation(security_id, day)
        reasons.update(errors)
        bars.append(observation)
    valid_bars = [item for item in bars if item is not None]
    unit_mismatch = len({(item.currency, item.price_unit) for item in valid_bars}) > 1
    if unit_mismatch:
        reasons.add("unit_mismatch")

    amount_bars = []
    for day in sessions[-6:]:
        records = projection.bars.get((security_id, day), [])
        if len(records) != 1:
            amount_bars = []
            break
        amount_bars.append(records[0])
    amount_change, amount_reason = _amount_change(amount_bars)

    display_normalized, display_drawdown = _display_curves(bars, adjustment)
    if _batch_reasons(batch) or unit_mismatch:
        display_normalized = [None] * len(sessions)
        display_drawdown = [None] * len(sessions)
    complete = not reasons and len(valid_bars) == len(sessions)
    if not complete:
        if amount_reason is not None:
            reasons.add(amount_reason)
        return {
            "return_ratio": None,
            "max_drawdown_ratio": None,
            "amount_change_ratio": None,
            "normalized": display_normalized,
            "drawdown": display_drawdown,
            "reasons": sorted(reasons),
        }

    prices = tuple(_price(item, adjustment) for item in valid_bars)
    if amount_reason is not None:
        reasons.add(amount_reason)
    return {
        "return_ratio": interval_return(prices),
        "max_drawdown_ratio": maximum_drawdown(prices),
        "amount_change_ratio": amount_change,
        "normalized": display_normalized,
        "drawdown": display_drawdown,
        "reasons": sorted(reasons),
    }


def _window(
    projection: MarketProjection,
    topic_batch: TopicBatch | None,
    sessions: tuple[date, ...],
    benchmark_id: str,
) -> dict[str, Any]:
    batch = projection.batch
    stock_ids = tuple(
        security_id
        for security_id in dict.fromkeys(batch.expected_security_ids)
        if (
            (current_state := projection.state(security_id, batch.trade_date)) is None
            or current_state.security_type != "index"
        )
    )
    benchmark_state = projection.state(benchmark_id, batch.trade_date)
    benchmark_identity_valid = (
        benchmark_state is not None and benchmark_state.security_type == "index"
    )
    topics, topic_status, topic_reasons = _topic_memberships(batch, topic_batch)
    identities = {
        security_id: projection.state(security_id, batch.trade_date)
        for security_id in stock_ids
    }
    result: dict[str, Any] = {
        "available": True,
        "reason": None,
        "offsets": len(sessions) - 1,
        "sessions": [day.isoformat() for day in sessions],
        "benchmark_id": benchmark_id,
        "adjustments": {},
    }
    for adjustment in ("raw", "adjusted"):
        stock_series = {
            security_id: _series(projection, security_id, sessions, adjustment)
            for security_id in stock_ids
        }
        benchmark = (
            _series(projection, benchmark_id, sessions, adjustment)
            if benchmark_identity_valid
            else {
                "return_ratio": None,
                "normalized": [None] * len(sessions),
                "drawdown": [None] * len(sessions),
                "reasons": ["benchmark_identity_invalid"],
            }
        )
        benchmark_return = benchmark["return_ratio"]
        rows = []
        exclusions = []
        curves = {}
        for security_id in stock_ids:
            current = stock_series[security_id]
            reasons = list(current["reasons"])
            value = current["return_ratio"]
            relative = None
            if benchmark_return is None:
                reasons.append(
                    "benchmark_unavailable"
                    if benchmark_identity_valid
                    else "benchmark_identity_invalid"
                )
            elif value is not None:
                relative = excess_return(value, benchmark_return)
            identity = identities[security_id]
            row_reasons = sorted(set(reasons))
            if value is None:
                exclusions.append(
                    {
                        "security_id": security_id,
                        "reasons": row_reasons,
                    }
                )
            else:
                rows.append(
                    {
                        "rank": None,
                        "security_id": security_id,
                        "name": identity.name if identity is not None else None,
                        "topics": topics.get(security_id, []),
                        "topic_status": topic_status,
                        "topic_reasons": list(topic_reasons),
                        "return_ratio": value,
                        "excess_return_ratio": relative,
                        "max_drawdown_ratio": current["max_drawdown_ratio"],
                        "amount_change_ratio": current["amount_change_ratio"],
                        "reasons": row_reasons,
                    }
                )
            curves[security_id] = {
                "normalized": current["normalized"],
                "drawdown": current["drawdown"],
                "reasons": current["reasons"],
            }
        rows.sort(key=lambda item: item["security_id"])
        rows = sorted(
            rows,
            key=lambda item: item["return_ratio"],
            reverse=True,
        )
        previous_return = None
        current_rank = 0
        for position, row in enumerate(rows, start=1):
            value = row["return_ratio"]
            if value is None:
                continue
            if previous_return is None or value != previous_return:
                current_rank = position
                previous_return = value
            row["rank"] = current_rank
        curves.setdefault(
            benchmark_id,
            {
                "normalized": benchmark["normalized"],
                "drawdown": benchmark["drawdown"],
                "reasons": benchmark["reasons"],
            },
        )
        result["adjustments"][adjustment] = {
            "ranking": rows,
            "exclusions": exclusions,
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
    projection = MarketProjection(batch)
    materialized = {}
    for size in windows:
        try:
            sessions = calendar.n_day_window(batch.trade_date, size)
        except ValueError as error:
            if str(error) != "insufficient calendar history for requested window":
                raise
            materialized[str(size)] = {
                "requested_offsets": size,
                "available": False,
                "reason": "insufficient_calendar_history",
            }
            continue
        materialized[str(size)] = _window(
            projection,
            topic_batch,
            sessions,
            benchmark_id,
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
            "adjusted": {
                "available": True,
                "price_basis": "close_times_adjustment_factor",
                "reason": None,
            },
            "qfq": {
                "available": False,
                "price_basis": None,
                "reason": "qfq_semantics_unproven_from_generic_adjustment_factor",
            },
            "hfq": {
                "available": False,
                "price_basis": None,
                "reason": "hfq_semantics_unproven_from_generic_adjustment_factor",
            },
        },
        "windows": materialized,
    }
