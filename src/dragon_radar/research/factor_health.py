"""Contemporaneous factor health: coverage, distribution, rank correlation."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from typing import cast

import dragon_radar.readmodels.sentiment_research as method
from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.research import statistics as stats
from dragon_radar.research.models import REASON_INSUFFICIENT_SAMPLE
from dragon_radar.research.samples import ValidatedPack

_NULL_REASON_KEYS = (
    method.REASON_NO_VALID_DAILY_RETURNS,
    method.REASON_NO_LIMIT_EVENTS,
    method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
)
_PAIRS = (
    (method.COMPONENT_BREADTH, method.COMPONENT_LIMIT_BALANCE),
    (method.COMPONENT_BREADTH, method.COMPONENT_PROMOTION),
    (method.COMPONENT_LIMIT_BALANCE, method.COMPONENT_PROMOTION),
)
_DISTRIBUTION_KEYS = ("n", "min", "max", "mean", "median", "p25", "p75")


def _ratio(component: dict[str, object]) -> Decimal:
    # Callers guarantee denominator > 0 (zero-denominator days are counted,
    # never ratioed), so no defensive guard is needed here.
    numerator = cast(int, component["numerator"])
    denominator = cast(int, component["denominator"])
    with localcontext(_decimal_context()):
        return Decimal(numerator) / Decimal(denominator)


def _empty_distribution() -> dict[str, None]:
    return {key: None for key in _DISTRIBUTION_KEYS}


def _score_distribution(scores: list[Decimal]) -> dict[str, object]:
    if scores:
        base = stats.decimal_distribution(scores)
        multiplicity = Counter(scores)
        top_tie_share = Decimal(max(multiplicity.values())) / Decimal(len(scores))
    else:
        base = _empty_distribution()
        # ``n`` is a count: an empty sample carries zero observations even
        # though every moment (min/max/mean/median/quantiles) is unavailable.
        base["n"] = 0
        top_tie_share = None
    base.update(
        {
            "zero_count": sum(1 for score in scores if score == 0),
            "hundred_count": sum(1 for score in scores if score == 100),
            "top_tie_share": top_tie_share,
        }
    )
    return base


def _component_blocks(points: tuple[dict[str, object], ...]) -> dict[str, object]:
    blocks: dict[str, object] = {}
    for key in method.COMPONENT_KEYS:
        ratios: list[Decimal] = []
        zero_denominator = 0
        for point in points:
            component = cast(dict[str, object], cast(dict, point["components"])[key])
            if cast(int, component["denominator"]) == 0:
                zero_denominator += 1
            else:
                ratios.append(_ratio(component))
        blocks[key] = {
            "available_days": len(ratios),
            "zero_denominator_days": zero_denominator,
            "distribution": (
                stats.decimal_distribution(ratios) if ratios else _empty_distribution()
            ),
        }
    return blocks


def _rank_correlations(valid_points: list[dict[str, object]]) -> dict[str, object]:
    series: dict[str, list[Decimal]] = {key: [] for key in method.COMPONENT_KEYS}
    scores: list[Decimal] = []
    for point in valid_points:
        components = cast(dict[str, dict[str, object]], point["components"])
        for key in method.COMPONENT_KEYS:
            series[key].append(_ratio(components[key]))
        scores.append(cast(Decimal, point["score"]))

    def correlation(name: str, left: list[Decimal], right: list[Decimal]):
        result = stats.spearman(left, right)
        return {"value": result.value, "reason": result.reason, "__name": name}

    names = [f"{left}_{right}" for left, right in _PAIRS] + [
        f"{key}_score" for key in method.COMPONENT_KEYS
    ]
    if not valid_points:
        # stats.spearman raises on empty vectors; the health block must
        # degrade honestly instead, using the same insufficient-sample
        # reason the statistics layer reports for a single observation.
        return {
            name: {"value": None, "reason": REASON_INSUFFICIENT_SAMPLE}
            for name in names
        }

    raw = [
        correlation(f"{left}_{right}", series[left], series[right])
        for left, right in _PAIRS
    ]
    raw += [
        correlation(f"{key}_score", series[key], scores)
        for key in method.COMPONENT_KEYS
    ]
    return {cast(str, item.pop("__name")): item for item in raw}


def build_factor_health(validated: ValidatedPack) -> dict[str, object]:
    calendar = validated.calendar
    points = validated.points
    present_dates = [cast(date, point["trade_date"]) for point in points]
    present = set(present_dates)
    valid_points = [
        point for point in points if cast(Decimal | None, point["score"]) is not None
    ]
    scores = [cast(Decimal, point["score"]) for point in valid_points]

    null_reasons = {key: 0 for key in _NULL_REASON_KEYS}
    for point in points:
        if cast(Decimal | None, point["score"]) is None:
            for reason in cast(list[str], point["reasons"]):
                null_reasons[reason] += 1

    holes: list[date] = []
    if present_dates:
        low = calendar.index(min(present_dates))
        high = calendar.index(max(present_dates))
        holes = [
            calendar[index]
            for index in range(low + 1, high)
            if calendar[index] not in present
        ]

    with localcontext(_decimal_context()):
        coverage_ratio = Decimal(len(valid_points)) / Decimal(len(calendar))

    return {
        "calendar_days": len(calendar),
        "present_days": len(points),
        "valid_days": len(valid_points),
        "absent_days": len(calendar) - len(points),
        "null_days": len(points) - len(valid_points),
        "coverage_ratio": coverage_ratio,
        "null_reasons": null_reasons,
        "calendar_holes": holes,
        "score_distribution": _score_distribution(scores),
        "components": _component_blocks(points),
        "rank_correlations": _rank_correlations(cast(list, valid_points)),
        "sample_window": {
            "first": valid_points[0]["trade_date"] if valid_points else None,
            "last": valid_points[-1]["trade_date"] if valid_points else None,
        },
    }
