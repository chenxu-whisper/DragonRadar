"""Private engineering archives; integrity is not production or historical trust."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from dragon_radar.domain.models import FrozenModel, SourceGrant, UTCDateTime
from dragon_radar.ingestion.sample_acceptance import SampleRequest, evaluate_sample
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import (
    EvidenceError,
    canonical_json,
    read_asset,
    strict_json,
)

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


class SampleArchiveError(ValueError):
    """Constant code only; never disclose private inputs or filesystem errors."""


class SampleChunk(FrozenModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    index: int = Field(ge=0)
    fingerprint: Digest
    raw_sha256: Digest
    archive_sha256: Digest
    first_received_at: UTCDateTime


class SampleLineage(FrozenModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    request_sha256: Digest
    grant_sha256: Digest
    profile_sha256: Digest
    sample_file_sha256: Digest
    extraction_sha256: Digest
    collection_fingerprint: Digest
    chunks: tuple[SampleChunk, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def consecutive_chunks(self):
        if tuple(chunk.index for chunk in self.chunks) != tuple(
            range(len(self.chunks))
        ):
            raise ValueError("chunk_sequence")
        return self


class SampleArchive(FrozenModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    schema_version: Literal["dragon-radar-normalized-sample-archive/v1"] = Field(
        alias="schema"
    )
    sample: SampleRequest = Field(repr=False)
    lineage: SampleLineage
    production_approved: Literal[False]
    historical_pit_status: Literal["unverified"]

    @model_validator(mode="after")
    def bind_receipts(self):
        batch = self.sample.batch
        receipts = {chunk.first_received_at for chunk in self.lineage.chunks}
        if (
            batch.provenance != "retrospective"
            or self.production_approved is not False
            or any(receipt > batch.as_of for receipt in receipts)
            or any(
                row.available_at not in receipts
                for row in (*batch.bars, *batch.security_states, *batch.limit_up_events)
            )
        ):
            raise ValueError("sample_receipt_binding")
        return self


_INVALID = (
    ValidationError,
    EvidenceError,
    OSError,
    TypeError,
    ValueError,
    RecursionError,
    OverflowError,
)


def _quality(package: SampleArchive, now: datetime) -> dict:
    authorize(package.sample.batch.grant, package.sample.batch.source_id, now)
    return evaluate_sample(package.sample, now=now)


def _check(package: SampleArchive, now: datetime) -> None:
    if _quality(package, now)["status"] != "pass":
        raise SampleArchiveError("sample_not_qualified")


def archive_bytes(
    package: SampleArchive, *, clock: Callable[[], datetime] = utc_now
) -> bytes:
    """Revalidate even unchecked model copies before serializing full private data."""
    try:
        raw = canonical_json(package.model_dump(mode="json", by_alias=True))
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise SampleArchiveError("sample_archive_budget")
        checked = SampleArchive.model_validate_json(raw)
        _check(checked, operation_time(clock))
        authorize(
            checked.sample.batch.grant,
            checked.sample.batch.source_id,
            operation_time(clock),
        )
        return raw
    except SupplierError as error:
        raise SampleArchiveError(error.code) from None
    except SampleArchiveError:
        raise
    except _INVALID:
        raise SampleArchiveError("invalid_sample_archive") from None


def write_sample_archive(
    path: Path, package: SampleArchive, *, clock: Callable[[], datetime] = utc_now
) -> str:
    """Exclusive private output with rollback on validation/write/fsync failure."""
    try:

        def reauthorize():
            authorize(
                package.sample.batch.grant,
                package.sample.batch.source_id,
                operation_time(clock),
            )

        with secure_output(path, on_durable=reauthorize) as stream:
            raw = archive_bytes(package, clock=clock)
            stream.write(raw)
            authorize(
                package.sample.batch.grant,
                package.sample.batch.source_id,
                operation_time(clock),
            )
        return sha256(raw).hexdigest()
    except SampleArchiveError:
        raise
    except SupplierError as error:
        raise SampleArchiveError(error.code) from None
    except _INVALID:
        raise SampleArchiveError("sample_output_unavailable") from None


@dataclass(frozen=True)
class SampleVerification:
    report: dict
    archive: SampleArchive | None = field(default=None, repr=False)


def verify_sample_archive(
    path: Path,
    *,
    expected_sha256: str,
    grant: SourceGrant,
    clock: Callable[[], datetime] = utc_now,
) -> SampleVerification:
    """Verify once; unqualified samples expose only safe quality diagnostics."""
    try:
        current = SourceGrant.model_validate(grant.model_dump())
        now = operation_time(clock)
        authorize(current, current.source_id, now)
        path = Path(path).absolute()
        raw = read_asset(path.parent, path.name, MAX_ARCHIVE_BYTES)
        value = strict_json(raw)
        if (
            type(expected_sha256) is not str
            or sha256(raw).hexdigest() != expected_sha256
            or canonical_json(value) != raw
        ):
            raise SampleArchiveError("sample_archive_integrity")
        package = SampleArchive.model_validate_json(raw)
        authorize(current, package.sample.batch.source_id, now)
        if package.sample.batch.grant != current:
            raise SampleArchiveError("sample_grant_mismatch")
        report = _quality(package, now) | {
            "sample_archive_sha256": expected_sha256,
            "provenance": "retrospective",
            "chunks": len(package.lineage.chunks),
            **package.lineage.model_dump(mode="json", exclude={"chunks"}),
        }
        authorize(current, package.sample.batch.source_id, operation_time(clock))
        return SampleVerification(
            report, package if report["status"] == "pass" else None
        )
    except SampleArchiveError:
        raise
    except SupplierError as error:
        raise SampleArchiveError(error.code) from None
    except _INVALID:
        raise SampleArchiveError("invalid_sample_archive") from None


def read_sample_archive(
    path: Path,
    *,
    expected_sha256: str,
    grant: SourceGrant,
    clock: Callable[[], datetime] = utc_now,
) -> SampleArchive:
    """Read by externally retained digest, and return only qualified private data."""
    result = verify_sample_archive(
        path, expected_sha256=expected_sha256, grant=grant, clock=clock
    )
    if result.archive is None:
        raise SampleArchiveError("sample_not_qualified")
    return result.archive
