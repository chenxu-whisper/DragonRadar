"""Distinct public sharing application with an explicit published-read allowlist."""

import asyncio
import ipaddress
import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request

from dragon_radar.http.access import PublicPublicationRepository
from dragon_radar.http.contracts import empty_envelope, snapshot_summary
from dragon_radar.http.errors import (
    StorageUnavailable,
    error_response,
    register_error_handlers,
)
from dragon_radar.http.routes.analysis import router as analysis_router
from dragon_radar.http.routes.market import router as market_router
from dragon_radar.http.routes.news import router as news_router
from dragon_radar.http.routes.regulation import router as regulation_router
from dragon_radar.http.routes.roles import router as roles_router
from dragon_radar.http.routes.stock import router as stock_router
from dragon_radar.http.selection import query_date, query_integer, query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import NotFound, Repository

PUBLIC_READ_PATHS = frozenset(
    {
        "/api/snapshots/list",
        "/api/snapshots/{snapshot_id}",
        "/api/market/overview",
        "/api/market/sector-flow",
        "/api/market/sentiment",
        "/api/market/heatmap",
        "/api/search",
        "/api/limit-up/ladder",
        "/api/limit-up/continuity",
        "/api/role/candidates",
        "/api/role/evidence",
        "/api/rank/range",
        "/api/compare/curves",
        "/api/stock/{security_id}",
        "/api/stock/{security_id}/kline",
        "/api/news",
        "/api/news/{article_id}",
        "/api/regulation/status",
        "/api/regulation/events",
    }
)


class DirectPeerConcurrencyLimit:
    """Bound every HTTP path by a verified real-client address."""

    def __init__(
        self,
        app,
        maximum: int = 20,
        trusted_proxy_cidr: str | None = None,
    ):
        if type(maximum) is not int or maximum < 1:
            raise ValueError("maximum must be a positive integer")
        self.app = app
        self.maximum = maximum
        self.trusted_proxy = (
            ipaddress.ip_network(trusted_proxy_cidr, strict=False)
            if trusted_proxy_cidr
            else None
        )
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def active_peers(self) -> int:
        return len(self._counts)

    async def _acquire(self, peer: str) -> bool:
        async with self._lock:
            current = self._counts.get(peer, 0)
            if current >= self.maximum:
                return False
            self._counts[peer] = current + 1
            return True

    async def _release(self, peer: str) -> None:
        async with self._lock:
            remaining = self._counts[peer] - 1
            if remaining:
                self._counts[peer] = remaining
            else:
                del self._counts[peer]

    def _client_key(self, scope) -> str:
        client = scope.get("client")
        direct = client[0] if client and client[0] else "<unknown>"
        try:
            direct_ip = ipaddress.ip_address(direct)
        except ValueError:
            return direct
        if self.trusted_proxy is None or direct_ip not in self.trusted_proxy:
            return str(direct_ip)
        values = [
            value
            for name, value in scope.get("headers", ())
            if name.lower() == b"x-dragon-client-ip"
        ]
        if len(values) != 1:
            return str(direct_ip)
        try:
            forwarded = values[0].decode("ascii").strip()
            return str(ipaddress.ip_address(forwarded))
        except (UnicodeDecodeError, ValueError):
            return str(direct_ip)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        peer = self._client_key(scope)
        if not await self._acquire(peer):
            body = b'{"code":"RATE_LIMITED","message":"Too many requests"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"cache-control", b"no-store"),
                        (b"retry-after", b"1"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        disconnected = asyncio.Event()

        async def pump_receive():
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected.set()
                await queue.put(message)
                if message["type"] == "http.disconnect":
                    return

        application = asyncio.create_task(self.app(scope, queue.get, send))
        pump = asyncio.create_task(pump_receive())
        disconnect = asyncio.create_task(disconnected.wait())
        try:
            done, _pending = await asyncio.wait(
                {application, disconnect},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect in done and not application.done():
                application.cancel()
                await asyncio.gather(application, return_exceptions=True)
                return
            await application
        finally:
            for task in (application, pump, disconnect):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                application,
                pump,
                disconnect,
                return_exceptions=True,
            )
            await self._release(peer)


class PublicProtocolGate:
    """Reject every write and provide correct HEAD semantics without route drift."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        if method not in {"GET", "HEAD"}:
            response = error_response(403, "FORBIDDEN", "Read-only public API")
            await response(scope, receive, send)
            return
        header_names = {name.lower() for name, _value in scope.get("headers", ())}
        if {b"content-length", b"transfer-encoding"} & header_names:
            response = error_response(
                400,
                "INVALID_REQUEST",
                "GET and HEAD requests must not contain a body",
            )
            await response(scope, receive, send)
            return
        if method != "HEAD":
            await self.app(scope, receive, send)
            return

        head_scope = dict(scope)
        head_scope["method"] = "GET"

        async def head_send(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app(head_scope, receive, head_send)


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def create_public_app(
    database_url: str | None = None,
    *,
    repository=None,
) -> FastAPI:
    """Create only the public sharing API; owner and private services are absent."""
    owned = repository is None
    if repository is not None:
        database = repository.database
    else:
        value = database_url or os.getenv("DRAGON_DATABASE_URL")
        database = Database(value) if value else None
        repository = (
            PublicPublicationRepository(database, Repository(database))
            if database is not None
            else None
        )

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if owned and database is not None:
                database.close()

    app = FastAPI(
        title="Dragon Radar public sharing API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        redirect_slashes=False,
    )
    app.state.repository = repository
    app.state.analysis_repository = None
    register_error_handlers(app)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if database is None or not database.ready():
            raise StorageUnavailable
        return {"status": "ok"}

    @app.get("/api/snapshots/list")
    def list_snapshots(request: Request):
        query = query_values(request, {"date", "limit", "offset"})
        trade_date = query_date(query.get("date"))
        limit = query_integer(query.get("limit", "50"), 1, 100)
        offset = query_integer(query.get("offset", "0"), 0, 10000)
        snapshots = _repository(request).list_snapshots(
            trade_date=trade_date,
            limit=limit,
            offset=offset,
        )
        payload = empty_envelope(trade_date)
        payload["source"] = "snapshot_store"
        payload["quality_flags"] = [] if snapshots else ["no_data"]
        payload["data"] = {
            "snapshots": [snapshot_summary(snapshot) for snapshot in snapshots]
        }
        return DecimalJSONResponse(payload)

    @app.get("/api/snapshots/{snapshot_id}")
    def get_snapshot_summary(snapshot_id: UUID, request: Request):
        snapshot = _repository(request).get_published_snapshot(snapshot_id)
        if snapshot is None:
            raise NotFound("Snapshot not found")
        return DecimalJSONResponse(snapshot_summary(snapshot))

    for router in (
        market_router,
        news_router,
        regulation_router,
        roles_router,
        stock_router,
        analysis_router,
    ):
        for route in router.routes:
            if route.path in PUBLIC_READ_PATHS and route.methods == {"GET"}:
                app.router.routes.append(route)

    mounted = {
        route.path for route in app.router.routes if route.path.startswith("/api/")
    }
    if mounted != PUBLIC_READ_PATHS:
        raise RuntimeError("Public read route allowlist is incomplete")

    app.add_middleware(PublicProtocolGate)
    app.add_middleware(
        DirectPeerConcurrencyLimit,
        maximum=20,
        trusted_proxy_cidr=os.getenv("DRAGON_TRUSTED_PROXY_CIDR"),
    )
    return app
