"""Regression coverage for the append-only topic migration chain."""

from alembic import command
from alembic.config import Config
from factories import NOW, grant
from sqlalchemy import inspect, text
from topic_factories import topic_batch


def _upgrade(database, revision):
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_topic_attempt_ranges_are_added_after_0004(database_url):
    from dragon_radar.storage.database import Database
    from dragon_radar.storage.repository import Repository
    from dragon_radar.topics.repository import TopicRepository

    database = Database(database_url)
    try:
        _upgrade(database, "0004_topic_snapshots")
        old_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns("topic_import_attempts")
        }
        assert "request_from" not in old_columns
        assert "request_to" not in old_columns

        _upgrade(database, "head")
        with database.engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0004a_topic_attempt_ranges"
            )
        new_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns("topic_import_attempts")
        }
        assert {"request_from", "request_to"} <= new_columns

        Repository(database, clock=lambda: NOW).register_source(grant())
        repository = TopicRepository(database, clock=lambda: NOW)
        payload = topic_batch()
        record = repository.import_batch(payload, key="post-migration-import")
        attempts = repository.list_attempts(status="succeeded")
        assert len(attempts) == 1
        assert repository.get_attempt(attempts[0].id) == attempts[0]
        assert attempts[0].request_from == payload.page_manifest.request_from
        assert attempts[0].request_to == payload.page_manifest.request_to
        assert record.id is not None
    finally:
        database.close()
