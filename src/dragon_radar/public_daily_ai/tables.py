"""Private audit links and public immutable artifact registries."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dragon_radar.ai import tables as _ai_tables  # noqa: F401
from dragon_radar.publication import tables as _publication_tables  # noqa: F401
from dragon_radar.storage.models import metadata

ai_output_entitlements = Table(
    "ai_output_entitlements",
    metadata,
    Column("reference", String(200), primary_key=True),
    Column("document", JSONB, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("registered_at", DateTime(timezone=True), nullable=False),
)
ai_output_entitlement_revocations = Table(
    "ai_output_entitlement_revocations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "reference",
        String(200),
        ForeignKey("ai_output_entitlements.reference"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("reason_reference", String(200), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=False),
)


def artifact_table(name):
    return Table(
        name,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column(
            "base_publication_id",
            UUID(as_uuid=True),
            ForeignKey("publications.id"),
            nullable=False,
        ),
        Column("trade_date", Date, nullable=False),
        Column("archive_version", Integer, nullable=False),
        Column("publication_sequence", BigInteger, unique=True, nullable=False),
        Column("supersedes", UUID(as_uuid=True)),
        Column("published_at", DateTime(timezone=True), nullable=False),
        Column("content_hash", String(64), nullable=False),
        Column("safe_payload", JSONB, nullable=False),
        Column("rights_manifest", JSONB, nullable=False),
        Column("attestation_reference", String(200), nullable=False),
        Column("article_revision_id", UUID(as_uuid=True)),
        UniqueConstraint(
            "base_publication_id", "archive_version", "article_revision_id"
        ),
        CheckConstraint("archive_version > 0"),
        Index(name + "_listing", "base_publication_id", "archive_version", "id"),
    )


public_daily_ai_archives = artifact_table("public_daily_ai_archives")
Index(
    "public_daily_ai_pub_version",
    public_daily_ai_archives.c.base_publication_id,
    public_daily_ai_archives.c.archive_version,
    unique=True,
)
promoted_news_summaries = artifact_table("promoted_news_summaries")

ai_artifact_private_links = Table(
    "ai_artifact_private_links",
    metadata,
    Column("artifact_id", UUID(as_uuid=True), primary_key=True),
    Column("kind", String(30), nullable=False),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("owner_principals.id"),
        nullable=False,
    ),
    Column("task_id", UUID(as_uuid=True), ForeignKey("ai_tasks.id"), nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column(
        "review_id",
        UUID(as_uuid=True),
        ForeignKey("ai_task_reviews.id"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("consent_reference", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "kind", "idempotency_key"),
)
ai_artifact_verifications = Table(
    "ai_artifact_verifications",
    metadata,
    Column(
        "artifact_id",
        UUID(as_uuid=True),
        ForeignKey("ai_artifact_private_links.artifact_id"),
        primary_key=True,
    ),
    Column("input_hash", String(64), nullable=False),
    Column("output_hash", String(64), nullable=False),
    Column("verification_version", String(100), nullable=False),
    Column("model_version", String(200), nullable=False),
    Column("prompt_version", String(200), nullable=False),
    Column("verified_at", DateTime(timezone=True), nullable=False),
)
ai_artifact_revocations = Table(
    "ai_artifact_revocations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "artifact_id",
        UUID(as_uuid=True),
        ForeignKey("ai_artifact_private_links.artifact_id"),
        nullable=False,
    ),
    Column(
        "owner_id",
        UUID(as_uuid=True),
        ForeignKey("owner_principals.id"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("reason_code", String(40), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "idempotency_key"),
)
OWNED_TABLES = (
    ai_output_entitlements,
    ai_output_entitlement_revocations,
    public_daily_ai_archives,
    promoted_news_summaries,
    ai_artifact_private_links,
    ai_artifact_verifications,
    ai_artifact_revocations,
)
