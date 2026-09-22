"""Immutable role runs, evidence, changes, and relay validations."""

from alembic import op

from dragon_radar.roles.tables import (
    role_change_summaries,
    role_changes,
    role_factor_observations,
    role_limit_rows,
    role_materialization_attempts,
    role_pending_validations,
    role_results,
    role_run_topics,
    role_runs,
    role_topic_statuses,
    role_validation_inputs,
    role_validations,
)

revision = "0007_role_readmodels"
down_revision = "0006_market_readmodels"
branch_labels = None
depends_on = None


def _immutable(table_name):
    op.execute(f"""
        CREATE TRIGGER {table_name}_immutable
        BEFORE UPDATE OR DELETE ON {table_name}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER {table_name}_no_truncate
        BEFORE TRUNCATE ON {table_name}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def upgrade():
    bind = op.get_bind()
    for table in (
        role_runs,
        role_run_topics,
        role_topic_statuses,
        role_results,
        role_factor_observations,
        role_limit_rows,
        role_change_summaries,
        role_changes,
        role_pending_validations,
        role_validations,
        role_validation_inputs,
        role_materialization_attempts,
    ):
        table.create(bind)

    op.create_foreign_key(
        "publications_role_input_id_fkey",
        "publications",
        "role_runs",
        ["role_input_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute("""
        CREATE FUNCTION guard_role_run_transition() RETURNS trigger AS $$
        DECLARE
            topic_count integer;
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
        CREATE TRIGGER role_runs_transition
        BEFORE INSERT OR UPDATE OR DELETE ON role_runs
        FOR EACH ROW EXECUTE FUNCTION guard_role_run_transition();
        CREATE TRIGGER role_runs_no_truncate
        BEFORE TRUNCATE ON role_runs
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)
    op.execute("""
        CREATE FUNCTION guard_role_evidence_insert() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM role_runs run
                 WHERE run.id = NEW.role_run_id
                   AND run.status = 'draft'
            ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'role evidence requires a draft root run';
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in (
        role_run_topics,
        role_topic_statuses,
        role_results,
        role_factor_observations,
        role_limit_rows,
        role_change_summaries,
        role_changes,
    ):
        _immutable(table.name)
        op.execute(f"""
            CREATE TRIGGER {table.name}_draft_insert
            BEFORE INSERT ON {table.name}
            FOR EACH ROW EXECUTE FUNCTION guard_role_evidence_insert();
        """)
    _immutable(role_pending_validations.name)
    op.execute("""
        CREATE FUNCTION guard_role_pending_insert() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM role_runs run
                 WHERE run.id = NEW.observation_run_id
                   AND run.status = 'draft'
            ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'pending validation requires a draft root run';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER role_pending_validations_draft_insert
        BEFORE INSERT ON role_pending_validations
        FOR EACH ROW EXECUTE FUNCTION guard_role_pending_insert();
    """)
    for table in (
        role_validations,
        role_validation_inputs,
        role_materialization_attempts,
    ):
        _immutable(table.name)

    op.execute("""
        CREATE FUNCTION guard_publication_role_binding() RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'draft' AND NEW.status = 'published' THEN
                IF NEW.role_input_id IS NULL THEN
                    RETURN NEW;
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM role_runs run
                     WHERE run.id = NEW.role_input_id
                       AND run.status = 'materialized'
                       AND run.market_snapshot_id = NEW.market_snapshot_id
                       AND run.topic_snapshot_id = NEW.topic_snapshot_id
                       AND run.trade_date = NEW.trade_date
                       AND length(run.rule_hash) = 64
                       AND run.rule_json <> ''
                ) THEN
                    RAISE EXCEPTION 'invalid publication role binding';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publications_role_binding
        BEFORE UPDATE ON publications
        FOR EACH ROW EXECUTE FUNCTION guard_publication_role_binding();
    """)


def downgrade():
    raise RuntimeError("Role evidence history cannot be destructively downgraded")
