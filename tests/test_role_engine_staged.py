from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from factories import bar, batch, state
from topic_factories import (
    coverage_cell,
    topic_batch,
    topic_definition,
    topic_membership,
)

from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.roles.models import (
    RankedCandidate,
    RoleContext,
    RoleEvaluation,
    RoleTopicStatus,
)
from dragon_radar.roles.rules import RULE_DEFINITIONS

T = date(2026, 9, 8)
CUTOFF = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _sessions(count: int = 70) -> tuple[date, ...]:
    result = []
    current = T
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(result))


SESSIONS = _sessions()
MEMBERS = ("A", "B", "C")


def _flat_then(end: str, *, count: int = 61) -> tuple[Decimal, ...]:
    return (Decimal("100"),) * (count - 1) + (Decimal(end),)


def _context(
    *,
    member_ids: tuple[str, ...] = MEMBERS,
    pool_ids: tuple[str, ...] | None = None,
    prices: dict[str, tuple[Decimal, ...]] | None = None,
    amounts: dict[str, tuple[Decimal | None, ...]] | None = None,
    currencies: dict[str, str] | None = None,
    limits: dict[str, dict] | None = None,
    bars_extra=(),
    coverage_cells=None,
    source_version: str = "s1",
    membership_version: str = "m1",
) -> RoleContext:
    pool_ids = pool_ids or member_ids
    prices = prices or {
        security_id: _flat_then(str(120 + index * 10))
        for index, security_id in enumerate(pool_ids)
    }
    amounts = amounts or {
        security_id: (Decimal("100"),) * 25 for security_id in pool_ids
    }
    currencies = currencies or {}
    limits = limits or {}
    market_bars = []
    for security_id in pool_ids:
        security_prices = prices.get(security_id, ())
        price_days = SESSIONS[-len(security_prices) :] if security_prices else ()
        security_amounts = amounts.get(security_id, ())
        amount_days = SESSIONS[-len(security_amounts) :] if security_amounts else ()
        amount_by_day = dict(zip(amount_days, security_amounts, strict=True))
        for day, close in zip(price_days, security_prices, strict=True):
            changes = {}
            if day == T:
                changes.update(limits.get(security_id, {}))
            market_bars.append(
                bar(
                    security_id=security_id,
                    trade_date=day,
                    close=close,
                    amount=amount_by_day.get(day, Decimal("100")),
                    currency=currencies.get(security_id, "CNY"),
                    source_version=source_version,
                    **changes,
                )
            )
    market_bars.extend(bars_extra)
    states = tuple(
        state(
            security_id=security_id,
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0],
            source_version=source_version,
        )
        for security_id in pool_ids
    )
    memberships = tuple(
        topic_membership(
            security_id=security_id,
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for security_id in member_ids
    )
    if coverage_cells is None:
        coverage_cells = tuple(
            coverage_cell(
                trade_date=day,
                expected_member_count=len(member_ids),
                received_member_count=len(member_ids),
                complete_as_of=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
            )
            for day in SESSIONS
        )
    calendar = ExchangeCalendar(exchange="TEST", version="staged-c1", sessions=SESSIONS)
    market = batch(
        trade_date=T,
        as_of=CUTOFF,
        source_version=source_version,
        calendar=calendar,
        calendar_version=calendar.version,
        expected_security_ids=pool_ids,
        security_states=states,
        bars=tuple(market_bars),
    )
    topic = topic_batch(
        trade_date=T,
        as_of=CUTOFF,
        source_version=source_version,
        exchange="TEST",
        calendar=calendar,
        calendar_version=calendar.version,
        coverage_from=SESSIONS[0],
        coverage_to=T,
        coverage_cells=coverage_cells,
        topics=(
            topic_definition(
                effective_from=SESSIONS[0],
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        memberships=memberships,
        membership_version=membership_version,
    )
    return RoleContext(market=market, topic=topic, rule_version="role-test-v1")


def _trend_evaluation(
    security_ids: tuple[str, ...],
    *,
    topic_id: str = "topic-a",
) -> RoleEvaluation:
    rows = tuple(
        RankedCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="trend",
            status="candidate",
            factors=(Decimal("1"), Decimal("1"), Decimal("0")),
            reasons=("candidate_conditions_met",),
            tie_rank=1,
        )
        for security_id in security_ids
    )
    return RoleEvaluation(
        topic_status=RoleTopicStatus(
            topic_id=topic_id,
            role="trend",
            capability="ready",
            status="ready",
            expected=len(rows),
            valid=len(rows),
            legal_excluded=0,
            missing=0,
        ),
        candidates=rows,
    )


def _predecessor(context: RoleContext, **changes):
    from dragon_radar.roles.engine import RelayPredecessorRun

    values = {
        "trade_date": SESSIONS[-2],
        "active_run_count": 1,
        "rule_version": context.rule_version,
        "visibility_mode": "local_display",
        "market_benchmark_definition": RULE_DEFINITIONS[context.rule_version]["roles"][
            "trend"
        ]["required_inputs"]["market_benchmark"]["definition"],
        "topic_id": "topic-a",
        "topic_source_id": context.topic.source_id,
        "topic_source_version": context.topic.source_version,
        "taxonomy_version": context.topic.taxonomy_version,
        "membership_version": context.topic.membership_version,
        "member_ids": MEMBERS,
        "trend_evaluation": _trend_evaluation(("B",)),
    }
    values.update(changes)
    return RelayPredecessorRun(**values)


@pytest.fixture
def role_scenario():
    from dragon_radar.roles.engine import (
        evaluate_catchup,
        evaluate_emotion,
        evaluate_follower,
        evaluate_relay,
    )

    def evaluate(scenario: str) -> RoleTopicStatus:
        if scenario == "unknown_limit":
            context = _context(
                member_ids=("A",),
                prices={"A": _flat_then("100")},
                limits={"A": {"limit_status": "unknown"}},
            )
            return evaluate_emotion(context, "topic-a").topic_status
        if scenario == "disjoint_catchup_windows":
            return evaluate_catchup(_catchup_context(), "topic-a").topic_status
        if scenario == "tied_trend_core":
            context = _follower_context(tied=True)
            return evaluate_follower(context, "topic-a").topic_status
        if scenario == "matching_predecessor_event":
            context = _relay_context()
            return evaluate_relay(
                context, "topic-a", _predecessor(context)
            ).topic_status
        if scenario == "incomparable_predecessor":
            context = _relay_context()
            predecessor = _predecessor(context, visibility_mode="export")
            return evaluate_relay(context, "topic-a", predecessor).topic_status
        raise AssertionError(f"unknown scenario: {scenario}")

    return evaluate


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("unknown_limit", "insufficient"),
        ("disjoint_catchup_windows", "simplified"),
        ("tied_trend_core", "insufficient"),
        ("matching_predecessor_event", "pending_validation"),
        ("incomparable_predecessor", "insufficient"),
    ],
)
def test_staged_role_statuses(role_scenario, scenario, expected):
    assert role_scenario(scenario).status == expected


def _catchup_context(
    *,
    current_amount: str = "200",
    prior_amount: str = "100",
    currencies: dict[str, str] | None = None,
) -> RoleContext:
    prices = {
        "A": (Decimal("100"),) * 21 + (Decimal("120"),) * 5,
        "B": (Decimal("100"),) + (Decimal("120"),) * 20 + (Decimal("126"),) * 5,
        "C": (Decimal("100"),) + (Decimal("120"),) * 20 + (Decimal("126"),) * 5,
    }
    amounts = {
        "A": (Decimal(prior_amount),) * 20 + (Decimal(current_amount),) * 5,
        "B": (Decimal("100"),) * 25,
        "C": (Decimal("100"),) * 25,
    }
    return _context(prices=prices, amounts=amounts, currencies=currencies)


def _follower_context(*, tied: bool = False) -> RoleContext:
    ends = {"A": "150", "B": "150" if tied else "130", "C": "120"}
    prices = {security_id: _flat_then(end) for security_id, end in ends.items()}
    prices.update({security_id: _flat_then("100") for security_id in ("M1", "M2")})
    return _context(
        pool_ids=(*MEMBERS, "M1", "M2"),
        prices=prices,
        amounts={
            security_id: (Decimal("100"),) * 5 + (Decimal("200"),) * 5
            for security_id in (*MEMBERS, "M1", "M2")
        },
    )


def _relay_context() -> RoleContext:
    prices = {
        "A": (Decimal("100"),) * 60 + (Decimal("120"),),
        "B": (Decimal("130"),) + (Decimal("100"),) * 60,
        "C": (Decimal("100"),) * 60 + (Decimal("110"),),
        "M1": _flat_then("100"),
        "M2": _flat_then("100"),
    }
    return _context(pool_ids=(*MEMBERS, "M1", "M2"), prices=prices)


def test_emotion_uses_exact_limit_streak_relative5_and_daily_share():
    from dragon_radar.roles.engine import evaluate_emotion

    context = _context(
        prices={
            "A": _flat_then("120"),
            "B": _flat_then("110"),
            "C": _flat_then("100"),
        },
        amounts={
            "A": (Decimal("100"),) * 24 + (Decimal("300"),),
            "B": (Decimal("100"),) * 25,
            "C": (Decimal("100"),) * 25,
        },
        limits={
            "A": {
                "limit_status": "limited",
                "limit_date": T,
                "limit_up": Decimal("120"),
                "limit_down": Decimal("80"),
            }
        },
    )

    result = evaluate_emotion(context, "topic-a")

    assert result.topic_status.status == "simplified"
    assert result.topic_status.expected == result.topic_status.valid == 3
    candidate = result.candidates[0]
    assert (candidate.security_id, candidate.status, candidate.tie_rank) == (
        "A",
        "simplified_candidate",
        1,
    )
    assert candidate.factors == (
        Decimal("1"),
        Decimal("0.1"),
        Decimal("0.6"),
    )
    assert "daily_simplified_candidate_only" in candidate.reasons
    assert not any(
        claim in " ".join(candidate.reasons)
        for claim in ("first_seal", "reseal", "order_quality")
    )


def test_emotion_keeps_complete_first_business_tie_as_simplified_candidates():
    from dragon_radar.roles.engine import evaluate_emotion

    limit = {
        "limit_status": "limited",
        "limit_date": T,
        "limit_up": Decimal("120"),
        "limit_down": Decimal("80"),
    }
    result = evaluate_emotion(
        _context(
            prices={
                "A": _flat_then("120"),
                "B": _flat_then("120"),
                "C": _flat_then("100"),
            },
            amounts={
                "A": (Decimal("200"),) * 25,
                "B": (Decimal("200"),) * 25,
                "C": (Decimal("100"),) * 25,
            },
            limits={"A": limit, "B": limit},
        ),
        "topic-a",
    )

    assert [
        (row.security_id, row.status, row.tie_rank) for row in result.candidates[:2]
    ] == [
        ("A", "simplified_candidate", 1),
        ("B", "simplified_candidate", 1),
    ]


@pytest.mark.parametrize(("current", "prior"), (("100", "100"), ("200", "0")))
def test_catchup_strict_zero_boundary_and_zero_denominator(current, prior):
    from dragon_radar.roles.engine import evaluate_catchup

    result = evaluate_catchup(
        _catchup_context(current_amount=current, prior_amount=prior), "topic-a"
    )

    if prior == "0":
        assert result.topic_status.missing == 1
        assert any(
            "A" in reason and "mean" in reason for reason in result.topic_status.reasons
        )
    else:
        assert result.topic_status.status == "no_candidate"
        assert not any(
            row.status == "simplified_candidate" for row in result.candidates
        )


def test_catchup_rejects_currency_change_and_incomplete_coverage():
    from dragon_radar.roles.engine import evaluate_catchup

    baseline = _catchup_context(currencies={"A": "USD"})
    currency_changed_bars = tuple(
        daily_bar.model_copy(update={"currency": "CNY"})
        if daily_bar.security_id == "A" and daily_bar.trade_date == T
        else daily_bar
        for daily_bar in baseline.market.bars
    )
    changed = RoleContext(
        market=baseline.market.model_copy(update={"bars": currency_changed_bars}),
        topic=baseline.topic,
        rule_version=baseline.rule_version,
    )
    broken_day = SESSIONS[-10]
    incomplete_cells = tuple(
        cell.model_copy(update={"status": "unavailable", "reason": "missing page"})
        if cell.trade_date == broken_day
        else cell
        for cell in baseline.topic.coverage_cells
    )
    incomplete = RoleContext(
        market=baseline.market,
        topic=baseline.topic.model_copy(update={"coverage_cells": incomplete_cells}),
        rule_version=baseline.rule_version,
    )

    currency_result = evaluate_catchup(changed, "topic-a")
    coverage_result = evaluate_catchup(incomplete, "topic-a")

    assert currency_result.topic_status.missing == 1
    assert any("currency" in reason for reason in currency_result.topic_status.reasons)
    assert coverage_result.topic_status.status == "insufficient"
    assert broken_day.isoformat() in " ".join(coverage_result.topic_status.reasons)


def test_follower_only_emits_watch_rows_and_uses_nonoverlapping_amount_windows():
    from dragon_radar.roles.engine import evaluate_follower

    result = evaluate_follower(_follower_context(), "topic-a")

    assert result.topic_status.status == "watch_only"
    assert result.topic_status.valid == 3
    assert [(row.security_id, row.status) for row in result.candidates] == [
        ("B", "watch"),
        ("C", "watch"),
    ]
    assert all(row.status in {"watch", "excluded"} for row in result.candidates)
    assert result.candidates[0].factors == (Decimal("0.3"), Decimal("1"))
    assert "intraday_start_order_unavailable" in result.candidates[0].reasons
    assert "independent_catalyst_evidence_unavailable" in result.candidates[0].reasons
    assert "correlation_is_not_causation" in result.candidates[0].reasons


def test_follower_zero_prior_amount_and_future_bar_are_not_silently_used():
    from dragon_radar.roles.engine import evaluate_follower

    context = _follower_context()
    changed_bars = tuple(
        daily_bar.model_copy(update={"amount": Decimal("0")})
        if daily_bar.security_id == "B" and daily_bar.trade_date in SESSIONS[-10:-5]
        else daily_bar
        for daily_bar in context.market.bars
    )
    late = bar(
        security_id="B",
        trade_date=SESSIONS[-10],
        close=Decimal("100"),
        amount=Decimal("999"),
        available_at=CUTOFF + timedelta(seconds=1),
    )
    changed = RoleContext(
        market=context.market.model_copy(update={"bars": (*changed_bars, late)}),
        topic=context.topic,
        rule_version=context.rule_version,
    )

    result = evaluate_follower(changed, "topic-a")

    assert result.topic_status.missing == 1
    assert {row.security_id for row in result.candidates} == {"C"}
    assert any(
        "B" in reason and "mean" in reason for reason in result.topic_status.reasons
    )


def test_follower_requires_prices_only_from_t_minus_5_through_t():
    from dragon_radar.roles.engine import evaluate_follower

    context = _follower_context()
    changed_bars = tuple(
        daily_bar.model_copy(update={"close": None})
        if daily_bar.security_id == "B" and daily_bar.trade_date in SESSIONS[-10:-6]
        else daily_bar
        for daily_bar in context.market.bars
    )
    changed = RoleContext(
        market=context.market.model_copy(update={"bars": changed_bars}),
        topic=context.topic,
        rule_version=context.rule_version,
    )

    result = evaluate_follower(changed, "topic-a")

    assert result.topic_status.status == "watch_only"
    assert result.topic_status.valid == 3
    assert {row.security_id for row in result.candidates} == {"B", "C"}


def test_relay_records_membership_change_without_making_run_incomparable():
    from dragon_radar.roles.engine import evaluate_relay

    context = _relay_context()
    predecessor = _predecessor(
        context,
        membership_version="old-membership",
        member_ids=("A", "B"),
    )

    result = evaluate_relay(context, "topic-a", predecessor)

    assert result.topic_status.status == "pending_validation"
    assert result.candidates[0].security_id == "A"
    assert result.candidates[0].status == "pending_validation"
    assert "membership_changed" in result.topic_status.reasons
    assert "validation_window:T+1..T+5" in result.candidates[0].reasons


def test_relay_without_predecessor_is_insufficient():
    from dragon_radar.roles.engine import evaluate_relay

    result = evaluate_relay(_relay_context(), "topic-a", None)

    assert result.topic_status.status == "insufficient"
    assert "predecessor_run_unavailable" in result.topic_status.reasons
    assert result.candidates == ()


@pytest.mark.parametrize(
    "changes",
    (
        {"active_run_count": 2},
        {"trade_date": T},
        {"rule_version": "role-v1"},
        {"market_benchmark_definition": "other"},
        {"topic_source_version": "revision-2"},
    ),
)
def test_relay_rejects_incomparable_predecessor_contract(changes):
    from dragon_radar.roles.engine import evaluate_relay

    context = _relay_context()
    result = evaluate_relay(context, "topic-a", _predecessor(context, **changes))
    assert result.topic_status.status == "insufficient"
    assert result.candidates == ()


@pytest.mark.parametrize(
    "evaluate_name",
    ("evaluate_emotion", "evaluate_catchup", "evaluate_follower"),
)
def test_staged_results_are_stable_under_input_order(evaluate_name):
    from dragon_radar.roles import engine

    context = {
        "evaluate_emotion": _context(
            limits={
                "A": {
                    "limit_status": "limited",
                    "limit_date": T,
                    "limit_up": Decimal("120"),
                    "limit_down": Decimal("80"),
                }
            }
        ),
        "evaluate_catchup": _catchup_context(),
        "evaluate_follower": _follower_context(),
    }[evaluate_name]
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
        rule_version=context.rule_version,
    )
    evaluate = getattr(engine, evaluate_name)
    assert evaluate(context, "topic-a") == evaluate(reversed_context, "topic-a")
