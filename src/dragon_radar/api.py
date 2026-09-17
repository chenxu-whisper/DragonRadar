"""Loopback-development read API. This is not a guest/public authorization model."""

import os
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.analytics.calendar import TradingCalendar
from dragon_radar.analytics.metrics import (
    BreadthAnalysis,
    IntervalAnalysis,
    analyze_interval,
    compare_interval,
    market_breadth,
)
from dragon_radar.readmodels.securities import (
    SecuritiesPage,
    SecurityDetail,
    SecuritySort,
    SortOrder,
    list_securities,
    security_detail,
)
from dragon_radar.roles import rules as role_rules
from dragon_radar.storage.database import Database
from dragon_radar.storage.models import Snapshot
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
)


def get_repository(request: Request) -> Repository:
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


class StorageUnavailable(Exception):
    pass


Repo = Annotated[Repository, Depends(get_repository)]


def _envelope(
    snapshot: Snapshot,
    result: IntervalAnalysis
    | BreadthAnalysis
    | SecuritiesPage
    | SecurityDetail
    | None = None,
) -> dict:
    batch = snapshot.batch
    metadata = batch.model_dump(
        mode="json", include={"trade_date", "as_of", "source_time"}
    )
    analysis_coverage = None
    if isinstance(result, IntervalAnalysis):
        analysis_coverage = {
            "requested": len(result.items) + len(result.exclusions),
            "eligible": len(result.items),
            "excluded": len(result.exclusions),
        }
    elif isinstance(result, BreadthAnalysis):
        analysis_coverage = result.coverage.model_dump(mode="json")
    return {
        "trade_date": metadata["trade_date"],
        "as_of": metadata["as_of"],
        "source_time": metadata["source_time"],
        "snapshot_id": str(snapshot.id),
        "source": {"id": batch.source_id, "version": batch.source_version},
        "coverage": snapshot.quality_report["coverage"],
        "coverage_scope": "snapshot_current",
        "analysis_coverage": analysis_coverage,
        "units": {
            name: sorted({getattr(bar, name) for bar in batch.bars})
            for name in ("currency", "price_unit", "volume_unit", "amount_unit")
        },
        "quality_flags": snapshot.quality_report["issues"],
        "rule_version": snapshot.manifest["rule_version"],
        "basis": result.basis if result is not None else "snapshot_summary",
        "manifest": snapshot.manifest,
        "provenance": batch.provenance,
        "result": result.model_dump(mode="json")
        if result is not None
        else {
            "revision": snapshot.revision,
            "supersedes": str(snapshot.supersedes) if snapshot.supersedes else None,
            "published_at": snapshot.published_at.isoformat(),
            "security_count": len(batch.expected_security_ids),
            "calendar_sessions": [
                day.isoformat()
                for day in batch.calendar.sessions
                if day <= batch.trade_date
            ],
            "earliest_date": batch.calendar.sessions[0].isoformat(),
        },
    }


def _query(request: Request, allowed: set[str]) -> None:
    keys = list(request.query_params.keys())
    if any(
        key not in allowed or len(request.query_params.getlist(key)) != 1
        for key in keys
    ):
        raise ValueError("Invalid query parameters")
    for key in {"trade_date", "start", "end"}.intersection(keys):
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", request.query_params[key]):
            raise ValueError("Dates must use ISO YYYY-MM-DD")


def _select(
    repository: Repository, snapshot_id: UUID | None, trade_date: date | None
) -> Snapshot:
    if snapshot_id is not None:
        if trade_date is not None:
            raise ValueError("Snapshot and date selectors cannot be mixed")
        return repository.get_snapshot(snapshot_id)
    results = repository.list_snapshots(trade_date=trade_date, limit=1)
    if not results:
        raise NotFound
    return results[0]


def _interval(
    snapshot: Snapshot,
    n: str | None,
    start: date | None,
    end: date | None,
    ids: str | None,
) -> dict:
    batch = snapshot.batch
    calendar = TradingCalendar(batch.calendar)
    if start is not None or end is not None:
        if start is None or end is None or n is not None:
            raise ValueError("Provide either n or start and end")
        if start < batch.calendar.sessions[0]:
            raise ValueError("Window is outside snapshot history")
        sessions = calendar.custom_window(start, end)
        if sessions[-1] > batch.trade_date:
            raise ValueError("Window exceeds snapshot trade date")
        if len(sessions) > 251:
            raise ValueError("Window exceeds 250 return offsets")
    else:
        if n is not None and not re.fullmatch(r"[0-9]{1,3}", n):
            raise ValueError("Invalid n")
        sessions = calendar.n_day_window(batch.trade_date, int(n) if n else 20)
    if ids is None:
        result = analyze_interval(batch, sessions[0], sessions[-1])
    else:
        requested = ids.split(",")
        if any(not sid.strip() or len(sid) > 200 for sid in requested):
            raise ValueError("Invalid comparison IDs")
        result = compare_interval(batch, sessions[0], sessions[-1], requested)
    return _envelope(snapshot, result)


def create_app(
    database_url: str | None = None, *, repository: Repository | None = None
) -> FastAPI:
    """Explicit URL or environment only; no .env loading and no startup migration."""
    role_rules.validate_frozen_rule_definitions()
    owned = repository is None
    value = database_url or os.environ.get("DRAGON_DATABASE_URL")
    database = (
        repository.database
        if repository is not None
        else (Database(value) if value else None)
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if owned and database is not None:
                database.close()

    app = FastAPI(
        title="Dragon Radar local read API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.repository = repository or (
        Repository(database) if database is not None else None
    )

    def error_handler(status: int, message: str):
        async def handler(request, exc):
            return JSONResponse(status_code=status, content={"error": message})

        return handler

    for exception, status, message in (
        (SQLAlchemyError, 503, "Storage unavailable"),
        (StorageUnavailable, 503, "Storage unavailable"),
        (RequestValidationError, 422, "Invalid request"),
        (ValueError, 400, "Invalid request"),
        (ArithmeticError, 422, "Analysis unavailable"),
        (Forbidden, 403, "Source access denied"),
        (NotFound, 404, "Snapshot not found"),
        (Conflict, 409, "Conflict"),
        (QualityRejected, 422, "Quality gate rejected"),
    ):
        app.add_exception_handler(exception, error_handler(status, message))

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        if database is None or not database.ready():
            raise StorageUnavailable
        return {"status": "ok"}

    @app.get("/api/v1/snapshots")
    def list_snapshots(
        request: Request,
        repo: Repo,
        trade_date: date | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ):
        _query(request, {"trade_date", "limit", "offset"})
        return [
            _envelope(snapshot)
            for snapshot in repo.list_snapshots(
                trade_date=trade_date, limit=limit, offset=offset
            )
        ]

    @app.get("/api/v1/snapshots/{snapshot_id}")
    def detail(snapshot_id: UUID, request: Request, repo: Repo):
        _query(request, set())
        return _envelope(repo.get_snapshot(snapshot_id))

    @app.get("/api/v1/snapshots/{snapshot_id}/securities")
    def snapshot_securities(
        snapshot_id: UUID,
        request: Request,
        repo: Repo,
        q: Annotated[str, Query(max_length=100)] = "",
        sort: SecuritySort = "security_id",
        order: SortOrder = "asc",
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: int = 50,
    ):
        snapshot = repo.get_snapshot(snapshot_id)
        _query(request, {"q", "sort", "order", "page", "page_size"})
        return _envelope(
            snapshot,
            list_securities(
                snapshot.batch,
                q=q,
                sort=sort,
                order=order,
                page=page,
                page_size=page_size,
            ),
        )

    @app.get("/api/v1/snapshots/{snapshot_id}/securities/{security_id}")
    def snapshot_security(
        snapshot_id: UUID, security_id: str, request: Request, repo: Repo
    ):
        snapshot = repo.get_snapshot(snapshot_id)
        _query(request, set())
        return _envelope(snapshot, security_detail(snapshot.batch, security_id))

    @app.get("/api/v1/snapshots/{snapshot_id}/overview")
    def snapshot_overview(snapshot_id: UUID, request: Request, repo: Repo):
        _query(request, set())
        snapshot = repo.get_snapshot(snapshot_id)
        return _envelope(snapshot, market_breadth(snapshot.batch))

    @app.get("/api/v1/snapshots/{snapshot_id}/interval")
    def snapshot_interval(
        snapshot_id: UUID,
        request: Request,
        repo: Repo,
        n: str | None = None,
        start: date | None = None,
        end: date | None = None,
        ids: Annotated[str | None, Query(max_length=1004)] = None,
    ):
        _query(request, {"n", "start", "end", "ids"})
        return _interval(repo.get_snapshot(snapshot_id), n, start, end, ids)

    @app.get("/api/v1/overview")
    def overview(
        request: Request,
        repo: Repo,
        snapshot_id: UUID | None = None,
        trade_date: date | None = None,
    ):
        _query(request, {"snapshot_id", "trade_date"})
        snapshot = _select(repo, snapshot_id, trade_date)
        return _envelope(snapshot, market_breadth(snapshot.batch))

    @app.get("/api/v1/interval")
    def interval(
        request: Request,
        repo: Repo,
        snapshot_id: UUID | None = None,
        trade_date: date | None = None,
        n: str | None = None,
        start: date | None = None,
        end: date | None = None,
        ids: Annotated[str | None, Query(max_length=1004)] = None,
    ):
        _query(request, {"snapshot_id", "trade_date", "n", "start", "end", "ids"})
        return _interval(_select(repo, snapshot_id, trade_date), n, start, end, ids)

    return app
