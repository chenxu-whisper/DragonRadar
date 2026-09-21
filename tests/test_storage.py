"""Real PostgreSQL storage contracts, including transaction and race behavior."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier

import pytest
from factories import NOW, batch, grant
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError


def test_database_readiness_requires_explicit_idempotent_migration(database_url):
    from dragon_radar.storage.database import Database

    db = Database(database_url)
    try:
        assert not db.ready()
        db.migrate()
        db.migrate()
        assert db.ready()
    finally:
        db.close()


def test_import_canonical_idempotent_restart_and_conflict(repository, database_url):
    from dragon_radar.storage.database import Database
    from dragon_radar.storage.repository import Conflict, Repository

    payload = batch()
    stored = repository.import_batch(payload, key="one")
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert stored.canonical_json == canonical
    assert stored.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert stored.quality_report == {
        "issues": [],
        "coverage": {"expected": 1, "valid": 1, "legal_excluded": 0, "missing": 0},
        "publishable": True,
    }
    assert repository.import_batch(payload, key="one").id == stored.id
    with pytest.raises(Conflict):
        repository.import_batch(batch(complete_pages=False), key="one")
    db = Database(database_url)
    try:
        assert (
            Repository(db, clock=lambda: NOW).import_batch(payload, key="one").id
            == stored.id
        )
    finally:
        db.close()


def test_legacy_repository_can_archive_identity_beyond_normalized_bounds(repository):
    from factories import state

    payload = batch(security_states=(state(code="X" * 11),))

    stored = repository.import_batch(payload, key="legacy-wide-identity")

    assert json.loads(stored.canonical_json)["security_states"][0]["code"] == "X" * 11


@pytest.mark.parametrize("changes", [{"complete_pages": False}, {"bars": ()}])
def test_quality_failure_archived_exactly_but_never_published(repository, changes):
    from dragon_radar.domain.quality import validate_batch
    from dragon_radar.storage.repository import QualityRejected

    payload = batch(**changes)
    stored = repository.import_batch(payload, key="bad")
    report = validate_batch(payload, now=NOW)
    assert stored.quality_report["issues"] == [
        issue.model_dump(mode="json") for issue in report.issues
    ]
    assert not stored.quality_report["publishable"]
    with pytest.raises(QualityRejected):
        repository.publish(stored.id)
    assert repository.list_snapshots() == []


def test_embedded_grant_cannot_register_or_override(database):
    from dragon_radar.storage.repository import Forbidden, Repository

    repo = Repository(database, clock=lambda: NOW)
    with pytest.raises(Forbidden):
        repo.import_batch(batch(), key="forged")
    repo.register_source(grant(approved=False))
    with pytest.raises(Forbidden):
        repo.import_batch(batch(), key="forged")
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 0


@pytest.mark.parametrize(
    "embedded",
    [grant(approved=False), grant(source_id="other"), grant(local_storage=False)],
)
def test_embedded_rights_and_source_identity_also_required(repository, embedded):
    from dragon_radar.storage.repository import Forbidden

    with pytest.raises(Forbidden):
        repository.import_batch(batch(grant=embedded), key="denied")


def test_storage_only_license_archives_but_cannot_publish_or_read(repository):
    from dragon_radar.storage.repository import Forbidden

    storage_only = grant(local_display=False)
    repository.register_source(storage_only)

    stored = repository.import_batch(
        batch(grant=storage_only),
        key="storage-only",
    )

    assert json.loads(stored.canonical_json) == batch(
        grant=storage_only
    ).model_dump(mode="json")
    assert not stored.quality_report["publishable"]
    with pytest.raises(Forbidden):
        repository.publish(stored.id)
    assert repository.list_snapshots() == []


def test_display_only_current_grant_can_publish_and_read_existing_archive(repository):
    from dragon_radar.storage.repository import Forbidden

    stored = repository.import_batch(batch(), key="display-only-existing")
    repository.register_source(grant(local_storage=False))

    with pytest.raises(Forbidden):
        repository.import_batch(batch(), key="display-only-existing")
    snapshot = repository.publish(stored.id)

    assert repository.get_snapshot(snapshot.id) == snapshot
    assert repository.list_snapshots() == [snapshot]


def test_revisions_preserve_original_and_same_batch_publish_is_idempotent(repository):
    first_batch = repository.import_batch(batch(), key="first")
    first = repository.publish(first_batch.id)
    second_batch = repository.import_batch(batch(source_time=NOW), key="correction")
    second = repository.publish(second_batch.id)
    assert (first.revision, first.supersedes) == (1, None)
    assert (second.revision, second.supersedes) == (2, first.id)
    assert repository.publish(first_batch.id).id == first.id
    assert repository.get_snapshot(first.id).batch.source_time is None
    assert repository.get_snapshot(second.id).batch.source_time == NOW
    assert repository.list_snapshots(trade_date=date(2020, 1, 1)) == []
    assert [s.id for s in repository.list_snapshots()] == [second.id, first.id]


@pytest.mark.parametrize("change", ["revoke", "expire", "embedded_expire"])
def test_current_authorization_on_every_read_and_publish(repository, change):
    from dragon_radar.storage.repository import Forbidden

    record = repository.import_batch(batch(), key="a")
    snapshot = repository.publish(record.id)
    if change == "revoke":
        repository.revoke_source("synthetic")
    elif change == "expire":
        repository.register_source(grant(expires_at=NOW))
    else:
        repository.register_source(grant(expires_at=datetime(2029, 1, 1, tzinfo=UTC)))
        repository.clock = lambda: datetime(2028, 1, 1, tzinfo=UTC)
    with pytest.raises(Forbidden):
        repository.get_snapshot(snapshot.id)
    with pytest.raises(Forbidden):
        repository.publish(record.id)
    with pytest.raises(Forbidden):
        repository.import_batch(batch(), key="a")
    assert repository.list_snapshots() == []


@pytest.mark.parametrize("table", ["import_batches", "snapshots", "audit_events"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_database_rejects_mutation_of_immutable_records(repository, table, operation):
    repository.publish(repository.import_batch(batch(), key="a").id)
    statement = (
        f"UPDATE {table} SET id = id"
        if operation == "UPDATE"
        else f"DELETE FROM {table}"
    )
    with pytest.raises(DBAPIError), repository.database.engine.begin() as connection:
        connection.execute(text(statement))


def test_publication_audit_failure_rolls_back_entire_transaction(repository):
    stored = repository.import_batch(batch(), key="a")

    def fail_audit(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO audit_events"):
            raise RuntimeError("injected transaction failure")

    event.listen(repository.database.engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            repository.publish(stored.id)
    finally:
        event.remove(repository.database.engine, "before_cursor_execute", fail_audit)
    assert repository.list_snapshots() == []
    assert repository.publish(stored.id).revision == 1


def test_concurrent_import_same_key_has_one_archive_and_audit(repository):
    barrier = Barrier(4)

    def run(_):
        barrier.wait()
        return repository.import_batch(batch(), key="race").id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(run, range(4)))
    assert len(set(ids)) == 1
    with repository.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action = 'import'")
            )
            == 1
        )


def test_concurrent_publication_revisions_and_same_batch(repository):
    records = [repository.import_batch(batch(), key=str(i)) for i in range(4)]
    barrier = Barrier(4)

    def publish(record):
        barrier.wait()
        return repository.publish(record.id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(publish, records))
    ordered = sorted(snapshots, key=lambda s: s.revision)
    assert [s.revision for s in ordered] == [1, 2, 3, 4]
    assert [s.supersedes for s in ordered] == [None] + [s.id for s in ordered[:-1]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        repeated = list(pool.map(publish, [records[0]] * 4))
    assert len({s.id for s in repeated}) == 1


def test_source_registry_changes_are_audited(repository):
    repository.register_source(grant(reference="updated-local-reference"))
    repository.revoke_source("synthetic")
    with repository.database.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT action FROM audit_events ORDER BY occurred_at, id")
            )
            .scalars()
            .all()
            .count("source_register")
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action = 'source_revoke'")
            )
            == 1
        )


def test_concurrent_key_conflict_archives_only_winner(repository):
    from dragon_radar.storage.repository import Conflict

    barrier = Barrier(2)

    def run(payload):
        barrier.wait(timeout=10)
        try:
            repository.import_batch(payload, key="competing")
            return "created"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [batch(), batch(source_time=NOW)]))
    assert sorted(results) == ["conflict", "created"]
    with repository.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 1


def test_snapshot_retrieval_after_engine_restart(repository, database_url):
    from dragon_radar.storage.database import Database
    from dragon_radar.storage.repository import Repository

    snapshot = repository.publish(repository.import_batch(batch(), key="restart").id)
    repository.database.close()
    db = Database(database_url)
    try:
        restored = Repository(db, clock=lambda: NOW).get_snapshot(snapshot.id)
        assert restored == snapshot
    finally:
        db.close()


@pytest.mark.parametrize(
    "table", ["source_grants", "import_batches", "snapshots", "audit_events"]
)
def test_truncate_cannot_bypass_immutability(repository, table):
    repository.publish(repository.import_batch(batch(), key="a").id)
    with pytest.raises(DBAPIError), repository.database.engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table} CASCADE"))
    assert len(repository.list_snapshots()) == 1


@pytest.mark.parametrize("operation", ["import", "publish", "read", "list"])
def test_source_row_locked_until_operation_finishes(repository, operation):
    record = repository.import_batch(batch(), key="initial")
    snapshot = repository.publish(record.id)
    checked = []

    def check_lock(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" not in statement:
            return
        with pytest.raises(DBAPIError), repository.database.engine.begin() as other:
            other.execute(text("SELECT * FROM source_grants FOR UPDATE NOWAIT"))
        checked.append(True)

    event.listen(repository.database.engine, "after_cursor_execute", check_lock)
    try:
        if operation == "import":
            repository.import_batch(batch(), key="new")
        elif operation == "publish":
            repository.publish(record.id)
        elif operation == "read":
            repository.get_snapshot(snapshot.id)
        else:
            repository.list_snapshots()
    finally:
        event.remove(repository.database.engine, "after_cursor_execute", check_lock)
    assert checked
