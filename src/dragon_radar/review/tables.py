"""Owner-scoped heads and append-only research history."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dragon_radar.owner.models import owner_principals  # noqa: F401
from dragon_radar.storage.models import metadata


def identity_columns():
    return (
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("owner_id", UUID(as_uuid=True), nullable=False),
        ForeignKeyConstraint(["owner_id"], ["owner_principals.id"]),
        UniqueConstraint("owner_id", "id"),
    )


watch_entries = Table(
    "watch_entries",
    metadata,
    *identity_columns(),
    Column("security_id", Text, nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
    UniqueConstraint("owner_id", "security_id"),
    CheckConstraint("version > 0", name="watch_version_positive"),
    Index("watch_owner_updated", "owner_id", "updated_at", "id"),
)

review_notes = Table(
    "review_notes",
    metadata,
    *identity_columns(),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("security_id", Text),
    Column("kind", String(20), nullable=False),
    Column("version", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("derived_from_revision_id", UUID(as_uuid=True)),
    Column("template_version", String(80)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
    CheckConstraint("version > 0", name="notes_version_positive"),
    CheckConstraint("kind IN ('note', 'daily')", name="notes_kind_valid"),
    CheckConstraint("state IN ('draft', 'confirmed')", name="notes_state_valid"),
    Index("notes_owner_updated", "owner_id", "updated_at", "id"),
)

review_note_revisions = Table(
    "review_note_revisions",
    metadata,
    *identity_columns(),
    Column("note_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("content", JSONB, nullable=False),
    Column("saved_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["owner_id", "note_id"], ["review_notes.owner_id", "review_notes.id"]
    ),
    UniqueConstraint("owner_id", "note_id", "version"),
    UniqueConstraint("owner_id", "note_id", "id"),
    CheckConstraint("version > 0", name="revisions_version_positive"),
    CheckConstraint("state IN ('draft', 'confirmed')", name="revisions_state_valid"),
)
review_notes.append_constraint(
    ForeignKeyConstraint(
        ["owner_id", "derived_from_revision_id"],
        ["review_note_revisions.owner_id", "review_note_revisions.id"],
        name="notes_derived_owner_fk",
        use_alter=True,
    )
)

review_verifications = Table(
    "review_verifications",
    metadata,
    *identity_columns(),
    Column("note_id", UUID(as_uuid=True), nullable=False),
    Column("revision_id", UUID(as_uuid=True), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True)),
    Column("outcome", String(20), nullable=False),
    Column("evidence", Text, nullable=False),
    Column("supersedes", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["owner_id", "note_id"], ["review_notes.owner_id", "review_notes.id"]
    ),
    ForeignKeyConstraint(
        ["owner_id", "note_id", "revision_id"],
        [
            "review_note_revisions.owner_id",
            "review_note_revisions.note_id",
            "review_note_revisions.id",
        ],
    ),
    ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"]),
    ForeignKeyConstraint(
        ["owner_id", "note_id", "revision_id", "supersedes"],
        [
            "review_verifications.owner_id",
            "review_verifications.note_id",
            "review_verifications.revision_id",
            "review_verifications.id",
        ],
    ),
    UniqueConstraint("owner_id", "note_id", "revision_id", "id"),
    CheckConstraint(
        "outcome IN ('supported', 'weakened', 'refuted', 'not_due')",
        name="verification_outcome_valid",
    ),
    Index("verifications_owner_note", "owner_id", "note_id"),
)

owner_commands = Table(
    "owner_commands",
    metadata,
    Column("owner_id", UUID(as_uuid=True), primary_key=True),
    Column("key", String(200), primary_key=True),
    Column("digest", String(64), nullable=False),
    Column("result", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["owner_id"], ["owner_principals.id"]),
)

private_audit_events = Table(
    "private_audit_events",
    metadata,
    *identity_columns(),
    Column("action", String(80), nullable=False),
    Column("object_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)
