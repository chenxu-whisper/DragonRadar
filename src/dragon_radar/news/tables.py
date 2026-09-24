"""SQLAlchemy mappings for immutable news history and owner reviews."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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

news_articles = Table(
    "news_articles",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("source_article_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "source_article_id"),
)

news_article_revisions = Table(
    "news_article_revisions",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "article_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_articles.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("source_version", Text, nullable=False),
    Column("source_article_id", Text, nullable=False),
    Column("news_type", String(30), nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("content_scope", String(20), nullable=False),
    Column("summary", Text),
    Column("content", Text),
    Column("content_digest", String(64), nullable=False),
    Column("embedded_grant", JSONB, nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("article_id", "revision"),
    UniqueConstraint("source_id", "source_article_id", "revision"),
    CheckConstraint("revision > 0"),
    CheckConstraint("available_at >= received_at"),
    CheckConstraint("news_type IN ('news', 'announcement', 'research')"),
    CheckConstraint("content_scope IN ('none', 'summary', 'full')"),
    Index(
        "news_article_revisions_available",
        text("available_at DESC"),
        "article_id",
        text("revision DESC"),
    ),
)

news_events = Table(
    "news_events",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("evidence_kind", String(40), nullable=False),
    Column("evidence_value", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("evidence_kind", "evidence_value"),
    CheckConstraint(
        "evidence_kind IN ('source_event_id', 'canonical_url', 'content_digest', "
        "'manual_confirmation', 'isolated_article')"
    ),
)

news_event_articles = Table(
    "news_event_articles",
    metadata,
    Column(
        "event_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_events.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "article_revision_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_article_revisions.id", ondelete="RESTRICT"),
        primary_key=True,
        unique=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

news_associations = Table(
    "news_associations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "article_revision_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_article_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("target_type", String(20), nullable=False),
    Column("target_id", Text, nullable=False),
    Column("method", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("evidence_snippets", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("article_revision_id", "target_type", "target_id", "method"),
    CheckConstraint("target_type IN ('security', 'topic')"),
    CheckConstraint("method IN ('direct', 'manual', 'ai')"),
    CheckConstraint("status IN ('accepted', 'unreviewed', 'rejected')"),
    Index("news_associations_target", "target_type", "target_id"),
)

news_association_reviews = Table(
    "news_association_reviews",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "association_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_associations.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("expected_revision", Integer, nullable=False),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("decision", String(20), nullable=False),
    Column("reviewed_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("association_id", "revision"),
    CheckConstraint("revision > 0"),
    CheckConstraint("expected_revision >= 0"),
    CheckConstraint("decision IN ('accept', 'reject')"),
)

news_review_commands = Table(
    "news_review_commands",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column(
        "review_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_association_reviews.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner_id", "idempotency_key"),
)

news_review_audit_events = Table(
    "news_review_audit_events",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "owner_id",
        PGUUID(as_uuid=True),
        ForeignKey("owner_principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("action", String(80), nullable=False),
    Column("object_id", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

news_snapshots = Table(
    "news_snapshots",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("command_hash", String(64), nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

news_snapshot_items = Table(
    "news_snapshot_items",
    metadata,
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "article_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_articles.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "article_revision_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_article_revisions.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("revision", Integer, nullable=False),
    Column(
        "event_id",
        PGUUID(as_uuid=True),
        ForeignKey("news_events.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("position", Integer, nullable=False),
    UniqueConstraint("snapshot_id", "article_id"),
    UniqueConstraint("snapshot_id", "position"),
    CheckConstraint("revision > 0"),
    CheckConstraint("position >= 0"),
)
