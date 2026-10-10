"""Deterministic, side-effect-free realtime anomaly calculations."""

from collections.abc import Sequence
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from dragon_radar.realtime.models import (
    AnomalyResult,
    AnomalyRule,
    MinuteBarInput,
)


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
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

    if len({bar.security_id for bar in bars}) > 1:
        raise ValueError("anomaly bars must have the same security_id")

    if rule.metric == "order_book_imbalance":
        return _unavailable(rule, "order_book_unavailable")

    latest: dict[tuple[str, datetime], MinuteBarInput] = {}
    for bar in bars:
        key = (bar.security_id, bar.event_at)
        previous = latest.get(key)
        if previous is not None and previous.revision == bar.revision:
            raise ValueError("duplicate minute bar revision")
        if previous is None or bar.revision > previous.revision:
            latest[key] = bar
    ordered = tuple(sorted(latest.values(), key=lambda bar: bar.event_at))
    if len(ordered) < rule.window:
        return _unavailable(rule, "insufficient_window")

    window = ordered[-rule.window :]
    if rule.metric == "window_return":
        denominator = window[0].close
        if denominator == 0:
            return _unavailable(rule, "zero_denominator")
        with localcontext(_decimal_context()):
            current, denominator, one = (
                +window[-1].close,
                +denominator,
                +Decimal(1),
            )
            value = current / denominator - one
    else:
        reference = window[0].volume
        current = window[-1].volume
        if reference is None or current is None:
            return _unavailable(rule, "volume_unavailable")
        if reference == 0:
            return _unavailable(rule, "zero_denominator")
        with localcontext(_decimal_context()):
            value = +current / +reference

    return AnomalyResult(
        triggered=value >= rule.threshold,
        metric=rule.metric,
        window=rule.window,
        value=value,
        threshold=rule.threshold,
        reason=None,
    )


__all__ = ["detect_anomalies"]
