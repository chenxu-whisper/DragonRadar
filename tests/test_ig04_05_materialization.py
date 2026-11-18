"""IG05 fixed-yesterday detail archive, using synthetic publication inputs only."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from factories import DAYS, NOW, bar, batch, state

from dragon_radar.domain.models import LimitUpEvent
from dragon_radar.readmodels.market import materialize_market


def event(security_id, day, boards=2, **changes):
    return LimitUpEvent(
        **{
            "security_id": security_id,
            "trade_date": day,
            "available_at": datetime(day.year, day.month, day.day, 16, tzinfo=UTC),
            "source_id": "synthetic",
            "source_version": "s1",
            "is_limit_up": True,
            "touched_limit": True,
            "continuous_boards": boards,
            **changes,
        }
    )


def feedback_batch(*, suspended=(), missing=()):
    ids = ("A", "B", "C", "D")
    return batch(
        expected_security_ids=ids,
        security_states=tuple(
            state(security_id, suspended=security_id in suspended)
            for security_id in ids
        ),
        bars=tuple(
            bar(
                security_id,
                trade_date=day,
                close=(
                    {
                        "A": Decimal("11"),
                        "B": Decimal("9"),
                        "C": Decimal("10"),
                        "D": Decimal("12"),
                    }[security_id]
                    if day == DAYS[-1]
                    else Decimal("10")
                ),
                previous_close=Decimal("10"),
            )
            for day in DAYS
            for security_id in ids
            if not (day == DAYS[-1] and security_id in missing)
        ),
        limit_up_events=(
            event("A", DAYS[-2], 2),
            event("B", DAYS[-2], 3),
            event("C", DAYS[-2], 1),
            event("A", DAYS[-1], 3),
            event("D", DAYS[-1], 1),
        ),
    )


def details(payload):
    sentiment = materialize_market(payload, None)["overview"]["sentiment"]
    assert sentiment.get("continuity_detail_version") == 1, (
        "new publications must archive versioned fixed-pool feedback detail"
    )
    detail = sentiment.get("continuity_detail")
    assert isinstance(detail, dict), "summary-only feedback is not drill-down evidence"
    return sentiment, detail


def test_materialize_market_archives_fixed_yesterday_members_and_direction():
    sentiment, detail = details(feedback_batch())
    assert detail["trade_date"] == DAYS[-1]
    assert detail["previous_trade_date"] == DAYS[-2]
    promotion = detail["promotion"]
    feedback = detail["feedback"]
    assert [item["security_id"] for item in promotion["members"]] == ["A", "B"]
    assert [item["security_id"] for item in feedback["members"]] == ["A", "B", "C"]
    assert promotion["coverage"] == {
        "expected": 2,
        "valid": 2,
        "excluded": 0,
        "missing": 0,
    }
    assert feedback["coverage"] == {
        "expected": 3,
        "valid": 3,
        "excluded": 0,
        "missing": 0,
    }
    assert feedback["direction_counts"] == {"up": 1, "down": 1, "flat": 1}
    assert [
        (item["return"], item["direction"], item["status"], item["name"])
        for item in feedback["members"]
    ] == [
        (Decimal("0.1"), "up", "valid", "Synthetic A"),
        (Decimal("-0.1"), "down", "valid", "Synthetic B"),
        (Decimal("0"), "flat", "valid", "Synthetic C"),
    ]
    assert [item["sealed"] for item in promotion["members"]] == [True, False]
    assert (
        sum(item["sealed"] is True for item in promotion["members"])
        == (sentiment["promote_rate"]["numerator"])
    )
    assert "D" not in {item["security_id"] for item in feedback["members"]}


@pytest.mark.parametrize(
    ("suspended", "missing", "coverage", "statuses"),
    [
        (
            ("B",),
            (),
            {"expected": 3, "valid": 2, "excluded": 1, "missing": 0},
            ["valid", "excluded", "valid"],
        ),
        (
            (),
            ("B",),
            {"expected": 3, "valid": 2, "excluded": 0, "missing": 1},
            ["valid", "missing", "valid"],
        ),
        (
            ("B",),
            ("A",),
            {"expected": 3, "valid": 1, "excluded": 1, "missing": 1},
            ["missing", "excluded", "valid"],
        ),
    ],
)
def test_materialize_market_detail_keeps_excluded_and_missing_fixed_members(
    suspended, missing, coverage, statuses
):
    sentiment, detail = details(feedback_batch(suspended=suspended, missing=missing))
    feedback = detail["feedback"]
    assert feedback["coverage"] == sentiment["yesterday_limit_up_return_coverage"]
    assert feedback["coverage"] == coverage
    assert [item["security_id"] for item in feedback["members"]] == ["A", "B", "C"]
    assert [item["status"] for item in feedback["members"]] == statuses
    assert sum(feedback["direction_counts"].values()) == coverage["valid"]
    for member in feedback["members"]:
        if member["status"] != "valid":
            assert member["return"] is None
            assert member["direction"] is None
            assert member["reasons"]


def test_materialize_market_empty_pools_are_archived_not_fabricated_from_today():
    payload = feedback_batch()
    payload = payload.model_copy(
        update={
            "limit_up_events": (event("D", DAYS[-1], 1),),
        }
    )
    sentiment, detail = details(payload)
    assert detail["promotion"]["members"] == []
    assert detail["feedback"]["members"] == []
    assert detail["feedback"]["direction_counts"] == {"up": 0, "down": 0, "flat": 0}
    assert sentiment["yesterday_limit_up_median_return"] is None


@pytest.mark.parametrize("kind", ["late", "wrong_source", "duplicate"])
def test_materialize_market_detail_rejects_ambiguous_yesterday_events(kind):
    payload = feedback_batch()
    foreign = event("D", DAYS[-2], 2)
    if kind == "late":
        foreign = foreign.model_copy(update={"available_at": NOW.replace(day=10)})
    elif kind == "wrong_source":
        foreign = foreign.model_copy(update={"source_id": "unbound-provider"})
    additions = (foreign, foreign) if kind == "duplicate" else (foreign,)
    _, detail = details(
        payload.model_copy(
            update={
                "limit_up_events": (*payload.limit_up_events, *additions),
            }
        )
    )
    assert [item["security_id"] for item in detail["feedback"]["members"]] == [
        "A",
        "B",
        "C",
    ]
