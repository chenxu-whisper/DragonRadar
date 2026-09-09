"""SQL schema mappings and detached immutable service results."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.domain.models import MarketBatch

metadata = MetaData()

source_grants = Table(
    "source_grants",
    metadata,
    Column("source_id", Text, primary_key=True),
    Column("grant", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

import_batches = Table(
    "import_batches",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("source_id", Text, ForeignKey("source_grants.source_id"), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("canonical_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("quality_report", JSONB, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
)

snapshots = Table(
    "snapshots",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        PGUUID(as_uuid=True),
        ForeignKey("import_batches.id"),
        nullable=False,
        unique=True,
    ),
    Column("source_id", Text, ForeignKey("source_grants.source_id"), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("supersedes", PGUUID(as_uuid=True), ForeignKey("snapshots.id")),
    Column("manifest", JSONB, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "trade_date", "revision"),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("action", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("object_id", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class ImportRecord:
    id: UUID
    canonical_json: str
    payload_hash: str
    quality_report: dict


@dataclass(frozen=True)
class Snapshot:
    id: UUID
    batch_id: UUID
    revision: int
    supersedes: UUID | None
    published_at: datetime
    manifest: dict
    batch: MarketBatch
    quality_report: dict

    @property
    def trade_date(self) -> date:
        return self.batch.trade_date
