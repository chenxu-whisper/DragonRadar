"""Immutable topic import attempts, archives, and revisioned snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_topic_snapshots"
down_revision = "0003_private_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "topic_import_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("canonical_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("quality_report", JSONB, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "topic_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("topic_import_batches.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column(
            "publication_sequence",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "supersedes",
            UUID(as_uuid=True),
            sa.ForeignKey("topic_snapshots.id", ondelete="RESTRICT"),
        ),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "trade_date", "revision"),
        sa.CheckConstraint("revision > 0"),
    )
    op.create_index(
        "topic_snapshots_listing",
        "topic_snapshots",
        [
            sa.text("trade_date DESC"),
            sa.text("source_id ASC"),
            sa.text("revision DESC"),
            sa.text("id ASC"),
        ],
    )
    op.create_table(
        "topic_import_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("topic_import_batches.id", ondelete="RESTRICT"),
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("embedded_grant", JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("missing_items", JSONB, nullable=False),
        sa.Column("failed_cursor", sa.Text),
        sa.Column("retry_from", sa.Date),
        sa.Column("retry_to", sa.Date),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('failed', 'succeeded')"),
    )
    op.create_index(
        "topic_import_attempts_listing",
        "topic_import_attempts",
        [sa.text("created_at DESC"), sa.text("id ASC")],
    )
    for table in (
        "topic_import_attempts",
        "topic_import_batches",
        "topic_snapshots",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
            CREATE TRIGGER {table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        """)


def downgrade():
    raise RuntimeError("Topic research history cannot be destructively downgraded")
