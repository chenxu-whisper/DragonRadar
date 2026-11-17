"""Alembic works from the installed package, not the current working directory."""

from alembic import context

from dragon_radar.storage.database import Database


def run(connection):
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    database = Database()
    try:
        with database.engine.begin() as connection:
            run(connection)
    finally:
        database.close()
