"""Add versioned immutable research cache; never rewrite historical results."""

import sqlalchemy as sa
from alembic import op

from dragon_radar.research.tables import research_backtest_cache_v2

revision = "0018_research_contract"
down_revision = "0017_analysis_limits"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("research_backtest_runs")}
    for name, size, default in (
        ("result_schema_version", 100, "dragon-radar.research-backtest/v1"),
        ("compute_contract_sha256", 64, "0" * 64),
    ):
        if name not in columns:
            op.add_column(
                "research_backtest_runs",
                sa.Column(
                    name,
                    sa.String(size),
                    nullable=False,
                    server_default=default,
                ),
            )
    checks = {
        c["name"]
        for c in sa.inspect(op.get_bind()).get_check_constraints(
            "research_backtest_runs"
        )
    }
    for name, expression in (
        (
            "research_backtest_runs_result_schema",
            "result_schema_version IN ('dragon-radar.research-backtest/v1', "
            "'dragon-radar.research-backtest-result/v2')",
        ),
        (
            "research_backtest_runs_compute_contract",
            "compute_contract_sha256 ~ '^[0-9a-f]{64}$'",
        ),
    ):
        if name not in checks:
            op.create_check_constraint(name, "research_backtest_runs", expression)
    research_backtest_cache_v2.create(op.get_bind(), checkfirst=True)
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_research_backtest_inputs()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.config_hash IS DISTINCT FROM OLD.config_hash
               OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
               OR NEW.input_manifest IS DISTINCT FROM OLD.input_manifest
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.max_wall_clock_ms IS DISTINCT FROM OLD.max_wall_clock_ms
               OR NEW.result_schema_version IS DISTINCT FROM OLD.result_schema_version
               OR NEW.compute_contract_sha256
                  IS DISTINCT FROM OLD.compute_contract_sha256
            THEN
                RAISE EXCEPTION 'research backtest inputs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER research_backtest_cache_v2_immutable
        BEFORE UPDATE OR DELETE ON research_backtest_cache_v2
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER research_backtest_cache_v2_no_truncate
        BEFORE TRUNCATE ON research_backtest_cache_v2
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def downgrade():
    raise RuntimeError("Research history cannot be downgraded")
