"""Rotation timeline tests for the Task 24 / EX11 pure-function slice.

This module pins only ``build_rotation`` and its frozen input/output models.
It is a synchronous pure-function slice: no PostgreSQL, no HTTP client, no
materialization, and no import of database modules. Every input is built
locally with the ``_run`` factory so the suite runs without
``DRAGON_TEST_DATABASE_URL`` and has zero skipped cases.

Semantic contract pinned here:

* events describe set algebra over the archived trend rank-1 core sets
  (``expanded`` / ``contracted`` / ``reorganized`` / ``unchanged``) and never
  assert causation such as succession or relay;
* history is never backfilled from current members: a missing past run simply
  has no event, and incomparable basis (rule/topic/taxonomy/membership/pool/
  explicit predecessor/session gap) restarts the segment instead;
* runs published after ``as_of`` are invisible even when their trade date is
  inside the requested window.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from dragon_radar.roles.rotation import (
    RotationEvent,
    RotationInputManifest,
    RotationRun,
    RotationTimeline,
    build_rotation,
)

D1 = date(2026, 10, 12)
D2 = date(2026, 10, 13)
D3 = date(2026, 10, 14)
D4 = date(2026, 10, 15)
D5 = date(2026, 10, 16)

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=1)

MARKET_SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000601")
TOPIC_SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000602")

SECTOR_A = "sector-a"
SECTOR_B = "sector-b"


def _run(
    *,
    trade_date=D1,
    sector_id=SECTOR_A,
    run_id=None,
    core_ids=("A",),
    member_ids=("A", "B"),
    rule_version="role-v1",
    topic_source_version="topic-source-v1",
    taxonomy_version="taxonomy-v1",
    membership_version="membership-v1",
    available_at=NOW,
    market_snapshot_id=MARKET_SNAPSHOT_ID,
    topic_snapshot_id=TOPIC_SNAPSHOT_ID,
    previous_comparable_run_id=None,
):
    return RotationRun(
        run_id=run_id or f"run-{sector_id}-{trade_date.isoformat()}",
        sector_id=sector_id,
        trade_date=trade_date,
        available_at=available_at,
        rule_version=rule_version,
        topic_source_version=topic_source_version,
        taxonomy_version=taxonomy_version,
        membership_version=membership_version,
        member_ids=member_ids,
        core_ids=core_ids,
        market_snapshot_id=market_snapshot_id,
        topic_snapshot_id=topic_snapshot_id,
        previous_comparable_run_id=previous_comparable_run_id,
    )


def _event(timeline, sector_id, trade_date):
    matches = [
        event
        for event in timeline.events
        if event.sector_id == sector_id and event.at == trade_date.isoformat()
    ]
    assert len(matches) == 1
    return matches[0]


def _event_keys(timeline):
    return [
        (date.fromisoformat(event.at), event.sector_id) for event in timeline.events
    ]


def test_core_path_expands_then_contracts_with_set_algebra_only():
    # A -> {A, B} tied cores -> {B}; same rule version and member pool
    # throughout, so only the first observation starts a segment.
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D2, core_ids=("A", "B")),
        _run(trade_date=D3, core_ids=("B",)),
    )

    timeline = build_rotation(runs)

    assert [event.at for event in timeline.events] == [
        D1.isoformat(),
        D2.isoformat(),
        D3.isoformat(),
    ]
    first, second, third = timeline.events
    assert first.change == "segment_start"
    assert first.comparable is False
    assert first.reasons == ("first_observation",)
    assert first.core_ids == ("A",)
    assert second.change == "expanded"
    assert second.comparable is True
    assert second.reasons == ()
    assert second.core_ids == ("A", "B")
    assert third.change == "contracted"
    assert third.comparable is True
    assert third.reasons == ()
    assert third.core_ids == ("B",)
    # Pure set algebra: no causal/succession wording may ever surface.
    blob = " ".join(reason for event in timeline.events for reason in event.reasons)
    assert "succeed" not in blob
    assert "relay" not in blob
    assert "replace" not in blob


def test_no_core_candidate_contracts_then_reexpands_within_one_segment():
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D2, core_ids=()),
        _run(trade_date=D3, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs)

    first, empty, restored = timeline.events
    assert first.reasons == ("first_observation",)
    assert empty.change == "contracted"
    assert empty.comparable is True
    assert empty.core_ids == ()
    assert empty.reasons == ("no_core_candidate",)
    assert restored.change == "expanded"
    assert restored.comparable is True
    assert restored.core_ids == ("A", "B")
    assert restored.reasons == ()


def test_first_observation_without_a_core_carries_only_first_observation():
    # Pinned caliber: a segment_start event (including the first visible
    # observation) never appends no_core_candidate even when its core set is
    # empty; the label exists only for comparable observations.
    runs = (_run(trade_date=D1, member_ids=("A", "B"), core_ids=()),)

    timeline = build_rotation(runs)

    first = timeline.events[0]
    assert first.change == "segment_start"
    assert first.comparable is False
    assert first.core_ids == ()
    assert first.reasons == ("first_observation",)
    assert "no_core_candidate" not in first.reasons


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        (
            {"membership_version": "membership-v2"},
            "membership_version_changed",
        ),
        (
            {
                "member_ids": ("X", "Y", "Z"),
                "core_ids": ("X",),
            },
            "member_pool_changed",
        ),
        (
            {"topic_source_version": "topic-source-v2"},
            "topic_revision_changed",
        ),
        (
            {"taxonomy_version": "taxonomy-v2"},
            "taxonomy_version_changed",
        ),
    ],
    ids=[
        "membership_version",
        "member_pool",
        "topic_revision",
        "taxonomy_version",
    ],
)
def test_incomparable_basis_starts_a_new_segment(changes, expected_reason):
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D2, **{"core_ids": ("A", "B"), **changes}),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert expected_reason in restarted.reasons


def test_restarted_segment_never_references_cores_before_the_break():
    # Day 2 changes the member pool (A,B -> X,Y,Z). Day 3 expands only
    # relative to day 2's {X}: a cross-segment comparison against day 1's
    # {A} would wrongly classify {X, Y} as "reorganized".
    runs = (
        _run(trade_date=D1, member_ids=("A", "B"), core_ids=("A",)),
        _run(
            trade_date=D2,
            member_ids=("X", "Y", "Z"),
            core_ids=("X",),
        ),
        _run(
            trade_date=D3,
            member_ids=("X", "Y", "Z"),
            core_ids=("X", "Y"),
        ),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.reasons == ("member_pool_changed",)
    resumed = _event(timeline, SECTOR_A, D3)
    assert resumed.comparable is True
    assert resumed.change == "expanded"
    assert resumed.reasons == ()


def test_rule_version_change_starts_a_new_segment():
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(
            trade_date=D2,
            core_ids=("A", "B"),
            rule_version="role-v2",
        ),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert restarted.reasons == ("rule_version_changed",)


def test_multiple_segment_break_reasons_accumulate_in_fixed_order():
    # Adjacent days simultaneously changing rule version, membership
    # version, member pool, and disagreeing on the explicit predecessor.
    # Reasons accumulate without duplicates in the frozen emission order;
    # no session calendar is supplied, so no session reason appears.
    runs = (
        _run(
            trade_date=D1,
            run_id="run-d1",
            member_ids=("A", "B", "C"),
            core_ids=("A",),
        ),
        _run(
            trade_date=D2,
            run_id="run-d2",
            rule_version="role-v2",
            membership_version="membership-v2",
            member_ids=("X", "Y", "Z"),
            core_ids=("X",),
            previous_comparable_run_id="run-some-other",
        ),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert restarted.reasons == (
        "rule_version_changed",
        "membership_version_changed",
        "member_pool_changed",
        "previous_comparable_run_mismatch",
    )
    assert len(restarted.reasons) == len(set(restarted.reasons))


def test_future_publication_is_excluded_even_inside_the_date_window():
    # D2 has an older trade date than D3 but is published after as_of; it
    # must not enter the timeline at all, so D3 compares directly with D1.
    runs = (
        _run(trade_date=D1, member_ids=("A", "B", "X"), core_ids=("A",)),
        _run(
            trade_date=D2,
            member_ids=("A", "B", "X"),
            core_ids=("X",),
            available_at=FUTURE,
        ),
        _run(trade_date=D3, member_ids=("A", "B", "X"), core_ids=("A", "B")),
    )

    visible = build_rotation(runs, as_of=NOW)

    assert _event_keys(visible) == [(D1, SECTOR_A), (D3, SECTOR_A)]
    later = _event(visible, SECTOR_A, D3)
    assert later.comparable is True
    assert later.change == "expanded"

    # Without an as_of cutoff every published run is visible.
    all_visible = build_rotation(runs)
    assert _event_keys(all_visible) == [
        (D1, SECTOR_A),
        (D2, SECTOR_A),
        (D3, SECTOR_A),
    ]


def test_old_trade_date_published_in_the_future_is_excluded():
    runs = (
        _run(trade_date=D1, available_at=FUTURE),
        _run(trade_date=D2, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs, as_of=NOW)

    assert _event_keys(timeline) == [(D2, SECTOR_A)]
    only = _event(timeline, SECTOR_A, D2)
    assert only.change == "segment_start"
    assert only.comparable is False
    assert only.reasons == ("first_observation",)


def test_available_at_equal_to_as_of_is_visible():
    runs = (_run(trade_date=D1, available_at=NOW),)

    timeline = build_rotation(runs, as_of=NOW)

    assert _event_keys(timeline) == [(D1, SECTOR_A)]


def test_explicit_predecessor_mismatch_restarts_the_segment():
    runs = (
        _run(trade_date=D1, run_id="run-d1"),
        _run(
            trade_date=D2,
            core_ids=("A", "B"),
            run_id="run-d2",
            previous_comparable_run_id="run-some-other",
        ),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert restarted.reasons == ("previous_comparable_run_mismatch",)


def test_matching_explicit_predecessor_keeps_the_segment_comparable():
    runs = (
        _run(trade_date=D1, run_id="run-d1"),
        _run(
            trade_date=D2,
            core_ids=("A", "B"),
            run_id="run-d2",
            previous_comparable_run_id="run-d1",
        ),
    )

    timeline = build_rotation(runs)

    compared = _event(timeline, SECTOR_A, D2)
    assert compared.comparable is True
    assert compared.change == "expanded"
    assert compared.reasons == ()


@pytest.mark.parametrize(
    "sessions",
    [
        (D1, D2, D3, D4),
        (D3, D4),
    ],
    ids=["missing_intermediate_session", "previous_session_not_in_calendar"],
)
def test_session_gap_starts_segment_when_sessions_are_supplied(sessions):
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D3, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs, sessions=sessions)

    gapped = _event(timeline, SECTOR_A, D3)
    assert gapped.change == "segment_start"
    assert gapped.comparable is False
    assert gapped.reasons == ("not_previous_comparable_session",)


def test_adjacent_sessions_remain_comparable():
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D2, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs, sessions=(D1, D2, D3))

    compared = _event(timeline, SECTOR_A, D2)
    assert compared.comparable is True
    assert compared.change == "expanded"
    assert compared.reasons == ()


def test_session_gap_is_not_judged_without_a_session_calendar():
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D3, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs)

    compared = _event(timeline, SECTOR_A, D3)
    assert compared.comparable is True
    assert compared.change == "expanded"
    assert "not_previous_comparable_session" not in compared.reasons


def test_current_date_absent_from_sessions_fails_closed_as_not_adjacent():
    # Fail-closed branch of the session check: the previous date is in the
    # supplied calendar but the current run's trade_date is not a calendar
    # session at all, so no predecessor session can be established.
    runs = (
        _run(trade_date=D1, core_ids=("A",)),
        _run(trade_date=D2, core_ids=("A", "B")),
    )

    timeline = build_rotation(runs, sessions=(D1, D3, D4))

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert restarted.reasons == ("not_previous_comparable_session",)


def test_unsorted_or_duplicate_sessions_raise_value_error():
    runs = (_run(trade_date=D1),)

    with pytest.raises(ValueError):
        build_rotation(runs, sessions=(D2, D1))
    with pytest.raises(ValueError):
        build_rotation(runs, sessions=(D1, D1))


def test_events_sort_globally_by_date_then_sector_and_identifiers_canonicalize():
    runs = (
        _run(
            trade_date=D2,
            sector_id=SECTOR_B,
            member_ids=("B", "A", "A"),
            core_ids=("B", "A", "A"),
        ),
        _run(trade_date=D1, sector_id=SECTOR_B),
        _run(
            trade_date=D2,
            sector_id=SECTOR_A,
            member_ids=("B", "A", "A"),
            core_ids=("B", "A", "A"),
        ),
        _run(trade_date=D1, sector_id=SECTOR_A),
    )

    shuffled = RotationRun.model_validate(runs[0].model_dump())
    assert shuffled.member_ids == ("A", "B")
    assert shuffled.core_ids == ("A", "B")

    first = build_rotation(runs)
    second = build_rotation(tuple(reversed(runs)))

    expected_keys = [
        (D1, SECTOR_A),
        (D1, SECTOR_B),
        (D2, SECTOR_A),
        (D2, SECTOR_B),
    ]
    assert _event_keys(first) == expected_keys
    assert first == second
    assert isinstance(first, RotationTimeline)
    assert isinstance(first.events, tuple)
    for event in first.events:
        assert event.core_ids == tuple(sorted(event.core_ids))
        assert len(set(event.core_ids)) == len(event.core_ids)


def test_duplicate_sector_date_runs_raise_value_error():
    runs = (
        _run(trade_date=D1, run_id="run-one"),
        _run(trade_date=D1, run_id="run-two", core_ids=("B",)),
    )

    with pytest.raises(ValueError):
        build_rotation(runs)


@pytest.mark.parametrize(
    ("previous_cores", "current_cores", "expected_change"),
    [
        (("A",), ("C",), "reorganized"),
        (("A", "B"), ("A", "B"), "unchanged"),
    ],
    ids=["disjoint_core_sets", "identical_core_sets"],
)
def test_set_algebra_reorganizes_disjoint_and_keeps_identical(
    previous_cores, current_cores, expected_change
):
    # The member pool stays comparable and contains both A and C.
    runs = (
        _run(trade_date=D1, member_ids=("A", "B", "C"), core_ids=previous_cores),
        _run(trade_date=D2, member_ids=("A", "B", "C"), core_ids=current_cores),
    )

    timeline = build_rotation(runs)

    compared = _event(timeline, SECTOR_A, D2)
    assert compared.comparable is True
    assert compared.change == expected_change
    assert compared.reasons == ()


def test_sector_keyword_keeps_only_the_requested_sector():
    runs = (
        _run(trade_date=D1, sector_id=SECTOR_A),
        _run(trade_date=D1, sector_id=SECTOR_B),
    )

    timeline = build_rotation(runs, sector=SECTOR_B)

    assert _event_keys(timeline) == [(D1, SECTOR_B)]


def test_sector_keyword_without_match_returns_empty_timeline():
    runs = (_run(trade_date=D1, sector_id=SECTOR_A),)

    timeline = build_rotation(runs, sector="sector-missing")

    assert timeline.events == ()
    assert isinstance(timeline.events, tuple)


def test_input_manifest_echoes_exact_run_binding_values_and_types():
    market_id = uuid4()
    topic_id = uuid4()
    run = _run(
        trade_date=D1,
        run_id="run-manifest",
        rule_version="role-v1",
        membership_version="membership-v1",
        market_snapshot_id=market_id,
        topic_snapshot_id=topic_id,
    )

    timeline = build_rotation((run,))

    manifest = timeline.events[0].input_manifest
    assert manifest.run_id == "run-manifest"
    assert isinstance(manifest.run_id, str)
    assert manifest.market_snapshot_id == market_id
    assert manifest.topic_snapshot_id == topic_id
    assert isinstance(manifest.market_snapshot_id, UUID)
    assert isinstance(manifest.topic_snapshot_id, UUID)
    assert manifest.rule_version == "role-v1"
    assert manifest.membership_version == "membership-v1"
    assert isinstance(manifest.rule_version, str)
    assert isinstance(manifest.membership_version, str)


def test_empty_input_builds_empty_timeline():
    assert build_rotation(()).events == ()


def test_core_ids_must_be_a_subset_of_member_ids():
    with pytest.raises(ValidationError):
        _run(member_ids=("A", "B"), core_ids=("C",))


@pytest.mark.parametrize(
    ("member_ids", "core_ids"),
    [
        (("A", ""), ("A",)),
        (("A",), ("",)),
    ],
    ids=["empty_member_id", "empty_core_id"],
)
def test_member_and_core_identifiers_must_be_nonempty(member_ids, core_ids):
    with pytest.raises(ValidationError):
        _run(member_ids=member_ids, core_ids=core_ids)


def test_naive_available_at_is_rejected():
    with pytest.raises(ValidationError):
        RotationRun(
            run_id="run-naive",
            sector_id=SECTOR_A,
            trade_date=D1,
            available_at=datetime(2026, 10, 20, 9, 0),
            rule_version="role-v1",
            topic_source_version="topic-source-v1",
            taxonomy_version="taxonomy-v1",
            membership_version="membership-v1",
            member_ids=("A",),
            core_ids=("A",),
            market_snapshot_id=MARKET_SNAPSHOT_ID,
            topic_snapshot_id=TOPIC_SNAPSHOT_ID,
        )


# ---------------------------------------------------------------------------
# Quality-review hardening: cutoff typing, calendar validation, event
# invariants, domain identifiers, keyword validation, and set-algebra edges
# ---------------------------------------------------------------------------

BEIJING_TZ = timezone(timedelta(hours=8))


def _input_manifest():
    return RotationInputManifest(
        run_id="run-event",
        market_snapshot_id=MARKET_SNAPSHOT_ID,
        topic_snapshot_id=TOPIC_SNAPSHOT_ID,
        rule_version="role-v1",
        membership_version="membership-v1",
    )


def _rotation_event(**overrides):
    fields = {
        "at": D1.isoformat(),
        "sector_id": SECTOR_A,
        "core_ids": ("A",),
        "change": "segment_start",
        "comparable": False,
        "reasons": ("first_observation",),
        "input_manifest": _input_manifest(),
    }
    fields.update(overrides)
    return RotationEvent(**fields)


def test_naive_as_of_raises_controlled_value_error():
    with pytest.raises(
        ValueError,
        match="as_of must be a timezone-aware datetime",
    ):
        build_rotation((_run(),), as_of=datetime(2026, 10, 20, 9, 0))


def test_aware_non_utc_as_of_keeps_the_same_instant_cutoff():
    # 2026-10-20 17:00 +08:00 is the same instant as 09:00 UTC (NOW).
    cutoff_beijing = datetime(2026, 10, 20, 17, 0, tzinfo=BEIJING_TZ)
    runs = (
        _run(trade_date=D1, available_at=NOW),
        _run(trade_date=D2, available_at=NOW + timedelta(seconds=1)),
    )

    beijing_view = build_rotation(runs, as_of=cutoff_beijing)
    utc_view = build_rotation(runs, as_of=NOW)

    assert _event_keys(beijing_view) == [(D1, SECTOR_A)]
    assert beijing_view == utc_view


@pytest.mark.parametrize(
    "sessions",
    [
        (),
        (D2, D1),
        (D1, D1),
    ],
    ids=["empty", "unsorted", "duplicate"],
)
def test_invalid_sessions_raise_value_error(sessions):
    with pytest.raises(
        ValueError,
        match=r"^sessions must be nonempty, sorted and unique trade dates$",
    ):
        build_rotation((_run(),), sessions=sessions)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "change": "segment_start",
            "comparable": True,
            "reasons": ("first_observation",),
        },
        {
            "change": "expanded",
            "comparable": False,
            "reasons": ("rule_version_changed",),
        },
        {"comparable": False, "reasons": ()},
        {"at": " "},
    ],
    ids=[
        "segment_start_marked_comparable",
        "set_algebra_marked_not_comparable",
        "not_comparable_without_reasons",
        "blank_at_identifier",
    ],
)
def test_rotation_event_rejects_inconsistent_or_blank_fields(overrides):
    with pytest.raises(ValidationError):
        _rotation_event(**overrides)


def test_consistent_comparable_rotation_event_constructs():
    event = _rotation_event(
        at=D2.isoformat(),
        change="unchanged",
        comparable=True,
        reasons=(),
    )

    assert event.change == "unchanged"
    assert event.comparable is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", " "),
        ("sector_id", " "),
        ("rule_version", " "),
        ("topic_source_version", " "),
        ("taxonomy_version", " "),
        ("membership_version", " "),
        ("member_ids", ("A", " ")),
        ("core_ids", (" ",)),
    ],
    ids=[
        "run_id",
        "sector_id",
        "rule_version",
        "topic_source_version",
        "taxonomy_version",
        "membership_version",
        "member_ids_element",
        "core_ids_element",
    ],
)
def test_whitespace_only_identifiers_are_rejected(field, value):
    fields = {
        "run_id": "run-ok",
        "sector_id": SECTOR_A,
        "trade_date": D1,
        "available_at": NOW,
        "rule_version": "role-v1",
        "topic_source_version": "topic-source-v1",
        "taxonomy_version": "taxonomy-v1",
        "membership_version": "membership-v1",
        "member_ids": ("A", "B"),
        "core_ids": ("A",),
        "market_snapshot_id": MARKET_SNAPSHOT_ID,
        "topic_snapshot_id": TOPIC_SNAPSHOT_ID,
    }
    fields[field] = value

    with pytest.raises(ValidationError):
        RotationRun(**fields)


@pytest.mark.parametrize("sector", ["", "   "], ids=["empty", "whitespace"])
def test_blank_sector_keyword_raises_value_error(sector):
    with pytest.raises(
        ValueError,
        match="sector must be a nonempty, non-whitespace identifier",
    ):
        build_rotation((_run(),), sector=sector)


def test_duplicate_sector_date_is_judged_only_over_visible_runs():
    # Two runs share (sector, D1), but one is published after as_of; after
    # visibility filtering the duplicate key no longer exists.
    runs = (
        _run(trade_date=D1, run_id="run-visible", available_at=NOW),
        _run(
            trade_date=D1,
            run_id="run-hidden",
            core_ids=("B",),
            available_at=FUTURE,
        ),
    )

    timeline = build_rotation(runs, as_of=NOW)

    assert _event_keys(timeline) == [(D1, SECTOR_A)]
    assert timeline.events[0].input_manifest.run_id == "run-visible"


def test_empty_to_empty_cores_is_unchanged_with_no_core_candidate():
    runs = (
        _run(trade_date=D1, core_ids=()),
        _run(trade_date=D2, core_ids=()),
    )

    timeline = build_rotation(runs)

    second = _event(timeline, SECTOR_A, D2)
    assert second.change == "unchanged"
    assert second.comparable is True
    assert second.reasons == ("no_core_candidate",)


def test_partially_overlapping_core_sets_are_reorganized():
    runs = (
        _run(trade_date=D1, member_ids=("A", "B", "C"), core_ids=("A", "B")),
        _run(trade_date=D2, member_ids=("A", "B", "C"), core_ids=("B", "C")),
    )

    timeline = build_rotation(runs)

    second = _event(timeline, SECTOR_A, D2)
    assert second.change == "reorganized"
    assert second.comparable is True
    assert second.reasons == ()


def test_segment_restart_without_cores_carries_only_break_reasons():
    # Symmetric to the first-observation caliber: a non-first event that
    # breaks the segment via member_pool_changed and has empty cores carries
    # only the break reasons, never no_core_candidate.
    runs = (
        _run(trade_date=D1, member_ids=("A", "B"), core_ids=("A",)),
        _run(trade_date=D2, member_ids=("X", "Y", "Z"), core_ids=()),
    )

    timeline = build_rotation(runs)

    restarted = _event(timeline, SECTOR_A, D2)
    assert restarted.change == "segment_start"
    assert restarted.comparable is False
    assert restarted.core_ids == ()
    assert restarted.reasons == ("member_pool_changed",)
    assert "no_core_candidate" not in restarted.reasons
