"""Transaction-local projection costs and current authorization."""

from datetime import UTC, datetime

import pytest
from factories import DAYS, bar, batch, state
from sqlalchemy import event
from test_review_repository import content, create
from test_review_repository import private as private  # noqa: F401

from dragon_radar.review.facts import TransactionFacts


def test_list_parses_each_snapshot_once_and_batches_current_revisions(
    private, monkeypatch
):
    service, _, session, _, _ = private
    for index in range(20):
        create(private, key=f"note-{index}")
    parsed, statements = [], []
    original = service.market._snapshot

    def parse(row, archive):
        parsed.append(row["id"])
        return original(row, archive)

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(service.market, "_snapshot", parse)
    event.listen(service.database.engine, "before_cursor_execute", observe)
    try:
        result = service.list_notes(session.token, page_size=20)
    finally:
        event.remove(service.database.engine, "before_cursor_execute", observe)
    assert len(result["items"]) == 20
    assert len(parsed) == 1
    assert sum("FROM import_batches" in sql for sql in statements) == 1
    assert sum("FROM review_note_revisions" in sql for sql in statements) == 1
    assert len(statements) <= 12
    assert all(item["facts"]["result"]["close"] == "10" for item in result["items"])
    service.list_notes(session.token, page_size=20)
    assert len(parsed) == 2


def test_reused_facts_recheck_grant_expiry_on_every_consumption(private, monkeypatch):
    service, _, session, _, _ = private
    for index in range(3):
        create(private, key=f"note-{index}")
    original = service.market._authorize
    checked = []

    def authorize(current, batch):
        checked.append(batch.source_id)
        if len(checked) > 1:
            service.market.clock = lambda: datetime(2027, 1, 1, tzinfo=UTC)
        return original(current, batch)

    monkeypatch.setattr(service.market, "_authorize", authorize)
    result = service.list_notes(session.token, page_size=20)
    assert result["items"][0]["facts"]["status"] == "available"
    assert all(
        item["facts"] == {"status": "unavailable"} for item in result["items"][1:]
    )
    assert len(checked) == 3


@pytest.mark.parametrize("listing", ["notes", "watch"])
def test_transaction_projection_does_not_cache_revoked_facts(private, listing):
    service, _, session, snapshot, sid = private
    create(private)
    service.add_watch(
        session.token,
        session.csrf,
        key="watch",
        snapshot_id=snapshot.id,
        security_id=sid,
    )
    read = service.list_notes if listing == "notes" else service.list_watch
    assert read(session.token)["items"][0]["facts"]["status"] == "available"
    service.market.revoke_source(snapshot.batch.source_id)
    assert read(session.token)["items"][0]["facts"] == {"status": "unavailable"}


def test_projection_refuses_reuse_after_transaction_end(private):
    service, _, _, snapshot, sid = private
    with service.database.engine.connect() as connection:
        with pytest.raises(ValueError):
            TransactionFacts(connection, service.market)
        with connection.begin():
            facts = TransactionFacts(connection, service.market)
            assert facts.facts(snapshot.id, sid)["status"] == "available"
        with pytest.raises(ValueError):
            facts.facts(snapshot.id, sid)
        with connection.begin(), pytest.raises(ValueError):
            facts.facts(snapshot.id, sid)


def test_watch_projection_reuses_one_archive_and_index_for_multiple_securities(
    private, monkeypatch
):
    service, _, session, _, _ = private
    market = batch(
        expected_security_ids=("A", "B", "C"),
        security_states=tuple(state(sid) for sid in ("A", "B", "C")),
        bars=tuple(bar(sid, day) for sid in ("A", "B", "C") for day in DAYS),
    )
    archived = service.market.import_batch(market, key="many-securities")
    snapshot = service.market.publish(archived.id)
    for sid in market.expected_security_ids:
        service.add_watch(
            session.token,
            session.csrf,
            key=sid,
            snapshot_id=snapshot.id,
            security_id=sid,
        )
    parsed = []
    original = service.market._snapshot

    def parse(row, archive):
        parsed.append(row["id"])
        return original(row, archive)

    monkeypatch.setattr(service.market, "_snapshot", parse)
    result = service.list_watch(session.token)
    assert len(parsed) == 1
    assert {item["facts"]["result"]["security_id"] for item in result["items"]} == {
        "A",
        "B",
        "C",
    }
    assert all("committed" not in item for item in result["items"])


def test_list_parsing_cost_tracks_distinct_snapshots(private, monkeypatch):
    service, _, session, snapshot, sid = private
    create(private)
    archived = service.market.import_batch(
        batch(provenance="retrospective"), key="second-snapshot"
    )
    other = service.market.publish(archived.id)
    for index in range(3):
        service.create_note(
            session.token,
            session.csrf,
            key=f"second-{index}",
            snapshot_id=other.id,
            security_id=sid,
            content=content(),
        )
    parsed = []
    original = service.market._snapshot

    def parse(row, archive):
        parsed.append(row["id"])
        return original(row, archive)

    monkeypatch.setattr(service.market, "_snapshot", parse)
    result = service.list_notes(session.token)
    assert len(result["items"]) == 4
    assert len(parsed) == 2
    assert set(parsed) == {snapshot.id, other.id}
