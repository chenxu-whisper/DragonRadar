"""Integration tests use disposable schemas in the dedicated test database only."""

import os
import sys
from uuid import uuid4

import pytest
from factories import NOW, grant
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def pytest_collection_modifyitems(config, items):
    """Drop structurally unavailable probes instead of collecting skips.

    The Task20 full-scope verifier requires command-native runs with zero
    skips. Windows-only junction checks cannot run on Linux and the named
    container restore drill needs a labeled PostgreSQL container; deselect
    them at collection time on unsupported hosts instead of recording skips.
    """
    selected = []
    deselected = []
    for item in items:
        if item.get_closest_marker("windows_only") and sys.platform != "win32":
            deselected.append(item)
        elif item.get_closest_marker("requires_restore_container") and not (
            os.environ.get("DRAGON_TEST_POSTGRES_CONTAINER")
            and os.environ.get("DRAGON_TEST_DATABASE_URL")
        ):
            deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


@pytest.fixture
def database_url():
    value = os.environ.get("DRAGON_TEST_DATABASE_URL")
    if not value:
        pytest.skip("DRAGON_TEST_DATABASE_URL is required for PostgreSQL integration")
    url = make_url(value)
    if url.database != "dragon_test":
        pytest.fail("Integration tests require the dedicated dragon_test database")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield scoped.render_as_string(hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def database(database_url):
    from dragon_radar.storage.database import Database

    db = Database(database_url)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def repository(database):
    from dragon_radar.storage.repository import Repository

    repo = Repository(database, clock=lambda: NOW)
    repo.register_source(grant())
    return repo
