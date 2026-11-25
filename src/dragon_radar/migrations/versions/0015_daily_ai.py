"""Add immutable AI output licensing and public artifact registries."""

from alembic import op
from sqlalchemy import inspect

from dragon_radar.public_daily_ai.tables import OWNED_TABLES

revision = "0015_daily_ai"
down_revision = "0014_research"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_ai_artifact_erasure() RETURNS trigger AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'safe_payload') IS DISTINCT FROM
               (to_jsonb(OLD) - 'safe_payload')
               OR NEW.safe_payload <> jsonb_build_object(
                   'schema', 'dragon-radar.ai-tombstone/v1', 'erased', true)
               OR NOT EXISTS (
                   SELECT 1 FROM withdrawal_records w
                   JOIN withdrawal_dependencies wd ON wd.withdrawal_id = w.id
                   JOIN source_dependencies sd ON sd.id = wd.source_dependency_id
                   JOIN withdrawal_binding_seals seal ON seal.withdrawal_id = w.id
                   WHERE w.scope = 'storage'
                     AND w.action IN ('deleted', 'deidentified')
                     AND sd.dependency_sequence <= w.cutoff_dependency_sequence
                     AND sd.derived_id = OLD.id
                     AND sd.archive_type = CASE TG_TABLE_NAME
                         WHEN 'public_daily_ai_archives' THEN 'public_daily_ai'
                         ELSE 'promoted_news_summary' END
               )
            THEN
                RAISE EXCEPTION 'Unsealed AI artifact mutation is forbidden';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in OWNED_TABLES:
        table.create(bind, checkfirst=True)
        present = {c["name"] for c in inspect(bind).get_columns(table.name)}
        if present != set(table.c.keys()):
            raise RuntimeError("Incompatible daily AI registry schema")
        artifact = table.name in ("public_daily_ai_archives", "promoted_news_summaries")
        events = (
            ("DELETE" if artifact else "UPDATE OR DELETE", "immutable"),
            ("TRUNCATE", "no_truncate"),
        )
        for event, suffix in events:
            name = table.name + "_" + suffix
            exists = bind.exec_driver_sql(
                "SELECT 1 FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                (name, table.name),
            ).first()
            if not exists:
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {event} ON {table.name} "
                    "FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation()"
                )
        if artifact:
            name = table.name + "_erasure"
            exists = bind.exec_driver_sql(
                "SELECT 1 FROM pg_trigger WHERE tgname = %s AND tgrelid = %s::regclass",
                (name, table.name),
            ).first()
            if not exists:
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE UPDATE ON {table.name} "
                    "FOR EACH ROW EXECUTE FUNCTION guard_ai_artifact_erasure()"
                )
    op.execute(
        "CREATE INDEX IF NOT EXISTS source_dependencies_ai_reverse "
        "ON source_dependencies (derived_type, derived_id, dependency_sequence)"
    )
    _install_withdrawal_binding_guard()
    _install_news_publication_guard()


def _install_news_publication_guard():
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_publication_news_binding()
        RETURNS trigger AS $$
        DECLARE
            expected_count integer;
            dependency_count integer;
            valid_count integer;
            summary_count integer;
            summary_dependencies integer;
            valid_summaries integer;
            news_payload jsonb;
        BEGIN
            IF NOT (OLD.status = 'draft' AND NEW.status = 'published') THEN
                RETURN NEW;
            END IF;
            SELECT count(*) INTO dependency_count
              FROM source_dependencies d
             WHERE d.publication_id = NEW.id
               AND (d.derived_type = 'news'
                    OR d.archive_type = 'news_article_revision')
               AND d.archive_type <> 'promoted_news_summary';
            SELECT count(*) INTO summary_dependencies
              FROM source_dependencies d
             WHERE d.publication_id = NEW.id
               AND d.archive_type = 'promoted_news_summary';
            IF NEW.news_input_id IS NULL THEN
                IF dependency_count <> 0 OR summary_dependencies <> 0 THEN
                    RAISE EXCEPTION 'invalid publication news lineage';
                END IF;
                RETURN NEW;
            END IF;
            SELECT count(*) INTO expected_count FROM news_snapshot_items
             WHERE snapshot_id = NEW.news_input_id;
            SELECT count(*) INTO valid_count
              FROM source_dependencies d
              JOIN news_article_revisions r ON r.id = d.archive_id
              JOIN news_snapshot_items n ON n.snapshot_id = NEW.news_input_id
               AND n.article_revision_id = r.id
             WHERE d.publication_id = NEW.id AND d.derived_id = NEW.id
               AND d.derived_type = 'news'
               AND d.archive_type = 'news_article_revision'
               AND d.source_id = r.source_id;
            IF dependency_count <> expected_count OR valid_count <> expected_count THEN
                RAISE EXCEPTION 'invalid publication news lineage';
            END IF;
            SELECT payload INTO news_payload FROM publication_results
             WHERE publication_id = NEW.id AND domain = 'news' AND status = 'ready';
            SELECT count(*) INTO summary_count
              FROM jsonb_array_elements(news_payload->'articles') article
             WHERE article->'summary_artifact' IS NOT NULL
               AND article->'summary_artifact' <> 'null'::jsonb;
            SELECT count(*) INTO valid_summaries
              FROM source_dependencies d
              JOIN promoted_news_summaries a ON a.id = d.archive_id
              JOIN news_article_revisions r ON r.id = a.article_revision_id
              JOIN news_snapshot_items n ON n.snapshot_id = NEW.news_input_id
               AND n.article_revision_id = r.id
              CROSS JOIN LATERAL jsonb_array_elements(
                  news_payload->'articles') article
              CROSS JOIN LATERAL jsonb_array_elements(
                  NEW.command_json::jsonb->'summary_selections') selector
             WHERE d.publication_id = NEW.id AND d.derived_id = NEW.id
               AND d.derived_type = 'news'
               AND d.archive_type = 'promoted_news_summary'
               AND d.source_id = r.source_id
               AND article->'summary_artifact' = a.safe_payload
               AND article->>'revision_id' = r.id::text
               AND article->>'article_id' = r.article_id::text
               AND (article->>'revision')::integer = r.revision
               AND selector->>'summary_id' = a.id::text
               AND (selector->>'summary_revision')::integer = a.archive_version
               AND selector->>'article_revision_id' = r.id::text
               AND selector->>'article_id' = r.article_id::text
               AND (selector->>'article_revision')::integer = r.revision
               AND a.published_at <= NEW.published_at;
            IF summary_dependencies <> summary_count
               OR valid_summaries <> summary_count
               OR coalesce(jsonb_array_length(
                   NEW.command_json::jsonb->'summary_selections'), 0) <> summary_count
               OR (summary_count > 0
                   AND (news_payload->>'schema_version')::integer <> 3)
            THEN
                RAISE EXCEPTION 'invalid publication summary lineage';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def _install_withdrawal_binding_guard():
    # Split dispatch without modifying the legacy five-domain trigger function.
    op.execute("""
        CREATE OR REPLACE FUNCTION is_ai_artifact_dependency(dependency_id uuid)
        RETURNS boolean AS $$
            SELECT EXISTS (
                SELECT 1 FROM source_dependencies
                WHERE id = dependency_id AND archive_type IN (
                    'public_daily_ai', 'promoted_news_summary')
            );
        $$ LANGUAGE sql STABLE;

        CREATE OR REPLACE FUNCTION guard_ai_withdrawal_binding()
        RETURNS trigger AS $$
        DECLARE
            w withdrawal_records%ROWTYPE;
            d source_dependencies%ROWTYPE;
            valid_binding boolean := false;
        BEGIN
            SELECT * INTO STRICT w FROM withdrawal_records
              WHERE id = NEW.withdrawal_id FOR UPDATE;
            SELECT * INTO STRICT d FROM source_dependencies
              WHERE id = NEW.source_dependency_id;
            IF w.scope <> 'storage'
               OR w.action NOT IN ('deleted', 'deidentified')
               OR w.policy_reference IS NULL
               OR w.cutoff_dependency_sequence IS NULL
               OR d.source_id <> w.source_id
               OR d.dependency_sequence > w.cutoff_dependency_sequence
               OR NEW.archive_type IS NOT NULL OR NEW.archive_id IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM withdrawal_binding_seals
                   WHERE withdrawal_id = w.id)
            THEN
                RAISE EXCEPTION 'Invalid AI withdrawal cutoff binding';
            END IF;
            IF d.archive_type = 'public_daily_ai' THEN
                SELECT EXISTS (
                    SELECT 1 FROM public_daily_ai_archives a
                    WHERE a.id = d.archive_id AND a.id = d.derived_id
                      AND a.base_publication_id = d.publication_id
                      AND d.derived_type = 'daily_ai'
                      AND EXISTS (
                          SELECT 1 FROM jsonb_array_elements(
                              a.rights_manifest->'sources') rights
                          WHERE rights->>'source_id' = d.source_id)
                ) INTO valid_binding;
            ELSIF d.archive_type = 'promoted_news_summary' THEN
                SELECT EXISTS (
                    SELECT 1 FROM promoted_news_summaries a
                    JOIN news_article_revisions r ON r.id = a.article_revision_id
                    WHERE a.id = d.archive_id AND r.source_id = d.source_id
                      AND (
                          (d.derived_type = 'news_summary'
                           AND a.id = d.derived_id
                           AND a.base_publication_id = d.publication_id)
                          OR
                          (d.derived_type = 'news'
                           AND d.derived_id = d.publication_id
                           AND EXISTS (
                               SELECT 1 FROM publications p
                               JOIN news_snapshot_items n
                                 ON n.snapshot_id = p.news_input_id
                               WHERE p.id = d.publication_id
                                 AND n.article_revision_id = a.article_revision_id
                                 AND p.status = 'published'
                                 AND a.published_at <= p.published_at
                                 AND (
                                     EXISTS (
                                         SELECT 1 FROM publication_results result
                                         CROSS JOIN LATERAL jsonb_array_elements(
                                             result.payload->'articles') article
                                         WHERE result.publication_id = p.id
                                           AND result.domain = 'news'
                                           AND article->'summary_artifact' =
                                               a.safe_payload)
                                     OR EXISTS (
                                         SELECT 1
                                         FROM withdrawal_dependencies prior_binding
                                         JOIN withdrawal_records prior_withdrawal
                                           ON prior_withdrawal.id =
                                              prior_binding.withdrawal_id
                                         JOIN withdrawal_binding_seals prior_seal
                                           ON prior_seal.withdrawal_id =
                                              prior_withdrawal.id
                                         WHERE prior_binding.source_dependency_id = d.id
                                           AND prior_withdrawal.source_id = d.source_id
                                           AND prior_withdrawal.scope = 'storage'
                                           AND prior_withdrawal.action IN (
                                               'deleted', 'deidentified')
                                           AND d.dependency_sequence <=
                                               prior_withdrawal.cutoff_dependency_sequence
                                           AND prior_withdrawal.sequence < w.sequence
                                     )
                                 )))
                      )
                ) INTO valid_binding;
            END IF;
            IF NOT valid_binding THEN
                RAISE EXCEPTION 'Invalid AI withdrawal artifact identity';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER withdrawal_dependencies_insert_guard
          ON withdrawal_dependencies;
        CREATE TRIGGER withdrawal_dependencies_insert_guard
          BEFORE INSERT ON withdrawal_dependencies FOR EACH ROW
          WHEN (NOT is_ai_artifact_dependency(NEW.source_dependency_id))
          EXECUTE FUNCTION guard_withdrawal_dependency_insert();
        CREATE TRIGGER withdrawal_dependencies_ai_insert_guard
          BEFORE INSERT ON withdrawal_dependencies FOR EACH ROW
          WHEN (is_ai_artifact_dependency(NEW.source_dependency_id))
          EXECUTE FUNCTION guard_ai_withdrawal_binding();
    """)


def downgrade():
    raise RuntimeError("Daily AI archives require backup_restore_only")
