"""Real selected-summary publication, replay, projection and withdrawal."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from factories import NOW
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from test_b3_daily_ai_integration import accepted
from test_b3_daily_ai_integration import setup as setup

from dragon_radar.http.routes.news import _authorized_summary, _payload
from dragon_radar.news.summary_publication import PromotedSummaryMaterializer
from dragon_radar.public_daily_ai.retention import TOMBSTONE
from dragon_radar.public_daily_ai.tables import promoted_news_summaries
from dragon_radar.publication.models import PublicationCommand, RetentionPolicy
from dragon_radar.publication.retention import RetentionService
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import source_dependencies
from dragon_radar.storage.repository import Conflict, Forbidden


@pytest.mark.parametrize("action", ["delete", "deidentify"])
def test_selected_summary_publication_replay_read_and_sealed_withdrawal(setup, action):
    tasks, _service, session, base, promotion = setup
    task, command = accepted(setup, kind="summarize")
    receipt = promotion.publish_summary(session.token, session.csrf, task.id, command)
    with tasks.database.engine.connect() as connection:
        archived = connection.scalar(
            select(promoted_news_summaries.c.safe_payload).where(
                promoted_news_summaries.c.id == UUID(receipt["summary_id"]),
            )
        )
    selectors = [
        {
            key: archived[key]
            for key in (
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
    inputs = PublicationCommand.model_validate_json(base.command_json)
    selected = publisher.publish(
        inputs,
        key="selected-summary",
        summary_selections=selectors,
    )
    assert (
        publisher.publish(
            inputs,
            key="selected-summary",
            summary_selections=selectors,
        ).id
        == selected.id
    )
    with pytest.raises(Conflict):
        publisher.publish(inputs, key="selected-summary")
    snapshot = promotion.publications.get_snapshot(selected.id)
    payload, _coverage, _reason = _payload(snapshot)
    article = payload["articles"][0]
    assert payload["schema_version"] == 3
    assert article["summary_artifact"]["summary_id"] == receipt["summary_id"]
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                public_daily_ai_repository=promotion.public,
            )
        )
    )
    preview, full = _authorized_summary(request, article)
    assert preview["status"] == "ready"
    assert full == "Synthetic missing evidence"
    old = promotion.publications.get_snapshot(base.id)
    assert _payload(old)[0]["schema_version"] == 2
    with tasks.database.engine.connect() as connection:
        dependencies = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.archive_id == UUID(receipt["summary_id"]),
                )
            )
            .mappings()
            .all()
        )
    assert {row["publication_id"] for row in dependencies} == {base.id, selected.id}
    assert {row["derived_type"] for row in dependencies} == {"news_summary", "news"}
    source = dependencies[0]["source_id"]
    retention = RetentionService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    retention.withdraw_storage(
        source,
        policy=RetentionPolicy(
            reference="synthetic-retention-policy",
            approved=True,
            action=action,
        ),
    )
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
    assert row["safe_payload"] == TOMBSTONE
    assert row["content_hash"] == receipt["content_hash"]
    with pytest.raises(Forbidden):
        promotion.publications.get_snapshot(selected.id)


def test_database_rejects_summary_text_not_equal_to_promoted_registry(
    setup,
    monkeypatch,
):
    tasks, _service, session, base, promotion = setup
    task, command = accepted(setup, kind="summarize")
    receipt = promotion.publish_summary(session.token, session.csrf, task.id, command)
    with tasks.database.engine.connect() as connection:
        archived = connection.scalar(
            select(promoted_news_summaries.c.safe_payload).where(
                promoted_news_summaries.c.id == UUID(receipt["summary_id"]),
            )
        )
    selectors = [
        {
            key: archived[key]
            for key in (
                "summary_id",
                "summary_revision",
                "article_id",
                "article_revision_id",
                "article_revision",
            )
        }
    ]
    original = PromotedSummaryMaterializer.materialize_context

    def corrupt(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        payload = result.model_dump(mode="json")["payload"]
        payload["articles"][0]["summary_artifact"]["text"] = "Forged summary"
        return result.model_copy(update={"payload": payload})

    monkeypatch.setattr(PromotedSummaryMaterializer, "materialize_context", corrupt)
    publisher = PublicationService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    with pytest.raises(SQLAlchemyError, match="invalid publication summary lineage"):
        publisher.publish(
            PublicationCommand.model_validate_json(base.command_json),
            key="forged-summary",
            summary_selections=selectors,
        )
    assert len(promotion.publications.list_snapshots()) == 1
