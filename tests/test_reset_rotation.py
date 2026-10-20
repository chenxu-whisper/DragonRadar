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

import json
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from dragon_radar.roles.rotation import (
    ROTATION_REASONS,
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


def test_rotation_event_rejects_reason_outside_allowlist():
    # The frozen vocabulary is the model boundary: a typo or a fabricated
    # token must never be archivable (and therefore never served).
    with pytest.raises(ValidationError):
        _rotation_event(reasons=("totally_fake",))
    with pytest.raises(ValidationError):
        _rotation_event(reasons=("first_observation", "succeeded"))


def test_rotation_reason_allowlist_is_the_frozen_v1_vocabulary():
    assert ROTATION_REASONS == frozenset(
        {
            "first_observation",
            "rule_version_changed",
            "topic_revision_changed",
            "taxonomy_version_changed",
            "membership_version_changed",
            "member_pool_changed",
            "previous_comparable_run_mismatch",
            "not_previous_comparable_session",
            "no_core_candidate",
        }
    )


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


# ---------------------------------------------------------------------------
# Slice 2: publication-time materialization adapters (still pure; no DB or
# HTTP import). ``trend_core_ids`` extracts the archived core set from
# role-result-shaped mapping rows; ``materialize_run_rotation`` turns the
# already-loaded run/topic/result rows of one run and its optional immediate
# predecessor into frozen per-topic event payloads.
# ---------------------------------------------------------------------------

import factories  # noqa: E402
import topic_factories  # noqa: E402
from sqlalchemy import delete, insert, update  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402

import dragon_radar.publication.repository as publication_repository  # noqa: E402
from dragon_radar.domain.models import ExchangeCalendar  # noqa: E402
from dragon_radar.publication.models import PublicationCommand  # noqa: E402
from dragon_radar.publication.repository import PublicationRepository  # noqa: E402
from dragon_radar.publication.service import PublicationService  # noqa: E402
from dragon_radar.roles.materialize import RoleMaterializer  # noqa: E402
from dragon_radar.roles.repository import RoleRepository  # noqa: E402
from dragon_radar.roles.rotation import (  # noqa: E402
    materialize_run_rotation,
    rotation_event_payload,
    trend_core_ids,
)
from dragon_radar.roles.tables import (  # noqa: E402
    role_results,
    role_run_topics,
    role_runs,
)
from dragon_radar.topics.repository import TopicRepository  # noqa: E402


def _result_row(
    security_id,
    *,
    topic_id=SECTOR_A,
    role="trend",
    internal_status="candidate",
    tie_rank=1,
):
    return {
        "topic_id": topic_id,
        "security_id": security_id,
        "role": role,
        "internal_status": internal_status,
        "tie_rank": tie_rank,
    }


def _db_run(
    trade_date=D1,
    *,
    run_id=None,
    previous=None,
    rule_version="role-v1",
    topic_source_version="topic-source-v1",
    taxonomy_version="taxonomy-v1",
    membership_version="membership-v1",
    market_snapshot_id=MARKET_SNAPSHOT_ID,
    topic_snapshot_id=TOPIC_SNAPSHOT_ID,
    available_at=NOW,
):
    return {
        "id": run_id or f"run-{trade_date.isoformat()}",
        "trade_date": trade_date,
        "materialized_at": available_at,
        "rule_version": rule_version,
        "topic_source_version": topic_source_version,
        "taxonomy_version": taxonomy_version,
        "membership_version": membership_version,
        "market_snapshot_id": market_snapshot_id,
        "topic_snapshot_id": topic_snapshot_id,
        "previous_comparable_run_id": previous,
    }


def _db_topic(topic_id=SECTOR_A, members=("A", "B")):
    return {"topic_id": topic_id, "member_ids": list(members)}


def _core_rows(core_ids, *, topic_id=SECTOR_A):
    return [_result_row(security_id, topic_id=topic_id) for security_id in core_ids]


def _materialize(
    current,
    current_topics,
    current_results,
    *,
    sessions=(D1, D2, D3, D4, D5),
    predecessor=None,
    predecessor_topics=None,
    predecessor_results=None,
):
    return materialize_run_rotation(
        run_row=current,
        topic_rows=current_topics,
        result_rows=current_results,
        sessions=sessions,
        predecessor_row=predecessor,
        predecessor_topic_rows=predecessor_topics,
        predecessor_result_rows=predecessor_results,
    )


def test_trend_core_ids_keeps_only_rank_one_trend_candidates_and_sorts():
    rows = [
        _result_row("Z", tie_rank=2),
        _result_row("A", internal_status="excluded"),
        _result_row("B", role="core"),
        _result_row("A"),
        _result_row("M", role="trend", internal_status="pending_validation"),
        _result_row("C"),
    ]

    assert trend_core_ids(rows) == ("A", "C")


def test_trend_core_ids_deduplicates_and_supports_empty_and_mapping_rows():
    class _Row(dict):
        pass

    assert trend_core_ids([_Row(_result_row("B")), _Row(_result_row("B"))]) == ("B",)
    assert trend_core_ids(()) == ()


def test_materialize_run_rotation_chains_first_expand_then_contract_across_calls():
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(D2, run_id="run-d2", previous="run-d1")
    run3 = _db_run(D3, run_id="run-d3", previous="run-d2")

    first = _materialize(
        run1,
        [_db_topic()],
        _core_rows(("A",)),
    )
    second = _materialize(
        run2,
        [_db_topic()],
        _core_rows(("A", "B")),
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )
    third = _materialize(
        run3,
        [_db_topic()],
        _core_rows(("B",)),
        predecessor=run2,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A", "B")),
    )

    event1 = first[SECTOR_A]
    event2 = second[SECTOR_A]
    event3 = third[SECTOR_A]
    assert event1["at"] == D1.isoformat()
    assert event1["change"] == "segment_start"
    assert event1["comparable"] is False
    assert event1["reasons"] == ["first_observation"]
    assert event2["change"] == "expanded"
    assert event2["comparable"] is True
    assert event2["reasons"] == []
    assert event2["core_ids"] == ["A", "B"]
    assert event3["change"] == "contracted"
    assert event3["comparable"] is True
    assert event3["core_ids"] == ["B"]
    # Every event binds the manifest of its own run, never the predecessor.
    assert event1["input_manifest"]["run_id"] == "run-d1"
    assert event2["input_manifest"]["run_id"] == "run-d2"
    assert event3["input_manifest"]["run_id"] == "run-d3"


def test_materialize_run_rotation_binds_manifest_uuids_per_run():
    market2 = uuid4()
    topic2 = uuid4()
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(
        D2,
        run_id="run-d2",
        previous="run-d1",
        market_snapshot_id=market2,
        topic_snapshot_id=topic2,
    )

    blocks = _materialize(
        run2,
        [_db_topic()],
        _core_rows(("A", "B")),
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )

    manifest = blocks[SECTOR_A]["input_manifest"]
    assert manifest == {
        "run_id": "run-d2",
        "market_snapshot_id": str(market2),
        "topic_snapshot_id": str(topic2),
        "rule_version": "role-v1",
        "membership_version": "membership-v1",
    }
    json.dumps(blocks)


def test_materialize_run_rotation_empty_core_comparable_carries_no_core_candidate():
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(D2, run_id="run-d2", previous="run-d1")

    blocks = _materialize(
        run2,
        [_db_topic()],
        [],
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )

    event = blocks[SECTOR_A]
    assert event["change"] == "contracted"
    assert event["comparable"] is True
    assert event["reasons"] == ["no_core_candidate"]
    assert event["core_ids"] == []


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"rule_version": "role-v2"}, "rule_version_changed"),
        (
            {"topic_source_version": "topic-source-v2"},
            "topic_revision_changed",
        ),
        ({"taxonomy_version": "taxonomy-v2"}, "taxonomy_version_changed"),
        (
            {"membership_version": "membership-v2"},
            "membership_version_changed",
        ),
    ],
    ids=["rule_version", "topic_revision", "taxonomy", "membership_version"],
)
def test_materialize_run_rotation_version_breaks_restart_segment(
    changes, expected_reason
):
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(D2, run_id="run-d2", previous="run-d1", **changes)

    blocks = _materialize(
        run2,
        [_db_topic()],
        _core_rows(("A", "B")),
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )

    event = blocks[SECTOR_A]
    assert event["change"] == "segment_start"
    assert event["comparable"] is False
    assert event["reasons"][0] == expected_reason


def test_materialize_run_rotation_member_pool_and_pointer_breaks_in_fixed_order():
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(
        D2,
        run_id="run-d2",
        previous="run-some-other",
        membership_version="membership-v2",
    )

    blocks = _materialize(
        run2,
        [_db_topic(members=("X", "Y", "Z"))],
        _core_rows(("X",)),
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )

    assert blocks[SECTOR_A]["reasons"] == [
        "membership_version_changed",
        "member_pool_changed",
        "previous_comparable_run_mismatch",
    ]


def test_materialize_run_rotation_session_gap_breaks_the_segment():
    run1 = _db_run(D1, run_id="run-d1")
    run3 = _db_run(D3, run_id="run-d3", previous="run-d1")

    blocks = _materialize(
        run3,
        [_db_topic()],
        _core_rows(("A", "B")),
        sessions=(D1, D2, D3),
        predecessor=run1,
        predecessor_topics=[_db_topic()],
        predecessor_results=_core_rows(("A",)),
    )

    event = blocks[SECTOR_A]
    assert event["change"] == "segment_start"
    assert event["reasons"] == ["not_previous_comparable_session"]


def test_materialize_run_rotation_without_predecessor_topic_is_first_observation():
    # The predecessor run exists for another sector but never carried this
    # sector, so the current sector starts a fresh observation rather than
    # comparing against a reconstructed current membership.
    run1 = _db_run(D1, run_id="run-d1")
    run2 = _db_run(D2, run_id="run-d2", previous="run-d1")

    blocks = _materialize(
        run2,
        [_db_topic(SECTOR_A)],
        _core_rows(("A",)),
        predecessor=run1,
        predecessor_topics=[_db_topic(SECTOR_B)],
        predecessor_results=_core_rows(("B",), topic_id=SECTOR_B),
    )

    event = blocks[SECTOR_A]
    assert event["change"] == "segment_start"
    assert event["comparable"] is False
    assert event["reasons"] == ["first_observation"]


def test_materialize_run_rotation_covers_every_current_topic_without_predecessor():
    run1 = _db_run(D1)

    blocks = _materialize(
        run1,
        [_db_topic(SECTOR_A), _db_topic(SECTOR_B, members=("B",))],
        [
            *_core_rows(("A",), topic_id=SECTOR_A),
            *_core_rows(("B",), topic_id=SECTOR_B),
        ],
        predecessor=None,
    )

    assert set(blocks) == {SECTOR_A, SECTOR_B}
    for event in blocks.values():
        assert event["reasons"] == ["first_observation"]
    assert blocks[SECTOR_B]["sector_id"] == SECTOR_B
    assert blocks[SECTOR_B]["core_ids"] == ["B"]


def test_materialize_run_rotation_rejects_core_outside_member_pool_fail_closed():
    run1 = _db_run(D1)

    with pytest.raises(ValidationError):
        _materialize(
            run1,
            [_db_topic(members=("A",))],
            _core_rows(("Z",)),
        )


def test_rotation_event_payload_is_json_safe_with_lists_and_iso_strings():
    market_id = uuid4()
    topic_id = uuid4()
    run = RotationRun(
        run_id="run-payload",
        sector_id=SECTOR_A,
        trade_date=D2,
        available_at=NOW,
        rule_version="role-v1",
        topic_source_version="topic-source-v1",
        taxonomy_version="taxonomy-v1",
        membership_version="membership-v1",
        member_ids=("A", "B"),
        core_ids=("A", "B"),
        market_snapshot_id=market_id,
        topic_snapshot_id=topic_id,
    )

    event = build_rotation((run,)).events[0]
    payload = rotation_event_payload(event)

    assert set(payload) == {
        "at",
        "sector_id",
        "core_ids",
        "change",
        "comparable",
        "reasons",
        "input_manifest",
    }
    assert payload["at"] == D2.isoformat()
    assert isinstance(payload["core_ids"], list)
    assert isinstance(payload["reasons"], list)
    assert isinstance(payload["comparable"], bool)
    assert set(payload["input_manifest"]) == {
        "run_id",
        "market_snapshot_id",
        "topic_snapshot_id",
        "rule_version",
        "membership_version",
    }
    assert payload["input_manifest"]["market_snapshot_id"] == str(market_id)
    assert payload["input_manifest"]["topic_snapshot_id"] == str(topic_id)
    json.dumps(payload)


# ---------------------------------------------------------------------------
# Slice 2 B: PostgreSQL publication-time materialization. These tests auto
# skip without DRAGON_TEST_DATABASE_URL (see tests/conftest.py); each gets a
# disposable migrated schema. They exercise a real two-session publication
# chain (9/7 -> 9/8) through RoleMaterializer and PublicationService.
# ---------------------------------------------------------------------------

_ROTATION_BLOCK_KEYS = {
    "at",
    "sector_id",
    "core_ids",
    "change",
    "comparable",
    "reasons",
    "input_manifest",
}
_MANIFEST_KEYS = {
    "run_id",
    "market_snapshot_id",
    "topic_snapshot_id",
    "rule_version",
    "membership_version",
}


def _chain_inputs(database, repository, trade_day, *, key):
    # The role evidence contract requires one shared calendar identity: the
    # market batch calendar must equal the topic batch calendar (exchange,
    # version and sessions). The topic quality gate also requires the full
    # 61-session role-v1 window ending on the batch date, so the 9/7 batch
    # uses a calendar shifted one weekday earlier than the default TOPIC_DAYS.
    sessions = topic_factories._weekday_sessions(end=trade_day, count=61)
    first_member_day = topic_factories.TOPIC_DAYS[0]
    cells = [
        topic_factories.coverage_cell(
            trade_date=day,
            expected_member_count=1 if day >= first_member_day else 0,
            received_member_count=1 if day >= first_member_day else 0,
        )
        for day in sessions
    ]
    topic_batch = topic_factories.topic_batch(
        trade_date=trade_day,
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions),
        coverage_from=sessions[0],
        coverage_to=trade_day,
        coverage_cells=cells,
        page_manifest={
            "request_from": sessions[0],
            "request_to": trade_day,
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
            "declared_total": 2,
            "terminal_cursor": None,
        },
    )
    topics = TopicRepository(database, clock=lambda: factories.NOW)
    topic_snapshot = topics.publish(
        topics.import_batch(topic_batch, key=f"{key}-topic").id
    )
    market = factories.batch(
        trade_date=trade_day,
        calendar=topic_batch.calendar,
        calendar_version=topic_batch.calendar_version,
        security_states=(factories.state("A", effective_from=sessions[0]),),
        bars=tuple(factories.bar(trade_date=day) for day in sessions),
    )
    market_snapshot = repository.publish(
        repository.import_batch(market, key=f"{key}-market").id
    )
    return market_snapshot, topic_snapshot


def _publish_role_day(database, repository, trade_day, market, topic, *, key):
    materializer = RoleMaterializer(database, repository, clock=lambda: factories.NOW)
    run = materializer.materialize(
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        rule_version="role-v1",
        benchmark_id="CSI300",
        key=f"{key}-run",
    )
    service = PublicationService(database, repository, clock=lambda: factories.NOW)
    publication = service.publish(
        PublicationCommand(
            trade_date=trade_day,
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            role_input_id=run.id,
            disabled_domains=("news", "regulation"),
        ),
        key=f"{key}-publication",
    )
    return run, publication


@pytest.fixture
def rotation_chain(database, repository):
    day1, day2 = factories.DAYS[-2], factories.DAYS[-1]
    market1, topic1 = _chain_inputs(database, repository, day1, key="rotation-day1")
    run1, publication1 = _publish_role_day(
        database, repository, day1, market1, topic1, key="rotation-day1"
    )
    market2, topic2 = _chain_inputs(database, repository, day2, key="rotation-day2")
    run2, publication2 = _publish_role_day(
        database, repository, day2, market2, topic2, key="rotation-day2"
    )
    roles = RoleRepository(database, repository, clock=lambda: factories.NOW)
    return SimpleNamespace(
        database=database,
        repository=repository,
        roles=roles,
        day1=day1,
        day2=day2,
        run1=run1,
        run2=run2,
        publication1=publication1,
        publication2=publication2,
        market1=market1,
        market2=market2,
        topic1=topic1,
        topic2=topic2,
    )


def _stored_role_result(chain, publication):
    persisted = PublicationRepository(chain.database, chain.repository)
    return next(
        result
        for result in persisted.list_results(publication.id)
        if result.domain == "role"
    )


def _assert_manifest(block, run, market, topic):
    manifest = block["input_manifest"]
    assert set(manifest) == _MANIFEST_KEYS
    assert manifest == {
        "run_id": str(run.id),
        "market_snapshot_id": str(market.id),
        "topic_snapshot_id": str(topic.id),
        "rule_version": "role-v1",
        "membership_version": "m1",
    }


def _archive_trend_cores(database, run_id, cores, *, members=None):
    with database.engine.begin() as connection:
        connection.execute(sql_text("ALTER TABLE role_results DISABLE TRIGGER USER"))
        connection.execute(sql_text("ALTER TABLE role_run_topics DISABLE TRIGGER USER"))
        if members is not None:
            connection.execute(
                update(role_run_topics)
                .where(role_run_topics.c.role_run_id == run_id)
                .values(member_ids=list(members))
            )
        for security_id in cores:
            connection.execute(
                insert(role_results).values(
                    id=uuid4(),
                    role_run_id=run_id,
                    topic_id="topic-a",
                    security_id=security_id,
                    observation_at=factories.NOW,
                    role="trend",
                    rule_version="role-v1",
                    internal_status="candidate",
                    tie_rank=1,
                    factors=[],
                    reasons=[],
                )
            )
        connection.execute(sql_text("ALTER TABLE role_results ENABLE TRIGGER USER"))
        connection.execute(sql_text("ALTER TABLE role_run_topics ENABLE TRIGGER USER"))


def test_pg_rotation_first_observation_then_comparable_chain(rotation_chain):
    chain = rotation_chain
    # ImmutableJsonObject hydrates JSON arrays as tuples; dump through the
    # JSON mode so the stored shape is compared exactly as it is served.
    first = _stored_role_result(chain, chain.publication1).model_dump(mode="json")[
        "payload"
    ]
    second = _stored_role_result(chain, chain.publication2).model_dump(mode="json")[
        "payload"
    ]

    assert first["rotation_version"] == 1
    assert second["rotation_version"] == 1
    block1 = first["topics"][0]["rotation"]
    block2 = second["topics"][0]["rotation"]
    assert set(block1) == _ROTATION_BLOCK_KEYS
    assert block1["at"] == chain.day1.isoformat()
    assert block1["sector_id"] == "topic-a"
    assert block1["core_ids"] == []
    assert block1["change"] == "segment_start"
    assert block1["comparable"] is False
    assert block1["reasons"] == ["first_observation"]
    _assert_manifest(block1, chain.run1, chain.market1, chain.topic1)
    assert block2["at"] == chain.day2.isoformat()
    assert block2["sector_id"] == "topic-a"
    assert block2["core_ids"] == []
    assert block2["change"] == "unchanged"
    assert block2["comparable"] is True
    assert block2["reasons"] == ["no_core_candidate"]
    _assert_manifest(block2, chain.run2, chain.market2, chain.topic2)
    # The read path recomputes from the same immutable rows without rerunning any
    # role algorithm, and every topic carries exactly one rotation block.
    assert len(first["topics"]) == len(second["topics"]) == 1
    assert chain.roles.payload(chain.run2.id)["topics"][0]["rotation"] == block2


def test_pg_rotation_payload_is_deterministic_across_repeated_reads(rotation_chain):
    chain = rotation_chain
    assert chain.roles.payload(chain.run1.id) == chain.roles.payload(chain.run1.id)
    assert chain.roles.payload(chain.run2.id) == chain.roles.payload(chain.run2.id)


def test_pg_rotation_projects_archived_trend_rank_one_cores(rotation_chain):
    chain = rotation_chain
    _archive_trend_cores(chain.database, chain.run1.id, ("A",), members=("A", "B"))
    _archive_trend_cores(chain.database, chain.run2.id, ("A", "B"), members=("A", "B"))

    block1 = chain.roles.payload(chain.run1.id)["topics"][0]["rotation"]
    block2 = chain.roles.payload(chain.run2.id)["topics"][0]["rotation"]

    assert block1["change"] == "segment_start"
    assert block1["core_ids"] == ["A"]
    assert block2["change"] == "expanded"
    assert block2["core_ids"] == ["A", "B"]
    assert block2["comparable"] is True
    assert block2["reasons"] == []


def test_pg_rotation_restarts_segment_when_membership_version_breaks(rotation_chain):
    chain = rotation_chain
    with chain.database.engine.begin() as connection:
        connection.execute(sql_text("ALTER TABLE role_runs DISABLE TRIGGER USER"))
        connection.execute(
            update(role_runs)
            .where(role_runs.c.id == chain.run2.id)
            .values(membership_version="m9")
        )
        connection.execute(sql_text("ALTER TABLE role_runs ENABLE TRIGGER USER"))

    block = chain.roles.payload(chain.run2.id)["topics"][0]["rotation"]

    assert block["change"] == "segment_start"
    assert block["comparable"] is False
    assert "membership_version_changed" in block["reasons"]
    assert block["input_manifest"]["membership_version"] == "m9"


def test_pg_rotation_fails_closed_when_core_is_outside_member_pool(rotation_chain):
    chain = rotation_chain
    # role_run_topics still archives member pool ["A"]; a "Z" trend core can
    # never be materialized by the real pipeline, so the read must fail the
    # publication transaction rather than silently emitting a bad block.
    _archive_trend_cores(chain.database, chain.run2.id, ("Z",))

    with pytest.raises(ValidationError):
        chain.roles.payload(chain.run2.id)


def test_pg_rotation_window_validates_bounds_before_storage(rotation_chain):
    chain = rotation_chain
    persisted = PublicationRepository(chain.database, chain.repository)

    # Bounds are rejected before any transaction opens, so a random anchor
    # id is enough: storage must never be reached.
    with pytest.raises(ValueError):
        persisted.list_published_window(chain.publication2.id, chain.day2, chain.day1)
    with pytest.raises(ValueError):
        persisted.list_published_window(chain.publication2.id, "2026-09-01", chain.day2)
    with pytest.raises(ValueError):
        persisted.list_published_window(chain.publication2.id, chain.day1, 9)
    with pytest.raises(ValueError):
        persisted.list_published_window(
            chain.publication2.id,
            chain.day1,
            datetime(2026, 9, 8),
        )


def test_pg_rotation_window_distinct_date_count_guard(rotation_chain, monkeypatch):
    chain = rotation_chain
    persisted = PublicationRepository(chain.database, chain.repository)
    sessions = topic_factories._weekday_sessions(end=chain.day2, count=61)

    # The two-day chain yields exactly one predecessor distinct trade date.
    rows = persisted.list_published_window(
        chain.publication2.id, sessions[0], chain.day2
    )
    assert [row.trade_date for row in rows] == [chain.day1]

    # The production guard is a module constant exercised for real: with the
    # bound tightened below the row count the read must raise rather than
    # silently truncate the timeline.
    monkeypatch.setattr(
        publication_repository, "_PUBLISHED_WINDOW_MAX_DISTINCT_DATES", 0
    )
    with pytest.raises(ValueError):
        persisted.list_published_window(chain.publication2.id, sessions[0], chain.day2)


# ---------------------------------------------------------------------------
# Slice 2 C: HTTP timeline assembly (GET /api/topics/rotation). These are
# async PostgreSQL tests; without DRAGON_TEST_DATABASE_URL the database
# fixture skips them. The module-level anyio mark is a no-op for the sync
# tests above (anyio only wraps coroutine test functions).
# ---------------------------------------------------------------------------

from httpx import ASGITransport, AsyncClient  # noqa: E402

from dragon_radar.publication.tables import publication_results  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


_HTTP_DAYS = (factories.DAYS[-3], factories.DAYS[-2], factories.DAYS[-1])
_WIRE_EVENT_KEYS = {
    "at",
    "sector_id",
    "core_ids",
    "change",
    "comparable",
    "reason",
    "input_manifest",
}
_FUTURE_DAY = date(2026, 9, 9)


def _rotation_block(day, sector, members, run, market, topic):
    scenarios = {
        (_HTTP_DAYS[0], "topic-a"): (
            ("A",),
            "segment_start",
            False,
            ["first_observation"],
        ),
        (_HTTP_DAYS[0], "topic-b"): (
            ("C",),
            "segment_start",
            False,
            ["first_observation"],
        ),
        (_HTTP_DAYS[1], "topic-a"): (("A", "B"), "expanded", True, []),
        (_HTTP_DAYS[1], "topic-b"): ((), "unchanged", True, ["no_core_candidate"]),
        (_HTTP_DAYS[2], "topic-a"): (("B",), "contracted", True, []),
        (_HTTP_DAYS[2], "topic-b"): (("C",), "unchanged", True, []),
    }
    cores, change, comparable, reasons = scenarios[(day, sector)]
    return {
        "at": day.isoformat(),
        "sector_id": sector,
        "core_ids": list(cores),
        "change": change,
        "comparable": comparable,
        "reasons": reasons,
        "input_manifest": {
            "run_id": str(run.id),
            "market_snapshot_id": str(market.id),
            "topic_snapshot_id": str(topic.id),
            "rule_version": "role-v1",
            "membership_version": "m1",
        },
    }


def _rotation_http_topic(day, sector, members, run, market, topic):
    return {
        "topic_id": sector,
        "member_ids": list(members),
        "rotation": _rotation_block(day, sector, members, run, market, topic),
        # The publication authorization layer deep-parses role payloads;
        # synthetic blocks still travel with empty validation ledgers.
        "role_validations": [],
        "role_validation_inputs": [],
    }


def _rotation_http_payload(day, entry):
    return {
        "role_run_id": str(entry.run.id),
        "trade_date": day.isoformat(),
        "rule_version": "role-v1",
        "benchmark_id": "CSI300",
        "rotation_version": 1,
        "topics": [
            _rotation_http_topic(
                day, "topic-a", ("A", "B"), entry.run, entry.market, entry.topic
            ),
            _rotation_http_topic(
                day, "topic-b", ("C",), entry.run, entry.market, entry.topic
            ),
        ],
    }


def _write_role_payload(database, publication, payload):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(payload=payload)
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )


def _set_role_flags(database, publication, flags):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(quality_flags=list(flags))
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )


def _set_role_status(database, publication, status):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(status=status)
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )


def _delete_role_result(database, publication):
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            delete(publication_results).where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )


async def _rotation_client(persisted):
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=persisted)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


async def _get_rotation(client, day, **params):
    return await client.get(
        "/api/topics/rotation",
        params={"date": day.isoformat(), **params},
    )


@pytest.fixture
def rotation_http_chain(database, repository):
    entries = {}
    for index, day in enumerate(_HTTP_DAYS):
        market, topic = _chain_inputs(
            database, repository, day, key=f"rotation-http-{index}-{day.isoformat()}"
        )
        run, publication = _publish_role_day(
            database,
            repository,
            day,
            market,
            topic,
            key=f"rotation-http-{index}-{day.isoformat()}",
        )
        entries[day] = SimpleNamespace(
            run=run,
            publication=publication,
            market=market,
            topic=topic,
        )
    persisted = PublicationRepository(database, repository)
    payloads = {
        day: _rotation_http_payload(day, entry) for day, entry in entries.items()
    }
    for day, payload in payloads.items():
        _write_role_payload(database, entries[day].publication, payload)
    return SimpleNamespace(
        database=database,
        repository=repository,
        persisted=persisted,
        days=_HTTP_DAYS,
        entries=entries,
        payloads=payloads,
    )


def _clone(payload):
    return json.loads(json.dumps(payload))


async def test_http_rotation_three_day_two_sector_timeline(rotation_http_chain):
    chain = rotation_http_chain
    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, chain.days[2])

    assert response.status_code == 200
    body = response.json()
    events = body["data"]["events"]
    assert [(event["at"], event["sector_id"]) for event in events] == [
        ("2026-09-04", "topic-a"),
        ("2026-09-04", "topic-b"),
        ("2026-09-07", "topic-a"),
        ("2026-09-07", "topic-b"),
        ("2026-09-08", "topic-a"),
        ("2026-09-08", "topic-b"),
    ]
    assert all(set(event) == _WIRE_EVENT_KEYS for event in events)
    first, _first_b, expanded, no_core, contracted, unchanged = events
    assert first["core_ids"] == ["A"]
    assert first["change"] == "segment_start"
    assert first["comparable"] is False
    assert first["reason"] == "first_observation"
    assert (
        UUID(first["input_manifest"]["run_id"]) == chain.entries[chain.days[0]].run.id
    )
    assert expanded["change"] == "expanded"
    assert expanded["core_ids"] == ["A", "B"]
    assert expanded["reason"] is None
    assert no_core["core_ids"] == []
    assert no_core["reason"] == "no_core_candidate"
    assert contracted["change"] == "contracted"
    assert contracted["core_ids"] == ["B"]
    assert contracted["reason"] is None
    assert unchanged["reason"] is None
    # The 200 wire projection must never surface a token outside the frozen
    # vocabulary (the non-null projection of the archived reason tuple).
    assert all(
        event["reason"] is None or event["reason"] in ROTATION_REASONS
        for event in events
    )
    assert body["data"]["days"] == 30
    assert body["data"]["sector"] is None
    sessions = topic_factories._weekday_sessions(end=chain.days[2], count=61)
    assert body["data"]["window"] == {
        "start": sessions[31].isoformat(),
        "end": "2026-09-08",
    }
    assert body["snapshot_id"] == str(chain.entries[chain.days[2]].publication.id)
    assert body["source"] == "role_materialization"
    assert body["rule_version"] == "role-v1"
    assert body["basis"]["benchmark"] == "CSI300"


async def test_http_rotation_days_parameter_clamps_window(rotation_http_chain):
    chain = rotation_http_chain
    async with await _rotation_client(chain.persisted) as client:
        two_days = await _get_rotation(client, chain.days[2], days=2)
        one_day = await _get_rotation(client, chain.days[2], days=1)
        defaulted = await _get_rotation(client, chain.days[2])

    two_body = two_days.json()
    assert [
        (event["at"], event["sector_id"]) for event in two_body["data"]["events"]
    ] == [
        ("2026-09-07", "topic-a"),
        ("2026-09-07", "topic-b"),
        ("2026-09-08", "topic-a"),
        ("2026-09-08", "topic-b"),
    ]
    assert two_body["data"]["window"]["start"] == "2026-09-07"
    one_body = one_day.json()
    assert [event["at"] for event in one_body["data"]["events"]] == [
        "2026-09-08",
        "2026-09-08",
    ]
    assert one_body["data"]["window"] == {
        "start": "2026-09-08",
        "end": "2026-09-08",
    }
    assert defaulted.json()["data"]["days"] == 30


@pytest.mark.parametrize(
    "query",
    [
        "",
        "date=2026-09-08&days=0",
        "date=2026-09-08&days=251",
        "date=2026-09-08&days=abc",
        "date=2026-09-08&days=-1",
        "date=2026-09-08&unknown=1",
        "date=2026-09-08&sector=topic-a&sector=topic-b",
        "date=2026-09-08&snapshot_id=not-a-uuid",
        "date=2026-9-8",
        "date=2026-09-08&sector=",
    ],
)
async def test_http_rotation_parameters_fail_before_storage_access(query):
    class NoStorage:
        database = type("Database", (), {"ready": lambda self: True})()
        calls = []

        def get_snapshot(self, value):
            self.calls.append(value)

        def list_snapshots(self, **values):
            self.calls.append(values)
            return []

    repository = NoStorage()
    async with await _rotation_client(repository) as client:
        response = await client.get(f"/api/topics/rotation?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert repository.calls == []


async def test_http_rotation_sector_filter_and_unknown_sector(rotation_http_chain):
    chain = rotation_http_chain
    async with await _rotation_client(chain.persisted) as client:
        filtered = await _get_rotation(client, chain.days[2], sector="topic-a")
        unknown = await _get_rotation(client, chain.days[2], sector="topic-z")

    filtered_body = filtered.json()
    assert filtered.status_code == 200
    assert filtered_body["data"]["sector"] == "topic-a"
    assert [
        (event["at"], event["sector_id"]) for event in filtered_body["data"]["events"]
    ] == [
        ("2026-09-04", "topic-a"),
        ("2026-09-07", "topic-a"),
        ("2026-09-08", "topic-a"),
    ]
    unknown_body = unknown.json()
    assert unknown.status_code == 200
    assert unknown_body["data"]["events"] == []
    assert unknown_body["data"]["sector"] == "topic-z"
    assert unknown_body["data"]["window"]["end"] == "2026-09-08"


async def test_http_rotation_anchor_sequence_pins_prior_day_versions(
    rotation_http_chain,
):
    chain = rotation_http_chain
    database = chain.database
    day1, _day2, day3 = chain.days
    first = chain.entries[day1]

    # A v2 of the oldest day is published *after* the newest day, so its
    # sequence is greater than the anchor sequence: point-in-time window
    # reads must never let it flow back into timelines anchored earlier.
    republished = PublicationService(
        database, chain.repository, clock=lambda: factories.NOW
    ).publish(
        PublicationCommand(
            trade_date=day1,
            market_snapshot_id=first.market.id,
            topic_snapshot_id=first.topic.id,
            role_input_id=first.run.id,
            disabled_domains=("news", "regulation"),
        ),
        key="rotation-http-republish-2026-09-04",
    )
    v2_payload = _clone(chain.payloads[day1])
    v2_block = v2_payload["topics"][0]["rotation"]
    v2_block["core_ids"] = ["A", "B"]
    v2_block["change"] = "expanded"
    v2_block["comparable"] = True
    v2_block["reasons"] = []
    _write_role_payload(database, republished, v2_payload)

    async with await _rotation_client(chain.persisted) as client:
        anchored = await _get_rotation(client, day3)
        pinned = await _get_rotation(
            client, day3, snapshot_id=chain.entries[day3].publication.id
        )
        old_version = await _get_rotation(
            client,
            day1,
            snapshot_id=first.publication.id,
        )
        current_version = await _get_rotation(client, day1)

    anchored_body = anchored.json()
    day1_events = [
        event
        for event in anchored_body["data"]["events"]
        if event["at"] == "2026-09-04"
    ]
    assert len(day1_events) == 2
    assert day1_events[0]["sector_id"] == "topic-a"
    assert day1_events[0]["core_ids"] == ["A"]
    assert pinned.json()["data"]["events"] == anchored_body["data"]["events"]

    old_body = old_version.json()
    assert old_body["snapshot_id"] == str(first.publication.id)
    assert len(old_body["data"]["events"]) == 2
    assert old_body["data"]["events"][0]["core_ids"] == ["A"]
    old_sessions = topic_factories._weekday_sessions(end=day1, count=61)
    assert old_body["data"]["window"] == {
        "start": old_sessions[31].isoformat(),
        "end": "2026-09-04",
    }

    current_body = current_version.json()
    assert current_body["snapshot_id"] == str(republished.id)
    assert current_body["data"]["events"][0]["sector_id"] == "topic-a"
    assert current_body["data"]["events"][0]["core_ids"] == ["A", "B"]


async def test_http_rotation_future_publication_never_flows_back(rotation_http_chain):
    chain = rotation_http_chain
    sessions = topic_factories._weekday_sessions(end=_FUTURE_DAY, count=61)
    future_batch = factories.batch(
        trade_date=_FUTURE_DAY,
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions),
        calendar_version="c1",
        security_states=(factories.state("A", effective_from=sessions[0]),),
        bars=tuple(
            factories.bar(
                trade_date=day,
                available_at=datetime(day.year, day.month, day.day, 11, tzinfo=UTC),
            )
            for day in sessions
        ),
    )
    future_snapshot = chain.repository.publish(
        chain.repository.import_batch(
            future_batch, key="rotation-http-2026-09-09-market"
        ).id
    )
    PublicationService(
        chain.database, chain.repository, clock=lambda: factories.NOW
    ).publish(
        PublicationCommand(
            trade_date=_FUTURE_DAY,
            market_snapshot_id=future_snapshot.id,
            disabled_domains=("news", "regulation"),
        ),
        key="rotation-http-2026-09-09-publication",
    )

    async with await _rotation_client(chain.persisted) as client:
        timeline = await _get_rotation(client, chain.days[2])
        future_day = await _get_rotation(client, _FUTURE_DAY)

    body = timeline.json()
    assert [(event["at"], event["sector_id"]) for event in body["data"]["events"]] == [
        ("2026-09-04", "topic-a"),
        ("2026-09-04", "topic-b"),
        ("2026-09-07", "topic-a"),
        ("2026-09-07", "topic-b"),
        ("2026-09-08", "topic-a"),
        ("2026-09-08", "topic-b"),
    ]
    assert body["data"]["window"]["end"] == "2026-09-08"
    future_body = future_day.json()
    assert future_day.status_code == 200
    assert future_body["data"] is None
    assert "role_missing" in future_body["quality_flags"]


async def test_http_rotation_skips_middle_day_without_rotation_marker(
    rotation_http_chain,
):
    chain = rotation_http_chain
    _day1, day2, day3 = chain.days
    stale_payload = _clone(chain.payloads[day2])
    del stale_payload["rotation_version"]
    _write_role_payload(chain.database, chain.entries[day2].publication, stale_payload)
    _set_role_flags(
        chain.database, chain.entries[day2].publication, ["rotation_q_middle"]
    )
    _set_role_flags(
        chain.database, chain.entries[day3].publication, ["rotation_q_anchor"]
    )

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    body = response.json()
    assert response.status_code == 200
    assert [(event["at"], event["sector_id"]) for event in body["data"]["events"]] == [
        ("2026-09-04", "topic-a"),
        ("2026-09-04", "topic-b"),
        ("2026-09-08", "topic-a"),
        ("2026-09-08", "topic-b"),
    ]
    assert "rotation_q_anchor" in body["quality_flags"]
    assert "rotation_q_middle" not in body["quality_flags"]


async def test_http_rotation_anchor_without_marker_is_data_unavailable(
    rotation_http_chain,
):
    chain = rotation_http_chain
    day3 = chain.days[2]
    stale_payload = _clone(chain.payloads[day3])
    del stale_payload["rotation_version"]
    _write_role_payload(chain.database, chain.entries[day3].publication, stale_payload)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


def _set_change(block, *_args):
    block["change"] = "moon"


def _set_at(block, *_args):
    block["at"] = "2020-01-01"


def _set_foreign_core(block, *_args):
    block["core_ids"] = ["A", "Z"]


def _set_incomparable_pair(block, *_args):
    block["change"] = "segment_start"
    block["comparable"] = True


def _set_foreign_run(block, *_args):
    block["input_manifest"]["run_id"] = str(uuid4())


def _add_extra_key(block, *_args):
    block["unexpected"] = True


def _set_bad_manifest_uuid(block, *_args):
    block["input_manifest"]["market_snapshot_id"] = "not-a-uuid"


def _set_comparable_break_reason(block, *_args):
    block["reasons"] = ["first_observation"]


def _empty_segment_reasons(block, *_args):
    block["change"] = "segment_start"
    block["comparable"] = False
    block["reasons"] = []


def _set_comparable_as_int(block, *_args):
    # int 1 must not be accepted in place of the JSON boolean true.
    block["comparable"] = 1


def _set_non_string_core(block, *_args):
    block["core_ids"] = ["A", 2]


def _set_manifest_null_uuid(block, *_args):
    block["input_manifest"]["market_snapshot_id"] = None


def _set_fake_reason(block, *_args):
    # segment_start with a non-empty, non-whitelisted token: this passes the
    # old cross-field shape checks, so only the allowlist guard can stop it.
    block["reasons"] = ["totally_fake"]


def _set_multiple_break_reasons(block, *_args):
    block["change"] = "segment_start"
    block["comparable"] = False
    block["reasons"] = ["membership_version_changed", "member_pool_changed"]


def _duplicate_topic(payload):
    payload["topics"].append(_clone(payload["topics"][0]))


@pytest.mark.parametrize(
    ("corrupt_block", "corrupt_payload"),
    [
        (_set_change, None),
        (_set_at, None),
        (_set_foreign_core, None),
        (_set_incomparable_pair, None),
        (_set_foreign_run, None),
        (_add_extra_key, None),
        (_set_bad_manifest_uuid, None),
        (_set_comparable_break_reason, None),
        (_empty_segment_reasons, None),
        (_set_comparable_as_int, None),
        (_set_non_string_core, None),
        (_set_manifest_null_uuid, None),
        (None, _duplicate_topic),
    ],
)
async def test_http_rotation_corrupt_middle_day_block_fails_closed(
    rotation_http_chain, corrupt_block, corrupt_payload
):
    chain = rotation_http_chain
    day2 = chain.days[1]
    broken = _clone(chain.payloads[day2])
    if corrupt_block is not None:
        corrupt_block(broken["topics"][0]["rotation"])
    if corrupt_payload is not None:
        corrupt_payload(broken)
    _write_role_payload(chain.database, chain.entries[day2].publication, broken)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, chain.days[2])

    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


async def test_http_rotation_unknown_date_returns_no_data_envelope(
    rotation_http_chain,
):
    chain = rotation_http_chain
    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, date(2025, 1, 1))

    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["snapshot_id"] is None
    assert body["quality_flags"] == ["no_data"]


async def test_http_rotation_fake_reason_in_frozen_block_fails_closed(
    rotation_http_chain,
):
    # The oldest day is a segment_start block; swapping its whitelisted
    # reason for a fabricated token must fail closed at the frozen-block
    # validation instead of serving the token on the 200 timeline.
    chain = rotation_http_chain
    day1 = chain.days[0]
    broken = _clone(chain.payloads[day1])
    _set_fake_reason(broken["topics"][0]["rotation"])
    _write_role_payload(chain.database, chain.entries[day1].publication, broken)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, chain.days[2])

    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


@pytest.mark.parametrize(
    "bad_version", [2, "1", True], ids=["future_int", "string", "bool"]
)
async def test_http_rotation_anchor_requires_exact_rotation_version_one(
    rotation_http_chain, bad_version
):
    # bool True compares equal to int 1 in Python, so the guard must pin
    # both type and value; a string tag or future version fail likewise.
    chain = rotation_http_chain
    day3 = chain.days[2]
    broken = _clone(chain.payloads[day3])
    broken["rotation_version"] = bad_version
    _write_role_payload(chain.database, chain.entries[day3].publication, broken)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


async def test_http_rotation_skips_history_day_with_other_rotation_version(
    rotation_http_chain,
):
    # A non-v1 history day is a pre/post-contract publication: it is skipped
    # wholesale (its blocks are never validated) and never backfilled.
    chain = rotation_http_chain
    _day1, day2, day3 = chain.days
    other_version = _clone(chain.payloads[day2])
    other_version["rotation_version"] = 2
    # Deliberately corrupt the block shape: a skipped day must not reach
    # frozen-block validation at all.
    other_version["topics"][0]["rotation"].pop("at")
    _write_role_payload(chain.database, chain.entries[day2].publication, other_version)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    assert response.status_code == 200
    body = response.json()
    assert [(event["at"], event["sector_id"]) for event in body["data"]["events"]] == [
        ("2026-09-04", "topic-a"),
        ("2026-09-04", "topic-b"),
        ("2026-09-08", "topic-a"),
        ("2026-09-08", "topic-b"),
    ]


async def test_http_rotation_multiple_break_reasons_project_first_token_only(
    rotation_http_chain,
):
    # The wire contract projects reasons[0] onto a single nullable "reason";
    # the internal reason tuple is never echoed as a "reasons" array.
    chain = rotation_http_chain
    day1 = chain.days[0]
    broken = _clone(chain.payloads[day1])
    _set_multiple_break_reasons(broken["topics"][0]["rotation"])
    _write_role_payload(chain.database, chain.entries[day1].publication, broken)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, chain.days[2], sector="topic-a")

    assert response.status_code == 200
    first = response.json()["data"]["events"][0]
    assert first["at"] == "2026-09-04"
    assert first["reason"] == "membership_version_changed"
    assert "reasons" not in first
    assert set(first) == _WIRE_EVENT_KEYS


async def test_http_rotation_disabled_role_domain_is_unavailable(rotation_http_chain):
    chain = rotation_http_chain
    day3 = chain.days[2]
    _set_role_status(chain.database, chain.entries[day3].publication, "disabled")

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["source"] == "role_materialization"
    assert "role_disabled" in body["quality_flags"]


async def test_http_rotation_missing_role_result_domain_is_unavailable(
    rotation_http_chain,
):
    chain = rotation_http_chain
    day3 = chain.days[2]
    _delete_role_result(chain.database, chain.entries[day3].publication)

    async with await _rotation_client(chain.persisted) as client:
        response = await _get_rotation(client, day3)

    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["source"] == "role_materialization"
    assert "role_missing" in body["quality_flags"]
