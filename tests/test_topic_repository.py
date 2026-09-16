"""PostgreSQL contracts for immutable topic imports and snapshots."""

import base64
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
    assert attempt.missing_items == ("expected_cursor:cursor-2",)
    assert (attempt.retry_from, attempt.retry_to) == (
        payload.coverage_from,
        payload.coverage_to,
    )


def test_failed_attempt_records_page_after_missing_continuation(topic_repository):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    pages = list(payload.page_manifest.pages)
    pages[0] = pages[0].model_copy(update={"next_cursor": None})
    manifest = payload.page_manifest.model_copy(update={"pages": tuple(pages)})
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="missing-continuation")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.missing_items == ("missing_after_page:page-1",)
    assert attempt.failed_cursor == "cursor-2"


def test_failed_attempt_records_missing_cursor_between_pages(topic_repository):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    pages = list(payload.page_manifest.pages)
    pages[0] = pages[0].model_copy(update={"next_cursor": None})
    pages[1] = pages[1].model_copy(update={"request_cursor": None})
    manifest = payload.page_manifest.model_copy(update={"pages": tuple(pages)})
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="missing-between-pages")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.missing_items == ("missing_after_page:page-1",)
    assert attempt.failed_cursor is None


def test_failed_attempt_records_actual_terminal_cursor(topic_repository):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    pages = list(payload.page_manifest.pages)
    pages[-1] = pages[-1].model_copy(update={"next_cursor": "expected-terminal"})
    manifest = payload.page_manifest.model_copy(
        update={
            "pages": tuple(pages),
            "terminal_cursor": "actual-terminal",
        }
    )
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="terminal-mismatch")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.missing_items == ("expected_cursor:expected-terminal",)
    assert attempt.failed_cursor == "actual-terminal"


def test_failed_attempt_records_missing_actual_terminal_cursor(topic_repository):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    pages = list(payload.page_manifest.pages)
    pages[-1] = pages[-1].model_copy(update={"next_cursor": "expected-terminal"})
    manifest = payload.page_manifest.model_copy(update={"pages": tuple(pages)})
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="missing-terminal")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.missing_items == ("expected_cursor:expected-terminal",)
    assert attempt.failed_cursor is None


def test_failed_attempt_records_missing_initial_page(topic_repository):
    from dragon_radar.storage.repository import QualityRejected

    payload = topic_batch()
    manifest = payload.page_manifest.model_copy(
        update={
            "pages": (),
            "declared_total": 0,
            "terminal_cursor": None,
        }
    )
    malformed = payload.model_copy(update={"page_manifest": manifest})

    with pytest.raises(QualityRejected):
        topic_repository.import_batch(malformed, key="missing-initial-page")

    attempt = topic_repository.list_attempts(status="failed")[0]
    assert attempt.failed_cursor is None
    assert attempt.missing_items == ("expected_cursor:<initial>",)
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
    snapshots = []
    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"members-v{revision}"}
        )
        snapshots.append(
            topic_repository.publish(
                topic_repository.import_batch(payload, key=f"revision-{revision}").id
            )
        )

    page = topic_repository.list_snapshots(page_size=20)
    assert page.items == tuple(reversed(snapshots[1:]))
    assert page.page_size == 20
    assert page.next_cursor is not None
    next_page = topic_repository.list_snapshots(
        page_size=20,
        cursor=page.next_cursor,
    )
    assert next_page.items == (snapshots[0],)
    assert next_page.next_cursor is None
    with pytest.raises(ValueError, match="cursor"):
        topic_repository.list_snapshots(cursor="not-a-cursor")


@pytest.mark.parametrize("page_size", [1, 19, 21, 99, 101, True])
def test_topic_snapshot_listing_rejects_non_contract_page_sizes(
    topic_repository, page_size
):
    with pytest.raises(ValueError, match="20, 50, or 100"):
        topic_repository.list_snapshots(page_size=page_size)


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


def test_topic_table_metadata_matches_migration_constraints_and_indexes():
    from sqlalchemy import CheckConstraint

    from dragon_radar.topics.tables import (
        topic_import_attempts,
        topic_snapshots,
    )

    snapshot_checks = {
        str(item.sqltext)
        for item in topic_snapshots.constraints
        if isinstance(item, CheckConstraint)
    }
    attempt_checks = {
        str(item.sqltext)
        for item in topic_import_attempts.constraints
        if isinstance(item, CheckConstraint)
    }
    assert snapshot_checks == {"revision > 0"}
    assert attempt_checks == {"status IN ('failed', 'succeeded')"}
    assert {
        item.name: tuple(str(expression) for expression in item.expressions)
        for item in topic_snapshots.indexes
    } == {
        "topic_snapshots_listing": (
            "trade_date DESC",
            "source_id ASC",
            "revision DESC",
            "id ASC",
        )
    }
    assert {
        item.name: tuple(str(expression) for expression in item.expressions)
        for item in topic_import_attempts.indexes
    } == {
        "topic_import_attempts_listing": (
            "created_at DESC",
            "id ASC",
        )
    }


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


def test_topic_cursor_excludes_later_snapshot_with_same_published_at(
    topic_repository,
):
    from dragon_radar.storage.repository import Repository

    snapshots = []
    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"watermark-v{revision}"}
        )
        snapshots.append(
            topic_repository.publish(
                topic_repository.import_batch(
                    payload, key=f"watermark-{revision}"
                ).id
            )
        )

    page = topic_repository.list_snapshots(page_size=20)
    assert page.items == tuple(reversed(snapshots[1:]))
    assert page.next_cursor is not None

    Repository(topic_repository.database, clock=lambda: NOW).register_source(
        grant(source_id="z-source")
    )
    later_payload = topic_batch().model_copy(
        update={
            "source_id": "z-source",
            "grant": grant(source_id="z-source"),
        }
    )
    later = topic_repository.publish(
        topic_repository.import_batch(later_payload, key="watermark-later").id
    )
    next_page = topic_repository.list_snapshots(
        page_size=20, cursor=page.next_cursor
    )
    assert later.published_at == page.items[0].published_at
    assert next_page.items == (snapshots[0],)


def test_topic_cursor_rejects_tampered_transaction_snapshot_before_query(
    topic_repository,
):
    snapshots = []
    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"tamper-v{revision}"}
        )
        snapshots.append(
            topic_repository.publish(
                topic_repository.import_batch(
                    payload, key=f"tamper-{revision}"
                ).id
            )
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None

    encoded_payload, separator, signature = page.next_cursor.partition(".")
    padded = encoded_payload + "=" * (-len(encoded_payload) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    with topic_repository.database.engine.connect() as connection:
        payload["transaction_snapshot"] = connection.scalar(
            text("SELECT txid_current_snapshot()::text")
        )
    tampered_payload = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    tampered_cursor = (
        f"{tampered_payload}.{signature}" if separator else tampered_payload
    )
    snapshot_queries = []

    def record_snapshot_query(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "txid_visible_in_snapshot" in statement:
            snapshot_queries.append(statement)

    event.listen(
        topic_repository.database.engine,
        "before_cursor_execute",
        record_snapshot_query,
    )
    try:
        with pytest.raises(ValueError, match="Invalid topic snapshot cursor"):
            topic_repository.list_snapshots(
                page_size=20,
                cursor=tampered_cursor,
            )
    finally:
        event.remove(
            topic_repository.database.engine,
            "before_cursor_execute",
            record_snapshot_query,
        )
    assert snapshot_queries == []


def test_topic_cursor_normalizes_invalid_signed_postgresql_snapshot(
    topic_repository,
):
    from dragon_radar.topics.repository import _decode_cursor, _encode_cursor

    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"invalid-snapshot-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(
                payload, key=f"invalid-snapshot-{revision}"
            ).id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None
    payload = _decode_cursor(
        page.next_cursor,
        topic_repository._cursor_signing_key,
    )
    payload["transaction_snapshot"] = "not-a-postgresql-snapshot"
    cursor = _encode_cursor(payload, topic_repository._cursor_signing_key)

    with pytest.raises(ValueError, match="Invalid topic snapshot cursor"):
        topic_repository.list_snapshots(page_size=20, cursor=cursor)


def test_topic_cursor_rejects_invalid_postgresql_snapshot_without_visible_sources(
    topic_repository,
):
    from dragon_radar.topics.repository import _encode_cursor

    cursor = _encode_cursor(
        {
            "version": 1,
            "sort_version": 1,
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=15)).isoformat(),
            "watermark": NOW.isoformat(),
            "transaction_snapshot": "not-a-postgresql-snapshot",
            "filters": {
                "trade_date": None,
                "source_id": None,
            },
            "last": [
                TOPIC_DAYS[-1].isoformat(),
                "synthetic",
                1,
                "00000000-0000-0000-0000-000000000001",
            ],
        },
        topic_repository._cursor_signing_key,
    )

    with pytest.raises(ValueError, match="^Invalid topic snapshot cursor$"):
        topic_repository.list_snapshots(page_size=20, cursor=cursor)


def test_topic_cursor_remains_valid_after_repository_restart(topic_repository):
    from dragon_radar.topics.repository import TopicRepository

    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"restart-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"restart-{revision}").id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None

    restarted = TopicRepository(topic_repository.database, clock=lambda: NOW)
    assert restarted.list_snapshots(
        page_size=20,
        cursor=page.next_cursor,
    ).items == (topic_repository.get_snapshot(page.items[-1].supersedes),)


def test_topic_cursor_has_canonical_utc_fifteen_minute_lifetime(topic_repository):
    from dragon_radar.topics.repository import (
        TOPIC_SNAPSHOT_CURSOR_TTL,
        _decode_cursor,
    )

    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"ttl-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"ttl-{revision}").id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None

    payload = _decode_cursor(
        page.next_cursor,
        topic_repository._cursor_signing_key,
    )
    assert TOPIC_SNAPSHOT_CURSOR_TTL == timedelta(minutes=15)
    assert payload["issued_at"] == NOW.isoformat()
    assert payload["expires_at"] == (NOW + timedelta(minutes=15)).isoformat()


def test_topic_cursor_rejects_expiry_before_database_query(topic_repository):
    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"expired-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"expired-{revision}").id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None
    topic_repository.clock = lambda: NOW + timedelta(minutes=15)
    queries = []

    def record_query(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(
        topic_repository.database.engine,
        "before_cursor_execute",
        record_query,
    )
    try:
        with pytest.raises(ValueError, match="^Invalid topic snapshot cursor$"):
            topic_repository.list_snapshots(
                page_size=20,
                cursor=page.next_cursor,
            )
    finally:
        event.remove(
            topic_repository.database.engine,
            "before_cursor_execute",
            record_query,
        )
    assert queries == []


@pytest.mark.parametrize(
    "changes",
    [
        {
            "issued_at": (NOW + timedelta(seconds=1)).isoformat(),
            "expires_at": (NOW + timedelta(minutes=15, seconds=1)).isoformat(),
        },
        {
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
        },
        {
            "issued_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=15)).isoformat(),
        },
        {
            "issued_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=16)).isoformat(),
        },
    ],
    ids=["future-issued", "inverted", "noncanonical-utc", "wrong-ttl"],
)
def test_topic_cursor_rejects_signed_invalid_lifetime_schema(topic_repository, changes):
    from dragon_radar.topics.repository import _decode_cursor, _encode_cursor

    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"invalid-time-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"invalid-time-{revision}").id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None
    payload = _decode_cursor(
        page.next_cursor,
        topic_repository._cursor_signing_key,
    )
    payload.update(changes)
    cursor = _encode_cursor(payload, topic_repository._cursor_signing_key)

    with pytest.raises(ValueError, match="^Invalid topic snapshot cursor$"):
        topic_repository.list_snapshots(page_size=20, cursor=cursor)


def test_topic_cursor_does_not_renew_lifetime_on_later_pages(topic_repository):
    from dragon_radar.topics.repository import _decode_cursor

    for revision in range(1, 42):
        payload = topic_batch().model_copy(
            update={"membership_version": f"no-renew-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"no-renew-{revision}").id
        )
    first = topic_repository.list_snapshots(page_size=20)
    assert first.next_cursor is not None
    first_payload = _decode_cursor(
        first.next_cursor,
        topic_repository._cursor_signing_key,
    )

    topic_repository.clock = lambda: NOW + timedelta(minutes=1)
    second = topic_repository.list_snapshots(
        page_size=20,
        cursor=first.next_cursor,
    )
    assert second.next_cursor is not None
    second_payload = _decode_cursor(
        second.next_cursor,
        topic_repository._cursor_signing_key,
    )
    assert (
        second_payload["issued_at"],
        second_payload["expires_at"],
    ) == (
        first_payload["issued_at"],
        first_payload["expires_at"],
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "1"),
        ("filters", {"trade_date": None, "source_id": "other"}),
        ("sort_version", 2),
        ("last", ["2026-09-08", "synthetic", "1", "not-a-uuid"]),
        ("last", ["2026-09-08", "synthetic", True, "0" * 32]),
        ("last", ["2026-09-08", "synthetic", 1]),
        ("extra", "unexpected"),
    ],
)
def test_topic_cursor_strictly_binds_filters_sort_version_and_last_types(
    topic_repository, field, value
):
    from dragon_radar.topics.repository import _decode_cursor, _encode_cursor

    for revision in range(1, 22):
        payload = topic_batch().model_copy(
            update={"membership_version": f"strict-v{revision}"}
        )
        topic_repository.publish(
            topic_repository.import_batch(payload, key=f"strict-{revision}").id
        )
    page = topic_repository.list_snapshots(page_size=20)
    assert page.next_cursor is not None
    payload = _decode_cursor(
        page.next_cursor,
        topic_repository._cursor_signing_key,
    )
    payload[field] = value
    cursor = _encode_cursor(payload, topic_repository._cursor_signing_key)

    with pytest.raises(ValueError, match="Invalid topic snapshot cursor"):
        topic_repository.list_snapshots(page_size=20, cursor=cursor)
