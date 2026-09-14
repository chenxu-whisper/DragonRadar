"""Private HTTP validation and authorization, using the production factory."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from test_review_api import ORIGIN, payload, run
from test_review_api import setup as setup  # noqa: F401

from dragon_radar.api import create_app
from dragon_radar.review.tables import owner_commands, private_audit_events

PREFIX = "/api/owner/v1"
PRIVATE_REQUESTS = [
    ("GET", "/notes"),
    ("POST", "/notes"),
    ("GET", "/notes/{id}"),
    ("PATCH", "/notes/{id}"),
    ("POST", "/notes/{id}/confirm"),
    ("POST", "/notes/{id}/fork"),
    ("GET", "/notes/{id}/revisions"),
    ("GET", "/notes/{id}/revisions/1"),
    ("GET", "/watchlist"),
    ("POST", "/watchlist"),
    ("PATCH", "/watchlist/{id}"),
    ("POST", "/review-templates"),
    ("GET", "/notes/{id}/verifications"),
    ("POST", "/notes/{id}/verifications"),
]


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert response.headers["cache-control"] == "no-store"
    if status != 409:
        assert set(response.json()) == {"code", "message"}
    for marker in ("secret-marker", "SELECT ", "postgresql", "__Host-"):
        assert marker not in response.text


def counts(database):
    with database.engine.connect() as connection:
        return tuple(
            connection.scalar(select(func.count()).select_from(table))
            for table in (owner_commands, private_audit_events)
        )


@pytest.mark.parametrize("key", [None, "", "has space", "secret-marker/", "x" * 201])
def test_idempotency_key_missing_or_invalid(setup, database, key):
    async def scenario(client, snapshot):
        client.headers.pop("Idempotency-Key")
        headers = {} if key is None else {"Idempotency-Key": key}
        error(
            await client.post(
                PREFIX + "/notes", json=payload(snapshot), headers=headers
            ),
            400,
            "idempotency_required",
        )
        assert counts(database) == (0, 0)

    run(setup, scenario)


@pytest.mark.parametrize("target", ["note", "confirm", "watch"])
@pytest.mark.parametrize(
    "version,status",
    [
        (None, 428),
        ("1", 400),
        ('W/"1"', 400),
        ('"0"', 400),
        ('"-1"', 400),
        ('"01"', 400),
        ('"1","2"', 400),
        ('"secret-marker"', 400),
    ],
)
def test_if_match_contract(setup, database, target, version, status):
    async def scenario(client, snapshot):
        path = f"{PREFIX}/notes/{uuid4()}"
        body = {"content": payload(snapshot)["content"]}
        method = "PATCH"
        if target == "confirm":
            method, path, body = "POST", path + "/confirm", {}
        elif target == "watch":
            path, body = f"{PREFIX}/watchlist/{uuid4()}", {"active": False}
        headers = {} if version is None else {"If-Match": version}
        error(
            await client.request(method, path, json=body, headers=headers),
            status,
            "version_required" if version is None else "invalid_version",
        )
        assert counts(database) == (0, 0)

    run(setup, scenario)


@pytest.mark.parametrize(
    "header,value",
    [
        ("Idempotency-Key", "create"),
        ("If-Match", '"1"'),
        ("X-CSRF-Token", "secret-marker"),
        ("Origin", ORIGIN),
    ],
)
def test_duplicate_headers_rejected_by_gate(setup, database, header, value):
    async def scenario(client, snapshot):
        response = await client.post(
            PREFIX + "/notes",
            json=payload(snapshot),
            headers=[(header, value), (header.lower(), value)],
        )
        error(response, 403, "origin_forbidden")
        assert counts(database) == (0, 0)

    run(setup, scenario)


@pytest.mark.parametrize(
    "query,status",
    [
        ("owner_id=secret-marker", 400),
        ("page=1&page=2", 400),
        ("page_size=20&page_size=50", 400),
        ("state=draft&state=confirmed", 400),
        ("page=0", 422),
        ("page=100001", 422),
        ("page=abc", 422),
        ("page_size=30", 422),
        ("page_size=abc", 422),
        ("state=other", 422),
    ],
)
def test_list_query_contract(setup, query, status):
    async def scenario(client, snapshot):
        error(
            await client.get(f"{PREFIX}/notes?{query}"),
            status,
            "invalid_query" if status == 400 else "invalid_request",
        )

    run(setup, scenario)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "content_extra",
        "numeric_title",
        "null_judgment",
        "boolean_security",
        "empty_security",
        "invalid_snapshot",
        "timestamp_date",
        "empty_title",
        "long_title",
        "long_body",
        "nul",
    ],
)
def test_strict_note_fields_no_input_echo(setup, database, mutation):
    async def scenario(client, snapshot):
        body = payload(snapshot)
        if mutation == "extra":
            body["owner_id"] = "secret-marker"
        elif mutation == "content_extra":
            body["content"]["html"] = "secret-marker"
        elif mutation == "numeric_title":
            body["content"]["title"] = 1
        elif mutation == "null_judgment":
            body["content"]["judgment"] = None
        elif mutation == "boolean_security":
            body["security_id"] = False
        elif mutation == "empty_security":
            body["security_id"] = ""
        elif mutation == "invalid_snapshot":
            body["snapshot_id"] = "secret-marker"
        elif mutation == "timestamp_date":
            body["content"]["verification_date"] = "2026-09-11T00:00:00Z"
        elif mutation == "empty_title":
            body["content"]["title"] = " "
        elif mutation == "long_title":
            body["content"]["title"] = "x" * 101
        elif mutation == "long_body":
            body["content"]["evidence"] = "x" * 20001
        else:
            body["content"]["evidence"] = "secret-marker\x00"
        error(await client.post(PREFIX + "/notes", json=body), 422, "invalid_request")
        assert counts(database) == (0, 0)

    run(setup, scenario)


@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/watchlist", "POST", {"snapshot_id": None, "security_id": 1}),
        ("/watchlist/{id}", "PATCH", {"active": "false"}),
        ("/watchlist/{id}", "PATCH", {"active": False, "extra": "secret-marker"}),
        ("/notes/{id}", "PATCH", {"content": {"title": "X"}, "snapshot_id": None}),
        ("/notes/{id}/confirm", "POST", {"extra": "secret-marker"}),
        ("/notes/{id}/fork", "POST", {"version": "1"}),
        ("/notes/{id}/fork", "POST", {"version": True}),
        ("/notes/{id}/fork", "POST", {"version": 1, "snapshot_id": False}),
        ("/notes/{id}/verifications", "POST", {"version": True, "outcome": "not_due"}),
        ("/notes/{id}/verifications", "POST", {"version": 1, "outcome": "automatic"}),
        (
            "/notes/{id}/verifications",
            "POST",
            {"version": 1, "outcome": "not_due", "evidence": 1},
        ),
        ("/review-templates", "POST", {"snapshot_id": None}),
    ],
)
def test_other_write_bodies_are_strict(setup, path, method, body):
    async def scenario(client, snapshot):
        error(
            await client.request(
                method,
                PREFIX + path.format(id=uuid4()),
                json=body,
                headers={"If-Match": '"1"'},
            ),
            422,
            "invalid_request",
        )

    run(setup, scenario)


def test_query_allowlists_on_detail_writes_and_other_lists(setup):
    async def scenario(client, snapshot):
        note = (await client.post(PREFIX + "/notes", json=payload(snapshot))).json()
        url = f"{PREFIX}/notes/{note['id']}"
        for path in (
            url,
            url + "/revisions/1",
            url + "/revisions",
            url + "/verifications",
            PREFIX + "/watchlist",
        ):
            error(
                await client.get(path + "?owner_id=secret-marker"), 400, "invalid_query"
            )
        error(
            await client.post(
                PREFIX + "/notes?key=secret-marker", json=payload(snapshot)
            ),
            400,
            "invalid_query",
        )

    run(setup, scenario)


def test_all_private_routes_stay_out_of_read_app(setup):
    app = create_app(repository=setup[0].state.repository)
    private = PRIVATE_REQUESTS + [
        ("POST", "/pair"),
        ("GET", "/session"),
        ("DELETE", "/session"),
    ]

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN
        ) as client:
            for method, path in private:
                response = await client.request(
                    method, PREFIX + path.format(id=uuid4()), json={}
                )
                assert response.status_code == 404
                assert "set-cookie" not in response.headers
            assert (await client.get("/api/v1/snapshots")).status_code == 200

    asyncio.run(scenario())


def test_missing_repository_on_all_private_routes(setup):
    app = setup[0]
    app.state.review_repository = None

    async def scenario(client, snapshot):
        for method, path in PRIVATE_REQUESTS:
            error(
                await client.request(method, PREFIX + path.format(id=uuid4()), json={}),
                503,
                "storage_unavailable",
            )

    run(setup, scenario)


def test_storage_failure_does_not_echo_sql_or_body(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationalError(
            "SELECT secret-marker", {}, Exception("postgresql secret-marker")
        )

    monkeypatch.setattr(setup[0].state.review_repository, "create_note", fail)

    async def scenario(client, snapshot):
        body = payload(snapshot)
        body["content"]["title"] = "secret-marker"
        error(
            await client.post(PREFIX + "/notes", json=body), 503, "storage_unavailable"
        )

    run(setup, scenario)


def test_gate_success_does_not_replace_transaction_authorization(
    setup, database, monkeypatch
):
    service = setup[0].state.review_repository
    original = service.create_note

    def revoke_before_write(token, csrf, **kwargs):
        service.owners.revoke_all()
        return original(token, csrf, **kwargs)

    monkeypatch.setattr(service, "create_note", revoke_before_write)

    async def scenario(client, snapshot):
        error(
            await client.post(PREFIX + "/notes", json=payload(snapshot)),
            401,
            "session_unauthorized",
        )
        assert counts(database) == (0, 0)

    run(setup, scenario)


def test_each_write_rejects_query_parameters(setup, database):
    async def scenario(client, snapshot):
        bodies = {
            "/notes": payload(snapshot),
            "/notes/{id}": {"content": {"title": "Draft"}},
            "/notes/{id}/confirm": {},
            "/notes/{id}/fork": {"version": 1},
            "/watchlist": {"snapshot_id": str(snapshot.id), "security_id": "A"},
            "/watchlist/{id}": {"active": False},
            "/review-templates": {"snapshot_id": str(snapshot.id)},
            "/notes/{id}/verifications": {"version": 1, "outcome": "not_due"},
        }
        for method, path in PRIVATE_REQUESTS:
            if method == "GET":
                continue
            error(
                await client.request(
                    method,
                    PREFIX + path.format(id=uuid4()) + "?extra=secret-marker",
                    json=bodies[path],
                    headers={"If-Match": '"1"'},
                ),
                400,
                "invalid_query",
            )
        assert counts(database) == (0, 0)

    run(setup, scenario)


def test_installer_is_idempotent_and_missing_state_is_explicit(setup):
    from fastapi import FastAPI, Request

    from dragon_radar.owner.security import BoundaryError
    from dragon_radar.review.routes import _repository, install_review_routes

    app = setup[0]
    repository = app.state.review_repository
    routes = list(app.routes)
    install_review_routes(app)
    assert app.state.review_repository is repository
    assert app.routes == routes
    empty = FastAPI()
    request = Request({"type": "http", "app": empty})
    with pytest.raises(BoundaryError) as failure:
        _repository(request)
    assert failure.value.status_code == 503
    install_review_routes(empty)
    assert empty.state.review_repository is None
    with pytest.raises(BoundaryError) as failure:
        _repository(request)
    assert failure.value.status_code == 503
