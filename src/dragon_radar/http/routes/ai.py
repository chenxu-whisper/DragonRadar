"""Owner-only HTTP endpoints for asynchronous AI tasks."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.ai.models import AITaskRecord
from dragon_radar.ai.repository import AITaskRateLimited
from dragon_radar.ai.service import AIService
from dragon_radar.http.errors import StorageUnavailable, error_response
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.owner.security import (
    BoundaryError,
    OwnerRead,
    OwnerWrite,
    invalid_request,
    json_object,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound


class _AIRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except Forbidden:
                return error_response(403, "FORBIDDEN", "Source access denied")
            except NotFound:
                return error_response(404, "NOT_FOUND", "Resource not found")
            except Conflict:
                return error_response(409, "VERSION_CONFLICT", "Version conflict")
            except AITaskRateLimited:
                return error_response(429, "RATE_LIMITED", "Too many requests")
            except (SQLAlchemyError, StorageUnavailable):
                return error_response(503, "STORAGE_UNAVAILABLE", "Storage unavailable")
            except (BoundaryError, TypeError, ValueError):
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except Exception:
                return error_response(503, "AI_UNAVAILABLE", "AI task unavailable")

        return handler


def _service(request: Request) -> AIService:
    service = request.app.state.ai_service
    if not isinstance(service, AIService):
        raise StorageUnavailable
    return service


def _no_query(request: Request) -> None:
    if request.query_params:
        raise invalid_request()


def _safe_record(record: AITaskRecord) -> dict:
    draft = (
        record.content_draft.model_dump(mode="json")
        if record.content_draft is not None
        else None
    )
    return {
        "task_id": str(record.id),
        "task_type": record.task_type,
        "snapshot_id": str(record.snapshot_id),
        "as_of": record.as_of.isoformat(),
        "version": record.version,
        "status": record.status,
        "verification_status": record.verification_status,
        "human_review_status": record.human_review_status,
        "processing_overdue": record.processing_overdue,
        "content_draft": draft,
        "model_version": record.model_version,
        "prompt_version": record.prompt_version,
        "retrieval_version": record.retrieval_version,
        "provider": record.provider,
        "token_usage": record.token_usage,
        "cost": record.cost,
        "latency_ms": record.latency_ms,
        "failure": record.failure,
    }


def install_ai_routes(app: FastAPI) -> None:
    router = APIRouter(route_class=_AIRoute)

    def submit(request: Request, owner: OwnerWrite, task_type: str, allowed: set[str]):
        _no_query(request)
        common = {"snapshot_id", "as_of", "version", "idempotency_key"}
        body = json_object(request, common | allowed)
        if set(body) != common | allowed:
            raise invalid_request()
        key = body["idempotency_key"]
        if not isinstance(key, str):
            raise invalid_request()
        assert owner.csrf is not None
        record = _service(request).submit(
            owner.token,
            owner.csrf,
            task_type=task_type,
            snapshot_id=body["snapshot_id"],
            as_of=body["as_of"],
            version=body["version"],
            task_input={field: body[field] for field in allowed},
            key=key,
        )
        return DecimalJSONResponse(_safe_record(record))

    @router.post("/api/ai/summarize")
    def summarize(request: Request, owner: OwnerWrite):
        return submit(request, owner, "summarize", {"article_id"})

    @router.post("/api/ai/topic-suggest")
    def topic_suggest(request: Request, owner: OwnerWrite):
        return submit(request, owner, "topic_suggest", {"date"})

    @router.post("/api/ai/explain-stock")
    def explain_stock(request: Request, owner: OwnerWrite):
        return submit(request, owner, "explain_stock", {"security_id", "date"})

    @router.post("/api/ai/daily-review")
    def daily_review(request: Request, owner: OwnerWrite):
        return submit(request, owner, "daily_review", {"date", "notes_context"})

    @router.get("/api/ai/task/{task_id}")
    def get_task(task_id: UUID, request: Request, owner: OwnerRead):
        _no_query(request)
        record = _service(request).tasks.get(owner.token, task_id)
        return DecimalJSONResponse(_safe_record(record))

    @router.post("/api/ai/task/{task_id}/cancel")
    def cancel_task(task_id: UUID, request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(request, {"idempotency_key"})
        if set(body) != {"idempotency_key"} or not isinstance(
            body["idempotency_key"], str
        ):
            raise invalid_request()
        assert owner.csrf is not None
        receipt = _service(request).tasks.cancel(
            owner.token,
            owner.csrf,
            task_id,
            key=body["idempotency_key"],
        )
        return DecimalJSONResponse(receipt.model_dump(mode="json"))

    @router.post("/api/ai/task/{task_id}/review")
    def review_task(task_id: UUID, request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(
            request,
            {"decision", "revision", "idempotency_key", "edited_content"},
        )
        required = {"decision", "revision", "idempotency_key"}
        if (
            not required <= set(body)
            or body["decision"] not in {"accept", "edit", "reject"}
            or type(body["revision"]) is not int
            or not isinstance(body["idempotency_key"], str)
        ):
            raise invalid_request()
        assert owner.csrf is not None
        receipt = _service(request).tasks.review(
            owner.token,
            owner.csrf,
            task_id,
            decision=body["decision"],
            revision=body["revision"],
            key=body["idempotency_key"],
            edited_content=body.get("edited_content"),
        )
        return DecimalJSONResponse(receipt.model_dump(mode="json"))

    app.router.routes.extend(router.routes)


__all__ = ["install_ai_routes"]
