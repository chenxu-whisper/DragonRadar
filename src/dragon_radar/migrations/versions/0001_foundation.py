"""Immutable archives, revisions and audits with an audited grant registry."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_grants",
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("grant", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("\"grant\"->>'source_id' = source_id"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("canonical_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("quality_report", JSONB, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("import_batches.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("supersedes", UUID(as_uuid=True), sa.ForeignKey("snapshots.id")),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "trade_date", "revision"),
        sa.CheckConstraint("revision > 0"),
    )
    op.create_index(
        "snapshots_listing", "snapshots", ["trade_date", "source_id", "revision"]
    )
    op.create_table(
        "audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("object_id", sa.Text, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION reject_immutable_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable records cannot be changed';
        END;
        $$;
    """)
    for table in ("import_batches", "snapshots", "audit_events"):
        op.execute(f"""
            CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """)
        op.execute(f"""
            CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation()
        """)
    op.execute("""
        CREATE FUNCTION audit_source_grant() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR
               (TG_OP = 'UPDATE' AND OLD.source_id <> NEW.source_id) THEN
                RAISE EXCEPTION 'source identity cannot be removed or changed';
            END IF;
            INSERT INTO audit_events
                (id, action, source_id, object_id, details, occurred_at)
            VALUES (
                gen_random_uuid(),
                CASE WHEN TG_OP = 'UPDATE'
                     AND OLD."grant"->>'approved' = 'true'
                     AND NEW."grant"->>'approved' = 'false'
                     THEN 'source_revoke' ELSE 'source_register' END,
                NEW.source_id, NEW.source_id,
                jsonb_build_object('previous',
                    CASE WHEN TG_OP = 'UPDATE' THEN OLD."grant" ELSE NULL END,
                    'current', NEW."grant"),
                NEW.updated_at
            );
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER grant_audit AFTER INSERT OR UPDATE OR DELETE ON source_grants
        FOR EACH ROW EXECUTE FUNCTION audit_source_grant();
        CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON source_grants
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def downgrade():
    raise RuntimeError(
        "Destructive downgrade of immutable research archives is disabled"
    )
