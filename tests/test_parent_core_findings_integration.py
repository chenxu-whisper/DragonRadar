"""Exact F1-F5 PostgreSQL regressions; run only with exclusive DB ownership."""

from uuid import UUID

import pytest
from factories import NOW, batch, grant
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_b3_daily_ai_integration import accepted
from test_b3_daily_ai_integration import setup as setup
from test_reset_ai_tasks import _headers

from dragon_radar.http.access import PublicPublicationRepository
from dragon_radar.http.public_app import create_public_app
from dragon_radar.http.routes.ai_publication import install_ai_publication_routes
from dragon_radar.owner.security import OwnerGate
from dragon_radar.public_daily_ai.tables import promoted_news_summaries
from dragon_radar.publication.models import PublicationCommand, RetentionPolicy
from dragon_radar.publication.retention import RetentionService
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import source_dependencies
from dragon_radar.storage.repository import Forbidden


def selected_summary(setup, *, key="selected"):
    tasks, _service, session, base, promotion = setup
    task, command = accepted(setup, kind="summarize")
    receipt = promotion.publish_summary(session.token, session.csrf, task.id, command)
    with tasks.database.engine.connect() as connection:
        row = (
            connection.execute(
                select(promoted_news_summaries).where(
                    promoted_news_summaries.c.id == UUID(receipt["summary_id"]),
                )
            )
            .mappings()
            .one()
        )
    selectors = [
        {
            field: row["safe_payload"][field]
            for field in (
                "summary_id",
                "summary_revision",
                "article_id",
                "article_revision_id",
                "article_revision",
            )
        }
    ]
    publisher = PublicationService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    selected = publisher.publish(
        PublicationCommand.model_validate_json(base.command_json),
        key=key,
        summary_selections=selectors,
    )
    return task, command, receipt, row, publisher, selected


def test_f1_factory_public_read_discovery_and_selected_summary(setup, monkeypatch):
    tasks, _service, session, base, promotion = setup
    task, command = accepted(setup, key="daily")
    receipt = promotion.publish_daily(session.token, session.csrf, task.id, command)
    _task, _command, summary, row, _publisher, selected = selected_summary(setup)
    facade = PublicPublicationRepository(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    monkeypatch.setenv("DRAGON_REALTIME_CURSOR_SECRET", "synthetic-f1-cursor-only")
    app = create_public_app(repository=facade)
    app.state.public_daily_ai_repository.clock = lambda: NOW
    app.state.public_daily_ai_repository.entitlements.clock = lambda: NOW
    query = f"date={base.trade_date}&snapshot_id={base.id}"
    with TestClient(app) as client:
        assert client.get(f"/api/market/daily-ai?{query}").status_code == 200
        assert client.get(f"/api/market/daily-ai/archives?{query}").status_code == 200
        assert client.get("/api/ai/task/private").status_code == 404
    public = app.state.public_daily_ai_repository
    with tasks.database.engine.begin() as connection:
        snapshot = facade.get_ai_snapshot(connection, selected.id, ai_domains=("news",))
        article = next(result for result in snapshot.results if result.domain == "news")
        value = public.summary(
            connection, summary["summary_id"], 1, article.payload["articles"][0]
        )
    assert value["summary_id"] == str(row["id"])
    assert receipt["archive_version"] == 1


@pytest.mark.parametrize("kind", ["daily_review", "summarize"])
def test_f2_display_restore_denies_old_artifact_allows_explicit_new(setup, kind):
    tasks, _service, session, base, promotion = setup
    task, command = accepted(setup, kind=kind)
    publish = (
        promotion.publish_daily if kind == "daily_review" else promotion.publish_summary
    )
    receipt = publish(session.token, session.csrf, task.id, command)
    artifact_id = UUID(receipt.get("archive_id", receipt.get("summary_id")))
    with tasks.database.engine.begin() as connection:
        source = connection.scalar(
            select(source_dependencies.c.source_id).where(
                source_dependencies.c.archive_id == artifact_id,
            )
        )
        original = promotion.publications.market._source(connection, source)
    retention = RetentionService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    block = retention.withdraw_display(source)
    assert block.cutoff_dependency_sequence is None
    retention.replay_withdrawals(source)
    retention.restore_source(source, original)
    with pytest.raises(Forbidden):
        publish(session.token, session.csrf, task.id, command)
    if kind == "daily_review":
        with pytest.raises(Forbidden):
            promotion.public.get(base.id, base.trade_date, archive_id=artifact_id)
        assert promotion.public.discover(base.id, base.trade_date)[2] == []
    else:
        with tasks.database.engine.begin() as connection:
            old = (
                connection.execute(
                    select(promoted_news_summaries).where(
                        promoted_news_summaries.c.id == artifact_id,
                    )
                )
                .mappings()
                .one()
            )
            with pytest.raises(Forbidden):
                promotion.public.authorize(connection, old)
    new_command = {**command, "idempotency_key": "explicit-republish"}
    if kind == "daily_review":
        new_command["expected_archive_version"] = 1
    new = publish(session.token, session.csrf, task.id, new_command)
    assert new.get("archive_id", new.get("summary_id")) != str(artifact_id)


@pytest.mark.parametrize("action", ["delete", "deidentify"])
def test_f3_repeat_sealed_storage_withdrawal_after_restore(setup, action):
    tasks, _service, _session, _base, promotion = setup
    _task, _command, _receipt, row, _publisher, selected = selected_summary(setup)
    with tasks.database.engine.begin() as connection:
        source = connection.scalar(
            select(source_dependencies.c.source_id).where(
                source_dependencies.c.archive_id == row["id"],
            )
        )
        original = promotion.publications.market._source(connection, source)
    retention = RetentionService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    policy = RetentionPolicy(reference="repeat-policy", approved=True, action=action)
    first = retention.withdraw_storage(source, policy=policy)
    retention.replay_withdrawals(source)
    retention.restore_source(source, original)
    second = retention.withdraw_storage(source, policy=policy)
    assert second.sequence > first.sequence
    with pytest.raises(Forbidden):
        promotion.publications.get_snapshot(selected.id)


def test_f4_consuming_publication_first_source_locks_are_global_union(
    setup, monkeypatch
):
    tasks, _service, session, base, promotion = setup
    market = promotion.publications.market
    origin_grant = grant(source_id="A", ai=True, sharing=True)
    market.register_source(origin_grant)
    source_batch = batch()
    alternate = source_batch.model_copy(
        update={
            "source_id": "A",
            "grant": origin_grant,
            "security_states": tuple(
                s.model_copy(update={"source_id": "A"})
                for s in source_batch.security_states
            ),
            "bars": tuple(
                b.model_copy(update={"source_id": "A"}) for b in source_batch.bars
            ),
        }
    )
    origin_market = market.publish(
        market.import_batch(alternate, key="origin-market").id
    )
    publisher = PublicationService(tasks.database, market, clock=lambda: NOW)
    origin = publisher.publish(
        PublicationCommand.model_validate_json(base.command_json).model_copy(
            update={
                "market_snapshot_id": origin_market.id,
            }
        ),
        key="origin",
    )
    origin_setup = (tasks, _service, session, origin, promotion)
    task, command = accepted(origin_setup, kind="summarize")
    receipt = promotion.publish_summary(session.token, session.csrf, task.id, command)
    with tasks.database.engine.connect() as connection:
        payload = connection.scalar(
            select(promoted_news_summaries.c.safe_payload).where(
                promoted_news_summaries.c.id == UUID(receipt["summary_id"]),
            )
        )
    selectors = [
        {
            field: payload[field]
            for field in (
                "summary_id",
                "summary_revision",
                "article_id",
                "article_revision_id",
                "article_revision",
            )
        }
    ]
    locks = []
    original_source = market._source

    def tracked(connection, source_id):
        locks.append(source_id)
        return original_source(connection, source_id)

    monkeypatch.setattr(market, "_source", tracked)
    publisher.publish(
        PublicationCommand.model_validate_json(base.command_json),
        key="consume-origin",
        summary_selections=selectors,
    )
    assert list(dict.fromkeys(locks)) == ["A", "synthetic"]


def test_f5_real_owner_summary_revoke_exact_body_and_daily_unchanged(setup):
    tasks, _service, session, _base, promotion = setup
    task, command = accepted(setup, kind="summarize")
    summary = promotion.publish_summary(session.token, session.csrf, task.id, command)
    app = FastAPI()
    app.state.ai_publication_service = promotion
    app.add_middleware(
        OwnerGate, origin="https://localhost:8030", owner_repository=tasks.owners
    )
    install_ai_publication_routes(app)
    path = f"/api/ai/task/{task.id}/published-summary/{summary['summary_id']}/revoke"
    body = {
        "expected_summary_revision": 1,
        "idempotency_key": "withdraw",
        "reason_code": "withdrawn_by_owner",
    }
    with TestClient(app, base_url="https://localhost:8030") as client:
        assert (
            client.post(
                path, json=body, headers=_headers(session, csrf=False)
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                json={**body, "expected_archive_version": 1},
                headers=_headers(session),
            ).status_code
            == 400
        )
        response = client.post(path, json=body, headers=_headers(session))
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
        assert (
            client.post(path, json=body, headers=_headers(session)).json()
            == response.json()
        )
        daily_task, daily_command = accepted(setup, key="daily-after-summary")
        daily = promotion.publish_daily(
            session.token,
            session.csrf,
            daily_task.id,
            daily_command,
        )
        daily_path = (
            f"/api/ai/task/{daily_task.id}/published-daily/{daily['archive_id']}/revoke"
        )
        assert (
            client.post(daily_path, json=body, headers=_headers(session)).status_code
            == 400
        )
        daily_body = {
            "expected_archive_version": 1,
            "idempotency_key": "withdraw-daily",
            "reason_code": "withdrawn_by_owner",
        }
        assert (
            client.post(
                daily_path, json=daily_body, headers=_headers(session)
            ).status_code
            == 200
        )
