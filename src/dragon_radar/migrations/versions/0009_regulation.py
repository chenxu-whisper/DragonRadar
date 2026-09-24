"""Add reviewed regulation rules, calculations, events, and snapshots."""

from alembic import op

from dragon_radar.regulation.tables import (
    regulation_calculations,
    regulation_commands,
    regulation_events,
    regulation_rule_statuses,
    regulation_rules,
    regulation_snapshot_events,
    regulation_snapshot_inputs,
    regulation_snapshot_rules,
    regulation_snapshots,
)

revision = "0009_regulation"
down_revision = "0008_news"
branch_labels = None
depends_on = None


def _immutable(table_name: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {table_name}_immutable
        BEFORE UPDATE OR DELETE ON {table_name}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER {table_name}_no_truncate
        BEFORE TRUNCATE ON {table_name}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def _replace_withdrawal_guard() -> None:
    op.execute("""
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
                SELECT 1 FROM withdrawal_binding_seals
                 WHERE withdrawal_id = NEW.withdrawal_id
            ) THEN
                RAISE EXCEPTION 'withdrawal dependencies are sealed';
            END IF;
            IF NEW.source_dependency_id IS NOT NULL THEN
                SELECT * INTO STRICT dependency
                  FROM source_dependencies
                 WHERE id = NEW.source_dependency_id;
                IF dependency.source_id <> withdrawal_source
                   OR dependency.dependency_sequence > withdrawal_cutoff
                   OR dependency.derived_id <> dependency.publication_id
                THEN
                    RAISE EXCEPTION 'invalid withdrawal dependency identity';
                END IF;
                IF dependency.archive_type = 'market_import_batch' THEN
                    SELECT source_id INTO STRICT archive_source
                      FROM import_batches WHERE id = dependency.archive_id;
                    IF NOT (
                        (
                            dependency.derived_type = 'market'
                            AND EXISTS (
                                SELECT 1 FROM publications publication
                                JOIN snapshots snapshot
                                  ON snapshot.id = publication.market_snapshot_id
                                WHERE publication.id = dependency.publication_id
                                  AND snapshot.batch_id = dependency.archive_id
                                  AND snapshot.source_id = dependency.source_id
                            )
                        )
                        OR (
                            dependency.derived_type = 'role'
                            AND EXISTS (
                                SELECT 1 FROM publications publication
                                WHERE publication.id = dependency.publication_id
                                  AND publication.role_input_id IS NOT NULL
                            )
                        )
                        OR (
                            dependency.derived_type = 'regulation'
                            AND EXISTS (
                                SELECT 1 FROM publications publication
                                JOIN regulation_snapshots regulation
                                  ON regulation.id
                                     = publication.regulation_input_id
                                JOIN snapshots snapshot
                                  ON snapshot.id
                                     = regulation.market_snapshot_id
                                WHERE publication.id = dependency.publication_id
                                  AND snapshot.batch_id = dependency.archive_id
                                  AND snapshot.source_id = dependency.source_id
                            )
                        )
                    ) THEN
                        RAISE EXCEPTION 'invalid market withdrawal dependency';
                    END IF;
                ELSIF dependency.archive_type = 'topic_import_batch' THEN
                    SELECT source_id INTO STRICT archive_source
                      FROM topic_import_batches WHERE id = dependency.archive_id;
                    IF NOT (
                        (
                            dependency.derived_type = 'topic'
                            AND EXISTS (
                                SELECT 1 FROM publications publication
                                JOIN topic_snapshots snapshot
                                  ON snapshot.id = publication.topic_snapshot_id
                                WHERE publication.id = dependency.publication_id
                                  AND snapshot.batch_id = dependency.archive_id
                                  AND snapshot.source_id = dependency.source_id
                            )
                        )
                        OR (
                            dependency.derived_type = 'role'
                            AND EXISTS (
                                SELECT 1 FROM publications publication
                                WHERE publication.id = dependency.publication_id
                                  AND publication.role_input_id IS NOT NULL
                            )
                        )
                    ) THEN
                        RAISE EXCEPTION 'invalid topic withdrawal dependency';
                    END IF;
                ELSIF dependency.archive_type = 'news_article_revision' THEN
                    SELECT source_id INTO STRICT archive_source
                      FROM news_article_revisions
                     WHERE id = dependency.archive_id;
                    IF dependency.derived_type <> 'news'
                       OR NOT EXISTS (
                           SELECT 1
                             FROM publications publication
                             JOIN news_snapshot_items item
                               ON item.snapshot_id = publication.news_input_id
                            WHERE publication.id = dependency.publication_id
                              AND item.article_revision_id = dependency.archive_id
                       )
                    THEN
                        RAISE EXCEPTION 'invalid news withdrawal dependency';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'unsupported withdrawal dependency archive';
                END IF;
            ELSIF NEW.archive_type = 'market_import_batch' THEN
                SELECT source_id INTO STRICT archive_source
                  FROM import_batches WHERE id = NEW.archive_id;
            ELSIF NEW.archive_type = 'topic_import_batch' THEN
                SELECT source_id INTO STRICT archive_source
                  FROM topic_import_batches WHERE id = NEW.archive_id;
            ELSIF NEW.archive_type = 'news_article_revision' THEN
                SELECT source_id INTO STRICT archive_source
                  FROM news_article_revisions WHERE id = NEW.archive_id;
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
    bind = op.get_bind()
    for table in (
        regulation_rules,
        regulation_rule_statuses,
        regulation_events,
        regulation_snapshots,
        regulation_snapshot_rules,
        regulation_snapshot_inputs,
        regulation_calculations,
        regulation_snapshot_events,
        regulation_commands,
    ):
        table.create(bind)

    op.create_foreign_key(
        "publications_regulation_input_id_fkey",
        "publications",
        "regulation_snapshots",
        ["regulation_input_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    for table in (
        regulation_rules,
        regulation_rule_statuses,
        regulation_events,
        regulation_snapshots,
        regulation_snapshot_rules,
        regulation_snapshot_inputs,
        regulation_calculations,
        regulation_snapshot_events,
        regulation_commands,
    ):
        _immutable(table.name)

    op.execute("""
        CREATE FUNCTION regulation_rule_is_production_eligible(
            rule_row regulation_rules
        )
        RETURNS boolean AS $$
        BEGIN
            RETURN COALESCE(
               rule_row.review_switch = 'approved'
               AND rule_row.official_source_url
                   ~* '^https://[^/?#@]+($|[/?#])'
               AND rule_row.review_record IS NOT NULL
               AND rule_row.review_record ?& ARRAY[
                   'prepared_by',
                   'reviewed_by',
                   'published_by',
                   'reviewed_at',
                   'published_at'
               ]
               AND rule_row.review_record ->> 'prepared_by'
                   <> rule_row.review_record ->> 'reviewed_by'
               AND (rule_row.review_record ->> 'published_at')::timestamptz
                   >= (rule_row.review_record ->> 'reviewed_at')::timestamptz
               AND jsonb_array_length(rule_row.test_cases) > 0
               AND rule_row.regression_passed
               AND rule_row.calculation_method IN ('relative_return')
               AND NOT EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements(rule_row.test_cases) test_case
                    WHERE NOT (test_case ?& ARRAY[
                              'name',
                              'computed_value',
                              'expected_state'
                          ])
                       OR test_case ->> 'expected_state' IS DISTINCT FROM
                          CASE
                              WHEN rule_row.direction = 'upper'
                                   AND (test_case ->> 'computed_value')::numeric
                                       >= rule_row.threshold
                              THEN 'triggered'
                              WHEN rule_row.direction = 'upper'
                                   AND (test_case ->> 'computed_value')::numeric
                                       >= rule_row.warning_threshold
                              THEN 'approaching_alert'
                              WHEN rule_row.direction = 'lower'
                                   AND (test_case ->> 'computed_value')::numeric
                                       <= rule_row.threshold
                              THEN 'triggered'
                              WHEN rule_row.direction = 'lower'
                                   AND (test_case ->> 'computed_value')::numeric
                                       <= rule_row.warning_threshold
                              THEN 'approaching_alert'
                              ELSE 'idle'
                          END
               ),
               FALSE
            );
        END;
        $$ LANGUAGE plpgsql IMMUTABLE;

        CREATE FUNCTION guard_regulation_rule_initial_activation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.active
               AND regulation_rule_is_production_eligible(NEW) IS NOT TRUE
            THEN
                RAISE EXCEPTION 'regulation rule is not production eligible';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_rules_activation_guard
        BEFORE INSERT ON regulation_rules
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_rule_initial_activation();

        CREATE FUNCTION guard_regulation_rule_status_activation()
        RETURNS trigger AS $$
        DECLARE
            rule_row regulation_rules%ROWTYPE;
        BEGIN
            IF NEW.status <> 'active' THEN
                RETURN NEW;
            END IF;
            SELECT * INTO STRICT rule_row
              FROM regulation_rules
             WHERE id = NEW.rule_record_id;
            IF regulation_rule_is_production_eligible(rule_row) IS NOT TRUE THEN
                RAISE EXCEPTION 'regulation rule is not production eligible';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_rule_statuses_activation_guard
        BEFORE INSERT ON regulation_rule_statuses
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_rule_status_activation();
    """)

    op.execute("""
        CREATE FUNCTION guard_regulation_snapshot_rule() RETURNS trigger AS $$
        DECLARE
            snapshot_as_of timestamptz;
            status_watermark bigint;
            initial_active boolean;
            latest_status text;
        BEGIN
            SELECT as_of, status_sequence_watermark
              INTO STRICT snapshot_as_of, status_watermark
              FROM regulation_snapshots
             WHERE id = NEW.snapshot_id;
            SELECT active INTO STRICT initial_active
              FROM regulation_rules
             WHERE id = NEW.rule_record_id
               AND created_at <= snapshot_as_of;
            SELECT status INTO latest_status
              FROM regulation_rule_statuses
             WHERE rule_record_id = NEW.rule_record_id
               AND sequence <= status_watermark
             ORDER BY sequence DESC
             LIMIT 1;
            IF latest_status = 'withdrawn'
               OR NEW.active IS DISTINCT FROM
                  COALESCE(latest_status = 'active', initial_active)
            THEN
                RAISE EXCEPTION 'invalid regulation snapshot rule';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_snapshot_rules_guard
        BEFORE INSERT ON regulation_snapshot_rules
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_snapshot_rule();
    """)

    op.execute("""
        CREATE FUNCTION guard_regulation_snapshot_input() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM regulation_snapshots snapshot
                 WHERE snapshot.id = NEW.snapshot_id
                   AND snapshot.trade_date = NEW.trade_date
            ) THEN
                RAISE EXCEPTION 'invalid regulation snapshot input';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_snapshot_inputs_guard
        BEFORE INSERT ON regulation_snapshot_inputs
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_snapshot_input();
    """)

    op.execute("""
        CREATE FUNCTION guard_regulation_calculation() RETURNS trigger AS $$
        DECLARE
            snapshot_date date;
        BEGIN
            SELECT trade_date INTO STRICT snapshot_date
              FROM regulation_snapshots
             WHERE id = NEW.snapshot_id;
            IF NEW.trade_date <> snapshot_date
               OR NOT EXISTS (
                   SELECT 1
                     FROM regulation_snapshot_rules binding
                    WHERE binding.snapshot_id = NEW.snapshot_id
                      AND binding.rule_record_id = NEW.rule_record_id
               )
            THEN
                RAISE EXCEPTION 'invalid regulation calculation lineage';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_calculations_guard
        BEFORE INSERT ON regulation_calculations
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_calculation();
    """)

    op.execute("""
        CREATE FUNCTION guard_regulation_snapshot_event() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM regulation_snapshots snapshot
                  JOIN regulation_events event
                    ON event.event_id = NEW.event_id
                 WHERE snapshot.id = NEW.snapshot_id
                   AND event.created_at <= snapshot.as_of
                   AND event.effective_from <= snapshot.trade_date
                   AND (
                       event.effective_to IS NULL
                       OR snapshot.trade_date < event.effective_to
                   )
            ) THEN
                RAISE EXCEPTION 'invalid regulation snapshot event';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_snapshot_events_guard
        BEFORE INSERT ON regulation_snapshot_events
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_snapshot_event();
    """)

    op.execute("""
        CREATE FUNCTION guard_publication_regulation_binding()
        RETURNS trigger AS $$
        DECLARE
            dependency_count integer;
            valid_count integer;
        BEGIN
            IF NOT (OLD.status = 'draft' AND NEW.status = 'published') THEN
                RETURN NEW;
            END IF;
            SELECT count(*) INTO dependency_count
              FROM source_dependencies dependency
             WHERE dependency.publication_id = NEW.id
               AND dependency.derived_type = 'regulation';
            IF NEW.regulation_input_id IS NULL THEN
                IF dependency_count <> 0 THEN
                    RAISE EXCEPTION 'invalid publication regulation lineage';
                END IF;
                RETURN NEW;
            END IF;
            SELECT count(*) INTO valid_count
              FROM source_dependencies dependency
              JOIN regulation_snapshots regulation
                ON regulation.id = NEW.regulation_input_id
              JOIN snapshots market
                ON market.id = regulation.market_snapshot_id
             WHERE dependency.publication_id = NEW.id
               AND dependency.derived_id = NEW.id
               AND dependency.derived_type = 'regulation'
               AND dependency.archive_type = 'market_import_batch'
               AND dependency.source_id = market.source_id
               AND dependency.archive_id = market.batch_id
               AND regulation.market_snapshot_id = NEW.market_snapshot_id
               AND regulation.trade_date = NEW.trade_date;
            IF dependency_count <> 1 OR valid_count <> 1 THEN
                RAISE EXCEPTION 'invalid publication regulation lineage';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publications_regulation_binding
        BEFORE UPDATE ON publications
        FOR EACH ROW EXECUTE FUNCTION guard_publication_regulation_binding();
    """)

    _replace_withdrawal_guard()


def downgrade():
    raise RuntimeError("Regulation history cannot be destructively downgraded")
