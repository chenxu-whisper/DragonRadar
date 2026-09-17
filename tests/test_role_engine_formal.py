from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from factories import bar, batch, grant, state
from topic_factories import (
    coverage_cell,
    topic_batch,
    topic_definition,
    topic_membership,
)

from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.roles.models import RoleCandidate, RoleContext

T = date(2026, 9, 8)
CUTOFF = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _sessions(count: int = 130) -> tuple[date, ...]:
    result = []
    current = T
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(result))


SESSIONS = _sessions()


def _prices(start: str, end: str, count: int = 61) -> tuple[Decimal, ...]:
    first = Decimal(start)
    last = Decimal(end)
    step = (last - first) / Decimal(count - 1)
    return tuple(first + step * index for index in range(count))


def _window_prices(start: str, start20: str, end: str) -> tuple[Decimal, ...]:
    return (
        (Decimal(start),) * 40
        + (Decimal(start20),)
        + (Decimal(start20),) * 19
        + (Decimal(end),)
    )


def _context(
    *,
    member_ids: tuple[str, ...] = ("A", "B", "C"),
    pool_ids: tuple[str, ...] | None = None,
    prices: dict[str, tuple[Decimal, ...]] | None = None,
    amounts: dict[str, tuple[Decimal, ...]] | None = None,
    states=None,
    memberships=None,
    coverage_cells=None,
    bars_extra=(),
    currencies: dict[str, str] | None = None,
    market_exchange: str = "TEST",
    topic_exchange: str = "TEST",
    market_grant=None,
    topic_grant=None,
    rule_version: str = "role-test-v1",
) -> RoleContext:
    pool_ids = pool_ids or member_ids
    prices = prices or {
        security_id: _prices("100", str(120 + index * 10))
        for index, security_id in enumerate(pool_ids)
    }
    amounts = amounts or {
        security_id: tuple(Decimal(100 + index * 100) for _ in range(20))
        for index, security_id in enumerate(pool_ids)
    }
    currencies = currencies or {}
    states = states or tuple(
        state(
            security_id=security_id,
            exchange=market_exchange,
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0],
        )
        for security_id in pool_ids
    )
    market_bars = []
    for security_id in pool_ids:
        security_prices = prices.get(security_id, ())
        price_days = SESSIONS[-len(security_prices) :]
        security_amounts = amounts.get(security_id, ())
        amount_by_day = dict(
            zip(SESSIONS[-len(security_amounts) :], security_amounts, strict=True)
        )
        for day, close in zip(price_days, security_prices, strict=True):
            market_bars.append(
                bar(
                    security_id=security_id,
                    trade_date=day,
                    close=close,
                    amount=amount_by_day.get(day, Decimal("100")),
                    currency=currencies.get(security_id, "CNY"),
                    available_at=datetime(
                        day.year,
                        day.month,
                        day.day,
                        16,
                        tzinfo=UTC,
                    ),
                )
            )
    market_bars.extend(bars_extra)

    memberships = memberships or tuple(
        topic_membership(
            security_id=security_id,
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for security_id in member_ids
    )
    if coverage_cells is None:
        cells = []
        for day in SESSIONS:
            complete_as_of = datetime(
                day.year,
                day.month,
                day.day,
                17,
                tzinfo=UTC,
            )
            actual = len(
                {
                    membership["security_id"]
                    for membership in memberships
                    if membership["effective_from"] <= day
                    and (
                        membership.get("effective_to") is None
                        or day < membership["effective_to"]
                    )
                    and membership["available_at"] <= complete_as_of
                }
            )
            cells.append(
                coverage_cell(
                    trade_date=day,
                    expected_member_count=actual,
                    received_member_count=actual,
                    complete_as_of=complete_as_of,
                )
            )
        coverage_cells = tuple(cells)
    calendar = ExchangeCalendar(
        exchange=market_exchange,
        version="formal-c1",
        sessions=SESSIONS,
    )
    market = batch(
        trade_date=T,
        as_of=CUTOFF,
        grant=market_grant or batch().grant,
        calendar=calendar,
        calendar_version="formal-c1",
        expected_security_ids=pool_ids,
        security_states=tuple(states),
        bars=tuple(market_bars),
    )
    topic = topic_batch(
        trade_date=T,
        as_of=CUTOFF,
        grant=topic_grant or topic_batch().grant,
        exchange=topic_exchange,
        calendar=ExchangeCalendar(
            exchange=topic_exchange,
            version="formal-c1",
            sessions=SESSIONS,
        ),
        calendar_version="formal-c1",
        coverage_from=SESSIONS[0],
        coverage_to=T,
        coverage_cells=tuple(coverage_cells),
        topics=(
            topic_definition(
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        memberships=tuple(memberships),
    )
    return RoleContext(market=market, topic=topic, rule_version=rule_version)


def _engine():
    from dragon_radar.roles.engine import dense_rank, evaluate_core, evaluate_trend

    return dense_rank, evaluate_core, evaluate_trend


def test_dense_rank_uses_complete_decimal_key_and_stable_id_only_inside_ties():
    dense_rank, _, _ = _engine()
    rows = (
        RoleCandidate(
            topic_id="topic-a",
            security_id="B",
            role="trend",
            status="candidate",
            factors=(Decimal("0.1000000000000000000000000000000002"), Decimal("1")),
        ),
        RoleCandidate(
            topic_id="topic-a",
            security_id="C",
            role="trend",
            status="candidate",
            factors=(Decimal("0.1000000000000000000000000000000001"), Decimal("9")),
        ),
        RoleCandidate(
            topic_id="topic-a",
            security_id="A",
            role="trend",
            status="candidate",
            factors=(Decimal("0.1000000000000000000000000000000002"), Decimal("1")),
        ),
    )

    ranked = dense_rank(rows, key=lambda row: (-row.factors[0], -row.factors[1]))

    assert [(row.security_id, row.tie_rank) for row in ranked] == [
        ("A", 1),
        ("B", 1),
        ("C", 2),
    ]


def test_trend_uses_market_medians_positive_relative_returns_and_mdd_gate():
    _, _, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B", "C"),
        pool_ids=("A", "B", "C", "M1", "M2", "M3"),
        prices={
            "A": _window_prices("100", "120", "150"),
            "B": _window_prices("50", "56", "70"),
            "C": (
                (Decimal("100"),)
                + (Decimal("145"),) * 30
                + (Decimal("80"),) * 9
                + (Decimal("90"),)
                + (Decimal("90"),) * 19
                + (Decimal("130"),)
            ),
            "M1": _window_prices("100", "100", "105"),
            "M2": _window_prices("100", "100", "110"),
            "M3": _window_prices("100", "100", "115"),
        },
    )

    result = evaluate_trend(context, "topic-a")

    assert result.topic_status.status == "ready"
    assert result.topic_status.expected == 3
    assert result.topic_status.valid == 3
    assert [
        (row.security_id, row.status, row.tie_rank) for row in result.candidates
    ] == [
        ("A", "candidate", 1),
        ("B", "eligible_non_candidate", 2),
        ("C", "eligible_non_candidate", 3),
    ]
    assert result.candidates[0].factors == (
        Decimal("0.275"),
        Decimal("0.05"),
        Decimal("0"),
    )
    assert result.candidates[1].reasons == (
        "candidate_conditions_met",
        "not_first_tie_group",
    )
    assert result.candidates[2].factors[2] > Decimal("0")


def test_trend_is_no_candidate_when_only_later_ranks_meet_candidate_conditions():
    _, _, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B", "C"),
        pool_ids=("A", "B", "C", "M1", "M2", "M3"),
        prices={
            "A": (
                (Decimal("100"),)
                + (Decimal("200"),) * 59
                + (Decimal("150"),)
            ),
            "B": _window_prices("100", "120", "140"),
            "C": _window_prices("100", "110", "130"),
            "M1": _window_prices("100", "100", "105"),
            "M2": _window_prices("100", "100", "110"),
            "M3": _window_prices("100", "100", "115"),
        },
    )

    result = evaluate_trend(context, "topic-a")

    assert result.topic_status.status == "no_candidate"
    assert [
        (row.security_id, row.status, row.tie_rank) for row in result.candidates
    ] == [
        ("A", "eligible_non_candidate", 1),
        ("B", "eligible_non_candidate", 2),
        ("C", "eligible_non_candidate", 3),
    ]
    assert result.candidates[1].reasons == (
        "candidate_conditions_met",
        "not_first_tie_group",
    )


def test_trend_requires_exactly_61_sessions_and_reports_missing_member():
    _, _, evaluate_trend = _engine()
    context = _context(
        prices={
            "A": _prices("100", "130"),
            "B": _prices("100", "120", 60),
            "C": _prices("100", "110"),
        }
    )

    result = evaluate_trend(context, "topic-a")

    assert result.topic_status.status == "ready"
    assert (
        result.topic_status.expected,
        result.topic_status.valid,
        result.topic_status.legal_excluded,
        result.topic_status.missing,
    ) == (3, 2, 0, 1)
    assert any(
        "B" in reason and "61" in reason for reason in result.topic_status.reasons
    )


@pytest.mark.parametrize(
    "coverage_changes",
    (
        {
            "status": "unavailable",
            "received_member_count": 2,
            "reason": "provider outage",
        },
        {"complete_as_of": CUTOFF + timedelta(seconds=1)},
    ),
)
def test_trend_requires_complete_coverage_by_cutoff_for_every_window_day(
    coverage_changes,
):
    _, _, evaluate_trend = _engine()
    unavailable_day = SESSIONS[-10]
    coverage_cells = tuple(
        coverage_cell(
            trade_date=day,
            **{
                "expected_member_count": 3,
                "received_member_count": 3,
                "complete_as_of": datetime(
                    day.year,
                    day.month,
                    day.day,
                    17,
                    tzinfo=UTC,
                ),
                **(coverage_changes if day == unavailable_day else {}),
            },
        )
        for day in SESSIONS
    )

    result = evaluate_trend(
        _context(coverage_cells=coverage_cells),
        "topic-a",
    )

    status = result.topic_status
    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (3, 0, 0, 3)
    assert status.reasons == (
        f"{unavailable_day.isoformat()}: topic coverage incomplete",
    )
    assert result.candidates == ()


@pytest.mark.parametrize(
    "forgery",
    ("unknown_definition", "unknown_membership", "duplicate_membership"),
)
def test_formal_roles_recompute_coverage_members_at_cell_completion(forgery):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(member_ids=("A", "B"))
    topic_changes = {}
    forged_count = 2
    if forgery == "unknown_definition":
        topic_changes["topics"] = (
            context.topic.topics[0].model_copy(
                update={"available_at": CUTOFF},
            ),
        )
    elif forgery == "unknown_membership":
        topic_changes["memberships"] = (
            context.topic.memberships[0],
            context.topic.memberships[1].model_copy(
                update={"available_at": CUTOFF},
            ),
        )
    else:
        forged_count = 3
        topic_changes["memberships"] = (
            *context.topic.memberships,
            context.topic.memberships[0],
        )
    topic_changes["coverage_cells"] = tuple(
        cell.model_copy(
            update={
                "expected_member_count": forged_count,
                "received_member_count": forged_count,
            }
        )
        for cell in context.topic.coverage_cells
    )
    forged_topic = context.topic.model_copy(update=topic_changes)
    if forgery == "duplicate_membership":
        forged = RoleContext.model_construct(
            market=context.market,
            topic=forged_topic,
            rule_version=context.rule_version,
        )
    else:
        forged = RoleContext(
            market=context.market,
            topic=forged_topic,
            rule_version=context.rule_version,
        )

    for result in (
        evaluate_trend(forged, "topic-a"),
        evaluate_core(forged, "topic-a"),
    ):
        assert result.topic_status.status == "insufficient"
        assert any(
            "coverage incomplete" in reason
            for reason in result.topic_status.reasons
        )
        assert result.candidates == ()


@pytest.mark.parametrize("duplicate_after_cutoff", (False, True))
def test_formal_roles_keep_duplicate_coverage_cell_incomplete(
    duplicate_after_cutoff,
):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context()
    duplicate_cell = context.topic.coverage_cells[-1]
    if duplicate_after_cutoff:
        duplicate_cell = duplicate_cell.model_copy(
            update={"complete_as_of": CUTOFF + timedelta(seconds=1)}
        )
    duplicate = RoleContext(
        market=context.market,
        topic=context.topic.model_copy(
            update={
                "coverage_cells": (
                    *context.topic.coverage_cells,
                    duplicate_cell,
                )
            }
        ),
        rule_version=context.rule_version,
    )

    for result in (
        evaluate_trend(duplicate, "topic-a"),
        evaluate_core(duplicate, "topic-a"),
    ):
        assert result.topic_status.status == "insufficient"
        assert result.topic_status.reasons == (
            f"{T.isoformat()}: topic coverage incomplete",
        )
        assert result.candidates == ()


def test_trend_refilters_cutoff_and_counts_missing_state():
    _, _, evaluate_trend = _engine()
    late_state = state(
        security_id="B",
        exchange="TEST",
        effective_from=SESSIONS[0],
        available_at=CUTOFF + timedelta(seconds=1),
        listing_date=SESSIONS[0],
    )
    context = _context(states=(_context().market.security_states[0], late_state))

    result = evaluate_trend(context, "topic-a")

    assert result.topic_status.status == "no_candidate"
    assert result.topic_status.expected == 3
    assert result.topic_status.missing == 2
    assert any(
        "B" in reason and "state" in reason for reason in result.topic_status.reasons
    )


def test_formal_roles_ignore_bar_after_knowledge_cutoff():
    _, evaluate_core, evaluate_trend = _engine()
    baseline = _context()
    late_bar = bar(
        security_id="A",
        trade_date=T,
        close=Decimal("999"),
        amount=Decimal("999"),
        available_at=CUTOFF + timedelta(seconds=1),
    )
    with_late_bar = _context(bars_extra=(late_bar,))

    assert evaluate_trend(with_late_bar, "topic-a") == evaluate_trend(
        baseline,
        "topic-a",
    )
    assert evaluate_core(with_late_bar, "topic-a") == evaluate_core(
        baseline,
        "topic-a",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_id", "other-source"),
        ("source_version", "other-version"),
        ("pool_version", "other-pool"),
    ),
)
def test_formal_roles_ignore_security_states_not_bound_to_market_batch(field, value):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A",),
        states=(
            state(
                security_id="A",
                exchange="TEST",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
                **{field: value},
            ),
        ),
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == (1, 0, 0, 1)
        assert "A: missing effective security state" in status.reasons
        assert result.candidates == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_id", "other-source"),
        ("source_version", "other-version"),
        ("adjustment_version", "other-adjustment"),
    ),
)
def test_formal_roles_ignore_daily_bars_not_bound_to_market_batch(field, value):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context()
    mismatched = RoleContext(
        market=context.market.model_copy(
            update={
                "bars": tuple(
                    daily_bar.model_copy(update={field: value})
                    if daily_bar.security_id == "A"
                    else daily_bar
                    for daily_bar in context.market.bars
                )
            }
        ),
        topic=context.topic,
        rule_version=context.rule_version,
    )

    for result in (
        evaluate_trend(mismatched, "topic-a"),
        evaluate_core(mismatched, "topic-a"),
    ):
        status = result.topic_status
        expected_counts = (
            (3, 2, 0, 1) if status.role == "trend" else (3, 0, 0, 3)
        )
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == expected_counts
        assert any("A" in reason for reason in status.reasons)
        assert {row.security_id for row in result.candidates} <= {"B", "C"}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_id", "other-source"),
        ("source_version", "other-version"),
        ("adjustment_version", "other-adjustment"),
    ),
)
def test_wrong_version_bar_does_not_duplicate_the_unique_bound_bar(field, value):
    _, evaluate_core, evaluate_trend = _engine()
    baseline = _context()
    wrong_version_duplicate = bar(
        security_id="A",
        trade_date=T,
        close=Decimal("999"),
        amount=Decimal("999"),
        **{field: value},
    )
    context = _context(bars_extra=(wrong_version_duplicate,))

    assert evaluate_trend(context, "topic-a") == evaluate_trend(
        baseline,
        "topic-a",
    )
    assert evaluate_core(context, "topic-a") == evaluate_core(
        baseline,
        "topic-a",
    )


def test_duplicate_bound_bars_remain_unavailable():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        bars_extra=(
            bar(
                security_id="A",
                trade_date=T,
                close=Decimal("999"),
                amount=Decimal("999"),
            ),
        )
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        expected_counts = (
            (3, 2, 0, 1) if status.role == "trend" else (3, 0, 0, 3)
        )
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == expected_counts
        assert any("A" in reason for reason in status.reasons)
        assert {row.security_id for row in result.candidates} <= {"B", "C"}


@pytest.mark.parametrize(
    ("batch_name", "expected_reason"),
    (
        ("market", "market_calendar_version_mismatch"),
        ("topic", "topic_calendar_version_mismatch"),
    ),
)
def test_formal_roles_reject_calendar_version_mismatch(
    batch_name,
    expected_reason,
):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context()
    changed_batch = getattr(context, batch_name).model_copy(
        update={"calendar_version": "other-calendar"}
    )
    changed = RoleContext(
        market=changed_batch if batch_name == "market" else context.market,
        topic=changed_batch if batch_name == "topic" else context.topic,
        rule_version=context.rule_version,
    )

    for result in (
        evaluate_trend(changed, "topic-a"),
        evaluate_core(changed, "topic-a"),
    ):
        status = result.topic_status
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == (3, 0, 0, 3)
        assert status.reasons == (expected_reason,)
        assert result.candidates == ()


@pytest.mark.parametrize("fact", ("definition", "membership"))
def test_formal_roles_ignore_topic_facts_after_knowledge_cutoff(fact):
    _, evaluate_core, evaluate_trend = _engine()
    baseline = _context()
    topic_changes = {}
    if fact == "definition":
        topic_changes["topics"] = (
            *baseline.topic.topics,
            baseline.topic.topics[0].model_copy(
                update={
                    "name": "Future duplicate",
                    "available_at": CUTOFF + timedelta(seconds=1),
                }
            ),
        )
    else:
        topic_changes["memberships"] = (
            *baseline.topic.memberships,
            baseline.topic.memberships[0].model_copy(
                update={
                    "security_id": "FUTURE",
                    "available_at": CUTOFF + timedelta(seconds=1),
                }
            ),
        )
    with_late_fact = RoleContext(
        market=baseline.market,
        topic=baseline.topic.model_copy(update=topic_changes),
        rule_version=baseline.rule_version,
    )

    assert evaluate_trend(with_late_fact, "topic-a") == evaluate_trend(
        baseline,
        "topic-a",
    )
    assert evaluate_core(with_late_fact, "topic-a") == evaluate_core(
        baseline,
        "topic-a",
    )


def test_trend_market_benchmark_excludes_security_below_listing_minimum():
    _, _, evaluate_trend = _engine()
    member_ids = ("A", "B", "C", "D", "E")
    pool_ids = (*member_ids, "NEW")
    ends = ("150", "140", "130", "120", "110")
    states = tuple(
        state(
            security_id=security_id,
            board_category="sh_main",
            exchange="TEST",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0],
        )
        for security_id in member_ids
    ) + (
        state(
            security_id="NEW",
            board_category="sh_main",
            exchange="TEST",
            effective_from=SESSIONS[-61],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[-61],
        ),
    )
    context = _context(
        member_ids=member_ids,
        pool_ids=pool_ids,
        states=states,
        prices={
            **{
                security_id: _window_prices("100", "100", end)
                for security_id, end in zip(member_ids, ends, strict=True)
            },
            "NEW": _window_prices("100", "100", "1000"),
        },
        rule_version="role-v1",
    )

    result = evaluate_trend(context, "topic-a")

    assert result.topic_status.status == "ready"
    assert result.candidates[0].security_id == "A"
    assert result.candidates[0].factors[0] == Decimal("0.2")


def test_formal_roles_count_future_listing_on_observation_day_as_missing():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A",),
        states=(
            state(
                security_id="A",
                exchange="TEST",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=T + timedelta(days=1),
            ),
        ),
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        assert (
            result.topic_status.valid,
            result.topic_status.legal_excluded,
            result.topic_status.missing,
        ) == (0, 0, 1)
        assert "A: listing date is after observation day" in result.topic_status.reasons


def test_trend_rejects_forged_history_before_listing_date():
    _, _, evaluate_trend = _engine()
    listing_date = SESSIONS[-30]
    states = tuple(
        state(
            security_id=security_id,
            exchange="TEST",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=listing_date if security_id == "B" else SESSIONS[0],
        )
        for security_id in ("A", "B", "C")
    )

    result = evaluate_trend(_context(states=states), "topic-a")

    assert (
        result.topic_status.expected,
        result.topic_status.valid,
        result.topic_status.legal_excluded,
        result.topic_status.missing,
    ) == (3, 2, 0, 1)
    assert "B: listing date is after observation day" in result.topic_status.reasons
    assert {row.security_id for row in result.candidates} == {"A", "C"}


def test_formal_roles_count_incomplete_listing_calendar_as_missing():
    _, evaluate_core, evaluate_trend = _engine()
    member_ids = ("A", "B", "C", "D", "E")
    states = tuple(
        state(
            security_id=security_id,
            board_category="sh_main",
            exchange="TEST",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0] - timedelta(days=1),
        )
        for security_id in member_ids
    )
    context = _context(
        member_ids=member_ids,
        states=states,
        rule_version="role-v1",
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == (5, 0, 0, 5)
        assert status.reasons[: len(member_ids)] == tuple(
            f"{security_id}: incomplete listing calendar"
            for security_id in member_ids
        )


def test_formal_roles_count_member_outside_market_pool_as_missing():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(member_ids=("OUT",), pool_ids=("MARKET",))

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == (1, 0, 0, 1)
        assert "OUT: outside declared market pool" in status.reasons


@pytest.mark.parametrize(
    ("field", "value", "classification"),
    (
        ("security_type", "fund", "legal_excluded"),
        ("security_type", None, "missing"),
        ("board_category", "sh_main", "legal_excluded"),
        ("board_category", None, "missing"),
        ("is_st", True, "legal_excluded"),
        ("is_st", None, "missing"),
        ("is_delisting", True, "legal_excluded"),
        ("is_delisting", None, "missing"),
    ),
)
def test_formal_role_state_fields_distinguish_legal_exclusion_from_missing(
    field,
    value,
    classification,
):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A",),
        states=(
            state(
                security_id="A",
                exchange="TEST",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
                **{field: value},
            ),
        ),
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        expected_counts = (
            (1, 0, 1, 0)
            if classification == "legal_excluded"
            else (1, 0, 0, 1)
        )
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == expected_counts
        assert any(
            (
                "ineligible security state"
                if classification == "legal_excluded"
                else "missing required security state"
            )
            in reason
            for reason in status.reasons
        )
        assert result.candidates == ()


def test_formal_roles_reject_cross_exchange_member_as_insufficient():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A",),
        states=(
            state(
                security_id="A",
                exchange="OTHER",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
        ),
    )

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        status = result.topic_status
        assert status.status == "insufficient"
        assert (
            status.expected,
            status.valid,
            status.legal_excluded,
            status.missing,
        ) == (1, 0, 0, 1)
        assert status.reasons == ("cross_exchange", "A: cross_exchange")


def test_trend_keeps_cross_exchange_priority_over_incomplete_coverage():
    _, _, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B"),
        states=(
            state(
                security_id="A",
                exchange="OTHER",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
            state(
                security_id="B",
                exchange="TEST",
                security_type="fund",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
        ),
    )
    broken_day = SESSIONS[-10]
    broken = RoleContext(
        market=context.market,
        topic=context.topic.model_copy(
            update={
                "coverage_cells": tuple(
                    cell.model_copy(
                        update={
                            "status": "unavailable",
                            "reason": "provider outage",
                        }
                    )
                    if cell.trade_date == broken_day
                    else cell
                    for cell in context.topic.coverage_cells
                )
            }
        ),
        rule_version=context.rule_version,
    )

    status = evaluate_trend(broken, "topic-a").topic_status

    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (2, 0, 1, 1)
    assert status.reasons == (
        "cross_exchange",
        "A: cross_exchange",
        "B: ineligible security state",
    )


def test_trend_preserves_legal_exclusion_when_coverage_is_incomplete():
    _, _, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B"),
        states=(
            state(
                security_id="A",
                exchange="TEST",
                security_type="fund",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
            state(
                security_id="B",
                exchange="TEST",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
        ),
    )
    broken_day = SESSIONS[-10]
    broken = RoleContext(
        market=context.market,
        topic=context.topic.model_copy(
            update={
                "coverage_cells": tuple(
                    cell.model_copy(
                        update={
                            "status": "unavailable",
                            "reason": "provider outage",
                        }
                    )
                    if cell.trade_date == broken_day
                    else cell
                    for cell in context.topic.coverage_cells
                )
            }
        ),
        rule_version=context.rule_version,
    )

    status = evaluate_trend(broken, "topic-a").topic_status

    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (2, 0, 1, 1)
    assert status.reasons == (
        "A: ineligible security state",
        f"{broken_day.isoformat()}: topic coverage incomplete",
    )


@pytest.mark.parametrize(
    ("role", "session_count", "calendar_reason"),
    (
        ("trend", 60, "market_calendar_missing_61_sessions"),
        ("core", 19, "market_calendar_missing_20_sessions"),
    ),
)
def test_short_window_preserves_current_legal_exclusion(
    role,
    session_count,
    calendar_reason,
):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B"),
        states=(
            state(
                security_id="A",
                exchange="TEST",
                security_type="fund",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[-session_count],
            ),
            state(
                security_id="B",
                exchange="TEST",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[-session_count],
            ),
        ),
    )
    short = RoleContext(
        market=context.market.model_copy(
            update={
                "calendar": context.market.calendar.model_copy(
                    update={"sessions": SESSIONS[-session_count:]}
                )
            }
        ),
        topic=context.topic,
        rule_version=context.rule_version,
    )
    evaluator = evaluate_trend if role == "trend" else evaluate_core

    status = evaluator(short, "topic-a").topic_status

    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (2, 0, 1, 1)
    assert status.reasons == (
        "A: ineligible security state",
        calendar_reason,
    )


@pytest.mark.parametrize(
    ("role", "session_count"),
    (
        ("trend", 60),
        ("core", 19),
    ),
)
def test_short_window_keeps_cross_exchange_reason_priority(role, session_count):
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(
        member_ids=("A", "B"),
        states=(
            state(
                security_id="A",
                exchange="OTHER",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
            state(
                security_id="B",
                exchange="TEST",
                security_type="fund",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
        ),
    )
    short = RoleContext(
        market=context.market.model_copy(
            update={
                "calendar": context.market.calendar.model_copy(
                    update={"sessions": SESSIONS[-session_count:]}
                )
            }
        ),
        topic=context.topic,
        rule_version=context.rule_version,
    )
    evaluator = evaluate_trend if role == "trend" else evaluate_core

    status = evaluator(short, "topic-a").topic_status

    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (2, 0, 1, 1)
    assert status.reasons == (
        "cross_exchange",
        "A: cross_exchange",
        "B: ineligible security state",
    )


def test_formal_roles_reject_cross_exchange_topic_as_insufficient():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context(topic_exchange="OTHER")

    for result in (
        evaluate_trend(context, "topic-a"),
        evaluate_core(context, "topic-a"),
    ):
        assert result.topic_status.status == "insufficient"
        assert result.candidates == ()
        assert "cross_exchange" in result.topic_status.reasons


@pytest.mark.parametrize("source", ("market", "topic"))
@pytest.mark.parametrize(
    "grant_changes",
    (
        {"approved": False},
        {"source_id": "other"},
        {"reference": None},
        {"starts_at": CUTOFF + timedelta(seconds=1)},
        {"expires_at": CUTOFF},
        {"local_storage": False},
        {"local_display": False},
    ),
)
def test_formal_roles_require_each_embedded_grant_at_knowledge_cutoff(
    source,
    grant_changes,
):
    _, _, evaluate_trend = _engine()
    context_kwargs = {f"{source}_grant": grant(**grant_changes)}

    result = evaluate_trend(_context(**context_kwargs), "topic-a")

    status = result.topic_status
    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (3, 0, 0, 3)
    assert status.reasons == (f"{source}_source_not_materializable",)
    assert result.candidates == ()


def test_every_call_returns_status_when_topic_is_missing():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context()

    for result in (
        evaluate_trend(context, "absent"),
        evaluate_core(context, "absent"),
    ):
        assert result.topic_status.status == "insufficient"
        assert result.topic_status.expected == 0
        assert result.candidates == ()
        assert "topic_unavailable" in result.topic_status.reasons


def test_core_uses_t_minus_4_and_t_minus_19_inclusive_and_mad_sort_order():
    _, evaluate_core, _ = _engine()
    context = _context(
        amounts={
            "A": (Decimal("100"),) * 20,
            "B": (Decimal("100"),) * 15 + (Decimal("300"),) * 5,
            "C": (Decimal("150"), Decimal("50")) * 5 + (Decimal("100"),) * 10,
        }
    )

    result = evaluate_core(context, "topic-a")

    assert result.topic_status.status == "ready"
    assert [
        (row.security_id, row.status, row.tie_rank) for row in result.candidates
    ] == [
        ("B", "candidate", 1),
        ("A", "eligible_non_candidate", 2),
        ("C", "eligible_non_candidate", 3),
    ]
    assert result.candidates[0].factors == (
        Decimal("0.6"),
        Decimal("150"),
        Decimal("0.5"),
    )
    assert result.candidates[2].factors[1:] == (
        Decimal("100"),
        Decimal("0.25"),
    )
    assert result.candidates[1].factors[1:] == (
        Decimal("100"),
        Decimal("0"),
    )


def test_core_marks_every_member_of_the_first_tie_group_as_candidate():
    _, evaluate_core, _ = _engine()
    amounts = {
        security_id: (Decimal("100"),) * 20
        for security_id in ("A", "B", "C")
    }

    result = evaluate_core(_context(amounts=amounts), "topic-a")

    assert result.topic_status.status == "ready"
    assert [
        (row.security_id, row.status, row.tie_rank) for row in result.candidates
    ] == [
        ("A", "candidate", 1),
        ("B", "candidate", 1),
        ("C", "candidate", 1),
    ]


def test_core_allows_zero_member_amount_when_daily_denominator_is_positive():
    _, evaluate_core, _ = _engine()
    result = evaluate_core(
        _context(
            amounts={
                "A": (Decimal("0"),) * 20,
                "B": (Decimal("100"),) * 20,
                "C": (Decimal("100"),) * 20,
            }
        ),
        "topic-a",
    )

    factors = {row.security_id: row.factors for row in result.candidates}
    assert result.topic_status.status == "ready"
    assert factors["B"][0] == factors["C"][0] == Decimal("0.5")
    assert result.topic_status.missing == 1
    assert "A: mean must be positive" in result.topic_status.reasons


def test_core_daily_denominator_uses_each_days_effective_membership():
    _, evaluate_core, _ = _engine()
    b_start = SESSIONS[-5]
    memberships = (
        topic_membership(
            security_id="A",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        topic_membership(
            security_id="B",
            effective_from=b_start,
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    context = _context(
        member_ids=("A", "B"),
        memberships=memberships,
        amounts={
            "A": (Decimal("100"),) * 20,
            "B": (Decimal("100"),) * 20,
        },
    )

    result = evaluate_core(context, "topic-a")

    factors = {row.security_id: row.factors for row in result.candidates}
    assert factors["A"][0] == Decimal("0.5")
    assert factors["B"][0] == Decimal("0.5")


def test_core_requires_ranked_security_membership_for_each_share_day():
    _, evaluate_core, _ = _engine()
    memberships = (
        topic_membership(
            security_id="A",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        topic_membership(
            security_id="B",
            effective_from=T,
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    context = _context(
        member_ids=("A", "B"),
        memberships=memberships,
        amounts={
            "A": (Decimal("100"),) * 20,
            "B": (Decimal("100"),) * 20,
        },
    )

    result = evaluate_core(context, "topic-a")

    assert result.topic_status.status == "ready"
    assert result.topic_status.valid == 1
    assert result.topic_status.missing == 1
    assert [row.security_id for row in result.candidates] == ["A"]
    assert any(
        "B" in reason and "membership" in reason
        for reason in result.topic_status.reasons
    )


def test_core_requires_complete_daily_coverage_and_all_member_amounts():
    _, evaluate_core, _ = _engine()
    incomplete = tuple(
        coverage_cell(
            trade_date=day,
            expected_member_count=3,
            received_member_count=3 if day != SESSIONS[-3] else 2,
            status="complete" if day != SESSIONS[-3] else "unavailable",
            reason=None if day != SESSIONS[-3] else "missing page",
            complete_as_of=datetime(
                day.year,
                day.month,
                day.day,
                17,
                tzinfo=UTC,
            ),
        )
        for day in SESSIONS
    )

    result = evaluate_core(_context(coverage_cells=incomplete), "topic-a")

    assert result.topic_status.status == "insufficient"
    assert result.candidates == ()
    assert any("coverage" in reason for reason in result.topic_status.reasons)


@pytest.mark.parametrize("scenario", ("missing_state", "outside_pool"))
def test_core_denominator_does_not_silently_drop_missing_share_day_member(scenario):
    _, evaluate_core, _ = _engine()
    missing_day = SESSIONS[-5]
    memberships = (
        topic_membership(
            security_id="A",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        topic_membership(
            security_id="B",
            effective_from=missing_day,
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    states = (
        state(
            security_id="A",
            exchange="TEST",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0],
        ),
    )
    pool_ids = ("A",)
    if scenario == "missing_state":
        states += (
            state(
                security_id="B",
                exchange="TEST",
                effective_from=SESSIONS[-4],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            ),
        )
        pool_ids = ("A", "B")

    result = evaluate_core(
        _context(
            member_ids=("A", "B"),
            pool_ids=pool_ids,
            memberships=memberships,
            states=states,
        ),
        "topic-a",
    )

    status = result.topic_status
    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (2, 0, 0, 2)
    assert any(
        "B" in reason and missing_day.isoformat() in reason
        for reason in status.reasons
    )
    assert result.candidates == ()


def test_core_is_insufficient_when_one_denominator_member_amount_is_missing():
    _, evaluate_core, _ = _engine()
    missing_day = SESSIONS[-7]
    amounts = [Decimal("100")] * 20
    amounts[-7] = None

    result = evaluate_core(
        _context(member_ids=("A",), amounts={"A": tuple(amounts)}),
        "topic-a",
    )

    status = result.topic_status
    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (1, 0, 0, 1)
    assert status.reasons == (
        f"A: incomplete topic amount coverage on {missing_day.isoformat()}",
    )
    assert result.candidates == ()


def test_core_is_insufficient_when_early_daily_denominator_is_nonpositive():
    _, evaluate_core, _ = _engine()
    zero_denominator_day = SESSIONS[-7]
    amounts = (Decimal("100"),) * 13 + (Decimal("0"),) + (Decimal("100"),) * 6

    result = evaluate_core(
        _context(amounts={security_id: amounts for security_id in ("A", "B", "C")}),
        "topic-a",
    )

    status = result.topic_status
    assert status.status == "insufficient"
    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (3, 0, 0, 3)
    assert status.reasons == (
        f"{zero_denominator_day.isoformat()}: "
        "topic amount denominator nonpositive or unavailable",
    )
    assert result.candidates == ()


def test_core_reports_empty_denominator_when_all_daily_members_are_excluded():
    _, evaluate_core, _ = _engine()
    member_ids = ("A", "B", "C")
    context = _context(
        member_ids=member_ids,
        states=tuple(
            state(
                security_id=security_id,
                exchange="TEST",
                security_type="fund",
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
            )
            for security_id in member_ids
        ),
    )

    status = evaluate_core(context, "topic-a").topic_status

    assert (
        status.expected,
        status.valid,
        status.legal_excluded,
        status.missing,
    ) == (3, 0, 3, 0)
    assert status.reasons == (
        "A: ineligible security state",
        "B: ineligible security state",
        "C: ineligible security state",
        f"{SESSIONS[-20].isoformat()}: topic amount denominator unavailable/empty",
    )


def test_core_zero_mean_and_currency_mismatch_are_insufficient():
    _, evaluate_core, _ = _engine()
    zero_context = _context(
        amounts={
            "A": (Decimal("0"),) * 20,
            "B": (Decimal("100"),) * 20,
            "C": (Decimal("100"),) * 20,
        }
    )
    currency_context = _context(
        amounts={
            "A": (Decimal("100"),) * 20,
            "B": (Decimal("100"),) * 20,
            "C": (Decimal("100"),) * 20,
        },
        currencies={"B": "USD"},
    )

    zero = evaluate_core(zero_context, "topic-a")
    currency = evaluate_core(currency_context, "topic-a")

    assert zero.topic_status.status == "ready"
    assert zero.topic_status.missing == 1
    assert any(
        "A" in reason and "mean" in reason for reason in zero.topic_status.reasons
    )
    assert currency.topic_status.status == "insufficient"
    assert any("currency" in reason for reason in currency.topic_status.reasons)


def test_core_can_have_complete_inputs_but_no_candidate():
    _, evaluate_core, _ = _engine()
    context = _context(
        member_ids=("A", "B"),
        amounts={
            "A": (Decimal("1000"),) * 15 + (Decimal("1"),) * 5,
            "B": (Decimal("1"),) * 15 + (Decimal("1000"),) * 5,
        },
    )

    result = evaluate_core(context, "topic-a")

    assert result.topic_status.status == "no_candidate"
    assert all(row.status == "eligible_non_candidate" for row in result.candidates)


def test_input_order_does_not_change_normalized_formal_results():
    _, evaluate_core, evaluate_trend = _engine()
    context = _context()
    reversed_context = RoleContext(
        market=context.market.model_copy(
            update={
                "security_states": tuple(reversed(context.market.security_states)),
                "bars": tuple(reversed(context.market.bars)),
                "expected_security_ids": tuple(
                    reversed(context.market.expected_security_ids)
                ),
            }
        ),
        topic=context.topic.model_copy(
            update={
                "topics": tuple(reversed(context.topic.topics)),
                "memberships": tuple(reversed(context.topic.memberships)),
                "coverage_cells": tuple(reversed(context.topic.coverage_cells)),
            }
        ),
        rule_version="role-test-v1",
    )

    assert evaluate_trend(context, "topic-a") == evaluate_trend(
        reversed_context, "topic-a"
    )
    assert evaluate_core(context, "topic-a") == evaluate_core(
        reversed_context, "topic-a"
    )
