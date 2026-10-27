"""Fail-closed frozen sample validation and method score rebuilding."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import dragon_radar.readmodels.sentiment_research as method
from dragon_radar.research.models import (
    STREAM_BENCHMARK_CLOSES,
    STREAM_COMPONENTS,
    STREAM_NAMES,
    STREAM_TARGET_CLOSES,
    ComponentCounts,
    DailyComponents,
    InputManifest,
    ResearchValidationError,
    SamplePack,
)

_COMPONENT_UNAVAILABLE_REASONS = {
    method.COMPONENT_BREADTH: method.REASON_NO_VALID_DAILY_RETURNS,
    method.COMPONENT_LIMIT_BALANCE: method.REASON_NO_LIMIT_EVENTS,
    method.COMPONENT_PROMOTION: (
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET
    ),
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ValidatedPack:
    pack: SamplePack
    calendar: tuple[date, ...]
    points: tuple[dict[str, object], ...]
    points_by_date: dict[date, dict[str, object]]
    valid_dates: tuple[date, ...]
    target_closes: dict[date, Decimal]
    benchmark_closes: dict[date, Decimal]
    as_of_index: int


def canonical_payload_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_token(value: Decimal) -> str:
    return str(value.normalize())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchValidationError(message)


def _require_text(value: object, label: str) -> None:
    _require(
        type(value) is str and value.strip() != "",
        f"{label} must be a non-empty string",
    )


def _components_payload(
    components: tuple[DailyComponents, ...],
) -> list[list[object]]:
    return [
        [
            item.trade_date.isoformat(),
            item.breadth.numerator,
            item.breadth.denominator,
            item.limit_balance.numerator,
            item.limit_balance.denominator,
            item.promotion.numerator,
            item.promotion.denominator,
        ]
        for item in components
    ]


def _closes_payload(closes: dict[date, Decimal]) -> list[list[str]]:
    return [
        [day.isoformat(), _decimal_token(price)]
        for day, price in sorted(closes.items())
    ]


def _validate_closes(
    mapping: object, calendar_set: set[date], label: str
) -> dict[date, Decimal]:
    _require(isinstance(mapping, Mapping), f"{label} must be a mapping")
    result: dict[date, Decimal] = {}
    for day, price in mapping.items():
        _require(type(day) is date, f"{label} keys must be dates")
        _require(day in calendar_set, f"{label} dates must be calendar dates")
        _require(
            isinstance(price, Decimal) and not isinstance(price, bool),
            f"{label} values must be Decimal",
        )
        _require(
            price.is_finite() and price > 0,
            f"{label} values must be finite and positive",
        )
        result[day] = price
    return result


def _validate_manifests(
    manifests: object,
    as_of: date,
    components: tuple[DailyComponents, ...],
    target: dict[date, Decimal],
    benchmark: dict[date, Decimal],
) -> tuple[InputManifest, ...]:
    _require(
        type(manifests) is tuple and len(manifests) == len(STREAM_NAMES),
        "manifests must contain exactly three entries",
    )
    by_name: dict[str, InputManifest] = {}
    for manifest in manifests:
        _require(type(manifest) is InputManifest, "manifest must be InputManifest")
        _require(
            manifest.name in STREAM_NAMES,
            f"manifest name is unknown: {manifest.name!r}",
        )
        _require(manifest.name not in by_name, "manifest names must be unique")
        _require_text(manifest.source_version, "source_version")
        _require_text(manifest.adjustment, "adjustment")
        _require(type(manifest.sha256) is str, "sha256 must be a string")
        _require(
            _SHA256_RE.fullmatch(manifest.sha256) is not None,
            "sha256 must be 64 lowercase hex characters",
        )
        _require(
            type(manifest.row_count) is int and manifest.row_count >= 0,
            "row_count must be a non-negative int",
        )
        _require(
            type(manifest.as_of) is date and manifest.as_of == as_of,
            "manifest as_of must equal pack as_of",
        )
        _require(type(manifest.frozen_at) is str, "frozen_at must be a string")
        try:
            parsed = datetime.fromisoformat(manifest.frozen_at)
        except (TypeError, ValueError):
            # Fail closed in the module's own error vocabulary.
            raise ResearchValidationError(
                "frozen_at must be an ISO-8601 timestamp"
            ) from None
        _require(
            parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "frozen_at must be timezone aware",
        )
        by_name[manifest.name] = manifest
    _require(
        tuple(manifest.name for manifest in manifests) == STREAM_NAMES,
        "manifests must be ordered as components, target_closes, benchmark_closes",
    )
    payloads = {
        STREAM_COMPONENTS: _components_payload(components),
        STREAM_TARGET_CLOSES: _closes_payload(target),
        STREAM_BENCHMARK_CLOSES: _closes_payload(benchmark),
    }
    for name in STREAM_NAMES:
        manifest = by_name[name]
        _require(
            manifest.row_count == len(payloads[name]),
            f"{name} row_count mismatch",
        )
        _require(
            manifest.sha256 == canonical_payload_hash(payloads[name]),
            f"{name} sha256 mismatch",
        )
    return tuple(by_name[name] for name in STREAM_NAMES)


def _rebuild_point(item: DailyComponents) -> dict[str, object]:
    """Rebuild a method point from integer counts.

    The rebuilt points intentionally carry no ``coverage`` field (the frozen
    pack has no coverage inputs); downstream tasks must not assert it.
    """
    return method.build_research_point(
        item.trade_date,
        {
            method.COMPONENT_BREADTH: method.build_component(
                item.breadth.numerator,
                item.breadth.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_BREADTH],
            ),
            method.COMPONENT_LIMIT_BALANCE: method.build_component(
                item.limit_balance.numerator,
                item.limit_balance.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_LIMIT_BALANCE],
            ),
            method.COMPONENT_PROMOTION: method.build_component(
                item.promotion.numerator,
                item.promotion.denominator,
                _COMPONENT_UNAVAILABLE_REASONS[method.COMPONENT_PROMOTION],
            ),
        },
    )


def validate_pack(pack: SamplePack) -> ValidatedPack:
    _require(type(pack) is SamplePack, "pack must be a SamplePack")
    _require_text(pack.pack_id, "pack_id")
    _require_text(pack.generator, "generator")
    _require_text(pack.target_name, "target_name")
    _require_text(pack.benchmark_name, "benchmark_name")
    _require(type(pack.synthetic) is bool, "synthetic must be a bool")
    _require(
        pack.seed is None
        or (type(pack.seed) is int and not isinstance(pack.seed, bool)),
        "seed must be int or None",
    )

    calendar = pack.calendar
    _require(type(calendar) is tuple and calendar, "calendar must be a non-empty tuple")
    for day in calendar:
        _require(type(day) is date, "calendar dates must be dates")
    _require(
        len(set(calendar)) == len(calendar) and calendar == tuple(sorted(calendar)),
        "calendar must be strictly ascending dates",
    )
    as_of = pack.as_of
    _require(type(as_of) is date, "as_of must be a date")
    _require(as_of in calendar, "as_of must be a calendar date")

    components = pack.components
    _require(type(components) is tuple, "components must be a tuple")
    component_dates: list[date] = []
    for item in components:
        _require(type(item) is DailyComponents, "components must hold DailyComponents")
        _require(type(item.trade_date) is date, "component trade_date must be a date")
        component_dates.append(item.trade_date)
        for counts in (item.breadth, item.limit_balance, item.promotion):
            _require(type(counts) is ComponentCounts, "component must hold counts")
            _require(
                type(counts.numerator) is int and type(counts.denominator) is int,
                "component counts must be ints",
            )
            _require(
                0 <= counts.numerator <= counts.denominator,
                "component counts must satisfy 0 <= numerator <= denominator",
            )
    _require(
        component_dates == sorted(component_dates)
        and len(set(component_dates)) == len(component_dates),
        "component dates must be strictly ascending and unique",
    )
    calendar_set = set(calendar)
    _require(
        all(day in calendar_set for day in component_dates),
        "component dates must be calendar dates",
    )
    _require(
        all(day <= as_of for day in component_dates),
        "components_after_as_of",
    )

    target = _validate_closes(pack.target_closes, calendar_set, "target_closes")
    benchmark = _validate_closes(
        pack.benchmark_closes, calendar_set, "benchmark_closes"
    )
    _validate_manifests(pack.manifests, as_of, components, target, benchmark)

    points = tuple(_rebuild_point(item) for item in components)
    # Shape-only self-check against the real method contract; the method
    # validates a shallow copy and never mutates the points, so discard.
    _ = method.materialize_sentiment_research(list(points))
    points_by_date = {
        point["trade_date"]: point
        for point in points  # type: ignore[index]
    }
    valid_dates = tuple(
        point["trade_date"]  # type: ignore[index]
        for point in points
        if point["score"] is not None  # type: ignore[index]
    )
    return ValidatedPack(
        pack=pack,
        calendar=calendar,
        points=points,
        points_by_date=points_by_date,
        valid_dates=valid_dates,
        target_closes=target,
        benchmark_closes=benchmark,
        as_of_index=calendar.index(as_of),
    )
