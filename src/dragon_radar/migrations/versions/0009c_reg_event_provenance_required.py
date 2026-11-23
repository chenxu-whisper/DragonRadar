"""Require provenance on new regulation events while retaining legacy rows."""

import sqlalchemy as sa
from alembic import op

revision = "0009c_reg_event_provenance_required"
down_revision = "0009b_reg_event_provenance"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
    constraints = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("regulation_events")
    }
    if "regulation_events_source_provenance_required" not in constraints:
        op.execute(
            """
            ALTER TABLE regulation_events
            ADD CONSTRAINT regulation_events_source_provenance_required
            CHECK (source_id IS NOT NULL AND source_version IS NOT NULL)
            NOT VALID
            """
        )


def downgrade():
    raise RuntimeError("Required regulation event provenance cannot be downgraded")
