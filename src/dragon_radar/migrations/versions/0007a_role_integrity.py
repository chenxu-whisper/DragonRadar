"""Add role integrity constraints and validation command persistence."""

import sqlalchemy as sa
from alembic import op

from dragon_radar.roles.tables import role_validation_commands

revision = "0007a_role_integrity"
down_revision = "0007_role_readmodels"
branch_labels = None
depends_on = None

_ROLES = "'trend', 'core', 'emotion', 'catchup', 'follower', 'relay'"


def _replace_role_run_guard(*, require_visible_topics: bool) -> None:
    visible_declaration = (
        "visible_topic_count integer;" if require_visible_topics else ""
    )
    visible_count = (
        """
                SELECT count(*) INTO visible_topic_count
                  FROM topic_snapshots snapshot
                  JOIN topic_import_batches batch
                    ON batch.id = snapshot.batch_id
                 CROSS JOIN LATERAL jsonb_array_elements(
                     batch.canonical_json::jsonb -> 'topics'
                 ) definition
                 WHERE snapshot.id = NEW.topic_snapshot_id
                   AND (definition ->> 'available_at')::timestamptz
                       <= NEW.knowledge_cutoff
                   AND (definition ->> 'effective_from')::date <= NEW.trade_date
                   AND (
                       (definition ->> 'effective_to') IS NULL
                       OR NEW.trade_date < (definition ->> 'effective_to')::date
                   );"""
        if require_visible_topics
        else ""
    )
    visible_checks = (
        """
                   OR topic_count <> visible_topic_count
                   OR EXISTS (
                       SELECT 1
                         FROM role_run_topics topic
                        WHERE topic.role_run_id = NEW.id
                          AND NOT EXISTS (
                              SELECT 1
                                FROM topic_snapshots snapshot
                                JOIN topic_import_batches batch
                                  ON batch.id = snapshot.batch_id
                               CROSS JOIN LATERAL jsonb_array_elements(
                                   batch.canonical_json::jsonb -> 'topics'
                               ) definition
                               WHERE snapshot.id = NEW.topic_snapshot_id
                                 AND definition ->> 'topic_id' = topic.topic_id
                                 AND (definition ->> 'available_at')::timestamptz
                                     <= NEW.knowledge_cutoff
                                 AND (definition ->> 'effective_from')::date
                                     <= NEW.trade_date
                                 AND (
                                     (definition ->> 'effective_to') IS NULL
                                     OR NEW.trade_date
                                        < (definition ->> 'effective_to')::date
                                 )
                          )
                   )"""
        if require_visible_topics
        else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION guard_role_run_transition() RETURNS trigger AS $$
        DECLARE
            topic_count integer;
            {visible_declaration}
            status_count integer;
            invalid_topic_count integer;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status = 'draft' AND NEW.materialized_at IS NULL THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'role runs must be inserted as drafts';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.status = 'draft'
               AND NEW.status = 'materialized'
               AND OLD.materialized_at IS NULL
               AND NEW.materialized_at IS NOT NULL
               AND (to_jsonb(NEW) - 'status' - 'materialized_at')
                   = (to_jsonb(OLD) - 'status' - 'materialized_at')
            THEN
                SELECT count(*) INTO topic_count
                  FROM role_run_topics topic
                 WHERE topic.role_run_id = NEW.id;
                {visible_count}
                SELECT count(*) INTO status_count
                  FROM role_topic_statuses status
                 WHERE status.role_run_id = NEW.id;
                SELECT count(*) INTO invalid_topic_count
                  FROM role_run_topics topic
                 WHERE topic.role_run_id = NEW.id
                   AND (
                       SELECT count(*)
                         FROM role_topic_statuses status
                        WHERE status.role_run_id = topic.role_run_id
                          AND status.topic_id = topic.topic_id
                   ) <> 6;
                IF topic_count = 0
                   {visible_checks}
                   OR status_count <> topic_count * 6
                   OR invalid_topic_count <> 0
                THEN
                    RAISE EXCEPTION
                        'materialized role run requires six statuses per topic';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'immutable records cannot be changed';
        END;
        $$ LANGUAGE plpgsql;
    """)


def upgrade():
    op.create_foreign_key(
        "role_topic_statuses_role_run_id_topic_id_fkey",
        "role_topic_statuses",
        "role_run_topics",
        ["role_run_id", "topic_id"],
        ["role_run_id", "topic_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "role_topic_statuses_role",
        "role_topic_statuses",
        f"role IN ({_ROLES})",
    )
    op.create_unique_constraint(
        "role_results_pending_identity",
        "role_results",
        ["id", "role_run_id", "topic_id", "role"],
    )
    op.create_foreign_key(
        "role_results_role_run_id_topic_id_fkey",
        "role_results",
        "role_run_topics",
        ["role_run_id", "topic_id"],
        ["role_run_id", "topic_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "role_results_role",
        "role_results",
        f"role IN ({_ROLES})",
    )
    for table_name in (
        "role_factor_observations",
        "role_limit_rows",
        "role_change_summaries",
        "role_changes",
    ):
        op.create_foreign_key(
            f"{table_name}_role_run_id_topic_id_fkey",
            table_name,
            "role_run_topics",
            ["role_run_id", "topic_id"],
            ["role_run_id", "topic_id"],
            ondelete="RESTRICT",
        )
    for table_name in ("role_change_summaries", "role_changes"):
        op.create_check_constraint(
            f"{table_name}_role",
            table_name,
            f"role IN ({_ROLES})",
        )
    op.add_column(
        "role_pending_validations",
        sa.Column(
            "pending_result_role",
            sa.String(30),
            nullable=False,
            server_default="relay",
        ),
    )
    op.create_foreign_key(
        "role_pending_validations_observation_run_id_topic_id_fkey",
        "role_pending_validations",
        "role_run_topics",
        ["observation_run_id", "topic_id"],
        ["role_run_id", "topic_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "role_pending_validations_pending_result_id_observation_run_fkey",
        "role_pending_validations",
        "role_results",
        [
            "pending_result_id",
            "observation_run_id",
            "topic_id",
            "pending_result_role",
        ],
        ["id", "role_run_id", "topic_id", "role"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "role_pending_validations_relay",
        "role_pending_validations",
        "pending_result_role = 'relay'",
    )

    role_validation_commands.create(op.get_bind())
    op.execute("""
        CREATE TRIGGER role_validation_commands_immutable
        BEFORE UPDATE OR DELETE ON role_validation_commands
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER role_validation_commands_no_truncate
        BEFORE TRUNCATE ON role_validation_commands
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)
    op.execute("""
        CREATE FUNCTION guard_role_validation_chain() RETURNS trigger AS $$
        DECLARE
            previous_pending uuid;
            previous_revision integer;
        BEGIN
            IF NEW.revision = 1 THEN
                IF NEW.supersedes IS NOT NULL THEN
                    RAISE EXCEPTION
                        'first validation revision cannot supersede another record';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.revision <= 1 OR NEW.supersedes IS NULL THEN
                RAISE EXCEPTION
                    'validation correction requires a previous revision';
            END IF;
            SELECT pending_validation_id, revision
              INTO previous_pending, previous_revision
              FROM role_validations
             WHERE id = NEW.supersedes;
            IF NOT FOUND
               OR previous_pending <> NEW.pending_validation_id
               OR previous_revision <> NEW.revision - 1
            THEN
                RAISE EXCEPTION
                    'validation correction must supersede the same pending chain';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER role_validations_chain
        BEFORE INSERT ON role_validations
        FOR EACH ROW EXECUTE FUNCTION guard_role_validation_chain();
    """)
    _replace_role_run_guard(require_visible_topics=True)


def downgrade():
    _replace_role_run_guard(require_visible_topics=False)
    op.execute("DROP TRIGGER role_validations_chain ON role_validations")
    op.execute("DROP FUNCTION guard_role_validation_chain()")
    role_validation_commands.drop(op.get_bind())

    op.drop_constraint(
        "role_pending_validations_pending_result_id_observation_run_fkey",
        "role_pending_validations",
    )
    op.drop_constraint(
        "role_pending_validations_observation_run_id_topic_id_fkey",
        "role_pending_validations",
    )
    op.drop_constraint(
        "role_pending_validations_relay",
        "role_pending_validations",
    )
    op.drop_column("role_pending_validations", "pending_result_role")
    for table_name in ("role_changes", "role_change_summaries"):
        op.drop_constraint(f"{table_name}_role", table_name)
    for table_name in (
        "role_changes",
        "role_change_summaries",
        "role_limit_rows",
        "role_factor_observations",
    ):
        op.drop_constraint(
            f"{table_name}_role_run_id_topic_id_fkey",
            table_name,
        )
    op.drop_constraint("role_results_role", "role_results")
    op.drop_constraint(
        "role_results_role_run_id_topic_id_fkey",
        "role_results",
    )
    op.drop_constraint("role_results_pending_identity", "role_results")
    op.drop_constraint("role_topic_statuses_role", "role_topic_statuses")
    op.drop_constraint(
        "role_topic_statuses_role_run_id_topic_id_fkey",
        "role_topic_statuses",
    )
