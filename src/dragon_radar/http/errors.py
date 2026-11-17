"""Fixed, sanitized errors shared by workbench routers."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
)


class StorageUnavailable(Exception):
    pass


class DataUnavailable(Exception):
    pass


class UpstreamError(Exception):
    """Adapter-facing helper; provider messages must not become HTTP content."""

    def __init__(self, *, rate_limited: bool = False):
        super().__init__()
        self.rate_limited = rate_limited


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def register_error_handlers(app: FastAPI) -> None:
    def fixed(status: int, code: str, message: str):
        async def handler(request, exc):
            return error_response(status, code, message)

        return handler

    for exception, status, code, message in (
        (RequestValidationError, 400, "INVALID_PARAM", "Invalid request"),
        (ValueError, 400, "INVALID_PARAM", "Invalid request"),
        (Forbidden, 403, "FORBIDDEN", "Source access denied"),
        (NotFound, 404, "NOT_FOUND", "Resource not found"),
        (Conflict, 409, "VERSION_CONFLICT", "Version conflict"),
        (SQLAlchemyError, 503, "STORAGE_UNAVAILABLE", "Storage unavailable"),
        (StorageUnavailable, 503, "STORAGE_UNAVAILABLE", "Storage unavailable"),
        (DataUnavailable, 503, "DATA_UNAVAILABLE", "Data unavailable"),
        (ArithmeticError, 503, "DATA_UNAVAILABLE", "Data unavailable"),
        (QualityRejected, 503, "DATA_UNAVAILABLE", "Data unavailable"),
        (ResponseValidationError, 503, "DATA_UNAVAILABLE", "Data unavailable"),
    ):
        app.add_exception_handler(exception, fixed(status, code, message))

    @app.exception_handler(UpstreamError)
    async def upstream(request, exc: UpstreamError):
        if exc.rate_limited:
            return error_response(429, "RATE_LIMITED", "Too many requests")
        return error_response(502, "UPSTREAM_ERROR", "Upstream unavailable")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc: HTTPException):
        errors = {
            400: ("INVALID_PARAM", "Invalid request"),
            403: ("FORBIDDEN", "Access denied"),
            404: ("NOT_FOUND", "Resource not found"),
            405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
            409: ("VERSION_CONFLICT", "Version conflict"),
            429: ("RATE_LIMITED", "Too many requests"),
            502: ("UPSTREAM_ERROR", "Upstream unavailable"),
            503: ("STORAGE_UNAVAILABLE", "Storage unavailable"),
        }
        code, message = errors.get(exc.status_code, ("HTTP_ERROR", "Request failed"))
        return error_response(exc.status_code, code, message)
