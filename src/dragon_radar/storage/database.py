"""Explicit PostgreSQL lifecycle. Construction never opens a connection."""

import os

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from dragon_radar.storage.schema_contract import (
    capture_schema_contract,
    compare_schema_contract,
    load_packaged_schema_contract,
)

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
                expected = load_packaged_schema_contract()
                actual = capture_schema_contract(connection)
                return not compare_schema_contract(expected, actual)
        except Exception:
            return False

    def close(self) -> None:
        self.engine.dispose()
