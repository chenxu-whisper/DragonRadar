"""Unified publication manifests and atomic materialization."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch, grant
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, inspect, select, text
from topic_factories import topic_batch

from dragon_radar.publication.models import (
    DomainMaterialization,
    PublicationCommand,
)
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import (
    publication_attempts,
    publication_results,
    publications,
)
from dragon_radar.storage.models import source_grants
from dragon_radar.storage.repository import Conflict, Forbidden
from dragon_radar.topics.repository import TopicRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def published_inputs(database, repository):
    topic_repository = TopicRepository(database, clock=lambda: NOW)
    market = repository.publish(
        repository.import_batch(batch(), key="publication-market").id
    )
    topic = topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="publication-topic").id
    )
    return market, topic


@pytest.fixture
def publication(database, repository, published_inputs):
    market, topic = published_inputs
    service = PublicationService(database, repository, clock=lambda: NOW)
    command = PublicationCommand(
        trade_date=DAYS[-1],
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        disabled_domains=("news", "regulation", "role"),
    )
    return service, command


@asynccontextmanager
async def workbench(repository):
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=repository)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


def test_migration_creates_publication_schema_and_readiness(database):
    assert database.ready()
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "publication_attempts",
        "publication_results",
        "publications",
        "source_dependencies",
        "withdrawal_records",
        "publication_module_controls",
    } <= tables
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0005_publications"
        )


def test_domain_failure_keeps_attempt_without_visible_partial_publication(
    publication, database
):
    service, command = publication

    def fail(_connection, _command):
        raise RuntimeError("injected topic materialization failure")

    with pytest.raises(RuntimeError, match="injected"):
        service.publish(
            command,
            key="atomic-failure",
            materializers={
                "market": lambda _connection, _command: DomainMaterialization(
                    payload={"ready": True}
                ),
                "topic": fail,
            },
        )

    with database.engine.connect() as connection:
        assert (
            connection.scalar(select(text("count(*)")).select_from(publications)) == 0
        )
        assert (
            connection.scalar(select(text("count(*)")).select_from(publication_results))
            == 0
        )
        attempt = connection.execute(select(publication_attempts)).mappings().one()
        assert attempt["status"] == "failed"
        assert attempt["stage"] == "topic"
        assert attempt["publication_id"] is None


def test_replay_is_idempotent_conflicting_key_rejected_and_revision_appends(
    publication, database, repository
):
    service, command = publication
    first = service.publish(command, key="publish-v1")
    replay = service.publish(command, key="publish-v1")
    assert replay == first

    changed_market = repository.publish(
        repository.import_batch(batch(source_time=NOW), key="publication-market-v2").id
    )
    changed = command.model_copy(update={"market_snapshot_id": changed_market.id})
    with pytest.raises(Conflict):
        service.publish(changed, key="publish-v1")

    second = service.publish(changed, key="publish-v2")
    assert second.id != first.id
    assert (second.version, second.supersedes) == (2, first.id)
    assert first.status == second.status == "published"
    assert first.domains == {
        "market": "ready",
        "topic": "ready",
        "news": "disabled",
        "regulation": "disabled",
        "role": "disabled",
    }
    with database.engine.connect() as connection:
        assert (
            connection.scalar(select(text("count(*)")).select_from(publications)) == 2
        )
        assert (
            connection.scalar(
                select(text("count(*)")).select_from(publication_attempts)
            )
            == 2
        )


def test_missing_domains_are_explicit_and_materialized_results_are_stored(publication):
    service, command = publication
    command = command.model_copy(update={"disabled_domains": ("regulation",)})
    record = service.publish(command, key="domain-statuses")
    assert record.domains == {
        "market": "ready",
        "topic": "ready",
        "news": "missing",
        "regulation": "disabled",
        "role": "missing",
    }
    assert record.coverage["market"]["expected"] == 1
    assert record.quality_flags == ()
    results = service.repository.list_results(record.id)
    assert {(item.domain, item.status) for item in results} == {
        ("market", "ready"),
        ("topic", "ready"),
        ("news", "missing"),
        ("regulation", "disabled"),
        ("role", "missing"),
    }


@pytest.mark.anyio
async def test_fixed_publication_and_old_snapshot_ids_remain_pinned(
    publication, database, repository
):
    service, command = publication
    first = service.publish(command, key="selection-v1")
    repository.register_source(grant())
    changed_market = repository.publish(
        repository.import_batch(batch(source_time=NOW), key="selection-market-v2").id
    )
    second = service.publish(
        command.model_copy(update={"market_snapshot_id": changed_market.id}),
        key="selection-v2",
    )
    selection = PublicationRepository(database, repository, clock=lambda: NOW)

    async with workbench(selection) as client:
        pinned = (
            await client.get(
                "/api/market/overview", params={"snapshot_id": str(first.id)}
            )
        ).json()
        latest = (
            await client.get(
                "/api/market/overview", params={"date": DAYS[-1].isoformat()}
            )
        ).json()
        legacy = (
            await client.get(
                "/api/market/overview",
                params={"snapshot_id": str(command.market_snapshot_id)},
            )
        ).json()
        unknown = await client.get(
            "/api/market/overview", params={"snapshot_id": str(uuid4())}
        )

    assert pinned["snapshot_id"] == str(first.id)
    assert latest["snapshot_id"] == str(second.id)
    assert legacy["snapshot_id"] == str(command.market_snapshot_id)
    assert unknown.status_code == 200
    assert unknown.json()["data"] is None


@pytest.mark.anyio
async def test_current_display_permission_is_rechecked_for_list_and_fixed_read(
    publication, database, repository
):
    service, command = publication
    record = service.publish(command, key="permission")
    selection = PublicationRepository(database, repository, clock=lambda: NOW)

    async with workbench(selection) as client:
        assert (
            await client.get(
                "/api/market/overview", params={"snapshot_id": str(record.id)}
            )
        ).status_code == 200
        repository.register_source(grant(local_display=False))
        denied = await client.get(
            "/api/market/overview", params={"snapshot_id": str(record.id)}
        )
        listing = await client.get("/api/snapshots/list")

    assert denied.status_code == 403
    assert listing.json()["data"] == {"snapshots": []}
    with pytest.raises(Forbidden):
        selection.get_snapshot(record.id)


def test_publication_command_json_is_canonical_and_does_not_invent_inputs(publication):
    service, command = publication
    record = service.publish(command, key="canonical")
    payload = json.loads(record.command_json)
    assert payload["market_snapshot_id"] == str(command.market_snapshot_id)
    assert payload["topic_snapshot_id"] == str(command.topic_snapshot_id)
    assert payload["news_input_id"] is None
    assert payload["regulation_input_id"] is None
    assert payload["role_input_id"] is None


def test_publication_cli_replays_same_command(
    database, repository, published_inputs, tmp_path
):
    from test_cli import run_cli

    market, topic = published_inputs
    path = tmp_path / "publication.json"
    path.write_text(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            disabled_domains=("news", "regulation", "role"),
        ).model_dump_json(),
        encoding="utf-8",
    )
    database_url = database.engine.url.render_as_string(hide_password=False)
    first = run_cli(
        ["publication-publish", str(path), "--key", "publication-cli"],
        database_url,
    )
    replay = run_cli(
        ["publication-publish", str(path), "--key", "publication-cli"],
        database_url,
    )
    assert first.returncode == replay.returncode == 0
    first_body = json.loads(first.stdout)
    assert json.loads(replay.stdout) == first_body
    assert set(first_body) == {
        "published_at",
        "snapshot_id",
        "status",
        "supersedes",
        "trade_date",
        "version",
    }


def test_migration_upgrades_0004a_without_rewriting_history(database_url):
    from alembic import command as alembic_command
    from alembic.config import Config

    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            alembic_command.upgrade(config, "0004a_topic_attempt_ranges")
            connection.execute(
                insert(source_grants).values(
                    source_id="migration-sentinel",
                    grant={},
                    updated_at=NOW,
                )
            )
        database.migrate()
        with database.engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM source_grants "
                        "WHERE source_id = 'migration-sentinel'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0005_publications"
            )
    finally:
        database.close()
