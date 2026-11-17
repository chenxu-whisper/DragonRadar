"""Add immutable LHB disclosure revisions and source-scoped seat catalog."""

from alembic import op

from dragon_radar.lhb.tables import (
    lhb_disclosure_revisions,
    lhb_disclosure_seats,
    lhb_disclosures,
    lhb_import_attempts,
    lhb_import_batches,
    lhb_seats,
)

revision = "0012_lhb"
down_revision = "0011_realtime"
branch_labels = None
depends_on = None

LHB_TABLES = (
    lhb_import_batches,
    lhb_import_attempts,
    lhb_disclosures,
    lhb_disclosure_revisions,
    lhb_seats,
    lhb_disclosure_seats,
)


def upgrade():
    connection = op.get_bind()
    for table in LHB_TABLES:
        table.create(connection)
    for table in LHB_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER immutable_rows
            BEFORE UPDATE OR DELETE ON {table.name}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER immutable_truncate
            BEFORE TRUNCATE ON {table.name}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation()
            """
        )


def downgrade():
    raise RuntimeError("LHB disclosure history cannot be downgraded")
