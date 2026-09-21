"""SQLAlchemy mappings for unified publications and retention controls."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata

PUBLICATION_SEQUENCE_LOCK = "publication-sequence"

publications = Table(
    "publications",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "publication_sequence",
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    ),
    Column("trade_date", Date, nullable=False),
    Column("version", Integer, nullable=False),
    Column(
        "supersedes",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
    ),
    Column("status", String(20), nullable=False),
    Column(
        "market_snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "topic_snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("topic_snapshots.id", ondelete="RESTRICT"),
    ),
    Column("news_input_id", PGUUID(as_uuid=True)),
    Column("regulation_input_id", PGUUID(as_uuid=True)),
    Column("role_input_id", PGUUID(as_uuid=True)),
    Column("domains", JSONB, nullable=False),
    Column("coverage", JSONB, nullable=False),
    Column("quality_flags", JSONB, nullable=False),
    Column("command_json", Text, nullable=False),
    Column("command_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    UniqueConstraint("trade_date", "version"),
    CheckConstraint("version > 0"),
    CheckConstraint("status IN ('draft', 'published', 'revoked')"),
    CheckConstraint(
        "(status = 'draft' AND published_at IS NULL) "
        "OR (status IN ('published', 'revoked') AND published_at IS NOT NULL)",
        name="publications_status_published_at",
    ),
    Index(
        "publications_listing",
        text("trade_date DESC"),
        text("version DESC"),
        text("id ASC"),
    ),
)

publication_results = Table(
    "publication_results",
    metadata,
    Column(
        "publication_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("domain", String(20), primary_key=True),
    Column("input_id", PGUUID(as_uuid=True)),
    Column("status", String(20), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("coverage", JSONB, nullable=False),
    Column("quality_flags", JSONB, nullable=False),
    CheckConstraint("domain IN ('market', 'topic', 'news', 'regulation', 'role')"),
    CheckConstraint("status IN ('ready', 'missing', 'disabled')"),
)

publication_attempts = Table(
    "publication_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False),
    Column("command_hash", String(64), nullable=False),
    Column(
        "publication_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
    ),
    Column("status", String(20), nullable=False),
    Column("stage", String(80), nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('failed', 'published')"),
    CheckConstraint(
        "(status = 'published' AND publication_id IS NOT NULL) "
        "OR (status = 'failed' AND publication_id IS NULL)"
    ),
    Index(
        "publication_attempts_published_key",
        "idempotency_key",
        unique=True,
        postgresql_where=text("status = 'published'"),
    ),
)

source_dependencies = Table(
    "source_dependencies",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "dependency_sequence",
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    ),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("archive_type", String(40), nullable=False),
    Column("archive_id", PGUUID(as_uuid=True), nullable=False),
    Column("derived_type", String(40), nullable=False),
    Column("derived_id", PGUUID(as_uuid=True), nullable=False),
    Column(
        "publication_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "source_id",
        "archive_type",
        "archive_id",
        "derived_type",
        "derived_id",
    ),
    Index("source_dependencies_source", "source_id", "publication_id"),
    Index(
        "source_dependencies_source_sequence",
        "source_id",
        "dependency_sequence",
    ),
)

withdrawal_records = Table(
    "withdrawal_records",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "sequence",
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    ),
    Column("source_id", Text, nullable=False),
    Column("scope", String(20), nullable=False),
    Column("action", String(30), nullable=False),
    Column("policy_reference", Text),
    Column("cutoff_dependency_sequence", BigInteger),
    Column("details", JSONB, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("scope IN ('display', 'storage')"),
    CheckConstraint(
        "action IN ('blocked', 'deidentified', 'deleted', 'replayed', 'restored')"
    ),
    CheckConstraint(
        "(action IN ('deidentified', 'deleted') "
        "AND scope = 'storage' AND policy_reference IS NOT NULL "
        "AND cutoff_dependency_sequence IS NOT NULL) "
        "OR (action IN ('blocked', 'replayed', 'restored') "
        "AND policy_reference IS NULL AND cutoff_dependency_sequence IS NULL)",
        name="withdrawal_records_scope_action_policy",
    ),
    Index("withdrawal_records_source", "source_id", "sequence"),
)

withdrawal_dependencies = Table(
    "withdrawal_dependencies",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "withdrawal_id",
        PGUUID(as_uuid=True),
        ForeignKey("withdrawal_records.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "source_dependency_id",
        PGUUID(as_uuid=True),
        ForeignKey("source_dependencies.id", ondelete="RESTRICT"),
    ),
    Column("archive_type", String(40)),
    Column("archive_id", PGUUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("withdrawal_id", "source_dependency_id"),
    UniqueConstraint("withdrawal_id", "archive_type", "archive_id"),
    CheckConstraint(
        "(source_dependency_id IS NOT NULL "
        "AND archive_type IS NULL AND archive_id IS NULL) "
        "OR (source_dependency_id IS NULL "
        "AND archive_type IS NOT NULL AND archive_id IS NOT NULL)"
    ),
    Index(
        "withdrawal_dependencies_archive",
        "archive_type",
        "archive_id",
        "withdrawal_id",
    ),
    Index(
        "withdrawal_dependencies_source_dependency",
        "source_dependency_id",
        "withdrawal_id",
    ),
)

withdrawal_binding_seals = Table(
    "withdrawal_binding_seals",
    metadata,
    Column(
        "withdrawal_id",
        PGUUID(as_uuid=True),
        ForeignKey("withdrawal_records.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("sealed_at", DateTime(timezone=True), nullable=False),
)

publication_module_controls = Table(
    "publication_module_controls",
    metadata,
    Column("source_id", Text, primary_key=True),
    Column("module", String(40), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("blocked_by_withdrawal", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
