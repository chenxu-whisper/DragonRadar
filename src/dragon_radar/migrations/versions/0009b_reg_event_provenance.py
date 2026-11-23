"""Bind regulation events and publication lineage to authorized sources."""

import sqlalchemy as sa
from alembic import op

revision = "0009b_reg_event_provenance"
down_revision = "0009a_reg_event_source"
branch_labels = None
depends_on = None


def _add_event_provenance() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("regulation_events")}
    if "source_id" not in columns:
        op.add_column(
            "regulation_events",
            sa.Column("source_id", sa.Text(), nullable=True),
        )
    if "source_version" not in columns:
        op.add_column(
            "regulation_events",
            sa.Column("source_version", sa.String(length=200), nullable=True),
        )

    foreign_keys = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in sa.inspect(bind).get_foreign_keys("regulation_events")
    }
    if ("source_id",) not in foreign_keys:
        op.create_foreign_key(
            "regulation_events_source_id_fkey",
            "regulation_events",
            "source_grants",
            ["source_id"],
            ["source_id"],
            ondelete="RESTRICT",
        )

    constraints = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_check_constraints("regulation_events")
    }
    if "regulation_events_source_provenance" not in constraints:
        op.create_check_constraint(
            "regulation_events_source_provenance",
            "regulation_events",
            "(source_id IS NULL AND source_version IS NULL) OR "
            "(source_id IS NOT NULL AND source_version IS NOT NULL "
            "AND source_id ~ '^[A-Za-z0-9_.:-]{1,200}$' "
            "AND source_version ~ '^[A-Za-z0-9_.:-]{1,200}$')",
        )

    indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("regulation_events")
    }
    if "regulation_events_source" not in indexes:
        op.create_index(
            "regulation_events_source",
            "regulation_events",
            ["source_id", "source_version"],
        )


def _replace_snapshot_event_guard() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_regulation_snapshot_event()
        RETURNS trigger AS $$
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
                   AND (
                       snapshot.event_source_completeness = 'unknown'
                       OR (
                           event.source_id = snapshot.event_source_id
                           AND event.source_version = snapshot.event_source_version
                       )
                   )
            ) THEN
                RAISE EXCEPTION 'invalid regulation snapshot event';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def _replace_publication_guard() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_publication_regulation_binding()
        RETURNS trigger AS $$
        DECLARE
            dependency_count integer;
            valid_count integer;
            event_source_count integer;
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

            WITH expected_event_sources AS (
                SELECT event_source_id AS source_id
                  FROM regulation_snapshots
                 WHERE id = NEW.regulation_input_id
                   AND event_source_completeness = 'complete'
                UNION
                SELECT event.source_id
                  FROM regulation_snapshot_events binding
                  JOIN regulation_events event
                    ON event.event_id = binding.event_id
                 WHERE binding.snapshot_id = NEW.regulation_input_id
                   AND event.source_id IS NOT NULL
            )
            SELECT count(*) INTO event_source_count
              FROM expected_event_sources;

            WITH expected_event_sources AS (
                SELECT event_source_id AS source_id
                  FROM regulation_snapshots
                 WHERE id = NEW.regulation_input_id
                   AND event_source_completeness = 'complete'
                UNION
                SELECT event.source_id
                  FROM regulation_snapshot_events binding
                  JOIN regulation_events event
                    ON event.event_id = binding.event_id
                 WHERE binding.snapshot_id = NEW.regulation_input_id
                   AND event.source_id IS NOT NULL
            )
            SELECT count(*) INTO valid_count
              FROM source_dependencies dependency
              JOIN regulation_snapshots regulation
                ON regulation.id = NEW.regulation_input_id
              JOIN snapshots market
                ON market.id = regulation.market_snapshot_id
             WHERE dependency.publication_id = NEW.id
               AND dependency.derived_id = NEW.id
               AND dependency.derived_type = 'regulation'
               AND regulation.market_snapshot_id = NEW.market_snapshot_id
               AND regulation.trade_date = NEW.trade_date
               AND (
                   (
                       dependency.archive_type = 'market_import_batch'
                       AND dependency.source_id = market.source_id
                       AND dependency.archive_id = market.batch_id
                   )
                   OR (
                       dependency.archive_type = 'regulation_event_snapshot'
                       AND dependency.archive_id = regulation.id
                       AND dependency.source_id IN (
                           SELECT source_id FROM expected_event_sources
                       )
                   )
               );
            IF dependency_count <> 1 + event_source_count
               OR valid_count <> dependency_count
            THEN
                RAISE EXCEPTION 'invalid publication regulation lineage';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
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
                                  ON regulation.id = publication.regulation_input_id
                                JOIN snapshots snapshot
                                  ON snapshot.id = regulation.market_snapshot_id
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
                ELSIF dependency.archive_type = 'regulation_event_snapshot' THEN
                    archive_source := dependency.source_id;
                    IF dependency.derived_type <> 'regulation'
                       OR NOT EXISTS (
                           SELECT 1
                             FROM publications publication
                             JOIN regulation_snapshots snapshot
                               ON snapshot.id = publication.regulation_input_id
                            WHERE publication.id = dependency.publication_id
                              AND snapshot.id = dependency.archive_id
                              AND (
                                  snapshot.event_source_id = dependency.source_id
                                  OR EXISTS (
                                      SELECT 1
                                        FROM regulation_snapshot_events binding
                                        JOIN regulation_events event
                                          ON event.event_id = binding.event_id
                                       WHERE binding.snapshot_id = snapshot.id
                                         AND event.source_id = dependency.source_id
                                  )
                              )
                       )
                    THEN
                        RAISE EXCEPTION
                            'invalid regulation event withdrawal dependency';
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
    _add_event_provenance()
    _replace_snapshot_event_guard()
    _replace_publication_guard()
    _replace_withdrawal_guard()


def downgrade():
    raise RuntimeError("Regulation event provenance cannot be downgraded")
