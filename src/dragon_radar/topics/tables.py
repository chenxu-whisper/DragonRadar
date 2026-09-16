"""SQLAlchemy mappings for topic import attempts, archives, and snapshots."""

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata

topic_import_batches = Table(
    "topic_import_batches",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("trade_date", Date, nullable=False),
    Column("canonical_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("quality_report", JSONB, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
)

topic_snapshots = Table(
    "topic_snapshots",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        PGUUID(as_uuid=True),
        ForeignKey("topic_import_batches.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("trade_date", Date, nullable=False),
    Column("revision", Integer, nullable=False),
    Column(
        "supersedes",
        PGUUID(as_uuid=True),
        ForeignKey("topic_snapshots.id", ondelete="RESTRICT"),
    ),
    Column("manifest", JSONB, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "trade_date", "revision"),
)

topic_import_attempts = Table(
    "topic_import_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        PGUUID(as_uuid=True),
        ForeignKey("topic_import_batches.id", ondelete="RESTRICT"),
    ),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("payload_hash", String(64), nullable=False),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("embedded_grant", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("stage", String(80), nullable=False),
    Column("missing_items", JSONB, nullable=False),
    Column("failed_cursor", Text),
    Column("retry_from", Date),
    Column("retry_to", Date),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
