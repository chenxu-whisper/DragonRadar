"""Explicit private application; the original read application remains unchanged."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from dragon_radar.ai.repository import AITaskRepository
from dragon_radar.ai.service import AIService
from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.analysis_jobs.routes import install_analysis_job_routes
from dragon_radar.api import StorageUnavailable, create_app
from dragon_radar.http.app import create_workbench_app
from dragon_radar.http.routes.ai import install_ai_routes
from dragon_radar.http.routes.news import install_news_review_routes
from dragon_radar.http.routes.query import install_query_routes
from dragon_radar.news.repository import NewsRepository
from dragon_radar.owner.repository import (
    DEFAULT_ORIGIN,
    SESSION_LIFETIME,
    OwnerRepository,
)
from dragon_radar.owner.security import (
    CSRF_COOKIE,
    OWNER_ERRORS,
    PAIR_PATH,
    SESSION_COOKIE,
    SESSION_PATH,
    BoundaryError,
    OwnerGate,
    OwnerRead,
    OwnerWrite,
    invalid_request,
    json_object,
    public_identity,
    storage_unavailable,
    validate_origin,
)
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.query.repository import QueryRepository
from dragon_radar.review.routes import install_review_routes
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
)

_RESET_READ_PATHS = {
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


def create_owner_app(
    database_url: str | None = None,
    *,
    repository: Repository | None = None,
    owner_repository: OwnerRepository | None = None,
    publication_repository: PublicationRepository | None = None,
    analysis_repository: AnalysisJobRepository | None = None,
    ai_service: AIService | None = None,
    news_repository: NewsRepository | None = None,
    query_repository: QueryRepository | None = None,
    origin: str = DEFAULT_ORIGIN,
) -> FastAPI:
    """No implicit migration, initialization, dotenv, proxy trust or public routes."""
    validate_origin(origin)
    owned = all(
        dependency is None
        for dependency in (
            repository,
            owner_repository,
            publication_repository,
            analysis_repository,
            ai_service,
            news_repository,
            query_repository,
        )
    )
    if ai_service is not None:
        publication_repository = publication_repository or ai_service.publications
        owner_repository = owner_repository or ai_service.tasks.owners
    if analysis_repository is not None:
        publication_repository = (
            publication_repository or analysis_repository.publications
        )
        owner_repository = owner_repository or analysis_repository.owners
    if publication_repository is not None:
        repository = repository or publication_repository.market
    value = database_url or os.environ.get("DRAGON_DATABASE_URL")
    database = (
        repository.database
        if repository is not None
        else publication_repository.database
        if publication_repository is not None
        else analysis_repository.database
        if analysis_repository is not None
        else ai_service.tasks.database
        if ai_service is not None
        else news_repository.database
        if news_repository is not None
        else owner_repository.database
        if owner_repository is not None
        else Database(value)
        if value
        else None
    )
    if repository is None and database is not None:
        repository = Repository(database)
    if owner_repository is None and database is not None:
        owner_repository = OwnerRepository(database)
    if publication_repository is None and database is not None:
        publication_repository = PublicationRepository(database, repository)
    if analysis_repository is None and database is not None:
        analysis_repository = AnalysisJobRepository(
            database,
            owner_repository,
            publication_repository,
        )
    if ai_service is None and database is not None:
        ai_service = AIService(
            AITaskRepository(
                database,
                owner_repository,
                publication_repository,
            ),
            publication_repository,
        )
    if news_repository is None and database is not None:
        news_repository = NewsRepository(
            database,
            repository,
            owner_repository=owner_repository,
        )
    if query_repository is None and database is not None:
        query_repository = QueryRepository(
            database,
            owner_repository,
            publication_repository,
        )

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owned and database is not None:
                database.close()

    app = FastAPI(
        title="Dragon Radar private owner API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        redirect_slashes=False,
    )
    app.state.repository = publication_repository
    app.state.market_repository = repository
    app.state.owner_repository = owner_repository
    app.state.publication_repository = publication_repository
    app.state.analysis_repository = analysis_repository
    app.state.ai_service = ai_service
    app.state.news_repository = news_repository
    app.state.query_repository = query_repository
    app.state.owner_origin = origin
    # Copy only explicit Reset read routes. Do not mount a second application whose
    # lifecycle, exception handlers or request.app state could bypass this gate.
    workbench = create_workbench_app(
        repository=publication_repository,
        analysis_repository=analysis_repository,
    )
    read_routes = (
        nested
        for route in workbench.routes
        for nested in getattr(
            getattr(route, "original_router", None), "routes", [route]
        )
    )
    app.router.routes.extend(
        route
        for route in read_routes
        if getattr(route, "path", "") in _RESET_READ_PATHS
    )
    legacy = create_app(repository=repository)
    app.router.routes.extend(
        route
        for route in legacy.routes
        if getattr(route, "path", "").startswith("/api/v1/")
    )

    def fixed_error(status, code, message):
        async def handler(request, exc):
            return JSONResponse({"code": code, "message": message}, status_code=status)

        return handler

    async def owner_error(request, exc):
        return JSONResponse(
            {"code": exc.code, "message": exc.message}, status_code=exc.status_code
        )

    for exception in (*OWNER_ERRORS, BoundaryError):
        app.add_exception_handler(exception, owner_error)
    for exception, status, code, message in (
        (SQLAlchemyError, 503, "storage_unavailable", "Storage unavailable"),
        (StorageUnavailable, 503, "storage_unavailable", "Storage unavailable"),
        (RequestValidationError, 422, "invalid_request", "Invalid request"),
        (ValueError, 422, "invalid_request", "Invalid request"),
        (ArithmeticError, 422, "analysis_unavailable", "Analysis unavailable"),
        (Forbidden, 403, "source_forbidden", "Source access denied"),
        (NotFound, 404, "snapshot_not_found", "Snapshot not found"),
        (Conflict, 409, "conflict", "Conflict"),
        (QualityRejected, 422, "quality_rejected", "Quality gate rejected"),
    ):
        app.add_exception_handler(exception, fixed_error(status, code, message))
    app.add_exception_handler(HTTPException, fixed_error(404, "not_found", "Not found"))

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if database is None or not database.ready():
            raise storage_unavailable()
        return {"status": "ok"}

    @app.post(PAIR_PATH)
    def pair(request: Request):
        body = json_object(request, {"code"})
        if not isinstance(body.get("code"), str):
            raise invalid_request()
        if owner_repository is None:
            raise storage_unavailable()
        issued = owner_repository.exchange_pairing(body["code"], origin)
        response = JSONResponse(public_identity(issued))
        for name, value, httponly in (
            (SESSION_COOKIE, issued.token, True),
            (CSRF_COOKIE, issued.csrf, False),
        ):
            response.set_cookie(
                name,
                value,
                max_age=int(SESSION_LIFETIME.total_seconds()),
                secure=True,
                httponly=httponly,
                samesite="strict",
                path="/",
            )
        return response

    @app.get(SESSION_PATH)
    def session(context: OwnerRead):
        return public_identity(context.identity)

    @app.delete(SESSION_PATH)
    def logout(request: Request, context: OwnerWrite):
        json_object(request, set())
        # Gate authorization has completed and released its principal lock.
        # Logout only revokes this token, so concurrent revoke/logout is harmless.
        owner_repository.logout(context.token)
        response = JSONResponse({"status": "logged_out"})
        for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
            response.delete_cookie(
                name, path="/", secure=True, httponly=httponly, samesite="strict"
            )
        return response

    install_review_routes(app)
    install_analysis_job_routes(app)
    install_ai_routes(app)
    install_news_review_routes(app)
    install_query_routes(app)
    app.add_middleware(OwnerGate, origin=origin, owner_repository=owner_repository)
    return app
