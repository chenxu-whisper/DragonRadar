"""Add original request ranges to topic import attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0004a_topic_attempt_ranges"
down_revision = "0004_topic_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("topic_import_attempts", sa.Column("request_from", sa.Date))
    op.add_column("topic_import_attempts", sa.Column("request_to", sa.Date))


def downgrade():
    raise RuntimeError("Topic research history cannot be destructively downgraded")
