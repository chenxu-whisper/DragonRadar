"""Add immutable realtime evidence with commit-ordered point-in-time reads."""

from alembic import op

from dragon_radar.realtime.tables import (
    realtime_anomaly_events,
    realtime_import_attempts,
    realtime_minute_bars,
    realtime_quote_batches,
    realtime_quotes,
    realtime_sector_flow_points,
)

revision = "0011_realtime"
down_revision = "0010_ai_tasks"
branch_labels = None
depends_on = None

REALTIME_TABLES = (
    realtime_quote_batches,
    realtime_quotes,
    realtime_minute_bars,
    realtime_anomaly_events,
    realtime_sector_flow_points,
    realtime_import_attempts,
)


def upgrade():
    connection = op.get_bind()
    for table in REALTIME_TABLES:
        table.create(connection)
    for table in REALTIME_TABLES:
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
    raise RuntimeError("Realtime evidence history cannot be downgraded")
