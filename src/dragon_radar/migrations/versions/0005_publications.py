"""Unified immutable publications, dependency index, and withdrawal ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005_publications"
down_revision = "0004a_topic_attempt_ranges"
branch_labels = None
depends_on = None


def _immutable(table):
    op.execute(f"""
        CREATE TRIGGER {table}_immutable
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER {table}_no_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def upgrade():
    op.create_table(
        "publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "supersedes",
            UUID(as_uuid=True),
            sa.ForeignKey("publications.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "market_snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "topic_snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("topic_snapshots.id", ondelete="RESTRICT"),
        ),
        sa.Column("news_input_id", UUID(as_uuid=True)),
        sa.Column("regulation_input_id", UUID(as_uuid=True)),
        sa.Column("role_input_id", UUID(as_uuid=True)),
        sa.Column("domains", JSONB, nullable=False),
        sa.Column("coverage", JSONB, nullable=False),
        sa.Column("quality_flags", JSONB, nullable=False),
        sa.Column("command_json", sa.Text, nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("trade_date", "version"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("status IN ('draft', 'published', 'revoked')"),
        sa.CheckConstraint(
            "(status = 'draft' AND published_at IS NULL) "
            "OR (status IN ('published', 'revoked') AND published_at IS NOT NULL)",
            name="publications_status_published_at",
        ),
    )
    op.create_index(
        "publications_listing",
        "publications",
        [
            sa.text("trade_date DESC"),
            sa.text("version DESC"),
            sa.text("id ASC"),
        ],
    )
    op.create_table(
        "publication_results",
        sa.Column(
            "publication_id",
            UUID(as_uuid=True),
            sa.ForeignKey("publications.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("domain", sa.String(20), primary_key=True),
        sa.Column("input_id", UUID(as_uuid=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("coverage", JSONB, nullable=False),
        sa.Column("quality_flags", JSONB, nullable=False),
        sa.CheckConstraint(
            "domain IN ('market', 'topic', 'news', 'regulation', 'role')"
        ),
        sa.CheckConstraint("status IN ('ready', 'missing', 'disabled')"),
    )
    op.create_table(
        "publication_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column(
            "publication_id",
            UUID(as_uuid=True),
            sa.ForeignKey("publications.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('failed', 'published')"),
        sa.CheckConstraint(
            "(status = 'published' AND publication_id IS NOT NULL) "
            "OR (status = 'failed' AND publication_id IS NULL)"
        ),
    )
    op.create_index(
        "publication_attempts_published_key",
        "publication_attempts",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "source_dependencies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dependency_sequence",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("archive_type", sa.String(40), nullable=False),
        sa.Column("archive_id", UUID(as_uuid=True), nullable=False),
        sa.Column("derived_type", sa.String(40), nullable=False),
        sa.Column("derived_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "publication_id",
            UUID(as_uuid=True),
            sa.ForeignKey("publications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id",
            "archive_type",
            "archive_id",
            "derived_type",
            "derived_id",
        ),
    )
    op.create_index(
        "source_dependencies_source",
        "source_dependencies",
        ["source_id", "publication_id"],
    )
    op.create_index(
        "source_dependencies_source_sequence",
        "source_dependencies",
        ["source_id", "dependency_sequence"],
    )
    op.create_table(
        "withdrawal_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "sequence",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
            unique=True,
        ),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("policy_reference", sa.Text),
        sa.Column("cutoff_dependency_sequence", sa.BigInteger),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope IN ('display', 'storage')"),
        sa.CheckConstraint(
            "action IN ('blocked', 'deidentified', 'deleted', 'replayed', 'restored')"
        ),
        sa.CheckConstraint(
            "(action IN ('deidentified', 'deleted') "
            "AND scope = 'storage' AND policy_reference IS NOT NULL "
            "AND cutoff_dependency_sequence IS NOT NULL) "
            "OR (action IN ('blocked', 'replayed', 'restored') "
            "AND policy_reference IS NULL "
            "AND cutoff_dependency_sequence IS NULL)",
            name="withdrawal_records_scope_action_policy",
        ),
    )
    op.create_index(
        "withdrawal_records_source",
        "withdrawal_records",
        ["source_id", "sequence"],
    )
    op.create_table(
        "withdrawal_dependencies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "withdrawal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("withdrawal_records.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_dependency_id",
            UUID(as_uuid=True),
            sa.ForeignKey("source_dependencies.id", ondelete="RESTRICT"),
        ),
        sa.Column("archive_type", sa.String(40)),
        sa.Column("archive_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("withdrawal_id", "source_dependency_id"),
        sa.UniqueConstraint("withdrawal_id", "archive_type", "archive_id"),
        sa.CheckConstraint(
            "(source_dependency_id IS NOT NULL "
            "AND archive_type IS NULL AND archive_id IS NULL) "
            "OR (source_dependency_id IS NULL "
            "AND archive_type IS NOT NULL AND archive_id IS NOT NULL)"
        ),
    )
    op.create_index(
        "withdrawal_dependencies_archive",
        "withdrawal_dependencies",
        ["archive_type", "archive_id", "withdrawal_id"],
    )
    op.create_index(
        "withdrawal_dependencies_source_dependency",
        "withdrawal_dependencies",
        ["source_dependency_id", "withdrawal_id"],
    )
    op.create_table(
        "publication_module_controls",
        sa.Column("source_id", sa.Text, primary_key=True),
        sa.Column("module", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("blocked_by_withdrawal", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION guard_publication_attempt_authority() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'published'
               AND NOT EXISTS (
                   SELECT 1
                     FROM publications publication
                    WHERE publication.id = NEW.publication_id
                      AND publication.status = 'published'
                      AND publication.command_hash = NEW.command_hash
               )
            THEN
                RAISE EXCEPTION 'invalid published attempt authority';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publication_attempts_authority
        BEFORE INSERT OR UPDATE ON publication_attempts
        FOR EACH ROW EXECUTE FUNCTION guard_publication_attempt_authority();
    """)
    op.execute("""
        CREATE FUNCTION guard_withdrawal_dependency_insert() RETURNS trigger AS $$
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
             WHERE id = NEW.withdrawal_id;
            IF withdrawal_action NOT IN ('deidentified', 'deleted')
               OR withdrawal_scope <> 'storage'
               OR withdrawal_policy IS NULL
               OR withdrawal_cutoff IS NULL
            THEN
                RAISE EXCEPTION 'withdrawal cannot bind dependencies';
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
                    IF dependency.derived_type <> 'market'
                       OR NOT EXISTS (
                           SELECT 1
                             FROM publications publication
                             JOIN snapshots snapshot
                               ON snapshot.id = publication.market_snapshot_id
                            WHERE publication.id = dependency.publication_id
                              AND snapshot.batch_id = dependency.archive_id
                              AND snapshot.source_id = dependency.source_id
                       )
                    THEN
                        RAISE EXCEPTION 'invalid market withdrawal dependency';
                    END IF;
                ELSIF dependency.archive_type = 'topic_import_batch' THEN
                    SELECT source_id
                      INTO STRICT archive_source
                      FROM topic_import_batches
                     WHERE id = dependency.archive_id;
                    IF dependency.derived_type <> 'topic'
                       OR NOT EXISTS (
                           SELECT 1
                             FROM publications publication
                             JOIN topic_snapshots snapshot
                               ON snapshot.id = publication.topic_snapshot_id
                            WHERE publication.id = dependency.publication_id
                              AND snapshot.batch_id = dependency.archive_id
                              AND snapshot.source_id = dependency.source_id
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
        CREATE TRIGGER withdrawal_dependencies_insert_guard
        BEFORE INSERT ON withdrawal_dependencies
        FOR EACH ROW EXECUTE FUNCTION guard_withdrawal_dependency_insert();
    """)
    op.execute("""
        CREATE FUNCTION guard_publication_transition() RETURNS trigger AS $$
        DECLARE
            result_count integer;
            invalid_result_count integer;
            market_dependency_count integer;
            valid_market_dependency_count integer;
            topic_dependency_count integer;
            valid_topic_dependency_count integer;
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

                SELECT count(*) FILTER (
                           WHERE dependency.derived_type = 'market'
                              OR dependency.archive_type = 'market_import_batch'
                       ),
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

                SELECT count(*) FILTER (
                           WHERE dependency.derived_type = 'topic'
                              OR dependency.archive_type = 'topic_import_batch'
                       ),
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
        CREATE TRIGGER publications_transition
        BEFORE INSERT OR UPDATE OR DELETE ON publications
        FOR EACH ROW EXECUTE FUNCTION guard_publication_transition();
        CREATE TRIGGER publications_no_truncate
        BEFORE TRUNCATE ON publications
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)
    op.execute("""
        CREATE FUNCTION guard_publication_result_retention() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.publication_id IS NOT DISTINCT FROM OLD.publication_id
               AND NEW.domain IS NOT DISTINCT FROM OLD.domain
               AND NEW.input_id IS NOT DISTINCT FROM OLD.input_id
               AND NEW.status = 'disabled'
               AND NEW.payload = '{}'::jsonb
               AND NEW.coverage = '{}'::jsonb
               AND NEW.quality_flags = '["withdrawn"]'::jsonb
               AND EXISTS (
                   SELECT 1
                   FROM withdrawal_dependencies dependency
                   JOIN source_dependencies source_dependency
                     ON source_dependency.id = dependency.source_dependency_id
                   JOIN withdrawal_records withdrawal
                     ON withdrawal.id = dependency.withdrawal_id
                   WHERE source_dependency.publication_id = OLD.publication_id
                     AND source_dependency.derived_type = OLD.domain
                     AND withdrawal.scope = 'storage'
                     AND withdrawal.action = 'deidentified'
                     AND withdrawal.policy_reference IS NOT NULL
               )
            THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE'
               AND EXISTS (
                   SELECT 1
                   FROM withdrawal_dependencies dependency
                   JOIN source_dependencies source_dependency
                     ON source_dependency.id = dependency.source_dependency_id
                   JOIN withdrawal_records withdrawal
                     ON withdrawal.id = dependency.withdrawal_id
                   WHERE source_dependency.publication_id = OLD.publication_id
                     AND source_dependency.derived_type = OLD.domain
                     AND withdrawal.scope = 'storage'
                     AND withdrawal.action = 'deleted'
                     AND withdrawal.policy_reference IS NOT NULL
               )
            THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'immutable records cannot be changed';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER publication_results_retention
        BEFORE UPDATE OR DELETE ON publication_results
        FOR EACH ROW EXECUTE FUNCTION guard_publication_result_retention();
        CREATE TRIGGER publication_results_no_truncate
        BEFORE TRUNCATE ON publication_results
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)
    for table in (
        "publication_attempts",
        "source_dependencies",
        "withdrawal_records",
        "withdrawal_dependencies",
    ):
        _immutable(table)


def downgrade():
    raise RuntimeError("Publication history cannot be destructively downgraded")
