"""Enforce complete five-session role validation inputs."""

from alembic import op

revision = "0007b_role_validation_inputs"
down_revision = "0007a_role_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "role_validations_outcome",
        "role_validations",
        "outcome IN ('supported', 'weakened', 'refuted', 'unavailable')",
    )
    op.create_check_constraint(
        "role_validation_inputs_position",
        "role_validation_inputs",
        "position >= 0 AND position < 5",
    )
    op.execute("""
        CREATE FUNCTION guard_role_validation_input_completeness()
        RETURNS trigger AS $$
        DECLARE
            target_validation_id uuid;
            validation_outcome text;
            input_count integer;
            materialized_input_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'role_validations' THEN
                target_validation_id := NEW.id;
            ELSE
                target_validation_id := NEW.validation_id;
            END IF;

            SELECT outcome
              INTO validation_outcome
              FROM role_validations
             WHERE id = target_validation_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'role validation input has no validation';
            END IF;

            SELECT count(*),
                   count(*) FILTER (WHERE run.status = 'materialized')
              INTO input_count, materialized_input_count
              FROM role_validation_inputs input
              LEFT JOIN role_runs run ON run.id = input.input_role_run_id
             WHERE input.validation_id = target_validation_id;

            IF input_count > 5
               OR materialized_input_count <> input_count
               OR (
                   validation_outcome <> 'unavailable'
                   AND input_count <> 5
               )
            THEN
                RAISE EXCEPTION 'invalid role validation input completeness';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER role_validations_input_completeness
        AFTER INSERT ON role_validations
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION guard_role_validation_input_completeness();

        CREATE CONSTRAINT TRIGGER role_validation_inputs_completeness
        AFTER INSERT ON role_validation_inputs
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION guard_role_validation_input_completeness();
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM role_validations validation
                  LEFT JOIN LATERAL (
                      SELECT count(*) AS input_count,
                             count(*) FILTER (
                                 WHERE run.status = 'materialized'
                             ) AS materialized_input_count
                        FROM role_validation_inputs input
                        LEFT JOIN role_runs run
                          ON run.id = input.input_role_run_id
                       WHERE input.validation_id = validation.id
                  ) inputs ON TRUE
                 WHERE inputs.input_count > 5
                    OR inputs.materialized_input_count <> inputs.input_count
                    OR (
                        validation.outcome <> 'unavailable'
                        AND inputs.input_count <> 5
                    )
            ) THEN
                RAISE EXCEPTION
                    'existing role validation inputs violate completeness';
            END IF;
        END;
        $$;
    """)


def downgrade():
    op.execute(
        "DROP TRIGGER role_validation_inputs_completeness ON role_validation_inputs"
    )
    op.execute("DROP TRIGGER role_validations_input_completeness ON role_validations")
    op.execute("DROP FUNCTION guard_role_validation_input_completeness()")
    op.drop_constraint(
        "role_validation_inputs_position",
        "role_validation_inputs",
    )
    op.drop_constraint("role_validations_outcome", "role_validations")
