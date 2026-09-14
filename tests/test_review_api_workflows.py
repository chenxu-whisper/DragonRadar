"""Private workflows exercised end to end through HTTP and real transactions."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert
from test_review_api import ORIGIN, payload, run
from test_review_api import setup as setup  # noqa: F401
from test_review_api_boundaries import PREFIX, counts, error

from dragon_radar.owner.models import owner_principals, owner_sessions


async def create(client, snapshot, key="create", **changes):
    body = payload(snapshot)
    body.update(changes)
    response = await client.post(
        PREFIX + "/notes", json=body, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(client, note):
    response = await client.post(
        f"{PREFIX}/notes/{note['id']}/confirm",
        json={},
        headers={
            "Idempotency-Key": "confirm-" + note["id"],
            "If-Match": f'"{note["version"]}"',
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_template_regeneration_is_independent_and_confirmation_freezes(setup):
    async def scenario(client, snapshot):
        path = PREFIX + "/review-templates"
        body = {"snapshot_id": str(snapshot.id)}
        first_response = await client.post(path, json=body)
        assert first_response.status_code == 200
        first = first_response.json()
        assert first["template_version"] == "private-review-v1"
        assert first["content"] == {
            "title": f"{snapshot.trade_date} \u590d\u76d8",
            "judgment": "\u5e02\u573a\u89c2\u5bdf\n\n\u5f85\u9a8c\u8bc1\u4e8b\u9879\n",
            "evidence": "",
            "invalidation": "",
            "verification_date": None,
        }
        url = f"{PREFIX}/notes/{first['id']}"
        error(
            await client.post(
                url + "/confirm",
                json={},
                headers={"Idempotency-Key": "incomplete", "If-Match": '"1"'},
            ),
            422,
            "invalid_request",
        )
        saved = await client.patch(
            url,
            json={"content": payload(snapshot)["content"]},
            headers={"Idempotency-Key": "edit", "If-Match": '"1"'},
        )
        assert saved.status_code == 200
        frozen = await confirm(client, saved.json())
        for method, suffix, data in (
            ("PATCH", "", {"content": {"title": "Must not replace"}}),
            ("POST", "/confirm", {}),
        ):
            response = await client.request(
                method,
                url + suffix,
                json=data,
                headers={"Idempotency-Key": "frozen-" + method, "If-Match": '"3"'},
            )
            error(response, 409, "version_conflict")
            assert response.json()["current"]["revision_id"] == frozen["revision_id"]
        second = (
            await client.post(
                path, json=body, headers={"Idempotency-Key": "new-template"}
            )
        ).json()
        assert second["id"] != first["id"]
        assert second["content"] == first["content"]
        assert (await client.get(url)).json()["content"] == frozen["content"]
        fork = (
            await client.post(
                url + "/fork", json={"version": 3}, headers={"Idempotency-Key": "fork"}
            )
        ).json()
        assert fork["state"] == "draft"
        assert fork["derived_from_revision_id"] == frozen["revision_id"]
        assert fork["content"] == frozen["content"]

    run(setup, scenario)


def test_verification_due_boundary_and_append_only_correction(setup):
    service = setup[0].state.review_repository
    # 15:59:59 UTC is still the previous date in Beijing.
    clock = [datetime(2026, 9, 7, 15, 59, 59, tzinfo=UTC)]
    service.clock = lambda: clock[0]

    async def scenario(client, snapshot):
        content = {
            **payload(snapshot)["content"],
            "verification_date": str(snapshot.trade_date),
        }
        note = await create(client, snapshot, content=content, security_id="A")
        url = f"{PREFIX}/notes/{note['id']}/verifications"
        error(
            await client.post(
                url,
                json={"version": 1, "outcome": "not_due"},
                headers={"Idempotency-Key": "draft"},
            ),
            422,
            "invalid_request",
        )
        frozen = await confirm(client, note)
        due = {
            "version": 2,
            "outcome": "supported",
            "evidence": "Observed",
            "snapshot_id": str(snapshot.id),
        }
        error(
            await client.post(url, json=due, headers={"Idempotency-Key": "early"}),
            422,
            "invalid_request",
        )
        pending = await client.post(
            url,
            json={"version": 2, "outcome": "not_due"},
            headers={"Idempotency-Key": "pending"},
        )
        assert pending.status_code == 200
        assert pending.json()["snapshot_id"] is None
        error(
            await client.post(
                url,
                json={
                    "version": 2,
                    "outcome": "not_due",
                    "snapshot_id": str(snapshot.id),
                },
                headers={"Idempotency-Key": "false-facts"},
            ),
            422,
            "invalid_request",
        )
        clock[0] += timedelta(seconds=1)
        error(
            await client.post(
                url,
                json={"version": 2, "outcome": "not_due"},
                headers={"Idempotency-Key": "late-pending"},
            ),
            422,
            "invalid_request",
        )
        for key, body in (
            ("no-evidence", {**due, "evidence": " "}),
            ("no-snapshot", {**due, "snapshot_id": None}),
        ):
            error(
                await client.post(url, json=body, headers={"Idempotency-Key": key}),
                422,
                "invalid_request",
            )
        first = await client.post(url, json=due, headers={"Idempotency-Key": "due"})
        assert first.status_code == 200
        assert first.json()["revision_id"] == frozen["revision_id"]
        previous = first.json()["id"]
        for outcome in ("weakened", "refuted"):
            corrected = await client.post(
                url,
                json={**due, "outcome": outcome, "supersedes": previous},
                headers={"Idempotency-Key": outcome},
            )
            assert corrected.status_code == 200
            assert corrected.json()["supersedes"] == previous
            previous = corrected.json()["id"]
        records = (await client.get(url)).json()["items"]
        assert len(records) == 4
        assert (
            next(item for item in records if item["id"] == first.json()["id"])
            == first.json()
        )
        error(
            await client.post(
                url,
                json={**due, "supersedes": str(uuid4())},
                headers={"Idempotency-Key": "missing-prior"},
            ),
            404,
            "not_found",
        )
        other = await create(client, snapshot, "other", content=content)
        await confirm(client, other)
        error(
            await client.post(
                f"{PREFIX}/notes/{other['id']}/verifications",
                json={**due, "supersedes": first.json()["id"]},
                headers={"Idempotency-Key": "wrong-note"},
            ),
            404,
            "not_found",
        )

    run(setup, scenario)


def test_verification_snapshot_cannot_predate_due_date(setup):
    setup[0].state.review_repository.clock = lambda: datetime(2026, 9, 12, tzinfo=UTC)

    async def scenario(client, snapshot):
        note = await create(client, snapshot)
        await confirm(client, note)
        error(
            await client.post(
                f"{PREFIX}/notes/{note['id']}/verifications",
                json={
                    "version": 2,
                    "outcome": "supported",
                    "evidence": "Observed",
                    "snapshot_id": str(snapshot.id),
                },
                headers={"Idempotency-Key": "old-snapshot"},
            ),
            422,
            "invalid_request",
        )

    run(setup, scenario)


def test_source_loss_keeps_original_text_and_inherited_fork(
    setup, repository, database
):
    async def scenario(client, snapshot):
        original = await create(client, snapshot, security_id="A")
        assert original["facts"]["status"] == "available"
        assert original["facts"]["result"]["name"] == "Synthetic A"
        frozen = await confirm(client, original)
        repository.revoke_source(snapshot.batch.source_id)
        before = counts(database)
        # Replay resolves the original revision, but never cached market facts.
        replay = await create(client, snapshot, security_id="A")
        assert replay["revision_id"] == original["revision_id"]
        assert replay["facts"] == {"status": "unavailable"}
        assert counts(database) == before
        url = f"{PREFIX}/notes/{original['id']}"
        for path in (url, url + "/revisions/1"):
            read = (await client.get(path)).json()
            assert read["content"] == original["content"]
            assert read["facts"] == {"status": "unavailable"}
            assert "Synthetic A" not in str(read)
        fork = (
            await client.post(
                url + "/fork",
                json={"version": 2},
                headers={"Idempotency-Key": "inherit"},
            )
        ).json()
        assert fork["derived_from_revision_id"] == frozen["revision_id"]
        assert fork["snapshot_id"] == original["snapshot_id"]
        assert fork["facts"] == {"status": "unavailable"}
        edited = await client.patch(
            f"{PREFIX}/notes/{fork['id']}",
            json={"content": {"title": "Private edit"}},
            headers={"Idempotency-Key": "edit-unavailable", "If-Match": '"1"'},
        )
        assert edited.status_code == 200
        assert edited.json()["facts"] == {"status": "unavailable"}
        for path, body in (
            ("/notes", payload(snapshot)),
            ("/review-templates", {"snapshot_id": str(snapshot.id)}),
            ("/watchlist", {"snapshot_id": str(snapshot.id), "security_id": "A"}),
            (
                f"/notes/{original['id']}/fork",
                {"version": 2, "snapshot_id": str(snapshot.id)},
            ),
        ):
            error(
                await client.post(
                    PREFIX + path,
                    json=body,
                    headers={"Idempotency-Key": "new-" + uuid4().hex},
                ),
                403,
                "source_forbidden",
            )
        # Distinct failure domains, with otherwise valid requests.
        error(
            await client.get(f"/api/v1/snapshots/{snapshot.id}"),
            403,
            "source_forbidden",
        )
        csrf = client.headers.pop("X-CSRF-Token")
        error(
            await client.post(PREFIX + "/notes", json=payload(snapshot)),
            403,
            "csrf_forbidden",
        )
        client.headers["X-CSRF-Token"] = csrf
        client.headers.pop("Cookie")
        error(await client.get(url), 401, "session_unauthorized")

    run(setup, scenario)


def test_cross_owner_resources_are_indistinguishable_from_missing(setup, database):
    owners = setup[0].state.owner_repository
    now = owners.clock()
    token, csrf, other_id = (
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        uuid4(),
    )
    with database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(id=other_id, local_key=None, created_at=now)
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=other_id,
                origin=ORIGIN,
                created_at=now,
                expires_at=now + timedelta(hours=8),
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                csrf_hash=hashlib.sha256(csrf.encode()).hexdigest(),
            )
        )

    async def scenario(client, snapshot):
        note = await create(client, snapshot)
        watch = (
            await client.post(
                PREFIX + "/watchlist",
                json={"snapshot_id": str(snapshot.id), "security_id": "A"},
                headers={"Idempotency-Key": "watch"},
            )
        ).json()
        client.headers.update(
            {
                "Cookie": (
                    f"__Host-dragon-private-session={token}; "
                    f"__Host-dragon-private-csrf={csrf}"
                ),
                "X-CSRF-Token": csrf,
            }
        )
        before = counts(database)
        for method, suffix, body in (
            ("GET", "", None),
            ("GET", "/revisions", None),
            ("GET", "/revisions/1", None),
            ("GET", "/verifications", None),
            ("PATCH", "", {"content": {"title": "secret-marker"}}),
            ("POST", "/confirm", {}),
            ("POST", "/fork", {"version": 1}),
            ("POST", "/verifications", {"version": 1, "outcome": "not_due"}),
        ):
            responses = []
            for record_id in (note["id"], str(uuid4())):
                response = await client.request(
                    method,
                    f"{PREFIX}/notes/{record_id}{suffix}",
                    json=body,
                    headers={"Idempotency-Key": uuid4().hex, "If-Match": '"1"'},
                )
                error(response, 404, "not_found")
                responses.append(response.json())
            assert responses[0] == responses[1]
        error(
            await client.patch(
                f"{PREFIX}/watchlist/{watch['id']}",
                json={"active": False},
                headers={"Idempotency-Key": "remove", "If-Match": '"1"'},
            ),
            404,
            "not_found",
        )
        assert counts(database) == before
        for path in ("/notes", "/watchlist"):
            assert (await client.get(PREFIX + path)).json()["items"] == []
        own = await create(client, snapshot)
        assert own["owner_id"] == str(other_id)
        assert own["id"] != note["id"]

    run(setup, scenario)


def test_lost_response_replay_and_current_watch_commit(setup, database):
    async def scenario(client, snapshot):
        path = PREFIX + "/watchlist"
        added = (
            await client.post(
                path, json={"snapshot_id": str(snapshot.id), "security_id": "A"}
            )
        ).json()
        url = f"{path}/{added['id']}"
        remove_headers = {"Idempotency-Key": "remove", "If-Match": '"1"'}
        await client.patch(url, json={"active": False}, headers=remove_headers)
        restored = await client.patch(
            url,
            json={"active": True},
            headers={"Idempotency-Key": "undo", "If-Match": '"2"'},
        )
        assert restored.json()["version"] == 3
        before = counts(database)
        replay = await client.patch(url, json={"active": False}, headers=remove_headers)
        assert replay.status_code == 200
        assert replay.json()["active"] is True
        assert replay.json()["version"] == 3
        assert replay.json()["committed"] == {"id": added["id"], "version": 2}
        assert counts(database) == before
        conflict = await client.patch(
            url,
            json={"active": False},
            headers={"Idempotency-Key": "stale", "If-Match": '"2"'},
        )
        error(conflict, 409, "version_conflict")
        assert conflict.json()["current"]["version"] == 3
        assert conflict.json()["current"]["id"] == added["id"]

    run(setup, scenario)


def test_transport_loss_retry_writes_once_and_same_key_changes_conflict(
    setup, database
):
    app = setup[0]

    class LoseFirstResponse(httpx.ASGITransport):
        lost = False

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.method == "POST" and not self.lost:
                self.lost = True
                await response.aclose()
                raise httpx.ReadError("Synthetic response loss", request=request)
            return response

    async def scenario(client, snapshot):
        async with httpx.AsyncClient(
            transport=LoseFirstResponse(app=app),
            base_url=ORIGIN,
            headers=setup[1],
        ) as unreliable:
            with pytest.raises(httpx.ReadError, match="Synthetic response loss"):
                await unreliable.post(PREFIX + "/notes", json=payload(snapshot))
        assert counts(database) == (1, 1)
        recovered = await create(client, snapshot)
        assert recovered["version"] == 1
        assert counts(database) == (1, 1)
        error(
            await client.post(
                PREFIX + "/notes",
                json={**payload(snapshot), "content": {"title": "Changed"}},
            ),
            409,
            "conflict",
        )
        error(
            await client.post(
                PREFIX + "/review-templates", json={"snapshot_id": str(snapshot.id)}
            ),
            409,
            "conflict",
        )
        assert counts(database) == (1, 1)

    run(setup, scenario)


def test_plain_text_roundtrip_pagination_and_read_does_not_write(setup, database):
    async def scenario(client, snapshot):
        text = '<script>alert("literal")</script><img src="https://invalid.test">'
        content = {"title": text, "judgment": text}
        for index in range(21):
            await create(client, snapshot, f"note-{index}", content=content)
        before = counts(database)
        first = (await client.get(PREFIX + "/notes?page_size=20")).json()
        second = (await client.get(PREFIX + "/notes?page_size=20&page=2")).json()
        assert len(first["items"]) == 20 and first["has_more"]
        assert len(second["items"]) == 1 and not second["has_more"]
        assert len({item["id"] for item in first["items"] + second["items"]}) == 21
        assert all(item["content"]["judgment"] == text for item in first["items"])
        again = (await client.get(PREFIX + "/notes?page_size=20")).json()
        assert again == first
        assert (await client.get(PREFIX + "/notes?state=confirmed")).json()[
            "items"
        ] == []
        assert counts(database) == before

    run(setup, scenario)
