"""Add Task17 timeout-adjacent persistence and retrieval contracts."""

import sqlalchemy as sa
from alembic import op

revision = "0011_ai_task_gap_fixes"
down_revision = "0010_ai_tasks"
branch_labels = None
depends_on = None


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    task_columns = _column_names(inspector, "ai_tasks")
    if "retrieval_version" not in task_columns:
        op.add_column(
            "ai_tasks",
            sa.Column(
                "retrieval_version",
                sa.String(30),
                nullable=False,
                server_default=sa.text("'r1'"),
            ),
        )

    review_columns = _column_names(inspector, "ai_task_reviews")
    add_human_status = "human_review_status" not in review_columns
    add_verification_status = "verification_status" not in review_columns
    if add_human_status:
        op.add_column(
            "ai_task_reviews",
            sa.Column("human_review_status", sa.String(20), nullable=True),
        )
    if add_verification_status:
        op.add_column(
            "ai_task_reviews",
            sa.Column("verification_status", sa.String(20), nullable=True),
        )
    if add_human_status or add_verification_status:
        op.execute(
            "ALTER TABLE ai_task_reviews DISABLE TRIGGER ai_task_reviews_append_only"
        )
        op.execute("""
            UPDATE ai_task_reviews
            SET human_review_status = COALESCE(
                    human_review_status,
                    CASE decision
                        WHEN 'accept' THEN 'accepted'
                        WHEN 'edit' THEN 'edited'
                        ELSE 'rejected'
                    END
                ),
                verification_status = COALESCE(
                    verification_status,
                    CASE decision
                        WHEN 'edit' THEN 'needs_human'
                        ELSE 'pending'
                    END
                )
        """)
        op.execute(
            "ALTER TABLE ai_task_reviews ENABLE TRIGGER ai_task_reviews_append_only"
        )
    if add_human_status:
        op.alter_column("ai_task_reviews", "human_review_status", nullable=False)
    if add_verification_status:
        op.alter_column("ai_task_reviews", "verification_status", nullable=False)

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("ai_task_reviews")
    }
    if "ai_task_reviews_human_status" not in check_names:
        op.create_check_constraint(
            "ai_task_reviews_human_status",
            "ai_task_reviews",
            "human_review_status IN ('accepted', 'edited', 'rejected')",
        )
    if "ai_task_reviews_verification_status" not in check_names:
        op.create_check_constraint(
            "ai_task_reviews_verification_status",
            "ai_task_reviews",
            ("verification_status IN ('pending', 'passed', 'failed', 'needs_human')"),
        )

    index_names = {index["name"] for index in inspector.get_indexes("ai_tasks")}
    if "ai_tasks_snapshot_id" not in index_names:
        op.create_index("ai_tasks_snapshot_id", "ai_tasks", ["snapshot_id"])

    op.execute("""
        CREATE OR REPLACE FUNCTION guard_ai_task_inputs() RETURNS trigger AS $$
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
               AND NEW.status IS DISTINCT FROM OLD.status
            THEN
                RAISE EXCEPTION 'AI task terminal status is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade():
    raise RuntimeError("AI task gap-fix persistence cannot be downgraded")
