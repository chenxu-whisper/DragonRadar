"""Deterministic 0-100 sentiment research score (sentiment-research-v1).

Pure functions only. The market read model binds same-publication facts to
these builders at publication time; read routes never recompute them. Float
must never enter the financial math: component inputs are unrounded Decimal
ratios, while component ``value`` fields carry a separate 4-decimal display
quantization that is never mixed into the score.
"""

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

from dragon_radar.analytics.metrics import _decimal_context

METHOD_VERSION = "sentiment-research-v1"
RESEARCH_ONLY = True

SCORE_SCALE = Decimal("0.01")
DISPLAY_RATIO_SCALE = Decimal("0.0001")
ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
THREE = Decimal(3)

REASON_NO_VALID_DAILY_RETURNS = "no_valid_daily_returns"
REASON_NO_LIMIT_EVENTS = "no_limit_events"
REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET = (
    "no_tradable_members_from_yesterday_ge2_set"
)

COMPONENT_BREADTH = "breadth"
COMPONENT_LIMIT_BALANCE = "limit_balance"
COMPONENT_PROMOTION = "promotion"
COMPONENT_KEYS = (
    COMPONENT_BREADTH,
    COMPONENT_LIMIT_BALANCE,
    COMPONENT_PROMOTION,
)

PHASE_ICE = "ice"
PHASE_EBB = "ebb"
PHASE_DIVERGENT = "divergent"
PHASE_REPAIR = "repair"
PHASE_CLIMAX = "climax"
PHASES = (
    PHASE_ICE,
    PHASE_EBB,
    PHASE_DIVERGENT,
    PHASE_REPAIR,
    PHASE_CLIMAX,
)

# Equal weights expressed as an exact fraction to avoid 1/3 floating error.
WEIGHT = {"numerator": 1, "denominator": 3}

_COMPONENT_REASONS = {
    COMPONENT_BREADTH: REASON_NO_VALID_DAILY_RETURNS,
    COMPONENT_LIMIT_BALANCE: REASON_NO_LIMIT_EVENTS,
    COMPONENT_PROMOTION: REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
}
_COVERAGE_KEYS = frozenset({"expected", "valid", "excluded", "missing"})
_POINT_KEYS = frozenset({"trade_date", "score", "phase", "components", "reasons"})


class ResearchBlockError(ValueError):
    """A research block or point violates the closed sentiment-research shape."""


def phase_for(score: Decimal) -> str:
    """Map a 0-100 score to its contemporaneous bucket (no time-order claim)."""
    _validate_score(score)
    if score < Decimal(20):
        return PHASE_ICE
    if score < Decimal(40):
        return PHASE_EBB
    if score < Decimal(60):
        return PHASE_DIVERGENT
    if score < Decimal(80):
        return PHASE_REPAIR
    return PHASE_CLIMAX


def _validate_score(score: Any) -> None:
    if isinstance(score, bool) or not isinstance(score, Decimal):
        raise ResearchBlockError("score must be a Decimal")
    if not score.is_finite() or not ZERO <= score <= HUNDRED:
        raise ResearchBlockError("score must be a finite Decimal within 0-100")
    if score % SCORE_SCALE:
        raise ResearchBlockError("score must be quantized to two decimals")


def _validate_ratio(key: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ResearchBlockError(f"{key} requires an unrounded Decimal ratio")
    if not value.is_finite() or not ZERO <= value <= ONE:
        raise ResearchBlockError(f"{key} ratio must be finite and within [0, 1]")


def _validate_counts(numerator: Any, denominator: Any) -> None:
    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, int)
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or numerator < 0
        or denominator < 0
        or numerator > denominator
    ):
        raise ResearchBlockError("component counts must satisfy 0 <= num <= denom")


def score_components(
    breadth: Decimal | None,
    limit_balance: Decimal | None,
    promotion: Decimal | None,
) -> tuple[Decimal | None, str | None, tuple[str, ...]]:
    """Combine three unrounded ratios with equal weight, or fail the day honestly.

    Any unavailable component nulls score/phase and reports every reason;
    weights are never redistributed and no zero fill is substituted.
    """
    values = (breadth, limit_balance, promotion)
    reasons: list[str] = []
    for key, value in zip(COMPONENT_KEYS, values, strict=True):
        if value is None:
            reasons.append(_COMPONENT_REASONS[key])
        else:
            _validate_ratio(key, value)
    if reasons:
        return None, None, tuple(reasons)
    with localcontext(_decimal_context()):
        score = (HUNDRED * (breadth + limit_balance + promotion) / THREE).quantize(
            SCORE_SCALE, rounding=ROUND_HALF_UP
        )
    return score, phase_for(score), ()


def build_component(
    numerator: int,
    denominator: int,
    unavailable_reason: str,
    *,
    coverage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Freeze one component's display shape from integer counts."""
    _validate_counts(numerator, denominator)
    if unavailable_reason not in _COMPONENT_REASONS.values():
        raise ResearchBlockError("unknown component unavailable reason")
    if coverage is not None:
        if not isinstance(coverage, Mapping) or set(coverage) != _COVERAGE_KEYS:
            raise ResearchBlockError("coverage needs expected/valid/excluded/missing")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in coverage.values()
        ):
            raise ResearchBlockError("coverage counts must be non-negative ints")
    component: dict[str, Any] = {
        "value": None,
        "numerator": numerator,
        "denominator": denominator,
        "weight": {**WEIGHT},
        "reason": None if denominator else unavailable_reason,
    }
    if denominator:
        with localcontext(_decimal_context()):
            ratio = Decimal(numerator) / Decimal(denominator)
        component["value"] = ratio.quantize(DISPLAY_RATIO_SCALE, rounding=ROUND_HALF_UP)
    if coverage is not None:
        component["coverage"] = dict(coverage)
    return component


def _raw_ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    with localcontext(_decimal_context()):
        return Decimal(numerator) / Decimal(denominator)


def _validate_component(key: str, component: Any) -> None:
    if not isinstance(component, Mapping):
        raise ResearchBlockError(f"{key} component must be an object")
    allowed = {"value", "numerator", "denominator", "weight", "reason"}
    if "coverage" in component:
        allowed = allowed | {"coverage"}
    if set(component) != allowed:
        raise ResearchBlockError(f"{key} component has unexpected keys")
    numerator = component["numerator"]
    denominator = component["denominator"]
    _validate_counts(numerator, denominator)
    weight = component["weight"]
    if (
        not isinstance(weight, Mapping)
        or set(weight) != {"numerator", "denominator"}
        or type(weight["numerator"]) is not int
        or type(weight["denominator"]) is not int
        or weight != WEIGHT
    ):
        raise ResearchBlockError(f"{key} component weight must be the integer 1/3")
    reason = component["reason"]
    expected_reason = _COMPONENT_REASONS[key]
    value = component["value"]
    if denominator == 0:
        if reason != expected_reason or value is not None:
            raise ResearchBlockError(f"{key} unavailable component is malformed")
    else:
        if reason is not None or value is None:
            raise ResearchBlockError(f"{key} available component is malformed")
        _validate_ratio(key, value)
        if value % DISPLAY_RATIO_SCALE:
            raise ResearchBlockError(f"{key} value must be quantized to four decimals")
    if "coverage" in component:
        coverage = component["coverage"]
        if not isinstance(coverage, Mapping) or set(coverage) != _COVERAGE_KEYS:
            raise ResearchBlockError(f"{key} coverage shape is malformed")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in coverage.values()
        ):
            raise ResearchBlockError(f"{key} coverage counts must be non-negative ints")


def _validate_point(point: Any) -> None:
    if not isinstance(point, Mapping) or set(point) != _POINT_KEYS:
        raise ResearchBlockError("research point has unexpected keys")
    if type(point["trade_date"]) is not date:
        raise ResearchBlockError("research point trade_date must be a date")
    components = point["components"]
    if not isinstance(components, Mapping) or set(components) != set(COMPONENT_KEYS):
        raise ResearchBlockError("research point needs exactly three components")
    for key in COMPONENT_KEYS:
        _validate_component(key, components[key])
    reasons = point["reasons"]
    if not isinstance(reasons, list) or any(
        not isinstance(item, str) for item in reasons
    ):
        raise ResearchBlockError("research point reasons must be a string list")
    expected_reasons = [
        components[key]["reason"]
        for key in COMPONENT_KEYS
        if components[key]["reason"] is not None
    ]
    if reasons != expected_reasons:
        raise ResearchBlockError("research point reasons mismatch components")
    score = point["score"]
    phase = point["phase"]
    if expected_reasons:
        if score is not None or phase is not None:
            raise ResearchBlockError("incomplete point must not carry score/phase")
        return
    _validate_score(score)
    if phase_for(score) != phase or phase not in PHASES:
        raise ResearchBlockError("research point phase does not match score")


def build_research_point(
    trade_date: date, components: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    """Assemble one trade-date point from the three frozen components."""
    if type(trade_date) is not date:
        raise ResearchBlockError("trade_date must be a date")
    if not isinstance(components, Mapping) or set(components) != set(COMPONENT_KEYS):
        raise ResearchBlockError("research point requires exactly three components")
    ordered = {
        key: {**components[key], "weight": {**components[key]["weight"]}}
        for key in COMPONENT_KEYS
    }
    ratios = {
        key: (
            None
            if ordered[key]["reason"] is not None
            else _raw_ratio(ordered[key]["numerator"], ordered[key]["denominator"])
        )
        for key in COMPONENT_KEYS
    }
    score, phase, reasons = score_components(
        ratios[COMPONENT_BREADTH],
        ratios[COMPONENT_LIMIT_BALANCE],
        ratios[COMPONENT_PROMOTION],
    )
    point = {
        "trade_date": trade_date,
        "score": score,
        "phase": phase,
        "components": ordered,
        "reasons": list(reasons),
    }
    _validate_point(point)
    return point


def materialize_sentiment_research(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap the ordered, publication-bound series in its self-describing block."""
    series = list(points)
    for point in series:
        _validate_point(point)
    return {
        "method_version": METHOD_VERSION,
        "research_only": RESEARCH_ONLY,
        "series": series,
    }


_BLOCK_KEYS = frozenset({"method_version", "research_only", "series"})


def _archived_date(value: Any) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ResearchBlockError(
                "research point trade_date must be an ISO calendar date"
            ) from None
    raise ResearchBlockError("research point trade_date must be an ISO date string")


def _archived_score(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ResearchBlockError("research point score must be a decimal string")
    if isinstance(value, str):
        try:
            score = Decimal(value)
        except InvalidOperation:
            raise ResearchBlockError("research point score is not numeric") from None
    else:
        score = value
    if not score.is_finite() or not ZERO <= score <= HUNDRED:
        raise ResearchBlockError("research point score must be finite within 0-100")
    return score


def _archived_point(point: Any) -> dict[str, Any]:
    if not isinstance(point, Mapping) or set(point) != _POINT_KEYS:
        raise ResearchBlockError("research point has unexpected keys")
    raw_reasons = point["reasons"]
    reasons = list(raw_reasons) if isinstance(raw_reasons, tuple) else raw_reasons
    normalized = {
        "trade_date": _archived_date(point["trade_date"]),
        "score": _archived_score(point["score"]),
        "phase": point["phase"],
        "components": point["components"],
        "reasons": reasons,
    }
    _validate_point(normalized)
    return normalized


def parse_archived_research_block(block: Any) -> dict[str, Any]:
    """Fail-closed parse of an archive-roundtripped ``sentiment_research`` block.

    Publication stores Decimals/dates as strings and the generic HTTP decimal
    restorer does not know the research ``score`` field, so scores and trade
    dates are normalized here before the closed-shape self-check runs. Read
    routes must never recompute the block: every violation raises
    :class:`ResearchBlockError` for the caller to map to unavailable.
    """
    if not isinstance(block, Mapping):
        raise ResearchBlockError("research block must be an object")
    if set(block) != _BLOCK_KEYS:
        raise ResearchBlockError("research block has unexpected keys")
    if block["method_version"] != METHOD_VERSION:
        raise ResearchBlockError("unknown sentiment research method version")
    if block["research_only"] is not True:
        raise ResearchBlockError("sentiment research block must stay research-only")
    raw_series = block["series"]
    if not isinstance(raw_series, (list, tuple)) or not raw_series:
        raise ResearchBlockError("research series must be a non-empty list")
    series = [_archived_point(point) for point in raw_series]
    trade_dates = [point["trade_date"] for point in series]
    if trade_dates != sorted(trade_dates) or len(set(trade_dates)) != len(series):
        raise ResearchBlockError("research series must be strictly date-ascending")
    return {
        "method_version": METHOD_VERSION,
        "research_only": True,
        "series": series,
    }
