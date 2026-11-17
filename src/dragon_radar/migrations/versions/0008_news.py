"""Add immutable point-in-time news, event, association, and review history."""

from alembic import op

from dragon_radar.news.tables import (
    news_article_revisions,
    news_articles,
    news_association_reviews,
    news_associations,
    news_event_articles,
    news_events,
    news_ingest_commands,
    news_review_audit_events,
    news_review_commands,
    news_snapshot_items,
    news_snapshots,
)
from dragon_radar.owner import models as _owner_models  # noqa: F401

revision = "0008_news"
down_revision = "0007c_analysis_jobs"
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


def upgrade():
    bind = op.get_bind()
    for table in (
        news_articles,
        news_article_revisions,
        news_events,
        news_event_articles,
        news_associations,
        news_ingest_commands,
        news_association_reviews,
        news_review_commands,
        news_review_audit_events,
        news_snapshots,
        news_snapshot_items,
    ):
        table.create(bind)

    op.create_foreign_key(
        "publications_news_input_id_fkey",
        "publications",
        "news_snapshots",
        ["news_input_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    for table in (
        news_articles,
        news_article_revisions,
        news_events,
        news_event_articles,
        news_associations,
        news_ingest_commands,
        news_association_reviews,
        news_review_commands,
        news_review_audit_events,
        news_snapshots,
        news_snapshot_items,
    ):
        _immutable(table.name)

    op.execute("""
        CREATE FUNCTION guard_news_snapshot_item() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM news_article_revisions revision
                  JOIN news_snapshots snapshot ON snapshot.id = NEW.snapshot_id
                  JOIN news_event_articles event_article
                    ON event_article.article_revision_id = revision.id
                 WHERE revision.id = NEW.article_revision_id
                   AND revision.article_id = NEW.article_id
                   AND revision.revision = NEW.revision
                   AND revision.available_at <= snapshot.as_of
                   AND event_article.event_id = NEW.event_id
            ) THEN
                RAISE EXCEPTION 'invalid news snapshot item';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER news_snapshot_items_guard
        BEFORE INSERT ON news_snapshot_items
        FOR EACH ROW EXECUTE FUNCTION guard_news_snapshot_item();
    """)

    op.execute("""
        CREATE FUNCTION guard_news_review_revision() RETURNS trigger AS $$
        DECLARE
            current_revision integer;
        BEGIN
            PERFORM 1
              FROM news_associations
             WHERE id = NEW.association_id
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'news association does not exist';
            END IF;
            SELECT COALESCE(max(revision), 0)
              INTO current_revision
              FROM news_association_reviews
             WHERE association_id = NEW.association_id;
            IF NEW.expected_revision <> current_revision
               OR NEW.revision <> current_revision + 1
            THEN
                RAISE EXCEPTION 'news association revision conflict';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER news_association_reviews_revision
        BEFORE INSERT ON news_association_reviews
        FOR EACH ROW EXECUTE FUNCTION guard_news_review_revision();
    """)

    op.execute("""
        CREATE FUNCTION guard_publication_news_binding() RETURNS trigger AS $$
        DECLARE
            expected_count integer;
            dependency_count integer;
            valid_count integer;
        BEGIN
            IF NOT (
                OLD.status = 'draft'
                AND NEW.status = 'published'
            ) THEN
                RETURN NEW;
            END IF;
            SELECT count(*)
              INTO dependency_count
              FROM source_dependencies dependency
             WHERE dependency.publication_id = NEW.id
               AND (
                   dependency.derived_type = 'news'
                   OR dependency.archive_type = 'news_article_revision'
               );
            IF NEW.news_input_id IS NULL THEN
                IF dependency_count <> 0 THEN
                    RAISE EXCEPTION 'invalid publication news lineage';
                END IF;
                RETURN NEW;
            END IF;
            SELECT count(*)
              INTO expected_count
              FROM news_snapshot_items item
             WHERE item.snapshot_id = NEW.news_input_id;
            SELECT count(*)
              INTO valid_count
              FROM source_dependencies dependency
              JOIN news_article_revisions revision
                ON revision.id = dependency.archive_id
              JOIN news_snapshot_items item
                ON item.snapshot_id = NEW.news_input_id
               AND item.article_revision_id = revision.id
             WHERE dependency.publication_id = NEW.id
               AND dependency.derived_id = NEW.id
               AND dependency.derived_type = 'news'
               AND dependency.archive_type = 'news_article_revision'
               AND dependency.source_id = revision.source_id;
            IF dependency_count <> expected_count
               OR valid_count <> expected_count
            THEN
                RAISE EXCEPTION 'invalid publication news lineage';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publications_news_binding
        BEFORE UPDATE ON publications
        FOR EACH ROW EXECUTE FUNCTION guard_publication_news_binding();
    """)

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

    op.execute("""
        CREATE FUNCTION guard_news_withdrawal_seal() RETURNS trigger AS $$
        DECLARE
            withdrawal_source text;
        BEGIN
            SELECT source_id INTO STRICT withdrawal_source
              FROM withdrawal_records
             WHERE id = NEW.withdrawal_id;
            IF EXISTS (
                SELECT 1
                  FROM news_article_revisions revision
                 WHERE revision.source_id = withdrawal_source
                   AND NOT EXISTS (
                       SELECT 1
                         FROM withdrawal_dependencies binding
                         LEFT JOIN source_dependencies dependency
                           ON dependency.id = binding.source_dependency_id
                        WHERE binding.withdrawal_id = NEW.withdrawal_id
                          AND COALESCE(
                              binding.archive_type,
                              dependency.archive_type
                          ) = 'news_article_revision'
                          AND COALESCE(
                              binding.archive_id,
                              dependency.archive_id
                          ) = revision.id
                   )
            ) THEN
                RAISE EXCEPTION 'news withdrawal archive bindings are incomplete';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER withdrawal_binding_seals_news_guard
        BEFORE INSERT ON withdrawal_binding_seals
        FOR EACH ROW EXECUTE FUNCTION guard_news_withdrawal_seal();
    """)


def downgrade():
    raise RuntimeError("News history cannot be destructively downgraded")
