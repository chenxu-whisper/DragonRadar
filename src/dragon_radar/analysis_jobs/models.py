"""Immutable contracts for versioned custom analysis jobs."""

import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.domain.models import FrozenModel
from dragon_radar.owner.models import owner_principals  # noqa: F401
from dragon_radar.publication.tables import publications  # noqa: F401
from dragon_radar.storage.models import metadata
from dragon_radar.topics.models import ImmutableJsonObject

ANALYSIS_ALGORITHM_VERSION = "analysis-custom-v1-decimal34"
Adjustment = Literal["raw", "adjusted", "qfq", "hfq"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
SafeIdentifier = Annotated[
    StrictStr,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


class _StrictFrozenModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class AnalysisJobCommand(_StrictFrozenModel):
    """Canonical request whose complete identity participates in cache reuse."""

    snapshot_id: UUID
    start: date
    end: date
    benchmark: SafeIdentifier = "B000300"
    adjustment: Adjustment = "raw"
    symbols: Annotated[tuple[SafeIdentifier, ...], Field(max_length=5)]
    algorithm_version: Literal["analysis-custom-v1-decimal34"] = (
        ANALYSIS_ALGORITHM_VERSION
    )

    @field_validator("symbols")
    @classmethod
    def unique_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("symbols must be unique")
        return value

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.start > self.end:
            raise ValueError("start must not be after end")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("ascii")).hexdigest()


class AnalysisJobRecord(_StrictFrozenModel):
    id: UUID
    owner_id: UUID
    command: AnalysisJobCommand
    status: JobStatus
    attempt_count: Annotated[int, Field(ge=0)]
    result: ImmutableJsonObject | None = None
    failure: ImmutableJsonObject | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AnalysisJobLease(_StrictFrozenModel):
    job: AnalysisJobRecord
    token: UUID
    worker_id: SafeIdentifier
    expires_at: datetime


analysis_job_cache = Table(
    "analysis_job_cache",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("cache_key", String(64), nullable=False, unique=True),
    Column("algorithm_version", String(100), nullable=False),
    Column("input_manifest", JSONB, nullable=False),
    Column("result", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

analysis_jobs = Table(
    "analysis_jobs",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("cache_key", String(64), nullable=False),
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("input_manifest", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", PGUUID(as_uuid=True)),
    Column("lease_owner", String(100)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("result", JSONB(none_as_null=True)),
    Column("failure", JSONB(none_as_null=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint("owner_id", "idempotency_key"),
    CheckConstraint("attempt_count >= 0"),
    CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')"),
    CheckConstraint(
        "(status = 'queued' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NULL AND failure IS NULL AND completed_at IS NULL) OR "
        "(status = 'running' AND lease_token IS NOT NULL "
        "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND result IS NULL AND failure IS NULL AND completed_at IS NULL) OR "
        "(status = 'succeeded' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NOT NULL AND failure IS NULL AND completed_at IS NOT NULL) OR "
        "(status = 'failed' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NULL AND failure IS NOT NULL AND completed_at IS NOT NULL)",
        name="analysis_jobs_state",
    ),
)

Index(
    "analysis_jobs_claim",
    analysis_jobs.c.available_at,
    analysis_jobs.c.created_at,
    postgresql_where=analysis_jobs.c.status.in_(("queued", "running")),
)


__all__ = [
    "ANALYSIS_ALGORITHM_VERSION",
    "AnalysisJobCommand",
    "AnalysisJobLease",
    "AnalysisJobRecord",
    "analysis_job_cache",
    "analysis_jobs",
]
