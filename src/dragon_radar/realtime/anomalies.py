"""Deterministic, side-effect-free realtime anomaly calculations."""

from collections.abc import Sequence
from decimal import Decimal

from dragon_radar.realtime.models import (
    AnomalyResult,
    AnomalyRule,
    MinuteBarInput,
)


def _unavailable(rule: AnomalyRule, reason: str) -> AnomalyResult:
    return AnomalyResult(
        triggered=False,
        metric=rule.metric,
        window=rule.window,
        value=None,
        threshold=rule.threshold,
        reason=reason,
    )


def detect_anomalies(
    bars: Sequence[MinuteBarInput],
    rule: AnomalyRule,
) -> AnomalyResult:
    """Evaluate one rule over the latest deterministically ordered window."""

    if rule.metric == "order_book_imbalance":
        return _unavailable(rule, "order_book_unavailable")

    ordered = tuple(sorted(bars, key=lambda bar: (bar.event_at, bar.revision)))
    if len(ordered) < rule.window:
        return _unavailable(rule, "insufficient_window")

    window = ordered[-rule.window :]
    if rule.metric == "window_return":
        denominator = window[0].close
        if denominator == 0:
            return _unavailable(rule, "zero_denominator")
        value = window[-1].close / denominator - Decimal(1)
    else:
        reference = window[0].volume
        current = window[-1].volume
        if reference is None or current is None:
            return _unavailable(rule, "volume_unavailable")
        if reference == 0:
            return _unavailable(rule, "zero_denominator")
        value = current / reference

    return AnomalyResult(
        triggered=value >= rule.threshold,
        metric=rule.metric,
        window=rule.window,
        value=value,
        threshold=rule.threshold,
        reason=None,
    )


__all__ = ["detect_anomalies"]
