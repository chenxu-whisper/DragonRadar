"""Persist regulation event-source completeness evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0009a_reg_event_source"
down_revision = "0009_regulation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("regulation_snapshots")
    }
    if "event_source_completeness" not in columns:
        op.add_column(
            "regulation_snapshots",
            sa.Column(
                "event_source_completeness",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            ),
        )
    if "event_source_id" not in columns:
        op.add_column(
            "regulation_snapshots",
            sa.Column("event_source_id", sa.String(length=200), nullable=True),
        )
    if "event_source_version" not in columns:
        op.add_column(
            "regulation_snapshots",
            sa.Column("event_source_version", sa.String(length=200), nullable=True),
        )
    constraints = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("regulation_snapshots")
    }
    if "regulation_snapshots_event_source_evidence" not in constraints:
        op.create_check_constraint(
            "regulation_snapshots_event_source_evidence",
            "regulation_snapshots",
            "("
            "event_source_completeness = 'unknown' "
            "AND event_source_id IS NULL "
            "AND event_source_version IS NULL"
            ") OR ("
            "event_source_completeness = 'complete' "
            "AND event_source_id IS NOT NULL "
            "AND event_source_version IS NOT NULL "
            "AND event_source_id ~ '^[A-Za-z0-9_.:-]{1,200}$' "
            "AND event_source_version ~ '^[A-Za-z0-9_.:-]{1,200}$'"
            ")",
        )


def downgrade():
    raise RuntimeError("Regulation event-source evidence cannot be downgraded")
