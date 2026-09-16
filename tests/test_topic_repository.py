"""PostgreSQL contracts for immutable topic imports and snapshots."""

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest
from factories import NOW, grant
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from topic_factories import TOPIC_DAYS, coverage_cell, topic_batch


@pytest.fixture
def topic_repository(database):
    from dragon_radar.storage.repository import Repository
    from dragon_radar.topics.repository import TopicRepository

    Repository(database, clock=lambda: NOW).register_source(grant())
    return TopicRepository(database, clock=lambda: NOW)


def test_topic_import_replays_same_key_and_rejects_different_payload(
    topic_repository,
):
    from dragon_radar.storage.repository import Conflict

    payload = topic_batch()
    first = topic_repository.import_batch(payload, key="topic-key")
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert first.canonical_json == canonical
    assert first.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert topic_repository.import_batch(payload, key="topic-key").id == first.id

    revised = payload.model_copy(update={"membership_version": "members-v2"})
    with pytest.raises(Conflict):
        topic_repository.import_batch(revised, key="topic-key")


def test_failed_quality_attempt_is_persisted_without_a_batch(topic_repository):
    from dragon_radar.storage.repository import Conflict, QualityRejected

    payload = topic_batch(
        coverage_cells=tuple(
            coverage_cell(
                trade_date=day,
                received_member_count=0,
            )
            if day == TOPIC_DAYS[-1]
            else coverage_cell(trade_date=day)
            for day in TOPIC_DAYS
        )
    )
    with pytest.raises(QualityRejected, match="quality"):
        topic_repository.import_batch(payload, key="incomplete")
    with pytest.raises(QualityRejected, match="quality"):
        topic_repository.import_batch(payload, key="incomplete")
    with pytest.raises(Conflict):
        topic_repository.import_batch(topic_batch(), key="incomplete")

    attempts = topic_repository.list_attempts(status="failed")
    assert len(attempts) == 1
    attempt = topic_repository.get_attempt(attempts[0].id)
    assert attempt.status == "failed"
    assert attempt.stage == "quality"
    assert attempt.retry_from == payload.coverage_from
    assert attempt.retry_to == payload.coverage_to
    assert attempt.details
    with topic_repository.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_import_batches")) == 0
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0


def test_topic_listing_uses_one_operation_time(topic_repository):
    topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="single-clock").id
    )
    calls = []

    def clock():
        calls.append(None)
        return NOW

    topic_repository.clock = clock
    topic_repository.list_snapshots()
    assert len(calls) == 1


def test_failed_attempt_records_broken_page_cursor_and_retry_range(
    topic_repository,
):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    pages = list(payload.page_manifest.pages)
    pages[1] = pages[1].model_copy(update={"request_cursor": "broken-cursor"})
    manifest = payload.page_manifest.model_copy(update={"pages": tuple(pages)})
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="broken-manifest")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.failed_cursor == "broken-cursor"
    assert attempt.missing_items
    assert (attempt.retry_from, attempt.retry_to) == (
        payload.coverage_from,
        payload.coverage_to,
    )


def test_successful_import_attempt_is_append_only(topic_repository):
    stored = topic_repository.import_batch(topic_batch(), key="successful")

    attempts = topic_repository.list_attempts(status="succeeded")
    assert len(attempts) == 1
    assert attempts[0].stage == "import"
    assert topic_repository.get_attempt(attempts[0].id) == attempts[0]
    with topic_repository.database.engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT count(*) FROM topic_import_attempts "
                "WHERE batch_id = :batch_id"
            ),
            {"batch_id": stored.id},
        ) == 1


def test_topic_publish_is_immutable_and_revisioned(topic_repository):
    payload = topic_batch()
    first = topic_repository.publish(
        topic_repository.import_batch(payload, key="topic-v1").id
    )
    revised = payload.model_copy(update={"membership_version": "members-v2"})
    second = topic_repository.publish(
        topic_repository.import_batch(revised, key="topic-v2").id
    )

    assert (first.revision, first.supersedes) == (1, None)
    assert second.revision == 2
    assert second.supersedes == first.id
    assert topic_repository.publish(first.batch_id).id == first.id
    assert topic_repository.get_snapshot(first.id) == first


def test_topic_snapshot_listing_has_exact_filters_and_stable_cursor(
    topic_repository,
):
    signature = inspect.signature(topic_repository.list_snapshots)
    assert tuple(signature.parameters) == (
        "trade_date",
        "source_id",
        "page_size",
        "cursor",
    )
    first = topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="first").id
    )
    second_payload = topic_batch().model_copy(
        update={"membership_version": "members-v2"}
    )
    second = topic_repository.publish(
        topic_repository.import_batch(second_payload, key="second").id
    )

    page = topic_repository.list_snapshots(page_size=1)
    assert page.items == (second,)
    assert page.page_size == 1
    assert page.next_cursor is not None
    next_page = topic_repository.list_snapshots(
        page_size=1,
        cursor=page.next_cursor,
    )
    assert next_page.items == (first,)
    assert next_page.next_cursor is None
    with pytest.raises(ValueError, match="cursor"):
        topic_repository.list_snapshots(cursor="not-a-cursor")


@pytest.mark.parametrize("change", ["revoke", "embedded_expire"])
def test_topic_publish_rechecks_current_and_embedded_grants(
    topic_repository, change
):
    from dragon_radar.storage.repository import Forbidden, Repository

    stored = topic_repository.import_batch(topic_batch(), key="authorization")
    if change == "revoke":
        Repository(
            topic_repository.database, clock=lambda: NOW
        ).revoke_source("synthetic")
    else:
        topic_repository.clock = lambda: datetime(2028, 1, 1, tzinfo=UTC)

    with pytest.raises(Forbidden):
        topic_repository.publish(stored.id)
    with topic_repository.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0


def test_topic_publish_holds_source_lock_against_revocation(topic_repository):
    stored = topic_repository.import_batch(topic_batch(), key="lock")
    checked = []

    def check_lock(conn, cursor, statement, parameters, context, executemany):
        if "source_grants" not in statement or "FOR UPDATE" not in statement:
            return
        with (
            pytest.raises(DBAPIError),
            topic_repository.database.engine.begin() as other,
        ):
            other.execute(
                text(
                    "SELECT * FROM source_grants "
                    "WHERE source_id = 'synthetic' FOR UPDATE NOWAIT"
                )
            )
        checked.append(True)

    event.listen(
        topic_repository.database.engine, "after_cursor_execute", check_lock
    )
    try:
        topic_repository.publish(stored.id)
    finally:
        event.remove(
            topic_repository.database.engine, "after_cursor_execute", check_lock
        )
    assert checked


@pytest.mark.parametrize(
    "table",
    ["topic_import_attempts", "topic_import_batches", "topic_snapshots"],
)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_topic_history_rejects_statement_level_mutation(
    topic_repository, table, operation
):
    topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="immutable").id
    )
    if operation == "UPDATE":
        statement = f"UPDATE {table} SET id = id WHERE false"
    elif operation == "DELETE":
        statement = f"DELETE FROM {table} WHERE false"
    else:
        statement = f"TRUNCATE {table} CASCADE"

    with (
        pytest.raises(DBAPIError),
        topic_repository.database.engine.begin() as connection,
    ):
        connection.execute(text(statement))


def test_topic_publication_failure_leaves_no_partial_snapshot(topic_repository):
    stored = topic_repository.import_batch(topic_batch(), key="atomic")

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO topic_snapshots"):
            raise RuntimeError("injected publication failure")

    event.listen(topic_repository.database.engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            topic_repository.publish(stored.id)
    finally:
        event.remove(
            topic_repository.database.engine, "before_cursor_execute", fail_insert
        )

    with topic_repository.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM topic_snapshots")) == 0
    assert topic_repository.publish(stored.id).revision == 1


def test_topic_cursor_excludes_snapshots_published_after_first_page(
    topic_repository,
):
    operation_time = [NOW]
    topic_repository.clock = lambda: operation_time[0]
    first = topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="watermark-first").id
    )
    operation_time[0] = NOW + timedelta(seconds=1)
    second_payload = topic_batch().model_copy(
        update={"membership_version": "members-v2"}
    )
    second = topic_repository.publish(
        topic_repository.import_batch(second_payload, key="watermark-second").id
    )
    page = topic_repository.list_snapshots(page_size=1)
    assert page.items == (second,)
    assert page.next_cursor is not None

    operation_time[0] = NOW + timedelta(seconds=2)
    third_payload = topic_batch().model_copy(
        update={"membership_version": "members-v3"}
    )
    topic_repository.publish(
        topic_repository.import_batch(third_payload, key="watermark-third").id
    )
    next_page = topic_repository.list_snapshots(
        page_size=10, cursor=page.next_cursor
    )
    assert next_page.items == (first,)
