"""Owner-only HTTP routes for whitelist natural-language queries (EX12/EX13)."""

from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.api import StorageUnavailable as LegacyStorageUnavailable
from dragon_radar.http.errors import (
    DataUnavailable,
    StorageUnavailable,
    error_response,
)
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.owner.security import (
    BoundaryError,
    OwnerWrite,
    invalid_request,
    json_object,
    storage_unavailable,
)
from dragon_radar.query.executor import execute_query
from dragon_radar.query.models import wire_ast
from dragon_radar.query.parser import explain_ast, with_snapshot_line
from dragon_radar.query.repository import QueryRepository
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    StoredRecordCorrupt,
)

_PARSE_KEYS = {"text", "snapshot_id", "idempotency_key"}
_EXECUTE_KEYS = {
    "query_id",
    "confirmed_ast_hash",
    "snapshot_id",
    "idempotency_key",
}


class _QueryRoute(APIRoute):
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
            except (
                SQLAlchemyError,
                StorageUnavailable,
                LegacyStorageUnavailable,
            ):
                return error_response(
                    503,
                    "STORAGE_UNAVAILABLE",
                    "Storage unavailable",
                )
            except (
                DataUnavailable,
                ArithmeticError,
                QualityRejected,
                # A tampered/unreadable immutable AST is data-side corruption,
                # not a client 400 or a storage outage.
                StoredRecordCorrupt,
            ):
                return error_response(503, "DATA_UNAVAILABLE", "Data unavailable")
            except ValueError:
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except BoundaryError as error:
                if error.status_code == 503:
                    return error_response(
                        503,
                        "STORAGE_UNAVAILABLE",
                        "Storage unavailable",
                    )
                return error_response(400, "INVALID_PARAM", "Invalid request")

        return handler


def _repository(request: Request) -> QueryRepository:
    repository = request.app.state.query_repository
    if not isinstance(repository, QueryRepository):
        raise storage_unavailable()
    return repository


def _no_query(request: Request) -> None:
    if request.query_params:
        raise invalid_request()


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise invalid_request()
    try:
        return UUID(value)
    except ValueError:
        raise invalid_request() from None


def _string_body(body: dict, keys: set[str]) -> None:
    if any(not isinstance(body.get(key), str) for key in keys):
        raise invalid_request()


def install_query_routes(app: FastAPI) -> None:
    router = APIRouter(route_class=_QueryRoute)

    @router.post("/api/query/parse")
    def parse(request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(request, _PARSE_KEYS)
        _string_body(body, _PARSE_KEYS)
        snapshot_id = _uuid(body["snapshot_id"])
        key = body["idempotency_key"]
        assert owner.csrf is not None
        parsed = _repository(request).parse(
            owner.token,
            owner.csrf,
            text_value=body["text"],
            snapshot_id=snapshot_id,
            key=key,
        )
        if parsed.record is None:
            return DecimalJSONResponse(
                {
                    "query_id": None,
                    "typed_ast": None,
                    "explanation": None,
                    "unsupported": [
                        item.model_dump(mode="json") for item in parsed.unsupported
                    ],
                }
            )
        record = parsed.record
        snapshot = parsed.snapshot
        return DecimalJSONResponse(
            {
                "query_id": str(record.id),
                "typed_ast": wire_ast(record.ast).model_dump(mode="json"),
                "explanation": with_snapshot_line(
                    explain_ast(record.ast),
                    trade_date=snapshot.trade_date,
                    revision=snapshot.revision,
                ),
                "unsupported": [],
            }
        )

    @router.post("/api/query/execute")
    def execute(request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(request, _EXECUTE_KEYS)
        _string_body(body, _EXECUTE_KEYS)
        query_id = _uuid(body["query_id"])
        snapshot_id = _uuid(body["snapshot_id"])
        assert owner.csrf is not None
        record, snapshot = _repository(request).execute(
            owner.token,
            owner.csrf,
            query_id=query_id,
            confirmed_ast_hash=body["confirmed_ast_hash"],
            snapshot_id=snapshot_id,
            key=body["idempotency_key"],
        )
        return DecimalJSONResponse(execute_query(snapshot, record.ast))

    app.router.routes.extend(router.routes)


__all__ = ["install_query_routes"]
