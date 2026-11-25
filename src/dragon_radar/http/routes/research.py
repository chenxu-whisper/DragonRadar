"""Owner-only HTTP routes for research backtest runs (EX14 submit, EX15 read).

The routes are thin transport adapters over :class:`ResearchRunRepository`:
POST validates the EX14 field whitelist and the strict kernel model, GET is a
lightweight materialized-result read that never recomputes a run, and the
cancel command exposes cooperative termination without any recomputation.
"""

import json
from collections.abc import Mapping
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.http.errors import StorageUnavailable, error_response
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.owner.security import (
    BoundaryError,
    OwnerRead,
    OwnerWrite,
    invalid_request,
    json_object,
    storage_unavailable,
)
from dragon_radar.research.models import (
    BacktestConfig,
    BacktestPeriod,
    BacktestSubmission,
)
from dragon_radar.research.repository import (
    ResearchRunRecord,
    ResearchRunRepository,
)
from dragon_radar.research.result_contracts import (
    ResearchDataUnavailable,
    validate_envelope,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

# EX14 whitelist: the ten BacktestConfig fields plus the idempotency key, which
# deliberately never participates in the config hash.
_EX14_ALLOWED = {
    "schema_version",
    "snapshot_id",
    "strategy_id",
    "parameters",
    "period",
    "cost_model",
    "execution_model",
    "walk_forward",
    "experiment",
    "input_manifests",
    "idempotency_key",
}


class _ResearchRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except ResearchDataUnavailable:
                return error_response(
                    503, "DATA_UNAVAILABLE", "Research data unavailable"
                )
            except ValidationError:
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except Forbidden:
                return error_response(403, "FORBIDDEN", "Source access denied")
            except NotFound:
                return error_response(404, "NOT_FOUND", "Resource not found")
            except Conflict:
                return error_response(409, "VERSION_CONFLICT", "Version conflict")
            except (SQLAlchemyError, StorageUnavailable):
                return error_response(
                    503,
                    "STORAGE_UNAVAILABLE",
                    "Storage unavailable",
                )
            except (TypeError, ValueError):
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


def _repository(request: Request) -> ResearchRunRepository:
    repository = request.app.state.research_repository
    if not isinstance(repository, ResearchRunRepository):
        raise storage_unavailable()
    return repository


def _no_query(request: Request) -> None:
    if request.query_params:
        raise invalid_request()


def _submission(body: dict) -> BacktestSubmission:
    key = body.get("idempotency_key")
    if not isinstance(key, str) or "snapshot_id" not in body:
        raise invalid_request()
    config_payload = {
        name: value for name, value in body.items() if name != "idempotency_key"
    }
    try:
        # JSON-mode validation rebuilds UUID/date/tuple fields from the
        # JSON-native body under the strict, extra-forbidding kernel model.
        config = BacktestConfig.model_validate_json(json.dumps(config_payload))
        return BacktestSubmission(config=config, idempotency_key=UUID(key))
    except (ValidationError, ValueError):
        raise invalid_request() from None


def _jsonable(value):
    # ImmutableJsonObject hydration freezes dicts into MappingProxyType and
    # lists into tuples; unfold them back to plain JSON containers without
    # touching scalar value semantics (Decimal strings stay strings).
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _safe_record(record: ResearchRunRecord) -> dict:
    payload = {
        "run_id": str(record.id),
        "status": record.status,
        "config_hash": record.config.config_hash,
        "research_only": True,
        "result": _jsonable(record.result),
        "failure": _jsonable(record.failure),
    }
    try:
        return validate_envelope(payload, record.config, run_id=record.id)
    except (ValueError, TypeError, KeyError) as exc:
        raise ResearchDataUnavailable("materialized research data unavailable") from exc


def install_research_routes(app: FastAPI) -> None:
    router = APIRouter(route_class=_ResearchRoute)

    @router.get("/api/research/capabilities")
    def get_capabilities(request: Request, owner: OwnerRead):
        pairs = list(request.query_params.multi_items())
        if len(pairs) != 3 or {key for key, _ in pairs} != {
            "snapshot_id",
            "start",
            "end",
        }:
            raise invalid_request()
        params = dict(pairs)
        period = BacktestPeriod.model_validate_json(
            json.dumps(
                {
                    "start": params["start"],
                    "end": params["end"],
                }
            )
        )
        return DecimalJSONResponse(
            _repository(request).capabilities(
                owner.token,
                UUID(params["snapshot_id"]),
                period,
            )
        )

    @router.post("/api/research/backtests")
    def submit_backtest(request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(request, _EX14_ALLOWED)
        submission = _submission(body)
        assert owner.csrf is not None
        record = _repository(request).submit(
            owner.token,
            owner.csrf,
            submission,
        )
        return DecimalJSONResponse({"run_id": str(record.id), "status": record.status})

    @router.get("/api/research/backtests/{run_id}")
    def get_backtest(run_id: UUID, request: Request, owner: OwnerRead):
        _no_query(request)
        record = _repository(request).get(owner.token, run_id)
        return DecimalJSONResponse(_safe_record(record))

    @router.get("/api/research/backtests/{run_id}/config")
    def get_run_config(run_id: UUID, request: Request, owner: OwnerRead):
        _no_query(request)
        return DecimalJSONResponse(_repository(request).run_config(owner.token, run_id))

    @router.get("/api/research/manifests/{snapshot_id}")
    def get_input_manifest(snapshot_id: UUID, request: Request, owner: OwnerRead):
        _no_query(request)
        payload = _repository(request).input_manifest(owner.token, snapshot_id)
        return DecimalJSONResponse(payload)

    @router.post("/api/research/backtests/{run_id}/cancel")
    def cancel_backtest(run_id: UUID, request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(request, {"idempotency_key"})
        if set(body) != {"idempotency_key"} or not isinstance(
            body["idempotency_key"], str
        ):
            raise invalid_request()
        assert owner.csrf is not None
        record = _repository(request).cancel(
            owner.token,
            owner.csrf,
            run_id,
        )
        return DecimalJSONResponse(_safe_record(record))

    app.router.routes.extend(router.routes)


__all__ = ["install_research_routes"]
