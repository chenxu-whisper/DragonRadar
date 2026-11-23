"""Add immutable whitelist natural-language query records."""

from alembic import op

from dragon_radar.query.models import query_records

revision = "0013_query"
down_revision = "0012_lhb"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    query_records.create(connection)
    op.execute(
        """
        CREATE TRIGGER immutable_rows
        BEFORE UPDATE OR DELETE ON query_records
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER immutable_truncate
        BEFORE TRUNCATE ON query_records
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade():
    raise RuntimeError("Query records cannot be downgraded")
