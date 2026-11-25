"""Clock storage, materialization, and legacy projections."""

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from dragon_radar.publication.models import DomainMaterialization, PublicationResult
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService


def clock_module():
    name = "dragon_radar.publication.clocks"
    assert importlib.util.find_spec(name) is not None, "Publication clocks missing"
    return importlib.import_module(name)


def test_models_carry_nullable_immutable_clock_archive():
    assert "clock_lineage" in DomainMaterialization.model_fields
    assert "clock_lineage" in PublicationResult.model_fields
    assert DomainMaterialization().clock_lineage is None


def test_migration_chain_and_idempotent_live_metadata_upgrade():
    root = Path(__file__).resolve().parents[1]
    file = root / "src/dragon_radar/migrations/versions/0016_clock_lineage.py"
    assert file.exists(), "0016 clock lineage migration missing"
    text = file.read_text()
    assert 'down_revision = "0015_daily_ai"' in text
    assert "inspect(" in text
    assert "clock_lineage" in text
    assert "reject_immutable_mutation" in text


def test_sidecar_assertion_alone_cannot_verify_old_receipt():
    c = clock_module()
    from dragon_radar.domain.clocks import InputClockEvidence

    object_bytes = b"actual immutable supplier object"
    body = {
        "source_id": "s",
        "source_version": "v1",
        "archive_type": "market_import_batch",
        "archive_id": str(uuid4()),
        "revision_key": "1",
        "input_sha256": hashlib.sha256(object_bytes).hexdigest(),
        "source_event_at": None,
        "provider_published_at": None,
        "first_received_at": "2026-11-24T10:30:00Z",
    }
    evidence_bytes = json.dumps(body).encode()
    evidence = InputClockEvidence(
        **body, evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest()
    )
    with pytest.raises(ValueError, match="trusted"):
        c.verify_input_evidence(
            evidence, object_bytes, evidence_bytes, verifier=lambda *_: False
        )
    checked = c.verify_input_evidence(
        evidence, object_bytes, evidence_bytes, verifier=lambda *_: True
    )
    assert checked == evidence
    with pytest.raises(ValueError, match="hash"):
        c.verify_input_evidence(
            evidence, b"other object", evidence_bytes, verifier=lambda *_: True
        )
    wrong = {**body, "source_id": "other"}
    wrong_bytes = json.dumps(wrong).encode()
    altered = InputClockEvidence(
        **body, evidence_sha256=hashlib.sha256(wrong_bytes).hexdigest()
    )
    with pytest.raises(ValueError, match="binding"):
        c.verify_input_evidence(
            altered, object_bytes, wrong_bytes, verifier=lambda *_: True
        )


def test_publication_service_archives_completion_and_repository_exposes_exact_read():
    import inspect

    assert "archive_domain" in inspect.getsource(PublicationService.publish)
    assert hasattr(PublicationRepository, "get_lineage")


def test_projection_preserves_legacy_unknown_and_rejects_present_mismatch():
    from datetime import UTC, date, datetime
    from types import SimpleNamespace

    from dragon_radar.domain.clocks import DomainClockArchive
    from dragon_radar.http.errors import DataUnavailable
    from dragon_radar.publication.models import DOMAIN_NAMES

    now = datetime(2026, 11, 24, 10, 30, tzinfo=UTC)
    pub = SimpleNamespace(id=uuid4(), trade_date=date(2026, 11, 24), published_at=now)
    results = tuple(
        PublicationResult(
            publication_id=pub.id,
            domain=domain,
            input_id=uuid4(),
            status="ready",
            payload={},
            coverage={},
            quality_flags=(),
        )
        for domain in DOMAIN_NAMES
    )
    response = clock_module().publication_lineage(
        pub,
        results[:1]
        + tuple(
            result.model_copy(update={"status": "missing", "input_id": None})
            for result in results[1:]
        ),
        legacy_cutoffs={"market": now},
    )
    assert set(response["domains"]) == set(DOMAIN_NAMES)
    assert response["domains"]["market"]["as_of"] == now.isoformat()
    assert response["domains"]["market"]["clocks"]["computed_at"]["state"] == "unknown"
    assert (
        response["domains"]["topic"]["clocks"]["computed_at"]["reason"]
        == "domain_missing"
    )
    wrong = DomainClockArchive(
        domain="topic",
        input_id=results[0].input_id,
        as_of=now,
        status="ready",
        inputs=(),
        computed_at=now,
    )
    corrupt = results[0].model_copy(update={"clock_lineage": wrong})
    with pytest.raises(DataUnavailable):
        clock_module().publication_lineage(pub, (corrupt, *results[1:]))


def test_real_publication_completion_is_after_materializer_and_before_pub(
    database, repository
):
    from datetime import datetime, timedelta

    from factories import DAYS, NOW, batch
    from sqlalchemy import select

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.tables import publication_results

    market = repository.publish(repository.import_batch(batch(), key="b4-market").id)
    moments = [NOW + timedelta(seconds=i) for i in range(1, 100)]
    observations = []
    service = PublicationService(database, repository, clock=lambda: moments.pop(0))

    def materialize(_connection, _command):
        observations.append(moments[0])
        return DomainMaterialization(payload={"actual": True})

    pub = service.publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="b4-pub",
        materializers={"market": materialize},
    )
    with database.engine.connect() as connection:
        row = (
            connection.execute(
                select(publication_results).where(
                    publication_results.c.publication_id == pub.id,
                    publication_results.c.domain == "market",
                )
            )
            .mappings()
            .one()
        )
    completion = datetime.fromisoformat(row["clock_lineage"]["computed_at"])
    assert completion == observations[0]
    assert completion < pub.published_at
    reader = PublicationRepository(database, repository, clock=lambda: NOW)
    first = reader.get_lineage(pub.id, DAYS[-1])
    assert first == reader.get_lineage(pub.id, DAYS[-1])
    assert (
        first["domains"]["role"]["clocks"]["computed_at"]["reason"] == "domain_disabled"
    )
    assert (
        first["domains"]["market"]["clocks"]["first_received_at"]["state"] == "unknown"
    )


def test_evidence_database_replay_conflict_and_immutable_trigger(database):
    from datetime import UTC, datetime

    from sqlalchemy import inspect, text
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.domain.clocks import InputClockEvidence
    from dragon_radar.storage.repository import Conflict

    c = clock_module()
    body = {
        "source_id": "s",
        "source_version": "v1",
        "archive_type": "market_import_batch",
        "archive_id": str(uuid4()),
        "revision_key": "1",
        "input_sha256": hashlib.sha256(b"object").hexdigest(),
        "source_event_at": None,
        "provider_published_at": None,
        "first_received_at": datetime(2026, 11, 24, tzinfo=UTC).isoformat(),
    }
    raw = json.dumps(body).encode()
    evidence = InputClockEvidence(
        **body, evidence_sha256=hashlib.sha256(raw).hexdigest()
    )
    store = c.InputClockRepository()
    with database.engine.begin() as connection:
        assert (
            store.append(
                connection,
                evidence,
                object_bytes=b"object",
                evidence_bytes=raw,
                verifier=lambda *_: True,
            )
            == evidence
        )
        assert (
            store.append(
                connection,
                evidence,
                object_bytes=b"object",
                evidence_bytes=raw,
                verifier=lambda *_: True,
            )
            == evidence
        )
    changed_body = {**body, "first_received_at": "2026-11-24T00:00:01Z"}
    changed_raw = json.dumps(changed_body).encode()
    changed = InputClockEvidence(
        **changed_body, evidence_sha256=hashlib.sha256(changed_raw).hexdigest()
    )
    with pytest.raises(Conflict), database.engine.begin() as connection:
        store.append(
            connection,
            changed,
            object_bytes=b"object",
            evidence_bytes=changed_raw,
            verifier=lambda *_: True,
        )
    assert "clock_lineage" in {
        column["name"]
        for column in inspect(database.engine).get_columns("publication_results")
    }
    for operation in (
        "UPDATE input_clock_evidence SET source_version='x'",
        "DELETE FROM input_clock_evidence",
        "TRUNCATE input_clock_evidence",
    ):
        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(text(operation))


def test_missing_materialization_with_command_reference_fails_before_publish(
    database, repository
):
    from factories import DAYS, NOW, batch

    from dragon_radar.publication.models import PublicationCommand

    market = repository.publish(repository.import_batch(batch(), key="b4-missing").id)
    service = PublicationService(database, repository, clock=lambda: NOW)
    with pytest.raises(ValueError, match="reference"):
        service.publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=market.id,
                disabled_domains=("topic", "news", "regulation", "role"),
            ),
            key="b4-missing-pub",
            materializers={
                "market": lambda *_: DomainMaterialization(status="missing"),
            },
        )


def test_legacy_naive_cutoff_is_corrupt_not_local_timezone_inference():
    from datetime import UTC, date, datetime
    from types import SimpleNamespace

    from dragon_radar.http.errors import DataUnavailable
    from dragon_radar.publication.models import DOMAIN_NAMES

    pub = SimpleNamespace(
        id=uuid4(),
        trade_date=date(2026, 11, 24),
        published_at=datetime(2026, 11, 24, tzinfo=UTC),
    )
    results = tuple(
        PublicationResult(
            publication_id=pub.id,
            domain=domain,
            input_id=None,
            status="missing",
            payload={},
            coverage={},
            quality_flags=(),
        )
        for domain in DOMAIN_NAMES
    )
    with pytest.raises(DataUnavailable):
        clock_module().publication_lineage(
            pub, results, legacy_cutoffs={"market": datetime(2026, 11, 24)}
        )


def test_backward_publish_rolls_back_every_domain_row(database, repository):
    from datetime import timedelta

    from factories import DAYS, NOW, batch
    from sqlalchemy import func, select

    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.tables import publication_results, publications

    market = repository.publish(repository.import_batch(batch(), key="b4-backward").id)
    moments = iter((NOW + timedelta(seconds=2), NOW, NOW))
    service = PublicationService(database, repository, clock=lambda: next(moments))
    with pytest.raises(ValueError, match="publication"):
        service.publish(
            PublicationCommand(
                trade_date=DAYS[-1],
                market_snapshot_id=market.id,
                disabled_domains=("topic", "news", "regulation", "role"),
            ),
            key="b4-backward-pub",
            materializers={
                "market": lambda *_: DomainMaterialization(),
            },
        )
    with database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(publications)) == 0
        assert (
            connection.scalar(select(func.count()).select_from(publication_results))
            == 0
        )


def test_migration_upgrades_existing_storage_without_column_and_is_idempotent(database):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    migration = importlib.import_module(
        "dragon_radar.migrations.versions.0016_clock_lineage"
    )
    with database.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE publication_results DROP COLUMN clock_lineage")
        )
        connection.execute(text("DROP TABLE input_clock_evidence"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert "clock_lineage" in {
            column["name"]
            for column in inspect(connection).get_columns("publication_results")
        }
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid='input_clock_evidence'::regclass "
                    "AND NOT tgisinternal"
                )
            )
            == 3
        )


def test_verified_daily_object_clocks_are_archived_and_current_public_rights_rechecked(
    database, repository
):
    from datetime import timedelta

    from factories import DAYS, NOW, batch, grant
    from sqlalchemy import select

    from dragon_radar.domain.clocks import InputClockEvidence
    from dragon_radar.publication.access import PublicSharingAccessPolicy
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.storage.models import import_batches
    from dragon_radar.storage.repository import Forbidden

    repository.register_source(grant(sharing=True))
    imported = repository.import_batch(
        batch(grant=grant(sharing=True)), key="b4-verified"
    )
    market = repository.publish(imported.id)
    with database.engine.begin() as connection:
        row = (
            connection.execute(
                select(import_batches).where(import_batches.c.id == imported.id)
            )
            .mappings()
            .one()
        )
        object_bytes = row["canonical_json"].encode()
        source = json.loads(row["canonical_json"])
        body = {
            "source_id": source["source_id"],
            "archive_type": "market_import_batch",
            "archive_id": str(imported.id),
            "revision_key": "1",
            "source_version": source["source_version"],
            "input_sha256": hashlib.sha256(object_bytes).hexdigest(),
            "source_event_at": (NOW - timedelta(seconds=3)).isoformat(),
            "provider_published_at": (NOW - timedelta(seconds=2)).isoformat(),
            "first_received_at": (NOW - timedelta(seconds=1)).isoformat(),
        }
        raw = json.dumps(body).encode()
        evidence = InputClockEvidence(
            **body, evidence_sha256=hashlib.sha256(raw).hexdigest()
        )
        clock_module().InputClockRepository().append(
            connection,
            evidence,
            object_bytes=object_bytes,
            evidence_bytes=raw,
            verifier=lambda *_: True,
        )
    service = PublicationService(database, repository, clock=lambda: NOW)
    pub = service.publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="b4-verified-pub",
        materializers={"market": lambda *_: DomainMaterialization()},
    )
    reader = PublicationRepository(
        database,
        repository,
        clock=lambda: NOW,
        access_policy=PublicSharingAccessPolicy(lambda: NOW),
    )
    response = reader.get_lineage(pub.id, DAYS[-1])
    assert all(
        response["domains"]["market"]["clocks"][name]["state"] == "known"
        for name in (
            "source_event_at",
            "provider_published_at",
            "first_received_at",
            "computed_at",
            "snapshot_published_at",
        )
    )
    assert response == reader.get_lineage(pub.id, DAYS[-1])
    repository.register_source(grant(sharing=False))
    with pytest.raises(Forbidden):
        reader.get_lineage(pub.id, DAYS[-1])
