"""Immutable topic import, snapshot, and repository-facing contracts."""

import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    Field,
    JsonValue,
    PlainSerializer,
    StrictStr,
    field_validator,
    model_validator,
)

from dragon_radar.domain.models import (
    ExchangeCalendar,
    FrozenModel,
    Identifier,
    Provenance,
    SourceGrant,
    UTCDateTime,
)

SafeReference = Annotated[StrictStr, Field(min_length=1, max_length=2000)]
SHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


def _freeze_json(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _serialize_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _serialize_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_json(item) for item in value]
    return value


ImmutableJsonObject = Annotated[
    Mapping[str, JsonValue],
    AfterValidator(_freeze_json),
    PlainSerializer(_serialize_json, return_type=dict[str, JsonValue]),
]


def _validate_reference(value: str) -> str:
    if not value.strip():
        raise ValueError("source references cannot be blank")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("source references cannot contain control characters")
    if re.match(r"(?i)^(javascript|data|file)\s*:", value.strip()):
        raise ValueError("source reference uses a forbidden protocol")
    return value


class ManifestPage(FrozenModel):
    page_id: Identifier
    request_cursor: str | None
    next_cursor: str | None
    page_hash: SHA256
    received_row_count: Annotated[int, Field(ge=0)]


class PageManifest(FrozenModel):
    request_from: date
    request_to: date
    pages: tuple[ManifestPage, ...]
    declared_total: Annotated[int, Field(ge=0)]
    terminal_cursor: str | None


class TopicDefinition(FrozenModel):
    topic_id: Identifier
    name: Identifier
    effective_from: date
    effective_to: date | None = None
    available_at: UTCDateTime
    source_reference: SafeReference

    _safe_source_reference = field_validator("source_reference")(
        _validate_reference
    )

    @model_validator(mode="after")
    def valid_interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("topic definition interval must be nonempty")
        return self


class TopicMembership(FrozenModel):
    topic_id: Identifier
    security_id: Identifier
    effective_from: date
    effective_to: date | None = None
    available_at: UTCDateTime
    evidence_refs: tuple[SafeReference, ...]

    @field_validator("evidence_refs")
    @classmethod
    def safe_evidence_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_reference(value) for value in values)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("topic membership interval must be nonempty")
        return self


class TopicCoverageCell(FrozenModel):
    topic_id: Identifier
    trade_date: date
    expected_member_count: Annotated[int, Field(ge=0)]
    received_member_count: Annotated[int, Field(ge=0)]
    complete_as_of: UTCDateTime
    status: Literal["complete", "unavailable"]
    reason: str | None = None


class TopicBatch(FrozenModel):
    trade_date: date
    as_of: UTCDateTime
    source_time: UTCDateTime | None = None
    provenance: Provenance
    source_id: Identifier
    source_version: Identifier
    grant: SourceGrant
    exchange: Identifier
    calendar: ExchangeCalendar
    calendar_version: Identifier
    taxonomy_version: Identifier
    membership_version: Identifier
    coverage_from: date
    coverage_to: date
    coverage_cells: tuple[TopicCoverageCell, ...]
    topics: tuple[TopicDefinition, ...]
    memberships: tuple[TopicMembership, ...]
    page_manifest: PageManifest

    @model_validator(mode="after")
    def nonoverlapping_memberships(self):
        groups: dict[tuple[str, str], list[TopicMembership]] = {}
        for membership in self.memberships:
            groups.setdefault(
                (membership.topic_id, membership.security_id), []
            ).append(membership)
        for memberships in groups.values():
            ordered = sorted(memberships, key=lambda item: item.effective_from)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if (
                    previous.effective_to is None
                    or current.effective_from < previous.effective_to
                ):
                    raise ValueError("topic membership intervals overlap")
        return self


class TopicImportRecord(FrozenModel):
    id: UUID
    canonical_json: str
    payload_hash: SHA256
    quality_report: ImmutableJsonObject


class TopicSnapshot(FrozenModel):
    id: UUID
    batch_id: UUID
    revision: Annotated[int, Field(ge=1)]
    supersedes: UUID | None
    published_at: UTCDateTime
    manifest: ImmutableJsonObject
    batch: TopicBatch
    quality_report: ImmutableJsonObject

    @property
    def trade_date(self) -> date:
        return self.batch.trade_date


class TopicAttempt(FrozenModel):
    id: UUID
    status: Literal["failed", "succeeded"]
    stage: Identifier
    missing_items: tuple[str, ...] = ()
    failed_cursor: str | None = None
    retry_from: date | None = None
    retry_to: date | None = None
    details: tuple[str, ...] = ()
    created_at: UTCDateTime


class TopicSnapshotPage(FrozenModel):
    items: tuple[TopicSnapshot, ...]
    page_size: int
    next_cursor: str | None
