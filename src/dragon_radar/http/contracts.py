"""Workbench-only adapters; never expose grants or archived payloads."""

from datetime import date

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.storage.models import Snapshot


def coverage_view(
    expected: int, valid: int, excluded: int, missing: int
) -> dict[str, int]:
    counts = (expected, valid, excluded, missing)
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("Coverage counts must be nonnegative integers")
    if expected != valid + excluded + missing:
        raise ValueError("Coverage must partition the expected pool")
    return dict(zip(("expected", "valid", "excluded", "missing"), counts, strict=True))


def archived_coverage(value: dict) -> dict[str, int]:
    try:
        return coverage_view(
            value["expected"], value["valid"], value["legal_excluded"], value["missing"]
        )
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None


def empty_envelope(trade_date: date | None = None) -> dict:
    return {
        "trade_date": trade_date,
        "as_of": None,
        "snapshot_id": None,
        "source": None,
        "source_version": None,
        "coverage": None,
        "quality_flags": ["no_data"],
        "rule_version": None,
        "basis": {
            "unit": None,
            "currency": None,
            "price_type": None,
            "benchmark": None,
        },
        "data": None,
    }


def snapshot_envelope(
    snapshot: Snapshot,
    data: dict,
    *,
    unit: str | None = None,
    coverage: dict[str, int] | None = None,
) -> dict:
    batch = snapshot.batch
    currencies = {bar.currency for bar in batch.bars}
    try:
        stored_coverage = archived_coverage(snapshot.quality_report["coverage"])
        rule_version = snapshot.manifest["rule_version"]
        flags = sorted({issue["code"] for issue in snapshot.quality_report["issues"]})
    except (KeyError, TypeError):
        raise DataUnavailable from None
    return {
        "trade_date": batch.trade_date,
        "as_of": batch.as_of,
        "snapshot_id": snapshot.id,
        "source": batch.source_id,
        "source_version": batch.source_version,
        "coverage": coverage if coverage is not None else stored_coverage,
        "quality_flags": flags,
        "rule_version": rule_version,
        "basis": {
            "unit": unit,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "price_type": "raw",
            "benchmark": None,
        },
        "data": data,
    }


def snapshot_summary(snapshot: Snapshot) -> dict:
    calendar_sessions = [
        session.isoformat()
        for session in snapshot.batch.calendar.sessions
        if session <= snapshot.trade_date
    ]
    return snapshot_envelope(
        snapshot,
        {
            "revision": snapshot.revision,
            "supersedes": snapshot.supersedes,
            "published_at": snapshot.published_at,
            "security_count": len(snapshot.batch.expected_security_ids),
            "calendar_sessions": calendar_sessions,
            "earliest_date": calendar_sessions[0] if calendar_sessions else None,
        },
    )
