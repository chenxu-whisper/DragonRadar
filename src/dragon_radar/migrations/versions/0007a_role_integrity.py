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


def _replace_publication_dependency_guards(*, allow_role_dependencies: bool) -> None:
    market_scope = (
        "dependency.derived_type = 'market'"
        if allow_role_dependencies
        else "dependency.derived_type = 'market' "
        "OR dependency.archive_type = 'market_import_batch'"
    )
    topic_scope = (
        "dependency.derived_type = 'topic'"
        if allow_role_dependencies
        else "dependency.derived_type = 'topic' "
        "OR dependency.archive_type = 'topic_import_batch'"
    )
    role_declaration = (
        "invalid_role_dependency_count integer;" if allow_role_dependencies else ""
    )
    role_validation = (
        """
                SELECT count(*) FILTER (
                           WHERE dependency.derived_type = 'role'
                             AND (
                                 NEW.role_input_id IS NULL
                                 OR dependency.derived_id <> NEW.id
                                 OR dependency.publication_id <> NEW.id
                                 OR (
                                     dependency.archive_type = 'market_import_batch'
                                     AND NOT EXISTS (
                                         SELECT 1
                                           FROM import_batches archive
                                          WHERE archive.id = dependency.archive_id
                                            AND archive.source_id
                                                = dependency.source_id
                                     )
                                 )
                                 OR (
                                     dependency.archive_type = 'topic_import_batch'
                                     AND NOT EXISTS (
                                         SELECT 1
                                           FROM topic_import_batches archive
                                          WHERE archive.id = dependency.archive_id
                                            AND archive.source_id
                                                = dependency.source_id
                                     )
                                 )
                                 OR dependency.archive_type NOT IN (
                                     'market_import_batch',
                                     'topic_import_batch'
                                 )
                             )
                       )
                  INTO invalid_role_dependency_count
                  FROM source_dependencies dependency
                 WHERE dependency.publication_id = NEW.id;
                IF invalid_role_dependency_count <> 0 THEN
                    RAISE EXCEPTION 'invalid publication role lineage';
                END IF;"""
        if allow_role_dependencies
        else ""
    )
    op.execute(f"""
        CREATE OR REPLACE FUNCTION guard_publication_transition()
        RETURNS trigger AS $$
        DECLARE
            result_count integer;
            invalid_result_count integer;
            market_dependency_count integer;
            valid_market_dependency_count integer;
            topic_dependency_count integer;
            valid_topic_dependency_count integer;
            {role_declaration}
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status = 'draft' AND NEW.published_at IS NULL THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'publications must be inserted as drafts';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.status = 'draft'
               AND NEW.status = 'published'
               AND NEW.id IS NOT DISTINCT FROM OLD.id
               AND NEW.trade_date IS NOT DISTINCT FROM OLD.trade_date
               AND NEW.version IS NOT DISTINCT FROM OLD.version
               AND NEW.supersedes IS NOT DISTINCT FROM OLD.supersedes
               AND NEW.market_snapshot_id IS NOT DISTINCT FROM OLD.market_snapshot_id
               AND NEW.topic_snapshot_id IS NOT DISTINCT FROM OLD.topic_snapshot_id
               AND NEW.news_input_id IS NOT DISTINCT FROM OLD.news_input_id
               AND NEW.regulation_input_id IS NOT DISTINCT FROM OLD.regulation_input_id
               AND NEW.role_input_id IS NOT DISTINCT FROM OLD.role_input_id
               AND NEW.domains IS NOT DISTINCT FROM OLD.domains
               AND NEW.coverage IS NOT DISTINCT FROM OLD.coverage
               AND NEW.quality_flags IS NOT DISTINCT FROM OLD.quality_flags
               AND NEW.command_json IS NOT DISTINCT FROM OLD.command_json
               AND NEW.command_hash IS NOT DISTINCT FROM OLD.command_hash
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
               AND OLD.published_at IS NULL
               AND NEW.published_at IS NOT NULL
            THEN
                SELECT count(*),
                       count(*) FILTER (
                           WHERE result.input_id IS DISTINCT FROM
                               CASE result.domain
                                   WHEN 'market' THEN NEW.market_snapshot_id
                                   WHEN 'topic' THEN NEW.topic_snapshot_id
                                   WHEN 'news' THEN NEW.news_input_id
                                   WHEN 'regulation' THEN NEW.regulation_input_id
                                   WHEN 'role' THEN NEW.role_input_id
                               END
                              OR result.status IS DISTINCT FROM
                                 NEW.domains ->> result.domain
                              OR (result.status = 'ready')
                                 IS DISTINCT FROM (result.input_id IS NOT NULL)
                       )
                  INTO result_count, invalid_result_count
                  FROM publication_results result
                 WHERE result.publication_id = NEW.id;
                IF result_count <> 5
                   OR invalid_result_count <> 0
                   OR (
                       SELECT count(*) FROM jsonb_object_keys(NEW.domains)
                   ) <> 5
                THEN
                    RAISE EXCEPTION 'invalid publication result contract';
                END IF;

                SELECT count(*) FILTER (WHERE {market_scope}),
                       count(*) FILTER (
                           WHERE dependency.derived_type = 'market'
                             AND dependency.archive_type = 'market_import_batch'
                             AND dependency.derived_id = NEW.id
                             AND dependency.publication_id = NEW.id
                             AND dependency.source_id = snapshot.source_id
                             AND dependency.archive_id = snapshot.batch_id
                       )
                  INTO market_dependency_count, valid_market_dependency_count
                  FROM source_dependencies dependency
                  LEFT JOIN snapshots snapshot
                    ON snapshot.id = NEW.market_snapshot_id
                 WHERE dependency.publication_id = NEW.id;
                IF market_dependency_count <> 1
                   OR valid_market_dependency_count <> 1
                THEN
                    RAISE EXCEPTION 'invalid publication market lineage';
                END IF;

                SELECT count(*) FILTER (WHERE {topic_scope}),
                       count(*) FILTER (
                           WHERE dependency.derived_type = 'topic'
                             AND dependency.archive_type = 'topic_import_batch'
                             AND dependency.derived_id = NEW.id
                             AND dependency.publication_id = NEW.id
                             AND dependency.source_id = snapshot.source_id
                             AND dependency.archive_id = snapshot.batch_id
                       )
                  INTO topic_dependency_count, valid_topic_dependency_count
                  FROM source_dependencies dependency
                  LEFT JOIN topic_snapshots snapshot
                    ON snapshot.id = NEW.topic_snapshot_id
                 WHERE dependency.publication_id = NEW.id;
                IF (NEW.topic_snapshot_id IS NULL AND topic_dependency_count <> 0)
                   OR (
                       NEW.topic_snapshot_id IS NOT NULL
                       AND (
                           topic_dependency_count <> 1
                           OR valid_topic_dependency_count <> 1
                       )
                   )
                THEN
                    RAISE EXCEPTION 'invalid publication topic lineage';
                END IF;
                {role_validation}
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.status = 'published'
               AND NEW.status = 'revoked'
               AND NEW.id IS NOT DISTINCT FROM OLD.id
               AND NEW.trade_date IS NOT DISTINCT FROM OLD.trade_date
               AND NEW.version IS NOT DISTINCT FROM OLD.version
               AND NEW.supersedes IS NOT DISTINCT FROM OLD.supersedes
               AND NEW.market_snapshot_id IS NOT DISTINCT FROM OLD.market_snapshot_id
               AND NEW.topic_snapshot_id IS NOT DISTINCT FROM OLD.topic_snapshot_id
               AND NEW.news_input_id IS NOT DISTINCT FROM OLD.news_input_id
               AND NEW.regulation_input_id IS NOT DISTINCT FROM OLD.regulation_input_id
               AND NEW.role_input_id IS NOT DISTINCT FROM OLD.role_input_id
               AND NEW.domains IS NOT DISTINCT FROM OLD.domains
               AND NEW.coverage IS NOT DISTINCT FROM OLD.coverage
               AND NEW.quality_flags IS NOT DISTINCT FROM OLD.quality_flags
               AND NEW.command_json IS NOT DISTINCT FROM OLD.command_json
               AND NEW.command_hash IS NOT DISTINCT FROM OLD.command_hash
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
               AND NEW.published_at IS NOT DISTINCT FROM OLD.published_at
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'immutable records cannot be changed';
        END;
        $$ LANGUAGE plpgsql;
    """)
    role_market = (
        """
                       OR (
                           dependency.derived_type = 'role'
                           AND EXISTS (
                               SELECT 1
                                 FROM publications publication
                                WHERE publication.id = dependency.publication_id
                                  AND publication.role_input_id IS NOT NULL
                           )
                       )"""
        if allow_role_dependencies
        else ""
    )
    role_topic = role_market
    op.execute(f"""
        CREATE OR REPLACE FUNCTION guard_withdrawal_dependency_insert()
        RETURNS trigger AS $$
        DECLARE
            withdrawal_source text;
            withdrawal_action text;
            withdrawal_scope text;
            withdrawal_policy text;
            withdrawal_cutoff bigint;
            dependency source_dependencies%ROWTYPE;
            archive_source text;
        BEGIN
            SELECT source_id, action, scope, policy_reference,
                   cutoff_dependency_sequence
              INTO STRICT withdrawal_source, withdrawal_action, withdrawal_scope,
                          withdrawal_policy, withdrawal_cutoff
              FROM withdrawal_records
             WHERE id = NEW.withdrawal_id
               FOR UPDATE;
            IF withdrawal_action NOT IN ('deidentified', 'deleted')
               OR withdrawal_scope <> 'storage'
               OR withdrawal_policy IS NULL
               OR withdrawal_cutoff IS NULL
            THEN
                RAISE EXCEPTION 'withdrawal cannot bind dependencies';
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM withdrawal_binding_seals seal
                 WHERE seal.withdrawal_id = NEW.withdrawal_id
            )
            THEN
                RAISE EXCEPTION 'withdrawal dependencies are sealed';
            END IF;
            IF NEW.source_dependency_id IS NOT NULL THEN
                SELECT *
                  INTO STRICT dependency
                  FROM source_dependencies
                 WHERE id = NEW.source_dependency_id;
                IF dependency.source_id <> withdrawal_source
                   OR dependency.dependency_sequence > withdrawal_cutoff
                   OR dependency.derived_id <> dependency.publication_id
                THEN
                    RAISE EXCEPTION 'invalid withdrawal dependency identity';
                END IF;
                IF dependency.archive_type = 'market_import_batch' THEN
                    SELECT source_id
                      INTO STRICT archive_source
                      FROM import_batches
                     WHERE id = dependency.archive_id;
                    IF NOT (
                        (
                            dependency.derived_type = 'market'
                            AND EXISTS (
                                SELECT 1
                                  FROM publications publication
                                  JOIN snapshots snapshot
                                    ON snapshot.id
                                       = publication.market_snapshot_id
                                 WHERE publication.id
                                       = dependency.publication_id
                                   AND snapshot.batch_id = dependency.archive_id
                                   AND snapshot.source_id = dependency.source_id
                            )
                        )
                        {role_market}
                    )
                    THEN
                        RAISE EXCEPTION 'invalid market withdrawal dependency';
                    END IF;
                ELSIF dependency.archive_type = 'topic_import_batch' THEN
                    SELECT source_id
                      INTO STRICT archive_source
                      FROM topic_import_batches
                     WHERE id = dependency.archive_id;
                    IF NOT (
                        (
                            dependency.derived_type = 'topic'
                            AND EXISTS (
                                SELECT 1
                                  FROM publications publication
                                  JOIN topic_snapshots snapshot
                                    ON snapshot.id = publication.topic_snapshot_id
                                 WHERE publication.id
                                       = dependency.publication_id
                                   AND snapshot.batch_id = dependency.archive_id
                                   AND snapshot.source_id = dependency.source_id
                            )
                        )
                        {role_topic}
                    )
                    THEN
                        RAISE EXCEPTION 'invalid topic withdrawal dependency';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'unsupported withdrawal dependency archive';
                END IF;
            ELSIF NEW.archive_type = 'market_import_batch' THEN
                SELECT source_id
                  INTO STRICT archive_source
                  FROM import_batches
                 WHERE id = NEW.archive_id;
            ELSIF NEW.archive_type = 'topic_import_batch' THEN
                SELECT source_id
                  INTO STRICT archive_source
                  FROM topic_import_batches
                 WHERE id = NEW.archive_id;
            ELSE
                RAISE EXCEPTION 'unsupported withdrawal archive';
            END IF;
            IF archive_source <> withdrawal_source THEN
                RAISE EXCEPTION 'withdrawal archive did not exist for source';
            END IF;
            RETURN NEW;
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
    _replace_publication_dependency_guards(allow_role_dependencies=True)


def downgrade():
    _replace_publication_dependency_guards(allow_role_dependencies=False)
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
