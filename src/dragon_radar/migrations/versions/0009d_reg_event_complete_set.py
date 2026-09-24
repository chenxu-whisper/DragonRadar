"""Seal complete regulation snapshots against their full effective event set."""

from alembic import op

revision = "0009d_reg_event_complete_set"
down_revision = "0009c_reg_event_provenance_required"
branch_labels = None
depends_on = None


def upgrade():
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

            PERFORM 1
              FROM regulation_snapshots snapshot
             WHERE snapshot.id = NEW.regulation_input_id
               FOR UPDATE;
            IF (
                SELECT snapshot.event_source_completeness = 'complete'
                  FROM regulation_snapshots snapshot
                 WHERE snapshot.id = NEW.regulation_input_id
            ) AND (
                EXISTS (
                    SELECT event.event_id
                      FROM regulation_snapshots snapshot
                      JOIN regulation_events event
                        ON event.source_id = snapshot.event_source_id
                       AND event.source_version = snapshot.event_source_version
                       AND event.created_at <= snapshot.as_of
                       AND event.effective_from <= snapshot.trade_date
                       AND (
                           event.effective_to IS NULL
                           OR snapshot.trade_date < event.effective_to
                       )
                     WHERE snapshot.id = NEW.regulation_input_id
                    EXCEPT
                    SELECT binding.event_id
                      FROM regulation_snapshot_events binding
                     WHERE binding.snapshot_id = NEW.regulation_input_id
                )
                OR EXISTS (
                    SELECT binding.event_id
                      FROM regulation_snapshot_events binding
                     WHERE binding.snapshot_id = NEW.regulation_input_id
                    EXCEPT
                    SELECT event.event_id
                      FROM regulation_snapshots snapshot
                      JOIN regulation_events event
                        ON event.source_id = snapshot.event_source_id
                       AND event.source_version = snapshot.event_source_version
                       AND event.created_at <= snapshot.as_of
                       AND event.effective_from <= snapshot.trade_date
                       AND (
                           event.effective_to IS NULL
                           OR snapshot.trade_date < event.effective_to
                       )
                     WHERE snapshot.id = NEW.regulation_input_id
                )
            ) THEN
                RAISE EXCEPTION 'invalid complete regulation event set';
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


def downgrade():
    raise RuntimeError("Complete regulation event-set sealing cannot be downgraded")
