"""Response shapes, write transaction credentials and replay contracts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from test_review_api import payload, run
from test_review_api import setup as setup  # noqa: F401
from test_review_api_boundaries import PREFIX, counts, error
from test_review_api_workflows import confirm, create


@pytest.mark.parametrize(
    "action",
    [
        "create",
        "save",
        "confirm",
        "fork",
        "watch_add",
        "watch_set",
        "template",
        "verify",
    ],
)
def test_every_write_reauthorizes_and_replays_without_audit_duplication(
    setup, database, monkeypatch, action
):
    service = setup[0].state.review_repository
    service.clock = lambda: datetime(2026, 9, 11, tzinfo=UTC)

    async def scenario(client, snapshot):
        content = {
            **payload(snapshot)["content"],
            "verification_date": str(snapshot.trade_date),
        }
        note = await create(client, snapshot, "initial", content=content)
        frozen = await confirm(client, note)
        watch = (
            await client.post(
                PREFIX + "/watchlist",
                json={"snapshot_id": str(snapshot.id), "security_id": "A"},
                headers={"Idempotency-Key": "initial-watch"},
            )
        ).json()
        draft = await create(client, snapshot, "draft")
        method, path, body, expected = {
            "create": ("POST", "/notes", payload(snapshot), None),
            "save": (
                "PATCH",
                f"/notes/{draft['id']}",
                {"content": {"title": "Saved"}},
                '"1"',
            ),
            "confirm": ("POST", f"/notes/{draft['id']}/confirm", {}, '"1"'),
            "fork": ("POST", f"/notes/{note['id']}/fork", {"version": 2}, None),
            "watch_add": (
                "POST",
                "/watchlist",
                {"snapshot_id": str(snapshot.id), "security_id": "A"},
                None,
            ),
            "watch_set": (
                "PATCH",
                f"/watchlist/{watch['id']}",
                {"active": False},
                '"1"',
            ),
            "template": (
                "POST",
                "/review-templates",
                {"snapshot_id": str(snapshot.id)},
                None,
            ),
            "verify": (
                "POST",
                f"/notes/{note['id']}/verifications",
                {
                    "version": frozen["version"],
                    "outcome": "supported",
                    "snapshot_id": str(snapshot.id),
                    "evidence": "Observed",
                },
                None,
            ),
        }[action]
        headers = {"Idempotency-Key": "command"}
        if expected:
            headers["If-Match"] = expected
        authorized = []
        inserts = []
        original = service.owners.authorize_connection

        def authorize(connection, token, csrf=None):
            assert csrf == client.headers["X-CSRF-Token"]
            assert f"session={token};" in client.headers["Cookie"]
            identity = original(connection, token, csrf)
            authorized.append(connection)
            return identity

        def observe(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO private_audit_events"):
                assert any(connection is item for item in authorized)
                inserts.append(connection)

        monkeypatch.setattr(service.owners, "authorize_connection", authorize)
        before = counts(database)
        event.listen(database.engine, "before_cursor_execute", observe)
        try:
            first = await client.request(
                method, PREFIX + path, json=body, headers=headers
            )
            assert first.status_code == 200, first.text
            assert len(authorized) == 2
            assert len(inserts) == 1
            assert inserts[0] is authorized[1]
            replay = await client.request(
                method, PREFIX + path, json=body, headers=headers
            )
            assert replay.status_code == 200
            assert replay.json() == first.json()
            assert len(authorized) == 4
            assert len(inserts) == 1
            assert counts(database) == tuple(value + 1 for value in before)
            if expected:
                error(
                    await client.request(
                        method,
                        PREFIX + path,
                        json=body,
                        headers={**headers, "If-Match": '"2"'},
                    ),
                    409,
                    "conflict",
                )
        finally:
            event.remove(database.engine, "before_cursor_execute", observe)

    run(setup, scenario)


def test_response_fields_match_documented_contract(setup):
    setup[0].state.review_repository.clock = lambda: datetime(2026, 9, 11, tzinfo=UTC)

    async def scenario(client, snapshot):
        note = await create(
            client,
            snapshot,
            security_id="A",
            content={
                **payload(snapshot)["content"],
                "verification_date": str(snapshot.trade_date),
            },
        )
        assert set(note) == {
            "id",
            "owner_id",
            "snapshot_id",
            "trade_date",
            "security_id",
            "kind",
            "version",
            "state",
            "derived_from_revision_id",
            "template_version",
            "created_at",
            "updated_at",
            "revision_id",
            "saved_at",
            "content",
            "facts",
        }
        assert set(note["facts"]) == {
            "status",
            "snapshot_id",
            "trade_date",
            "as_of",
            "source_time",
            "provenance",
            "manifest",
            "result",
        }
        assert set(note["facts"]["result"]) == {
            "security_id",
            "name",
            "exchange",
            "board",
            "close",
            "change",
            "amount",
            "suspended",
            "missing_reasons",
            "change_unit",
            "currency",
            "price_unit",
            "volume_unit",
            "amount_unit",
        }
        assert note["facts"]["result"]["close"] == "10"
        url = f"{PREFIX}/notes/{note['id']}"
        revisions = (await client.get(url + "/revisions")).json()
        assert set(revisions) == {"items", "page", "page_size", "has_more"}
        revision = revisions["items"][0]
        assert set(revision) == {
            "id",
            "owner_id",
            "note_id",
            "version",
            "state",
            "content",
            "saved_at",
        }
        assert revision["id"] == note["revision_id"]
        assert (await client.get(url + "/revisions/1")).json() == note
        added = (
            await client.post(
                PREFIX + "/watchlist",
                json={"snapshot_id": str(snapshot.id), "security_id": "A"},
                headers={"Idempotency-Key": "watch"},
            )
        ).json()
        assert set(added) == {
            "id",
            "owner_id",
            "security_id",
            "snapshot_id",
            "active",
            "version",
            "added_at",
            "updated_at",
            "facts",
            "committed",
        }
        listed = (await client.get(PREFIX + "/watchlist")).json()["items"][0]
        assert set(listed) == set(added) - {"committed"}
        frozen = await confirm(client, note)
        verification = (
            await client.post(
                url + "/verifications",
                json={
                    "version": 2,
                    "outcome": "supported",
                    "evidence": "Observed",
                    "snapshot_id": str(snapshot.id),
                },
                headers={"Idempotency-Key": "verify"},
            )
        ).json()
        assert set(verification) == {
            "id",
            "owner_id",
            "note_id",
            "revision_id",
            "snapshot_id",
            "outcome",
            "evidence",
            "supersedes",
            "created_at",
        }
        assert verification["revision_id"] == frozen["revision_id"]
        assert (await client.get(url + "/verifications")).json()["items"] == [
            verification
        ]

    run(setup, scenario)


def test_verification_replay_retains_reference_without_cached_facts(
    setup, repository, database
):
    setup[0].state.review_repository.clock = lambda: datetime(2026, 9, 11, tzinfo=UTC)

    async def scenario(client, snapshot):
        note = await create(
            client,
            snapshot,
            content={
                **payload(snapshot)["content"],
                "verification_date": str(snapshot.trade_date),
            },
        )
        await confirm(client, note)
        path = f"{PREFIX}/notes/{note['id']}/verifications"
        body = {
            "version": 2,
            "outcome": "supported",
            "evidence": "Observed",
            "snapshot_id": str(snapshot.id),
        }
        headers = {"Idempotency-Key": "verify"}
        first = await client.post(path, json=body, headers=headers)
        assert first.status_code == 200
        before = counts(database)
        repository.revoke_source(snapshot.batch.source_id)
        replay = await client.post(path, json=body, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert "facts" not in replay.json()
        assert counts(database) == before
        error(
            await client.post(path, json=body, headers={"Idempotency-Key": "new"}),
            403,
            "source_forbidden",
        )
        error(
            await client.get(f"/api/v1/snapshots/{snapshot.id}"),
            403,
            "source_forbidden",
        )

    run(setup, scenario)


def test_watch_source_loss_replay_and_revoked_session(setup, repository, database):
    async def scenario(client, snapshot):
        body = {"snapshot_id": str(snapshot.id), "security_id": "A"}
        path = PREFIX + "/watchlist"
        first = await client.post(path, json=body)
        assert first.status_code == 200
        repository.revoke_source(snapshot.batch.source_id)
        before = counts(database)
        replay = await client.post(path, json=body)
        assert replay.status_code == 200
        assert replay.json()["facts"] == {"status": "unavailable"}
        assert replay.json()["committed"] == first.json()["committed"]
        assert counts(database) == before
        removed = await client.patch(
            f"{path}/{first.json()['id']}",
            json={"active": False},
            headers={"Idempotency-Key": "remove", "If-Match": '"1"'},
        )
        assert removed.status_code == 200
        assert removed.json()["facts"] == {"status": "unavailable"}
        before = counts(database)
        setup[0].state.owner_repository.revoke_all()
        error(await client.post(path, json=body), 401, "session_unauthorized")
        assert counts(database) == before

    run(setup, scenario)


def test_conflict_returns_current_owned_text_not_failed_input(setup):
    async def scenario(client, snapshot):
        note = await create(client, snapshot)
        path = f"{PREFIX}/notes/{note['id']}"
        saved = await client.patch(
            path,
            json={"content": {"title": "Server version"}},
            headers={"Idempotency-Key": "save", "If-Match": '"1"'},
        )
        assert saved.status_code == 200
        response = await client.patch(
            path,
            json={"content": {"title": "secret-marker"}},
            headers={"Idempotency-Key": "stale", "If-Match": '"1"'},
        )
        error(response, 409, "version_conflict")
        assert response.json()["current"] == saved.json()

    run(setup, scenario)
