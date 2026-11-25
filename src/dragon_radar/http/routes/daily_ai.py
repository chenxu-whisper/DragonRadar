"""Public GET-only sidecar endpoints; no private task service imports."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.http.errors import DataUnavailable, StorageUnavailable, error_response
from dragon_radar.http.selection import query_date, query_integer, query_values
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.storage.repository import Forbidden, NotFound


class DailyAIRoute(APIRoute):
    def get_route_handler(self):
        inner = super().get_route_handler()

        async def handler(request):
            try:
                response = await inner(request)
            except (RequestValidationError, ValueError, TypeError):
                response = error_response(400, "INVALID_PARAM", "Invalid request")
            except Forbidden:
                response = error_response(403, "FORBIDDEN", "Source access denied")
            except NotFound:
                response = error_response(404, "NOT_FOUND", "Resource not found")
            except DataUnavailable:
                response = error_response(503, "DATA_UNAVAILABLE", "Data unavailable")
            except (SQLAlchemyError, StorageUnavailable):
                response = error_response(
                    503, "STORAGE_UNAVAILABLE", "Storage unavailable"
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


def install_daily_ai_routes(app: FastAPI):
    router = APIRouter(route_class=DailyAIRoute)

    def selection(request, discovery=False):
        allowed = (
            {"date", "snapshot_id", "limit", "cursor"}
            if discovery
            else {"date", "snapshot_id", "archive_id"}
        )
        values = query_values(request, allowed)
        if not {"date", "snapshot_id"} <= set(values):
            raise ValueError("Required query missing")
        return values, UUID(values["snapshot_id"]), query_date(values["date"])

    def service(request):
        value = getattr(request.app.state, "public_daily_ai_service", None)
        if value is None:
            raise StorageUnavailable
        return value

    @router.get("/api/market/daily-ai")
    def daily(request: Request):
        values, snapshot_id, trade_date = selection(request)
        archive_id = UUID(values["archive_id"]) if "archive_id" in values else None
        return DecimalJSONResponse(
            service(request).get(snapshot_id, trade_date, archive_id=archive_id)
        )

    @router.get("/api/market/daily-ai/archives")
    def archives(request: Request):
        values, snapshot_id, trade_date = selection(request, discovery=True)
        limit = query_integer(values.get("limit", "50"), 1, 100)
        from dragon_radar.public_daily_ai.service import decode_cursor

        if "cursor" in values:
            decode_cursor(values["cursor"], snapshot_id, trade_date)
        return DecimalJSONResponse(
            service(request).discover(
                snapshot_id,
                trade_date,
                limit=limit,
                cursor=values.get("cursor"),
            )
        )

    app.include_router(router)
