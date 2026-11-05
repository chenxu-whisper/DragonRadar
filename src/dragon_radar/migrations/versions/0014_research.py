"""Add owner-scoped research backtest runs and the immutable result cache."""

from alembic import op

from dragon_radar.research.tables import research_backtest_cache, research_backtest_runs

revision = "0014_research"
down_revision = "0013_query"
branch_labels = None
depends_on = None


def upgrade():
    research_backtest_cache.create(op.get_bind())
    research_backtest_runs.create(op.get_bind())
    op.execute("""
        CREATE FUNCTION guard_research_backtest_inputs() RETURNS trigger AS $$
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
            THEN
                RAISE EXCEPTION 'research backtest inputs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER research_backtest_runs_immutable_inputs
        BEFORE UPDATE ON research_backtest_runs
        FOR EACH ROW EXECUTE FUNCTION guard_research_backtest_inputs();

        CREATE TRIGGER research_backtest_cache_immutable
        BEFORE UPDATE OR DELETE ON research_backtest_cache
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();

        CREATE TRIGGER research_backtest_cache_no_truncate
        BEFORE TRUNCATE ON research_backtest_cache
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def downgrade():
    raise RuntimeError("Research backtest history cannot be downgraded")
