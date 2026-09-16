from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from factories import DAYS, NOW
from pydantic import ValidationError
from topic_factories import (
    topic_batch,
    topic_batch_dict,
    topic_definition,
    topic_membership,
)

from dragon_radar.topics.models import (
    TopicBatch,
    TopicImportRecord,
    TopicSnapshot,
    TopicSnapshotPage,
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
                quality_report={"nested": {"items": [1, {"status": "clean"}]}},
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
def test_archived_topic_json_is_recursively_immutable_and_round_trips(
    record, field
):
    value = getattr(record, field)
    assert isinstance(value, Mapping)
    assert isinstance(value["nested"], Mapping)
    assert isinstance(value["nested"]["items"], tuple)
    with pytest.raises(TypeError):
        value["nested"]["items"][1]["status"] = "changed"

    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored.model_dump(mode="json") == record.model_dump(mode="json")


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
    payload["memberships"][0]["effective_to"] = DAYS[0]
    with pytest.raises(ValidationError, match="interval"):
        TopicBatch.model_validate(payload)


def test_clean_topic_batch_has_exact_calendar_coverage():
    from dragon_radar.topics.quality import validate_topic_batch

    report = validate_topic_batch(topic_batch(), now=NOW)
    assert report.publishable
    assert report.coverage.expected == 4
    assert report.coverage.valid == 4
    assert report.coverage.legal_excluded == report.coverage.missing == 0


@pytest.mark.parametrize(
    "change,code",
    [
        ({"coverage_from": DAYS[0] - timedelta(days=1)}, "coverage_range"),
        ({"coverage_to": DAYS[-1] + timedelta(days=1)}, "coverage_range"),
        ({"trade_date": DAYS[0] - timedelta(days=1)}, "trade_date"),
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


def test_unavailable_coverage_is_not_counted_as_complete():
    payload = topic_batch_dict()
    payload["coverage_cells"][-1].update(
        status="unavailable", reason="provider outage"
    )
    from dragon_radar.topics.quality import validate_topic_batch

    report = validate_topic_batch(TopicBatch.model_validate(payload), now=NOW)
    assert not report.publishable
    assert report.coverage.valid == 3
    assert report.coverage.missing == 1
    assert "unavailable_coverage" in {issue.code for issue in report.issues}


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


@pytest.mark.parametrize("record", ["topics", "memberships"])
def test_topic_records_cannot_arrive_after_batch_or_validation_cutoff(record):
    payload = topic_batch_dict()
    payload[record][0]["available_at"] = NOW + timedelta(seconds=1)
    assert "future_availability" in issue_codes(payload)
    assert "future_availability" in issue_codes(
        topic_batch_dict(), now=NOW - timedelta(days=8)
    )


def test_memberships_must_reference_declared_topics():
    payload = topic_batch_dict(
        memberships=[topic_membership(topic_id="unknown")]
    )
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
    payload = topic_batch_dict(
        memberships=[topic_membership(evidence_refs=(value,))]
    )
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)
