from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from factories import DAYS, NOW
from pydantic import ValidationError
from topic_factories import (
    TOPIC_DAYS,
    coverage_cell,
    topic_batch,
    topic_batch_dict,
    topic_definition,
    topic_membership,
)

from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.topics.models import (
    TopicBatch,
    TopicImportRecord,
    TopicSnapshot,
    TopicSnapshotPage,
)


def archived_topic_record(model, field, value):
    nested = {"nested": {"items": [["value", value]]}}
    if model is TopicImportRecord:
        return TopicImportRecord(
            id=UUID(int=1),
            canonical_json="{}",
            payload_hash="a" * 64,
            quality_report=nested,
        )
    return TopicSnapshot(
        id=UUID(int=2),
        batch_id=UUID(int=1),
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest=nested if field == "manifest" else {},
        batch=topic_batch(),
        quality_report=nested if field == "quality_report" else {},
    )


def issue_codes(payload, *, now=NOW):
    from dragon_radar.topics.quality import validate_topic_batch

    return {
        issue.code
        for issue in validate_topic_batch(
            TopicBatch.model_validate(payload), now=now
        ).issues
    }


def test_topic_models_are_frozen_and_reject_unknown_fields():
    batch = topic_batch()
    with pytest.raises(ValidationError, match="frozen"):
        batch.taxonomy_version = "changed"
    payload = topic_batch_dict(unexpected=True)
    with pytest.raises(ValidationError, match="extra"):
        TopicBatch.model_validate(payload)
    with pytest.raises(ValidationError, match="extra"):
        TopicSnapshotPage(items=(), page_size=20, next_cursor=None, extra=True)


@pytest.mark.parametrize(
    "record,field",
    [
        (
            TopicImportRecord(
                id=UUID(int=1),
                canonical_json="{}",
                payload_hash="a" * 64,
                quality_report={"nested": {"items": [1.25, {"status": "clean"}]}},
            ),
            "quality_report",
        ),
        (
            TopicSnapshot(
                id=UUID(int=2),
                batch_id=UUID(int=1),
                revision=1,
                supersedes=None,
                published_at=NOW,
                manifest={"nested": {"items": [1, {"status": "clean"}]}},
                batch=topic_batch(),
                quality_report={},
            ),
            "manifest",
        ),
        (
            TopicSnapshot(
                id=UUID(int=2),
                batch_id=UUID(int=1),
                revision=1,
                supersedes=None,
                published_at=NOW,
                manifest={},
                batch=topic_batch(),
                quality_report={"nested": {"items": [1, {"status": "clean"}]}},
            ),
            "quality_report",
        ),
    ],
)
def test_archived_topic_json_is_recursively_immutable_and_round_trips(record, field):
    value = getattr(record, field)
    assert type(record).model_json_schema()["properties"][field]["type"] == "object"
    assert isinstance(value, Mapping)
    assert isinstance(value["nested"], Mapping)
    assert isinstance(value["nested"]["items"], tuple)
    with pytest.raises(TypeError):
        value["nested"]["items"][1]["status"] = "changed"

    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored.model_dump(mode="json") == record.model_dump(mode="json")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    ("model", "field"),
    [
        (TopicSnapshot, "quality_report"),
        (TopicSnapshot, "manifest"),
        (TopicImportRecord, "quality_report"),
    ],
)
def test_archived_topic_json_rejects_nested_nonfinite_floats(model, field, value):
    with pytest.raises(ValidationError, match="finite"):
        archived_topic_record(model, field, value)


def test_topic_batch_requires_complete_coverage_at_a_known_cutoff():
    payload = topic_batch_dict()
    payload["coverage_cells"][0]["complete_as_of"] = None
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_topic_batch_requires_aware_availability():
    payload = topic_batch_dict()
    payload["topics"][0]["available_at"] = datetime(2026, 9, 2)
    with pytest.raises(ValidationError, match="timezone"):
        TopicBatch.model_validate(payload)


def test_topic_batch_rejects_overlapping_membership_intervals():
    payload = topic_batch_dict()
    payload["memberships"].append(dict(payload["memberships"][0]))
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_effective_intervals_are_half_open_and_nonempty():
    payload = topic_batch_dict(
        memberships=[
            topic_membership(effective_to=DAYS[2]),
            topic_membership(effective_from=DAYS[2]),
        ]
    )
    assert len(TopicBatch.model_validate(payload).memberships) == 2
    payload["memberships"][0]["effective_to"] = TOPIC_DAYS[0]
    with pytest.raises(ValidationError, match="interval"):
        TopicBatch.model_validate(payload)


def test_topic_batch_with_exactly_61_sessions_is_publishable():
    from dragon_radar.domain.quality import QualityReport
    from dragon_radar.topics import quality

    data = topic_batch()
    report = quality.validate_topic_batch(data, now=NOW)
    assert getattr(quality, "ROLE_V1_MAX_WINDOW_SESSION_COUNT", None) == 61
    assert isinstance(report, QualityReport)
    assert isinstance(report.batch, TopicBatch)
    assert report.batch is data
    assert type(report).model_validate_json(report.model_dump_json()) == report
    assert report.publishable
    assert report.coverage.expected == 61
    assert report.coverage.valid == 61
    assert report.coverage.legal_excluded == report.coverage.missing == 0


def test_topic_batch_rejects_four_sessions_for_role_v1_windows():
    payload = topic_batch_dict(
        trade_date=DAYS[-1],
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=DAYS),
        coverage_from=DAYS[0],
        coverage_to=DAYS[-1],
        coverage_cells=[coverage_cell(trade_date=day) for day in DAYS],
    )
    payload["page_manifest"].update(request_from=DAYS[0], request_to=DAYS[-1])

    assert "role_window_coverage" in issue_codes(payload)


def test_topic_batch_role_window_coverage_may_start_earlier():
    earlier = TOPIC_DAYS[0] - timedelta(days=1)
    payload = topic_batch_dict(
        calendar=ExchangeCalendar(
            exchange="TEST",
            version="c1",
            sessions=(earlier, *TOPIC_DAYS),
        ),
        coverage_from=earlier,
        coverage_cells=[
            coverage_cell(
                trade_date=earlier,
                expected_member_count=0,
                received_member_count=0,
            ),
            *topic_batch_dict()["coverage_cells"],
        ],
    )
    payload["page_manifest"]["request_from"] = earlier

    assert issue_codes(payload) == set()


def test_topic_batch_rejects_coverage_starting_after_role_v1_window():
    payload = topic_batch_dict(
        coverage_from=TOPIC_DAYS[1],
        coverage_cells=topic_batch_dict()["coverage_cells"][1:],
    )
    payload["page_manifest"]["request_from"] = TOPIC_DAYS[1]

    assert "role_window_coverage" in issue_codes(payload)


@pytest.mark.parametrize(
    "change,code",
    [
        (
            {"coverage_from": TOPIC_DAYS[0] - timedelta(days=1)},
            "coverage_range",
        ),
        (
            {"coverage_to": TOPIC_DAYS[-1] + timedelta(days=1)},
            "coverage_range",
        ),
        (
            {"trade_date": TOPIC_DAYS[0] - timedelta(days=1)},
            "trade_date",
        ),
        ({"trade_date": DAYS[1] + timedelta(days=1)}, "trade_date"),
        ({"exchange": "OTHER"}, "calendar_mismatch"),
        ({"calendar_version": "c2"}, "calendar_mismatch"),
    ],
)
def test_topic_batch_calendar_contract(change, code):
    assert code in issue_codes(topic_batch_dict(**change))


def test_topic_batch_requires_each_topic_session_cell_exactly_once():
    payload = topic_batch_dict()
    payload["coverage_cells"].pop()
    assert "missing_coverage" in issue_codes(payload)
    payload = topic_batch_dict()
    payload["coverage_cells"].append(dict(payload["coverage_cells"][0]))
    assert "duplicate_coverage" in issue_codes(payload)


def test_missing_coverage_issues_are_sorted_by_topic_and_trade_date():
    from dragon_radar.topics.quality import validate_topic_batch

    topic_ids = ("topic-z", "topic-a", "topic-m")
    missing_keys = {
        ("topic-z", TOPIC_DAYS[1]),
        ("topic-a", TOPIC_DAYS[-1]),
        ("topic-m", TOPIC_DAYS[2]),
        ("topic-a", TOPIC_DAYS[0]),
        ("topic-z", TOPIC_DAYS[-2]),
        ("topic-m", TOPIC_DAYS[-3]),
    }
    payload = topic_batch_dict(
        topics=[topic_definition(topic_id) for topic_id in topic_ids],
        memberships=[
            topic_membership(topic_id, security_id=topic_id) for topic_id in topic_ids
        ],
        coverage_cells=[
            coverage_cell(topic_id=topic_id, trade_date=day)
            for topic_id in topic_ids
            for day in TOPIC_DAYS
            if (topic_id, day) not in missing_keys
        ],
    )

    report = validate_topic_batch(TopicBatch.model_validate(payload), now=NOW)

    messages = [
        issue.message for issue in report.issues if issue.code == "missing_coverage"
    ]
    assert messages == [
        f"Missing coverage cell: {topic_id} {day}"
        for topic_id, day in sorted(missing_keys)
    ]


@pytest.mark.parametrize(
    "cell_change,code",
    [
        ({"received_member_count": 0}, "coverage_count"),
        ({"expected_member_count": 2}, "coverage_count"),
        ({"complete_as_of": NOW + timedelta(seconds=1)}, "coverage_time"),
        ({"status": "unavailable", "reason": None}, "unavailable_reason"),
    ],
)
def test_topic_coverage_cells_are_exact_and_time_bounded(cell_change, code):
    payload = topic_batch_dict()
    payload["coverage_cells"][-1].update(cell_change)
    assert code in issue_codes(payload)


def test_reasoned_unavailable_coverage_is_publishable_but_not_complete():
    payload = topic_batch_dict()
    payload["coverage_cells"][-1].update(status="unavailable", reason="provider outage")
    from dragon_radar.topics.quality import validate_topic_batch

    report = validate_topic_batch(TopicBatch.model_validate(payload), now=NOW)
    assert report.publishable
    assert report.issues == ()
    assert report.coverage.valid == 60
    assert report.coverage.missing == 1


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("source_id", "other", "grant_denied"),
        ("source_time", NOW + timedelta(seconds=1), "source_time"),
        ("as_of", NOW + timedelta(seconds=1), "batch_time"),
    ],
)
def test_topic_batch_rejects_source_and_future_cutoff_errors(field, value, code):
    assert code in issue_codes(topic_batch_dict(**{field: value}))


def test_topic_batch_trade_date_uses_shanghai_not_utc_calendar_day():
    cutoff = datetime(2026, 9, 7, 16, tzinfo=UTC)  # Sep 8 00:00 in Shanghai
    payload = topic_batch_dict(as_of=cutoff, source_time=cutoff)
    for cell in payload["coverage_cells"]:
        cell["complete_as_of"] = cutoff

    assert issue_codes(payload, now=cutoff) == set()


def test_topic_batch_rejects_trade_date_after_shanghai_cutoff_date():
    future_sessions = (
        *TOPIC_DAYS,
        DAYS[-1] + timedelta(days=1),
        DAYS[-1] + timedelta(days=2),
    )
    completion = datetime(2026, 9, 8, 12, tzinfo=UTC)
    payload = topic_batch_dict(
        trade_date=future_sessions[-1],
        calendar=ExchangeCalendar(
            exchange="TEST", version="c1", sessions=future_sessions
        ),
        coverage_to=future_sessions[-1],
        coverage_cells=[
            coverage_cell(trade_date=day, complete_as_of=completion)
            for day in future_sessions
        ],
    )
    payload["page_manifest"]["request_to"] = future_sessions[-1]

    assert "batch_time" in issue_codes(payload)


def test_topic_coverage_rejects_completion_before_shanghai_trade_date():
    payload = topic_batch_dict()
    payload["coverage_cells"][-1]["complete_as_of"] = datetime(
        2026, 9, 7, 15, 59, tzinfo=UTC
    )

    assert "coverage_time" in issue_codes(payload)


@pytest.mark.parametrize("record", ["topics", "memberships"])
def test_topic_records_cannot_arrive_after_batch_or_validation_cutoff(record):
    payload = topic_batch_dict()
    payload[record][0]["available_at"] = NOW + timedelta(seconds=1)
    assert "future_availability" in issue_codes(payload)
    assert "future_availability" in issue_codes(
        topic_batch_dict(
            **{
                record: [
                    (topic_definition if record == "topics" else topic_membership)(
                        available_at=NOW - timedelta(days=7)
                    )
                ]
            }
        ),
        now=NOW - timedelta(days=8),
    )


def test_memberships_must_reference_declared_topics():
    payload = topic_batch_dict(memberships=[topic_membership(topic_id="unknown")])
    assert "unknown_topic" in issue_codes(payload)


def test_coverage_counts_members_known_by_complete_as_of_without_duplicates():
    payload = topic_batch_dict(
        memberships=[
            topic_membership(available_at=datetime(2026, 9, 8, 18, tzinfo=UTC)),
            topic_membership(
                security_id="B",
                available_at=datetime(2026, 9, 8, 18, tzinfo=UTC),
            ),
        ]
    )
    payload["coverage_cells"][-1].update(
        expected_member_count=2, received_member_count=2
    )
    assert "coverage_count" in issue_codes(payload)


def test_coverage_excludes_members_after_topic_definition_expires():
    payload = topic_batch_dict(
        topics=[topic_definition(effective_to=TOPIC_DAYS[-1])]
    )
    payload["coverage_cells"][-1].update(
        expected_member_count=0,
        received_member_count=0,
    )

    codes = issue_codes(payload)

    assert "coverage_count" not in codes
    assert "membership_interval" in codes


def test_coverage_excludes_members_when_topic_is_unknown_at_completion():
    known_after_completion = datetime(2026, 9, 8, 18, tzinfo=UTC)
    payload = topic_batch_dict(
        topics=[topic_definition(available_at=known_after_completion)]
    )
    for cell in payload["coverage_cells"]:
        cell.update(expected_member_count=0, received_member_count=0)

    assert "coverage_count" not in issue_codes(payload)


@pytest.mark.parametrize(
    "membership_change",
    [
        {"effective_from": TOPIC_DAYS[0] - timedelta(days=1)},
        {"effective_to": TOPIC_DAYS[-1] + timedelta(days=1)},
    ],
)
def test_membership_interval_must_be_contained_by_topic_definition(
    membership_change,
):
    payload = topic_batch_dict(
        topics=[topic_definition(effective_to=TOPIC_DAYS[-1])],
        memberships=[topic_membership(**membership_change)],
    )
    payload["coverage_cells"][-1].update(
        expected_member_count=0,
        received_member_count=0,
    )

    from dragon_radar.topics.quality import validate_topic_batch

    report = validate_topic_batch(TopicBatch.model_validate(payload), now=NOW)

    assert "membership_interval" in {issue.code for issue in report.issues}
    assert not report.publishable


def test_membership_effective_to_may_equal_topic_effective_to():
    effective_to = TOPIC_DAYS[-1]
    payload = topic_batch_dict(
        topics=[topic_definition(effective_to=effective_to)],
        memberships=[topic_membership(effective_to=effective_to)],
    )
    payload["coverage_cells"][-1].update(
        expected_member_count=0,
        received_member_count=0,
    )

    assert issue_codes(payload) == set()


def test_coverage_member_index_preserves_interval_and_availability_semantics():
    from dragon_radar.topics.quality import validate_topic_batch

    payload = topic_batch_dict(
        memberships=[
            topic_membership(effective_to=TOPIC_DAYS[-1]),
            topic_membership(effective_from=TOPIC_DAYS[-1]),
            topic_membership(
                security_id="B",
                effective_from=TOPIC_DAYS[-1],
                available_at=datetime(2026, 9, 8, 18, tzinfo=UTC),
            ),
        ]
    )
    payload["page_manifest"]["pages"][1]["received_row_count"] = 3
    payload["page_manifest"]["declared_total"] = 4

    report = validate_topic_batch(TopicBatch.model_validate(payload), now=NOW)

    assert report.publishable
    assert report.coverage.valid == report.coverage.expected == len(TOPIC_DAYS)


def test_coverage_member_index_keeps_security_ids_scoped_to_each_topic():
    from dragon_radar.topics.quality import validate_topic_batch

    payload = topic_batch_dict(
        topics=[topic_definition(), topic_definition(topic_id="topic-b")],
        memberships=[
            topic_membership(),
            topic_membership(topic_id="topic-b"),
        ],
        coverage_cells=[
            *topic_batch_dict()["coverage_cells"],
            *[coverage_cell(topic_id="topic-b", trade_date=day) for day in TOPIC_DAYS],
        ],
    )
    payload["page_manifest"]["pages"][1]["received_row_count"] = 3
    payload["page_manifest"]["declared_total"] = 4

    report = validate_topic_batch(
        TopicBatch.model_validate(payload),
        now=NOW,
    )

    assert report.publishable
    assert report.coverage.valid == report.coverage.expected == 2 * len(TOPIC_DAYS)


def test_topic_quality_indexes_memberships_before_evaluating_cells():
    from dragon_radar.topics.quality import validate_topic_batch

    class IterationCountingMemberships(tuple):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    memberships = IterationCountingMemberships(topic_batch().memberships)
    data = topic_batch().model_copy(update={"memberships": memberships})

    validate_topic_batch(data, now=NOW)

    assert memberships.iterations <= 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["pages"].__setitem__(
            1, {**manifest["pages"][1], "request_cursor": "gap"}
        ),
        lambda manifest: manifest["pages"].__setitem__(
            1, {**manifest["pages"][1], "page_id": "page-1"}
        ),
        lambda manifest: manifest["pages"].__setitem__(
            1, {**manifest["pages"][1], "page_hash": "not-a-hash"}
        ),
        lambda manifest: manifest["pages"].__setitem__(
            1, {**manifest["pages"][1], "received_row_count": 2}
        ),
        lambda manifest: manifest.update(declared_total=3),
        lambda manifest: manifest.update(terminal_cursor="unexpected"),
        lambda manifest: manifest.update(request_to=DAYS[-2]),
    ],
)
def test_topic_batch_requires_a_contiguous_page_manifest(mutate):
    payload = topic_batch_dict()
    payload["page_manifest"]["pages"] = list(payload["page_manifest"]["pages"])
    mutate(payload["page_manifest"])
    try:
        batch = TopicBatch.model_validate(payload)
    except ValidationError:
        return
    from dragon_radar.topics.quality import validate_topic_batch

    assert "page_manifest" in {
        issue.code for issue in validate_topic_batch(batch, now=NOW).issues
    }


def test_topic_batch_rejects_a_cursor_cycle():
    payload = topic_batch_dict()
    payload["page_manifest"]["pages"] = [
        payload["page_manifest"]["pages"][0],
        {
            **payload["page_manifest"]["pages"][1],
            "next_cursor": "cursor-2",
            "received_row_count": 0,
        },
        {
            "page_id": "page-3",
            "request_cursor": "cursor-2",
            "next_cursor": None,
            "page_hash": "c" * 64,
            "received_row_count": 1,
        },
    ]
    assert "page_manifest" in issue_codes(payload)


def test_topic_batch_rejects_restart_after_an_early_terminal_page():
    payload = topic_batch_dict()
    payload["page_manifest"]["pages"] = [
        payload["page_manifest"]["pages"][0],
        {
            **payload["page_manifest"]["pages"][1],
            "next_cursor": None,
            "received_row_count": 0,
        },
        {
            "page_id": "page-3",
            "request_cursor": None,
            "next_cursor": "terminal",
            "page_hash": "c" * 64,
            "received_row_count": 1,
        },
    ]
    payload["page_manifest"]["terminal_cursor"] = "terminal"
    assert "page_manifest" in issue_codes(payload)


def test_topic_batch_rejects_duplicate_page_content():
    payload = topic_batch_dict()
    payload["page_manifest"]["pages"][1]["page_hash"] = "a" * 64
    assert "page_manifest" in issue_codes(payload)


def test_security_state_new_fields_default_to_unknown_for_old_archives():
    from factories import state

    from dragon_radar.domain.models import SecurityState

    payload = state().model_dump(
        exclude={"security_type", "board_category", "is_delisting"}
    )
    restored = SecurityState.model_validate(payload)
    assert (
        restored.security_type
        is restored.board_category
        is restored.is_delisting
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        "bad\x00ref",
        "bad\u0085ref",
        "javascript:alert(1)",
        "data:text/plain,x",
        "file:///tmp/evidence.json",
    ],
)
def test_source_reference_rejects_control_text_and_dangerous_protocols(value):
    payload = topic_batch_dict(topics=[topic_definition(source_reference=value)])
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_source_reference_rejects_whitespace_only():
    payload = topic_batch_dict(topics=[topic_definition(source_reference="   ")])
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


@pytest.mark.parametrize(
    "value",
    [
        "bad\x00ref",
        "bad\u0085ref",
        "javascript:alert(1)",
        "data:text/plain,x",
        "file:///tmp/evidence.json",
    ],
)
def test_evidence_refs_reject_control_text_and_dangerous_protocols(value):
    payload = topic_batch_dict(memberships=[topic_membership(evidence_refs=(value,))])
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_evidence_refs_reject_whitespace_only():
    payload = topic_batch_dict(memberships=[topic_membership(evidence_refs=("   ",))])
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_evidence_refs_reject_empty_tuple():
    payload = topic_batch_dict(memberships=[topic_membership(evidence_refs=())])
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_evidence_refs_accept_single_safe_reference_as_tuple():
    payload = topic_batch_dict(
        memberships=[topic_membership(evidence_refs=("synthetic:membership-a",))]
    )

    membership = TopicBatch.model_validate(payload).memberships[0]

    assert membership.evidence_refs == ("synthetic:membership-a",)
    assert isinstance(membership.evidence_refs, tuple)
