"""Append-only input clocks and nullable publication clock archive."""

from alembic import op
from sqlalchemy import Column, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from dragon_radar.publication.tables import input_clock_evidence

revision = "0016_clock_lineage"
down_revision = "0015_daily_ai"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = inspect(connection)
    if "clock_lineage" not in {
        column["name"] for column in inspector.get_columns("publication_results")
    }:
        op.add_column("publication_results", Column("clock_lineage", JSONB))
    input_clock_evidence.create(connection, checkfirst=True)
    # Old migrations create tables from current metadata on fresh schemas.
    # Check constraints independently for upgrades of already-existing schemas.
    checks = {
        item["name"]
        for item in inspect(connection).get_check_constraints("input_clock_evidence")
    }
    for constraint in input_clock_evidence.constraints:
        if hasattr(constraint, "sqltext") and constraint.name not in checks:
            op.create_check_constraint(
                constraint.name, "input_clock_evidence", str(constraint.sqltext)
            )
    uniques = {
        item["name"]
        for item in inspect(connection).get_unique_constraints("input_clock_evidence")
    }
    if "input_clock_evidence_content" not in uniques:
        op.create_unique_constraint(
            "input_clock_evidence_content",
            "input_clock_evidence",
            ["source_id", "archive_type", "archive_id", "revision_key", "input_sha256"],
        )
    for operation in ("UPDATE", "DELETE", "TRUNCATE"):
        name = "input_clock_evidence_no_" + operation.lower()
        exists = connection.scalar(
            text(
                "SELECT 1 FROM pg_trigger WHERE tgrelid = "
                "'input_clock_evidence'::regclass AND tgname = :name"
            ),
            {"name": name},
        )
        if not exists:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON input_clock_evidence "
                "FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation()"
            )


def downgrade():
    raise RuntimeError("Immutable clock lineage migration cannot be downgraded")
