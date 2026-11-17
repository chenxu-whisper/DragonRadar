"""Explicit offline row extraction from complete verified supplier archives."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, field_validator, model_validator

from dragon_radar.domain.models import FrozenModel, SourceGrant
from dragon_radar.ingestion.supplier_collection import (
    CollectedChunk,
    CollectionRequest,
    read_collection,
)
from dragon_radar.ingestion.supplier_mapping import (
    MappingProfile,
    NormalizationIssue,
    NormalizationResult,
    normalize_records,
)
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.production.trust import strict_json

_MAX_ROWS = 100000
_ROW_DATES = {
    "trade_date",
    "limit_date",
    "effective_from",
    "effective_to",
    "listing_date",
}


class RowExtraction(FrozenModel):
    """Exact object keys and array indices, never wildcards or inferred tables."""

    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    bar_path: tuple[str | int, ...] | None = None
    state_path: tuple[str | int, ...] | None = None

    @field_validator("bar_path", "state_path")
    @classmethod
    def explicit_path(cls, value):
        if value is not None and (
            not 1 <= len(value) <= 16
            or any(
                not (
                    type(part) is int
                    and 0 <= part < _MAX_ROWS
                    or type(part) is str
                    and 0 < len(part) <= 128
                    and "*" not in part
                )
                for part in value
            )
        ):
            raise ValueError("invalid_extraction")
        return value

    @model_validator(mode="after")
    def at_least_one(self):
        if self.bar_path is None and self.state_path is None:
            raise ValueError("invalid_extraction")
        return self


@dataclass(frozen=True)
class CollectionNormalization:
    collection_fingerprint: str
    profile_sha256: str
    chunks: tuple[CollectedChunk, ...]
    normalization: NormalizationResult
    provenance: Literal["retrospective"] = "retrospective"
    historical_pit_status: Literal["unverified"] = "unverified"
    production_approved: Literal[False] = False


def _rows(value, path):
    if path is None:
        return []
    try:
        for part in path:
            if type(part) is str and isinstance(value, dict):
                value = value[part]
            elif type(part) is int and isinstance(value, list):
                value = value[part]
            else:
                raise ValueError
        if (
            not isinstance(value, list)
            or len(value) > _MAX_ROWS
            or any(not isinstance(row, dict) for row in value)
        ):
            raise ValueError
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise SupplierError("extraction_failed") from None


def normalize_collection(
    request: CollectionRequest,
    *,
    grant: SourceGrant,
    archive_root: Path,
    mapping_json: bytes,
    extraction: RowExtraction,
    clock: Callable[[], datetime] = utc_now,
) -> CollectionNormalization:
    """Bind exact mapping bytes and inject archived receipt time into every row.

    Retain all mapping issues; this structural result still needs sample/quality
    checks and an independently justified pagination completeness declaration.
    No raw rows or vendor field names are included in returned diagnostics.
    """
    try:
        request = CollectionRequest.model_validate(request.model_dump())
        extraction = RowExtraction.model_validate(extraction.model_dump())
        if (
            type(mapping_json) is not bytes
            or not 0 < len(mapping_json) <= 4 * 1024 * 1024
        ):
            raise SupplierError("invalid_extraction")
        profile = MappingProfile.model_validate(strict_json(mapping_json))
        profile_hash = sha256(mapping_json).hexdigest()
        if (
            request.profile_id,
            request.source_id,
            request.source_version,
            request.profile_sha256,
        ) != (
            profile.profile_id,
            profile.source_id,
            profile.source_version,
            profile_hash,
        ):
            raise SupplierError("mapping_binding_mismatch")
        if any(
            "available_at" in getattr(profile, f"{kind}_{slot}")
            for kind in ("bar", "state")
            for slot in ("fields", "constants")
        ) or any(
            _ROW_DATES.intersection(getattr(profile, f"{kind}_constants"))
            for kind in ("bar", "state")
        ):
            raise SupplierError("invalid_extraction")
        verified = read_collection(
            request, grant=grant, archive_root=archive_root, clock=clock
        )
        bars, states, issues = [], [], []
        offsets = {"bar": 0, "state": 0}
        for chunk in verified.chunks:
            authorize(grant, request.source_id, operation_time(clock))
            value = strict_json(chunk.raw)
            rows = {
                "bar": _rows(value, extraction.bar_path),
                "state": _rows(value, extraction.state_path),
            }
            if sum(offsets.values()) + sum(map(len, rows.values())) > _MAX_ROWS:
                raise SupplierError("extraction_failed")
            data = profile.model_dump(mode="python", by_alias=True)
            for kind in ("bar", "state"):
                data[f"{kind}_constants"]["available_at"] = (
                    chunk.metadata.first_received_at
                )
            received_profile = MappingProfile.model_validate(data)
            result = normalize_records(
                received_profile, bar_rows=rows["bar"], state_rows=rows["state"]
            )
            bars.extend(result.bars)
            states.extend(result.states)
            issues.extend(
                NormalizationIssue(
                    kind=issue.kind,
                    row_index=offsets[issue.kind] + issue.row_index,
                    field=issue.field,
                    code=issue.code,
                )
                for issue in result.issues
            )
            for kind in offsets:
                offsets[kind] += len(rows[kind])
        authorize(grant, request.source_id, operation_time(clock))
        return CollectionNormalization(
            verified.fingerprint,
            profile_hash,
            tuple(chunk.metadata for chunk in verified.chunks),
            NormalizationResult(
                bars=tuple(bars), states=tuple(states), issues=tuple(issues)
            ),
        )
    except SupplierError:
        raise
    except Exception:
        raise SupplierError("invalid_extraction") from None
