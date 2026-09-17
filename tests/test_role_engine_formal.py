from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from factories import bar, batch, state
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
    coverage_cells = coverage_cells or tuple(
        coverage_cell(
            trade_date=day,
            expected_member_count=len(member_ids),
            received_member_count=len(member_ids),
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
        ("B", "candidate", 2),
        ("C", "eligible_non_candidate", 3),
    ]
    assert result.candidates[0].factors == (
        Decimal("0.275"),
        Decimal("0.05"),
        Decimal("0"),
    )
    assert result.candidates[2].factors[2] > Decimal("0")


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
        ("A", "candidate", 2),
        ("C", "candidate", 3),
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
