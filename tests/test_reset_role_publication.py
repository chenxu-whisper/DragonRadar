"""Persistent role runs and publication-bound evidence."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from factories import DAYS, NOW, batch, grant
from sqlalchemy import insert, inspect, select, text, update
from sqlalchemy.exc import DBAPIError
from topic_factories import topic_batch

from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.service import PublicationService
from dragon_radar.roles.materialize import RoleMaterializer
from dragon_radar.roles.repository import RoleRepository
from dragon_radar.roles.tables import (
    role_factor_observations,
    role_limit_rows,
    role_materialization_attempts,
    role_results,
    role_run_topics,
    role_runs,
    role_topic_statuses,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound
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
        "role_materialization_attempts",
    } <= tables
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0007_role_readmodels"
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
    assert counts["role_results"] >= 0
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
    assert "context" not in payload
    assert "grant" not in encoded
    assert "rule_json" not in encoded
