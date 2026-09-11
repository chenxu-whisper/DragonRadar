"""Owner HTTP integration against disposable PostgreSQL schemas."""

import importlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest
from factories import batch
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from dragon_radar.owner.repository import OwnerRepository

ORIGIN = "https://localhost:8030"
PAIR = "/api/owner/v1/pair"
SESSION = "/api/owner/v1/session"
TOKEN_COOKIE = "__Host-dragon-private-session"
CSRF_COOKIE = "__Host-dragon-private-csrf"
PAIR_HEADERS = {"Origin": ORIGIN, "X-Dragon-Owner-Pair": "1"}
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def owners(database):
    clock = [datetime.now(UTC)]
    repo = OwnerRepository(database, clock=lambda: clock[0])
    repo.initialize()
    return repo, clock


def owner_app(**kwargs):
    assert importlib.util.find_spec("dragon_radar.owner.app") is not None
    module = importlib.import_module("dragon_radar.owner.app")
    return module.create_owner_app(**kwargs)


@asynccontextmanager
async def client_for(app, base_url=ORIGIN):
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client,
    ):
        yield client


async def pair(client, owners):
    code = owners.issue_pairing(ORIGIN)
    response = await client.post(PAIR, json={"code": code}, headers=PAIR_HEADERS)
    assert response.status_code == 200
    return response, code


def assert_private(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert not any(key.startswith("access-control-") for key in response.headers)
    if status >= 400:
        assert set(response.json()) == {"code", "message"}
        assert "secret-marker" not in response.text


async def test_independent_health_and_disabled_docs(monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = owner_app()
    async with client_for(app) as client:
        assert_private(await client.get("/health/live"), 200)
        assert_private(await client.get("/health/ready"), 503)
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert_private(await client.get(path), 404)
        assert_private(await client.get("/health/live?secret-marker=1"), 422)


@pytest.mark.parametrize(
    "path", ["/health/live", "/health/ready", SESSION, "/api/v1/snapshots", PAIR]
)
@pytest.mark.parametrize(
    "host",
    ["evil.test", "localhost", "localhost:8031", "LOCALHOST:8030", "localhost:08030"],
)
async def test_exact_host_before_routing(path, host):
    async with client_for(owner_app()) as client:
        assert_private(
            await client.get(
                path,
                headers={
                    "Host": host,
                    "Forwarded": "host=localhost:8030;proto=https",
                    "X-Forwarded-Host": "localhost:8030",
                },
            ),
            403,
        )


@pytest.mark.parametrize("path", [SESSION, "/api/v1/snapshots", "/api/v1/unknown"])
async def test_all_api_paths_require_real_session(owners, path):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        response = await client.get(
            path,
            headers={
                "Forwarded": "for=127.0.0.1;proto=https;host=localhost:8030",
                "X-Owner-Id": str(repo.initialize()),
            },
        )
        assert_private(response, 401)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": ORIGIN},
        {"Origin": "https://evil.test", "X-Dragon-Owner-Pair": "1"},
        {"Origin": ORIGIN + "/", "X-Dragon-Owner-Pair": "1"},
        {"Origin": "null", "X-Dragon-Owner-Pair": "1"},
        {"Origin": ORIGIN, "X-Dragon-Owner-Pair": "0"},
        {**PAIR_HEADERS, "Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_pair_requires_exact_origin_and_intent(owners, headers):
    repo, _ = owners
    code = repo.issue_pairing(ORIGIN)
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(
            await client.post(PAIR, json={"code": code}, headers=headers), 403
        )
    assert repo.exchange_pairing(code, ORIGIN).owner_id == repo.initialize()


@pytest.mark.parametrize(
    "name,value",
    [
        ("Host", "localhost:8030"),
        ("Origin", ORIGIN),
        ("X-Dragon-Owner-Pair", "1"),
        ("X-CSRF-Token", "secret-marker"),
        ("Idempotency-Key", "secret-marker"),
        ("If-Match", "1"),
        ("Sec-Fetch-Site", "same-origin"),
    ],
)
async def test_duplicate_security_headers_rejected(owners, name, value):
    repo, _ = owners
    headers = [
        (key, val) for key, val in PAIR_HEADERS.items() if key.lower() != name.lower()
    ]
    headers.extend([(name, value), (name.lower(), value)])
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(
            await client.post(PAIR, json={"code": "unused"}, headers=headers), 403
        )


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"null",
        b"[]",
        b'{"code":1}',
        b'{"code":null}',
        b'{"code":"secret-marker","extra":1}',
        b'{"code":"secret-marker","code":"other"}',
        b'{"code":NaN}',
        b"\xff",
    ],
)
async def test_pair_rejects_bad_json_without_echo(owners, body):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(
            await client.post(
                PAIR,
                content=body,
                headers={**PAIR_HEADERS, "Content-Type": "application/json"},
            ),
            422,
        )


@pytest.mark.parametrize("content_type", ["text/plain", "application/jsonp", ""])
async def test_write_requires_json(owners, content_type):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(
            await client.post(
                PAIR,
                content=b'{"code":"unused"}',
                headers={**PAIR_HEADERS, "Content-Type": content_type},
            ),
            422,
        )


@pytest.mark.parametrize("chunked", [False, True])
async def test_body_limit_before_json_even_without_content_length(owners, chunked):
    repo, _ = owners
    body = b'{"code":"' + b"x" * 131072 + b'"}'

    async def chunks():
        for i in range(0, len(body), 8192):
            yield body[i : i + 8192]

    async with client_for(owner_app(owner_repository=repo)) as client:
        response = await client.post(
            PAIR,
            content=chunks() if chunked else body,
            headers={**PAIR_HEADERS, "Content-Type": "application/json"},
        )
        assert_private(response, 413)


async def test_pair_session_cookies_public_json_and_replay(owners):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        response, code = await pair(client, repo)
        assert_private(response, 200)
        data = response.json()
        assert set(data) == {"owner_id", "session_id", "expires_at"}
        assert data["owner_id"] == str(repo.initialize())
        token = client.cookies[TOKEN_COOKIE]
        csrf = client.cookies[CSRF_COOKIE]
        for secret in (token, csrf, code):
            assert secret not in response.text
        assert len(response.headers.get_list("set-cookie")) == 2
        cookies = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookies.load(header)
        for name in (TOKEN_COOKIE, CSRF_COOKIE):
            cookie = cookies[name]
            assert cookie["secure"]
            assert cookie["samesite"].lower() == "strict"
            assert cookie["path"] == "/"
            assert cookie["domain"] == ""
            assert cookie["max-age"] == "28800"
        assert cookies[TOKEN_COOKIE]["httponly"]
        assert not cookies[CSRF_COOKIE]["httponly"]
        assert (await client.get(SESSION)).json() == data
        replay = await client.post(PAIR, json={"code": code}, headers=PAIR_HEADERS)
        assert_private(replay, 401)
        assert replay.json() == {
            "code": "invalid_pairing",
            "message": "Invalid pairing",
        }


async def test_proxy_backend_http_scheme_does_not_prevent_pairing(owners):
    repo, _ = owners
    async with client_for(
        owner_app(owner_repository=repo), base_url="http://localhost:8030"
    ) as client:
        response, _ = await pair(client, repo)
        assert_private(response, 200)
        cookie = "; ".join(
            header.split(";", 1)[0]
            for header in response.headers.get_list("set-cookie")
        )
        assert_private(
            await client.get(
                SESSION, headers={"Cookie": cookie, "X-Forwarded-Proto": "http"}
            ),
            200,
        )


@pytest.mark.parametrize("mode", ["expired", "revoked", "wrong_origin"])
async def test_session_expiry_revocation_and_origin_binding(owners, mode):
    repo, clock = owners
    issued_origin = "https://localhost:8031" if mode == "wrong_origin" else ORIGIN
    issued = repo.exchange_pairing(repo.issue_pairing(issued_origin), issued_origin)
    if mode == "expired":
        clock[0] = issued.expires_at
    elif mode == "revoked":
        repo.revoke_all()
    async with client_for(owner_app(owner_repository=repo)) as client:
        for path in (SESSION, "/api/v1/snapshots"):
            assert_private(
                await client.get(
                    path, headers={"Cookie": f"{TOKEN_COOKIE}={issued.token}"}
                ),
                401,
            )


@pytest.mark.parametrize("split_headers", [False, True])
@pytest.mark.parametrize("name", [TOKEN_COOKIE, CSRF_COOKIE])
async def test_duplicate_auth_cookies_rejected(owners, split_headers, name):
    repo, _ = owners
    issued = repo.exchange_pairing(repo.issue_pairing(ORIGIN), ORIGIN)
    value = issued.token if name == TOKEN_COOKIE else issued.csrf
    first = f"{TOKEN_COOKIE}={issued.token}; {CSRF_COOKIE}={issued.csrf}"
    second = f"{name}={value}"
    headers = (
        [("Cookie", first), ("Cookie", second)]
        if split_headers
        else [("Cookie", first + "; " + second)]
    )
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(await client.get(SESSION, headers=headers), 403)


@pytest.mark.parametrize(
    "mode", ["missing", "mismatch", "unbound", "wrong_origin", "not_empty"]
)
async def test_logout_requires_bound_csrf_and_empty_json(owners, mode):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        await pair(client, repo)
        token = client.cookies[TOKEN_COOKIE]
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}
        if mode == "missing":
            headers.pop("X-CSRF-Token")
        elif mode == "mismatch":
            headers["X-CSRF-Token"] = "other"
        elif mode == "unbound":
            other = repo.exchange_pairing(repo.issue_pairing(ORIGIN), ORIGIN)
            headers.update(
                {
                    "Cookie": f"{TOKEN_COOKIE}={token}; {CSRF_COOKIE}={other.csrf}",
                    "X-CSRF-Token": other.csrf,
                }
            )
        elif mode == "wrong_origin":
            headers["Origin"] = "https://evil.test"
        response = await client.request(
            "DELETE",
            SESSION,
            json={"extra": 1} if mode == "not_empty" else {},
            headers=headers,
        )
        assert_private(response, 422 if mode == "not_empty" else 403)
        assert repo.authenticate(token).owner_id == repo.initialize()


async def test_logout_revokes_session_and_clears_both_cookies(owners):
    from dragon_radar.owner.errors import SessionUnauthorized

    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        await pair(client, repo)
        token = client.cookies[TOKEN_COOKIE]
        response = await client.request(
            "DELETE",
            SESSION,
            json={},
            headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]},
        )
        assert_private(response, 200)
        assert response.json() == {"status": "logged_out"}
        assert len(response.headers.get_list("set-cookie")) == 2
        assert all("Max-Age=0" in h for h in response.headers.get_list("set-cookie"))
        with pytest.raises(SessionUnauthorized):
            repo.authenticate(token)
        assert_private(await client.get(SESSION), 401)


async def test_owner_and_market_query_allowlists_and_source_error(repository, owners):
    repo, _ = owners
    snapshot = repository.publish(repository.import_batch(batch(), key="owner-http").id)
    async with client_for(
        owner_app(repository=repository, owner_repository=repo)
    ) as client:
        await pair(client, repo)
        assert_private(await client.get("/health/ready"), 200)
        response = await client.get(f"/api/v1/snapshots/{snapshot.id}")
        assert_private(response, 200)
        assert response.json()["snapshot_id"] == str(snapshot.id)
        for path in (
            SESSION + "?owner_id=secret-marker",
            "/api/v1/snapshots?limit=1&limit=2",
            "/api/v1/snapshots?secret-marker=1",
        ):
            assert_private(await client.get(path), 422)
        assert_private(
            await client.post(
                PAIR + "?code=secret-marker", json={"code": "x"}, headers=PAIR_HEADERS
            ),
            422,
        )
        repository.revoke_source("synthetic")
        denied = await client.get(f"/api/v1/snapshots/{snapshot.id}")
        assert_private(denied, 403)
        assert denied.json()["message"] == "Source access denied"


async def test_fixed_storage_errors_and_pair_rate_limit(owners, monkeypatch):
    repo, _ = owners
    async with client_for(owner_app(owner_repository=repo)) as client:
        for _ in range(5):
            response = await client.post(
                PAIR, json={"code": "secret-marker"}, headers=PAIR_HEADERS
            )
            assert_private(response, 401)
        limited = await client.post(
            PAIR, json={"code": "secret-marker"}, headers=PAIR_HEADERS
        )
        assert_private(limited, 429)
        assert limited.json()["code"] == "pairing_rate_limited"

        def fail(*args):
            raise OperationalError("secret-marker SQL", {}, Exception("secret-marker"))

        monkeypatch.setattr(repo, "exchange_pairing", fail)
        response = await client.post(
            PAIR, json={"code": "secret-marker"}, headers=PAIR_HEADERS
        )
        assert_private(response, 503)
        assert response.json() == {
            "code": "storage_unavailable",
            "message": "Storage unavailable",
        }


async def test_readonly_app_has_no_owner_routes_or_cookies(repository):
    from dragon_radar.api import create_app

    async with client_for(create_app(repository=repository)) as client:
        for method, path in (("POST", PAIR), ("GET", SESSION), ("DELETE", SESSION)):
            response = await client.request(method, path)
            assert response.status_code == 404
            assert "set-cookie" not in response.headers
        assert (await client.get("/api/v1/snapshots")).status_code == 200


@pytest.mark.parametrize(
    "origin", ["http://localhost:8030", "https://localhost:0", ORIGIN + "/"]
)
async def test_invalid_app_origin_rejected(origin):
    with pytest.raises(ValueError, match="Invalid owner origin"):
        owner_app(origin=origin)


async def test_pairing_expiry(owners):
    repo, clock = owners
    code = repo.issue_pairing(ORIGIN)
    clock[0] += timedelta(minutes=5)
    async with client_for(owner_app(owner_repository=repo)) as client:
        response = await client.post(PAIR, json={"code": code}, headers=PAIR_HEADERS)
        assert_private(response, 401)


async def test_no_config_still_registers_all_market_routes(monkeypatch):
    from dragon_radar.api import create_app

    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = owner_app()
    expected = {
        route.path for route in create_app().routes if route.path.startswith("/api/v1/")
    }
    assert expected <= {route.path for route in app.routes}


async def test_context_reauthorizes_inside_future_business_transaction(owners):
    from dragon_radar.owner.security import OwnerWrite, json_object

    repo, _ = owners
    app = owner_app(owner_repository=repo)
    contexts = []

    @app.post("/api/owner/v1/context-probe")
    def probe(request: Request, context: OwnerWrite):
        json_object(request, set())
        assert context.token not in repr(context)
        assert context.csrf not in repr(context)
        with repo.database.engine.begin() as connection:
            identity = context.authorize(connection, repo)
        contexts.append(context)
        return {"owner_id": str(identity.owner_id)}

    async with client_for(app) as client:
        assert_private(
            await client.post(
                "/api/owner/v1/context-probe", json={}, headers={"Origin": ORIGIN}
            ),
            401,
        )
        await pair(client, repo)
        response = await client.post(
            "/api/owner/v1/context-probe",
            json={},
            headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]},
        )
        assert_private(response, 200)
        repo.revoke_all()
        from dragon_radar.owner.errors import SessionUnauthorized

        with repo.database.engine.begin() as connection:
            with pytest.raises(SessionUnauthorized):
                contexts[0].authorize(connection, repo)
        assert_private(
            await client.post(
                "/api/owner/v1/context-probe",
                json={},
                headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]},
            ),
            401,
        )


async def test_unknown_errors_sanitized_and_no_store(owners):
    repo, _ = owners
    app = owner_app(owner_repository=repo)

    @app.get("/api/owner/v1/failure-probe")
    def fail():
        raise RuntimeError("secret-marker")

    async with client_for(app) as client:
        await pair(client, repo)
        assert_private(await client.get("/api/owner/v1/failure-probe"), 503)


async def test_app_lifecycle_does_not_close_injected_repository(owners, monkeypatch):
    repo, _ = owners
    calls = []
    monkeypatch.setattr(repo.database, "close", lambda: calls.append("closed"))
    async with client_for(owner_app(owner_repository=repo)) as client:
        assert_private(await client.get("/health/ready"), 200)
    assert calls == []


async def test_app_lifecycle_closes_owned_database(database_url, monkeypatch):
    app = owner_app(database_url=database_url)
    calls = []
    database = app.state.owner_repository.database
    original = database.close

    def close():
        calls.append("closed")
        original()

    monkeypatch.setattr(database, "close", close)
    async with client_for(app) as client:
        assert_private(await client.get("/health/live"), 200)
        assert_private(await client.get("/health/ready"), 503)
    assert calls == ["closed"]


@pytest.mark.parametrize("length", [131071, 131072, 131073])
async def test_body_limit_exact_boundary(owners, length):
    repo, _ = owners
    code = repo.issue_pairing(ORIGIN)
    body = ('{"code":"' + code + '"}').encode()
    body += b" " * (length - len(body))
    async with client_for(owner_app(owner_repository=repo)) as client:
        response = await client.post(
            PAIR,
            content=body,
            headers={**PAIR_HEADERS, "Content-Type": "application/json"},
        )
        assert_private(response, 413 if length > 131072 else 200)


async def test_raw_asgi_missing_or_duplicate_content_headers(owners):
    repo, _ = owners
    app = owner_app(owner_repository=repo)
    for extras, status in (
        ([], 403),
        ([(b"host", b"localhost:8030"), (b"host", b"localhost:8030")], 403),
        ([(b"host", b"localhost:8030"), (b"content-length", b"invalid")], 422),
        (
            [
                (b"host", b"localhost:8030"),
                (b"content-length", b"1"),
                (b"content-length", b"1"),
            ],
            403,
        ),
    ):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message, output=messages):
            output.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health/live",
                "raw_path": b"/health/live",
                "query_string": b"",
                "root_path": "",
                "server": ("localhost", 8030),
                "client": ("127.0.0.1", 10000),
                "headers": extras,
            },
            receive,
            send,
        )
        assert messages[0]["status"] == status
        assert (b"cache-control", b"no-store") in messages[0]["headers"]
