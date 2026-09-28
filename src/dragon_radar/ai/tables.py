"""SQLAlchemy mappings for owner-scoped AI tasks and append-only history."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.owner.models import owner_principals  # noqa: F401
from dragon_radar.publication.tables import publications  # noqa: F401
from dragon_radar.storage.models import metadata

ai_tasks = Table(
    "ai_tasks",
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
    Column("task_type", String(30), nullable=False),
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("publications.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("version", String(100), nullable=False),
    Column("input_manifest", JSONB, nullable=False),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("verification_status", String(20), nullable=False),
    Column("human_review_status", String(20), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", PGUUID(as_uuid=True)),
    Column("lease_owner", String(100)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("selected_revision", Integer),
    Column("model_version", String(100)),
    Column("prompt_version", String(100)),
    Column("provider", String(100)),
    Column("token_usage", BigInteger),
    Column("cost", Numeric(18, 6)),
    Column("failure", JSONB(none_as_null=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint("owner_id", "idempotency_key"),
    CheckConstraint(
        "task_type IN ('summarize', 'topic_suggest', 'explain_stock', 'daily_review')"
    ),
    CheckConstraint(
        "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')"
    ),
    CheckConstraint(
        "verification_status IN ('pending', 'passed', 'failed', 'needs_human')"
    ),
    CheckConstraint(
        "human_review_status IN ('unreviewed', 'accepted', 'edited', 'rejected')"
    ),
    CheckConstraint("attempt_count >= 0 AND attempt_count <= 10"),
    CheckConstraint("selected_revision IS NULL OR selected_revision > 0"),
    CheckConstraint(
        "token_usage IS NULL OR (token_usage >= 0 AND token_usage <= 10000000)"
    ),
    CheckConstraint("cost IS NULL OR (cost >= 0 AND cost <= 1000000)"),
    CheckConstraint(
        "(status = 'pending' AND lease_token IS NULL AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND completed_at IS NULL) OR "
        "(status = 'running' AND lease_token IS NOT NULL AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND completed_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'cancelled') "
        "AND lease_token IS NULL AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND completed_at IS NOT NULL)",
        name="ai_tasks_state",
    ),
)

Index(
    "ai_tasks_claim",
    ai_tasks.c.available_at,
    ai_tasks.c.created_at,
    postgresql_where=ai_tasks.c.status.in_(("pending", "running")),
)

ai_task_drafts = Table(
    "ai_task_drafts",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "task_id",
        PGUUID(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("source", String(30), nullable=False),
    Column("content", JSONB, nullable=False),
    Column("model_version", String(100)),
    Column("prompt_version", String(100)),
    Column("provider", String(100)),
    Column("token_usage", BigInteger),
    Column("cost", Numeric(18, 6)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("task_id", "revision"),
    CheckConstraint("revision > 0"),
    CheckConstraint("source IN ('deterministic_template', 'provider', 'human_edit')"),
    CheckConstraint(
        "(source = 'provider' AND model_version IS NOT NULL "
        "AND prompt_version IS NOT NULL AND provider IS NOT NULL "
        "AND token_usage IS NOT NULL AND cost IS NOT NULL) OR "
        "(source <> 'provider' AND model_version IS NULL "
        "AND prompt_version IS NULL AND provider IS NULL "
        "AND token_usage IS NULL AND cost IS NULL)",
        name="ai_task_drafts_source_metadata",
    ),
)

ai_task_cancellations = Table(
    "ai_task_cancellations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "task_id",
        PGUUID(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("accepted_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "idempotency_key"),
)

ai_task_reviews = Table(
    "ai_task_reviews",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "task_id",
        PGUUID(as_uuid=True),
        ForeignKey("ai_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("decision", String(20), nullable=False),
    Column("expected_revision", Integer, nullable=False),
    Column("resulting_revision", Integer, nullable=False),
    Column(
        "draft_id",
        PGUUID(as_uuid=True),
        ForeignKey("ai_task_drafts.id", ondelete="RESTRICT"),
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "idempotency_key"),
    CheckConstraint("decision IN ('accept', 'edit', 'reject')"),
    CheckConstraint("expected_revision > 0 AND resulting_revision > 0"),
    CheckConstraint(
        "(decision = 'edit' AND draft_id IS NOT NULL "
        "AND resulting_revision > expected_revision) OR "
        "(decision <> 'edit' AND draft_id IS NULL "
        "AND resulting_revision = expected_revision)",
        name="ai_task_reviews_revision",
    ),
    Index("ai_task_reviews_task", "task_id", "created_at"),
)

__all__ = [
    "ai_task_cancellations",
    "ai_task_drafts",
    "ai_task_reviews",
    "ai_tasks",
]
