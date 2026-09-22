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
from test_role_history import _validation_fixture
from topic_factories import (
    TOPIC_DAYS,
    coverage_cell,
    topic_batch,
    topic_batch_dict,
    topic_definition,
    topic_membership,
)

from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import publications
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


def _draft_run(connection, source_run, *, key, trade_date=None):
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


def _validation_values(pending_id, *, revision, supersedes):
    return {
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


def _materialized_clone(connection, source_run, *, key, trade_date):
    run_id = _draft_run(
        connection,
        source_run,
        key=key,
        trade_date=trade_date,
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
