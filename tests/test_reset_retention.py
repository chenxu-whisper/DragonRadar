"""Source dependency, withdrawal, retention, and recovery contracts."""

from datetime import timedelta
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch, grant
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import DBAPIError
from topic_factories import topic_batch

from dragon_radar.publication.models import PublicationCommand, RetentionPolicy
from dragon_radar.publication.retention import PublicationCache, RetentionService
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import (
    publication_module_controls,
    publication_results,
    source_dependencies,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.models import source_grants
from dragon_radar.storage.repository import Conflict, Forbidden, Repository
from dragon_radar.topics.repository import TopicRepository


@pytest.fixture
def published(database, repository):
    topic_repository = TopicRepository(database, clock=lambda: NOW)
    market = repository.publish(
        repository.import_batch(batch(), key="retention-market").id
    )
    topic = topic_repository.publish(
        topic_repository.import_batch(topic_batch(), key="retention-topic").id
    )
    service = PublicationService(database, repository, clock=lambda: NOW)
    command = PublicationCommand(
        trade_date=DAYS[-1],
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        disabled_domains=("news", "regulation", "role"),
    )
    return service, service.publish(command, key="retention-publication")


def test_publication_indexes_source_archive_and_derived_result(published, database):
    _service, record = published
    with database.engine.connect() as connection:
        rows = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == record.id
                )
            )
            .mappings()
            .all()
        )
    assert {(row["archive_type"], row["derived_type"]) for row in rows} == {
        ("market_import_batch", "market"),
        ("topic_import_batch", "topic"),
    }
    assert all(row["source_id"] == "synthetic" for row in rows)
    assert all(row["derived_id"] == record.id for row in rows)


def test_display_withdrawal_blocks_reads_clears_cache_and_preserves_storage(
    published, database, repository
):
    service, record = published
    cache = PublicationCache()
    cache.put("synthetic", record.id, {"protected": True})
    retention = RetentionService(database, repository, cache=cache, clock=lambda: NOW)
    with database.engine.connect() as connection:
        before = {
            table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("import_batches", "snapshots", "publication_results")
        }

    withdrawal = retention.withdraw_display("synthetic")

    assert withdrawal.scope == "display"
    assert withdrawal.action == "blocked"
    assert cache.get(record.id) is None
    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)
    assert service.repository.list_snapshots() == []
    with database.engine.connect() as connection:
        after = {
            table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in before
        }
    assert after == before


def test_display_withdrawal_cannot_be_bypassed_by_registering_a_new_grant(
    published, database, repository
):
    service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_display("synthetic")

    repository.register_source(grant())

    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)

    retention.replay_withdrawals("synthetic")
    retention.restore_source("synthetic", grant())
    assert service.repository.get_snapshot(record.id).id == record.id


def test_storage_withdrawal_without_policy_disables_modules_without_deleting(
    published, database, repository
):
    service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    with database.engine.connect() as connection:
        before = connection.scalar(
            select(text("count(*)")).select_from(publication_results)
        )

    withdrawal = retention.withdraw_storage("synthetic")

    assert withdrawal.action == "blocked"
    assert withdrawal.policy_reference is None
    with database.engine.connect() as connection:
        current = connection.execute(
            select(source_grants.c.grant).where(
                source_grants.c.source_id == "synthetic"
            )
        ).scalar_one()
        controls = (
            connection.execute(
                select(publication_module_controls).where(
                    publication_module_controls.c.source_id == "synthetic"
                )
            )
            .mappings()
            .all()
        )
        assert (
            connection.scalar(select(text("count(*)")).select_from(publication_results))
            == before
        )
    assert current["local_storage"] is False
    assert current["local_display"] is False
    assert {row["module"] for row in controls} == {"market", "topic"}
    assert all(row["enabled"] is False for row in controls)
    assert all(row["blocked_by_withdrawal"] is True for row in controls)
    retention.replay_withdrawals("synthetic")
    with pytest.raises(Conflict):
        retention.restore_source("synthetic", grant())
    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)


def test_display_block_cannot_mask_pending_storage_policy_requirement(
    published, database, repository
):
    _service, _record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_storage("synthetic")
    retention.withdraw_display("synthetic")
    retention.replay_withdrawals("synthetic")

    with pytest.raises(Conflict):
        retention.restore_source("synthetic", grant())

    with database.engine.connect() as connection:
        controls = (
            connection.execute(
                select(publication_module_controls).where(
                    publication_module_controls.c.source_id == "synthetic"
                )
            )
            .mappings()
            .all()
        )
    assert controls
    assert all(not row["enabled"] for row in controls)
    assert all(row["blocked_by_withdrawal"] for row in controls)


def test_approved_storage_policy_survives_later_display_block(
    published, database, repository
):
    service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-storage-before-display",
            approved=True,
            action="deidentify",
        ),
    )
    retention.withdraw_display("synthetic")
    retention.replay_withdrawals("synthetic")

    restored = retention.restore_source("synthetic", grant())

    assert restored.action == "restored"
    with database.engine.connect() as connection:
        controls = (
            connection.execute(
                select(publication_module_controls).where(
                    publication_module_controls.c.source_id == "synthetic"
                )
            )
            .mappings()
            .all()
        )
    assert controls
    assert all(row["enabled"] for row in controls)
    assert all(not row["blocked_by_withdrawal"] for row in controls)
    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)


def test_restore_rejects_replay_below_latest_pending_block(
    published, database, repository
):
    _service, _record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    blocked = retention.withdraw_display("synthetic")
    with database.engine.begin() as connection:
        connection.execute(
            insert(withdrawal_records).values(
                id=uuid4(),
                source_id="synthetic",
                scope="display",
                action="replayed",
                policy_reference=None,
                details={"through_sequence": blocked.sequence - 1},
                occurred_at=NOW,
            )
        )

    with pytest.raises(Conflict):
        retention.restore_source("synthetic", grant())


def test_replay_ignores_withdrawals_before_latest_restore(
    published, database, repository
):
    _service, _record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-closed-ledger",
            approved=True,
            action="deidentify",
        ),
    )
    retention.replay_withdrawals("synthetic")
    retention.restore_source("synthetic", grant())

    with pytest.raises(Conflict):
        retention.replay_withdrawals("synthetic")


def test_approved_policy_deidentifies_derived_content_and_keeps_tombstones(
    published, database, repository
):
    _service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    policy = RetentionPolicy(
        reference="approved-policy-2026-09",
        approved=True,
        action="deidentify",
    )

    withdrawal = retention.withdraw_storage("synthetic", policy=policy)

    assert withdrawal.action == "deidentified"
    with database.engine.connect() as connection:
        ready = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == record.id,
                    publication_results.c.domain.in_(("market", "topic")),
                )
            )
            .mappings()
            .all()
        )
        ledger = (
            connection.execute(
                select(withdrawal_records)
                .where(withdrawal_records.c.source_id == "synthetic")
                .order_by(withdrawal_records.c.sequence)
            )
            .mappings()
            .all()
        )
    assert all(row["status"] == "disabled" for row in ready)
    assert all(row["payload"] == {} for row in ready)
    assert all(row["coverage"] == {} for row in ready)
    assert all(row["quality_flags"] == ["withdrawn"] for row in ready)
    assert [row["action"] for row in ledger] == ["blocked", "deidentified"]
    assert all("protected" not in str(row["details"]) for row in ledger)


def test_restore_requires_retention_action_then_ledger_replay(
    published, database, repository
):
    service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-delete-v1",
            approved=True,
            action="deidentify",
        ),
    )
    with pytest.raises(Conflict):
        retention.restore_source("synthetic", grant())

    replay = retention.replay_withdrawals("synthetic")
    restored = retention.restore_source("synthetic", grant())

    assert replay.action == "replayed"
    assert restored.action == "restored"
    with database.engine.connect() as connection:
        actions = (
            connection.execute(
                select(withdrawal_records.c.action)
                .where(withdrawal_records.c.source_id == "synthetic")
                .order_by(withdrawal_records.c.sequence)
            )
            .scalars()
            .all()
        )
        controls = (
            connection.execute(
                select(publication_module_controls).where(
                    publication_module_controls.c.source_id == "synthetic"
                )
            )
            .mappings()
            .all()
        )
    assert actions == ["blocked", "deidentified", "replayed", "restored"]
    assert controls
    assert all(row["enabled"] for row in controls)
    assert all(not row["blocked_by_withdrawal"] for row in controls)
    with pytest.raises(Forbidden):
        service.repository.get_snapshot(record.id)
    with pytest.raises(Forbidden):
        service.publish(
            PublicationCommand.model_validate_json(record.command_json),
            key="must-not-republish-purged-inputs",
        )


def test_destructive_withdrawal_only_applies_to_dependencies_existing_at_withdrawal(
    published, database, repository
):
    service, old_record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    withdrawal = retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-deidentify-v2",
            approved=True,
            action="deidentify",
        ),
    )
    retention.replay_withdrawals("synthetic")
    retention.restore_source("synthetic", grant())

    market = repository.publish(
        repository.import_batch(
            batch(),
            key="restored-market",
        ).id
    )
    topic_repository = TopicRepository(database, clock=lambda: NOW)
    topic = topic_repository.publish(
        topic_repository.import_batch(
            topic_batch(),
            key="restored-topic",
        ).id
    )
    new_record = service.publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            disabled_domains=("news", "regulation", "role"),
        ),
        key="restored-publication",
    )

    assert service.repository.get_snapshot(new_record.id).id == new_record.id
    with pytest.raises(Forbidden):
        service.repository.get_snapshot(old_record.id)
    with database.engine.connect() as connection:
        bound = connection.scalar(
            text(
                "SELECT count(*) FROM withdrawal_dependencies "
                "WHERE withdrawal_id = :withdrawal_id"
            ),
            {"withdrawal_id": withdrawal.id},
        )
    assert bound > 0
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            delete(publication_results).where(
                publication_results.c.publication_id == new_record.id,
                publication_results.c.domain == "market",
            )
        )


def _withdrawal_dependency_values(**values):
    legacy_defaults = {
        "source_id": "synthetic",
        "derived_type": None,
        "derived_id": None,
        "publication_id": None,
    }
    return {
        **{
            name: value
            for name, value in legacy_defaults.items()
            if name in withdrawal_dependencies.c
        },
        **values,
    }


def test_database_rejects_binding_old_withdrawal_to_future_dependency(
    published, database, repository
):
    _service, _old_record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    withdrawal = retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-future-binding",
            approved=True,
            action="deidentify",
        ),
    )
    retention.replay_withdrawals("synthetic")
    retention.restore_source("synthetic", grant())

    future = NOW + timedelta(days=1)
    future_repository = Repository(database, clock=lambda: future)
    market = future_repository.publish(
        future_repository.import_batch(batch(), key="future-binding-market").id
    )
    topics = TopicRepository(database, clock=lambda: future)
    topic = topics.publish(
        topics.import_batch(topic_batch(), key="future-binding-topic").id
    )
    future_record = PublicationService(
        database, future_repository, clock=lambda: future
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            disabled_domains=("news", "regulation", "role"),
        ),
        key="future-binding-publication",
    )
    with database.engine.connect() as connection:
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == future_record.id,
                    source_dependencies.c.derived_type == "market",
                )
            )
            .mappings()
            .one()
        )

    values = {
        "id": uuid4(),
        "withdrawal_id": withdrawal.id,
        "source_dependency_id": dependency["id"],
        "created_at": NOW,
    }
    if "source_id" in withdrawal_dependencies.c:
        values = _withdrawal_dependency_values(
            **values,
            archive_type=dependency["archive_type"],
            archive_id=dependency["archive_id"],
            derived_type=dependency["derived_type"],
            derived_id=dependency["derived_id"],
            publication_id=dependency["publication_id"],
        )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(withdrawal_dependencies).values(**values))


def test_database_rejects_mixed_withdrawal_dependency_shape(published, database):
    _service, record = published
    with database.engine.begin() as connection:
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == record.id,
                    source_dependencies.c.derived_type == "market",
                )
            )
            .mappings()
            .one()
        )
        withdrawal = (
            connection.execute(
                insert(withdrawal_records)
                .values(
                    id=uuid4(),
                    source_id="synthetic",
                    scope="storage",
                    action="deleted",
                    policy_reference="shape-test",
                    details={},
                    occurred_at=NOW,
                )
                .returning(withdrawal_records)
            )
            .mappings()
            .one()
        )

    values = _withdrawal_dependency_values(
        id=uuid4(),
        withdrawal_id=withdrawal["id"],
        source_dependency_id=dependency["id"],
        archive_type=dependency["archive_type"],
        archive_id=dependency["archive_id"],
        created_at=NOW,
    )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(insert(withdrawal_dependencies).values(**values))


@pytest.mark.parametrize(
    ("scope", "action", "policy_reference"),
    [
        ("display", "deidentified", "forged-policy"),
        ("display", "deleted", "forged-policy"),
        ("storage", "deidentified", None),
        ("storage", "deleted", None),
        ("display", "blocked", "forged-policy"),
        ("storage", "replayed", "forged-policy"),
        ("display", "restored", "forged-policy"),
    ],
)
def test_database_rejects_invalid_withdrawal_scope_action_policy(
    published,
    database,
    scope,
    action,
    policy_reference,
):
    _service, record = published
    with database.engine.connect() as connection:
        before = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == record.id
                )
            )
            .mappings()
            .all()
        )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(withdrawal_records).values(
                id=uuid4(),
                source_id="synthetic",
                scope=scope,
                action=action,
                policy_reference=policy_reference,
                details={},
                occurred_at=NOW,
            )
        )

    with database.engine.connect() as connection:
        after = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == record.id
                )
            )
            .mappings()
            .all()
        )
    assert after == before


def test_result_retention_trigger_rejects_display_deleted_legacy_record(
    published, database
):
    _service, record = published
    withdrawal_id = uuid4()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE withdrawal_records DROP CONSTRAINT IF EXISTS "
                "withdrawal_records_scope_action_policy"
            )
        )
        connection.execute(
            insert(withdrawal_records).values(
                id=withdrawal_id,
                source_id="synthetic",
                scope="display",
                action="deleted",
                policy_reference="forged-display-policy",
                details={},
                occurred_at=NOW,
            )
        )
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == record.id,
                    source_dependencies.c.derived_type == "market",
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(withdrawal_dependencies).values(
                id=uuid4(),
                withdrawal_id=withdrawal_id,
                source_dependency_id=dependency["id"],
                created_at=NOW,
            )
        )

    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            delete(publication_results).where(
                publication_results.c.publication_id == record.id,
                publication_results.c.domain == "market",
            )
        )

    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(text("count(*)"))
                .select_from(publication_results)
                .where(
                    publication_results.c.publication_id == record.id,
                    publication_results.c.domain == "market",
                )
            )
            == 1
        )


def test_archive_tombstone_uniqueness_cannot_be_bypassed_by_null(
    published, database, repository
):
    _service, record = published
    retention = RetentionService(database, repository, clock=lambda: NOW)
    withdrawal = retention.withdraw_storage(
        "synthetic",
        policy=RetentionPolicy(
            reference="approved-tombstone-unique",
            approved=True,
            action="deidentify",
        ),
    )
    with database.engine.connect() as connection:
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == record.id,
                    source_dependencies.c.derived_type == "market",
                )
            )
            .mappings()
            .one()
        )
    common = {
        "withdrawal_id": withdrawal.id,
        "source_dependency_id": None,
        "archive_type": dependency["archive_type"],
        "archive_id": dependency["archive_id"],
        "created_at": NOW,
    }
    with database.engine.begin() as connection:
        connection.execute(
            insert(withdrawal_dependencies).values(
                **_withdrawal_dependency_values(id=uuid4(), **common)
            )
        )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            insert(withdrawal_dependencies).values(
                **_withdrawal_dependency_values(id=uuid4(), **common)
            )
        )
