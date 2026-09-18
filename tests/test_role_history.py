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
                trade_date=day,
                expected_member_count=len(member_ids),
                received_member_count=len(member_ids),
                complete_as_of=datetime(day.year, day.month, day.day, 17, tzinfo=UTC),
            )
            for day in sessions
            if day <= trade_date
        ),
        topics=(topic_definition(effective_from=sessions[0], available_at=known_at),),
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
        (("A", "B"), ("B", "A")),
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
            {},
            {"market_benchmark_definition": "revised-market-benchmark"},
            ("market_benchmark_changed",),
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
    pending = PendingRelayValidation(
        pending_result_id="pending-1",
        observation_run_id="run-t0",
        observation_date=observation_date,
        topic_id=TOPIC,
        old_security_id="OLD",
        new_security_id="NEW",
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
    )
    return pending, tuple(runs)


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
    assert validation.reasons == ("topic_incomparable", expected_reason)


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
    validation = RoleValidation(
        pending_result_id="pending-1",
        observation_run_id="run-t0",
        topic_id=TOPIC,
        outcome=(
            "refuted"
            if Decimal(new_return) <= Decimal(old_return)
            else (
                "supported"
                if Decimal(new_return) > Decimal(topic_median)
                else "weakened"
            )
        ),
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
    )
    values = validation.model_dump()
    values["outcome"] = incorrect_outcome

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
        ("outcome:supported",),
        ("outcome:weakened",),
        ("outcome:refuted",),
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


def test_validate_relay_uses_frozen_window_when_future_calendar_adds_session():
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

    assert validation.outcome == "supported"
    assert validation.window_start == pending.validation_sessions[0]
    assert validation.window_end == pending.validation_sessions[-1]
    assert validation.return_end == pending.validation_sessions[-1]


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


def test_history_outputs_are_strict_frozen_models():
    for model in (LimitRow, RelayBaselinePrice, RoleChange, RoleValidation):
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
