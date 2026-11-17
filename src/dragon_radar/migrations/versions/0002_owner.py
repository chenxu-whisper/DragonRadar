"""Local owner identities, hashed credentials and installation pairing limit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_owner"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "owner_principals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("local_key", sa.Text, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("local_key IS NULL OR local_key = 'local'"),
    )
    op.create_table(
        "owner_pairings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("owner_principals.id"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("owner_pairings_owner", "owner_pairings", ["owner_id"])
    op.create_table(
        "owner_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("owner_principals.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("owner_sessions_owner", "owner_sessions", ["owner_id"])
    limits = op.create_table(
        "owner_pairing_limits",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.CheckConstraint("id = 1"),
        sa.CheckConstraint("failures >= 0 AND failures <= 5"),
    )
    op.bulk_insert(limits, [{"id": 1, "failures": 0}])


def downgrade():
    raise RuntimeError("Destructive downgrade of owner credentials is disabled")
