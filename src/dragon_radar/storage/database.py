"""Explicit PostgreSQL lifecycle. Construction never opens a connection."""

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

SCHEMA_REVISION = "0010_ai_tasks"


class Database:
    def __init__(self, database_url: str | None = None):
        value = database_url or os.environ.get("DRAGON_DATABASE_URL")
        if not value:
            raise ValueError("DRAGON_DATABASE_URL is required")
        try:
            url = make_url(value)
        except Exception:
            raise ValueError("Invalid database configuration") from None
        if url.drivername not in ("postgresql", "postgresql+psycopg"):
            raise ValueError("PostgreSQL with psycopg is required")
        self.engine = create_engine(
            url.set(drivername="postgresql+psycopg"),
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )

    def migrate(self) -> None:
        config = Config()
        config.set_main_option("script_location", "dragon_radar:migrations")
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "dragon-radar:migrations"},
            )
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                if connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) != (SCHEMA_REVISION):
                    return False
                for table in (
                    "source_grants",
                    "import_batches",
                    "snapshots",
                    "audit_events",
                    "owner_principals",
                    "owner_pairings",
                    "owner_sessions",
                    "owner_pairing_limits",
                    "watch_entries",
                    "review_notes",
                    "review_note_revisions",
                    "review_verifications",
                    "owner_commands",
                    "private_audit_events",
                    "topic_import_attempts",
                    "topic_import_batches",
                    "topic_snapshots",
                    "publications",
                    "publication_results",
                    "publication_attempts",
                    "source_dependencies",
                    "withdrawal_records",
                    "withdrawal_dependencies",
                    "withdrawal_binding_seals",
                    "publication_module_controls",
                    "market_import_tasks",
                    "market_import_attempts",
                    "security",
                    "security_state_history",
                    "daily_kline",
                    "index_daily_kline",
                    "limit_up_record",
                    "role_runs",
                    "role_run_topics",
                    "role_topic_statuses",
                    "role_results",
                    "role_factor_observations",
                    "role_limit_rows",
                    "role_change_summaries",
                    "role_changes",
                    "role_pending_validations",
                    "role_validations",
                    "role_validation_inputs",
                    "role_validation_commands",
                    "role_materialization_attempts",
                    "analysis_jobs",
                    "analysis_job_cache",
                    "news_articles",
                    "news_article_revisions",
                    "news_events",
                    "news_event_articles",
                    "news_associations",
                    "news_ingest_commands",
                    "news_association_reviews",
                    "news_review_commands",
                    "news_review_audit_events",
                    "news_snapshots",
                    "news_snapshot_items",
                    "regulation_rules",
                    "regulation_rule_statuses",
                    "regulation_events",
                    "regulation_snapshots",
                    "regulation_snapshot_rules",
                    "regulation_snapshot_inputs",
                    "regulation_calculations",
                    "regulation_snapshot_events",
                    "regulation_commands",
                    "ai_tasks",
                    "ai_task_drafts",
                    "ai_task_cancellations",
                    "ai_task_reviews",
                ):
                    connection.execute(text(f"SELECT 1 FROM {table} LIMIT 0"))
            return True
        except SQLAlchemyError:
            return False

    def close(self) -> None:
        self.engine.dispose()
