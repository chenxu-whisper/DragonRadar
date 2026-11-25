"""Owner/CSRF-only promotion routes, installed only on the private application."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, Request

from dragon_radar.ai.publication import AIPublicationService
from dragon_radar.http.errors import StorageUnavailable
from dragon_radar.http.routes.ai import _AIRoute
from dragon_radar.http.selection import query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.owner.security import (
    OwnerRead,
    OwnerWrite,
    invalid_request,
    json_object,
)
from dragon_radar.public_daily_ai.models import (
    PromoteSummary,
    PublishDaily,
    RevokeArtifact,
    RevokeSummary,
)


def install_ai_publication_routes(app: FastAPI):
    router = APIRouter(route_class=_AIRoute)

    def command(request, model):
        if request.query_params:
            raise invalid_request()
        fields = set(model.model_fields)
        body = json_object(request, fields)
        if set(body) != fields:
            raise invalid_request()
        return model.model_validate(body)

    def service(request):
        value = getattr(request.app.state, "ai_publication_service", None)
        if not isinstance(value, AIPublicationService):
            raise StorageUnavailable
        return value

    @router.get("/api/ai/task/{task_id}/publication-eligibility")
    def eligibility(task_id: UUID, request: Request, owner: OwnerRead):
        values = query_values(request, {"output_entitlement_reference"})
        if set(values) != {"output_entitlement_reference"}:
            raise invalid_request()
        from pydantic import TypeAdapter

        from dragon_radar.public_daily_ai.models import Reference

        reference = TypeAdapter(Reference).validate_python(
            values["output_entitlement_reference"]
        )
        return DecimalJSONResponse(
            service(request).eligibility(owner.token, task_id, reference)
        )

    @router.post("/api/ai/task/{task_id}/publish-daily")
    def daily(task_id: UUID, request: Request, owner: OwnerWrite):
        value = command(request, PublishDaily)
        return DecimalJSONResponse(
            service(request).publish_daily(owner.token, owner.csrf, task_id, value)
        )

    @router.post("/api/ai/task/{task_id}/publish-summary")
    def summary(task_id: UUID, request: Request, owner: OwnerWrite):
        value = command(request, PromoteSummary)
        return DecimalJSONResponse(
            service(request).publish_summary(owner.token, owner.csrf, task_id, value)
        )

    @router.post("/api/ai/task/{task_id}/published-daily/{archive_id}/revoke")
    def revoke_daily(
        task_id: UUID, archive_id: UUID, request: Request, owner: OwnerWrite
    ):
        value = command(request, RevokeArtifact)
        return DecimalJSONResponse(
            service(request).revoke(
                owner.token,
                owner.csrf,
                task_id,
                archive_id,
                value,
            )
        )

    @router.post("/api/ai/task/{task_id}/published-summary/{summary_id}/revoke")
    def revoke_summary(
        task_id: UUID, summary_id: UUID, request: Request, owner: OwnerWrite
    ):
        summary_command = command(request, RevokeSummary)
        value = RevokeArtifact(
            expected_archive_version=summary_command.expected_summary_revision,
            idempotency_key=summary_command.idempotency_key,
            reason_code=summary_command.reason_code,
        )
        return DecimalJSONResponse(
            service(request).revoke(
                owner.token,
                owner.csrf,
                task_id,
                summary_id,
                value,
                kind="summary",
            )
        )

    app.include_router(router)
