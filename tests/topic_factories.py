"""Synthetic, offline topic fixtures. No provider data."""

from datetime import UTC, date, datetime, timedelta

from factories import DAYS, NOW, grant


def _weekday_sessions(*, end: date, count: int) -> tuple[date, ...]:
    sessions: list[date] = []
    current = end
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(sessions))


TOPIC_DAYS = _weekday_sessions(end=DAYS[-1], count=61)
TOPIC_AVAILABLE_AT = datetime(2026, 6, 15, tzinfo=UTC)


def topic_definition(topic_id="topic-a", **changes):
    return {
        "topic_id": topic_id,
        "name": "Synthetic Topic A",
        "effective_from": TOPIC_DAYS[0],
        "effective_to": None,
        "available_at": TOPIC_AVAILABLE_AT,
        "source_reference": "synthetic:topic-a",
        **changes,
    }


def topic_membership(topic_id="topic-a", security_id="A", **changes):
    return {
        "topic_id": topic_id,
        "security_id": security_id,
        "effective_from": TOPIC_DAYS[0],
        "effective_to": None,
        "available_at": TOPIC_AVAILABLE_AT,
        "evidence_refs": ("synthetic:membership-a",),
        **changes,
    }


def coverage_cell(topic_id="topic-a", trade_date=None, **changes):
    day = trade_date or TOPIC_DAYS[-1]
    return {
        "topic_id": topic_id,
        "trade_date": day,
        "expected_member_count": 1,
        "received_member_count": 1,
        "complete_as_of": datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
        "status": "complete",
        "reason": None,
        **changes,
    }


def topic_batch_dict(**changes):
    from dragon_radar.domain.models import ExchangeCalendar

    topics = [topic_definition()]
    memberships = [topic_membership()]
    payload = {
        "trade_date": TOPIC_DAYS[-1],
        "as_of": NOW,
        "source_time": NOW,
        "provenance": "contemporaneous",
        "source_id": "synthetic",
        "source_version": "s1",
        "grant": grant(),
        "exchange": "TEST",
        "calendar": ExchangeCalendar(
            exchange="TEST", version="c1", sessions=TOPIC_DAYS
        ),
        "calendar_version": "c1",
        "taxonomy_version": "t1",
        "membership_version": "m1",
        "coverage_from": TOPIC_DAYS[0],
        "coverage_to": TOPIC_DAYS[-1],
        "coverage_cells": [coverage_cell(trade_date=day) for day in TOPIC_DAYS],
        "topics": topics,
        "memberships": memberships,
        "page_manifest": {
            "request_from": TOPIC_DAYS[0],
            "request_to": TOPIC_DAYS[-1],
            "pages": (
                {
                    "page_id": "page-1",
                    "request_cursor": None,
                    "next_cursor": "cursor-2",
                    "page_hash": "a" * 64,
                    "received_row_count": 1,
                },
                {
                    "page_id": "page-2",
                    "request_cursor": "cursor-2",
                    "next_cursor": None,
                    "page_hash": "b" * 64,
                    "received_row_count": 1,
                },
            ),
            "declared_total": len(topics) + len(memberships),
            "terminal_cursor": None,
        },
    }
    payload.update(changes)
    return payload


def topic_batch(**changes):
    from dragon_radar.topics.models import TopicBatch

    return TopicBatch.model_validate(topic_batch_dict(**changes))
