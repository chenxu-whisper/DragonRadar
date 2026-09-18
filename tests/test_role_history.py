from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from factories import bar, batch, grant, state
from pydantic import ValidationError
from topic_factories import (
    coverage_cell,
    topic_batch,
    topic_definition,
    topic_membership,
)

from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.roles.history import (
    build_limit_ladder,
    compare_runs,
    validate_relay,
)
from dragon_radar.roles.models import (
    LimitRow,
    PendingRelayValidation,
    RankedCandidate,
    RelayBaselinePrice,
    RelayObservationEligibility,
    RoleChange,
    RoleContext,
    RoleEvaluation,
    RoleRunEvidence,
    RoleTopicStatus,
    RoleValidation,
)

T = date(2026, 9, 8)
CUTOFF = datetime(2026, 9, 18, 20, tzinfo=UTC)
TOPIC = "topic-a"


def _sessions(count: int = 20, *, end: date = T) -> tuple[date, ...]:
    result = []
    current = end
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(result))


SESSIONS = _sessions()
FUTURE_SESSIONS = _sessions(25, end=date(2026, 9, 15))


def _limited_bar(
    security_id: str,
    day: date,
    *,
    close: str = "11",
    high: str | None = None,
    status: str = "limited",
    available_at: datetime | None = None,
):
    values = {
        "security_id": security_id,
        "trade_date": day,
        "close": Decimal(close),
        "open": Decimal("10"),
        "high": Decimal(high or close),
        "low": Decimal("10"),
        "available_at": available_at
        or datetime(day.year, day.month, day.day, 16, tzinfo=UTC),
    }
    if status == "limited":
        values.update(
            limit_status="limited",
            limit_date=day,
            limit_up=Decimal("11"),
            limit_down=Decimal("9"),
        )
    else:
        values.update(limit_status=status)
    return bar(**values)


def _context(
    *,
    trade_date: date = T,
    sessions: tuple[date, ...] = SESSIONS,
    topic_id: str = TOPIC,
    member_ids: tuple[str, ...] = ("A",),
    bars=(),
    states=(),
    market_grant=None,
    topic_grant=None,
    membership_version: str = "m1",
    rule_version: str = "role-test-v1",
    provenance: str = "contemporaneous",
    taxonomy_version: str = "t1",
    topic_source_version: str = "s1",
    pool_version: str = "p1",
    market_source_id: str = "synthetic",
    adjustment_version: str = "a1",
    calendar_version: str = "history-c1",
    exchange: str = "TEST",
    expected_security_ids: tuple[str, ...] | None = None,
) -> RoleContext:
    known_at = datetime(2026, 1, 1, tzinfo=UTC)
    states = states or tuple(
        state(
            security_id=security_id,
            effective_from=sessions[0],
            available_at=known_at,
            listing_date=sessions[0],
            pool_version=pool_version,
            source_id=market_source_id,
            exchange=exchange,
        )
        for security_id in member_ids
    )
    memberships = tuple(
        topic_membership(
            topic_id=topic_id,
            security_id=security_id,
            effective_from=sessions[0],
            available_at=known_at,
        )
        for security_id in member_ids
    )
    calendar = ExchangeCalendar(
        exchange=exchange,
        version=calendar_version,
        sessions=sessions,
    )
    market = batch(
        trade_date=trade_date,
        as_of=CUTOFF,
        grant=market_grant
        or grant(
            source_id=market_source_id,
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        ),
        source_id=market_source_id,
        adjustment_version=adjustment_version,
        calendar=calendar,
        calendar_version=calendar.version,
        expected_security_ids=expected_security_ids
        or tuple(
            sorted(
                set(member_ids)
                | {daily_bar.security_id for daily_bar in bars}
                | {security_state.security_id for security_state in states}
            )
        ),
        security_states=tuple(states),
        bars=tuple(bars),
        pool_version=pool_version,
        provenance=provenance,
    )
    topic = topic_batch(
        trade_date=trade_date,
        as_of=CUTOFF,
        grant=topic_grant or grant(expires_at=datetime(2027, 1, 1, tzinfo=UTC)),
        exchange=exchange,
        calendar=calendar,
        calendar_version=calendar.version,
        coverage_from=sessions[0],
        coverage_to=trade_date,
        coverage_cells=tuple(
            coverage_cell(
                topic_id=topic_id,
                trade_date=day,
                expected_member_count=len(member_ids),
                received_member_count=len(member_ids),
                complete_as_of=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
            )
            for day in sessions
            if day <= trade_date
        ),
        topics=(
            topic_definition(
                topic_id=topic_id,
                effective_from=sessions[0],
                available_at=known_at,
            ),
        ),
        memberships=memberships,
        membership_version=membership_version,
        taxonomy_version=taxonomy_version,
        source_version=topic_source_version,
        provenance=provenance,
    )
    return RoleContext(market=market, topic=topic, rule_version=rule_version)


def _evaluation(
    role: str,
    rows: tuple[tuple[str, int], ...],
    *,
    expected: int | None = None,
) -> RoleEvaluation:
    positive = {
        "trend": "candidate",
        "core": "candidate",
        "emotion": "simplified_candidate",
        "catchup": "simplified_candidate",
        "follower": "watch",
        "relay": "pending_validation",
    }[role]
    capability = {
        "trend": "ready",
        "core": "ready",
        "emotion": "simplified",
        "catchup": "simplified",
        "follower": "watch_only",
        "relay": "pending_validation",
    }[role]
    status = RoleTopicStatus(
        topic_id=TOPIC,
        role=role,
        capability=capability,
        status=capability,
        expected=expected if expected is not None else len(rows),
        valid=expected if expected is not None else len(rows),
        legal_excluded=0,
        missing=0,
    )
    candidates = tuple(
        RankedCandidate(
            topic_id=TOPIC,
            security_id=security_id,
            role=role,
            status=positive,
            factors=(),
            tie_rank=tie_rank,
        )
        for security_id, tie_rank in rows
    )
    return RoleEvaluation(topic_status=status, candidates=candidates)


def _insufficient_evaluation(role: str) -> RoleEvaluation:
    capability = {
        "trend": "ready",
        "core": "ready",
        "emotion": "simplified",
        "catchup": "simplified",
        "follower": "watch_only",
        "relay": "pending_validation",
    }[role]
    return RoleEvaluation(
        topic_status=RoleTopicStatus(
            topic_id=TOPIC,
            role=role,
            capability=capability,
            status="insufficient",
            expected=1,
            valid=0,
            legal_excluded=0,
            missing=1,
        )
    )


def _run(
    run_id: str,
    context: RoleContext,
    *,
    topic_id: str = TOPIC,
    evaluations: tuple[RoleEvaluation, ...] = (),
    previous_id: str | None = None,
    member_ids: tuple[str, ...] = ("A",),
    **changes,
) -> RoleRunEvidence:
    values = {
        "role_run_id": run_id,
        "previous_comparable_run_id": previous_id,
        "context": context,
        "topic_id": topic_id,
        "visibility_mode": (
            "retrospective"
            if "retrospective" in {context.market.provenance, context.topic.provenance}
            else "contemporaneous"
        ),
        "market_benchmark_definition": ("eligible_declared_market_pool_return_median"),
        "topic_source_id": context.topic.source_id,
        "topic_source_version": context.topic.source_version,
        "taxonomy_version": context.topic.taxonomy_version,
        "membership_version": context.topic.membership_version,
        "member_ids": member_ids,
        "evaluations": evaluations,
        "verification_status": "passed",
        "activation_event_id": f"activation-{run_id}",
        "active_run_count": 1,
        **changes,
    }
    return RoleRunEvidence(**values)


def test_limit_ladder_counts_ten_day_hits_and_exact_closing_streak():
    bars = tuple(
        _limited_bar("A", day, close="11" if index in {0, 2, 6, 7, 8, 9} else "10")
        for index, day in enumerate(SESSIONS[-10:])
    )

    rows = build_limit_ladder(_context(bars=bars), TOPIC)

    assert rows == (
        LimitRow(
            trade_date=T,
            topic_id=TOPIC,
            security_id="A",
            closing_limit_streak=4,
            limit_hits_10=6,
            broken_board=False,
            touched_not_sealed=False,
        ),
    )


def test_limit_ladder_revalidates_role_context_before_use():
    context = _context()
    forged = context.model_copy(
        update={
            "topic": context.topic.model_copy(
                update={"trade_date": SESSIONS[-2]},
            )
        }
    )

    with pytest.raises(ValidationError, match="trade_date"):
        build_limit_ladder(forged, TOPIC)


def test_limit_ladder_returns_unavailable_rows_for_off_calendar_trade_date():
    off_calendar_day = T + timedelta(days=1)
    context = _context(
        trade_date=off_calendar_day,
        member_ids=("A", "B"),
    )

    rows = build_limit_ladder(context, TOPIC)

    assert tuple(row.security_id for row in rows) == ("A", "B")
    assert all(
        (
            row.closing_limit_streak,
            row.limit_hits_10,
            row.broken_board,
            row.touched_not_sealed,
            row.reasons,
        )
        == (None, None, None, None, ("trade_date_not_in_calendar_sessions",))
        for row in rows
    )


@pytest.mark.parametrize(
    ("mismatch", "expected_reason"),
    (
        ("market_topic_exchange", "cross_exchange"),
        ("market_calendar_version", "market_calendar_version_mismatch"),
        ("topic_calendar_version", "topic_calendar_version_mismatch"),
        ("market_calendar_exchange", "cross_exchange"),
        ("topic_calendar_exchange", "cross_exchange"),
        ("topic_calendar_sessions", "market_topic_calendar_sessions_mismatch"),
        ("market_calendar_sessions", "market_topic_calendar_sessions_mismatch"),
    ),
)
def test_limit_ladder_blocks_inconsistent_global_context(
    mismatch,
    expected_reason,
):
    context = _context(bars=tuple(_limited_bar("A", day) for day in SESSIONS[-10:]))
    if mismatch == "market_topic_exchange":
        context = context.model_copy(
            update={"topic": context.topic.model_copy(update={"exchange": "OTHER"})}
        )
    elif mismatch == "market_calendar_version":
        context = context.model_copy(
            update={
                "market": context.market.model_copy(
                    update={"calendar_version": "other-calendar"}
                )
            }
        )
    elif mismatch == "topic_calendar_version":
        context = context.model_copy(
            update={
                "topic": context.topic.model_copy(
                    update={"calendar_version": "other-calendar"}
                )
            }
        )
    elif mismatch in {"market_calendar_exchange", "topic_calendar_exchange"}:
        side = "market" if mismatch == "market_calendar_exchange" else "topic"
        snapshot = getattr(context, side)
        context = context.model_copy(
            update={
                side: snapshot.model_copy(
                    update={
                        "calendar": snapshot.calendar.model_copy(
                            update={"exchange": "OTHER"}
                        )
                    }
                )
            }
        )
    elif mismatch == "topic_calendar_sessions":
        context = context.model_copy(
            update={
                "topic": context.topic.model_copy(
                    update={
                        "calendar": context.topic.calendar.model_copy(
                            update={"sessions": context.topic.calendar.sessions[:-1]}
                        )
                    }
                )
            }
        )
    else:
        context = context.model_copy(
            update={
                "market": context.market.model_copy(
                    update={
                        "calendar": context.market.calendar.model_copy(
                            update={"sessions": context.market.calendar.sessions[:-1]}
                        )
                    }
                )
            }
        )

    row = build_limit_ladder(context, TOPIC)[0]

    assert row.closing_limit_streak is None
    assert row.limit_hits_10 is None
    assert row.broken_board is None
    assert row.touched_not_sealed is None
    assert expected_reason in row.reasons


@pytest.mark.parametrize("interruption", ("suspended", "no_limit"))
def test_limit_ladder_suspension_and_no_limit_interrupt_streak(interruption):
    previous = SESSIONS[-2]
    states = (
        state(
            security_id="A",
            effective_from=SESSIONS[0],
            effective_to=T if interruption == "suspended" else None,
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0],
        ),
    )
    bars = (_limited_bar("A", previous),)
    if interruption == "suspended":
        states += (
            state(
                security_id="A",
                effective_from=T,
                available_at=datetime(2026, 1, 1, tzinfo=UTC),
                listing_date=SESSIONS[0],
                suspended=True,
            ),
        )
    else:
        bars += (_limited_bar("A", T, status="no_limit", close="10"),)

    row = build_limit_ladder(_context(bars=bars, states=states), TOPIC)[0]

    assert row.closing_limit_streak == 0
    assert row.broken_board is True


def test_limit_ladder_unknown_limit_propagates_and_ignores_future_bar():
    bars = tuple(_limited_bar("A", day) for day in SESSIONS[-10:-1]) + (
        _limited_bar("A", T, status="unknown", close="10"),
        _limited_bar(
            "A",
            T,
            available_at=CUTOFF + timedelta(seconds=1),
        ),
    )

    row = build_limit_ladder(_context(bars=bars), TOPIC)[0]

    assert row.closing_limit_streak is None
    assert row.limit_hits_10 is None
    assert row.broken_board is None
    assert row.touched_not_sealed is None
    assert row.reasons == ("limit_status_unavailable",)


def test_limit_ladder_marks_quality_gated_touched_not_sealed():
    bars = tuple(_limited_bar("A", day, close="10") for day in SESSIONS[-10:-1]) + (
        _limited_bar("A", T, close="10.5", high="11"),
    )

    row = build_limit_ladder(_context(bars=bars), TOPIC)[0]

    assert row.closing_limit_streak == 0
    assert row.limit_hits_10 == 0
    assert row.touched_not_sealed is True


def test_limit_ladder_rejects_high_above_limit_as_unavailable_touch_state():
    row = build_limit_ladder(
        _context(bars=(_limited_bar("A", T, close="10.5", high="12"),)),
        TOPIC,
    )[0]

    assert row.touched_not_sealed is None
    assert "touch_quality_gate_failed" in row.reasons


def test_limit_ladder_does_not_treat_calendar_boundary_as_exact_streak():
    bars = tuple(_limited_bar("A", day) for day in SESSIONS)
    states = (
        state(
            security_id="A",
            effective_from=SESSIONS[0],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=SESSIONS[0] - timedelta(days=1),
        ),
    )

    row = build_limit_ladder(_context(bars=bars, states=states), TOPIC)[0]

    assert row.closing_limit_streak is None
    assert "history_boundary_unknown" in row.reasons


def test_limit_ladder_counts_exact_streak_from_first_listing_session():
    bars = tuple(_limited_bar("A", day) for day in SESSIONS)

    row = build_limit_ladder(_context(bars=bars), TOPIC)[0]

    assert row.closing_limit_streak == len(SESSIONS)
    assert "history_boundary_unknown" not in row.reasons


def test_limit_ladder_stops_exact_streak_at_later_listing_boundary():
    listing_session = SESSIONS[-3]
    bars = tuple(_limited_bar("A", day) for day in SESSIONS[-3:])
    states = (
        state(
            security_id="A",
            effective_from=listing_session,
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
            listing_date=listing_session,
        ),
    )

    row = build_limit_ladder(_context(bars=bars, states=states), TOPIC)[0]

    assert row.closing_limit_streak == 3
    assert row.limit_hits_10 == 3
    assert row.reasons == ()


def test_limit_ladder_explains_unavailable_historical_observation():
    previous = SESSIONS[-2]
    bars = tuple(_limited_bar("A", day) for day in SESSIONS[-10:] if day != previous)

    row = build_limit_ladder(_context(bars=bars), TOPIC)[0]

    assert row.closing_limit_streak is None
    assert row.limit_hits_10 is None
    assert row.broken_board is None
    assert row.reasons == (f"{previous.isoformat()}: daily_bar_unavailable",)


def test_limit_ladder_materializes_member_missing_from_declared_market_pool():
    row = build_limit_ladder(
        _context(
            member_ids=("A",),
            expected_security_ids=("B",),
            bars=tuple(_limited_bar("A", day) for day in SESSIONS[-10:]),
        ),
        TOPIC,
    )[0]

    assert row.security_id == "A"
    assert row.closing_limit_streak is None
    assert row.limit_hits_10 is None
    assert row.broken_board is None
    assert row.touched_not_sealed is None
    assert row.reasons == ("member_not_in_market_pool",)


@pytest.mark.parametrize(
    "changes",
    (
        {"closing_limit_streak": 4, "limit_hits_10": 3},
        {"closing_limit_streak": 11, "limit_hits_10": 9},
        {
            "closing_limit_streak": 1,
            "limit_hits_10": 1,
            "broken_board": True,
        },
        {
            "closing_limit_streak": 1,
            "limit_hits_10": 1,
            "touched_not_sealed": True,
        },
        {
            "closing_limit_streak": 0,
            "limit_hits_10": 0,
            "broken_board": True,
        },
        {"closing_limit_streak": 9, "limit_hits_10": 10},
        {
            "closing_limit_streak": 0,
            "limit_hits_10": 10,
            "broken_board": True,
        },
        {
            "closing_limit_streak": 0,
            "limit_hits_10": 10,
            "touched_not_sealed": True,
        },
    ),
)
def test_limit_row_rejects_contradictory_known_metrics(changes):
    values = {
        "trade_date": T,
        "topic_id": TOPIC,
        "security_id": "A",
        "closing_limit_streak": 0,
        "limit_hits_10": 0,
        "broken_board": False,
        "touched_not_sealed": False,
        **changes,
    }

    with pytest.raises(ValidationError):
        LimitRow(**values)


def test_limit_row_allows_streak_beyond_ten_day_hit_window():
    row = LimitRow(
        trade_date=T,
        topic_id=TOPIC,
        security_id="A",
        closing_limit_streak=11,
        limit_hits_10=10,
        broken_board=False,
        touched_not_sealed=False,
    )

    assert row.closing_limit_streak == 11
    assert row.limit_hits_10 == 10


@pytest.mark.parametrize(
    ("flag", "limit_hits_10"),
    (
        ("broken_board", 1),
        ("touched_not_sealed", 0),
    ),
)
def test_limit_row_requires_explicit_zero_streak_when_not_sealed(
    flag,
    limit_hits_10,
):
    values = {
        "trade_date": T,
        "topic_id": TOPIC,
        "security_id": "A",
        "closing_limit_streak": None,
        "limit_hits_10": limit_hits_10,
        "broken_board": False,
        "touched_not_sealed": False,
        flag: True,
    }

    with pytest.raises(
        ValidationError,
        match="requires a zero closing_limit_streak",
    ):
        LimitRow(**values)


def test_compare_runs_reports_added_exited_tie_unchanged_and_membership_change():
    market_pool = ("A", "B", "C", "D", "E")
    previous_context = _context(
        trade_date=SESSIONS[-2],
        member_ids=("A", "B", "C", "D"),
        expected_security_ids=market_pool,
    )
    current_context = _context(
        member_ids=("A", "B", "C", "E"),
        membership_version="m2",
        expected_security_ids=market_pool,
    )
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1), ("B", 2), ("D", 3)), expected=4),),
        member_ids=("A", "B", "C", "D"),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1), ("B", 1), ("E", 3)), expected=4),),
        member_ids=("A", "B", "C", "E"),
    )

    changes = compare_runs(current, previous)

    assert [
        (change.security_id, change.change_type, change.membership_changed)
        for change in changes
    ] == [
        ("A", "tie_changed", True),
        ("B", "tie_changed", True),
        ("D", "exited", True),
        ("E", "added", True),
    ]
    assert all(change.comparison_status == "comparable" for change in changes)


def test_compare_runs_marks_every_member_of_changed_tie_group():
    previous_context = _context(trade_date=SESSIONS[-2], member_ids=("A", "B"))
    current_context = _context(member_ids=("A", "B"))
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1), ("B", 1))),),
        member_ids=("A", "B"),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1), ("B", 2))),),
        member_ids=("A", "B"),
    )

    changes = compare_runs(current, previous)

    assert [(change.security_id, change.change_type) for change in changes] == [
        ("A", "tie_changed"),
        ("B", "tie_changed"),
    ]


def test_compare_runs_reports_unchanged_for_identical_assignment_and_tie_group():
    previous_context = _context(trade_date=SESSIONS[-2], member_ids=("A", "B"))
    current_context = _context(member_ids=("A", "B"))
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1), ("B", 1))),),
        member_ids=("A", "B"),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1), ("B", 1))),),
        member_ids=("A", "B"),
    )

    changes = compare_runs(current, previous)

    assert [(change.security_id, change.change_type) for change in changes] == [
        ("A", "unchanged"),
        ("B", "unchanged"),
    ]
    assert all(change.comparison_status == "comparable" for change in changes)
    assert all(change.membership_changed is False for change in changes)
    assert all(change.reasons == () for change in changes)


@pytest.mark.parametrize("insufficient_side", ("current", "previous"))
def test_compare_runs_does_not_infer_change_from_insufficient_role(
    insufficient_side,
):
    previous_evaluation = _evaluation("trend", (("A", 1),))
    current_evaluation = _evaluation("trend", (("A", 1),))
    if insufficient_side == "current":
        current_evaluation = _insufficient_evaluation("trend")
    else:
        previous_evaluation = _insufficient_evaluation("trend")
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(previous_evaluation,),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(current_evaluation,),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == ("role_input_insufficient",)


def test_compare_runs_does_not_infer_exit_when_role_evaluation_is_missing():
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == ("role_input_insufficient",)


def test_compare_runs_role_insufficiency_does_not_block_complete_role():
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(
            _evaluation("trend", (("A", 1),)),
            _evaluation("core", (("A", 1),)),
        ),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(
            _insufficient_evaluation("trend"),
            _evaluation("core", (("A", 1),)),
        ),
    )

    changes = compare_runs(current, previous)

    assert [
        (
            change.role,
            change.comparison_status,
            change.change_type,
            change.reasons,
        )
        for change in changes
    ] == [
        ("core", "comparable", "unchanged", ()),
        ("trend", "not_comparable", None, ("role_input_insufficient",)),
    ]


def test_compare_runs_allows_daily_market_source_version_change():
    previous_context = _context(trade_date=SESSIONS[-2])
    current_context = _context()
    changed_market = current_context.market.model_copy(
        update={"source_version": "daily-s2"}
    )
    current_context = RoleContext(
        market=changed_market,
        topic=current_context.topic,
        rule_version=current_context.rule_version,
    )
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )

    assert compare_runs(current, previous)[0].comparison_status == "comparable"


@pytest.mark.parametrize(
    ("previous_pool_ids", "current_pool_ids"),
    (
        (("A",), ("A", "B")),
        (("A", "B"), ("A",)),
    ),
)
def test_compare_runs_rejects_changed_declared_market_pool_members(
    previous_pool_ids,
    current_pool_ids,
):
    previous_context = _context(trade_date=SESSIONS[-2])
    current_context = _context()
    previous_context = previous_context.model_copy(
        update={
            "market": previous_context.market.model_copy(
                update={"expected_security_ids": previous_pool_ids}
            )
        }
    )
    current_context = current_context.model_copy(
        update={
            "market": current_context.market.model_copy(
                update={"expected_security_ids": current_pool_ids}
            )
        }
    )
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 2),)),),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == ("calculation_basis_changed",)


def test_compare_runs_allows_declared_market_pool_reordering():
    previous_context = _context(
        trade_date=SESSIONS[-2],
        member_ids=("A", "B"),
        expected_security_ids=("A", "B"),
    )
    current_context = _context(
        member_ids=("A", "B"),
        expected_security_ids=("B", "A"),
    )
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),), expected=2),),
        member_ids=("A", "B"),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),), expected=2),),
        member_ids=("A", "B"),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "comparable"
    assert change.change_type == "unchanged"
    assert change.reasons == ()


@pytest.mark.parametrize(
    ("current_context_changes", "run_changes", "expected_reasons"),
    [
        ({"rule_version": "role-v1"}, {}, ("rule_version_changed",)),
        (
            {"taxonomy_version": "t2"},
            {},
            ("taxonomy_version_changed",),
        ),
        (
            {"provenance": "retrospective"},
            {},
            ("visibility_mode_changed",),
        ),
        (
            {"pool_version": "p2"},
            {},
            ("calculation_basis_changed",),
        ),
        (
            {"market_source_id": "revised-market-source"},
            {},
            ("calculation_basis_changed",),
        ),
        (
            {"adjustment_version": "a2"},
            {},
            ("calculation_basis_changed",),
        ),
        (
            {"calendar_version": "history-c2"},
            {},
            ("calculation_basis_changed",),
        ),
    ],
)
def test_compare_runs_marks_changed_calculation_identity_not_comparable(
    current_context_changes,
    run_changes,
    expected_reasons,
):
    previous_context = _context(trade_date=SESSIONS[-2])
    current_context = _context(**current_context_changes)
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 2),)),),
        **run_changes,
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == expected_reasons


@pytest.mark.parametrize(
    "current_context_changes",
    (
        {"exchange": "OTHER"},
        {"sessions": (date(2026, 8, 1), *SESSIONS[1:])},
    ),
)
def test_compare_runs_rejects_changed_exchange_or_calendar_sessions(
    current_context_changes,
):
    previous_context = _context(trade_date=SESSIONS[-2])
    current_context = _context(**current_context_changes)
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 2),)),),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == ("calculation_basis_changed",)


def test_compare_runs_marks_same_day_topic_revision_not_comparable():
    previous_context = _context(topic_source_version="s1")
    current_context = _context(topic_source_version="s2")
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 2),)),),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.change_type is None
    assert change.reasons == (
        "not_previous_comparable_session",
        "topic_revision_changed",
    )


@pytest.mark.parametrize(
    ("previous_date", "current_date"),
    (
        (SESSIONS[-3], SESSIONS[-1]),
        (SESSIONS[-2], date(2026, 9, 9)),
        (SESSIONS[-1], SESSIONS[0]),
    ),
)
def test_compare_runs_requires_strict_previous_frozen_calendar_session(
    previous_date,
    current_date,
):
    previous = _run(
        "previous",
        _context(trade_date=previous_date),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(trade_date=current_date),
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert "not_previous_comparable_session" in change.reasons


def test_compare_runs_records_current_declared_predecessor_on_mismatch():
    previous = _run(
        "actual-previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(),
        previous_id=None,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "not_comparable"
    assert change.previous_comparable_run_id is None
    assert change.reasons == ("previous_comparable_run_mismatch",)


@pytest.mark.parametrize("previous_id", (None, "current"))
def test_role_change_requires_distinct_previous_run_when_comparable(previous_id):
    with pytest.raises(ValidationError, match="previous_comparable_run_id"):
        RoleChange(
            current_run_id="current",
            previous_comparable_run_id=previous_id,
            topic_id=TOPIC,
            security_id="A",
            role="trend",
            comparison_status="comparable",
            change_type="unchanged",
            membership_changed=False,
        )


def test_role_change_rejects_not_comparable_without_reasons():
    with pytest.raises(ValidationError, match="reasons"):
        RoleChange(
            current_run_id="current",
            previous_comparable_run_id="previous",
            topic_id=TOPIC,
            security_id="A",
            role="trend",
            comparison_status="not_comparable",
            change_type=None,
            membership_changed=False,
        )


@pytest.mark.parametrize(
    ("side", "changes"),
    (
        ("current", {"topic_id": "forged-topic"}),
        ("previous", {"active_run_count": 2}),
    ),
)
def test_compare_runs_revalidates_both_input_projections(side, changes):
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    if side == "current":
        current = current.model_copy(update=changes)
    else:
        previous = previous.model_copy(update=changes)

    with pytest.raises(ValidationError):
        compare_runs(current, previous)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("topic_source_id", "forged-source"),
        ("topic_source_version", "forged-source-version"),
        ("taxonomy_version", "forged-taxonomy"),
        ("membership_version", "forged-membership"),
        ("visibility_mode", "retrospective"),
        ("topic_id", "missing-topic"),
    ],
)
def test_role_run_rejects_identity_projection_forged_against_context(
    field,
    forged_value,
):
    context = _context()
    valid = _run("current", context)
    values = valid.model_dump()
    values[field] = forged_value

    with pytest.raises(ValidationError):
        RoleRunEvidence.model_validate(values)


def test_role_run_rejects_stale_projection_after_context_revision():
    context = _context()
    valid = _run("current", context)
    values = valid.model_dump()
    values["context"]["topic"]["membership_version"] = "m2"

    with pytest.raises(ValidationError, match="membership_version"):
        RoleRunEvidence.model_validate(values)


def test_role_run_rejects_forged_market_benchmark_definition():
    values = _run("current", _context()).model_dump()
    values["market_benchmark_definition"] = "forged-market-benchmark"

    with pytest.raises(ValidationError, match="market_benchmark_definition"):
        RoleRunEvidence.model_validate(values)


def test_role_run_rejects_topic_calendar_exchange_mismatch():
    values = _run("current", _context()).model_dump()
    values["context"]["topic"]["calendar"]["exchange"] = "OTHER"

    with pytest.raises(ValidationError, match="calendar identity"):
        RoleRunEvidence.model_validate(values)


def test_role_run_requires_shared_market_and_topic_calendar_version():
    values = _run("current", _context()).model_dump()
    values["context"]["topic"]["calendar"]["version"] = "history-c2"
    values["context"]["topic"]["calendar_version"] = "history-c2"

    with pytest.raises(ValidationError, match="calendar identity"):
        RoleRunEvidence.model_validate(values)


def test_compare_runs_rejects_forged_internal_calendar_version():
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    forged_topic = current.context.topic.model_copy(
        update={
            "calendar": current.context.topic.calendar.model_copy(
                update={"version": "history-c2"}
            ),
            "calendar_version": "history-c2",
        }
    )
    forged_context = current.context.model_copy(update={"topic": forged_topic})
    forged_current = current.model_copy(update={"context": forged_context})

    with pytest.raises(ValidationError, match="calendar identity"):
        compare_runs(forged_current, previous)


def test_role_run_requires_exactly_one_visible_effective_topic_definition():
    context = _context()
    valid = _run("current", context)
    values = valid.model_dump()
    duplicate = dict(values["context"]["topic"]["topics"][0])
    duplicate["name"] = "Duplicate Topic"
    values["context"]["topic"]["topics"] = (
        *values["context"]["topic"]["topics"],
        duplicate,
    )

    with pytest.raises(ValidationError, match="exactly one"):
        RoleRunEvidence.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("verification_status", None),
        ("verification_status", "failed"),
        ("activation_event_id", ""),
    ),
)
def test_role_run_requires_verified_activated_projection(field, value):
    values = _run("current", _context()).model_dump()
    if value is None:
        del values[field]
    else:
        values[field] = value

    with pytest.raises(ValidationError):
        RoleRunEvidence.model_validate(values)


def test_role_run_allows_materialized_unverified_inactive_projection():
    run = _run(
        "current",
        _context(),
        verification_status="pending",
        activation_event_id=None,
        active_run_count=0,
    )

    assert run.verification_status == "pending"
    assert run.activation_event_id is None
    assert run.active_run_count == 0


def test_compare_runs_allows_inactive_current_against_active_predecessor():
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
        verification_status="pending",
        activation_event_id=None,
        active_run_count=0,
    )

    change = compare_runs(current, previous)[0]

    assert change.comparison_status == "comparable"
    assert change.change_type == "unchanged"


def test_compare_runs_rejects_inactive_previous_as_comparable():
    previous = _run(
        "previous",
        _context(trade_date=SESSIONS[-2]),
        evaluations=(_evaluation("trend", (("A", 1),)),),
        verification_status="pending",
        activation_event_id=None,
        active_run_count=0,
    )
    current = _run(
        "current",
        _context(),
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1),)),),
        verification_status="pending",
        activation_event_id=None,
        active_run_count=0,
    )

    with pytest.raises(ValueError, match="previous run must be passed and active"):
        compare_runs(current, previous)


@pytest.mark.parametrize(
    ("verification_status", "activation_event_id", "active_run_count"),
    (
        ("pending", "activation-current", 1),
        ("failed", "activation-current", 1),
        ("passed", None, 1),
        ("passed", "activation-current", 0),
    ),
)
def test_role_run_rejects_inconsistent_lifecycle_projection(
    verification_status,
    activation_event_id,
    active_run_count,
):
    values = _run("current", _context()).model_dump()
    values.update(
        verification_status=verification_status,
        activation_event_id=activation_event_id,
        active_run_count=active_run_count,
    )

    with pytest.raises(ValidationError, match="activation"):
        RoleRunEvidence.model_validate(values)


def test_role_run_rejects_previous_comparable_run_self_reference():
    values = _run("current", _context()).model_dump()
    values["previous_comparable_run_id"] = values["role_run_id"]

    with pytest.raises(
        ValidationError,
        match="previous_comparable_run_id must differ from role_run_id",
    ):
        RoleRunEvidence.model_validate(values)


def test_passed_role_run_rejects_evaluation_expected_below_visible_members():
    evaluation = RoleEvaluation(
        topic_status=RoleTopicStatus(
            topic_id=TOPIC,
            role="trend",
            capability="ready",
            status="insufficient",
            expected=0,
            valid=0,
            legal_excluded=0,
            missing=0,
        )
    )

    with pytest.raises(
        ValidationError,
        match="evaluation expected must not be less than member_ids",
    ):
        _run("current", _context(), evaluations=(evaluation,))


def test_passed_role_run_allows_evaluation_expected_above_visible_members():
    evaluation = _insufficient_evaluation("trend").model_copy(
        update={
            "topic_status": RoleTopicStatus(
                topic_id=TOPIC,
                role="trend",
                capability="ready",
                status="insufficient",
                expected=2,
                valid=0,
                legal_excluded=0,
                missing=2,
            )
        }
    )

    run = _run("current", _context(), evaluations=(evaluation,))

    assert run.evaluations[0].topic_status.expected == 2


def test_role_run_rejects_candidate_outside_topic_members():
    with pytest.raises(ValidationError, match="member_ids"):
        _run(
            "current",
            _context(),
            evaluations=(_evaluation("trend", (("OUTSIDE", 1),)),),
        )


def test_role_run_rejects_duplicate_role_evaluations():
    evaluation = _evaluation("trend", (("A", 1),))
    values = _run(
        "current",
        _context(),
        evaluations=(evaluation,),
    ).model_dump()
    values["evaluations"] = (evaluation.model_dump(), evaluation.model_dump())

    with pytest.raises(ValidationError, match="role"):
        RoleRunEvidence.model_validate(values)


def _validation_fixture(
    *,
    new_end: str,
    old_end: str,
    member_ends: dict[str, str],
) -> tuple[PendingRelayValidation, tuple[RoleRunEvidence, ...]]:
    observation_date = FUTURE_SESSIONS[-6]
    validation_days = FUTURE_SESSIONS[-5:]
    all_ids = tuple(sorted(set(member_ends) | {"OLD", "NEW"}))
    member_ids = all_ids
    starts = {security_id: Decimal("100") for security_id in all_ids}
    ends = {
        **{security_id: Decimal(value) for security_id, value in member_ends.items()},
        "OLD": Decimal(old_end),
        "NEW": Decimal(new_end),
    }
    runs = []
    for run_index, run_day in enumerate(validation_days, start=1):
        visible_days = (observation_date, *validation_days[:run_index])
        bars = tuple(
            bar(
                security_id=security_id,
                trade_date=day,
                close=(
                    starts[security_id]
                    if day == observation_date
                    else (
                        ends[security_id]
                        if day == validation_days[-1]
                        else starts[security_id]
                    )
                ),
                available_at=datetime(day.year, day.month, day.day, 16, tzinfo=UTC),
            )
            for security_id in all_ids
            for day in visible_days
        )
        context = _context(
            trade_date=run_day,
            sessions=FUTURE_SESSIONS,
            member_ids=member_ids,
            bars=bars,
            states=tuple(
                state(
                    security_id=security_id,
                    effective_from=FUTURE_SESSIONS[0],
                    available_at=datetime(2026, 1, 1, tzinfo=UTC),
                    listing_date=FUTURE_SESSIONS[0],
                )
                for security_id in all_ids
            ),
        )
        runs.append(
            _run(
                f"run-t{run_index}",
                context,
                member_ids=member_ids,
            )
        )
    observation_states = {
        security_state.security_id: security_state
        for security_state in runs[0].context.market.security_states
    }
    observation_bars = tuple(
        bar(
            security_id=security_id,
            trade_date=observation_date,
            close=starts[security_id],
            available_at=datetime(
                observation_date.year,
                observation_date.month,
                observation_date.day,
                16,
                tzinfo=UTC,
            ),
        )
        for security_id in all_ids
    )
    observation_context = _context(
        trade_date=observation_date,
        sessions=FUTURE_SESSIONS,
        member_ids=member_ids,
        bars=observation_bars,
        states=tuple(observation_states.values()),
    )
    observation_run = _run(
        "run-t0",
        observation_context,
        member_ids=member_ids,
    )
    pending = PendingRelayValidation(
        pending_result_id="pending-1",
        observation_run_id="run-t0",
        observation_run=observation_run,
        observation_date=observation_date,
        topic_id=TOPIC,
        old_security_id="OLD",
        new_security_id="NEW",
        observation_member_ids=member_ids,
        observation_eligibility=tuple(
            RelayObservationEligibility(
                security_id=security_id,
                eligible=True,
                eligibility_reason=None,
                security_type=observation_states[security_id].security_type,
                board_category=observation_states[security_id].board_category,
                listing_date=observation_states[security_id].listing_date,
                suspended=observation_states[security_id].suspended,
                is_st=observation_states[security_id].is_st,
                is_delisting=observation_states[security_id].is_delisting,
                exchange=observation_states[security_id].exchange,
                source_id=observation_states[security_id].source_id,
                pool_version=observation_states[security_id].pool_version,
            )
            for security_id in member_ids
        ),
        baseline_member_ids=member_ids,
        baseline_prices=tuple(
            RelayBaselinePrice(
                security_id=security_id,
                adjusted_price_raw=starts[security_id],
                observation_suspended=False,
            )
            for security_id in member_ids
        ),
        membership_version="m1",
        market_source_id="synthetic",
        adjustment_version="a1",
        pool_version="p1",
        expected_security_ids=all_ids,
        validation_sessions=validation_days,
        calendar_sessions=FUTURE_SESSIONS,
        rule_version="role-test-v1",
        visibility_mode="contemporaneous",
        market_benchmark_definition=("eligible_declared_market_pool_return_median"),
        topic_source_id="synthetic",
        topic_source_version="s1",
        taxonomy_version="t1",
        exchange="TEST",
        calendar_version="history-c1",
        operation_at=CUTOFF,
        current_grants=(grant(),),
    )
    return pending, tuple(runs)


def test_pending_relay_rejects_observation_run_id_mismatch():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["observation_run_id"] = "different-run"

    with pytest.raises(ValidationError, match="observation_run_id"):
        PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    "identity_change",
    (
        {"security_type": "fund"},
        {"suspended": True},
    ),
)
def test_pending_relay_rejects_caller_shrinking_eligible_denominator(
    identity_change,
):
    pending, runs = _validation_fixture(
        new_end="123",
        old_end="110",
        member_ends={"A": "120", "B": "125", "INELIGIBLE": "200"},
    )
    full_validation = validate_relay(pending, runs)[0]
    eligible_member_ids = tuple(
        security_id
        for security_id in pending.baseline_member_ids
        if security_id != "INELIGIBLE"
    )
    eligible_prices = tuple(
        price for price in pending.baseline_prices if price.security_id != "INELIGIBLE"
    )
    values = pending.model_dump()
    values.update(
        observation_eligibility=tuple(
            (
                member.model_copy(
                    update={
                        "eligible": False,
                        "eligibility_reason": "ineligible_security_state",
                        **identity_change,
                    }
                )
                if member.security_id == "INELIGIBLE"
                else member
            )
            for member in pending.observation_eligibility
        ),
        baseline_member_ids=eligible_member_ids,
        baseline_prices=eligible_prices,
    )

    assert full_validation.outcome == "weakened"
    forged = PendingRelayValidation.model_validate(values)
    validation = validate_relay(forged, runs)[0]

    assert validation.outcome == "unavailable"
    assert "INELIGIBLE: observation_identity_mismatch" in validation.reasons


def test_pending_relay_rejects_caller_forging_observation_adjusted_price():
    pending, runs = _validation_fixture(
        new_end="123",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["baseline_prices"] = tuple(
        (
            price.model_copy(update={"adjusted_price_raw": Decimal("110")})
            if price.security_id == "NEW"
            else price
        )
        for price in pending.baseline_prices
    )

    forged = PendingRelayValidation.model_validate(values)
    validation = validate_relay(forged, runs)[0]

    assert validation.outcome == "unavailable"
    assert "NEW: observation_adjusted_price_mismatch" in validation.reasons


def test_pending_relay_requires_five_strict_future_validation_sessions():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )

    invalid_sessions = (
        pending.validation_sessions[:4],
        (
            pending.validation_sessions[0],
            pending.validation_sessions[2],
            pending.validation_sessions[1],
            *pending.validation_sessions[3:],
        ),
        (
            pending.observation_date,
            *pending.validation_sessions[1:],
        ),
    )

    for sessions in invalid_sessions:
        values = pending.model_dump()
        values["validation_sessions"] = sessions
        with pytest.raises(ValidationError, match="validation_sessions"):
            PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    "calendar_sessions",
    (
        tuple(day for day in FUTURE_SESSIONS if day != FUTURE_SESSIONS[-6]),
        FUTURE_SESSIONS[:-1],
        (
            *FUTURE_SESSIONS[:-2],
            FUTURE_SESSIONS[-1],
            FUTURE_SESSIONS[-2],
        ),
    ),
)
def test_pending_relay_rejects_invalid_frozen_calendar(calendar_sessions):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["calendar_sessions"] = calendar_sessions

    with pytest.raises(ValidationError, match="calendar_sessions"):
        PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    "validation_sessions",
    (
        (*FUTURE_SESSIONS[-4:], date(2026, 9, 16)),
        tuple(date(2026, 10, day) for day in range(1, 6)),
    ),
)
def test_pending_relay_requires_next_five_frozen_calendar_sessions(
    validation_sessions,
):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["validation_sessions"] = validation_sessions

    with pytest.raises(ValidationError, match="validation_sessions"):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_accepts_next_five_frozen_calendar_sessions():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )

    assert PendingRelayValidation.model_validate(pending.model_dump()) == pending


def test_pending_relay_rejects_forged_market_benchmark_definition():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["market_benchmark_definition"] = "forged-market-benchmark"

    with pytest.raises(ValidationError, match="market_benchmark_definition"):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_requires_observation_eligibility():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    del values["observation_eligibility"]

    with pytest.raises(ValidationError, match="observation_eligibility"):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_requires_baseline_members_to_be_observation_members():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["observation_member_ids"] = tuple(
        security_id
        for security_id in pending.observation_member_ids
        if security_id != "A"
    )

    with pytest.raises(
        ValidationError,
        match="observation_member_ids must match observation_run member_ids",
    ):
        PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    "projection_change",
    (
        lambda members: (),
        lambda members: (members[0], members[0], *members[2:]),
        lambda members: tuple(
            member for member in members if member.security_id != "A"
        ),
    ),
)
def test_pending_relay_rejects_incomplete_or_duplicate_observation_eligibility(
    projection_change,
):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["observation_eligibility"] = projection_change(
        pending.observation_eligibility
    )

    with pytest.raises(
        ValidationError,
        match="observation eligibility must cover observation_member_ids",
    ):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_eligible_member_order_does_not_change_semantics():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["observation_eligibility"] = tuple(reversed(pending.observation_eligibility))

    validated = PendingRelayValidation.model_validate(values)

    assert {
        member.security_id
        for member in validated.observation_eligibility
        if member.eligible
    } == set(pending.baseline_member_ids)


@pytest.mark.parametrize(
    "missing_field",
    (
        "security_type",
        "board_category",
        "listing_date",
        "suspended",
        "is_st",
        "is_delisting",
    ),
)
def test_relay_observation_eligibility_accepts_missing_archived_identity(
    missing_field,
):
    values = {
        "security_id": "A",
        "eligible": False,
        "eligibility_reason": "missing_identity_evidence",
        "security_type": "common_stock",
        "board_category": "synthetic_test",
        "listing_date": FUTURE_SESSIONS[0],
        "suspended": False,
        "is_st": False,
        "is_delisting": False,
        "exchange": "TEST",
        "source_id": "synthetic",
        "pool_version": "p1",
        missing_field: None,
    }

    eligibility = RelayObservationEligibility(**values)

    assert eligibility.eligible is False
    assert eligibility.eligibility_reason == "missing_identity_evidence"


def test_pending_relay_excludes_missing_archived_identity_from_baseline():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    missing_fields = {
        "security_type": None,
        "board_category": None,
        "listing_date": None,
        "suspended": None,
        "is_st": None,
        "is_delisting": None,
    }
    observation_run = pending.observation_run.model_copy(
        update={
            "context": pending.observation_run.context.model_copy(
                update={
                    "market": pending.observation_run.context.market.model_copy(
                        update={
                            "security_states": tuple(
                                security_state.model_copy(update=missing_fields)
                                if security_state.security_id == "A"
                                else security_state
                                for security_state in (
                                    pending.observation_run.context.market.security_states
                                )
                            )
                        }
                    )
                }
            )
        }
    )
    values = pending.model_dump()
    values.update(
        observation_run=observation_run,
        observation_eligibility=tuple(
            (
                member.model_copy(
                    update={
                        **missing_fields,
                        "eligible": False,
                        "eligibility_reason": "missing_identity_evidence",
                    }
                )
                if member.security_id == "A"
                else member
            )
            for member in pending.observation_eligibility
        ),
        baseline_member_ids=tuple(
            security_id
            for security_id in pending.baseline_member_ids
            if security_id != "A"
        ),
        baseline_prices=tuple(
            price for price in pending.baseline_prices if price.security_id != "A"
        ),
    )

    archived = PendingRelayValidation.model_validate(values)
    validation = validate_relay(archived, runs)[0]

    assert "A" not in archived.baseline_member_ids
    assert validation.outcome == "supported"


@pytest.mark.parametrize("observation_suspended", (True, None))
def test_relay_baseline_rejects_member_not_explicitly_trading_at_observation(
    observation_suspended,
):
    with pytest.raises(ValidationError, match="observation_suspended"):
        RelayBaselinePrice(
            security_id="A",
            adjusted_price_raw=Decimal("100"),
            observation_suspended=observation_suspended,
        )


def test_relay_baseline_accepts_explicitly_trading_observation():
    baseline = RelayBaselinePrice(
        security_id="A",
        adjusted_price_raw=Decimal("100"),
        observation_suspended=False,
    )

    assert baseline.observation_suspended is False


def test_validate_relay_revalidates_observation_suspension_before_supported():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    forged_prices = tuple(
        price.model_copy(update={"observation_suspended": True})
        if price.security_id == "NEW"
        else price
        for price in pending.baseline_prices
    )
    forged_pending = pending.model_copy(update={"baseline_prices": forged_prices})

    with pytest.raises(ValidationError, match="observation_suspended"):
        validate_relay(forged_pending, runs)


@pytest.mark.parametrize(
    "field",
    ("membership_version", "validation_sessions", "calendar_sessions"),
)
def test_pending_relay_requires_frozen_membership_and_sessions(field):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    del values[field]

    with pytest.raises(ValidationError):
        PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("market_source_id", None),
        ("adjustment_version", ""),
        ("pool_version", None),
        ("expected_security_ids", ()),
        ("expected_security_ids", ("A", "A")),
        ("expected_security_ids", ("B", "A")),
    ),
)
def test_pending_relay_requires_canonical_frozen_market_identity(field, value):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    if value is None:
        del values[field]
    else:
        values[field] = value

    with pytest.raises(ValidationError):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_canonicalizes_observation_market_pool_identity():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    observation_run = pending.observation_run
    values["observation_run"] = observation_run.model_copy(
        update={
            "context": observation_run.context.model_copy(
                update={
                    "market": observation_run.context.market.model_copy(
                        update={
                            "expected_security_ids": tuple(
                                reversed(pending.expected_security_ids)
                            )
                        }
                    )
                }
            )
        }
    )

    validated = PendingRelayValidation.model_validate(values)

    assert validated.expected_security_ids == tuple(
        sorted(set(pending.expected_security_ids))
    )


def test_pending_relay_baseline_members_must_belong_to_frozen_market_pool():
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["expected_security_ids"] = tuple(
        security_id
        for security_id in pending.expected_security_ids
        if security_id != pending.new_security_id
    )

    with pytest.raises(ValidationError, match="expected_security_ids"):
        PendingRelayValidation.model_validate(values)


@pytest.mark.parametrize(
    ("market_field", "changed_value", "expected_reason"),
    (
        ("source_id", "other-source", "market_source_changed"),
        ("adjustment_version", "a2", "adjustment_version_changed"),
        ("pool_version", "p2", "pool_version_changed"),
        ("expected_security_ids", ("A",), "market_pool_changed"),
    ),
)
def test_validate_relay_rejects_changed_frozen_market_identity(
    market_field,
    changed_value,
    expected_reason,
):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={market_field: changed_value}
            )
        }
    )
    changed_run = target.model_copy(update={"context": changed_context})

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    if market_field == "source_id":
        assert validation.reasons == ("authorization_unavailable",)
    else:
        assert validation.reasons == ("topic_incomparable", expected_reason)


@pytest.mark.parametrize(
    ("state_field", "changed_value"),
    (
        ("security_type", "fund"),
        ("board_category", "sh_main"),
        ("listing_date", FUTURE_SESSIONS[1]),
        ("is_st", True),
        ("is_delisting", True),
        ("exchange", "OTHER"),
        ("source_id", "other-source"),
        ("pool_version", "other-pool"),
    ),
)
def test_validate_relay_rejects_changed_observation_security_identity(
    state_field,
    changed_value,
):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "security_states": tuple(
                        security_state.model_copy(update={state_field: changed_value})
                        if security_state.security_id == "A"
                        else security_state
                        for security_state in target.context.market.security_states
                    )
                }
            )
        }
    )
    changed_run = target.model_copy(update={"context": changed_context})

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == (f"A: identity_changed:{state_field}",)


def test_validate_relay_allows_declared_market_pool_reordering():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "expected_security_ids": tuple(
                        reversed(target.context.market.expected_security_ids)
                    )
                }
            )
        }
    )
    changed_run = target.model_copy(update={"context": changed_context})

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "supported"


def test_validate_relay_allows_daily_market_source_version_change():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "source_version": "daily-s2",
                    "bars": tuple(
                        daily_bar.model_copy(update={"source_version": "daily-s2"})
                        for daily_bar in target.context.market.bars
                    ),
                    "security_states": tuple(
                        security_state.model_copy(update={"source_version": "daily-s2"})
                        for security_state in target.context.market.security_states
                    ),
                }
            )
        }
    )
    changed_run = target.model_copy(update={"context": changed_context})

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "supported"


@pytest.mark.parametrize(
    "changes",
    (
        {"verification_status": "failed"},
        {"activation_event_id": ""},
        {"active_run_count": 2},
    ),
)
def test_validate_relay_rejects_invalid_future_run_projection(changes):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2].model_copy(update=changes)

    with pytest.raises(ValidationError):
        validate_relay(pending, (*runs[:2], target, *runs[3:]))


def test_pending_relay_rejects_duplicate_validation_input_run_ids():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    validation = validate_relay(pending, runs)[0]
    values = validation.model_dump()
    values["input_run_ids"] = ("run-t1",) * 5

    with pytest.raises(ValidationError, match="input_run_ids"):
        RoleValidation.model_validate(values)


def test_role_validation_rejects_zero_length_window():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = validate_relay(pending, runs)[0].model_dump()
    values["window_start"] = values["window_end"]

    with pytest.raises(ValidationError, match="validation endpoints"):
        RoleValidation.model_validate(values)


@pytest.mark.parametrize(
    ("old_return", "new_return", "topic_median", "incorrect_outcome"),
    (
        ("0.10", "0.05", "0.00", "supported"),
        ("0.10", "0.05", "0.00", "weakened"),
        ("0.10", "0.10", "0.00", "supported"),
        ("0.10", "0.10", "0.00", "weakened"),
        ("0.10", "0.20", "0.15", "refuted"),
        ("0.10", "0.20", "0.15", "weakened"),
        ("0.10", "0.20", "0.20", "refuted"),
        ("0.10", "0.20", "0.20", "supported"),
        ("0.10", "0.20", "0.25", "refuted"),
        ("0.10", "0.20", "0.25", "supported"),
    ),
)
def test_role_validation_rejects_outcome_inconsistent_with_raw_returns(
    old_return,
    new_return,
    topic_median,
    incorrect_outcome,
):
    expected_outcome = (
        "refuted"
        if Decimal(new_return) <= Decimal(old_return)
        else (
            "supported" if Decimal(new_return) > Decimal(topic_median) else "weakened"
        )
    )
    validation = RoleValidation(
        pending_result_id="pending-1",
        observation_run_id="run-t0",
        topic_id=TOPIC,
        outcome=expected_outcome,
        window_start=FUTURE_SESSIONS[-5],
        window_end=FUTURE_SESSIONS[-1],
        return_start=FUTURE_SESSIONS[-6],
        return_end=FUTURE_SESSIONS[-1],
        old_security_id="OLD",
        new_security_id="NEW",
        old_return_raw=Decimal(old_return),
        new_return_raw=Decimal(new_return),
        topic_median_raw=Decimal(topic_median),
        input_run_ids=tuple(f"run-t{i}" for i in range(1, 6)),
        reasons=(f"outcome:{expected_outcome}",),
    )
    values = validation.model_dump()
    values["outcome"] = incorrect_outcome
    values["reasons"] = (f"outcome:{incorrect_outcome}",)

    with pytest.raises(ValidationError, match="outcome"):
        RoleValidation.model_validate(values)


def test_role_validation_rejects_available_raw_returns_forged_as_unavailable():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = validate_relay(pending, runs)[0].model_dump()
    values["outcome"] = "unavailable"

    with pytest.raises(ValidationError, match="unavailable"):
        RoleValidation.model_validate(values)


@pytest.mark.parametrize(
    "reasons",
    (
        (),
        ("outcome:weakened",),
        ("outcome:supported", "outcome:supported"),
        ("outcome:supported", "outcome:refuted"),
        ("outcome:supported", "outcome:forged"),
    ),
)
def test_role_validation_rejects_invalid_available_outcome_reasons(reasons):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = validate_relay(pending, runs)[0].model_dump()
    values["reasons"] = reasons

    with pytest.raises(ValidationError, match="outcome reason"):
        RoleValidation.model_validate(values)


def test_role_validation_allows_one_matching_outcome_reason_with_other_details():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = validate_relay(pending, runs)[0].model_dump()
    values["reasons"] = ("outcome:supported", "calculation_complete")

    validation = RoleValidation.model_validate(values)

    assert validation.reasons == ("outcome:supported", "calculation_complete")


@pytest.mark.parametrize(
    "reasons",
    (
        (),
        ("outcome:supported",),
        ("outcome:weakened",),
        ("outcome:refuted",),
        ("outcome:unavailable",),
        ("outcome:forged",),
    ),
)
def test_role_validation_rejects_unavailable_without_failure_reason(reasons):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "bars": tuple(
                        daily_bar
                        for daily_bar in target.context.market.bars
                        if not (
                            daily_bar.security_id == "B"
                            and daily_bar.trade_date == FUTURE_SESSIONS[-1]
                        )
                    )
                }
            )
        }
    )
    unavailable = validate_relay(
        pending,
        (*runs[:-1], target.model_copy(update={"context": changed_context})),
    )[0]
    values = unavailable.model_dump()
    values["reasons"] = reasons

    with pytest.raises(ValidationError, match="unavailable"):
        RoleValidation.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pending_result_id", "other-pending"),
        ("observation_run_id", "other-observation"),
        ("topic_id", "other-topic"),
        ("old_security_id", "A"),
        ("new_security_id", "B"),
        ("return_start", FUTURE_SESSIONS[-7]),
        ("window_start", FUTURE_SESSIONS[-4]),
        ("window_end", FUTURE_SESSIONS[-2]),
        ("return_end", FUTURE_SESSIONS[-2]),
        ("input_run_ids", ("run-t1",) * 5),
    ],
)
def test_pending_relay_rejects_mismatched_current_validation(field, value):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    validation_values = validate_relay(pending, runs)[0].model_dump()
    validation_values[field] = value
    if field == "window_start":
        validation_values["return_start"] = FUTURE_SESSIONS[-5]
    if field == "window_end":
        validation_values["return_end"] = value
    if field == "return_end":
        validation_values["window_end"] = value
    values = pending.model_dump()
    values["current_validation"] = validation_values

    with pytest.raises(ValidationError, match="current_validation"):
        PendingRelayValidation.model_validate(values)


def test_pending_relay_rejects_available_validation_using_observation_run_id():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    validation_values = validate_relay(pending, runs)[0].model_dump()
    validation_values["input_run_ids"] = (
        pending.observation_run_id,
        *(run.role_run_id for run in runs[:4]),
    )
    values = pending.model_dump()
    values["current_validation"] = validation_values

    with pytest.raises(ValidationError, match="current_validation"):
        PendingRelayValidation.model_validate(values)


def test_validate_relay_revalidates_existing_validation_before_idempotent_return():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    validation = validate_relay(pending, runs)[0].model_copy(
        update={"observation_run_id": "other-observation"}
    )
    forged_pending = pending.model_copy(update={"current_validation": validation})

    with pytest.raises(ValidationError, match="current_validation"):
        validate_relay(forged_pending, runs)


def test_validate_relay_rejects_forged_current_validation_outcome():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    validation = validate_relay(pending, runs)[0].model_copy(
        update={"outcome": "refuted"}
    )
    forged_pending = pending.model_copy(update={"current_validation": validation})

    with pytest.raises(ValidationError, match="outcome"):
        validate_relay(forged_pending, runs)


def test_validate_relay_replays_existing_observation_unavailable_validation():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["baseline_prices"] = tuple(
        (
            price.model_copy(update={"adjusted_price_raw": Decimal("110")})
            if price.security_id == "NEW"
            else price
        )
        for price in pending.baseline_prices
    )
    mismatched = PendingRelayValidation.model_validate(values)
    existing = validate_relay(mismatched, runs)[0]
    persisted = mismatched.model_copy(update={"current_validation": existing})

    assert validate_relay(persisted, runs) == (existing,)


def test_validate_relay_existing_validation_ignores_invalid_future_runs():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    existing = validate_relay(pending, runs)[0]
    persisted = pending.model_copy(update={"current_validation": existing})
    forged_future = runs[-1].model_copy(
        update={
            "role_run_id": "unrelated-future",
            "active_run_count": 2,
        }
    )

    assert validate_relay(persisted, (*runs, forged_future)) == (existing,)


def test_validate_relay_available_replay_requires_all_five_future_runs():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    existing = validate_relay(pending, runs)[0]
    persisted = pending.model_copy(update={"current_validation": existing})

    validation = validate_relay(persisted, runs[:-1])[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)


@pytest.mark.parametrize(
    ("new_end", "old_end", "member_ends", "outcome"),
    [
        ("130", "110", {"A": "120", "B": "125"}, "supported"),
        ("120", "110", {"A": "125", "B": "130"}, "weakened"),
        ("110", "115", {"A": "120", "B": "125"}, "refuted"),
    ],
)
def test_validate_relay_matures_at_t_plus_5_with_exact_outcome(
    new_end,
    old_end,
    member_ends,
    outcome,
):
    pending, runs = _validation_fixture(
        new_end=new_end,
        old_end=old_end,
        member_ends=member_ends,
    )

    result = validate_relay(pending, runs)

    assert len(result) == 1
    validation = result[0]
    assert validation.outcome == outcome
    assert validation.window_start == FUTURE_SESSIONS[-5]
    assert validation.window_end == FUTURE_SESSIONS[-1]
    assert validation.return_start == FUTURE_SESSIONS[-6]
    assert validation.return_end == FUTURE_SESSIONS[-1]
    assert validation.input_run_ids == tuple(f"run-t{i}" for i in range(1, 6))
    assert validation.old_return_raw == Decimal(old_end) / Decimal("100") - 1
    assert validation.new_return_raw == Decimal(new_end) / Decimal("100") - 1


def test_validate_relay_ignores_other_topic_run_on_same_day():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    other_context = _context(
        trade_date=target.trade_date,
        sessions=FUTURE_SESSIONS,
        topic_id="topic-b",
        member_ids=target.member_ids,
        bars=target.context.market.bars,
        states=target.context.market.security_states,
    )
    other_topic = _run(
        "other-topic-run",
        other_context,
        topic_id="topic-b",
        member_ids=target.member_ids,
    )

    validation = validate_relay(pending, (*runs, other_topic))[0]

    assert validation.outcome == "supported"
    assert validation.input_run_ids == tuple(f"run-t{i}" for i in range(1, 6))


def test_validate_relay_ignores_same_topic_run_from_other_exchange():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    foreign_context = _context(
        trade_date=target.trade_date,
        sessions=FUTURE_SESSIONS,
        member_ids=target.member_ids,
        bars=target.context.market.bars,
        states=tuple(
            security_state.model_copy(update={"exchange": "OTHER"})
            for security_state in target.context.market.security_states
        ),
        expected_security_ids=target.context.market.expected_security_ids,
        exchange="OTHER",
    )
    foreign_run = _run(
        "foreign-exchange-run",
        foreign_context,
        member_ids=target.member_ids,
    )

    validation = validate_relay(pending, (*runs, foreign_run))[0]

    assert validation.outcome == "supported"
    assert validation.input_run_ids == tuple(f"run-t{i}" for i in range(1, 6))


def test_validate_relay_rejects_duplicate_same_topic_active_run_on_same_day():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    duplicate = runs[2].model_copy(update={"role_run_id": "duplicate-topic-run"})

    validation = validate_relay(pending, (*runs, duplicate))[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("multiple_active_topic_runs",)


def test_validate_relay_ignores_extra_retrospective_run_on_same_day():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    retrospective_context = RoleContext(
        market=target.context.market.model_copy(update={"provenance": "retrospective"}),
        topic=target.context.topic,
        rule_version=target.rule_version,
    )
    retrospective = _run(
        "retrospective-topic-run",
        retrospective_context,
        member_ids=target.member_ids,
        visibility_mode="retrospective",
    )

    validation = validate_relay(pending, (*runs, retrospective))[0]

    assert validation.outcome == "supported"
    assert validation.input_run_ids == tuple(f"run-t{i}" for i in range(1, 6))


def test_validate_relay_returns_unavailable_for_duplicate_selected_run_ids():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    duplicate_ids = tuple(
        run.model_copy(update={"role_run_id": "duplicate-run-id"}) for run in runs
    )

    validation = validate_relay(pending, duplicate_ids)[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("duplicate_input_run_id",)


def test_validate_relay_ignores_late_corrected_t_bar_in_future_snapshot():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    first = runs[0]
    corrected_bars = tuple(
        daily_bar.model_copy(update={"close": Decimal("1000")})
        if daily_bar.trade_date == pending.observation_date
        else daily_bar
        for daily_bar in first.context.market.bars
    )
    corrected_context = first.context.model_copy(
        update={
            "market": first.context.market.model_copy(update={"bars": corrected_bars})
        }
    )

    validation = validate_relay(
        pending,
        (first.model_copy(update={"context": corrected_context}), *runs[1:]),
    )[0]

    assert validation.outcome == "supported"
    assert validation.old_return_raw == Decimal("0.1")
    assert validation.new_return_raw == Decimal("0.3")


def test_validate_relay_requires_each_future_daily_price_point():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    target_day = FUTURE_SESSIONS[-3]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "bars": tuple(
                        daily_bar
                        for daily_bar in target.context.market.bars
                        if not (
                            daily_bar.security_id == "B"
                            and daily_bar.trade_date == target_day
                        )
                    )
                }
            )
        }
    )

    validation = validate_relay(
        pending,
        (*runs[:2], target.model_copy(update={"context": changed_context}), *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert "B: missing_price" in validation.reasons


@pytest.mark.parametrize(
    ("state_changes", "expected_reason"),
    (
        ({"exchange": "OTHER"}, "A: identity_changed:exchange"),
        ({"listing_date": None}, "A: identity_changed:listing_date"),
        (
            {"listing_date": FUTURE_SESSIONS[-2]},
            "A: identity_changed:listing_date",
        ),
    ),
)
def test_validate_relay_rejects_invalid_security_state_quality(
    state_changes,
    expected_reason,
):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[0]
    changed_states = tuple(
        security_state.model_copy(update=state_changes)
        if security_state.security_id == "A"
        else security_state
        for security_state in target.context.market.security_states
    )
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={"security_states": changed_states}
            )
        }
    )

    validation = validate_relay(
        pending,
        (target.model_copy(update={"context": changed_context}), *runs[1:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == (expected_reason,)


@pytest.mark.parametrize(
    "bar_changes",
    (
        {"open": Decimal("120"), "high": Decimal("110"), "low": Decimal("90")},
        {
            "limit_status": "limited",
            "limit_date": FUTURE_SESSIONS[-3],
            "limit_up": Decimal("90"),
            "limit_down": Decimal("110"),
        },
    ),
)
def test_validate_relay_rejects_bar_failing_domain_quality(bar_changes):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    target_day = target.trade_date
    changed_bars = tuple(
        daily_bar.model_copy(update=bar_changes)
        if daily_bar.security_id == "A" and daily_bar.trade_date == target_day
        else daily_bar
        for daily_bar in target.context.market.bars
    )
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(update={"bars": changed_bars})
        }
    )

    validation = validate_relay(
        pending,
        (*runs[:2], target.model_copy(update={"context": changed_context}), *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("A: bar_quality_unavailable",)


def test_validate_relay_failure_reasons_ignore_baseline_price_order():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "bars": tuple(
                        daily_bar
                        for daily_bar in target.context.market.bars
                        if not (
                            daily_bar.security_id in {"A", "B"}
                            and daily_bar.trade_date == target.trade_date
                        )
                    )
                }
            )
        }
    )
    changed_runs = (*runs[:-1], target.model_copy(update={"context": changed_context}))
    reversed_pending = pending.model_copy(
        update={"baseline_prices": tuple(reversed(pending.baseline_prices))}
    )

    forward = validate_relay(pending, changed_runs)[0]
    reversed_result = validate_relay(reversed_pending, changed_runs)[0]

    assert forward.reasons == reversed_result.reasons
    assert forward.reasons == ("A: missing_price", "B: missing_price")


@pytest.mark.parametrize("security_id", ("OLD", "NEW", "A"))
@pytest.mark.parametrize("run_index", range(5))
def test_validate_relay_marks_unknown_suspension_status_unavailable(
    security_id,
    run_index,
):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[run_index]
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={
                    "security_states": tuple(
                        security_state.model_copy(update={"suspended": None})
                        if security_state.security_id == security_id
                        else security_state
                        for security_state in target.context.market.security_states
                    )
                }
            )
        }
    )
    changed_runs = (
        *runs[:run_index],
        target.model_copy(update={"context": changed_context}),
        *runs[run_index + 1 :],
    )

    validation = validate_relay(pending, changed_runs)[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == (f"{security_id}: suspension_status_unknown",)


def test_validate_relay_returns_no_record_before_t_plus_5():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )

    assert validate_relay(pending, runs[:-1]) == ()


def test_validate_relay_does_not_materialize_observation_mismatch_before_maturity():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values["baseline_prices"] = tuple(
        (
            price.model_copy(update={"adjusted_price_raw": Decimal("110")})
            if price.security_id == "NEW"
            else price
        )
        for price in pending.baseline_prices
    )
    mismatched = PendingRelayValidation.model_validate(values)

    assert validate_relay(mismatched, runs[:-1]) == ()


def test_validate_relay_uses_all_topics_for_maturity_clock():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    maturity_run = runs[-1]
    other_context = _context(
        trade_date=maturity_run.trade_date,
        sessions=FUTURE_SESSIONS,
        topic_id="topic-b",
        member_ids=maturity_run.member_ids,
        bars=maturity_run.context.market.bars,
        states=maturity_run.context.market.security_states,
        expected_security_ids=maturity_run.context.market.expected_security_ids,
    )
    other_topic = _run(
        "other-topic-t5",
        other_context,
        topic_id="topic-b",
        member_ids=maturity_run.member_ids,
    )

    validation = validate_relay(pending, (*runs[:-1], other_topic))[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("window_incomplete",)


def test_validate_relay_foreign_exchange_does_not_advance_maturity_clock():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    maturity_run = runs[-1]
    foreign_context = _context(
        trade_date=maturity_run.trade_date,
        sessions=FUTURE_SESSIONS,
        topic_id="topic-b",
        member_ids=maturity_run.member_ids,
        bars=maturity_run.context.market.bars,
        states=tuple(
            security_state.model_copy(update={"exchange": "OTHER"})
            for security_state in maturity_run.context.market.security_states
        ),
        expected_security_ids=maturity_run.context.market.expected_security_ids,
        exchange="OTHER",
    )
    foreign_run = _run(
        "foreign-exchange-t5",
        foreign_context,
        topic_id="topic-b",
        member_ids=maturity_run.member_ids,
    )

    assert validate_relay(pending, (*runs[:-1], foreign_run)) == ()


@pytest.mark.parametrize("failure", ("suspended", "missing_price"))
def test_validate_relay_does_not_silently_remove_benchmark_member(failure):
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    if failure == "suspended":
        changed_context = target.context.model_copy(
            update={
                "market": target.context.market.model_copy(
                    update={
                        "security_states": tuple(
                            security_state.model_copy(update={"suspended": True})
                            if security_state.security_id == "B"
                            else security_state
                            for security_state in target.context.market.security_states
                        )
                    }
                )
            }
        )
    else:
        changed_context = target.context.model_copy(
            update={
                "market": target.context.market.model_copy(
                    update={
                        "bars": tuple(
                            daily_bar
                            for daily_bar in target.context.market.bars
                            if not (
                                daily_bar.security_id == "B"
                                and daily_bar.trade_date == FUTURE_SESSIONS[-1]
                            )
                        )
                    }
                )
            }
        )
    changed_runs = (*runs[:-1], target.model_copy(update={"context": changed_context}))

    validation = validate_relay(pending, changed_runs)[0]

    assert validation.outcome == "unavailable"
    assert any(failure.split("_")[0] in reason for reason in validation.reasons)


def test_validate_relay_rejects_removed_topic_member_with_stale_projection():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    changed_context = target.context.model_copy(
        update={
            "topic": target.context.topic.model_copy(
                update={
                    "memberships": tuple(
                        membership
                        for membership in target.context.topic.memberships
                        if membership.security_id != "B"
                    )
                }
            )
        }
    )

    with pytest.raises(ValidationError, match="member_ids"):
        validate_relay(
            pending,
            (*runs[:-1], target.model_copy(update={"context": changed_context})),
        )


def test_validate_relay_rejects_added_topic_member_even_if_projection_is_stale():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    added = type(target.context.topic.memberships[0]).model_validate(
        topic_membership(
            security_id="EXTRA",
            effective_from=FUTURE_SESSIONS[-1],
            available_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    changed_context = target.context.model_copy(
        update={
            "topic": target.context.topic.model_copy(
                update={"memberships": (*target.context.topic.memberships, added)}
            )
        }
    )

    with pytest.raises(ValidationError, match="member_ids"):
        validate_relay(
            pending,
            (*runs[:-1], target.model_copy(update={"context": changed_context})),
        )


def test_validate_relay_rejects_cross_exchange_future_run():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[-1]
    other_calendar = target.context.market.calendar.model_copy(
        update={"exchange": "OTHER"}
    )
    changed_context = target.context.model_copy(
        update={
            "market": target.context.market.model_copy(
                update={"calendar": other_calendar}
            )
        }
    )

    with pytest.raises(ValidationError, match="calendar identity"):
        validate_relay(
            pending,
            (*runs[:-1], target.model_copy(update={"context": changed_context})),
        )


def test_validate_relay_rejects_membership_version_change_with_same_members():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_context = RoleContext(
        market=target.context.market,
        topic=target.context.topic.model_copy(update={"membership_version": "m2"}),
        rule_version=target.context.rule_version,
    )
    changed_run = _run(
        target.role_run_id,
        changed_context,
        member_ids=target.member_ids,
    )

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == (
        "topic_incomparable",
        "membership_version_changed",
    )


def test_validate_relay_rejects_future_calendar_sessions_changed():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    first = runs[0]
    inserted_session = date(2026, 9, 13)
    changed_sessions = tuple(
        sorted((*first.context.market.calendar.sessions, inserted_session))
    )
    changed_calendar = first.context.market.calendar.model_copy(
        update={"sessions": changed_sessions}
    )
    changed_context = RoleContext(
        market=first.context.market.model_copy(update={"calendar": changed_calendar}),
        topic=first.context.topic.model_copy(update={"calendar": changed_calendar}),
        rule_version=first.context.rule_version,
    )
    changed_first = _run(
        first.role_run_id,
        changed_context,
        member_ids=first.member_ids,
    )

    validation = validate_relay(pending, (changed_first, *runs[1:]))[0]

    assert validation.outcome == "unavailable"
    assert validation.window_start == pending.validation_sessions[0]
    assert validation.window_end == pending.validation_sessions[-1]
    assert validation.return_end == pending.validation_sessions[-1]
    assert validation.reasons == (
        "topic_incomparable",
        "calendar_sessions_changed",
    )


def test_validate_relay_changed_calendar_does_not_advance_maturity():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    maturity_run = runs[-1]
    changed_sessions = tuple(
        sorted((*maturity_run.context.market.calendar.sessions, date(2026, 9, 16)))
    )
    changed_calendar = maturity_run.context.market.calendar.model_copy(
        update={"sessions": changed_sessions}
    )
    changed_context = RoleContext(
        market=maturity_run.context.market.model_copy(
            update={"calendar": changed_calendar}
        ),
        topic=maturity_run.context.topic.model_copy(
            update={"calendar": changed_calendar}
        ),
        rule_version=maturity_run.context.rule_version,
    )
    changed_maturity_run = _run(
        maturity_run.role_run_id,
        changed_context,
        member_ids=maturity_run.member_ids,
    )

    assert validate_relay(pending, (*runs[:-1], changed_maturity_run)) == ()


def test_validate_relay_requires_run_calendar_to_contain_its_trade_date():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    target = runs[2]
    changed_calendar = target.context.market.calendar.model_copy(
        update={
            "sessions": tuple(
                day
                for day in target.context.market.calendar.sessions
                if day != target.trade_date
            )
        }
    )
    changed_context = RoleContext(
        market=target.context.market.model_copy(update={"calendar": changed_calendar}),
        topic=target.context.topic.model_copy(update={"calendar": changed_calendar}),
        rule_version=target.context.rule_version,
    )
    changed_run = _run(
        target.role_run_id,
        changed_context,
        member_ids=target.member_ids,
    )

    validation = validate_relay(
        pending,
        (*runs[:2], changed_run, *runs[3:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == (
        "topic_incomparable",
        "calendar_sessions_changed",
    )


def test_validate_relay_is_idempotent_and_future_inputs_do_not_change_existing():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    first = validate_relay(pending, runs)
    persisted = pending.model_copy(update={"current_validation": first[0]})
    unrelated = runs[-1].model_copy(
        update={
            "role_run_id": "future-run",
            "context": runs[-1].context.model_copy(
                update={
                    "market": runs[-1].context.market.model_copy(
                        update={"trade_date": FUTURE_SESSIONS[-1]}
                    )
                }
            ),
        }
    )

    assert validate_relay(pending, runs) == first
    assert validate_relay(persisted, (*runs, unrelated)) == first


def test_validate_relay_authorization_revocation_makes_validation_unavailable():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    revoked = runs[-1]
    revoked_context = revoked.context.model_copy(
        update={
            "market": revoked.context.market.model_copy(
                update={"grant": grant(approved=False)}
            )
        }
    )

    validation = validate_relay(
        pending,
        (*runs[:-1], revoked.model_copy(update={"context": revoked_context})),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)


def test_validate_relay_rechecks_early_run_grant_at_validation_time():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    first = runs[0]
    early_cutoff = datetime(2026, 9, 10, 20, tzinfo=UTC)
    expires_before_maturity = datetime(2026, 9, 12, 20, tzinfo=UTC)
    early_context = first.context.model_copy(
        update={
            "market": first.context.market.model_copy(
                update={
                    "as_of": early_cutoff,
                    "grant": grant(expires_at=expires_before_maturity),
                }
            ),
            "topic": first.context.topic.model_copy(update={"as_of": early_cutoff}),
        }
    )

    validation = validate_relay(
        pending,
        (first.model_copy(update={"context": early_context}), *runs[1:]),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)


def test_validate_relay_blocks_existing_validation_after_observation_grant_revoked():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    existing = validate_relay(pending, runs)[0]
    revoked_observation = pending.observation_run.model_copy(
        update={
            "context": pending.observation_run.context.model_copy(
                update={
                    "market": pending.observation_run.context.market.model_copy(
                        update={"grant": grant(approved=False)}
                    )
                }
            )
        }
    )
    persisted = pending.model_copy(
        update={
            "observation_run": revoked_observation,
            "current_validation": existing,
        }
    )

    validation = validate_relay(persisted, runs)[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)
    assert persisted.current_validation == existing


def test_validate_relay_blocks_replay_when_current_registry_grant_is_revoked():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    existing = validate_relay(pending, runs)[0]
    persisted = pending.model_copy(
        update={
            "current_validation": existing,
            "current_grants": (grant(approved=False),),
        }
    )

    validation = validate_relay(persisted, runs)[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)


def test_validate_relay_public_api_accepts_exactly_two_arguments():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )

    assert validate_relay(pending, runs)[0].outcome == "supported"


def test_validate_relay_checks_all_grants_at_command_operation_time():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    operation_at = datetime(2026, 10, 1, tzinfo=UTC)
    expires_after_snapshot_cutoff = datetime(2026, 9, 25, tzinfo=UTC)
    expiring_runs = tuple(
        run.model_copy(
            update={
                "context": run.context.model_copy(
                    update={
                        "market": run.context.market.model_copy(
                            update={
                                "grant": grant(expires_at=expires_after_snapshot_cutoff)
                            }
                        ),
                        "topic": run.context.topic.model_copy(
                            update={
                                "grant": grant(expires_at=expires_after_snapshot_cutoff)
                            }
                        ),
                    }
                )
            }
        )
        for run in runs
    )

    pending = pending.model_copy(update={"operation_at": operation_at})
    validation = validate_relay(pending, expiring_runs)[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("authorization_unavailable",)


def test_history_outputs_are_strict_frozen_models():
    for model in (
        LimitRow,
        RelayBaselinePrice,
        RelayObservationEligibility,
        RoleChange,
        RoleValidation,
    ):
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"

    row = LimitRow(
        trade_date=T,
        topic_id=TOPIC,
        security_id="A",
        closing_limit_streak=1,
        limit_hits_10=1,
        broken_board=False,
        touched_not_sealed=False,
    )
    with pytest.raises(ValidationError, match="frozen"):
        row.limit_hits_10 = 2


@pytest.mark.parametrize(
    "changes",
    (
        {"new_security_id": "OLD"},
        {"baseline_member_ids": ("A", "B")},
        {
            "baseline_prices": (
                {"security_id": "A", "adjusted_price_raw": Decimal("100")},
            )
        },
        {
            "baseline_prices": (
                {"security_id": "A", "adjusted_price_raw": Decimal("100")},
                {"security_id": "A", "adjusted_price_raw": Decimal("101")},
                {"security_id": "B", "adjusted_price_raw": Decimal("100")},
                {"security_id": "NEW", "adjusted_price_raw": Decimal("100")},
                {"security_id": "OLD", "adjusted_price_raw": Decimal("100")},
            )
        },
        {
            "baseline_prices": (
                {"security_id": "A", "adjusted_price_raw": Decimal("100")},
                {"security_id": "B", "adjusted_price_raw": Decimal("100")},
                {"security_id": "EXTRA", "adjusted_price_raw": Decimal("100")},
                {"security_id": "OLD", "adjusted_price_raw": Decimal("100")},
            )
        },
    ),
)
def test_pending_relay_rejects_invalid_object_identity(changes):
    pending, _ = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    values = pending.model_dump()
    values.update(changes)

    with pytest.raises(ValidationError, match="security"):
        PendingRelayValidation.model_validate(values)
