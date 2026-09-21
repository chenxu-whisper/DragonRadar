"""Unified publication manifests and atomic materialization."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch, grant
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, inspect, select, text
from sqlalchemy.exc import DBAPIError
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
    source_dependencies,
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
        "withdrawal_dependencies",
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


def test_concurrent_failure_is_recorded_after_same_key_success(
    publication, database, repository
):
    _service, command = publication
    failing_service = PublicationService(database, repository, clock=lambda: NOW)
    successful_service = PublicationService(database, repository, clock=lambda: NOW)
    failure_ready = Event()
    success_done = Event()
    original_lock = failing_service.market._lock
    key_lock_calls = 0

    def controlled_lock(connection, key):
        nonlocal key_lock_calls
        if key == "publication-key:concurrent-attempt":
            key_lock_calls += 1
            if key_lock_calls == 2:
                failure_ready.set()
                assert success_done.wait(5)
        return original_lock(connection, key)

    def fail(_connection, _command):
        raise RuntimeError("controlled materialization failure")

    failing_service.market._lock = controlled_lock
    with ThreadPoolExecutor(max_workers=1) as pool:
        failed = pool.submit(
            failing_service.publish,
            command,
            key="concurrent-attempt",
            materializers={"topic": fail},
        )
        assert failure_ready.wait(5)
        try:
            published = successful_service.publish(command, key="concurrent-attempt")
        finally:
            success_done.set()
        with pytest.raises(RuntimeError, match="controlled"):
            failed.result()

    assert (
        successful_service.publish(command, key="concurrent-attempt").id == published.id
    )
    with pytest.raises(Conflict):
        successful_service.publish(
            command.model_copy(update={"trade_date": DAYS[0]}),
            key="concurrent-attempt",
        )
    with database.engine.connect() as connection:
        attempts = (
            connection.execute(
                select(publication_attempts)
                .where(publication_attempts.c.idempotency_key == "concurrent-attempt")
                .order_by(publication_attempts.c.status)
            )
            .mappings()
            .all()
        )
    assert [attempt["status"] for attempt in attempts] == ["failed", "published"]
    assert all(
        attempt["command_hash"] == attempts[0]["command_hash"] for attempt in attempts
    )


def test_published_attempt_is_authoritative_after_different_command_fails(
    publication, database, repository
):
    _service, failed_command = publication
    published_command = failed_command.model_copy(
        update={
            "topic_snapshot_id": None,
            "disabled_domains": ("topic", "news", "regulation", "role"),
        }
    )
    failing_service = PublicationService(database, repository, clock=lambda: NOW)
    successful_service = PublicationService(database, repository, clock=lambda: NOW)
    failure_ready = Event()
    success_done = Event()
    original_lock = failing_service.market._lock
    key_lock_calls = 0

    def controlled_lock(connection, key):
        nonlocal key_lock_calls
        if key == "publication-key:different-concurrent-attempt":
            key_lock_calls += 1
            if key_lock_calls == 2:
                failure_ready.set()
                assert success_done.wait(5)
        return original_lock(connection, key)

    def fail(_connection, _command):
        raise RuntimeError("controlled materialization failure")

    failing_service.market._lock = controlled_lock
    with ThreadPoolExecutor(max_workers=1) as pool:
        failed = pool.submit(
            failing_service.publish,
            failed_command,
            key="different-concurrent-attempt",
            materializers={"topic": fail},
        )
        assert failure_ready.wait(5)
        try:
            published = successful_service.publish(
                published_command,
                key="different-concurrent-attempt",
            )
        finally:
            success_done.set()
        with pytest.raises(RuntimeError, match="controlled"):
            failed.result()

    assert (
        successful_service.publish(
            published_command,
            key="different-concurrent-attempt",
        ).id
        == published.id
    )
    with pytest.raises(Conflict):
        successful_service.publish(
            failed_command,
            key="different-concurrent-attempt",
        )


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


@pytest.mark.parametrize(
    ("status", "publication_reference", "command_hash"),
    [
        ("published", False, "0" * 64),
        ("failed", True, "0" * 64),
        ("published", True, "f" * 64),
    ],
)
def test_database_rejects_invalid_publication_attempt_authority(
    publication,
    database,
    status,
    publication_reference,
    command_hash,
):
    service, command = publication
    record = service.publish(command, key="attempt-authority-source")

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(publication_attempts).values(
                id=uuid4(),
                idempotency_key="forged-attempt",
                command_hash=command_hash,
                publication_id=record.id if publication_reference else None,
                status=status,
                stage=status,
                details={},
                created_at=NOW,
            )
        )


def test_database_rejects_published_attempt_for_draft_publication(
    publication, database
):
    service, command = publication
    record = service.publish(command, key="attempt-draft-source")
    draft_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=("market", "topic"),
        status="draft",
    )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(publication_attempts).values(
                id=uuid4(),
                idempotency_key="forged-draft-attempt",
                command_hash=hashlib.sha256(
                    command.canonical_json().encode()
                ).hexdigest(),
                publication_id=draft_id,
                status="published",
                stage="published",
                details={},
                created_at=NOW,
            )
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE publications SET command_hash = repeat('f', 64) WHERE id = :id",
        "UPDATE publications SET status = 'draft', published_at = NULL WHERE id = :id",
    ],
)
def test_replay_rejects_inconsistent_published_attempt(publication, database, tamper):
    service, command = publication
    record = service.publish(command, key="defensive-replay")
    with database.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE publications DISABLE TRIGGER publications_transition")
        )
        connection.execute(text(tamper), {"id": record.id})

    with pytest.raises(Conflict):
        service.publish(command, key="defensive-replay")


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
async def test_default_workbench_repository_reads_unified_publications(
    publication, database_url
):
    service, command = publication
    record = service.publish(command, key="default-workbench")
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(database_url=database_url)
    assert isinstance(app.state.repository, PublicationRepository)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        response = await client.get(
            "/api/market/overview", params={"snapshot_id": str(record.id)}
        )

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == str(record.id)


@pytest.mark.anyio
async def test_unified_overview_uses_publish_time_materialization(
    publication, database, repository, monkeypatch
):
    service, command = publication
    record = service.publish(command, key="materialized-overview")
    selection = PublicationRepository(database, repository, clock=lambda: NOW)

    def fail_if_recomputed(_batch):
        raise RuntimeError("overview recomputed during read")

    monkeypatch.setattr("dragon_radar.http.app.market_breadth", fail_if_recomputed)
    async with workbench(selection) as client:
        response = await client.get(
            "/api/market/overview", params={"snapshot_id": str(record.id)}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == str(record.id)
    assert body["data"]["advancing"] == 0
    assert body["data"]["unchanged"] == 1


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


def test_unified_read_holds_all_source_locks_through_result_assembly(
    publication, database, repository
):
    service, command = publication
    record = service.publish(command, key="atomic-read")
    selection = PublicationRepository(database, repository, clock=lambda: NOW)
    source_lock_order = []
    assembled_while_locked = []

    def observe_transaction(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement and str(
            parameters.get("key", "")
        ).startswith("source:"):
            source_lock_order.append(parameters["key"])
        if "FROM publication_results" not in statement:
            return
        with pytest.raises(DBAPIError), database.engine.begin() as other:
            other.execute(
                text(
                    "SELECT source_id FROM source_grants "
                    "WHERE source_id = 'synthetic' FOR UPDATE NOWAIT"
                )
            )
        assembled_while_locked.append(True)

    event.listen(database.engine, "after_cursor_execute", observe_transaction)
    try:
        snapshot = selection.get_snapshot(record.id)
    finally:
        event.remove(database.engine, "after_cursor_execute", observe_transaction)

    assert snapshot.id == record.id
    assert assembled_while_locked == [True]
    assert source_lock_order == sorted(source_lock_order)


def _clone_publication_with_dependencies(
    database,
    record,
    *,
    topic_snapshot_id,
    dependency_domains,
    status="published",
):
    publication_id = uuid4()
    with database.engine.begin() as connection:
        original = (
            connection.execute(
                select(publications).where(publications.c.id == record.id)
            )
            .mappings()
            .one()
        )
        values = dict(original)
        values.update(
            id=publication_id,
            version=record.version + 1,
            supersedes=record.id,
            topic_snapshot_id=topic_snapshot_id,
            status=status,
        )
        connection.execute(insert(publications).values(**values))
        results = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == record.id
                )
            )
            .mappings()
            .all()
        )
        for result in results:
            values = dict(result)
            values["publication_id"] = publication_id
            connection.execute(insert(publication_results).values(**values))
        dependencies = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == record.id,
                    source_dependencies.c.derived_type.in_(dependency_domains),
                )
            )
            .mappings()
            .all()
        )
        for dependency in dependencies:
            values = dict(dependency)
            values.update(
                id=uuid4(),
                derived_id=publication_id,
                publication_id=publication_id,
            )
            connection.execute(insert(source_dependencies).values(**values))
    return publication_id


@pytest.mark.parametrize("status", ["draft", "revoked"])
def test_list_results_rejects_nonpublished_publications(publication, database, status):
    service, command = publication
    record = service.publish(command, key=f"results-{status}")
    hidden_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=("market", "topic"),
        status=status,
    )

    with pytest.raises(Forbidden):
        service.repository.list_results(hidden_id)


def test_unified_read_rejects_missing_topic_dependency(publication, database):
    service, command = publication
    record = service.publish(command, key="missing-topic-dependency")
    tampered_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=("market",),
    )

    with pytest.raises(Forbidden):
        service.repository.get_snapshot(tampered_id)


def test_unified_read_rejects_topic_snapshot_dependency_mismatch(publication, database):
    service, command = publication
    record = service.publish(command, key="mismatched-topic-dependency")
    topics = TopicRepository(database, clock=lambda: NOW)
    other_topic = topics.publish(
        topics.import_batch(
            topic_batch(source_time=NOW),
            key="mismatched-topic-snapshot",
        ).id
    )
    tampered_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=other_topic.id,
        dependency_domains=("market", "topic"),
    )

    with pytest.raises(Forbidden):
        service.repository.get_snapshot(tampered_id)


def test_unified_read_rejects_duplicate_topic_dependencies(publication, database):
    service, command = publication
    record = service.publish(command, key="duplicate-topic-dependency")
    topics = TopicRepository(database, clock=lambda: NOW)
    other_topic = topics.publish(
        topics.import_batch(
            topic_batch(source_time=NOW),
            key="duplicate-topic-snapshot",
        ).id
    )
    with database.engine.begin() as connection:
        other_snapshot = topics.get_snapshot(other_topic.id)
        connection.execute(
            insert(source_dependencies).values(
                id=uuid4(),
                source_id=other_snapshot.batch.source_id,
                archive_type="topic_import_batch",
                archive_id=other_snapshot.batch_id,
                derived_type="topic",
                derived_id=record.id,
                publication_id=record.id,
                created_at=NOW,
            )
        )

    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)


def test_publication_validation_locks_distinct_sources_in_stable_order(
    database, repository
):
    topic_grant = grant(source_id="aaa-topic")
    repository.register_source(topic_grant)
    market = repository.publish(
        repository.import_batch(batch(), key="stable-market").id
    )
    topic_repository = TopicRepository(database, clock=lambda: NOW)
    topic = topic_repository.publish(
        topic_repository.import_batch(
            topic_batch(source_id="aaa-topic", grant=topic_grant),
            key="stable-topic",
        ).id
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    source_lock_order = []

    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        key = str(parameters.get("key", ""))
        if "pg_advisory_xact_lock" in statement and key.startswith("source:"):
            source_lock_order.append(key)

    event.listen(database.engine, "after_cursor_execute", observe_lock)
    try:
        service.publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=market.id,
                topic_snapshot_id=topic.id,
                disabled_domains=("news", "regulation", "role"),
            ),
            key="stable-source-order",
        )
    finally:
        event.remove(database.engine, "after_cursor_execute", observe_lock)

    assert source_lock_order == [
        "source:aaa-topic",
        "source:synthetic",
        "source:aaa-topic",
        "source:synthetic",
    ]


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
