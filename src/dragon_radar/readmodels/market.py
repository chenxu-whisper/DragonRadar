"""Deterministic market observations materialized from publication-bound inputs."""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from dragon_radar.analytics.metrics import _decimal_context, _index, _observation
from dragon_radar.domain.models import LimitUpEvent, MarketBatch
from dragon_radar.domain.quality import state_at
from dragon_radar.topics.models import TopicBatch

RATIO_SCALE = Decimal("0.0001")


def _ratio(numerator: int, denominator: int, reason: str) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": (
            (Decimal(numerator) / Decimal(denominator)).quantize(
                RATIO_SCALE, rounding=ROUND_HALF_UP
            )
            if denominator
            else None
        ),
        "reason": None if denominator else reason,
    }


def _coverage(expected: int, valid: int, excluded: int, missing: int) -> dict:
    return {
        "expected": expected,
        "valid": valid,
        "excluded": excluded,
        "missing": missing,
    }


def _valid_events(batch: MarketBatch) -> dict[tuple[date, str], LimitUpEvent]:
    grouped: dict[tuple[date, str], list[LimitUpEvent]] = defaultdict(list)
    for event in batch.limit_up_events:
        if (
            event.source_id == batch.source_id
            and event.source_version == batch.source_version
            and event.available_at <= batch.as_of
            and event.trade_date <= batch.trade_date
        ):
            grouped[(event.trade_date, event.security_id)].append(event)
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}


def _daily_observations(batch: MarketBatch, day: date):
    bars = _index(batch)
    valid = {}
    excluded = 0
    missing = 0
    for security_id in dict.fromkeys(batch.expected_security_ids):
        current_state = state_at(batch, security_id, day)
        if current_state is not None and current_state.suspended:
            excluded += 1
            continue
        observation, reasons = _observation(batch, bars, security_id, day)
        if observation is None or reasons:
            missing += 1
        else:
            valid[security_id] = observation
    return valid, excluded, missing


def _daily_returns(batch: MarketBatch, day: date) -> tuple[dict[str, Decimal], dict]:
    sessions = batch.calendar.sessions
    try:
        position = sessions.index(day)
    except ValueError:
        return {}, _coverage(
            len(set(batch.expected_security_ids)),
            0,
            0,
            len(set(batch.expected_security_ids)),
        )
    current, excluded, missing = _daily_observations(batch, day)
    prior_day = sessions[position - 1] if position else None
    previous = _daily_observations(batch, prior_day)[0] if prior_day is not None else {}
    returns = {}
    for security_id, current_bar in current.items():
        reference = current_bar.previous_close
        if reference is None and security_id in previous:
            reference = previous[security_id].close
        if (
            reference is None
            or not reference.is_finite()
            or reference <= 0
            or current_bar.close is None
        ):
            missing += 1
            continue
        with localcontext(_decimal_context()):
            returns[security_id] = current_bar.close / reference - 1
    return returns, _coverage(
        len(set(batch.expected_security_ids)),
        len(returns),
        excluded,
        missing,
    )


def _mean(values: list[Decimal]) -> Decimal:
    with localcontext(_decimal_context()):
        return sum(values, Decimal(0)) / Decimal(len(values))


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _mean([ordered[middle - 1], ordered[middle]])


def _market_factors(batch: MarketBatch, returns: dict[str, Decimal]) -> dict:
    sessions = batch.calendar.sessions
    position = sessions.index(batch.trade_date)
    bars = _index(batch)
    expected = len(set(batch.expected_security_ids))

    ma_valid = 0
    above = 0
    ma_missing = 0
    if position >= 19:
        ma_days = sessions[position - 19 : position + 1]
        for security_id in dict.fromkeys(batch.expected_security_ids):
            prices = []
            for day in ma_days:
                observation, reasons = _observation(batch, bars, security_id, day)
                if observation is None or reasons:
                    break
                with localcontext(_decimal_context()):
                    prices.append(observation.close * observation.adjustment_factor)
            if len(prices) != 20:
                ma_missing += 1
                continue
            ma_valid += 1
            above += prices[-1] > _mean(prices)
    else:
        ma_missing = expected

    advancing = sum(value > 0 for value in returns.values())
    breadth_ratio = _ratio(advancing, len(returns), "no_valid_daily_returns")
    breadth_ratio.pop("reason")
    ma_ratio = _ratio(above, ma_valid, "no_valid_ma20_samples")
    ma_ratio["coverage"] = _coverage(expected, ma_valid, 0, ma_missing)

    amount = {
        "value": None,
        "today_total": None,
        "baseline_mean": None,
        "baseline_days": 20,
        "baseline_excludes_current": True,
        "coverage": _coverage(21, 0, 0, 21),
        "reason": "requires_complete_current_and_prior_20_sessions",
    }
    if position >= 20:
        amount_days = sessions[position - 20 : position + 1]
        totals = []
        for day in amount_days:
            observations, excluded, missing = _daily_observations(batch, day)
            values = [item.amount for item in observations.values()]
            if (
                excluded
                or missing
                or len(values) != expected
                or any(value is None or not value.is_finite() for value in values)
            ):
                totals.append(None)
            else:
                totals.append(sum(values, Decimal(0)))
        valid_days = sum(value is not None for value in totals)
        amount["coverage"] = _coverage(21, valid_days, 0, 21 - valid_days)
        if valid_days == 21:
            baseline = _mean(totals[:-1])
            today = totals[-1]
            if baseline > 0:
                with localcontext(_decimal_context()):
                    amount.update(
                        value=(today / baseline - 1).quantize(
                            RATIO_SCALE, rounding=ROUND_HALF_UP
                        ),
                        today_total=today,
                        baseline_mean=baseline,
                        reason=None,
                    )
            else:
                amount["reason"] = "baseline_mean_is_zero"

    indexes = []
    index_states = [
        item
        for item in batch.security_states
        if item.security_type == "index"
        and item.effective_from <= batch.trade_date
        and (item.effective_to is None or batch.trade_date < item.effective_to)
        and item.available_at <= batch.as_of
    ]
    for index_state in sorted(index_states, key=lambda item: item.security_id):
        closes = []
        for day in sessions[: position + 1]:
            observation, reasons = _observation(
                batch, bars, index_state.security_id, day
            )
            closes.append(
                observation.close if observation is not None and not reasons else None
            )
        available = [value for value in closes if value is not None]
        item = {
            "security_id": index_state.security_id,
            "code": index_state.code,
            "name": index_state.name,
            "close": closes[-1] if closes else None,
            "close_over_ma20": None,
            "close_over_ma60": None,
            "volatility_20": None,
            "volatility_method": "sample_stddev_non_annualized",
            "coverage": {
                "ma20": min(len(available), 20),
                "ma60": min(len(available), 60),
                "return20": 0,
            },
            "reason": None,
        }
        if len(closes) < 60 or any(value is None for value in closes[-60:]):
            item["reason"] = "requires_60_consecutive_index_closes"
            indexes.append(item)
            continue
        latest = closes[-1]
        with localcontext(_decimal_context()):
            item["close_over_ma20"] = (latest / _mean(closes[-20:]) - 1).quantize(
                RATIO_SCALE, rounding=ROUND_HALF_UP
            )
            item["close_over_ma60"] = (latest / _mean(closes[-60:]) - 1).quantize(
                RATIO_SCALE, rounding=ROUND_HALF_UP
            )
            returns20 = [
                closes[offset] / closes[offset - 1] - 1
                for offset in range(len(closes) - 20, len(closes))
            ]
            average_return = _mean(returns20)
            variance = sum(
                ((value - average_return) ** 2 for value in returns20), Decimal(0)
            ) / Decimal(len(returns20) - 1)
            item["volatility_20"] = variance.sqrt().quantize(
                RATIO_SCALE, rounding=ROUND_HALF_UP
            )
            item["coverage"]["return20"] = 20
        indexes.append(item)

    return {
        "FAC-M01": {
            "advancing": breadth_ratio,
            "above_ma20": ma_ratio,
            "method": "adjusted_close_ma20_including_current",
        },
        "FAC-M02": {
            **amount,
            "method": "same_pool_amount_vs_prior_20_session_mean",
        },
        "FAC-M03": {
            "indexes": indexes,
            "method": "close_over_ma20_ma60_and_20_return_sample_stddev_non_annualized",
        },
    }


def _sentiment(
    batch: MarketBatch,
    returns: dict[str, Decimal],
    events: dict[tuple[date, str], LimitUpEvent],
) -> tuple[dict, list[dict], list[dict]]:
    sessions = batch.calendar.sessions
    position = sessions.index(batch.trade_date)
    bars = _index(batch)

    def daily(day: date) -> dict:
        day_position = sessions.index(day)
        prior_day = sessions[day_position - 1] if day_position else None
        day_events = {
            security_id: event
            for (event_day, security_id), event in events.items()
            if event_day == day and security_id in batch.expected_security_ids
        }
        sealed = {
            security_id
            for security_id, event in day_events.items()
            if event.is_limit_up
        }
        current_bars = _daily_observations(batch, day)[0]
        limit_down = sum(
            bar.limit_status == "limited"
            and bar.limit_date == day
            and bar.limit_down is not None
            and bar.close == bar.limit_down
            for bar in current_bars.values()
        )
        prior_limit_up = {
            security_id
            for (event_day, security_id), event in events.items()
            if event_day == prior_day
            and event.is_limit_up
            and security_id in batch.expected_security_ids
        }
        prior_ge2 = {
            security_id
            for security_id in prior_limit_up
            if events[(prior_day, security_id)].continuous_boards >= 2
        }
        tradable = set()
        excluded = 0
        missing = 0
        for security_id in prior_ge2:
            current_state = state_at(batch, security_id, day)
            if current_state is not None and current_state.suspended:
                excluded += 1
                continue
            observation, reasons = _observation(batch, bars, security_id, day)
            if observation is None or reasons:
                missing += 1
            else:
                tradable.add(security_id)
        promote = _ratio(
            len(tradable & sealed),
            len(tradable),
            "no_tradable_members_from_yesterday_ge2_set",
        )
        promote.update(
            yesterday_ge2=len(prior_ge2),
            excluded=excluded,
            missing=missing,
        )
        day_returns = (
            returns if day == batch.trade_date else _daily_returns(batch, day)[0]
        )
        return {
            "trade_date": day,
            "limit_up_count": len(sealed),
            "limit_down_count": limit_down,
            "max_boards": max(
                (
                    event.continuous_boards
                    for event in day_events.values()
                    if event.is_limit_up
                ),
                default=0,
            ),
            "promote_rate": promote,
            "yesterday_limit_up_median_return": _median(
                [day_returns[item] for item in prior_limit_up if item in day_returns]
            ),
            "yesterday_limit_up_return_coverage": {
                "expected": len(prior_limit_up),
                "valid": sum(item in day_returns for item in prior_limit_up),
                "excluded": 0,
                "missing": sum(item not in day_returns for item in prior_limit_up),
            },
        }

    series = []
    heatmap = []
    for day in sessions[: position + 1]:
        daily_events = [
            event
            for (event_day, security_id), event in events.items()
            if event_day == day and security_id in batch.expected_security_ids
        ]
        if not daily_events:
            continue
        sealed_events = [event for event in daily_events if event.is_limit_up]
        counts: dict[int, int] = defaultdict(int)
        for event in sealed_events:
            if event.continuous_boards >= 1:
                counts[event.continuous_boards] += 1
        heatmap.extend(
            {"trade_date": day, "boards": boards, "count": count}
            for boards, count in sorted(counts.items())
        )
        series.append(daily(day))
    current = daily(batch.trade_date)
    current.pop("trade_date")
    return current, series, heatmap


def _topics(
    batch: MarketBatch,
    topic_batch: TopicBatch | None,
    returns: dict[str, Decimal],
    events: dict[tuple[date, str], LimitUpEvent],
) -> tuple[list[dict], dict]:
    if topic_batch is None:
        return [], {"enabled": False, "reason": "topic_input_not_published"}
    definitions = {
        item.topic_id: item
        for item in topic_batch.topics
        if item.available_at <= topic_batch.as_of
        and item.effective_from <= batch.trade_date
        and (item.effective_to is None or batch.trade_date < item.effective_to)
    }
    members: dict[str, set[str]] = defaultdict(set)
    for membership in topic_batch.memberships:
        if (
            membership.topic_id in definitions
            and membership.available_at <= topic_batch.as_of
            and membership.effective_from <= batch.trade_date
            and (
                membership.effective_to is None
                or batch.trade_date < membership.effective_to
            )
        ):
            members[membership.topic_id].add(membership.security_id)
    coverage_cells = {
        cell.topic_id: cell
        for cell in topic_batch.coverage_cells
        if cell.trade_date == batch.trade_date
        and cell.complete_as_of <= topic_batch.as_of
    }
    sectors = []
    for topic_id, definition in sorted(definitions.items()):
        ids = members[topic_id]
        cell = coverage_cells.get(topic_id)
        coverage_ready = (
            cell is not None
            and cell.status == "complete"
            and cell.received_member_count == cell.expected_member_count
        )
        values = (
            [returns[item] for item in ids if item in returns] if coverage_ready else []
        )
        limit_count = (
            sum(
                events.get((batch.trade_date, item)) is not None
                and events[(batch.trade_date, item)].is_limit_up
                for item in ids
            )
            if coverage_ready
            else None
        )
        flags = []
        if len(ids) < 5:
            flags.append("sample_insufficient")
        if not coverage_ready:
            flags.append("topic_coverage_unavailable")
        expected = cell.expected_member_count if cell is not None else len(ids)
        sectors.append(
            {
                "sector_id": topic_id,
                "name": definition.name,
                "member_count": len(ids),
                "valid_returns": len(values),
                "up_ratio": _ratio(
                    sum(value > 0 for value in values),
                    len(values),
                    (
                        "no_valid_member_returns"
                        if coverage_ready
                        else "topic_coverage_unavailable"
                    ),
                ),
                "limit_up_ratio": _ratio(
                    limit_count or 0,
                    len(values),
                    (
                        "no_valid_member_returns"
                        if coverage_ready
                        else "topic_coverage_unavailable"
                    ),
                ),
                "median_return": _median(values),
                "change_ratio": _median(values),
                "limit_up_count": limit_count,
                "net_inflow": None,
                "net_inflow_reason": "source_field_unavailable",
                "reason": (None if coverage_ready else "topic_coverage_unavailable"),
                "quality_flags": flags,
                "coverage": _coverage(expected, len(values), 0, expected - len(values)),
            }
        )
    historical = any(
        membership.effective_from < batch.trade_date
        for membership in topic_batch.memberships
    )
    return sectors, {
        "enabled": historical,
        "reason": None if historical else "historical_topic_membership_unavailable",
    }


def materialize_market(
    batch: MarketBatch, topic_batch: TopicBatch | None
) -> dict[str, Any]:
    """Build every Task09 read model once, while publication inputs are bound."""
    returns, return_coverage = _daily_returns(batch, batch.trade_date)
    events = _valid_events(batch)
    sentiment, sentiment_series, heatmap = _sentiment(batch, returns, events)
    factors = _market_factors(batch, returns)
    sectors, historical_topics = _topics(batch, topic_batch, returns, events)
    breadth = {
        "up": sum(value > 0 for value in returns.values()),
        "down": sum(value < 0 for value in returns.values()),
        "flat": sum(value == 0 for value in returns.values()),
        "valid_total": len(returns),
        "coverage": return_coverage,
        "seal_rate": _ratio(
            sum(
                event.is_limit_up and event.touched_limit is True
                for (day, security_id), event in events.items()
                if day == batch.trade_date
                and security_id in batch.expected_security_ids
            ),
            sum(
                event.touched_limit is True
                for (day, security_id), event in events.items()
                if day == batch.trade_date
                and security_id in batch.expected_security_ids
            ),
            "no_known_touched_limit_observations",
        ),
    }
    securities = []
    for security_id in sorted(set(batch.expected_security_ids)):
        security_state = state_at(batch, security_id, batch.trade_date)
        if security_state is not None:
            securities.append(
                {
                    "security_id": security_id,
                    "code": security_state.code,
                    "name": security_state.name,
                    "exchange": security_state.exchange,
                }
            )
    bar_index = _index(batch)
    current_position = batch.calendar.sessions.index(batch.trade_date)
    prior_day = (
        batch.calendar.sessions[current_position - 1] if current_position else None
    )
    indexes = []
    for item in factors["FAC-M03"]["indexes"]:
        current_bar, current_reasons = _observation(
            batch, bar_index, item["security_id"], batch.trade_date
        )
        prior_bar, prior_reasons = (
            _observation(batch, bar_index, item["security_id"], prior_day)
            if prior_day is not None
            else (None, ("missing_previous_close",))
        )
        change = None
        if (
            current_bar is not None
            and prior_bar is not None
            and not current_reasons
            and not prior_reasons
            and prior_bar.close > 0
        ):
            with localcontext(_decimal_context()):
                change = (current_bar.close / prior_bar.close - 1).quantize(
                    RATIO_SCALE, rounding=ROUND_HALF_UP
                )
        indexes.append(
            {
                "security_id": item["security_id"],
                "code": item["code"],
                "name": item["name"],
                "last": item["close"],
                "change_ratio": change,
                "amount": current_bar.amount if current_bar is not None else None,
                "reason": None if item["close"] is not None else item["reason"],
            }
        )
    return {
        "overview": {
            # Retain Task02 field names while reset consumers migrate to breadth.
            "advancing": breadth["up"],
            "declining": breadth["down"],
            "unchanged": breadth["flat"],
            "returns": [
                {
                    "security_id": security_id,
                    "value": value,
                    "basis": "explicit_or_prior_session_close",
                    "warnings": [],
                }
                for security_id, value in sorted(returns.items())
            ],
            "exclusions": [],
            "indexes": indexes,
            "breadth": breadth,
            "sentiment": sentiment,
            "hot_sectors": sectors,
            "factors": factors,
            "historical_topic_analysis": historical_topics,
        },
        "sector_flow": {"sectors": sectors},
        "sentiment": {"series": sentiment_series},
        "heatmap": {
            "columns": sorted({cell["boards"] for cell in heatmap}),
            "cells": heatmap,
        },
        "search": {"securities": securities, "topics": [], "messages": []},
    }
