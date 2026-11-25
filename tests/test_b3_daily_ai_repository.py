"""Registry mapping and additive migration contract."""


def test_sidecar_tables_do_not_add_publication_domain():
    from dragon_radar.public_daily_ai.tables import OWNED_TABLES

    assert {t.name for t in OWNED_TABLES} == {
        "ai_output_entitlements",
        "ai_output_entitlement_revocations",
        "public_daily_ai_archives",
        "promoted_news_summaries",
        "ai_artifact_private_links",
        "ai_artifact_revocations",
        "ai_artifact_verifications",
    }
    assert all(t.name != "publication_results" for t in OWNED_TABLES)


def test_migration_is_additive_and_backup_restore_only():
    import importlib

    migration = importlib.import_module(
        "dragon_radar.migrations.versions.0015_daily_ai"
    )
    assert migration.down_revision == "0014_research"
    import pytest

    with pytest.raises(RuntimeError):
        migration.downgrade()


def test_every_new_foreign_key_resolves_existing_table():
    from dragon_radar.public_daily_ai.tables import OWNED_TABLES

    for table in OWNED_TABLES:
        for foreign_key in table.foreign_keys:
            assert foreign_key.column is not None
            assert not foreign_key.target_fullname.startswith("owners.")


def test_upgrade_from_previous_head_and_existing_live_metadata(database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    from dragon_radar.public_daily_ai.tables import OWNED_TABLES
    from dragon_radar.storage.database import Database

    database = Database(database_url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "dragon-radar:migrations"},
            )
            config = Config()
            config.set_main_option("script_location", "dragon_radar:migrations")
            config.attributes["connection"] = connection
            command.upgrade(config, "0014_research")
            # Simulate prior migrations creating tables using live metadata.
            for table in OWNED_TABLES:
                table.create(connection, checkfirst=True)
            command.upgrade(config, "0015_daily_ai")
            inspector = inspect(connection)
            assert {table.name for table in OWNED_TABLES} <= set(
                inspector.get_table_names()
            )
            for table in OWNED_TABLES:
                assert set(table.c.keys()) == {
                    column["name"] for column in inspector.get_columns(table.name)
                }
    finally:
        database.close()
