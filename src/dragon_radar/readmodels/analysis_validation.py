"""Pure validation and coverage classification for analysis projections."""

from collections.abc import Collection
from datetime import date
from decimal import Decimal

LEGAL_EXCLUSION_REASONS = frozenset({"suspension"})


def _identifier_set(values: Collection[str]) -> frozenset[str]:
    identifiers = tuple(values)
    if any(not isinstance(item, str) or not item for item in identifiers) or len(
        set(identifiers)
    ) != len(identifiers):
        raise ValueError("analysis identifiers must be unique nonempty strings")
    return frozenset(identifiers)


def _string_list(value, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(isinstance(item, str) and item for item in value)
        and len(set(value)) == len(value)
    )


def _decimal(value) -> bool:
    return value is None or (isinstance(value, Decimal) and value.is_finite())


def _validate_exclusions(
    exclusions,
    expected_ids: frozenset[str],
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    if not isinstance(exclusions, list):
        raise ValueError("analysis exclusions must be a list")
    reasons_by_id = {}
    for row in exclusions:
        if (
            not isinstance(row, dict)
            or set(row) != {"security_id", "reasons"}
            or not isinstance(row["security_id"], str)
            or row["security_id"] not in expected_ids
            or not _string_list(row["reasons"], nonempty=True)
            or row["security_id"] in reasons_by_id
        ):
            raise ValueError("invalid analysis exclusion")
        reasons_by_id[row["security_id"]] = frozenset(row["reasons"])
    return frozenset(reasons_by_id), reasons_by_id


def validate_comparison_projection(
    *,
    curves,
    exclusions,
    expected_ids: Collection[str],
    sessions: tuple[date, ...],
) -> tuple[frozenset[str], frozenset[str]]:
    """Validate one complete comparison projection and return its identity partition."""
    expected = _identifier_set(expected_ids)
    expected_dates = [day.isoformat() for day in sessions]
    if not sessions or not isinstance(curves, list):
        raise ValueError("invalid comparison projection")

    curve_ids = []
    for row in curves:
        if (
            not isinstance(row, dict)
            or set(row) != {"security_id", "points", "reasons"}
            or not isinstance(row["security_id"], str)
            or row["security_id"] not in expected
            or not isinstance(row["points"], list)
            or len(row["points"]) != len(sessions)
            or not _string_list(row["reasons"])
        ):
            raise ValueError("invalid comparison curve")
        point_dates = []
        has_value = False
        for point in row["points"]:
            if (
                not isinstance(point, dict)
                or set(point) != {"trade_date", "normalized", "drawdown"}
                or not isinstance(point["trade_date"], str)
                or not _decimal(point["normalized"])
                or not _decimal(point["drawdown"])
                or (point["normalized"] is None) != (point["drawdown"] is None)
            ):
                raise ValueError("invalid comparison point")
            point_dates.append(point["trade_date"])
            has_value = has_value or point["normalized"] is not None
        if point_dates != expected_dates or not has_value:
            raise ValueError("invalid comparison series")
        curve_ids.append(row["security_id"])
    curves_partition = _identifier_set(curve_ids)
    exclusions_partition, _reasons = _validate_exclusions(exclusions, expected)
    if (
        curves_partition & exclusions_partition
        or curves_partition | exclusions_partition != expected
    ):
        raise ValueError("comparison projection must exactly partition requested IDs")
    return curves_partition, exclusions_partition


def classify_analysis_coverage(
    *,
    expected_ids: Collection[str],
    valid_ids: Collection[str],
    exclusions,
) -> tuple[int, int, int, int]:
    """Classify valid, legally excluded, and missing analysis identities."""
    expected = _identifier_set(expected_ids)
    valid = _identifier_set(valid_ids)
    if not valid <= expected:
        raise ValueError("valid analysis IDs must belong to the expected universe")
    exclusion_ids, reasons_by_id = _validate_exclusions(exclusions, expected)
    if valid & exclusion_ids:
        raise ValueError("valid and excluded analysis IDs must be disjoint")
    legally_excluded = frozenset(
        security_id
        for security_id, reasons in reasons_by_id.items()
        if reasons <= LEGAL_EXCLUSION_REASONS
    )
    return (
        len(expected),
        len(valid),
        len(legally_excluded),
        len(expected - valid - legally_excluded),
    )


__all__ = ["classify_analysis_coverage", "validate_comparison_projection"]
