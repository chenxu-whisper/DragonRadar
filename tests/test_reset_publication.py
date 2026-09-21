"""Unified publication manifests and atomic materialization."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Event
from uuid import uuid4

import pytest
from factories import DAYS, NOW, bar, batch, grant
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, insert, inspect, select, text, update
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
from dragon_radar.storage.database import SCHEMA_REVISION
from dragon_radar.storage.models import import_batches, snapshots, source_grants
from dragon_radar.storage.repository import Conflict, Forbidden, QualityRejected
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
    inspector = inspect(database.engine)
    tables = set(inspector.get_table_names())
    assert {
        "publication_attempts",
        "publication_results",
        "publications",
        "source_dependencies",
        "withdrawal_records",
        "withdrawal_dependencies",
        "withdrawal_binding_seals",
        "publication_module_controls",
    } <= tables
    publication_columns = {
        column["name"]: column for column in inspector.get_columns("publications")
    }
    assert publication_columns["publication_sequence"]["identity"]["always"] is True
    assert ("publication_sequence",) in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("publications")
    }
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == SCHEMA_REVISION
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


def test_unified_publication_rejects_quarantined_market_archive(
    database, repository
):
    from test_reset_ingestion import licensed_batch

    from dragon_radar.ingestion.runner import MarketIngestionRunner

    quarantined = MarketIngestionRunner(repository).import_batch(
        licensed_batch(contiguous=False),
        key="quarantined-unified-input",
    )
    snapshot_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            insert(snapshots).values(
                id=snapshot_id,
                batch_id=quarantined.batch_id,
                source_id="synthetic",
                trade_date=DAYS[-1],
                revision=1,
                supersedes=None,
                manifest={},
                published_at=NOW,
            )
        )

    service = PublicationService(database, repository, clock=lambda: NOW)
    with pytest.raises(QualityRejected):
        service.publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=snapshot_id,
                disabled_domains=("topic", "news", "regulation", "role"),
            ),
            key="reject-quarantined-unified-input",
        )


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


def test_new_publish_returns_committed_record_without_post_commit_read(
    publication, database, monkeypatch
):
    service, command = publication

    def fail_post_commit_read(_publication_id):
        raise RuntimeError("post-commit authorization read")

    monkeypatch.setattr(
        service.repository,
        "get_publication",
        fail_post_commit_read,
    )

    record = service.publish(command, key="return-without-post-commit-read")

    assert record.status == "published"
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(publications)
                .where(
                    publications.c.id == record.id,
                    publications.c.status == "published",
                )
            )
            == 1
        )
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(publication_results)
                .where(publication_results.c.publication_id == record.id)
            )
            == 5
        )


def test_idempotent_replay_still_rechecks_current_authorization(
    publication, repository
):
    service, command = publication
    service.publish(command, key="replay-rechecks-authorization")
    repository.register_source(grant(local_display=False))

    with pytest.raises(Forbidden):
        service.publish(command, key="replay-rechecks-authorization")


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


def test_list_snapshots_batches_candidates_and_prefetches_related_rows(
    publication, database, repository
):
    service, command = publication
    expected = service.publish(command, key="bounded-list-00")
    for index in range(1, 20):
        market = repository.publish(
            repository.import_batch(
                batch(source_time=NOW),
                key=f"bounded-list-market-{index:02}",
            ).id
        )
        expected = service.publish(
            command.model_copy(update={"market_snapshot_id": market.id}),
            key=f"bounded-list-{index:02}",
        )

    statements = []
    candidate_row_counts = []

    def count_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        if (
            "FROM publications" in statement
            and "publications.status" in statement
            and "ORDER BY publications.trade_date" in statement
        ):
            candidate_row_counts.append(cursor.rowcount)

    event.listen(database.engine, "after_cursor_execute", count_sql)
    try:
        listed = service.repository.list_snapshots(limit=1)
    finally:
        event.remove(database.engine, "after_cursor_execute", count_sql)

    candidate_queries = [
        statement
        for statement in statements
        if "FROM publications" in statement
        and "publications.status" in statement
        and "ORDER BY publications.trade_date" in statement
    ]
    assert [item.id for item in listed] == [expected.id]
    assert candidate_queries
    assert all("LIMIT" in statement for statement in candidate_queries)
    assert candidate_row_counts == [10]
    assert len(statements) <= 14


def test_list_snapshots_applies_offset_after_authorization_filter(
    publication, database, repository
):
    service, command = publication
    first = service.publish(command, key="authorized-offset-first")
    second_market = repository.publish(
        repository.import_batch(
            batch(source_time=NOW),
            key="authorized-offset-second-market",
        ).id
    )
    second = service.publish(
        command.model_copy(update={"market_snapshot_id": second_market.id}),
        key="authorized-offset-second",
    )
    denied_grant = grant(source_id="denied-list-source")
    repository.register_source(denied_grant)
    denied_batch = batch()
    denied_batch = denied_batch.model_copy(
        update={
            "source_id": denied_grant.source_id,
            "grant": denied_grant,
            "security_states": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in denied_batch.security_states
            ),
            "bars": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in denied_batch.bars
            ),
        }
    )
    denied_market = repository.publish(
        repository.import_batch(
            denied_batch,
            key="denied-list-market",
        ).id
    )
    service.publish(
        command.model_copy(update={"market_snapshot_id": denied_market.id}),
        key="denied-list-publication",
    )
    repository.register_source(denied_grant.model_copy(update={"local_display": False}))

    listed = service.repository.list_snapshots(limit=1, offset=1)

    assert [item.id for item in listed] == [first.id]
    assert second.id != first.id


def test_list_snapshots_uses_keyset_between_candidate_transactions(
    publication, database, repository
):
    service, command = publication
    first = service.publish(command, key="keyset-visible-first")
    second = service.publish(command, key="keyset-visible-second")
    denied_grant = grant(source_id="keyset-denied-source")
    repository.register_source(denied_grant)
    denied_batch = batch().model_copy(
        update={
            "source_id": denied_grant.source_id,
            "grant": denied_grant,
            "security_states": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in batch().security_states
            ),
            "bars": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in batch().bars
            ),
        }
    )
    for index in range(9):
        market = repository.publish(
            repository.import_batch(
                denied_batch,
                key=f"keyset-denied-market-{index}",
            ).id
        )
        service.publish(
            command.model_copy(update={"market_snapshot_id": market.id}),
            key=f"keyset-denied-publication-{index}",
        )
    repository.register_source(denied_grant.model_copy(update={"local_display": False}))

    candidate_queries = []
    watermark_queries = []
    concurrent_future = None

    def insert_ahead_after_first_batch(
        conn, cursor, statement, parameters, context, executemany
    ):
        nonlocal concurrent_future
        if (
            concurrent_future is not None
            or "FROM publications" not in statement
            or "publications.status" not in statement
            or "ORDER BY publications.trade_date" not in statement
        ):
            return
        candidate_queries.append(statement)
        concurrent_future = pool.submit(
            PublicationService(database, repository, clock=lambda: NOW).publish,
            command,
            key="keyset-concurrent-insert",
        )

    def await_concurrent_insert(
        conn, cursor, statement, parameters, context, executemany
    ):
        if (
            len(candidate_queries) == 1
            and concurrent_future is not None
            and "FROM publications" in statement
            and "publications.status" in statement
            and "ORDER BY publications.trade_date" in statement
        ):
            concurrent_future.result(timeout=5)

    def collect_candidate_queries(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "max(publications.publication_sequence)" in statement:
            watermark_queries.append(statement)
        if (
            "FROM publications" in statement
            and "publications.status" in statement
            and "ORDER BY publications.trade_date" in statement
            and statement not in candidate_queries
        ):
            candidate_queries.append(statement)

    with ThreadPoolExecutor(max_workers=1) as pool:
        event.listen(
            database.engine,
            "after_cursor_execute",
            insert_ahead_after_first_batch,
        )
        event.listen(
            database.engine,
            "before_cursor_execute",
            await_concurrent_insert,
        )
        event.listen(database.engine, "after_cursor_execute", collect_candidate_queries)
        try:
            listed = service.repository.list_snapshots(limit=2)
        finally:
            event.remove(
                database.engine,
                "after_cursor_execute",
                insert_ahead_after_first_batch,
            )
            event.remove(
                database.engine,
                "before_cursor_execute",
                await_concurrent_insert,
            )
            event.remove(
                database.engine,
                "after_cursor_execute",
                collect_candidate_queries,
            )

    assert [item.id for item in listed] == [second.id, first.id]
    assert concurrent_future is not None
    concurrent_future.result()
    assert len({item.id for item in listed}) == 2
    assert len(candidate_queries) == 2
    assert "OFFSET" not in candidate_queries[1]
    assert "publications.trade_date <" in candidate_queries[1]
    assert "publications.version <" in candidate_queries[1]
    assert "publications.id >" in candidate_queries[1]
    assert len(watermark_queries) == 1
    assert all(
        "publications.publication_sequence <=" in statement
        for statement in candidate_queries
    )


def test_list_snapshots_freezes_publication_watermark_across_candidate_batches(
    publication, database, repository
):
    service, command = publication

    def market_for(day, key):
        payload = batch(
            trade_date=day,
            bars=tuple(bar(trade_date=item) for item in DAYS if item <= day),
        )
        return repository.publish(repository.import_batch(payload, key=key).id)

    older = service.publish(
        command.model_copy(
            update={
                "trade_date": DAYS[0],
                "market_snapshot_id": market_for(DAYS[0], "watermark-oldest-market").id,
                "topic_snapshot_id": None,
                "disabled_domains": ("topic", "news", "regulation", "role"),
            }
        ),
        key="watermark-oldest",
    )
    newer = service.publish(
        command.model_copy(
            update={
                "trade_date": DAYS[2],
                "market_snapshot_id": market_for(DAYS[2], "watermark-newer-market").id,
                "topic_snapshot_id": None,
                "disabled_domains": ("topic", "news", "regulation", "role"),
            }
        ),
        key="watermark-newer",
    )
    concurrent_command = command.model_copy(
        update={
            "trade_date": DAYS[1],
            "market_snapshot_id": market_for(DAYS[1], "watermark-concurrent-market").id,
            "topic_snapshot_id": None,
            "disabled_domains": ("topic", "news", "regulation", "role"),
        }
    )

    denied_grant = grant(source_id="watermark-denied-source")
    repository.register_source(denied_grant)
    denied_payload = batch().model_copy(
        update={
            "source_id": denied_grant.source_id,
            "grant": denied_grant,
            "security_states": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in batch().security_states
            ),
            "bars": tuple(
                item.model_copy(update={"source_id": denied_grant.source_id})
                for item in batch().bars
            ),
        }
    )
    for index in range(10):
        denied_market = repository.publish(
            repository.import_batch(
                denied_payload,
                key=f"watermark-denied-market-{index}",
            ).id
        )
        service.publish(
            command.model_copy(update={"market_snapshot_id": denied_market.id}),
            key=f"watermark-denied-publication-{index}",
        )
    repository.register_source(denied_grant.model_copy(update={"local_display": False}))

    candidate_count = 0
    concurrent_future = None

    def publish_after_first_batch(
        conn, cursor, statement, parameters, context, executemany
    ):
        nonlocal candidate_count, concurrent_future
        if (
            "FROM publications" not in statement
            or "publications.status" not in statement
            or "ORDER BY publications.trade_date" not in statement
        ):
            return
        candidate_count += 1
        if candidate_count == 1:
            concurrent_future = pool.submit(
                service.publish,
                concurrent_command,
                key="watermark-concurrent",
            )

    def await_concurrent_publish(
        conn, cursor, statement, parameters, context, executemany
    ):
        if (
            candidate_count == 1
            and concurrent_future is not None
            and "FROM publications" in statement
            and "publications.status" in statement
            and "ORDER BY publications.trade_date" in statement
        ):
            concurrent_future.result(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        event.listen(database.engine, "after_cursor_execute", publish_after_first_batch)
        event.listen(database.engine, "before_cursor_execute", await_concurrent_publish)
        try:
            listed = service.repository.list_snapshots(limit=2)
        finally:
            event.remove(
                database.engine,
                "after_cursor_execute",
                publish_after_first_batch,
            )
            event.remove(
                database.engine,
                "before_cursor_execute",
                await_concurrent_publish,
            )

    assert [item.id for item in listed] == [newer.id, older.id]
    assert concurrent_future is not None
    assert concurrent_future.result().id not in {item.id for item in listed}


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
    result_domains=None,
    result_updates=None,
    dependency_updates=None,
    bypass_transition=False,
):
    publication_id = uuid4()
    result_domains = set(
        result_domains or ("market", "topic", "news", "regulation", "role")
    )
    result_updates = result_updates or {}
    dependency_updates = dependency_updates or {}
    with database.engine.begin() as connection:
        original = (
            connection.execute(
                select(publications).where(publications.c.id == record.id)
            )
            .mappings()
            .one()
        )
        values = dict(original)
        values.pop("publication_sequence")
        values.update(
            id=publication_id,
            version=record.version + 1,
            supersedes=record.id,
            topic_snapshot_id=topic_snapshot_id,
            status="draft",
            published_at=None,
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
            if result["domain"] not in result_domains:
                continue
            values = dict(result)
            values["publication_id"] = publication_id
            values.update(result_updates.get(result["domain"], {}))
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
            values.pop("dependency_sequence", None)
            values.update(
                id=uuid4(),
                derived_id=publication_id,
                publication_id=publication_id,
            )
            values.update(dependency_updates.get(dependency["derived_type"], {}))
            connection.execute(insert(source_dependencies).values(**values))
        if status in {"published", "revoked"}:
            if bypass_transition:
                connection.execute(
                    text(
                        "ALTER TABLE publications "
                        "DISABLE TRIGGER publications_transition"
                    )
                )
            connection.execute(
                update(publications)
                .where(publications.c.id == publication_id)
                .values(status="published", published_at=record.published_at)
            )
            if bypass_transition:
                connection.execute(
                    text(
                        "ALTER TABLE publications "
                        "ENABLE TRIGGER publications_transition"
                    )
                )
        if status == "revoked":
            connection.execute(
                update(publications)
                .where(publications.c.id == publication_id)
                .values(status="revoked")
            )
    return publication_id


@pytest.mark.parametrize(
    ("status", "published_at"),
    [
        ("published", NOW),
        ("revoked", NOW),
        ("draft", NOW),
        ("published", None),
    ],
)
def test_database_requires_draft_insert_and_consistent_published_at(
    publication, database, status, published_at
):
    service, command = publication
    record = service.publish(
        command, key=f"insert-state-{status}-{published_at is None}"
    )
    with database.engine.connect() as connection:
        original = (
            connection.execute(
                select(publications).where(publications.c.id == record.id)
            )
            .mappings()
            .one()
        )
    values = dict(original)
    values.pop("publication_sequence")
    values.update(
        id=uuid4(),
        version=record.version + 1,
        supersedes=record.id,
        status=status,
        published_at=published_at,
    )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(publications).values(**values))


@pytest.mark.parametrize(
    ("result_domains", "result_updates"),
    [
        (("market", "topic", "news", "regulation"), {}),
        (None, {"market": {"input_id": None}}),
        (None, {"news": {"status": "ready"}}),
        (None, {"topic": {"status": "missing"}}),
    ],
)
def test_database_transition_requires_complete_result_contract(
    publication, database, result_domains, result_updates
):
    service, command = publication
    record = service.publish(command, key=f"result-contract-{uuid4()}")
    draft_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=("market", "topic"),
        status="draft",
        result_domains=result_domains,
        result_updates=result_updates,
    )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(publications)
            .where(publications.c.id == draft_id)
            .values(status="published", published_at=NOW)
        )


@pytest.mark.parametrize(
    "defect",
    [
        "missing-market",
        "mismatched-market",
        "duplicate-market",
        "missing-topic",
        "mismatched-topic",
    ],
)
def test_database_transition_requires_exact_input_lineage(
    publication, database, defect
):
    service, command = publication
    record = service.publish(command, key=f"lineage-contract-{defect}")
    dependency_domains = ["market", "topic"]
    dependency_updates = {}
    if defect == "missing-market":
        dependency_domains.remove("market")
    elif defect == "missing-topic":
        dependency_domains.remove("topic")
    elif defect == "mismatched-market":
        dependency_updates["market"] = {"archive_id": uuid4()}
    elif defect == "mismatched-topic":
        dependency_updates["topic"] = {"archive_id": uuid4()}
    draft_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=tuple(dependency_domains),
        status="draft",
        dependency_updates=dependency_updates,
    )
    if defect == "duplicate-market":
        with database.engine.begin() as connection:
            connection.execute(
                insert(source_dependencies).values(
                    id=uuid4(),
                    source_id="synthetic",
                    archive_type="market_import_batch",
                    archive_id=uuid4(),
                    derived_type="market",
                    derived_id=draft_id,
                    publication_id=draft_id,
                    created_at=NOW,
                )
            )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(publications)
            .where(publications.c.id == draft_id)
            .values(status="published", published_at=NOW)
        )


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
        bypass_transition=True,
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
        bypass_transition=True,
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


@pytest.mark.parametrize("defect", ["missing", "mismatched", "duplicate"])
def test_unified_read_rejects_invalid_market_dependency(publication, database, defect):
    service, command = publication
    record = service.publish(command, key=f"read-market-{defect}")
    dependency_domains = ("topic",) if defect == "missing" else ("market", "topic")
    dependency_updates = (
        {"market": {"archive_id": uuid4()}} if defect == "mismatched" else {}
    )
    tampered_id = _clone_publication_with_dependencies(
        database,
        record,
        topic_snapshot_id=record.topic_snapshot_id,
        dependency_domains=dependency_domains,
        dependency_updates=dependency_updates,
        bypass_transition=True,
    )
    if defect == "duplicate":
        with database.engine.begin() as connection:
            connection.execute(
                insert(source_dependencies).values(
                    id=uuid4(),
                    source_id="synthetic",
                    archive_type="market_import_batch",
                    archive_id=uuid4(),
                    derived_type="market",
                    derived_id=tampered_id,
                    publication_id=tampered_id,
                    created_at=NOW,
                )
            )

    with pytest.raises(Forbidden):
        service.repository.get_snapshot(tampered_id)


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
    lock_order = []

    def observe_lock(conn, cursor, statement, parameters, context, executemany):
        key = str(parameters.get("key", ""))
        if "pg_advisory_xact_lock" in statement:
            lock_order.append(key)

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

    assert lock_order == [
        "publication-key:stable-source-order",
        "publication-sequence",
        f"publication-date:{DAYS[-1].isoformat()}",
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
    historical_batch = batch()
    canonical = json.dumps(
        historical_batch.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    archive_id = uuid4()
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
            connection.execute(
                insert(import_batches).values(
                    id=archive_id,
                    idempotency_key="migration-history",
                    source_id="migration-sentinel",
                    trade_date=historical_batch.trade_date,
                    canonical_json=canonical,
                    payload_hash=digest,
                    quality_report={"issues": [], "publishable": True},
                    imported_at=NOW,
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
            migrated = connection.execute(
                select(
                    import_batches.c.canonical_json,
                    import_batches.c.payload_hash,
                    import_batches.c.idempotency_hash,
                ).where(import_batches.c.id == archive_id)
            ).one()
            assert migrated == (canonical, digest, digest)
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_REVISION
            )
    finally:
        database.close()
