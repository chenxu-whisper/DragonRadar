"""Reusable offline verified collection -> exact standard sample service."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion.sample_acceptance import (
    SampleRequest,
    evaluate_sample,
    safe_failure,
)
from dragon_radar.ingestion.sample_archive import (
    SampleArchive,
    SampleChunk,
    SampleLineage,
)
from dragon_radar.ingestion.supplier_archive_mapping import (
    RowExtraction,
    normalize_collection,
)
from dragon_radar.ingestion.supplier_collection import CollectionRequest
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.production.trust import EvidenceError, strict_json

INPUTS = ("request", "grant", "mapping", "extraction", "sample")
MAX_INPUT = 4 * 1024 * 1024


class ArchiveSampleError(ValueError):
    """Only fixed internal codes may reach safe reports."""


def failure(code):
    report = safe_failure(code)
    report["provenance"] = "retrospective"
    if code == "storage_denied":
        report["status"] = "blocked"
        report["issues"][0]["status"] = "blocked"
    return report


@dataclass(frozen=True)
class ArchiveSampleResult:
    report: dict
    archive: SampleArchive | None = field(default=None, repr=False)


def _bind(sample, request, grant):
    batch = sample.batch
    if batch.bars or batch.security_states or batch.limit_up_events:
        raise ArchiveSampleError("sample_records_not_empty")
    if (
        (batch.source_id, batch.source_version)
        != (request.source_id, request.source_version)
        or batch.grant != grant
        or batch.calendar != request.calendar
        or batch.calendar_version != request.calendar.version
        or batch.trade_date != request.sessions[-1]
        or batch.provenance != "retrospective"
    ):
        raise ArchiveSampleError("sample_binding_mismatch")


def evaluate_archive(
    raw: Mapping[str, bytes],
    *,
    archive_root: Path,
    clock: Callable[[], datetime] = utc_now,
) -> ArchiveSampleResult:
    """No raw responses or provider identifiers escape in the report."""
    try:
        if set(raw) != set(INPUTS) or any(
            type(item) is not bytes or not 0 < len(item) <= MAX_INPUT
            for item in raw.values()
        ):
            raise ArchiveSampleError("invalid_configuration")
        for content in raw.values():
            strict_json(content)
        request = CollectionRequest.model_validate_json(raw["request"])
        grant = SourceGrant.model_validate_json(raw["grant"])
        extraction = RowExtraction.model_validate_json(raw["extraction"])
        sample = SampleRequest.model_validate_json(raw["sample"])
        _bind(sample, request, grant)
        mapped = normalize_collection(
            request,
            grant=grant,
            archive_root=archive_root.absolute(),
            mapping_json=raw["mapping"],
            extraction=extraction,
            clock=clock,
        )
        digests = {
            "request_sha256": sha256(raw["request"]).hexdigest(),
            "grant_sha256": sha256(raw["grant"]).hexdigest(),
            "profile_sha256": mapped.profile_sha256,
            "sample_file_sha256": sha256(raw["sample"]).hexdigest(),
            "extraction_sha256": sha256(raw["extraction"]).hexdigest(),
            "collection_fingerprint": mapped.collection_fingerprint,
        }
        metadata = digests | {
            "chunks": len(mapped.chunks),
            "provenance": mapped.provenance,
            "kind": sample.kind,
            "test_only": sample.kind == "test",
        }
        normalized = mapped.normalization
        if normalized.issues:
            report = failure("normalization_failed")
            report["normalization_issues"] = [
                issue.model_dump(mode="json") for issue in normalized.issues
            ]
            return ArchiveSampleResult(report | metadata)
        if any(chunk.first_received_at > sample.batch.as_of for chunk in mapped.chunks):
            return ArchiveSampleResult(failure("receipt_after_as_of") | metadata)
        if any(row.trade_date not in request.sessions for row in normalized.bars):
            return ArchiveSampleResult(failure("row_session_mismatch") | metadata)
        payload = sample.model_dump(mode="python", by_alias=True)
        payload["batch"]["bars"] = normalized.bars
        payload["batch"]["security_states"] = normalized.states
        verified_sample = SampleRequest.model_validate(payload)
        report = evaluate_sample(verified_sample, now=operation_time(clock)) | metadata
        if report["status"] != "pass":
            return ArchiveSampleResult(report)
        package = SampleArchive(
            schema="dragon-radar-normalized-sample-archive/v1",
            sample=verified_sample,
            lineage=SampleLineage(
                **digests,
                chunks=tuple(
                    SampleChunk(
                        index=chunk.index,
                        fingerprint=chunk.fingerprint,
                        raw_sha256=chunk.raw_sha256,
                        archive_sha256=chunk.archive_sha256,
                        first_received_at=chunk.first_received_at,
                    )
                    for chunk in mapped.chunks
                ),
            ),
            production_approved=False,
            historical_pit_status="unverified",
        )
        authorize(grant, request.source_id, operation_time(clock))
        return ArchiveSampleResult(report, package)
    except ArchiveSampleError as error:
        return ArchiveSampleResult(failure(str(error)))
    except SupplierError as error:
        return ArchiveSampleResult(failure(error.code))
    except (
        ValidationError,
        EvidenceError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        return ArchiveSampleResult(failure("invalid_configuration"))
