"""Add owner-scoped versioned analysis jobs and immutable result cache."""

from alembic import op

from dragon_radar.analysis_jobs.models import analysis_job_cache, analysis_jobs

revision = "0007c_analysis_jobs"
down_revision = "0007b_role_validation_inputs"
branch_labels = None
depends_on = None


def upgrade():
    analysis_job_cache.create(op.get_bind())
    analysis_jobs.create(op.get_bind())
    op.execute("""
        CREATE FUNCTION guard_analysis_job_inputs() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.cache_key IS DISTINCT FROM OLD.cache_key
               OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
               OR NEW.input_manifest IS DISTINCT FROM OLD.input_manifest
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'analysis job inputs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER analysis_jobs_immutable_inputs
        BEFORE UPDATE ON analysis_jobs
        FOR EACH ROW EXECUTE FUNCTION guard_analysis_job_inputs();

        CREATE TRIGGER analysis_job_cache_immutable
        BEFORE UPDATE OR DELETE ON analysis_job_cache
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();

        CREATE TRIGGER analysis_job_cache_no_truncate
        BEFORE TRUNCATE ON analysis_job_cache
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def downgrade():
    op.execute("DROP TRIGGER analysis_job_cache_no_truncate ON analysis_job_cache")
    op.execute("DROP TRIGGER analysis_job_cache_immutable ON analysis_job_cache")
    op.execute("DROP TRIGGER analysis_jobs_immutable_inputs ON analysis_jobs")
    op.execute("DROP FUNCTION guard_analysis_job_inputs()")
    analysis_jobs.drop(op.get_bind())
    analysis_job_cache.drop(op.get_bind())
