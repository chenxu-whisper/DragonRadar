"""Explicit session/code chunks, private write-once archives and verified resume.

Use a dedicated existing private directory per collection. Hash verification
detects corruption; it is not a signed supplier receipt or production PIT proof.
"""

import base64
import hashlib
import os
import re
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dragon_radar.domain.models import ExchangeCalendar, SourceGrant
from dragon_radar.ingestion.supplier_transport import (
    IfindTransport,
    SupplierError,
    authorize,
    credential_key,
    operation_time,
    safe_payload,
    utc_now,
    validate_endpoint,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import (
    EvidenceError,
    MissingEvidence,
    canonical_json,
    read_asset,
    strict_json,
)

_MAX_ARCHIVE = 8 * 1024 * 1024


class CollectionRequest(BaseModel):
    """No secrets. Exact calendar subset and explicit supplier codes, never inferred.

    payload_json is canonical immutable JSON for endpoint-specific parameters.
    codes_field and sessions_field declare the two list-valued partition fields.
    profile_sha256 is the external mapping-profile digest, not a generated grant.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )
    source_id: str = Field(min_length=1, max_length=256, repr=False)
    source_version: str = Field(min_length=1, max_length=256, repr=False)
    profile_id: str = Field(min_length=1, max_length=256, repr=False)
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    endpoint: str = Field(repr=False)
    securities: tuple[str, ...] = Field(min_length=1, max_length=10000, repr=False)
    calendar: ExchangeCalendar = Field(repr=False)
    sessions: tuple[date, ...] = Field(min_length=1, max_length=10000, repr=False)
    max_codes: int = Field(default=100, ge=1, le=1000)
    max_sessions: int = Field(default=20, ge=1, le=1000)
    payload_json: str = Field(default="{}", repr=False, max_length=1024 * 1024)
    codes_field: str = "codes"
    sessions_field: str = "dates"

    @field_validator("endpoint")
    @classmethod
    def endpoint_valid(cls, value):
        return validate_endpoint(value)

    @field_validator("payload_json")
    @classmethod
    def payload_valid(cls, value):
        try:
            return safe_payload(strict_json(value.encode("utf-8"))).decode("utf-8")
        except (EvidenceError, SupplierError):
            raise ValueError("invalid_payload") from None

    @model_validator(mode="after")
    def explicit_inputs(self):
        if (
            tuple(sorted(set(self.sessions))) != self.sessions
            or not set(self.sessions) <= set(self.calendar.sessions)
            or len(set(self.securities)) != len(self.securities)
            or any(
                not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", code)
                for code in self.securities
            )
            or self.codes_field == self.sessions_field
            or any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key)
                or credential_key(key)
                for key in (self.codes_field, self.sessions_field)
            )
        ):
            raise ValueError("invalid_partition")
        payload = strict_json(self.payload_json.encode("utf-8"))
        if self.codes_field in payload or self.sessions_field in payload:
            raise ValueError("partition_field_collision")
        if (len(self.securities) + self.max_codes - 1) // self.max_codes * (
            (len(self.sessions) + self.max_sessions - 1) // self.max_sessions
        ) > 10000:
            raise ValueError("too_many_chunks")
        return self


@dataclass(frozen=True)
class CollectedChunk:
    index: int
    fingerprint: str
    raw_path: str
    raw_sha256: str
    archive_sha256: str
    first_received_at: datetime
    provenance: str = "retrospective"


@dataclass(frozen=True)
class CollectionResult:
    fingerprint: str
    chunks: tuple[CollectedChunk, ...]


@dataclass(frozen=True)
class VerifiedChunk:
    metadata: CollectedChunk
    raw: bytes = field(repr=False)


@dataclass(frozen=True)
class VerifiedCollection:
    fingerprint: str
    chunks: tuple[VerifiedChunk, ...]


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_optional(root: Path, name: str) -> bytes | None:
    try:
        return read_asset(root, name, _MAX_ARCHIVE)
    except MissingEvidence:
        return None
    except EvidenceError:
        raise SupplierError("checkpoint_mismatch") from None


def _write_once(
    root: Path,
    name: str,
    content: bytes,
    *,
    before_publish: Callable = lambda: None,
) -> None:
    """Publish fully fsynced bytes atomically without replacing any destination."""
    temporary = root / (".collection-" + uuid4().hex)
    guard = root / (".collection-" + uuid4().hex)
    try:
        with ExitStack() as stack:
            before_publish()
            if os.name == "nt":
                # Keep secure_output's non-reparse ancestor handles pinned while
                # the closed, fsynced temporary file is atomically published.
                stack.enter_context(secure_output(guard))
                link = partial(os.link, temporary, root / name)
                remove = partial(temporary.unlink, missing_ok=True)
            else:
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, directory)
                link = partial(
                    os.link,
                    temporary.name,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                remove = partial(os.unlink, temporary.name, dir_fd=directory)
            before_publish()
            with secure_output(temporary) as stream:
                stream.write(content)
            before_publish()
            try:
                link()
            except FileExistsError:
                remove()
                if _read_optional(root, name) != content:
                    raise SupplierError("checkpoint_mismatch") from None
            else:
                remove()
    except SupplierError:
        raise
    except Exception:
        raise SupplierError("archive_error") from None
    finally:
        temporary.unlink(missing_ok=True)
        guard.unlink(missing_ok=True)


def _archive_names(root: Path) -> dict[int, str]:
    names = {}
    for path in root.glob("chunk-*.raw.json"):
        match = re.fullmatch(r"chunk-(\d{5})-([a-f0-9]{64})\.raw\.json", path.name)
        if not match or int(match[1]) in names or len(names) >= 10000:
            raise SupplierError("checkpoint_mismatch")
        names[int(match[1])] = path.name
    return names


def _storage_check(grant, source_id, received, clock):
    authorize(grant, source_id, received)
    authorize(grant, source_id, operation_time(clock))


def _restore(root, name, content, identity, fingerprint, grant, clock):
    try:
        envelope = strict_json(content)
        if (
            name != f"chunk-{identity['index']:05d}-{_digest(content)}.raw.json"
            or canonical_json(envelope) != content
            or set(envelope)
            != {
                "schema",
                "fingerprint",
                "request",
                "raw",
                "raw_sha256",
                "first_received_at",
                "provenance",
            }
            or envelope["schema"] != "dragon-radar-supplier-archive/v1"
            or envelope["fingerprint"] != fingerprint
            or envelope["request"] != identity
            or envelope["provenance"] != "retrospective"
        ):
            raise ValueError
        raw = base64.b64decode(envelope["raw"], validate=True)
        if (
            not 0 < len(raw) <= 4 * 1024 * 1024
            or _digest(raw) != envelope["raw_sha256"]
        ):
            raise ValueError
        safe_payload(strict_json(raw))
        received = datetime.fromisoformat(envelope["first_received_at"])
        now = operation_time(clock)
        if received.utcoffset() is None or received > now:
            raise ValueError
        authorize(grant, identity["source_id"], received)
        authorize(grant, identity["source_id"], now)
        return raw, received
    except SupplierError as error:
        if error.code == "storage_denied":
            raise
        raise SupplierError("checkpoint_mismatch") from None
    except Exception:
        raise SupplierError("checkpoint_mismatch") from None


def _payload(request, index):
    session_parts = (len(request.sessions) + request.max_sessions - 1) // (
        request.max_sessions
    )
    code_part, session_part = divmod(index, session_parts)
    codes = code_part * request.max_codes
    sessions = session_part * request.max_sessions
    return strict_json(request.payload_json.encode("utf-8")) | {
        request.codes_field: list(
            request.securities[codes : codes + request.max_codes]
        ),
        request.sessions_field: [
            day.isoformat()
            for day in request.sessions[sessions : sessions + request.max_sessions]
        ],
    }


def _marker(fingerprint, archived, raw, received, name):
    return canonical_json(
        {
            "schema": "dragon-radar-supplier-checkpoint/v1",
            "fingerprint": fingerprint,
            "archive_sha256": _digest(archived),
            "raw_sha256": _digest(raw),
            "first_received_at": received.isoformat(),
            "raw_path": name,
        }
    )


def _preflight(
    root,
    request,
    identity,
    names,
    grant,
    clock,
    *,
    retain=False,
    max_total_bytes=64 * 1024 * 1024,
):
    """Verify ALL existing chunks before a missing earlier chunk can send."""
    count = (
        (len(request.securities) + request.max_codes - 1)
        // request.max_codes
        * ((len(request.sessions) + request.max_sessions - 1) // request.max_sessions)
    )
    markers = {}
    for path in root.glob("chunk-*"):
        if path.name in names.values():
            continue
        match = re.fullmatch(r"chunk-(\d{5})\.done\.json", path.name)
        if not match or len(markers) >= 10000:
            raise SupplierError("checkpoint_mismatch")
        markers[int(match[1])] = path.name
    indices = set(names) | set(markers)
    if any(index >= count or index not in names for index in indices):
        raise SupplierError("checkpoint_mismatch")
    verified, total = [], 0
    for index in sorted(indices):
        authorize(grant, request.source_id, operation_time(clock))
        chunk_identity = identity | {
            "index": index,
            "payload_sha256": _digest(safe_payload(_payload(request, index))),
        }
        fingerprint = _digest(canonical_json(chunk_identity))
        name = names[index]
        archived = _read_optional(root, name)
        if archived is None:
            raise SupplierError("checkpoint_mismatch")
        if retain:
            total += len(archived)
            if total > max_total_bytes:
                raise SupplierError("archive_budget_exceeded")
        raw, received = _restore(
            root, name, archived, chunk_identity, fingerprint, grant, clock
        )
        if index in markers:
            marker = _read_optional(root, markers[index])
            if retain and marker is not None:
                total += len(marker)
                if total > max_total_bytes:
                    raise SupplierError("archive_budget_exceeded")
            if marker != _marker(fingerprint, archived, raw, received, name):
                raise SupplierError("checkpoint_mismatch")
        if retain:
            verified.append(
                VerifiedChunk(
                    CollectedChunk(
                        index,
                        fingerprint,
                        name,
                        _digest(raw),
                        _digest(archived),
                        received,
                    ),
                    raw,
                )
            )
    return tuple(verified)


def _plan_identity(request, token=None):
    fingerprint = _digest(safe_payload(request.model_dump(mode="json"), token))
    return fingerprint, {
        "source_id": request.source_id,
        "source_version": request.source_version,
        "profile_id": request.profile_id,
        "profile_sha256": request.profile_sha256,
        "endpoint": request.endpoint,
        "plan_sha256": fingerprint,
    }


def read_collection(
    request: CollectionRequest,
    *,
    grant: SourceGrant,
    archive_root: Path,
    clock: Callable[[], datetime] = utc_now,
    max_total_bytes: int = 64 * 1024 * 1024,
) -> VerifiedCollection:
    """Read a complete bounded collection without transport, token or writes.

    Fully archived raw chunks do not require repaired done markers. Partial
    collections never return partial payloads. Retain the same bytes that were
    verified, rather than reopening paths after validating their identities.
    """
    try:
        request = CollectionRequest.model_validate(request.model_dump())
        if (
            type(max_total_bytes) is not int
            or not 1 <= max_total_bytes <= 64 * 1024 * 1024
        ):
            raise SupplierError("invalid_limits")
        root = Path(archive_root)
        if not root.is_dir():
            raise SupplierError("archive_error")
        authorize(grant, request.source_id, operation_time(clock))
        _read_optional(root, "chunk-00000.done.json")
        fingerprint, identity = _plan_identity(request)
        chunks = _preflight(
            root,
            request,
            identity,
            _archive_names(root),
            grant,
            clock,
            retain=True,
            max_total_bytes=max_total_bytes,
        )
        count = (
            (len(request.securities) + request.max_codes - 1)
            // request.max_codes
            * (
                (len(request.sessions) + request.max_sessions - 1)
                // request.max_sessions
            )
        )
        if len(chunks) != count:
            raise SupplierError("collection_incomplete")
        authorize(grant, request.source_id, operation_time(clock))
        return VerifiedCollection(fingerprint, chunks)
    except SupplierError:
        raise
    except Exception:
        raise SupplierError("invalid_request") from None


def collect(
    request: CollectionRequest,
    *,
    grant: SourceGrant,
    archive_root: Path,
    transport: IfindTransport | None = None,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
    retry_delay: float = 1.0,
    request_interval: float = 0.0,
) -> CollectionResult:
    """Collect or resume. Grant is required even for protected reads.

    send injection lives on IfindTransport; clock/sleep bound every retry here.
    The directory must already exist with private permissions. Never creates
    grants, normalized records, DB rows, or historical availability assertions.
    """
    try:
        request = CollectionRequest.model_validate(request.model_dump())
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
            or type(retry_delay) not in (int, float)
            or not 0 <= retry_delay <= 60
            or type(request_interval) not in (int, float)
            or not 0 <= request_interval <= 60
        ):
            raise SupplierError("invalid_limits")
        root = Path(archive_root)
        if not root.is_dir():
            raise SupplierError("archive_error")
        client = transport or IfindTransport(request.endpoint)
        if (
            not isinstance(client, IfindTransport)
            or client.endpoint != request.endpoint
        ):
            raise SupplierError("invalid_endpoint")
        authorize(grant, request.source_id, operation_time(clock))
        _read_optional(root, "chunk-00000.done.json")
        names = _archive_names(root)
        # Never archive the base configuration: only its identity and chunk hash.
        plan_fingerprint, identity = _plan_identity(
            request, os.environ.get(client.token_env)
        )
        _preflight(root, request, identity, names, grant, clock)
        chunks, sent = [], False
        for _ in range(0, len(request.securities), request.max_codes):
            for _ in range(0, len(request.sessions), request.max_sessions):
                index = len(chunks)
                payload = _payload(request, index)
                chunk_identity = identity | {
                    "index": index,
                    "payload_sha256": _digest(safe_payload(payload)),
                }
                fingerprint = _digest(canonical_json(chunk_identity))
                name = names.get(index)
                done_name = f"chunk-{index:05d}.done.json"
                authorize(grant, request.source_id, operation_time(clock))
                archived = _read_optional(root, name) if name else None
                marker = _read_optional(root, done_name)
                if archived is None and marker is not None:
                    raise SupplierError("checkpoint_mismatch")
                if archived is not None:
                    raw, received = _restore(
                        root, name, archived, chunk_identity, fingerprint, grant, clock
                    )
                else:
                    for attempt in range(max_attempts):
                        if sent and (attempt == 0 or request_interval):
                            sleep(request_interval)
                        sent = True
                        try:
                            response = client.post(
                                payload,
                                grant=grant,
                                source_id=request.source_id,
                                clock=clock,
                            )
                            break
                        except SupplierError as error:
                            if not error.retryable or attempt == max_attempts - 1:
                                raise
                            sleep(retry_delay)
                    raw, received = response.raw, response.first_received_at
                    archived = canonical_json(
                        {
                            "schema": "dragon-radar-supplier-archive/v1",
                            "fingerprint": fingerprint,
                            "request": chunk_identity,
                            "raw": base64.b64encode(raw).decode("ascii"),
                            "raw_sha256": _digest(raw),
                            "first_received_at": received.isoformat(),
                            "provenance": "retrospective",
                        }
                    )
                    authorize(grant, request.source_id, received)
                    name = f"chunk-{index:05d}-{_digest(archived)}.raw.json"

                    _write_once(
                        root,
                        name,
                        archived,
                        before_publish=partial(
                            _storage_check, grant, request.source_id, received, clock
                        ),
                    )
                expected = _marker(fingerprint, archived, raw, received, name)
                if marker is not None and marker != expected:
                    raise SupplierError("checkpoint_mismatch")
                if marker is None:
                    _write_once(
                        root,
                        done_name,
                        expected,
                        before_publish=partial(
                            _storage_check, grant, request.source_id, received, clock
                        ),
                    )
                chunks.append(
                    CollectedChunk(
                        index,
                        fingerprint,
                        name,
                        _digest(raw),
                        _digest(archived),
                        received,
                    )
                )
        # A different partition must not silently leave unexamined prior chunks.
        if any(index >= len(chunks) for index in names):
            raise SupplierError("checkpoint_mismatch")
        return CollectionResult(plan_fingerprint, tuple(chunks))
    except SupplierError:
        raise
    except Exception:
        raise SupplierError("invalid_request") from None
