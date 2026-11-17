from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from factories import batch
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from dragon_radar.owner import OwnerRepository
from dragon_radar.review.models import NoteContent
from dragon_radar.review.repository import ReviewRepository
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
ORIGIN = "https://localhost:8030"


@pytest.fixture
def private(repository, database):
    market = batch()
    archived = repository.import_batch(market, key="private-fixture")
    snapshot = repository.publish(archived.id)
    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(owners.issue_pairing(ORIGIN), ORIGIN)
    service = ReviewRepository(database, owners, repository, clock=lambda: NOW)
    return service, owners, session, snapshot, market.expected_security_ids[0]


def content(title="Research"):
    return NoteContent(
        title=title,
        judgment="Judgment",
        evidence="Evidence",
        invalidation="Invalidation",
        verification_date=date(2026, 9, 11),
    )


def create(private, key="create"):
    service, _, session, snapshot, sid = private
    return service.create_note(
        session.token,
        session.csrf,
        key=key,
        snapshot_id=snapshot.id,
        security_id=sid,
        content=content(),
    )


def test_note_save_read_and_idempotent_retry(private):
    service, _, session, _, _ = private
    first = create(private)
    assert create(private)["id"] == first["id"]
    assert first["version"] == 1
    assert first["content"] == content().model_dump(mode="json")
    saved = service.save_note(
        session.token,
        session.csrf,
        first["id"],
        key="save",
        expected=1,
        content=content("Updated"),
    )
    assert saved["version"] == 2
    assert (
        service.save_note(
            session.token,
            session.csrf,
            first["id"],
            key="save",
            expected=1,
            content=content("Updated"),
        )
        == saved
    )
    assert service.get_note(session.token, first["id"])["content"]["title"] == "Updated"
    assert len(service.list_revisions(session.token, first["id"])["items"]) == 2
    with pytest.raises(Conflict):
        service.save_note(
            session.token,
            session.csrf,
            first["id"],
            key="other",
            expected=1,
            content=content("Stale"),
        )
    with pytest.raises(Conflict):
        service.save_note(
            session.token,
            session.csrf,
            first["id"],
            key="save",
            expected=1,
            content=content("Different"),
        )


def test_confirm_freezes_and_fork_preserves_revision(private):
    service, _, session, _, _ = private
    first = create(private)
    confirmed = service.confirm_note(
        session.token, session.csrf, first["id"], key="confirm", expected=1
    )
    assert confirmed["state"] == "confirmed"
    with pytest.raises(Conflict):
        service.save_note(
            session.token,
            session.csrf,
            first["id"],
            key="edit-confirmed",
            expected=2,
            content=content("Invalid"),
        )
    fork = service.fork_note(
        session.token,
        session.csrf,
        first["id"],
        version=2,
        key="fork",
    )
    assert fork["id"] != first["id"]
    assert fork["state"] == "draft"
    assert fork["derived_from_revision_id"] == confirmed["revision_id"]


def test_source_revocation_preserves_text_but_hides_facts(private, repository):
    service, _, session, snapshot, _ = private
    first = create(private)
    repository.revoke_source(snapshot.batch.source_id)
    read = service.get_note(session.token, first["id"])
    assert read["content"]["evidence"] == "Evidence"
    assert read["facts"]["status"] == "unavailable"
    assert read["facts"].get("result") is None
    assert (
        service.save_note(
            session.token,
            session.csrf,
            first["id"],
            key="offline-edit",
            expected=1,
            content=content("Private text"),
        )["version"]
        == 2
    )
    with pytest.raises(Forbidden):
        create(private, "new-after-revoke")


def test_watch_remove_undo_and_repeated_add(private):
    service, _, session, snapshot, sid = private
    args = dict(snapshot_id=snapshot.id, security_id=sid)
    first = service.add_watch(session.token, session.csrf, key="watch", **args)
    assert (
        service.add_watch(session.token, session.csrf, key="watch2", **args)["id"]
        == first["id"]
    )
    removed = service.set_watch(
        session.token, session.csrf, first["id"], key="remove", expected=1, active=False
    )
    assert removed["active"] is False
    with pytest.raises(Conflict):
        service.add_watch(session.token, session.csrf, key="watch3", **args)
    restored = service.set_watch(
        session.token, session.csrf, first["id"], key="undo", expected=2, active=True
    )
    assert restored["version"] == 3
    with pytest.raises(Conflict):
        service.set_watch(
            session.token,
            session.csrf,
            first["id"],
            key="old-undo",
            expected=2,
            active=True,
        )


def test_session_and_resource_guards(private):
    from dragon_radar.owner import CsrfForbidden, SessionUnauthorized

    service, owners, session, _, _ = private
    first = create(private)
    with pytest.raises(CsrfForbidden):
        service.save_note(
            session.token, "", first["id"], key="no-csrf", expected=1, content=content()
        )
    with pytest.raises(NotFound):
        service.get_note(session.token, uuid4())
    owners.revoke_all()
    with pytest.raises(SessionUnauthorized):
        service.get_note(session.token, first["id"])


def test_immutable_revision_and_audit(private, database):
    create(private)
    for table in (
        "review_note_revisions",
        "review_verifications",
        "private_audit_events",
    ):
        for operation in (f"DELETE FROM {table}", f"TRUNCATE {table}"):
            with pytest.raises(DBAPIError), database.engine.begin() as connection:
                connection.execute(text(operation))


def test_template_and_verification_are_explicit(private):
    service, _, session, snapshot, _ = private
    template = service.create_template(
        session.token, session.csrf, key="template", snapshot_id=snapshot.id
    )
    assert template["template_version"] == "private-review-v1"
    assert template["state"] == "draft"
    assert "市场观察" in template["content"]["judgment"]
    first = create(private)
    confirmed = service.confirm_note(
        session.token, session.csrf, first["id"], key="confirm", expected=1
    )
    with pytest.raises(ValueError):
        service.add_verification(
            session.token,
            session.csrf,
            first["id"],
            key="verify",
            version=confirmed["version"],
            outcome="supported",
            evidence="Evidence",
            snapshot_id=snapshot.id,
        )
    assert service.list_verifications(session.token, first["id"])["items"] == []


def test_two_owners_cannot_read_update_fork_or_list_each_others_data(private, database):
    import hashlib
    import secrets
    from datetime import timedelta

    from sqlalchemy import insert

    from dragon_radar.owner.models import owner_principals, owner_sessions

    service, _, first_session, snapshot, sid = private
    first = create(private)
    watched = service.add_watch(
        first_session.token,
        first_session.csrf,
        key="watch",
        snapshot_id=snapshot.id,
        security_id=sid,
    )
    other_id, token, csrf = (
        uuid4(),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
    )
    with database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(id=other_id, local_key=None, created_at=NOW)
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=other_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
                origin=ORIGIN,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=8),
            )
        )
    for query in (
        lambda: service.get_note(token, first["id"]),
        lambda: service.list_revisions(token, first["id"]),
        lambda: service.list_verifications(token, first["id"]),
        lambda: service.save_note(
            token, csrf, first["id"], key="save", expected=1, content=content()
        ),
        lambda: service.fork_note(token, csrf, first["id"], key="fork", version=1),
        lambda: service.set_watch(
            token, csrf, watched["id"], key="remove", expected=1, active=False
        ),
    ):
        with pytest.raises(NotFound):
            query()
    assert service.list_notes(token)["items"] == []
    assert service.list_watch(token)["items"] == []
    other = service.create_note(
        token, csrf, key="create", snapshot_id=snapshot.id, content=content()
    )
    assert other["id"] != first["id"]


def test_concurrent_save_has_one_winner(private):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    service, _, session, _, _ = private
    first = create(private)
    barrier = Barrier(2)

    def save(index):
        barrier.wait(timeout=10)
        try:
            return service.save_note(
                session.token,
                session.csrf,
                first["id"],
                key=f"save-{index}",
                expected=1,
                content=content(f"Edit {index}"),
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sum(result is not None for result in results) == 1
    assert service.get_note(session.token, first["id"])["version"] == 2


def test_concurrent_same_key_and_audit_failure_rollback(private, database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event

    service, _, session, _, _ = private
    barrier = Barrier(2)

    def create_one(_):
        barrier.wait(timeout=10)
        return create(private)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(create_one, range(2)))) == 1

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO private_audit_events"):
            raise RuntimeError("Synthetic transaction failure")

    event.listen(database.engine, "before_cursor_execute", fail_audit)
    try:
        with pytest.raises(RuntimeError):
            create(private, "rollback")
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_audit)
    assert len(service.list_notes(session.token)["items"]) == 1
    create(private, "rollback")
    assert len(service.list_notes(session.token)["items"]) == 2


def test_verification_due_date_and_append_only_corrections(private):
    from datetime import timedelta

    service, owners, session, snapshot, sid = private
    authored = NoteContent(
        title="Due",
        judgment="J",
        evidence="E",
        invalidation="I",
        verification_date=snapshot.trade_date,
    )
    note = service.create_note(
        session.token,
        session.csrf,
        key="due",
        snapshot_id=snapshot.id,
        security_id=sid,
        content=authored,
    )
    confirmed = service.confirm_note(
        session.token, session.csrf, note["id"], key="confirm-due", expected=1
    )
    first = service.add_verification(
        session.token,
        session.csrf,
        note["id"],
        key="verified",
        version=confirmed["version"],
        outcome="supported",
        evidence="Observed",
        snapshot_id=snapshot.id,
    )
    second = service.add_verification(
        session.token,
        session.csrf,
        note["id"],
        key="corrected",
        version=confirmed["version"],
        outcome="weakened",
        evidence="Correction",
        snapshot_id=snapshot.id,
        supersedes=first["id"],
    )
    assert second["supersedes"] == first["id"]
    assert len(service.list_verifications(session.token, note["id"])["items"]) == 2
    with pytest.raises(ValueError):
        service.add_verification(
            session.token,
            session.csrf,
            note["id"],
            key="false-not-due",
            version=2,
            outcome="not_due",
        )
    future = authored.model_copy(
        update={"verification_date": (NOW + timedelta(days=1)).date()}
    )
    pending = service.create_note(
        session.token,
        session.csrf,
        key="future",
        snapshot_id=snapshot.id,
        content=future,
    )
    service.confirm_note(
        session.token, session.csrf, pending["id"], key="confirm-future", expected=1
    )
    assert (
        service.add_verification(
            session.token,
            session.csrf,
            pending["id"],
            key="not-due",
            version=2,
            outcome="not_due",
        )["outcome"]
        == "not_due"
    )


@pytest.mark.parametrize("version", [None, True, 0, -1, "1", 1.0])
def test_explicit_revision_rejects_invalid_values(private, version):
    service, _, session, _, _ = private
    first = create(private)
    with pytest.raises(ValueError):
        service.get_revision(session.token, first["id"], version)
    with pytest.raises(ValueError):
        service.fork_note(
            session.token,
            session.csrf,
            first["id"],
            key="invalid-fork",
            version=version,
        )


def test_watch_replay_exposes_original_commit_separately(private):
    service, _, session, snapshot, sid = private
    args = dict(snapshot_id=snapshot.id, security_id=sid)
    first = service.add_watch(session.token, session.csrf, key="watch", **args)
    service.set_watch(
        session.token, session.csrf, first["id"], key="remove", expected=1, active=False
    )
    service.set_watch(
        session.token, session.csrf, first["id"], key="undo", expected=2, active=True
    )
    replay = service.set_watch(
        session.token, session.csrf, first["id"], key="remove", expected=1, active=False
    )
    assert replay["committed"] == {"id": first["id"], "version": 2}
    assert replay["version"] == 3
    assert replay["active"] is True


def test_fork_inherits_unavailable_reference_but_rebinding_is_checked(
    private, repository
):
    service, _, session, snapshot, _ = private
    first = create(private)
    service.confirm_note(
        session.token, session.csrf, first["id"], key="confirm", expected=1
    )
    repository.revoke_source(snapshot.batch.source_id)
    fork = service.fork_note(
        session.token, session.csrf, first["id"], key="fork", version=2
    )
    assert fork["state"] == "draft"
    assert fork["facts"]["status"] == "unavailable"
    with pytest.raises(Forbidden):
        service.fork_note(
            session.token,
            session.csrf,
            first["id"],
            key="explicit-target",
            version=2,
            snapshot_id=snapshot.id,
        )


@pytest.mark.parametrize("bad", [None, "", False, 0])
def test_required_ids_and_watch_security_are_strict(private, bad):
    service, _, session, snapshot, _ = private
    with pytest.raises(ValueError):
        service.list_revisions(session.token, bad)
    with pytest.raises(ValueError):
        service.list_verifications(session.token, bad)
    with pytest.raises(ValueError):
        service.add_watch(
            session.token,
            session.csrf,
            key="invalid",
            snapshot_id=snapshot.id,
            security_id=bad,
        )


@pytest.mark.parametrize("bad", ["", False, 0])
def test_explicit_fork_target_is_not_truthiness_fallback(private, bad):
    service, _, session, _, _ = private
    first = create(private)
    with pytest.raises(ValueError):
        service.fork_note(
            session.token,
            session.csrf,
            first["id"],
            key="bad",
            version=1,
            snapshot_id=bad,
        )


def test_private_list_acquires_source_locks_in_canonical_order(
    private, repository, monkeypatch
):
    from factories import bar, grant, state

    service, _, session, _, _ = private
    snapshots_by_source = {}
    for source_id in ("a-source", "z-source"):
        value = batch(
            source_id=source_id,
            grant=grant(source_id=source_id),
            security_states=(state(source_id=source_id),),
            bars=tuple(
                bar(trade_date=day, source_id=source_id)
                for day in batch().calendar.sessions
            ),
        )
        repository.register_source(value.grant)
        record = repository.import_batch(value, key=source_id)
        snapshot = repository.publish(record.id)
        snapshots_by_source[source_id] = snapshot.id
        service.create_note(
            session.token,
            session.csrf,
            key=source_id,
            snapshot_id=snapshot.id,
            content=content(),
        )
    order = []
    original = repository._source

    def observed(connection, source_id):
        if source_id not in order:
            order.append(source_id)
        return original(connection, source_id)

    monkeypatch.setattr(repository, "_source", observed)
    service.list_notes(session.token)
    assert order == sorted(order)
    service.add_watch(
        session.token,
        session.csrf,
        key="first-source",
        snapshot_id=snapshots_by_source["a-source"],
        security_id="A",
    )
    order.clear()
    service.add_watch(
        session.token,
        session.csrf,
        key="new-source",
        snapshot_id=snapshots_by_source["z-source"],
        security_id="A",
    )
    assert order == ["a-source", "z-source"]
    assert service.list_watch(session.token)["items"]


def test_metadata_constraints_and_indexes_match_migration(private, database):
    from sqlalchemy import CheckConstraint, inspect

    from dragon_radar.review import tables

    inspector = inspect(database.engine)
    for name in (
        "watch_entries",
        "review_notes",
        "review_note_revisions",
        "review_verifications",
    ):
        table = getattr(tables, name)
        expected = {
            item.name for item in table.constraints if isinstance(item, CheckConstraint)
        }
        assert expected == {
            item["name"] for item in inspector.get_check_constraints(name)
        }
        assert {item.name for item in table.indexes} == {
            item["name"]
            for item in inspector.get_indexes(name)
            if not item.get("duplicates_constraint")
        }


def test_database_rejects_verification_from_another_note(private, database):
    from sqlalchemy import insert

    from dragon_radar.review.tables import review_verifications

    service, _, session, snapshot, _ = private
    first = create(private)
    second = create(private, "second")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(review_verifications).values(
                id=uuid4(),
                owner_id=session.owner_id,
                note_id=first["id"],
                revision_id=second["revision_id"],
                snapshot_id=snapshot.id,
                outcome="supported",
                evidence="Unrelated",
                created_at=NOW,
            )
        )
