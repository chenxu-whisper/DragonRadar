"""Independent local read-only workbench API, not a public sharing gateway."""

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request

from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.analytics.metrics import market_breadth  # noqa: F401
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
from dragon_radar.http.selection import (
    query_date,
    query_integer,
    query_values,
)
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import NotFound, Repository


def get_repository(request: Request) -> Repository:
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def create_workbench_app(
    database_url: str | None = None,
    *,
    repository: Repository | None = None,
    analysis_repository: AnalysisJobRepository | None = None,
) -> FastAPI:
    """No implicit migrations, ingestion, environment files, or private routers."""
    owned = repository is None
    if repository is not None:
        database = repository.database
    else:
        value = (
            database_url
            if database_url is not None
            else os.getenv("DRAGON_DATABASE_URL")
        )
        database = Database(value) if value else None
        repository = (
            PublicationRepository(database, Repository(database))
            if database is not None
            else None
        )
    if (
        analysis_repository is None
        and database is not None
        and isinstance(repository, PublicationRepository)
    ):
        analysis_repository = AnalysisJobRepository(database, None, repository)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owned and database is not None:
                database.close()

    app = FastAPI(
        title="Dragon Radar local workbench read API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.analysis_repository = analysis_repository
    register_error_handlers(app)

    @app.middleware("http")
    async def readonly(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return error_response(403, "FORBIDDEN", "Read-only API")
        return await call_next(request)

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
        snapshots = get_repository(request).list_snapshots(
            trade_date=trade_date, limit=limit, offset=offset
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
        snapshot = get_repository(request).get_snapshot(snapshot_id)
        if snapshot is None:
            raise NotFound("Snapshot not found")
        return DecimalJSONResponse(snapshot_summary(snapshot))

    app.include_router(market_router)
    app.include_router(news_router)
    app.include_router(regulation_router)
    app.include_router(roles_router)
    app.include_router(stock_router)
    app.include_router(analysis_router)

    return app
