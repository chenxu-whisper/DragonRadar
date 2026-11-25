"""Real owner/revision/rights transactions in disposable dragon_test schemas."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from factories import NOW
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.storage.repository import Conflict, Forbidden


@pytest.fixture
def setup(database, repository, monkeypatch):
    import test_reset_ai_tasks as existing

    from dragon_radar.ai.publication import AIPublicationService

    monkeypatch.setenv("DRAGON_TOPIC_CURSOR_SECRET", "b3-disposable-test-only-secret")
    original = existing.grant
    monkeypatch.setattr(
        existing, "grant", lambda **values: original(sharing=True, **values)
    )
    tasks, service, owners, session, publication = existing.ai_setup.__wrapped__(
        database, repository
    )
    promotion = AIPublicationService(tasks, service.publications, clock=lambda: NOW)
    promotion.public.entitlements.register(
        {
            "reference": "output-test-v1",
            "provider": "fake",
            "model_version": "fake-v1",
            "starts_at": NOW - timedelta(days=1),
            "expires_at": NOW + timedelta(days=1),
            "storage": True,
            "display": True,
            "sharing": True,
            "audience": "public",
            "policy_reference": "synthetic-only",
        }
    )
    return tasks, service, session, publication, promotion


def accepted(setup, *, kind="daily_review", key="submit"):
    from news_factories import ARTICLE_A

    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.tables import ai_task_reviews
    from dragon_radar.public_daily_ai.models import digest

    tasks, service, session, publication, promotion = setup
    task = service.submit(
        session.token,
        session.csrf,
        task_type=kind,
        snapshot_id=publication.id,
        as_of=publication.published_at,
        version=f"v{publication.version}",
        task_input={"date": publication.trade_date.isoformat(), "notes_context": ""}
        if kind == "daily_review"
        else {"article_id": str(ARTICLE_A)},
        key=key,
    )
    output = {
        "facts": [],
        "inferences": [],
        "citations": [],
        "missing_evidence": [
            {"text": "Synthetic missing evidence", "reason": "test_only"}
        ],
    }
    result = tasks.succeed(
        tasks.claim("synthetic-worker"),
        AIProviderResult(content=output, token_usage=1, cost="0"),
        provider="fake",
        model_version="fake-v1",
        prompt_version="prompt-v1",
        latency_ms=1,
        max_tokens=100,
        max_cost=Decimal("1"),
    )
    tasks.review(
        session.token,
        session.csrf,
        task.id,
        decision="accept",
        revision=result.content_draft.revision,
        key="accept-" + key,
    )
    with tasks.database.engine.connect() as connection:
        review_id = connection.scalar(
            select(ai_task_reviews.c.id).where(
                ai_task_reviews.c.task_id == task.id,
                ai_task_reviews.c.decision == "accept",
            )
        )
    command = {
        "revision": result.content_draft.revision,
        "expected_review_id": str(review_id),
        "expected_content_hash": digest(output),
        "snapshot_id": str(publication.id),
        "date": publication.trade_date.isoformat(),
        "idempotency_key": "publish-" + key,
        "publication_consent_reference": "consent-test",
        "output_entitlement_reference": "output-test-v1",
        "attestation_reference": "nonpersonal-test",
    }
    if kind == "daily_review":
        command["expected_archive_version"] = 0
    return task, command


def test_publish_replay_revision_and_current_rights(setup):
    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    receipt = promotion.publish_daily(session.token, session.csrf, task.id, command)
    assert receipt["archive_version"] == 1
    assert (
        promotion.publish_daily(session.token, session.csrf, task.id, command)
        == receipt
    )
    with pytest.raises(Conflict):
        promotion.publish_daily(
            session.token,
            session.csrf,
            task.id,
            {**command, "attestation_reference": "changed"},
        )
    snapshot, row = promotion.public.get(
        publication.id, publication.trade_date, archive_id=receipt["archive_id"]
    )
    assert snapshot.id == publication.id
    assert "task_id" not in str(row["safe_payload"])
    promotion.public.entitlements.revoke(
        "output-test-v1", key="revoke-output", reason_reference="withdrawn"
    )
    with pytest.raises(Forbidden):
        promotion.publish_daily(session.token, session.csrf, task.id, command)
    with pytest.raises(Forbidden):
        promotion.public.get(
            publication.id, publication.trade_date, archive_id=receipt["archive_id"]
        )
    assert promotion.public.get(publication.id, publication.trade_date)[1] is None


def test_owner_revoke_cannot_mutate_archive(setup):
    from dragon_radar.public_daily_ai.tables import public_daily_ai_archives

    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    receipt = promotion.publish_daily(session.token, session.csrf, task.id, command)
    with pytest.raises(SQLAlchemyError), tasks.database.engine.begin() as connection:
        connection.execute(
            update(public_daily_ai_archives)
            .where(
                public_daily_ai_archives.c.id == UUID(receipt["archive_id"]),
            )
            .values(safe_payload={"erased": True})
        )
    promotion.revoke(
        session.token,
        session.csrf,
        task.id,
        receipt["archive_id"],
        {
            "expected_archive_version": 1,
            "idempotency_key": "owner-revoke",
            "reason_code": "withdrawn_by_owner",
        },
    )
    with pytest.raises(Forbidden):
        promotion.public.get(
            publication.id, publication.trade_date, archive_id=receipt["archive_id"]
        )
    with tasks.database.engine.connect() as connection:
        assert (
            connection.scalar(select(public_daily_ai_archives.c.content_hash))
            == receipt["content_hash"]
        )


def test_discovery_watermark_excludes_new_versions(setup):
    from dragon_radar.public_daily_ai.service import PublicDailyAIService

    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    promotion.publish_daily(session.token, session.csrf, task.id, command)
    promotion.publish_daily(
        session.token,
        session.csrf,
        task.id,
        {
            **command,
            "idempotency_key": "v2",
            "expected_archive_version": 1,
        },
    )
    public = PublicDailyAIService(promotion.public)
    first = public.discover(publication.id, publication.trade_date, limit=1)
    promotion.publish_daily(
        session.token,
        session.csrf,
        task.id,
        {
            **command,
            "idempotency_key": "v3",
            "expected_archive_version": 2,
        },
    )
    second = public.discover(
        publication.id, publication.trade_date, limit=1, cursor=first["next_cursor"]
    )
    assert [item["archive_version"] for item in first["items"]] == [2]
    assert [item["archive_version"] for item in second["items"]] == [1]


def test_summary_promotes_exact_article_only_and_registry(setup):
    from dragon_radar.news.summary_publication import NewsSummaryArtifact

    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup, kind="summarize")
    receipt = promotion.publish_summary(session.token, session.csrf, task.id, command)
    assert receipt["summary_revision"] == 1
    with tasks.database.engine.begin() as connection:
        snapshot = promotion.public.snapshot(
            connection, publication.id, publication.trade_date, kind="summary"
        )
        article = snapshot.result("news").model_dump(mode="json")["payload"][
            "articles"
        ][0]
        artifact = promotion.public.summary(
            connection, receipt["summary_id"], 1, article
        )
        assert (
            NewsSummaryArtifact.model_validate(artifact).text
            == "Synthetic missing evidence"
        )
        assert artifact["article_revision_id"] == receipt["article_revision_id"]


def test_migration_chain_tables_and_immutable_licenses(database):
    from sqlalchemy import inspect

    from dragon_radar.public_daily_ai.tables import OWNED_TABLES

    inspector = inspect(database.engine)
    assert {table.name for table in OWNED_TABLES} <= set(inspector.get_table_names())
    with database.engine.connect() as connection:
        triggers = (
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                    "AND tgrelid = 'public_daily_ai_archives'::regclass",
                )
            )
            .scalars()
            .all()
        )
        assert "public_daily_ai_archives_erasure" in triggers


def test_private_routes_require_live_owner_csrf_and_exact_body(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_reset_ai_tasks import _headers

    from dragon_radar.http.routes.ai_publication import install_ai_publication_routes
    from dragon_radar.owner.security import OwnerGate

    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    app = FastAPI()
    app.state.ai_publication_service = promotion
    app.add_middleware(
        OwnerGate, origin="https://localhost:8030", owner_repository=tasks.owners
    )
    install_ai_publication_routes(app)
    path = f"/api/ai/task/{task.id}/publish-daily"
    with TestClient(app, base_url="https://localhost:8030") as client:
        assert client.post(path, json=command).status_code == 403
        assert (
            client.post(
                path, json=command, headers={"Origin": "https://localhost:8030"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path, json=command, headers=_headers(session, csrf=False)
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                json={**command, "owner_id": str(session.owner_id)},
                headers=_headers(session),
            ).status_code
            == 400
        )
        assert (
            client.post(
                path + "?unexpected=1", json=command, headers=_headers(session)
            ).status_code
            == 400
        )
        response = client.post(path, json=command, headers=_headers(session))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_eligibility_is_private_and_fresh(setup):
    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    result = promotion.eligibility(session.token, task.id, "output-test-v1")
    assert result["eligible"] is True
    assert result["expected_content_hash"] == command["expected_content_hash"]
    promotion.public.entitlements.revoke(
        "output-test-v1", key="withdraw", reason_reference="test"
    )
    assert (
        promotion.eligibility(session.token, task.id, "output-test-v1")["eligible"]
        is False
    )


def test_sealed_storage_erasure_preserves_hash_and_cannot_restore(setup):
    from uuid import uuid4

    from sqlalchemy import insert

    from dragon_radar.public_daily_ai.retention import TOMBSTONE, erase_artifact
    from dragon_radar.public_daily_ai.tables import public_daily_ai_archives
    from dragon_radar.publication.tables import (
        source_dependencies,
        withdrawal_binding_seals,
        withdrawal_dependencies,
        withdrawal_records,
    )

    tasks, service, session, publication, promotion = setup
    task, command = accepted(setup)
    receipt = promotion.publish_daily(session.token, session.csrf, task.id, command)
    artifact_id, withdrawal_id = UUID(receipt["archive_id"]), uuid4()
    with tasks.database.engine.begin() as connection:
        dependency = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.derived_id == artifact_id,
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(withdrawal_records).values(
                id=withdrawal_id,
                source_id=dependency["source_id"],
                scope="storage",
                action="deleted",
                policy_reference="synthetic-retention",
                cutoff_dependency_sequence=dependency["dependency_sequence"],
                details={},
                occurred_at=NOW,
            )
        )
        bound = (
            connection.execute(
                select(source_dependencies.c.id).where(
                    source_dependencies.c.source_id == dependency["source_id"],
                    source_dependencies.c.dependency_sequence
                    <= dependency["dependency_sequence"],
                )
            )
            .scalars()
            .all()
        )
        for dependency_id in bound:
            connection.execute(
                insert(withdrawal_dependencies).values(
                    id=uuid4(),
                    withdrawal_id=withdrawal_id,
                    source_dependency_id=dependency_id,
                    created_at=NOW,
                )
            )
        with pytest.raises(Forbidden):
            erase_artifact(
                connection,
                withdrawal_id=withdrawal_id,
                artifact_id=artifact_id,
                derived_type="daily_ai",
            )
        connection.execute(
            insert(withdrawal_binding_seals).values(
                withdrawal_id=withdrawal_id, sealed_at=NOW
            )
        )
        erase_artifact(
            connection,
            withdrawal_id=withdrawal_id,
            artifact_id=artifact_id,
            derived_type="daily_ai",
        )
    with tasks.database.engine.connect() as connection:
        row = connection.execute(select(public_daily_ai_archives)).mappings().one()
        assert row["safe_payload"] == TOMBSTONE
        assert row["content_hash"] == receipt["content_hash"]
    with pytest.raises(SQLAlchemyError), tasks.database.engine.begin() as connection:
        connection.execute(
            update(public_daily_ai_archives).values(safe_payload={"facts": []})
        )
