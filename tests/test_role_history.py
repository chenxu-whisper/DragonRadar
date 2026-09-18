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
    member_ids: tuple[str, ...] = ("A",),
    bars=(),
    states=(),
    market_grant=None,
    topic_grant=None,
    membership_version: str = "m1",
) -> RoleContext:
    known_at = datetime(2026, 1, 1, tzinfo=UTC)
    states = states or tuple(
        state(
            security_id=security_id,
            effective_from=sessions[0],
            available_at=known_at,
            listing_date=sessions[0],
        )
        for security_id in member_ids
    )
    memberships = tuple(
        topic_membership(
            security_id=security_id,
            effective_from=sessions[0],
            available_at=known_at,
        )
        for security_id in member_ids
    )
    calendar = ExchangeCalendar(
        exchange="TEST",
        version="history-c1",
        sessions=sessions,
    )
    market = batch(
        trade_date=trade_date,
        as_of=CUTOFF,
        grant=market_grant or grant(expires_at=datetime(2027, 1, 1, tzinfo=UTC)),
        calendar=calendar,
        calendar_version=calendar.version,
        expected_security_ids=tuple(
            sorted(
                set(member_ids)
                | {daily_bar.security_id for daily_bar in bars}
                | {security_state.security_id for security_state in states}
            )
        ),
        security_states=tuple(states),
        bars=tuple(bars),
    )
    topic = topic_batch(
        trade_date=trade_date,
        as_of=CUTOFF,
        grant=topic_grant or grant(expires_at=datetime(2027, 1, 1, tzinfo=UTC)),
        calendar=calendar,
        calendar_version=calendar.version,
        coverage_from=sessions[0],
        coverage_to=trade_date,
        coverage_cells=tuple(
            coverage_cell(
                trade_date=day,
                expected_member_count=len(member_ids),
                received_member_count=len(member_ids),
                complete_as_of=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
            )
            for day in sessions
            if day <= trade_date
        ),
        topics=(
            topic_definition(effective_from=sessions[0], available_at=known_at),
        ),
        memberships=memberships,
        membership_version=membership_version,
    )
    return RoleContext(market=market, topic=topic, rule_version="role-test-v1")


def _evaluation(
    role: str,
    rows: tuple[tuple[str, int], ...],
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
        expected=len(rows),
        valid=len(rows),
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


def _run(
    run_id: str,
    context: RoleContext,
    *,
    evaluations: tuple[RoleEvaluation, ...] = (),
    previous_id: str | None = None,
    member_ids: tuple[str, ...] = ("A",),
    **changes,
) -> RoleRunEvidence:
    values = {
        "role_run_id": run_id,
        "previous_comparable_run_id": previous_id,
        "context": context,
        "topic_id": TOPIC,
        "visibility_mode": "contemporaneous",
        "market_benchmark_definition": (
            "eligible_declared_market_pool_return_median"
        ),
        "topic_source_id": context.topic.source_id,
        "topic_source_version": context.topic.source_version,
        "taxonomy_version": context.topic.taxonomy_version,
        "membership_version": context.topic.membership_version,
        "member_ids": member_ids,
        "evaluations": evaluations,
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


def test_compare_runs_reports_added_exited_tie_unchanged_and_membership_change():
    previous_context = _context(
        trade_date=SESSIONS[-2],
        member_ids=("A", "B", "C", "D"),
    )
    current_context = _context(
        member_ids=("A", "B", "C", "E"),
        membership_version="m2",
    )
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1), ("B", 2), ("D", 3))),),
        member_ids=("A", "B", "C", "D"),
    )
    current = _run(
        "current",
        current_context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 1), ("B", 1), ("E", 3))),),
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
    ("changes", "reason"),
    [
        ({"visibility_mode": "retrospective"}, "visibility_mode_changed"),
        ({"market_benchmark_definition": "other"}, "market_benchmark_changed"),
        ({"taxonomy_version": "t2"}, "taxonomy_version_changed"),
    ],
)
def test_compare_runs_marks_incompatible_runs_not_comparable(changes, reason):
    previous_context = _context(trade_date=SESSIONS[-2])
    context = _context()
    previous = _run(
        "previous",
        previous_context,
        evaluations=(_evaluation("trend", (("A", 1),)),),
    )
    current = _run(
        "current",
        context,
        previous_id="previous",
        evaluations=(_evaluation("trend", (("A", 2),)),),
        **changes,
    )

    result = compare_runs(current, previous)

    assert result == (
        RoleChange(
            current_run_id="current",
            previous_comparable_run_id="previous",
            topic_id=TOPIC,
            security_id="A",
            role="trend",
            comparison_status="not_comparable",
            change_type=None,
            membership_changed=False,
            reasons=(reason,),
        ),
    )


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
    pending = PendingRelayValidation(
        pending_result_id="pending-1",
        observation_run_id="run-t0",
        observation_date=observation_date,
        topic_id=TOPIC,
        old_security_id="OLD",
        new_security_id="NEW",
        baseline_member_ids=member_ids,
        rule_version="role-test-v1",
        visibility_mode="contemporaneous",
        market_benchmark_definition=(
            "eligible_declared_market_pool_return_median"
        ),
        topic_source_id="synthetic",
        topic_source_version="s1",
        taxonomy_version="t1",
        exchange="TEST",
        calendar_version="history-c1",
    )
    return pending, tuple(runs)


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


def test_validate_relay_returns_no_record_before_t_plus_5():
    pending, runs = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )

    assert validate_relay(pending, runs[:-1]) == ()


@pytest.mark.parametrize("failure", ("suspended", "membership", "missing_price"))
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
    elif failure == "membership":
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

    validation = validate_relay(
        pending,
        (*runs[:-1], target.model_copy(update={"context": changed_context})),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("membership_changed",)


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

    validation = validate_relay(
        pending,
        (*runs[:-1], target.model_copy(update={"context": changed_context})),
    )[0]

    assert validation.outcome == "unavailable"
    assert validation.reasons == ("topic_incomparable", "exchange_changed")


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


def test_history_outputs_are_strict_frozen_models():
    for model in (LimitRow, RoleChange, RoleValidation):
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
