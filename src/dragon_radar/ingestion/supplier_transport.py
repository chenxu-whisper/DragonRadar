"""Bounded stdlib iFinD HTTPS; credentials exist only during a request."""

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import grant_allows_storage
from dragon_radar.production.trust import (
    EvidenceError,
    canonical_json,
    strict_json,
)

OFFICIAL_HOSTS = frozenset({"quantapi.51ifind.com", "quantapi.10jqka.com.cn"})
_CODES = frozenset(
    {
        "invalid_endpoint",
        "invalid_request",
        "invalid_response",
        "invalid_limits",
        "storage_denied",
        "missing_credentials",
        "authentication_failed",
        "redirect_rejected",
        "rate_limited",
        "server_error",
        "http_error",
        "timeout",
        "network_error",
        "checkpoint_mismatch",
        "archive_error",
        "collection_incomplete",
        "archive_budget_exceeded",
        "invalid_extraction",
        "extraction_failed",
        "mapping_binding_mismatch",
    }
)


class SupplierError(Exception):
    """Only fixed codes cross the boundary, never remote errors or input values."""

    def __init__(self, code="invalid_request"):
        self.code = code if code in _CODES else "invalid_request"
        self.retryable = self.code in {"rate_limited", "server_error", "timeout"}
        super().__init__(self.code)
        self.__suppress_context__ = True


def utc_now() -> datetime:
    return datetime.now(UTC)


def operation_time(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise SupplierError("invalid_request") from None
    return value.astimezone(UTC)


def authorize(grant: SourceGrant, source_id: str, now: datetime) -> None:
    try:
        checked = SourceGrant.model_validate(grant.model_dump())
        allowed = grant_allows_storage(checked, source_id=source_id, now=now)
    except Exception:
        raise SupplierError("storage_denied") from None
    if not allowed:
        raise SupplierError("storage_denied") from None


def validate_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        if (
            type(endpoint) is not str
            or parsed.scheme != "https"
            or parsed.netloc not in OFFICIAL_HOSTS
            or parsed.hostname not in OFFICIAL_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_/-]+", parsed.path)
            or ".." in parsed.path
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SupplierError("invalid_endpoint") from None
    return endpoint


def credential_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(
        word in normalized
        for word in (
            "token",
            "password",
            "passwd",
            "secret",
            "credential",
            "authorization",
            "apikey",
            "accesskey",
            "privatekey",
            "cookie",
        )
    )


def safe_payload(value: Mapping, token: str | None = None) -> bytes:
    """Reject credential-shaped keys at every depth, and known credential values."""
    try:
        content = canonical_json(value)
        if token and token in content.decode("utf-8"):
            raise ValueError

        def check(item):
            if isinstance(item, Mapping):
                for key, child in item.items():
                    if credential_key(key) or (token and token in key):
                        raise ValueError
                    check(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check(child)
            elif token and isinstance(item, str) and token in item:
                raise ValueError

        check(value)
        return content
    except (EvidenceError, ValueError, TypeError, RecursionError):
        raise SupplierError("invalid_request") from None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class ReceivedResponse:
    raw: bytes = field(repr=False)
    first_received_at: datetime


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError("", code, "redirect_rejected", {}, None)


def https_send(request: Request, timeout: float, maximum: int) -> HttpResponse:
    """No ambient proxy credentials; no redirects, no unbounded response read."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read(maximum + 1))
    except HTTPError as error:
        status = error.code
        error.close()
        return HttpResponse(status, b"")


@dataclass(frozen=True, repr=False)
class IfindTransport:
    endpoint: str
    token_env: str = "IFIND_ACCESS_TOKEN"
    timeout: float = 10.0
    max_response_bytes: int = 4 * 1024 * 1024
    send: Callable[[Request, float, int], HttpResponse] = field(
        default=https_send, repr=False
    )

    def __post_init__(self):
        validate_endpoint(self.endpoint)
        if (
            type(self.timeout) not in (float, int)
            or not 0 < self.timeout <= 60
            or type(self.max_response_bytes) is not int
            or not 1 <= self.max_response_bytes <= 4 * 1024 * 1024
            or type(self.token_env) is not str
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.token_env)
            or not callable(self.send)
        ):
            raise SupplierError("invalid_limits") from None

    def __repr__(self):
        return "IfindTransport()"

    def post(
        self,
        payload: Mapping,
        *,
        grant: SourceGrant,
        source_id: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> ReceivedResponse:
        """Authorize every send and receipt; return bytes, never parsed floats."""
        try:
            token = os.environ.get(self.token_env)
            if (
                not token
                or len(token) > 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token)
            ):
                raise SupplierError("missing_credentials")
            content = safe_payload(payload, token)
            if len(content) > 1024 * 1024:
                raise SupplierError("invalid_request")
            request = Request(
                self.endpoint,
                data=content,
                method="POST",
                headers={"Content-Type": "application/json", "access_token": token},
            )
            authorize(grant, source_id, operation_time(clock))
            response = self.send(request, self.timeout, self.max_response_bytes)
            received = operation_time(clock)
            authorize(grant, source_id, received)
            if (
                not isinstance(response, HttpResponse)
                or type(response.status) is not int
            ):
                raise SupplierError("invalid_response")
            status = response.status
            if status in (401, 403):
                raise SupplierError("authentication_failed")
            if 300 <= status < 400:
                raise SupplierError("redirect_rejected")
            if status == 429:
                raise SupplierError("rate_limited")
            if 500 <= status <= 599:
                raise SupplierError("server_error")
            if status != 200:
                raise SupplierError("http_error")
            raw = response.body
            if type(raw) is not bytes or not 0 < len(raw) <= self.max_response_bytes:
                raise SupplierError("invalid_response")
            value = strict_json(raw)
            try:
                safe_payload(value, token)
            except SupplierError:
                raise SupplierError("invalid_response") from None
            if "errorcode" in value and (
                type(value["errorcode"]) is not int or value["errorcode"] != 0
            ):
                raise SupplierError("invalid_response")
            return ReceivedResponse(raw, received)
        except SupplierError:
            raise
        except TimeoutError:
            raise SupplierError("timeout") from None
        except URLError as error:
            code = (
                "timeout" if isinstance(error.reason, TimeoutError) else "network_error"
            )
            raise SupplierError(code) from None
        except EvidenceError:
            raise SupplierError("invalid_response") from None
        except Exception:
            raise SupplierError("network_error") from None
