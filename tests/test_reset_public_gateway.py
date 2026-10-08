"""Task19 public sharing gateway, isolation, and runtime contracts."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from factories import DAYS, NOW, batch, grant
from httpx import ASGITransport, AsyncClient

from dragon_radar.domain.models import SourceGrant
from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.service import PublicationService

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _shared_grant(**changes) -> SourceGrant:
    return grant(sharing=True, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"sharing": False},
        {"approved": False},
        {"reference": None},
        {"source_id": "other"},
        {"starts_at": datetime(2027, 1, 1, tzinfo=UTC)},
        {"expires_at": NOW},
    ],
)
def test_public_access_is_an_independent_fail_closed_predicate(changes):
    from dragon_radar.http.access import grant_allows_public_sharing

    candidate = _shared_grant().model_copy(update=changes)
    assert not grant_allows_public_sharing(
        candidate,
        source_id="synthetic",
        now=NOW,
    )
    local_only = grant(local_display=True, sharing=False)
    assert not grant_allows_public_sharing(
        local_only,
        source_id=local_only.source_id,
        now=NOW,
    )
    assert grant_allows_public_sharing(
        _shared_grant(),
        source_id="synthetic",
        now=NOW,
    )


def _publish_market_only(database, repository, *, key):
    market = repository.publish(
        repository.import_batch(batch(), key=f"{key}-market").id
    )
    return PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key=key,
    )


def test_public_repository_rechecks_current_sharing_and_never_uses_legacy_fallback(
    database, repository
):
    from dragon_radar.http.access import PublicPublicationRepository

    publication = _publish_market_only(
        database,
        repository,
        key="task19-publication",
    )
    public = PublicPublicationRepository(database, repository, clock=lambda: NOW)
    assert public.list_snapshots() == []
    with pytest.raises(Exception) as denied:
        public.get_snapshot(publication.id)
    assert denied.value.__class__.__name__ == "Forbidden"

    repository.register_source(_shared_grant())
    assert [item.id for item in public.list_snapshots()] == [publication.id]
    assert public.get_snapshot(publication.id).id == publication.id

    legacy = repository.publish(
        repository.import_batch(
            batch(),
            key="task19-legacy-market",
        ).id
    )
    assert public.get_snapshot(legacy.id) is None

    repository.register_source(_shared_grant(local_storage=False, local_display=False))
    assert [item.id for item in public.list_snapshots()] == [publication.id]
    assert public.get_snapshot(publication.id).id == publication.id

    repository.register_source(grant(local_display=True, sharing=False))
    assert public.list_snapshots() == []
    with pytest.raises(Exception) as revoked:
        public.list_results(publication.id)
    assert revoked.value.__class__.__name__ == "Forbidden"


class _EmptyPublicRepository:
    database = None

    def list_snapshots(self, **_kwargs):
        return []

    def get_snapshot(self, _snapshot_id):
        return None


async def test_public_app_is_distinct_read_only_and_spoofing_cannot_reach_owner():
    from dragon_radar.http.public_app import create_public_app

    app = create_public_app(repository=_EmptyPublicRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://public.example",
        headers={
            "Host": "localhost:8030",
            "Forwarded": "for=127.0.0.1;host=localhost:8030;proto=https",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "localhost:8030",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": "127.0.0.1",
            "Cookie": "__Host-dragon-private-session=spoof",
        },
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/api/snapshots/list")).status_code == 200
        assert (await client.head("/api/snapshots/list")).status_code == 200
        assert (await client.post("/api/snapshots/list")).status_code == 403
        assert (await client.post("/api/owner/v1/pair")).status_code == 403
        assert (await client.get("/api/owner/v1/session")).status_code == 404
        assert (await client.get("/api/ai/task/" + "0" * 36)).status_code == 404
        assert (await client.get("/api/analysis/jobs")).status_code == 404
        assert (await client.get("/api/not-published")).status_code == 404

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/api/owner/") for path in paths)
    assert not any(path.startswith("/api/ai/") for path in paths)


def _scope(peer="198.51.100.7"):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/held",
        "raw_path": b"/held",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.9"),
            (b"x-real-ip", b"203.0.113.10"),
            (b"host", b"localhost:8030"),
            (b"cookie", b"owner=spoof"),
        ],
        "client": (peer, 45000),
        "server": ("public", 8000),
    }


async def _invoke(app, scope, *, disconnect=None):
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        if disconnect is not None:
            await disconnect.wait()
            return {"type": "http.disconnect"}
        await asyncio.Future()

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def test_concurrency_limit_uses_direct_peer_holds_twenty_and_releases_normally():
    from dragon_radar.http.public_app import DirectPeerConcurrencyLimit

    entered = 0
    all_entered = asyncio.Event()
    release = asyncio.Event()

    async def held(_scope, _receive, send):
        nonlocal entered
        entered += 1
        if entered == 20:
            all_entered.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    limited = DirectPeerConcurrencyLimit(held, maximum=20)
    tasks = [asyncio.create_task(_invoke(limited, _scope())) for _ in range(20)]
    await asyncio.wait_for(all_entered.wait(), timeout=2)
    rejected = await _invoke(limited, _scope())
    assert rejected[0]["status"] == 429
    assert b"198.51.100.7" not in rejected[-1]["body"]
    assert b"203.0.113" not in rejected[-1]["body"]
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(result[0]["status"] == 200 for result in results)
    assert limited.active_peers == 0
    assert (await _invoke(limited, _scope()))[0]["status"] == 200


@pytest.mark.parametrize("ending", ["exception", "cancel", "disconnect"])
async def test_concurrency_slot_releases_after_abnormal_completion(ending):
    from dragon_radar.http.public_app import DirectPeerConcurrencyLimit

    entered = asyncio.Event()
    release = asyncio.Event()

    async def abnormal(_scope, _receive, _send):
        entered.set()
        if ending == "exception":
            raise RuntimeError("synthetic")
        await release.wait()

    limited = DirectPeerConcurrencyLimit(abnormal, maximum=1)
    disconnect = asyncio.Event() if ending == "disconnect" else None
    task = asyncio.create_task(_invoke(limited, _scope(), disconnect=disconnect))
    await asyncio.wait_for(entered.wait(), timeout=2)
    if ending == "exception":
        with pytest.raises(RuntimeError, match="synthetic"):
            await task
    elif ending == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        disconnect.set()
        await asyncio.wait_for(task, timeout=2)
    assert limited.active_peers == 0


async def test_cancel_holds_slot_until_downstream_cleanup_finishes():
    from dragon_radar.http.public_app import DirectPeerConcurrencyLimit

    entered = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def cleanup(_scope, _receive, _send):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()

    limited = DirectPeerConcurrencyLimit(cleanup, maximum=1)
    task = asyncio.create_task(_invoke(limited, _scope()))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=2)
    assert limited.active_peers == 1
    allow_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limited.active_peers == 0


def test_owner_and_public_cli_defaults_are_explicit_loopback(monkeypatch, database_url):
    from dragon_radar.cli import main

    calls = []
    monkeypatch.setenv("DRAGON_DATABASE_URL", database_url)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **options: calls.append((app, options)),
    )
    assert main(["serve-owner"]) == 0
    assert calls[-1][1] == {
        "host": "127.0.0.1",
        "port": 8001,
        "proxy_headers": False,
        "access_log": False,
    }
    assert main(["serve-public"]) == 0
    assert calls[-1][1] == {
        "host": "127.0.0.1",
        "port": 8000,
        "proxy_headers": False,
        "access_log": False,
    }
    assert calls[-1][0].title == "Dragon Radar public sharing API"


def test_reset_compose_and_caddy_static_isolation_contracts():
    compose = (ROOT / "compose.reset.yaml").read_text(encoding="utf-8")
    public_dockerfile = (ROOT / "deploy/public.Dockerfile").read_text(encoding="utf-8")
    public_caddy = (ROOT / "deploy/public.Caddyfile").read_text(encoding="utf-8")
    owner_caddy = (ROOT / "deploy/owner.Caddyfile").read_text(encoding="utf-8")

    assert "network_mode: service:owner-caddy" in compose
    assert 'serve-owner", "--host", "127.0.0.1", "--port", "8001"' in compose
    assert "127.0.0.1:${DRAGON_OWNER_PORT:-8031}:8443" in compose
    assert "networks: [owner-data, owner-edge]" in compose
    assert "owner-edge:\n" in compose
    assert "public-edge:\n" in compose
    assert "public-edge:\n    internal: true" not in compose
    assert "owner-edge:\n    internal: true" not in compose
    assert "public-api:" in compose and "serve-public" in compose
    assert "dockerfile: deploy/public.Dockerfile" in compose
    assert "VITE_RESET_WORKBENCH=1" in public_dockerfile
    assert "VITE_PRIVATE_REVIEW" not in public_dockerfile
    assert "VITE_PRIVATE_AI" not in public_dockerfile
    assert "owner-api" not in public_caddy
    assert "owner-caddy" not in public_caddy
    assert "public-api:8000" in public_caddy
    assert "127.0.0.1:8001" in owner_caddy
    assert "@write not method GET HEAD" in public_caddy
    assert "respond @write 403" in public_caddy
    assert "respond @api 404" in public_caddy
    assert "/api/owner/" not in public_caddy
    assert "/api/ai/" not in public_caddy


def test_reset_start_script_and_running_documentation_contracts():
    script = (ROOT / "scripts/start-reset.ps1").read_text(encoding="utf-8")
    docs = (ROOT / "docs/reset-running.md").read_text(encoding="utf-8")
    assert "Initialize-PrivateDirectory" in script
    assert "RandomNumberGenerator" in script
    assert "compose.reset.yaml" in script
    assert " migrate" in script
    assert "--wait" in script
    assert "DRAGON_DB_PASSWORD=" in script
    assert "Write-Output $password" not in script
    for phrase in (
        "no automatic public internet exposure",
        "sharing",
        "TLS",
        "shutdown",
        "blocked",
        "model",
    ):
        assert phrase.lower() in docs.lower()
