import asyncio

import httpx
import pytest
from factories import batch

from dragon_radar.owner.app import create_owner_app
from dragon_radar.owner.repository import OwnerRepository
from dragon_radar.review.routes import install_review_routes

ORIGIN = "https://localhost:8030"


@pytest.fixture
def setup(repository, database):
    record = repository.import_batch(batch(), key="api-review")
    snapshot = repository.publish(record.id)
    owners = OwnerRepository(database)
    owners.initialize()
    issued = owners.exchange_pairing(owners.issue_pairing(ORIGIN), ORIGIN)
    app = create_owner_app(
        repository=repository, owner_repository=owners, origin=ORIGIN
    )
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": issued.csrf,
        "Cookie": (
            f"__Host-dragon-private-session={issued.token}; "
            f"__Host-dragon-private-csrf={issued.csrf}"
        ),
        "Idempotency-Key": "create",
    }
    return app, headers, snapshot


def run(setup, scenario):
    app, headers, snapshot = setup

    async def execute():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=headers
            ) as client,
        ):
            await scenario(client, snapshot)

    asyncio.run(execute())


def payload(snapshot):
    return {
        "snapshot_id": str(snapshot.id),
        "content": {
            "title": "Private",
            "judgment": "J",
            "evidence": "E",
            "invalidation": "I",
            "verification_date": "2026-09-11",
        },
    }


def test_note_http_crud_conflict_confirmation_and_fork(setup):
    async def scenario(client, snapshot):
        first = await client.post("/api/owner/v1/notes", json=payload(snapshot))
        assert first.status_code == 200
        note = first.json()
        url = f"/api/owner/v1/notes/{note['id']}"
        assert first.headers["cache-control"] == "no-store"
        updated = {**note["content"], "title": "Updated"}
        missing = await client.patch(url, json={"content": updated})
        assert missing.status_code == 428
        saved = await client.patch(
            url,
            json={"content": updated},
            headers={"Idempotency-Key": "save", "If-Match": '"1"'},
        )
        assert saved.status_code == 200
        stale = await client.patch(
            url,
            json={"content": updated},
            headers={"Idempotency-Key": "stale", "If-Match": '"1"'},
        )
        assert stale.status_code == 409
        assert stale.json()["current"]["version"] == 2
        confirmed = await client.post(
            url + "/confirm",
            json={},
            headers={"Idempotency-Key": "confirm", "If-Match": '"2"'},
        )
        assert confirmed.json()["state"] == "confirmed"
        fork = await client.post(
            url + "/fork", json={"version": 3}, headers={"Idempotency-Key": "fork"}
        )
        assert fork.json()["state"] == "draft"
        assert (await client.get(url + "/revisions")).json()["items"]
        assert (await client.get(url + "/revisions/1")).json()["content"][
            "title"
        ] == "Private"

    run(setup, scenario)


def test_private_queries_reject_unknown_fields_and_missing_idempotency(setup):
    async def scenario(client, snapshot):
        assert (
            await client.get("/api/owner/v1/notes?owner_id=forged")
        ).status_code == 400
        assert (
            await client.post(
                "/api/owner/v1/notes", json={**payload(snapshot), "owner_id": "forged"}
            )
        ).status_code == 422
        assert (await client.get("/api/owner/v1/notes?page_size=30")).status_code == 422
        client.headers.pop("Idempotency-Key")
        assert (
            await client.post("/api/owner/v1/notes", json=payload(snapshot))
        ).status_code == 400

    run(setup, scenario)


def test_watch_http_and_template(setup):
    async def scenario(client, snapshot):
        response = await client.post(
            "/api/owner/v1/watchlist",
            json={"snapshot_id": str(snapshot.id), "security_id": "A"},
        )
        assert response.status_code == 200
        item = response.json()
        removed = await client.patch(
            f"/api/owner/v1/watchlist/{item['id']}",
            json={"active": False},
            headers={"Idempotency-Key": "remove", "If-Match": '"1"'},
        )
        assert removed.json()["active"] is False
        assert (await client.get("/api/owner/v1/watchlist")).json()["items"]
        template = await client.post(
            "/api/owner/v1/review-templates",
            json={"snapshot_id": str(snapshot.id)},
            headers={"Idempotency-Key": "template"},
        )
        assert template.json()["kind"] == "daily"

    run(setup, scenario)


def test_production_entry_mounts_private_routes(setup):
    async def scenario(client, snapshot):
        response = await client.get("/api/owner/v1/notes")
        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "page": 1,
            "page_size": 50,
            "has_more": False,
        }

    run(setup, scenario)


@pytest.mark.parametrize("page_size", [20, 50, 100])
def test_explicit_page_sizes_on_all_lists(setup, page_size):
    # Explicit installation isolates pagination from the production assembly bug.
    install_review_routes(setup[0])

    async def scenario(client, snapshot):
        created = await client.post("/api/owner/v1/notes", json=payload(snapshot))
        note_id = created.json()["id"]
        for path in (
            "/notes",
            "/watchlist",
            f"/notes/{note_id}/revisions",
            f"/notes/{note_id}/verifications",
        ):
            response = await client.get(
                "/api/owner/v1" + path, params={"page": 1, "page_size": page_size}
            )
            assert response.status_code == 200, response.text
            assert response.json()["page_size"] == page_size
            assert response.json()["page"] == 1

    run(setup, scenario)


def test_missing_review_repository_is_explicit_503(setup):
    from fastapi import Request

    from dragon_radar.owner.security import BoundaryError
    from dragon_radar.review.routes import _repository

    app = setup[0]
    install_review_routes(app)
    app.state.review_repository = None
    # Do not let OwnerGate's generic exception fallback mask a dependency bug.
    with pytest.raises(BoundaryError) as failure:
        _repository(Request({"type": "http", "app": app}))
    assert failure.value.status_code == 503
    assert failure.value.code == "storage_unavailable"

    async def scenario(client, snapshot):
        response = await client.get("/api/owner/v1/notes")
        assert response.status_code == 503
        assert response.json() == {
            "code": "storage_unavailable",
            "message": "Storage unavailable",
        }

    run(setup, scenario)


def test_private_not_found_does_not_override_market_error(setup):
    from uuid import uuid4

    install_review_routes(setup[0])

    async def scenario(client, snapshot):
        missing = uuid4()
        response = await client.get(f"/api/v1/snapshots/{missing}")
        assert response.status_code == 404
        assert response.json() == {
            "code": "snapshot_not_found",
            "message": "Snapshot not found",
        }
        private = await client.get(f"/api/owner/v1/notes/{missing}")
        assert private.status_code == 404
        assert private.json() == {"code": "not_found", "message": "Not found"}

    run(setup, scenario)


def test_production_route_table_remains_flat_and_explicit(monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = create_owner_app()
    paths = [route.path for route in app.routes]
    for path in (
        "/api/owner/v1/notes",
        "/api/owner/v1/watchlist",
        "/api/owner/v1/review-templates",
        "/api/owner/v1/notes/{note_id}/verifications",
        "/api/v1/snapshots",
    ):
        assert path in paths
