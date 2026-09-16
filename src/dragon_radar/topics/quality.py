"""Pure quality gate for immutable topic batches."""

from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dragon_radar.domain.quality import (
    Coverage,
    QualityIssue,
    QualityReport,
    grant_allows_local,
)
from dragon_radar.topics.models import TopicBatch, TopicMembership

ROLE_V1_MAX_WINDOW_SESSION_COUNT = 61


class TopicQualityReport(QualityReport):
    batch: TopicBatch


def validate_topic_batch(batch: TopicBatch, *, now: datetime) -> TopicQualityReport:
    """Validate topic completeness without persisting or repairing input."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("operation time requires a timezone")
    issues: list[QualityIssue] = []
    shanghai = ZoneInfo("Asia/Shanghai")

    def add(code: str, message: str) -> None:
        issues.append(QualityIssue(code=code, message=message))

    if not grant_allows_local(batch.grant, source_id=batch.source_id, now=now):
        add("grant_denied", "Current grant does not authorize local storage/display")
    if batch.as_of > now or batch.trade_date > batch.as_of.astimezone(shanghai).date():
        add("batch_time", "Batch cutoff/date is in the future")
    if batch.source_time is not None and (
        batch.source_time > batch.as_of or batch.source_time > now
    ):
        add("source_time", "Source timestamp exceeds batch cutoff or operation time")
    if (
        batch.exchange != batch.calendar.exchange
        or batch.calendar_version != batch.calendar.version
    ):
        add("calendar_mismatch", "Exchange or calendar version differs from batch")

    sessions = tuple(
        day
        for day in batch.calendar.sessions
        if batch.coverage_from <= day <= batch.coverage_to
    )
    if (
        batch.coverage_from > batch.coverage_to
        or batch.coverage_from not in batch.calendar.sessions
        or batch.coverage_to not in batch.calendar.sessions
    ):
        add("coverage_range", "Coverage endpoints must be supplied calendar sessions")
    if (
        not batch.coverage_from <= batch.trade_date <= batch.coverage_to
        or batch.trade_date not in batch.calendar.sessions
    ):
        add(
            "trade_date",
            "Batch date must be a calendar session inside the coverage range",
        )

    sessions_through_trade_date = tuple(
        day for day in batch.calendar.sessions if day <= batch.trade_date
    )
    role_window_covered = (
        batch.trade_date in batch.calendar.sessions
        and len(sessions_through_trade_date) >= ROLE_V1_MAX_WINDOW_SESSION_COUNT
        and batch.coverage_from
        <= sessions_through_trade_date[-ROLE_V1_MAX_WINDOW_SESSION_COUNT]
        and batch.coverage_to >= batch.trade_date
    )
    if not role_window_covered:
        add(
            "role_window_coverage",
            "Coverage must include the "
            f"{ROLE_V1_MAX_WINDOW_SESSION_COUNT}-session role-v1 window "
            "ending on the batch date",
        )

    topic_counts = Counter(topic.topic_id for topic in batch.topics)
    for topic_id, count in topic_counts.items():
        if count > 1:
            add("duplicate_topic", f"Duplicate topic definition: {topic_id}")
    topic_ids = set(topic_counts)
    for topic in batch.topics:
        if topic.available_at > batch.as_of or topic.available_at > now:
            add("future_availability", "Topic definition was not known at cutoff")
    memberships_by_topic: dict[str, list[TopicMembership]] = defaultdict(list)
    for membership in batch.memberships:
        memberships_by_topic[membership.topic_id].append(membership)
        if membership.topic_id not in topic_ids:
            add("unknown_topic", "Membership references an undeclared topic")
        if membership.available_at > batch.as_of or membership.available_at > now:
            add("future_availability", "Topic membership was not known at cutoff")

    expected_keys = {(topic_id, day) for topic_id in topic_ids for day in sessions}
    cell_counts = Counter(
        (cell.topic_id, cell.trade_date) for cell in batch.coverage_cells
    )
    supplied_keys = set(cell_counts)
    for key in sorted(expected_keys - supplied_keys):
        add("missing_coverage", f"Missing coverage cell: {key[0]} {key[1]}")
    for key, count in cell_counts.items():
        if count > 1:
            add("duplicate_coverage", f"Duplicate coverage cell: {key[0]} {key[1]}")
        if key not in expected_keys:
            add("invalid_coverage", f"Coverage cell outside declaration: {key}")

    valid_keys: set[tuple[str, date]] = set()
    for cell in batch.coverage_cells:
        key = (cell.topic_id, cell.trade_date)
        actual = len(
            {
                membership.security_id
                for membership in memberships_by_topic[cell.topic_id]
                if membership.effective_from <= cell.trade_date
                and (
                    membership.effective_to is None
                    or cell.trade_date < membership.effective_to
                )
                and membership.available_at <= cell.complete_as_of
            }
        )
        cell_valid = True
        if (
            cell.complete_as_of > batch.as_of
            or cell.trade_date > cell.complete_as_of.astimezone(shanghai).date()
        ):
            add(
                "coverage_time",
                "Coverage completion exceeds batch cutoff or precedes trade date",
            )
            cell_valid = False
        if cell.received_member_count != actual:
            add("coverage_count", "Received member count differs from known members")
            cell_valid = False
        if cell.status == "complete":
            if cell.received_member_count != cell.expected_member_count:
                add("coverage_count", "Complete coverage counts must match")
                cell_valid = False
        else:
            cell_valid = False
            if not cell.reason or not cell.reason.strip():
                add("unavailable_reason", "Unavailable coverage requires a reason")
        if cell_valid and cell_counts[key] == 1 and key in expected_keys:
            valid_keys.add(key)

    manifest = batch.page_manifest
    manifest_valid = True
    if (
        manifest.request_from != batch.coverage_from
        or manifest.request_to != batch.coverage_to
        or manifest.request_from > manifest.request_to
    ):
        manifest_valid = False
    pages = manifest.pages
    if not pages or pages[0].request_cursor is not None:
        manifest_valid = False
    if any(page.request_cursor is None for page in pages[1:]):
        manifest_valid = False
    if any(page.next_cursor is None for page in pages[:-1]):
        manifest_valid = False
    if len({page.page_id for page in pages}) != len(pages):
        manifest_valid = False
    if len({page.page_hash for page in pages}) != len(pages):
        manifest_valid = False
    request_cursors = [
        page.request_cursor for page in pages if page.request_cursor is not None
    ]
    if len(set(request_cursors)) != len(request_cursors):
        manifest_valid = False
    if any(
        current.request_cursor != previous.next_cursor
        for previous, current in zip(pages, pages[1:], strict=False)
    ):
        manifest_valid = False
    if pages:
        if pages[-1].next_cursor != manifest.terminal_cursor:
            manifest_valid = False
        if any(page.next_cursor == manifest.terminal_cursor for page in pages[:-1]):
            manifest_valid = False
    if sum(
        page.received_row_count for page in pages
    ) != manifest.declared_total or manifest.declared_total != len(batch.topics) + len(
        batch.memberships
    ):
        manifest_valid = False
    if not manifest_valid:
        add("page_manifest", "Page manifest is not contiguous and self-consistent")

    expected = len(expected_keys)
    valid = len(valid_keys)
    return TopicQualityReport(
        batch=batch,
        issues=tuple(issues),
        coverage=Coverage(
            expected=expected,
            valid=valid,
            legal_excluded=0,
            missing=expected - valid,
        ),
    )
