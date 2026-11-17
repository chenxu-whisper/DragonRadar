"""Owner HTTP boundary and transaction-ready, non-serializable credentials."""

import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.engine import Connection
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from dragon_radar.owner.errors import (
    CsrfForbidden,
    InvalidPairing,
    PairingRateLimited,
    SessionUnauthorized,
)
from dragon_radar.owner.models import OwnerIdentity
from dragon_radar.owner.repository import OwnerRepository

SESSION_COOKIE = "__Host-dragon-private-session"
CSRF_COOKIE = "__Host-dragon-private-csrf"
MAX_BODY_BYTES = 131072
ANALYSIS_JOBS_PATH = "/api/analysis/jobs"
AI_TASKS_PATH = "/api/ai/"
NEWS_PATH = "/api/news/"
QUERY_PATH = "/api/query/"
PAIR_PATH = "/api/owner/v1/pair"
SESSION_PATH = "/api/owner/v1/session"
OWNER_ERRORS = (CsrfForbidden, InvalidPairing, PairingRateLimited, SessionUnauthorized)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class BoundaryError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def forbidden() -> BoundaryError:
    return BoundaryError(403, "origin_forbidden", "Owner request forbidden")


def invalid_request() -> BoundaryError:
    return BoundaryError(422, "invalid_request", "Invalid request")


def storage_unavailable() -> BoundaryError:
    return BoundaryError(503, "storage_unavailable", "Storage unavailable")


def _analysis_path(path: str) -> bool:
    return (
        path == ANALYSIS_JOBS_PATH
        or path.startswith(ANALYSIS_JOBS_PATH + "/")
        or path.startswith(AI_TASKS_PATH)
    )


def _news_review_path(path: str) -> bool:
    return path.startswith(NEWS_PATH) and path.endswith("/associations/review")


def _query_path(path: str) -> bool:
    return path.startswith(QUERY_PATH)


def _analysis_error(exc: Exception) -> BoundaryError:
    if isinstance(exc, (SessionUnauthorized, CsrfForbidden)):
        return BoundaryError(403, "FORBIDDEN", "Access denied")
    if isinstance(exc, BoundaryError):
        if exc.status_code == 403:
            return BoundaryError(403, "FORBIDDEN", "Access denied")
        if exc.status_code == 503:
            return BoundaryError(503, "STORAGE_UNAVAILABLE", "Storage unavailable")
        status = 413 if exc.status_code == 413 else 400
        return BoundaryError(status, "INVALID_PARAM", "Invalid request")
    return BoundaryError(503, "STORAGE_UNAVAILABLE", "Storage unavailable")


def validate_origin(origin: str) -> str:
    """Return the exact authority; do not canonicalize untrusted aliases."""
    match = (
        re.fullmatch(r"https://localhost:([1-9][0-9]{0,4})", origin)
        if isinstance(origin, str)
        else None
    )
    if match is None or int(match[1]) > 65535:
        raise ValueError("Invalid owner origin")
    return origin.removeprefix("https://")


def public_identity(identity: OwnerIdentity) -> dict[str, str]:
    return {
        "owner_id": str(identity.owner_id),
        "session_id": str(identity.session_id),
        "expires_at": identity.expires_at.isoformat(),
    }


@dataclass(frozen=True)
class OwnerContext:
    """Identity is a read result, never a substitute for transaction authorization."""

    identity: OwnerIdentity
    token: str = field(repr=False)
    csrf: str | None = field(repr=False)
    origin: str

    def authorize(
        self, connection: Connection, owner_repository: OwnerRepository
    ) -> OwnerIdentity:
        identity = owner_repository.authorize_connection(
            connection, self.token, self.csrf
        )
        if identity.origin != self.origin:
            raise SessionUnauthorized()
        return identity


def get_owner_context(request: Request) -> OwnerContext:
    context = getattr(request.state, "owner_context", None)
    if not isinstance(context, OwnerContext):
        raise SessionUnauthorized()
    return context


def get_owner_write_context(request: Request) -> OwnerContext:
    context = get_owner_context(request)
    if not context.csrf:
        raise CsrfForbidden()
    return context


OwnerRead = Annotated[OwnerContext, Depends(get_owner_context)]
OwnerWrite = Annotated[OwnerContext, Depends(get_owner_write_context)]


def json_object(request: Request, allowed: set[str]) -> dict:
    """Read the already bounded, strictly decoded object, rejecting unknown keys."""
    body = getattr(request.state, "owner_json", None)
    if not isinstance(body, dict) or set(body) - allowed:
        raise invalid_request()
    return body


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise invalid_request()
        result[key] = value
    return result


def _constant(value: str):
    raise invalid_request()


def _headers(scope: Scope) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for key, value in scope["headers"]:
        headers.setdefault(key.decode("latin-1").lower(), []).append(
            value.decode("latin-1")
        )
    for name in (
        "host",
        "origin",
        "authorization",
        "x-dragon-owner-pair",
        "x-csrf-token",
        "idempotency-key",
        "if-match",
        "sec-fetch-site",
        "content-type",
        "content-length",
        "transfer-encoding",
    ):
        if len(headers.get(name, [])) > 1:
            raise forbidden()
    return headers


def _cookies(headers: dict[str, list[str]]) -> dict[str, str]:
    result = {}
    for header in headers.get("cookie", []):
        for item in header.split(";"):
            name, separator, value = item.strip().partition("=")
            if name not in (SESSION_COOKIE, CSRF_COOKIE):
                continue
            if (
                name in result
                or not separator
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", value)
            ):
                raise forbidden()
            result[name] = value
    return result


class OwnerGate:
    """Pure ASGI gate: checks run before routing and bodies are bounded pre-JSON."""

    def __init__(
        self, app: ASGIApp, origin: str, owner_repository: OwnerRepository | None
    ):
        self.app = app
        self.origin = origin
        self.authority = validate_origin(origin)
        self.owner_repository = owner_repository

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        started = False

        async def no_store(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                    and not key.lower().startswith(b"access-control-")
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        try:
            body = await self._check(scope, receive)
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, no_store)
        except (*OWNER_ERRORS, BoundaryError) as exc:
            if started:
                raise
            if (
                _analysis_path(scope["path"])
                or _news_review_path(scope["path"])
                or _query_path(scope["path"])
            ):
                exc = _analysis_error(exc)
            await JSONResponse(
                {"code": exc.code, "message": exc.message}, status_code=exc.status_code
            )(scope, receive, no_store)
        except Exception as exc:
            if started:
                raise
            exc = (
                _analysis_error(exc)
                if _analysis_path(scope["path"])
                or _news_review_path(scope["path"])
                or _query_path(scope["path"])
                else storage_unavailable()
            )
            await JSONResponse(
                {"code": exc.code, "message": exc.message}, status_code=exc.status_code
            )(scope, receive, no_store)

    async def _check(self, scope: Scope, receive: Receive) -> bytes:
        headers = _headers(scope)
        if headers.get("host") != [self.authority]:
            raise forbidden()
        origin = headers.get("origin")
        if origin is not None and origin != [self.origin]:
            raise forbidden()
        if headers.get("sec-fetch-site", ["none"])[0] not in (
            "none",
            "same-origin",
            "same-site",
        ):
            raise forbidden()
        cookies = _cookies(headers)
        path, method = scope["path"], scope["method"]
        pairing = path == PAIR_PATH and method == "POST"
        writing = method not in SAFE_METHODS
        if writing:
            if origin != [self.origin]:
                raise forbidden()
            media_type = headers.get("content-type", [""])[0].split(";", 1)[0]
            if media_type.strip().lower() != "application/json":
                raise invalid_request()
        if pairing and headers.get("x-dragon-owner-pair") != ["1"]:
            raise forbidden()
        if path in (PAIR_PATH, SESSION_PATH, "/health/live", "/health/ready"):
            if scope.get("query_string"):
                raise invalid_request()

        state = scope.setdefault("state", {})
        if (path == "/api" or path.startswith("/api/")) and not pairing:
            token = cookies.get(SESSION_COOKIE)
            if token is None:
                raise SessionUnauthorized()
            if self.owner_repository is None:
                raise storage_unavailable()
            csrf = None
            if writing:
                csrf = headers.get("x-csrf-token", [""])[0]
                cookie = cookies.get(CSRF_COOKIE, "")
                if (
                    not csrf
                    or not cookie
                    or not hmac.compare_digest(
                        csrf.encode("utf-8"), cookie.encode("utf-8")
                    )
                ):
                    raise CsrfForbidden()

            def authorize():
                with self.owner_repository.database.engine.begin() as connection:
                    identity = self.owner_repository.authorize_connection(
                        connection, token, csrf
                    )
                    if identity.origin != self.origin:
                        raise SessionUnauthorized()
                    return OwnerContext(identity, token, csrf, self.origin)

            state["owner_context"] = await run_in_threadpool(authorize)

        size = headers.get("content-length")
        if size:
            if not re.fullmatch(r"[0-9]+", size[0]):
                raise invalid_request()
            if len(size[0]) > 9 or int(size[0]) > MAX_BODY_BYTES:
                raise BoundaryError(413, "body_too_large", "Request body too large")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise invalid_request()
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise BoundaryError(413, "body_too_large", "Request body too large")
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if writing:
            try:
                value = json.loads(
                    body.decode("utf-8"),
                    object_pairs_hook=_object,
                    parse_constant=_constant,
                )
            except (ValueError, UnicodeError, RecursionError):
                raise invalid_request() from None
            if not isinstance(value, dict):
                raise invalid_request()
            state["owner_json"] = value
        return bytes(body)
