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
        CREATE FUNCTION regulation_identifier_is_valid(value text)
        RETURNS boolean AS $$
            SELECT value IS NOT NULL
               AND length(value) > 0
               AND value ~ '[^[:space:]]';
        $$ LANGUAGE sql IMMUTABLE;

        CREATE FUNCTION regulation_https_url_is_valid(value text)
        RETURNS boolean AS $$
            SELECT value IS NOT NULL
               AND value ~* '^https://[^/?#@]+($|[/?#])';
        $$ LANGUAGE sql IMMUTABLE;

        CREATE FUNCTION guard_regulation_rule_structure()
        RETURNS trigger AS $$
        DECLARE
            reviewed_at_value timestamptz;
            published_at_value timestamptz;
            test_case jsonb;
            test_value numeric;
            case_names text[] := ARRAY[]::text[];
        BEGIN
            IF NOT regulation_identifier_is_valid(NEW.rule_id)
               OR NOT regulation_identifier_is_valid(NEW.rule_version)
               OR (
                   NEW.applicable_exchange IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.applicable_exchange)
               )
               OR (
                   NEW.applicable_board IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.applicable_board)
               )
               OR (
                   NEW.security_status IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.security_status)
               )
               OR NEW.effective_to IS NOT NULL
                  AND NEW.effective_to <= NEW.effective_from
               OR NEW.threshold = 'NaN'::numeric
               OR NEW.warning_threshold = 'NaN'::numeric
               OR (
                   NEW.benchmark_index IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.benchmark_index)
               )
               OR (
                   NEW.normal_anomaly_count_mode IS NOT NULL
                   AND NOT regulation_identifier_is_valid(
                       NEW.normal_anomaly_count_mode
                   )
               )
               OR (
                   NEW.recompute_condition IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.recompute_condition)
               )
               OR NOT regulation_identifier_is_valid(NEW.calculation_method)
               OR (
                   NEW.official_source_url IS NOT NULL
                   AND NOT regulation_https_url_is_valid(NEW.official_source_url)
               )
            THEN
                RAISE EXCEPTION 'invalid regulation rule structure';
            END IF;

            IF NEW.review_record IS NOT NULL
               AND NEW.review_record <> 'null'::jsonb
            THEN
                IF jsonb_typeof(NEW.review_record) <> 'object'
                   OR NOT (NEW.review_record ?& ARRAY[
                       'prepared_by',
                       'reviewed_by',
                       'published_by',
                       'reviewed_at',
                       'published_at'
                   ])
                   OR (
                       SELECT count(*) FROM jsonb_object_keys(NEW.review_record)
                   ) <> 5
                   OR EXISTS (
                       SELECT 1
                         FROM jsonb_each(NEW.review_record) item
                        WHERE jsonb_typeof(item.value) <> 'string'
                   )
                   OR NOT regulation_identifier_is_valid(
                       NEW.review_record ->> 'prepared_by'
                   )
                   OR NOT regulation_identifier_is_valid(
                       NEW.review_record ->> 'reviewed_by'
                   )
                   OR NOT regulation_identifier_is_valid(
                       NEW.review_record ->> 'published_by'
                   )
                   OR NEW.review_record ->> 'prepared_by'
                      = NEW.review_record ->> 'reviewed_by'
                   OR NEW.review_record ->> 'reviewed_at'
                      !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                   OR NEW.review_record ->> 'published_at'
                      !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                THEN
                    RAISE EXCEPTION 'invalid regulation review record';
                END IF;
                BEGIN
                    reviewed_at_value :=
                        (NEW.review_record ->> 'reviewed_at')::timestamptz;
                    published_at_value :=
                        (NEW.review_record ->> 'published_at')::timestamptz;
                EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
                    RAISE EXCEPTION 'invalid regulation review record';
                END;
                IF published_at_value < reviewed_at_value THEN
                    RAISE EXCEPTION 'invalid regulation review record';
                END IF;
            END IF;

            IF jsonb_typeof(NEW.test_cases) <> 'array' THEN
                RAISE EXCEPTION 'invalid regulation test cases';
            END IF;
            FOR test_case IN
                SELECT value FROM jsonb_array_elements(NEW.test_cases)
            LOOP
                IF jsonb_typeof(test_case) <> 'object'
                   OR NOT (test_case ?& ARRAY[
                       'name',
                       'computed_value',
                       'expected_state'
                   ])
                   OR (SELECT count(*) FROM jsonb_object_keys(test_case)) <> 3
                   OR jsonb_typeof(test_case -> 'name') <> 'string'
                   OR jsonb_typeof(test_case -> 'computed_value') <> 'string'
                   OR jsonb_typeof(test_case -> 'expected_state') <> 'string'
                   OR NOT regulation_identifier_is_valid(test_case ->> 'name')
                   OR test_case ->> 'expected_state' NOT IN (
                       'idle',
                       'approaching_alert',
                       'triggered'
                   )
                THEN
                    RAISE EXCEPTION 'invalid regulation test case';
                END IF;
                BEGIN
                    test_value := (test_case ->> 'computed_value')::numeric;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION 'invalid regulation test case';
                END;
                IF test_value = 'NaN'::numeric
                   OR test_case ->> 'name' = ANY(case_names)
                THEN
                    RAISE EXCEPTION 'invalid regulation test case';
                END IF;
                case_names := array_append(case_names, test_case ->> 'name');
            END LOOP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_rules_structure_guard
        BEFORE INSERT ON regulation_rules
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_rule_structure();

        CREATE FUNCTION guard_regulation_event_structure()
        RETURNS trigger AS $$
        BEGIN
            IF NOT regulation_identifier_is_valid(NEW.event_id)
               OR NOT regulation_identifier_is_valid(NEW.rule_id)
               OR NOT regulation_identifier_is_valid(NEW.rule_version)
               OR NOT regulation_identifier_is_valid(NEW.security_id)
               OR (
                   NEW.announcement_id IS NOT NULL
                   AND NOT regulation_identifier_is_valid(NEW.announcement_id)
               )
               OR (
                   NEW.announcement_url IS NOT NULL
                   AND NOT regulation_https_url_is_valid(NEW.announcement_url)
               )
               OR NEW.effective_to IS NOT NULL
                  AND NEW.effective_to <= NEW.effective_from
            THEN
                RAISE EXCEPTION 'invalid regulation event structure';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_events_structure_guard
        BEFORE INSERT ON regulation_events
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_event_structure();
    """)

    op.execute("""
        CREATE FUNCTION regulation_rule_is_production_eligible(
            rule_row regulation_rules
        )
        RETURNS boolean AS $$
        BEGIN
            RETURN COALESCE(
               rule_row.review_switch = 'approved'
               AND regulation_https_url_is_valid(rule_row.official_source_url)
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
        CREATE FUNCTION guard_regulation_snapshot_root() RETURNS trigger AS $$
        DECLARE
            market_date date;
            market_as_of timestamptz;
            visible_status_watermark bigint;
        BEGIN
            SELECT market.trade_date,
                   (batch.canonical_json::jsonb ->> 'as_of')::timestamptz
              INTO STRICT market_date, market_as_of
              FROM snapshots market
              JOIN import_batches batch ON batch.id = market.batch_id
             WHERE market.id = NEW.market_snapshot_id;
            SELECT COALESCE(max(sequence), 0)
              INTO visible_status_watermark
              FROM regulation_rule_statuses;
            IF NEW.trade_date IS DISTINCT FROM market_date
               OR NEW.as_of IS DISTINCT FROM market_as_of
               OR NEW.status_sequence_watermark > visible_status_watermark
            THEN
                RAISE EXCEPTION 'invalid regulation snapshot root';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER regulation_snapshots_root_guard
        BEFORE INSERT ON regulation_snapshots
        FOR EACH ROW EXECUTE FUNCTION guard_regulation_snapshot_root();

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
            IF (NEW.input_state = 'complete') IS DISTINCT FROM
                   (NEW.computed_value IS NOT NULL)
               OR NEW.computed_value = 'NaN'::numeric
            THEN
                RAISE EXCEPTION 'invalid regulation snapshot input state';
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
            binding_active boolean;
            rule_row regulation_rules%ROWTYPE;
            input_row regulation_snapshot_inputs%ROWTYPE;
            expected_reasons jsonb := '[]'::jsonb;
            expected_state text;
        BEGIN
            SELECT trade_date INTO STRICT snapshot_date
              FROM regulation_snapshots
             WHERE id = NEW.snapshot_id;
            SELECT rule.* INTO STRICT rule_row
              FROM regulation_rules rule
              JOIN regulation_snapshot_rules binding
                ON binding.rule_record_id = rule.id
             WHERE binding.snapshot_id = NEW.snapshot_id
               AND binding.rule_record_id = NEW.rule_record_id;
            SELECT active INTO STRICT binding_active
              FROM regulation_snapshot_rules
             WHERE snapshot_id = NEW.snapshot_id
               AND rule_record_id = NEW.rule_record_id;
            SELECT * INTO STRICT input_row
              FROM regulation_snapshot_inputs input
             WHERE input.snapshot_id = NEW.snapshot_id
               AND input.rule_record_id = NEW.rule_record_id
               AND input.security_id = NEW.security_id;

            IF NEW.trade_date IS DISTINCT FROM snapshot_date THEN
                RAISE EXCEPTION 'invalid regulation calculation lineage';
            END IF;
            IF rule_row.review_switch <> 'approved' THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('rule_not_approved');
            END IF;
            IF NOT binding_active THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('rule_inactive');
            END IF;
            IF rule_row.official_source_url IS NULL THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('official_source_missing');
            END IF;
            IF rule_row.review_record IS NULL
               OR rule_row.review_record = 'null'::jsonb
            THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('review_record_missing');
            END IF;
            IF jsonb_array_length(rule_row.test_cases) = 0 THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('regression_cases_missing');
            END IF;
            IF NOT rule_row.regression_passed THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('regression_not_passed');
            END IF;
            IF rule_row.calculation_method NOT IN ('relative_return') THEN
                expected_reasons :=
                    expected_reasons
                    || jsonb_build_array('unsupported_calculation_method');
            END IF;
            IF rule_row.benchmark_index IS NULL THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('benchmark_missing');
            END IF;
            IF NOT (
                rule_row.effective_from <= input_row.trade_date
                AND (
                    rule_row.effective_to IS NULL
                    OR input_row.trade_date < rule_row.effective_to
                )
                AND (
                    rule_row.applicable_exchange IS NULL
                    OR rule_row.applicable_exchange = input_row.exchange
                )
                AND (
                    rule_row.applicable_board IS NULL
                    OR rule_row.applicable_board = input_row.board
                )
                AND (
                    rule_row.security_status IS NULL
                    OR rule_row.security_status = input_row.security_status
                )
            ) THEN
                expected_reasons :=
                    expected_reasons || jsonb_build_array('rule_not_applicable');
            END IF;
            IF input_row.input_state <> 'complete' THEN
                expected_reasons :=
                    expected_reasons
                    || jsonb_build_array('input_' || input_row.input_state);
            END IF;

            IF jsonb_array_length(expected_reasons) > 0 THEN
                expected_state := 'pending';
            ELSIF rule_row.direction = 'upper'
                  AND input_row.computed_value >= rule_row.threshold
            THEN
                expected_state := 'triggered';
            ELSIF rule_row.direction = 'upper'
                  AND input_row.computed_value >= rule_row.warning_threshold
            THEN
                expected_state := 'approaching_alert';
            ELSIF rule_row.direction = 'lower'
                  AND input_row.computed_value <= rule_row.threshold
            THEN
                expected_state := 'triggered';
            ELSIF rule_row.direction = 'lower'
                  AND input_row.computed_value <= rule_row.warning_threshold
            THEN
                expected_state := 'approaching_alert';
            ELSE
                expected_state := 'idle';
            END IF;

            IF NEW.input_state IS DISTINCT FROM input_row.input_state
               OR NEW.computed_value IS DISTINCT FROM input_row.computed_value
               OR NEW.state IS DISTINCT FROM expected_state
               OR jsonb_typeof(NEW.reasons) IS DISTINCT FROM 'array'
               OR NEW.reasons IS DISTINCT FROM expected_reasons
            THEN
                RAISE EXCEPTION 'invalid regulation calculation result';
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
