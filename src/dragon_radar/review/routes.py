"""Private resources mounted only by the owner application."""

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import StrictBool, StrictInt, StrictStr

from dragon_radar.domain.models import FrozenModel
from dragon_radar.owner.security import (
    BoundaryError,
    OwnerRead,
    OwnerWrite,
    storage_unavailable,
)
from dragon_radar.review.models import NoteContent
from dragon_radar.review.repository import ReviewRepository, VersionConflict
from dragon_radar.storage.repository import NotFound


class NewNote(FrozenModel):
    snapshot_id: UUID
    security_id: StrictStr | None = None
    content: NoteContent


class SaveNote(FrozenModel):
    content: NoteContent


class WatchAdd(FrozenModel):
    snapshot_id: UUID
    security_id: StrictStr


class WatchState(FrozenModel):
    active: StrictBool


class Template(FrozenModel):
    snapshot_id: UUID


class Fork(FrozenModel):
    version: StrictInt
    content: NoteContent | None = None
    snapshot_id: UUID | None = None


class Verification(FrozenModel):
    version: StrictInt
    outcome: Literal["supported", "weakened", "refuted", "not_due"]
    evidence: StrictStr = ""
    snapshot_id: UUID | None = None
    supersedes: UUID | None = None


def _query(request, allowed):
    if any(
        key not in allowed or len(request.query_params.getlist(key)) != 1
        for key in request.query_params
    ):
        raise BoundaryError(400, "invalid_query", "Invalid query")


def _key(request):
    _query(request, set())
    value = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise BoundaryError(400, "idempotency_required", "Idempotency key required")
    return value


def _version(request):
    value = request.headers.get("if-match")
    if value is None:
        raise BoundaryError(428, "version_required", "Expected version required")
    if not re.fullmatch(r'"[1-9][0-9]{0,9}"', value):
        raise BoundaryError(400, "invalid_version", "Invalid expected version")
    return int(value[1:-1])


def _repository(request: Request):
    repository = getattr(request.app.state, "review_repository", None)
    if repository is None:
        raise storage_unavailable()
    return repository


ReviewRepo = Annotated[ReviewRepository, Depends(_repository)]


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except NotFound:
                return JSONResponse(
                    {"code": "not_found", "message": "Not found"}, status_code=404
                )

        return private_handler


def install_review_routes(app):
    """Use state.repository/owner_repository; absent storage fails closed with 503."""
    if getattr(app.state, "review_routes_installed", False):
        return
    market = getattr(app.state, "repository", None)
    owners = getattr(app.state, "owner_repository", None)
    app.state.review_repository = (
        ReviewRepository(owners.database, owners, market)
        if owners is not None and market is not None
        else None
    )
    router = APIRouter(prefix="/api/owner/v1", route_class=PrivateRoute)

    async def version_conflict(request, exc):
        return JSONResponse(
            {
                "code": "version_conflict",
                "message": "Resource version conflict",
                "current": exc.current,
            },
            status_code=409,
        )

    app.add_exception_handler(VersionConflict, version_conflict)

    @router.get("/notes")
    def list_notes(
        request: Request,
        owner: OwnerRead,
        repo: ReviewRepo,
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: Annotated[int, Query()] = 50,
        state: Literal["draft", "confirmed"] | None = None,
    ):
        _query(request, {"page", "page_size", "state"})
        return repo.list_notes(owner.token, page=page, page_size=page_size, state=state)

    @router.post("/notes")
    def create_note(
        body: NewNote, request: Request, owner: OwnerWrite, repo: ReviewRepo
    ):
        return repo.create_note(
            owner.token,
            owner.csrf,
            key=_key(request),
            snapshot_id=body.snapshot_id,
            security_id=body.security_id,
            content=body.content,
        )

    @router.get("/notes/{note_id}")
    def get_note(note_id: UUID, request: Request, owner: OwnerRead, repo: ReviewRepo):
        _query(request, set())
        return repo.get_note(owner.token, note_id)

    @router.patch("/notes/{note_id}")
    def save_note(
        note_id: UUID,
        body: SaveNote,
        request: Request,
        owner: OwnerWrite,
        repo: ReviewRepo,
    ):
        return repo.save_note(
            owner.token,
            owner.csrf,
            note_id,
            key=_key(request),
            expected=_version(request),
            content=body.content,
        )

    @router.post("/notes/{note_id}/confirm")
    def confirm(
        note_id: UUID,
        body: FrozenModel,
        request: Request,
        owner: OwnerWrite,
        repo: ReviewRepo,
    ):
        return repo.confirm_note(
            owner.token,
            owner.csrf,
            note_id,
            key=_key(request),
            expected=_version(request),
        )

    @router.post("/notes/{note_id}/fork")
    def fork(
        note_id: UUID, body: Fork, request: Request, owner: OwnerWrite, repo: ReviewRepo
    ):
        return repo.fork_note(
            owner.token,
            owner.csrf,
            note_id,
            key=_key(request),
            version=body.version,
            content=body.content,
            snapshot_id=body.snapshot_id,
        )

    @router.get("/notes/{note_id}/revisions")
    def revisions(
        note_id: UUID,
        request: Request,
        owner: OwnerRead,
        repo: ReviewRepo,
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: Annotated[int, Query()] = 50,
    ):
        _query(request, {"page", "page_size"})
        return repo.list_revisions(owner.token, note_id, page=page, page_size=page_size)

    @router.get("/notes/{note_id}/revisions/{version}")
    def revision(
        note_id: UUID,
        version: int,
        request: Request,
        owner: OwnerRead,
        repo: ReviewRepo,
    ):
        _query(request, set())
        return repo.get_revision(owner.token, note_id, version)

    @router.get("/watchlist")
    def list_watch(
        request: Request,
        owner: OwnerRead,
        repo: ReviewRepo,
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: Annotated[int, Query()] = 50,
    ):
        _query(request, {"page", "page_size"})
        return repo.list_watch(owner.token, page=page, page_size=page_size)

    @router.post("/watchlist")
    def add_watch(
        body: WatchAdd, request: Request, owner: OwnerWrite, repo: ReviewRepo
    ):
        return repo.add_watch(
            owner.token,
            owner.csrf,
            key=_key(request),
            snapshot_id=body.snapshot_id,
            security_id=body.security_id,
        )

    @router.patch("/watchlist/{record_id}")
    def set_watch(
        record_id: UUID,
        body: WatchState,
        request: Request,
        owner: OwnerWrite,
        repo: ReviewRepo,
    ):
        return repo.set_watch(
            owner.token,
            owner.csrf,
            record_id,
            key=_key(request),
            expected=_version(request),
            active=body.active,
        )

    @router.post("/review-templates")
    def template(body: Template, request: Request, owner: OwnerWrite, repo: ReviewRepo):
        return repo.create_template(
            owner.token, owner.csrf, key=_key(request), snapshot_id=body.snapshot_id
        )

    @router.get("/notes/{note_id}/verifications")
    def list_verifications(
        note_id: UUID,
        request: Request,
        owner: OwnerRead,
        repo: ReviewRepo,
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: Annotated[int, Query()] = 50,
    ):
        _query(request, {"page", "page_size"})
        return repo.list_verifications(
            owner.token, note_id, page=page, page_size=page_size
        )

    @router.post("/notes/{note_id}/verifications")
    def add_verification(
        note_id: UUID,
        body: Verification,
        request: Request,
        owner: OwnerWrite,
        repo: ReviewRepo,
    ):
        return repo.add_verification(
            owner.token, owner.csrf, note_id, key=_key(request), **body.model_dump()
        )

    # Routes already carry their prefix and private handler; keep the owner
    # application's explicit, flat route table used by market assembly.
    app.router.routes.extend(router.routes)
    app.state.review_routes_installed = True
