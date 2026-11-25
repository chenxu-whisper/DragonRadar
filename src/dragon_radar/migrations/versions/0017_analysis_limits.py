"""Freeze a bounded cooperative analysis budget without changing command identity."""

import sqlalchemy as sa
from alembic import op

revision = "0017_analysis_limits"
down_revision = "0016_clock_lineage"
branch_labels = None
depends_on = None


def _guard(*, include_budget: bool) -> None:
    budget_clause = (
        "OR NEW.max_wall_clock_ms IS DISTINCT FROM OLD.max_wall_clock_ms"
        if include_budget else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION guard_analysis_job_inputs()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.cache_key IS DISTINCT FROM OLD.cache_key
               OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
               OR NEW.input_manifest IS DISTINCT FROM OLD.input_manifest
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               {budget_clause}
            THEN
                RAISE EXCEPTION 'analysis job inputs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)


def upgrade() -> None:
    # Older migrations create tables from live metadata on fresh databases.
    # Existing databases still need this additive column and constraint.
    inspector = sa.inspect(op.get_bind())
    if "max_wall_clock_ms" not in {
        column["name"] for column in inspector.get_columns("analysis_jobs")
    }:
        op.add_column(
            "analysis_jobs",
            sa.Column(
                "max_wall_clock_ms", sa.Integer(), nullable=False,
                server_default="300000",
            ),
        )
    if "analysis_jobs_wall_clock_budget" not in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("analysis_jobs")
    }:
        op.create_check_constraint(
            "analysis_jobs_wall_clock_budget", "analysis_jobs",
            "max_wall_clock_ms BETWEEN 1 AND 300000",
        )
    _guard(include_budget=True)


def downgrade() -> None:
    _guard(include_budget=False)
    op.drop_constraint(
        "analysis_jobs_wall_clock_budget", "analysis_jobs", type_="check",
    )
    op.drop_column("analysis_jobs", "max_wall_clock_ms")
