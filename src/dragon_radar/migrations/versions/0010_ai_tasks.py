"""Add owner-scoped asynchronous AI tasks and append-only draft history."""

from alembic import op

from dragon_radar.ai.tables import (
    ai_task_cancellations,
    ai_task_drafts,
    ai_task_reviews,
    ai_tasks,
)

revision = "0010_ai_tasks"
down_revision = "0009d_reg_event_complete_set"
branch_labels = None
depends_on = None


def upgrade():
    ai_tasks.create(op.get_bind())
    ai_task_drafts.create(op.get_bind())
    ai_task_cancellations.create(op.get_bind())
    ai_task_reviews.create(op.get_bind())
    op.execute("""
        CREATE FUNCTION guard_ai_task_inputs() RETURNS trigger AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
               OR NEW.task_type IS DISTINCT FROM OLD.task_type
               OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
               OR NEW.as_of IS DISTINCT FROM OLD.as_of
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.input_manifest IS DISTINCT FROM OLD.input_manifest
               OR NEW.retrieval_version IS DISTINCT FROM OLD.retrieval_version
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'AI task inputs are immutable';
            END IF;
            IF NEW.attempt_count < OLD.attempt_count THEN
                RAISE EXCEPTION 'AI task attempts cannot decrease';
            END IF;
            IF OLD.status IN ('succeeded', 'failed', 'cancelled')
               AND (
                   NEW.status IS DISTINCT FROM OLD.status
                   OR NEW.latency_ms IS DISTINCT FROM OLD.latency_ms
               )
            THEN
                RAISE EXCEPTION 'AI task terminal state is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER ai_tasks_guard
        BEFORE UPDATE ON ai_tasks
        FOR EACH ROW EXECUTE FUNCTION guard_ai_task_inputs();

        CREATE TRIGGER ai_task_drafts_append_only
        BEFORE UPDATE OR DELETE ON ai_task_drafts
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER ai_task_drafts_no_truncate
        BEFORE TRUNCATE ON ai_task_drafts
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();

        CREATE TRIGGER ai_task_cancellations_append_only
        BEFORE UPDATE OR DELETE ON ai_task_cancellations
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER ai_task_cancellations_no_truncate
        BEFORE TRUNCATE ON ai_task_cancellations
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();

        CREATE TRIGGER ai_task_reviews_append_only
        BEFORE UPDATE OR DELETE ON ai_task_reviews
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER ai_task_reviews_no_truncate
        BEFORE TRUNCATE ON ai_task_reviews
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def downgrade():
    raise RuntimeError("AI task append-only history cannot be downgraded")
