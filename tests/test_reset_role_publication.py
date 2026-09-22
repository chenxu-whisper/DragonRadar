"""Persistent role runs and publication-bound evidence."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from factories import DAYS, NOW, batch, grant
from sqlalchemy import delete, event, insert, inspect, select, text, update
from sqlalchemy.exc import DBAPIError
from test_role_history import _validation_fixture
from topic_factories import (
    TOPIC_DAYS,
    coverage_cell,
    topic_batch,
    topic_batch_dict,
    topic_definition,
    topic_membership,
)

from dragon_radar.publication.models import PublicationCommand, RetentionPolicy
from dragon_radar.publication.retention import RetentionService
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import (
    publication_results,
    publications,
    source_dependencies,
)
from dragon_radar.roles.materialize import RoleMaterializer
from dragon_radar.roles.models import RoleContext
from dragon_radar.roles.repository import RoleRepository
from dragon_radar.roles.tables import (
    role_factor_observations,
    role_limit_rows,
    role_materialization_attempts,
    role_pending_validations,
    role_results,
    role_run_topics,
    role_runs,
    role_topic_statuses,
    role_validation_commands,
    role_validation_inputs,
    role_validations,
)
from dragon_radar.storage.database import SCHEMA_REVISION
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound
from dragon_radar.topics.models import TopicBatch
from dragon_radar.topics.repository import TopicRepository


@pytest.fixture
def role_inputs(database, repository):
    market = repository.publish(repository.import_batch(batch(), key="role-market").id)
    topics = TopicRepository(database, clock=lambda: NOW)
    topic = topics.publish(topics.import_batch(topic_batch(), key="role-topic").id)
    return market, topic


@pytest.fixture
def materializer(database, repository):
    return RoleMaterializer(database, repository, clock=lambda: NOW)


def _materialize(materializer, role_inputs, *, key="role-run", **changes):
    market, topic = role_inputs
    values = {
        "market_snapshot_id": market.id,
        "topic_snapshot_id": topic.id,
        "rule_version": "role-v1",
        "benchmark_id": "CSI300",
        "key": key,
    }
    values.update(changes)
    return materializer.materialize(**values)


def _published_binding(connection, run, *, trade_date, version):
    publication_id = uuid4()
    connection.execute(text("ALTER TABLE publications DISABLE TRIGGER USER"))
    connection.execute(
        insert(publications).values(
            id=publication_id,
            trade_date=trade_date,
            version=version,
            supersedes=None,
            status="published",
            market_snapshot_id=run.market_snapshot_id,
            topic_snapshot_id=run.topic_snapshot_id,
            news_input_id=None,
            regulation_input_id=None,
            role_input_id=run.id,
            domains={},
            coverage={},
            quality_flags=[],
            command_json="{}",
            command_hash="0" * 64,
            created_at=NOW,
            published_at=NOW,
        )
    )
    connection.execute(text("ALTER TABLE publications ENABLE TRIGGER USER"))
    return publication_id


def _draft_run(connection, source_run, *, key, trade_date=None, **changes):
    source = (
        connection.execute(select(role_runs).where(role_runs.c.id == source_run.id))
        .mappings()
        .one()
    )
    run_id = uuid4()
    values = dict(source)
    values.update(
        id=run_id,
        idempotency_key=key,
        payload_hash=uuid4().hex + uuid4().hex,
        status="draft",
        materialized_at=None,
    )
    if trade_date is not None:
        values["trade_date"] = trade_date
    values.update(changes)
    connection.execute(insert(role_runs).values(**values))
    return run_id


def _topic_row(connection, run_id, topic_id="topic-a"):
    connection.execute(
        insert(role_run_topics).values(
            role_run_id=run_id,
            topic_id=topic_id,
            member_ids=["A"],
            member_identity="0" * 64,
        )
    )


def _result_row(connection, run_id, *, topic_id="topic-a", role="relay"):
    result_id = uuid4()
    connection.execute(
        insert(role_results).values(
            id=result_id,
            role_run_id=run_id,
            topic_id=topic_id,
            security_id="A",
            observation_at=NOW,
            role=role,
            rule_version="role-v1",
            internal_status=("pending_validation" if role == "relay" else "candidate"),
            tie_rank=1,
            factors=[],
            reasons=[],
        )
    )
    return result_id


def _pending_row(connection, run_id, result_id, *, topic_id="topic-a"):
    pending_id = uuid4()
    connection.execute(
        insert(role_pending_validations).values(
            id=pending_id,
            pending_result_id=result_id,
            observation_run_id=run_id,
            topic_id=topic_id,
            observation_date=DAYS[-1],
            validation_sessions=[day.isoformat() for day in DAYS[-3:]],
            baseline_evidence={},
            created_at=NOW,
        )
    )
    return pending_id


def _validation_values(pending_id, *, revision, supersedes, **changes):
    values = {
        "id": uuid4(),
        "pending_validation_id": pending_id,
        "revision": revision,
        "supersedes": supersedes,
        "outcome": "unavailable",
        "window_start": DAYS[1],
        "window_end": DAYS[-1],
        "return_start": DAYS[0],
        "return_end": DAYS[-1],
        "old_return_raw": None,
        "new_return_raw": None,
        "topic_median_raw": None,
        "reasons": ["validation_runs_missing"],
        "created_at": NOW,
    }
    values.update(changes)
    return values


def _validation_integrity_rows(connection, source, *, key):
    input_run_ids = [source.id]
    input_run_ids.extend(
        _materialized_clone(
            connection,
            source,
            key=f"{key}-input-{position}",
            trade_date=DAYS[-1],
        )
        for position in range(1, 5)
    )
    pending_run_id = _draft_run(connection, source, key=f"{key}-pending")
    _topic_row(connection, pending_run_id)
    pending_id = _pending_row(
        connection,
        pending_run_id,
        _result_row(connection, pending_run_id),
    )
    return pending_id, tuple(input_run_ids)


def _available_validation_values(pending_id):
    return _validation_values(
        pending_id,
        revision=1,
        supersedes=None,
        outcome="supported",
        old_return_raw="0.10",
        new_return_raw="0.20",
        topic_median_raw="0.15",
        reasons=["outcome:supported"],
    )


def _materialized_clone(connection, source_run, *, key, trade_date, **changes):
    run_id = _draft_run(
        connection,
        source_run,
        key=key,
        trade_date=trade_date,
        **changes,
    )
    topic = (
        connection.execute(
            select(role_run_topics).where(
                role_run_topics.c.role_run_id == source_run.id
            )
        )
        .mappings()
        .one()
    )
    connection.execute(
        insert(role_run_topics).values(**{**dict(topic), "role_run_id": run_id})
    )
    statuses = (
        connection.execute(
            select(role_topic_statuses).where(
                role_topic_statuses.c.role_run_id == source_run.id
            )
        )
        .mappings()
        .all()
    )
    connection.execute(
        insert(role_topic_statuses),
        [{**dict(status), "role_run_id": run_id} for status in statuses],
    )
    connection.execute(
        update(role_runs)
        .where(role_runs.c.id == run_id)
        .values(status="materialized", materialized_at=NOW)
    )
    return run_id


def _market_batch_with_source(source_id):
    source_grant = grant(source_id=source_id)
    source_batch = batch()
    return source_grant, source_batch.model_copy(
        update={
            "source_id": source_id,
            "grant": source_grant,
            "security_states": tuple(
                item.model_copy(update={"source_id": source_id})
                for item in source_batch.security_states
            ),
            "bars": tuple(
                item.model_copy(update={"source_id": source_id})
                for item in source_batch.bars
            ),
            "limit_up_events": tuple(
                item.model_copy(update={"source_id": source_id})
                for item in source_batch.limit_up_events
            ),
        }
    )


def _persist_validation_input_graph(
    materializer,
    role_inputs,
    database,
    repository,
    *,
    key,
):
    root = _materialize(materializer, role_inputs, key=f"{key}-root")
    market_grant, market_batch = _market_batch_with_source(f"{key}-market")
    repository.register_source(market_grant)
    input_market = repository.publish(
        repository.import_batch(market_batch, key=f"{key}-market-import").id
    )
    topic_grant = grant(source_id=f"{key}-topic")
    repository.register_source(topic_grant)
    topics = TopicRepository(database, clock=lambda: NOW)
    input_topic = topics.publish(
        topics.import_batch(
            topic_batch(source_id=topic_grant.source_id, grant=topic_grant),
            key=f"{key}-topic-import",
        ).id
    )

    with database.engine.begin() as connection:
        input_run_ids = tuple(
            _materialized_clone(
                connection,
                root,
                key=f"{key}-input-{position}",
                trade_date=DAYS[-1],
                market_snapshot_id=input_market.id,
                topic_snapshot_id=input_topic.id,
            )
            for position in range(5)
        )
        connection.execute(text("ALTER TABLE role_results DISABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations DISABLE TRIGGER USER")
        )
        result_id = _result_row(connection, root.id)
        pending_id = _pending_row(connection, root.id, result_id)
        connection.execute(text("ALTER TABLE role_results ENABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations ENABLE TRIGGER USER")
        )
        validation = _validation_values(pending_id, revision=1, supersedes=None)
        connection.execute(insert(role_validations).values(**validation))
        connection.execute(
            insert(role_validation_inputs),
            [
                {
                    "validation_id": validation["id"],
                    "position": position,
                    "input_role_run_id": run_id,
                }
                for position, run_id in enumerate(input_run_ids)
            ],
        )
    return {
        "root": root,
        "input_market": input_market,
        "input_topic": input_topic,
        "market_grant": market_grant,
        "topic_grant": topic_grant,
        "validation_id": validation["id"],
        "input_run_ids": input_run_ids,
    }


def _corrupt_available_validation_input_count(database, validation_id):
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE role_validations DISABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_validation_inputs DISABLE TRIGGER USER")
        )
        connection.execute(
            update(role_validations)
            .where(role_validations.c.id == validation_id)
            .values(
                outcome="supported",
                old_return_raw="0.10",
                new_return_raw="0.20",
                topic_median_raw="0.15",
                reasons=["outcome:supported"],
            )
        )
        last_input = (
            connection.execute(
                select(role_validation_inputs)
                .where(role_validation_inputs.c.validation_id == validation_id)
                .order_by(role_validation_inputs.c.position.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        connection.execute(
            delete(role_validation_inputs).where(
                role_validation_inputs.c.validation_id == validation_id,
                role_validation_inputs.c.position == last_input["position"],
            )
        )
        connection.execute(text("ALTER TABLE role_validations ENABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_validation_inputs ENABLE TRIGGER USER")
        )


def test_previous_published_uses_latest_authorized_publication_version(
    materializer, role_inputs, database, monkeypatch
):
    run_a = _materialize(materializer, role_inputs, key="previous-run-a")
    run_b = _materialize(materializer, role_inputs, key="previous-run-b")
    previous_date = DAYS[-2]
    with database.engine.begin() as connection:
        publication_a = _published_binding(
            connection, run_a, trade_date=previous_date, version=1
        )
        publication_b = _published_binding(
            connection, run_b, trade_date=previous_date, version=2
        )

    repository = materializer.repository
    rows = {
        run_a.id: {"id": run_a.id, "trade_date": previous_date},
        run_b.id: {"id": run_b.id, "trade_date": previous_date},
    }
    monkeypatch.setattr(
        repository,
        "authorized_row",
        lambda _connection, role_run_id: rows[role_run_id],
    )
    context = RoleContext(
        market=role_inputs[0].batch,
        topic=TopicRepository(database, clock=lambda: NOW)
        .get_snapshot(role_inputs[1].id)
        .batch,
        rule_version="role-v1",
    )
    with database.engine.begin() as connection:
        selected = repository.previous_published(connection, context)

    assert selected == (rows[run_b.id], publication_b)
    assert publication_a != publication_b


def test_previous_published_skips_unauthorized_newer_candidate(
    materializer, role_inputs, database, monkeypatch
):
    run_a = _materialize(materializer, role_inputs, key="authorized-previous-run")
    run_b = _materialize(materializer, role_inputs, key="forbidden-previous-run")
    previous_date = DAYS[-2]
    with database.engine.begin() as connection:
        publication_a = _published_binding(
            connection, run_a, trade_date=previous_date, version=1
        )
        _published_binding(connection, run_b, trade_date=previous_date, version=2)

    repository = materializer.repository
    authorized = {"id": run_a.id, "trade_date": previous_date}

    def authorize(_connection, role_run_id):
        if role_run_id == run_b.id:
            raise Forbidden("revoked")
        return authorized

    monkeypatch.setattr(repository, "authorized_row", authorize)
    context = RoleContext(
        market=role_inputs[0].batch,
        topic=TopicRepository(database, clock=lambda: NOW)
        .get_snapshot(role_inputs[1].id)
        .batch,
        rule_version="role-v1",
    )
    with database.engine.begin() as connection:
        selected = repository.previous_published(connection, context)

    assert selected == (authorized, publication_a)


def test_migration_upgrades_existing_0006_schema(database_url):
    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0006_market_readmodels")
        assert "role_runs" not in inspect(database.engine).get_table_names()

        database.migrate()

        assert database.ready()
        assert "role_runs" in inspect(database.engine).get_table_names()
    finally:
        database.close()


def test_migration_upgrades_database_already_at_original_0007(database_url):
    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0007_role_readmodels")
        assert (
            "role_validation_commands" not in inspect(database.engine).get_table_names()
        )

        database.migrate()

        assert database.ready()
        assert "role_validation_commands" in inspect(database.engine).get_table_names()
        with database.engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgname = 'role_validations_chain' "
                        "AND NOT tgisinternal"
                    )
                )
                == 1
            )
    finally:
        database.close()


def test_role_integrity_migration_roundtrips_from_clean_0006(database_url):
    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0006_market_readmodels")
            command.upgrade(config, "head")
        assert database.ready()

        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0007_role_readmodels")
        assert (
            "role_validation_commands" not in inspect(database.engine).get_table_names()
        )

        database.migrate()

        assert database.ready()
        assert "role_validation_commands" in inspect(database.engine).get_table_names()
    finally:
        database.close()


def test_migration_creates_role_schema_and_readiness(database):
    assert database.ready()
    tables = set(inspect(database.engine).get_table_names())
    assert {
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
    } <= tables
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == SCHEMA_REVISION
        )


def test_zero_candidate_topic_still_persists_all_six_statuses(
    materializer, role_inputs, database
):
    run = _materialize(materializer, role_inputs)

    assert run.status == "materialized"
    with database.engine.connect() as connection:
        topics = connection.scalar(
            select(text("count(*)"))
            .select_from(role_run_topics)
            .where(role_run_topics.c.role_run_id == run.id)
        )
        statuses = (
            connection.execute(
                select(
                    role_topic_statuses.c.topic_id,
                    role_topic_statuses.c.role,
                    role_topic_statuses.c.status,
                )
                .where(role_topic_statuses.c.role_run_id == run.id)
                .order_by(
                    role_topic_statuses.c.topic_id,
                    role_topic_statuses.c.role,
                )
            )
            .mappings()
            .all()
        )
    assert topics == 1
    assert {row["role"] for row in statuses} == {
        "trend",
        "core",
        "emotion",
        "catchup",
        "follower",
        "relay",
    }
    assert len(statuses) == 6


def test_materialization_persists_normalized_factor_and_ladder_evidence(
    materializer, role_inputs, database
):
    run = _materialize(materializer, role_inputs, key="normalized-evidence")

    with database.engine.connect() as connection:
        counts = {
            table.name: connection.scalar(
                select(text("count(*)"))
                .select_from(table)
                .where(table.c.role_run_id == run.id)
            )
            for table in (
                role_results,
                role_factor_observations,
                role_limit_rows,
            )
        }
    assert counts["role_results"] == 0
    assert counts["role_factor_observations"] > 0
    assert counts["role_limit_rows"] > 0


def test_failed_materialization_rolls_back_all_visible_evidence_and_records_attempt(
    materializer, role_inputs, database, monkeypatch
):
    def fail(_connection, _tree):
        raise RuntimeError("injected role persistence failure")

    monkeypatch.setattr(materializer.repository, "insert_tree", fail)
    with pytest.raises(RuntimeError, match="injected"):
        _materialize(materializer, role_inputs, key="atomic-role-failure")

    with database.engine.connect() as connection:
        assert connection.scalar(select(text("count(*)")).select_from(role_runs)) == 0
        assert (
            connection.scalar(select(text("count(*)")).select_from(role_topic_statuses))
            == 0
        )
        attempt = (
            connection.execute(select(role_materialization_attempts)).mappings().one()
        )
    assert attempt["status"] == "failed"
    assert attempt["role_run_id"] is None
    assert attempt["details"] == {"error_type": "RuntimeError"}


def test_role_materialization_is_idempotent_and_conflicting_payload_is_rejected(
    materializer, role_inputs
):
    first = _materialize(materializer, role_inputs, key="role-idempotency")
    assert (
        _materialize(materializer, role_inputs, key="role-idempotency").id == first.id
    )
    with pytest.raises(Conflict):
        _materialize(
            materializer,
            role_inputs,
            key="role-idempotency",
            benchmark_id="OTHER",
        )


def test_concurrent_role_materialization_returns_one_root_run(
    database, repository, role_inputs
):
    first_locked = Event()
    release_first = Event()
    first = RoleMaterializer(database, repository, clock=lambda: NOW)
    second = RoleMaterializer(database, repository, clock=lambda: NOW)
    original = first.repository._lock

    def controlled_lock(connection, key):
        result = original(connection, key)
        if key == "role-materialization-key:role-concurrent":
            first_locked.set()
            assert release_first.wait(5)
        return result

    first.repository._lock = controlled_lock
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(
            _materialize,
            first,
            role_inputs,
            key="role-concurrent",
        )
        assert first_locked.wait(5)
        other = pool.submit(
            _materialize,
            second,
            role_inputs,
            key="role-concurrent",
        )
        release_first.set()
        assert future.result().id == other.result().id


def test_role_evidence_and_materialized_root_are_database_immutable(
    materializer, role_inputs, database
):
    run = _materialize(materializer, role_inputs, key="immutable-role")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(role_topic_statuses)
            .where(role_topic_statuses.c.role_run_id == run.id)
            .values(reasons=["forged"])
        )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(role_runs)
            .where(role_runs.c.id == run.id)
            .values(benchmark_id="forged")
        )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(role_topic_statuses).values(
                role_run_id=run.id,
                topic_id="topic-a",
                role="forged",
                capability="ready",
                status="ready",
                expected=0,
                valid=0,
                legal_excluded=0,
                missing=0,
                reasons=[],
            )
        )


def test_database_rejects_invalid_role_name(materializer, role_inputs, database):
    source = _materialize(materializer, role_inputs, key="invalid-role-source")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        run_id = _draft_run(connection, source, key="invalid-role-draft")
        _topic_row(connection, run_id)
        connection.execute(
            insert(role_topic_statuses).values(
                role_run_id=run_id,
                topic_id="topic-a",
                role="forged",
                capability="ready",
                status="ready",
                expected=0,
                valid=0,
                legal_excluded=0,
                missing=0,
                reasons=[],
            )
        )


def test_database_rejects_cross_run_topic_result(materializer, role_inputs, database):
    source = _materialize(materializer, role_inputs, key="cross-topic-source")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        run_a = _draft_run(connection, source, key="cross-topic-a")
        run_b = _draft_run(connection, source, key="cross-topic-b")
        _topic_row(connection, run_a, "topic-a")
        _topic_row(connection, run_b, "topic-b")
        _result_row(connection, run_a, topic_id="topic-b")


def test_database_rejects_pending_validation_for_non_relay_result(
    materializer, role_inputs, database
):
    source = _materialize(materializer, role_inputs, key="pending-result-source")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        run_id = _draft_run(connection, source, key="pending-result-draft")
        _topic_row(connection, run_id)
        result_id = _result_row(connection, run_id, role="trend")
        _pending_row(connection, run_id, result_id)


def test_materialization_requires_every_visible_topic_before_transition(
    materializer, role_inputs, database
):
    market, _topic = role_inputs
    payload = topic_batch_dict()
    payload["topics"].append(
        topic_definition(
            "topic-b",
            name="Synthetic Topic B",
            source_reference="synthetic:topic-b",
        )
    )
    payload["memberships"].append(
        topic_membership(
            "topic-b",
            evidence_refs=("synthetic:membership-b",),
        )
    )
    payload["coverage_cells"].extend(
        coverage_cell("topic-b", trade_date=day) for day in TOPIC_DAYS
    )
    payload["page_manifest"]["pages"][0]["received_row_count"] = 2
    payload["page_manifest"]["pages"][1]["received_row_count"] = 2
    payload["page_manifest"]["declared_total"] = 4
    topics = TopicRepository(database, clock=lambda: NOW)
    topic = topics.publish(
        topics.import_batch(
            TopicBatch.model_validate(payload),
            key="two-topic-batch",
        ).id
    )
    run = materializer.materialize(
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        rule_version="role-v1",
        benchmark_id="CSI300",
        key="two-topic-run",
    )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_run_topics)
                .where(role_run_topics.c.role_run_id == run.id)
            )
            == 2
        )
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_topic_statuses)
                .where(role_topic_statuses.c.role_run_id == run.id)
            )
            == 12
        )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        incomplete_id = _draft_run(connection, run, key="incomplete-two-topic-run")
        topic_row = (
            connection.execute(
                select(role_run_topics).where(
                    role_run_topics.c.role_run_id == run.id,
                    role_run_topics.c.topic_id == "topic-a",
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(role_run_topics).values(
                **{**dict(topic_row), "role_run_id": incomplete_id}
            )
        )
        statuses = (
            connection.execute(
                select(role_topic_statuses).where(
                    role_topic_statuses.c.role_run_id == run.id,
                    role_topic_statuses.c.topic_id == "topic-a",
                )
            )
            .mappings()
            .all()
        )
        connection.execute(
            insert(role_topic_statuses),
            [{**dict(status), "role_run_id": incomplete_id} for status in statuses],
        )
        connection.execute(
            update(role_runs)
            .where(role_runs.c.id == incomplete_id)
            .values(status="materialized", materialized_at=NOW)
        )


@pytest.mark.parametrize(
    ("revision", "supersedes_mode"),
    [
        (1, "previous"),
        (2, "null"),
        (2, "other_pending"),
        (3, "previous"),
    ],
)
def test_database_rejects_invalid_validation_correction_chain(
    materializer,
    role_inputs,
    database,
    revision,
    supersedes_mode,
):
    source = _materialize(materializer, role_inputs, key=f"chain-source-{revision}")
    with database.engine.begin() as connection:
        run_id = _draft_run(
            connection,
            source,
            key=f"chain-draft-{revision}-{supersedes_mode}",
        )
        _topic_row(connection, run_id)
        pending_id = _pending_row(connection, run_id, _result_row(connection, run_id))
        other_run_id = _draft_run(
            connection,
            source,
            key=f"chain-other-{revision}-{supersedes_mode}",
        )
        _topic_row(connection, other_run_id)
        other_pending_id = _pending_row(
            connection,
            other_run_id,
            _result_row(connection, other_run_id),
        )
        previous = _validation_values(pending_id, revision=1, supersedes=None)
        other = _validation_values(other_pending_id, revision=1, supersedes=None)
        if revision != 1:
            connection.execute(insert(role_validations).values(**previous))
        connection.execute(insert(role_validations).values(**other))

    supersedes = {
        "previous": other["id"] if revision == 1 else previous["id"],
        "null": None,
        "other_pending": other["id"],
    }[supersedes_mode]
    invalid = _validation_values(
        pending_id,
        revision=revision,
        supersedes=supersedes,
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(role_validations).values(**invalid))


def test_database_allows_contiguous_validation_correction_chain(
    materializer, role_inputs, database
):
    source = _materialize(materializer, role_inputs, key="valid-chain-source")
    with database.engine.begin() as connection:
        run_id = _draft_run(connection, source, key="valid-chain-draft")
        _topic_row(connection, run_id)
        pending_id = _pending_row(connection, run_id, _result_row(connection, run_id))
        first = _validation_values(pending_id, revision=1, supersedes=None)
        second = _validation_values(
            pending_id,
            revision=2,
            supersedes=first["id"],
        )
        connection.execute(insert(role_validations).values(**first))
        connection.execute(insert(role_validations).values(**second))

    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_validations)
                .where(role_validations.c.pending_validation_id == pending_id)
            )
            == 2
        )


@pytest.mark.parametrize("input_count", [0, 4])
def test_database_rejects_available_validation_without_all_five_real_inputs(
    materializer,
    role_inputs,
    database,
    input_count,
):
    source = _materialize(
        materializer,
        role_inputs,
        key=f"incomplete-available-source-{input_count}",
    )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        pending_id, input_run_ids = _validation_integrity_rows(
            connection,
            source,
            key=f"incomplete-available-{input_count}",
        )
        validation = _available_validation_values(pending_id)
        connection.execute(insert(role_validations).values(**validation))
        if input_count:
            connection.execute(
                insert(role_validation_inputs),
                [
                    {
                        "validation_id": validation["id"],
                        "position": position,
                        "input_role_run_id": run_id,
                    }
                    for position, run_id in enumerate(input_run_ids[:input_count])
                ],
            )


def test_database_accepts_available_validation_with_five_real_inputs(
    materializer,
    role_inputs,
    database,
):
    source = _materialize(
        materializer,
        role_inputs,
        key="complete-available-source",
    )

    with database.engine.begin() as connection:
        pending_id, input_run_ids = _validation_integrity_rows(
            connection,
            source,
            key="complete-available",
        )
        validation = _available_validation_values(pending_id)
        connection.execute(insert(role_validations).values(**validation))
        connection.execute(
            insert(role_validation_inputs),
            [
                {
                    "validation_id": validation["id"],
                    "position": position,
                    "input_role_run_id": run_id,
                }
                for position, run_id in enumerate(input_run_ids)
            ],
        )


@pytest.mark.parametrize("position", [-1, 5])
def test_database_rejects_validation_input_position_outside_five_day_window(
    materializer,
    role_inputs,
    database,
    position,
):
    source = _materialize(
        materializer,
        role_inputs,
        key=f"invalid-position-source-{position}",
    )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        pending_id, input_run_ids = _validation_integrity_rows(
            connection,
            source,
            key=f"invalid-position-{position}",
        )
        validation = _validation_values(
            pending_id,
            revision=1,
            supersedes=None,
        )
        connection.execute(insert(role_validations).values(**validation))
        connection.execute(
            insert(role_validation_inputs).values(
                validation_id=validation["id"],
                position=position,
                input_role_run_id=input_run_ids[0],
            )
        )


def test_database_accepts_unavailable_validation_with_legal_missing_windows(
    materializer,
    role_inputs,
    database,
):
    source = _materialize(
        materializer,
        role_inputs,
        key="unavailable-missing-source",
    )

    with database.engine.begin() as connection:
        pending_id, input_run_ids = _validation_integrity_rows(
            connection,
            source,
            key="unavailable-missing",
        )
        validation = _validation_values(
            pending_id,
            revision=1,
            supersedes=None,
        )
        connection.execute(insert(role_validations).values(**validation))
        connection.execute(
            insert(role_validation_inputs),
            [
                {
                    "validation_id": validation["id"],
                    "position": position,
                    "input_role_run_id": input_run_ids[position],
                }
                for position in (0, 2, 4)
            ],
        )


def test_publication_binds_materialized_role_run_and_reads_stored_payload(
    materializer, role_inputs, database, repository, monkeypatch
):
    market, topic = role_inputs
    run = _materialize(materializer, role_inputs, key="publication-role")
    service = PublicationService(database, repository, clock=lambda: NOW)
    command = PublicationCommand(
        trade_date=DAYS[-1],
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        role_input_id=run.id,
        disabled_domains=("news", "regulation"),
    )
    record = service.publish(command, key="publication-with-role")

    def fail_recompute(*_args, **_kwargs):
        raise RuntimeError("role algorithm reran")

    monkeypatch.setattr(
        "dragon_radar.roles.engine.evaluate_trend",
        fail_recompute,
    )
    results = service.repository.list_results(record.id)
    role = next(item for item in results if item.domain == "role")
    loaded = RoleRepository(database, repository, clock=lambda: NOW).for_publication(
        record.id
    )

    assert role.input_id == run.id
    assert role.payload["role_run_id"] == str(run.id)
    assert len(role.payload["topics"][0]["statuses"]) == 6
    assert loaded.id == run.id


@pytest.mark.parametrize(
    "change",
    [
        {"trade_date": DAYS[0]},
        {"market_snapshot_id": uuid4()},
        {"topic_snapshot_id": uuid4()},
    ],
)
def test_publication_rejects_role_run_binding_mismatch(
    materializer, role_inputs, database, repository, change
):
    market, topic = role_inputs
    run = _materialize(materializer, role_inputs, key=f"binding-{uuid4()}")
    values = {
        "trade_date": DAYS[-1],
        "market_snapshot_id": market.id,
        "topic_snapshot_id": topic.id,
        "role_input_id": run.id,
        "disabled_domains": ("news", "regulation"),
    }
    values.update(change)
    with pytest.raises((ValueError, Forbidden, NotFound)):
        PublicationService(database, repository, clock=lambda: NOW).publish(
            PublicationCommand(**values),
            key=f"reject-binding-{uuid4()}",
        )


def test_current_grant_revocation_blocks_role_materialization_and_read(
    materializer, role_inputs, database, repository
):
    run = _materialize(materializer, role_inputs, key="grant-role")
    repository.register_source(grant(local_display=False))

    with pytest.raises(Forbidden):
        _materialize(materializer, role_inputs, key="grant-role-denied")
    with pytest.raises(Forbidden):
        RoleRepository(database, repository, clock=lambda: NOW).get(run.id)


def test_role_payload_reauthorizes_all_exposed_validation_inputs(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="payload-validation-auth",
    )
    roles = RoleRepository(database, repository, clock=lambda: NOW)

    payload = roles.payload(graph["root"].id)
    assert payload["topics"][0]["role_validations"][0]["id"] == str(
        graph["validation_id"]
    )

    repository.revoke_source(graph["topic_grant"].source_id)
    with pytest.raises(Forbidden):
        roles.payload(graph["root"].id)


def test_role_payload_rejects_corrupt_available_validation_input_count(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="payload-incomplete-validation",
    )
    _corrupt_available_validation_input_count(database, graph["validation_id"])

    with pytest.raises(Forbidden, match="validation inputs"):
        RoleRepository(database, repository, clock=lambda: NOW).payload(
            graph["root"].id
        )


def test_publication_create_rejects_corrupt_available_validation_input_count(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="publication-incomplete-validation",
    )
    _corrupt_available_validation_input_count(database, graph["validation_id"])

    with pytest.raises(Forbidden, match="validation inputs"):
        _validation_publication(
            PublicationService(database, repository, clock=lambda: NOW),
            role_inputs,
            graph["root"].id,
            key="reject-incomplete-validation-publication",
        )


def test_validate_pending_locks_cross_source_commands_in_one_stable_order(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="lock-order",
    )
    commands = (
        ("lock-order-root-target", graph["root"].market_snapshot_id),
        ("lock-order-input-target", graph["input_market"].id),
    )
    with database.engine.begin() as connection:
        for key, target_id in commands:
            canonical = json.dumps(
                {"through_market_snapshot_id": str(target_id)},
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                insert(role_validation_commands).values(
                    id=uuid4(),
                    idempotency_key=key,
                    payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                    through_market_snapshot_id=target_id,
                    validation_ids=[str(graph["validation_id"])],
                    mature_count=1,
                    immature_count=0,
                    created_at=NOW,
                )
            )

    roles = RoleRepository(database, repository, clock=lambda: NOW)
    observed_orders = []
    for key, target_id in commands:
        source_locks = []
        all_locks = []

        def observe_lock(
            conn,
            cursor,
            statement,
            parameters,
            context,
            executemany,
            source_locks=source_locks,
            all_locks=all_locks,
        ):
            lock_key = str(parameters.get("key", ""))
            if "pg_advisory_xact_lock" not in statement:
                return
            all_locks.append(lock_key)
            if lock_key.startswith("source:"):
                source_locks.append(lock_key)

        event.listen(database.engine, "after_cursor_execute", observe_lock)
        try:
            roles.validate_pending(target_id, key=key)
        finally:
            event.remove(database.engine, "after_cursor_execute", observe_lock)
        observed_orders.append(source_locks)
        assert all_locks.index(f"role-validation-key:{key}") > max(
            all_locks.index(source_lock) for source_lock in source_locks
        )

    expected = sorted(
        {
            "source:synthetic",
            f"source:{graph['market_grant'].source_id}",
            f"source:{graph['topic_grant'].source_id}",
        }
    )
    assert observed_orders == [expected, expected]


@pytest.mark.parametrize("validation_source_prefix", ["aaa-locks", "zzz-locks"])
def test_role_publication_locks_all_sources_once_in_global_sorted_order(
    materializer,
    role_inputs,
    database,
    repository,
    validation_source_prefix,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key=validation_source_prefix,
    )
    source_locks = []

    def observe_source_locks(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        lock_key = str(parameters.get("key", ""))
        if "pg_advisory_xact_lock" in statement and lock_key.startswith("source:"):
            source_locks.append(lock_key)

    event.listen(database.engine, "after_cursor_execute", observe_source_locks)
    try:
        _validation_publication(
            PublicationService(database, repository, clock=lambda: NOW),
            role_inputs,
            graph["root"].id,
            key=f"{validation_source_prefix}-publication",
        )
    finally:
        event.remove(database.engine, "after_cursor_execute", observe_source_locks)

    assert source_locks == sorted(set(source_locks))


def _validation_publication(service, role_inputs, role_run_id, *, key):
    return service.publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=role_inputs[0].id,
            topic_snapshot_id=role_inputs[1].id,
            role_input_id=role_run_id,
            disabled_domains=("news", "regulation"),
        ),
        key=key,
    )


def test_role_publication_rejects_revoked_validation_input_before_write(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="publish-validation-revoked",
    )
    repository.revoke_source(graph["market_grant"].source_id)
    service = PublicationService(database, repository, clock=lambda: NOW)

    with pytest.raises(Forbidden):
        _validation_publication(
            service,
            role_inputs,
            graph["root"].id,
            key="publish-revoked-validation",
        )


def test_role_publication_records_validation_input_archive_dependencies(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="publish-validation-dependencies",
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    publication = _validation_publication(
        service,
        role_inputs,
        graph["root"].id,
        key="publish-validation-dependencies",
    )

    with database.engine.connect() as connection:
        role_dependencies = {
            (row["source_id"], row["archive_type"], row["archive_id"])
            for row in connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == publication.id,
                    source_dependencies.c.derived_type == "role",
                )
            )
            .mappings()
            .all()
        }
    assert role_dependencies == {
        (
            graph["market_grant"].source_id,
            "market_import_batch",
            graph["input_market"].batch_id,
        ),
        (
            graph["topic_grant"].source_id,
            "topic_import_batch",
            graph["input_topic"].batch_id,
        ),
    }


def test_role_publication_read_reauthorizes_validation_input_dependencies(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="read-validation-dependencies",
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    publication = _validation_publication(
        service,
        role_inputs,
        graph["root"].id,
        key="read-validation-dependencies",
    )
    assert service.repository.list_results(publication.id)

    repository.revoke_source(graph["topic_grant"].source_id)
    with pytest.raises(Forbidden):
        service.repository.list_results(publication.id)


def test_role_publication_read_rejects_corrupt_validation_payload(
    materializer,
    role_inputs,
    database,
    repository,
):
    graph = _persist_validation_input_graph(
        materializer,
        role_inputs,
        database,
        repository,
        key="read-incomplete-validation",
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    publication = _validation_publication(
        service,
        role_inputs,
        graph["root"].id,
        key="read-incomplete-validation",
    )

    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE publication_results DISABLE TRIGGER USER"))
        role_result = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == publication.id,
                    publication_results.c.domain == "role",
                )
            )
            .mappings()
            .one()
        )
        payload = json.loads(json.dumps(role_result["payload"]))
        validation = payload["topics"][0]["role_validations"][0]
        validation["outcome"] = "supported"
        validation["old_return_raw"] = "0.10"
        validation["new_return_raw"] = "0.20"
        validation["topic_median_raw"] = "0.15"
        validation["reasons"] = ["outcome:supported"]
        validation["validation_inputs"] = validation["validation_inputs"][:4]
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(payload=payload)
        )
        connection.execute(text("ALTER TABLE publication_results ENABLE TRIGGER USER"))

    with pytest.raises(Forbidden, match="payload"):
        service.repository.list_results(publication.id)


def test_cli_contract_does_not_expose_context_grants_or_rule_json():
    from dragon_radar.cli import _parser

    args = _parser().parse_args(
        [
            "role-materialize",
            "--market-snapshot",
            str(uuid4()),
            "--topic-snapshot",
            str(uuid4()),
            "--rule-version",
            "role-v1",
            "--benchmark-id",
            "CSI300",
            "--key",
            "cli-role",
        ]
    )
    assert vars(args) == {
        "command": "role-materialize",
        "market_snapshot": args.market_snapshot,
        "topic_snapshot": args.topic_snapshot,
        "rule_version": "role-v1",
        "benchmark_id": "CSI300",
        "key": "cli-role",
    }


def test_role_validate_pending_cli_contract():
    from dragon_radar.cli import _parser

    args = _parser().parse_args(
        [
            "role-validate-pending",
            "--through-market-snapshot",
            str(uuid4()),
            "--key",
            "validate-through-snapshot",
        ]
    )

    assert vars(args) == {
        "command": "role-validate-pending",
        "through_market_snapshot": args.through_market_snapshot,
        "key": "validate-through-snapshot",
    }


def test_validate_pending_calls_relay_validator_atomically_and_is_idempotent(
    materializer,
    role_inputs,
    database,
    monkeypatch,
):
    pending, future_evidence = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    source = _materialize(materializer, role_inputs, key="validation-source")
    future_ids = []
    publication_ids = {}
    with database.engine.begin() as connection:
        for position, evidence in enumerate(future_evidence, start=1):
            run_id = _materialized_clone(
                connection,
                source,
                key=f"validation-future-{position}",
                trade_date=evidence.trade_date,
            )
            future_ids.append(run_id)
            publication_ids[run_id] = _published_binding(
                connection,
                source.model_copy(
                    update={"id": run_id, "trade_date": evidence.trade_date}
                ),
                trade_date=evidence.trade_date,
                version=1,
            )

        connection.execute(text("ALTER TABLE role_results DISABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations DISABLE TRIGGER USER")
        )
        pending_result_id = _result_row(connection, source.id)
        pending_id = uuid4()
        observation_run = pending.observation_run.model_copy(
            update={"role_run_id": str(source.id)}
        )
        stored_pending = pending.model_copy(
            update={
                "pending_result_id": str(pending_result_id),
                "observation_run_id": str(source.id),
                "observation_run": observation_run,
            }
        )
        connection.execute(
            insert(role_pending_validations).values(
                id=pending_id,
                pending_result_id=pending_result_id,
                observation_run_id=source.id,
                topic_id=stored_pending.topic_id,
                observation_date=stored_pending.observation_date,
                validation_sessions=[
                    day.isoformat() for day in stored_pending.validation_sessions
                ],
                baseline_evidence=stored_pending.model_dump(mode="json"),
                created_at=NOW,
            )
        )
        connection.execute(text("ALTER TABLE role_results ENABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations ENABLE TRIGGER USER")
        )

    operator_repository = type(materializer.market)(
        database,
        clock=lambda: pending.operation_at,
    )
    target = operator_repository.publish(
        operator_repository.import_batch(
            future_evidence[-1].context.market,
            key="validation-through-market",
        ).id
    )
    role_repository = RoleRepository(
        database,
        operator_repository,
        clock=lambda: pending.operation_at,
    )
    by_id = {
        run_id: evidence.model_copy(
            update={
                "role_run_id": str(run_id),
                "activation_event_id": str(publication_ids[run_id]),
            }
        )
        for run_id, evidence in zip(future_ids, future_evidence, strict=True)
    }
    monkeypatch.setattr(
        role_repository,
        "evidence",
        lambda _connection, row, _topic_id, **_kwargs: by_id[row["id"]],
    )
    immature = role_repository.validate_pending(
        role_inputs[0].id,
        key="immature-validation-command",
    )
    assert immature.mature_count == 0
    assert immature.immature_count == 1
    assert immature.validation_ids == ()
    original_append = role_repository._append_validation_record

    def fail_append(*_args, **_kwargs):
        raise RuntimeError("injected validation persistence failure")

    monkeypatch.setattr(
        role_repository,
        "_append_validation_record",
        fail_append,
    )
    with pytest.raises(RuntimeError, match="injected"):
        role_repository.validate_pending(target.id, key="failed-validation-command")
    with database.engine.connect() as connection:
        assert (
            connection.scalar(select(text("count(*)")).select_from(role_validations))
            == 0
        )
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_validation_commands)
                .where(
                    role_validation_commands.c.idempotency_key
                    == "failed-validation-command"
                )
            )
            == 0
        )

    monkeypatch.setattr(
        role_repository,
        "_append_validation_record",
        original_append,
    )
    first = role_repository.validate_pending(
        target.id,
        key="successful-validation-command",
    )
    replay = role_repository.validate_pending(
        target.id,
        key="successful-validation-command",
    )
    other_key = role_repository.validate_pending(
        target.id,
        key="same-validation-different-command",
    )

    assert first == replay
    assert other_key.validation_ids == first.validation_ids
    with pytest.raises(Conflict):
        role_repository.validate_pending(
            role_inputs[0].id,
            key="successful-validation-command",
        )
    assert first.mature_count == 1
    assert first.immature_count == 0
    assert len(first.validation_ids) == 1
    with database.engine.connect() as connection:
        validation = (
            connection.execute(
                select(role_validations).where(
                    role_validations.c.id == first.validation_ids[0]
                )
            )
            .mappings()
            .one()
        )
        inputs = tuple(
            connection.execute(
                select(role_validation_inputs.c.input_role_run_id)
                .where(
                    role_validation_inputs.c.validation_id == first.validation_ids[0]
                )
                .order_by(role_validation_inputs.c.position)
            ).scalars()
        )
    assert validation["outcome"] == "supported"
    assert inputs == tuple(future_ids)

    publication = PublicationService(
        database,
        materializer.market,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=role_inputs[0].id,
            topic_snapshot_id=role_inputs[1].id,
            role_input_id=source.id,
            disabled_domains=("news", "regulation"),
        ),
        key="publication-with-validation-evidence",
    )
    role_payload = next(
        result
        for result in PublicationService(
            database,
            materializer.market,
            clock=lambda: NOW,
        ).repository.list_results(publication.id)
        if result.domain == "role"
    ).payload
    topic_payload = role_payload["topics"][0]
    assert topic_payload["pending_validations"][0]["id"] == str(pending_id)
    assert topic_payload["role_validations"][0]["id"] == str(first.validation_ids[0])
    assert [
        item["input_role_run_id"]
        for item in topic_payload["role_validations"][0]["validation_inputs"]
    ] == [str(run_id) for run_id in future_ids]
    assert [
        item["input_role_run_id"] for item in topic_payload["role_validation_inputs"]
    ] == [str(run_id) for run_id in future_ids]


def test_validate_pending_reauthorizes_replay_inputs_atomically(
    materializer,
    role_inputs,
    database,
    repository,
    monkeypatch,
):
    pending, future_evidence = _validation_fixture(
        new_end="130",
        old_end="110",
        member_ends={"A": "120", "B": "125"},
    )
    source = _materialize(materializer, role_inputs, key="withdrawn-observation")
    second_source = _materialize(
        materializer,
        role_inputs,
        key="withdrawn-observation-second",
    )
    with database.engine.begin() as connection:
        connection.execute(text("ALTER TABLE role_results DISABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations DISABLE TRIGGER USER")
        )
        for observation_source in (source, second_source):
            pending_result_id = _result_row(connection, observation_source.id)
            observation_run = pending.observation_run.model_copy(
                update={"role_run_id": str(observation_source.id)}
            )
            stored_pending = pending.model_copy(
                update={
                    "pending_result_id": str(pending_result_id),
                    "observation_run_id": str(observation_source.id),
                    "observation_run": observation_run,
                }
            )
            connection.execute(
                insert(role_pending_validations).values(
                    id=uuid4(),
                    pending_result_id=pending_result_id,
                    observation_run_id=observation_source.id,
                    topic_id=stored_pending.topic_id,
                    observation_date=stored_pending.observation_date,
                    validation_sessions=[
                        day.isoformat() for day in stored_pending.validation_sessions
                    ],
                    baseline_evidence=stored_pending.model_dump(mode="json"),
                    created_at=NOW,
                )
            )
        connection.execute(text("ALTER TABLE role_results ENABLE TRIGGER USER"))
        connection.execute(
            text("ALTER TABLE role_pending_validations ENABLE TRIGGER USER")
        )

    target_source_id = "validation-target"
    target_grant = grant(source_id=target_source_id)
    repository.register_source(target_grant)
    target_batch = future_evidence[-1].context.market.model_copy(
        update={
            "source_id": target_source_id,
            "grant": target_grant,
            "security_states": tuple(
                item.model_copy(update={"source_id": target_source_id})
                for item in future_evidence[-1].context.market.security_states
            ),
            "bars": tuple(
                item.model_copy(update={"source_id": target_source_id})
                for item in future_evidence[-1].context.market.bars
            ),
            "limit_up_events": tuple(
                item.model_copy(update={"source_id": target_source_id})
                for item in future_evidence[-1].context.market.limit_up_events
            ),
        }
    )
    future_repository = type(repository)(
        database,
        clock=lambda: pending.operation_at,
    )
    target = future_repository.publish(
        future_repository.import_batch(
            target_batch,
            key="withdrawn-observation-target",
        ).id
    )
    topic_source_id = "validation-topic"
    topic_grant = grant(source_id=topic_source_id)
    repository.register_source(topic_grant)
    topic_repository = TopicRepository(
        database,
        clock=lambda: pending.operation_at,
    )
    future_topic_batch = role_inputs[1].batch.model_copy(
        update={
            "source_id": topic_source_id,
            "source_version": "s2",
            "grant": topic_grant,
        }
    )
    future_topic = topic_repository.publish(
        topic_repository.import_batch(
            future_topic_batch,
            key="withdrawn-observation-topic",
        ).id
    )

    future_ids = []
    publication_ids = {}
    with database.engine.begin() as connection:
        for position, evidence in enumerate(future_evidence, start=1):
            run_id = _materialized_clone(
                connection,
                source,
                key=f"withdrawn-observation-future-{position}",
                trade_date=evidence.trade_date,
                market_snapshot_id=target.id,
                topic_snapshot_id=future_topic.id,
            )
            future_ids.append(run_id)
            publication_ids[run_id] = _published_binding(
                connection,
                source.model_copy(
                    update={
                        "id": run_id,
                        "trade_date": evidence.trade_date,
                        "market_snapshot_id": target.id,
                        "topic_snapshot_id": future_topic.id,
                    }
                ),
                trade_date=evidence.trade_date,
                version=1,
            )

    role_repository = RoleRepository(
        database,
        future_repository,
        clock=lambda: pending.operation_at,
    )
    by_id = {
        run_id: evidence.model_copy(
            update={
                "role_run_id": str(run_id),
                "activation_event_id": str(publication_ids[run_id]),
            }
        )
        for run_id, evidence in zip(future_ids, future_evidence, strict=True)
    }
    monkeypatch.setattr(
        role_repository,
        "evidence",
        lambda _connection, row, _topic_id, **_kwargs: by_id[row["id"]],
    )

    first = role_repository.validate_pending(
        target.id,
        key="withdrawn-observation-command",
    )
    with database.engine.connect() as connection:
        command_before = dict(
            connection.execute(
                select(role_validation_commands).where(
                    role_validation_commands.c.idempotency_key
                    == "withdrawn-observation-command"
                )
            )
            .mappings()
            .one()
        )
        validations_before = tuple(
            connection.execute(select(role_validations).order_by(role_validations.c.id))
            .mappings()
            .all()
        )
        inputs_before = tuple(
            connection.execute(
                select(role_validation_inputs).order_by(
                    role_validation_inputs.c.validation_id,
                    role_validation_inputs.c.position,
                )
            )
            .mappings()
            .all()
        )
    assert command_before["validation_ids"] == [
        str(validation_id) for validation_id in first.validation_ids
    ]
    assert len(first.validation_ids) == 2
    assert {row["input_role_run_id"] for row in inputs_before} == set(future_ids)
    source_locks = []

    def observe_source_locks(
        conn,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        lock_key = str(parameters.get("key", ""))
        if "pg_advisory_xact_lock" in statement and lock_key.startswith("source:"):
            source_locks.append(lock_key)

    event.listen(database.engine, "after_cursor_execute", observe_source_locks)
    try:
        ordered_replay = role_repository.validate_pending(
            target.id,
            key="stable-new-validation-command",
        )
    finally:
        event.remove(database.engine, "after_cursor_execute", observe_source_locks)
    assert ordered_replay.validation_ids == first.validation_ids
    assert source_locks == sorted(set(source_locks))

    def fail_relay(*_args, **_kwargs):
        raise AssertionError("idempotent replay must not recompute validation")

    monkeypatch.setattr("dragon_radar.roles.repository.validate_relay", fail_relay)
    topic_repository.revoke_source(topic_source_id)
    with pytest.raises(Forbidden):
        role_repository.validate_pending(
            target.id,
            key="withdrawn-input-new-command",
        )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_validation_commands)
                .where(
                    role_validation_commands.c.idempotency_key
                    == "withdrawn-input-new-command"
                )
            )
            == 0
        )
        assert (
            tuple(
                connection.execute(
                    select(role_validations).order_by(role_validations.c.id)
                )
                .mappings()
                .all()
            )
            == validations_before
        )
        assert (
            tuple(
                connection.execute(
                    select(role_validation_inputs).order_by(
                        role_validation_inputs.c.validation_id,
                        role_validation_inputs.c.position,
                    )
                )
                .mappings()
                .all()
            )
            == inputs_before
        )
    topic_repository.register_source(topic_grant)

    future_repository.revoke_source(target_source_id)
    with pytest.raises(Forbidden):
        role_repository.validate_pending(
            target.id,
            key="withdrawn-observation-command",
        )
    future_repository.register_source(target_grant)

    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_storage(
        topic_source_id,
        policy=RetentionPolicy(
            reference="withdraw-validation-input",
            approved=True,
            action="deidentify",
        ),
    )
    retention.replay_withdrawals(topic_source_id)
    retention.restore_source(topic_source_id, topic_grant)
    with pytest.raises(Forbidden, match="withdrawn"):
        role_repository.validate_pending(
            target.id,
            key="withdrawn-input-archive-command",
        )
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(role_validation_commands)
                .where(
                    role_validation_commands.c.idempotency_key
                    == "withdrawn-input-archive-command"
                )
            )
            == 0
        )

    retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="withdraw-observation-run",
            approved=True,
            action="deidentify",
        ),
    )
    retention.replay_withdrawals("synthetic")
    retention.restore_source("synthetic", grant())

    with pytest.raises(Forbidden, match="withdrawn"):
        role_repository.validate_pending(
            target.id,
            key="withdrawn-observation-command",
        )

    with database.engine.connect() as connection:
        command_after = dict(
            connection.execute(
                select(role_validation_commands).where(
                    role_validation_commands.c.idempotency_key
                    == "withdrawn-observation-command"
                )
            )
            .mappings()
            .one()
        )
        validations_after = tuple(
            connection.execute(select(role_validations).order_by(role_validations.c.id))
            .mappings()
            .all()
        )
        inputs_after = tuple(
            connection.execute(
                select(role_validation_inputs).order_by(
                    role_validation_inputs.c.validation_id,
                    role_validation_inputs.c.position,
                )
            )
            .mappings()
            .all()
        )
    assert command_after == command_before
    assert validations_after == validations_before
    assert inputs_after == inputs_before


def test_role_materialize_cli_outputs_only_root_identity(
    database,
    role_inputs,
    monkeypatch,
    capsys,
):
    from dragon_radar.cli import main

    market, topic = role_inputs
    monkeypatch.setenv(
        "DRAGON_DATABASE_URL",
        database.engine.url.render_as_string(hide_password=False),
    )

    assert (
        main(
            [
                "role-materialize",
                "--market-snapshot",
                str(market.id),
                "--topic-snapshot",
                str(topic.id),
                "--rule-version",
                "role-v1",
                "--benchmark-id",
                "CSI300",
                "--key",
                "cli-role-output",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"role_run_id", "status", "trade_date"}
    assert payload["status"] == "materialized"


def test_role_validate_pending_cli_outputs_command_summary(
    database,
    role_inputs,
    monkeypatch,
    capsys,
):
    from dragon_radar.cli import main

    monkeypatch.setenv(
        "DRAGON_DATABASE_URL",
        database.engine.url.render_as_string(hide_password=False),
    )

    assert (
        main(
            [
                "role-validate-pending",
                "--through-market-snapshot",
                str(role_inputs[0].id),
                "--key",
                "cli-role-validation",
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "immature_count": 0,
        "mature_count": 0,
        "through_market_snapshot_id": str(role_inputs[0].id),
        "validation_ids": [],
    }


def test_role_payload_is_stable_json_without_embedded_context(
    materializer, role_inputs
):
    run = _materialize(materializer, role_inputs, key="minimal-wire")
    payload = RoleRepository(
        materializer.database,
        materializer.market,
        clock=lambda: NOW,
    ).payload(run.id)
    encoded = json.dumps(payload, sort_keys=True)
    topic = payload["topics"][0]
    assert set(
        (
            "change_summaries",
            "role_changes",
            "pending_validations",
            "role_validations",
            "role_validation_inputs",
        )
    ) <= set(topic)
    assert len(topic["change_summaries"]) == 6
    assert "context" not in payload
    assert "grant" not in encoded
    assert "rule_json" not in encoded
