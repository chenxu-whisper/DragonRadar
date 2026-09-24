"""Point-in-time news, deterministic events, publication, and owner review."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch, grant
from httpx import ASGITransport, AsyncClient
from news_factories import (
    ARTICLE_A,
    ARTICLE_B,
    ASSOCIATION_SECURITY,
    ASSOCIATION_TOPIC,
    REVISION_A1,
    REVISION_A2,
    article_revision,
    association,
    event_evidence,
    late_revision,
    second_article,
)
from pydantic import ValidationError
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import inspect, select, text

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _news_service(database, repository, *, owners=None):
    from dragon_radar.news.service import NewsService

    return NewsService(
        database,
        repository,
        owner_repository=owners,
        clock=lambda: NOW,
    )


def _ingest_base(service):
    return service.ingest_article(
        article_revision(),
        key="news-a1",
        event_evidence=event_evidence(),
        associations=(
            association(),
            association(
                id=ASSOCIATION_TOPIC,
                target_type="topic",
                target_id="topic-a",
                method="ai",
                status="unreviewed",
                evidence_snippets=("Synthetic topic phrase.",),
            ),
        ),
    )


def _publish_news(
    database,
    repository,
    news_snapshot,
    *,
    topic_snapshot=None,
    key_suffix="",
    clock=None,
):
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.service import PublicationService

    market = repository.publish(
        repository.import_batch(
            batch(),
            key=f"news-market-{news_snapshot.id}{key_suffix}",
        ).id
    )
    disabled_domains = (
        ("topic", "regulation", "role")
        if topic_snapshot is None
        else ("regulation", "role")
    )
    return PublicationService(
        database,
        repository,
        clock=clock or (lambda: NOW),
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=topic_snapshot.id if topic_snapshot else None,
            news_input_id=news_snapshot.id,
            disabled_domains=disabled_domains,
        ),
        key=f"news-publication-{news_snapshot.id}{key_suffix}",
    )


def _owner_headers(session):
    return {
        "Origin": "https://localhost:8030",
        "X-CSRF-Token": session.csrf,
        "Cookie": (
            f"__Host-dragon-private-session={session.token}; "
            f"__Host-dragon-private-csrf={session.csrf}"
        ),
    }


def _owner_id(database):
    from dragon_radar.owner.repository import OwnerRepository

    return OwnerRepository(database, clock=lambda: NOW).initialize()


def test_ingest_command_hash_covers_revision_event_and_sorted_associations():
    from dragon_radar.news.repository import _ingest_command_hash

    revision = article_revision()
    evidence = event_evidence()
    associations = (
        association(),
        association(
            id=ASSOCIATION_TOPIC,
            target_type="topic",
            target_id="topic-a",
            method="ai",
            status="unreviewed",
        ),
    )

    digest = _ingest_command_hash(revision, evidence, associations)

    assert digest == _ingest_command_hash(
        revision, evidence, tuple(reversed(associations))
    )
    assert digest != _ingest_command_hash(
        revision.model_copy(update={"title": "Changed title"}),
        evidence,
        associations,
    )
    assert digest != _ingest_command_hash(
        revision,
        event_evidence(value="synthetic:event:changed"),
        associations,
    )
    assert digest != _ingest_command_hash(
        revision,
        evidence,
        (
            associations[0].model_copy(
                update={"evidence_snippets": ("Changed association evidence.",)}
            ),
            associations[1],
        ),
    )


def test_publication_news_items_build_one_revision_lookup():
    from dragon_radar.publication.repository import _news_items_by_revision

    class OnePassItems:
        def __init__(self, rows):
            self.rows = rows
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("news items must be indexed in one pass")
            return iter(self.rows)

    rows = OnePassItems(
        [
            {"article_revision_id": REVISION_A1, "position": 0},
            {"article_revision_id": REVISION_A2, "position": 1},
        ]
    )

    indexed = _news_items_by_revision(rows)

    assert rows.iterations == 1
    assert indexed[REVISION_A1]["position"] == 0
    assert indexed[REVISION_A2]["position"] == 1


def test_publication_news_items_group_snapshots_in_one_pass_and_preserve_order():
    from dragon_radar.publication import repository

    class OnePassItems:
        def __init__(self, rows):
            self.rows = rows
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("news items must be grouped in one pass")
            return iter(self.rows)

    first_snapshot = uuid4()
    empty_snapshot = uuid4()
    rows = OnePassItems(
        [
            {"snapshot_id": first_snapshot, "position": 2},
            {"snapshot_id": uuid4(), "position": 0},
            {"snapshot_id": first_snapshot, "position": 1},
        ]
    )
    group_items = getattr(repository, "_news_items_by_snapshot", None)

    assert group_items is not None
    grouped = group_items((first_snapshot, empty_snapshot), rows)

    assert rows.iterations == 1
    assert [item["position"] for item in grouped[first_snapshot]] == [2, 1]
    assert grouped[empty_snapshot] == []


def test_news_models_enforce_availability_content_and_deterministic_event_evidence():
    from dragon_radar.news.models import NewsArticleRevision, NewsEventEvidence

    with pytest.raises(ValidationError):
        article_revision(available_at=NOW - timedelta(hours=3))
    with pytest.raises(ValidationError):
        article_revision(content_scope="none", summary="not licensed", content=None)
    with pytest.raises(ValidationError):
        article_revision(content_scope="summary", content="not licensed")
    for field in ("supporting_evidence", "opposing_evidence"):
        with pytest.raises(ValidationError):
            NewsArticleRevision.model_validate(
                article_revision().model_dump() | {field: (" ",)}
            )
        with pytest.raises(ValidationError):
            NewsArticleRevision.model_validate(
                article_revision().model_dump() | {field: (1,)}
            )
    with pytest.raises(ValidationError):
        NewsArticleRevision.model_validate(
            article_revision().model_dump() | {"is_novel_event": 1}
        )
    with pytest.raises(ValidationError):
        NewsEventEvidence(kind="title_similarity", value="same title")

    assert (
        NewsArticleRevision.model_validate(article_revision().model_dump()).revision
        == 1
    )
    with pytest.raises(ValidationError):
        NewsArticleRevision.model_validate(
            article_revision().model_dump(
                exclude={
                    "is_novel_event",
                    "supporting_evidence",
                    "opposing_evidence",
                }
            )
        )
    assert event_evidence(kind="canonical_url").kind == "canonical_url"
    assert (
        event_evidence(kind="content_digest", value="a" * 64).kind == "content_digest"
    )
    assert event_evidence(kind="manual_confirmation").kind == "manual_confirmation"


def test_news_models_freeze_prd_article_types():
    from dragon_radar.news.models import NewsArticleRevision

    for news_type in ("news", "announcement", "research"):
        assert article_revision(news_type=news_type).news_type == news_type
    for legacy_type in ("company", "industry", "regulation", "macro", "market"):
        with pytest.raises(ValidationError):
            NewsArticleRevision.model_validate(
                article_revision().model_dump() | {"news_type": legacy_type}
            )


def test_news_review_outcome_serializes_persisted_audit_identity():
    from dragon_radar.news.models import NewsReviewOutcome

    audit_id = uuid4()
    outcome = NewsReviewOutcome(
        association_id=ASSOCIATION_TOPIC,
        decision="accept",
        revision=1,
        audit_id=audit_id,
        effective=False,
        pending_topic_publication=True,
        reviewed_at=NOW,
    )

    assert outcome.model_dump(mode="json")["audit_id"] == str(audit_id)


def _materialization_context(*associations, reviews=None):
    revision = article_revision()
    return {
        "root": {"as_of": NOW + timedelta(hours=2)},
        "rows": [
            revision.model_dump()
            | {
                "article_revision_id": revision.id,
                "event_id": uuid4(),
            }
        ],
        "associations": [item.model_dump() for item in associations],
        "reviews": reviews or {},
    }


def test_news_routes_project_frozen_list_and_detail_wire_contracts():
    from dragon_radar.http.routes.news import _detail_article, _list_article
    from dragon_radar.news.service import NewsService

    internal = NewsService.materialize_context(
        _materialization_context(association())
    ).payload["articles"][0]

    listed = _list_article(internal)
    assert set(listed) == {
        "article_id",
        "title",
        "source",
        "source_version",
        "published_at",
        "available_at",
        "article_type",
        "summary",
        "event_id",
        "revision",
    }
    assert listed["source"] == "synthetic"
    assert listed["article_type"] == "announcement"
    assert listed["summary"] == "Synthetic licensed summary."

    detail = _detail_article(internal)
    assert {
        "article_id",
        "title",
        "content",
        "source",
        "url",
        "published_at",
        "received_at",
        "article_type",
        "ai_summary",
        "related_security_ids",
        "is_novel_event",
        "supporting_evidence",
        "opposing_evidence",
    } <= set(detail)
    assert detail["source"] == "synthetic"
    assert detail["ai_summary"] is None
    assert detail["summary"] == "Synthetic licensed summary."
    assert detail["related_security_ids"] == ["A"]
    assert detail["supporting_evidence"] != detail["securities"][0]["evidence_snippets"]


def test_ai_imported_accepted_association_is_not_self_effective():
    from dragon_radar.news.service import NewsService

    direct = association(method="direct", status="accepted")
    manual = association(id=uuid4(), method="manual", status="accepted")
    ai = association(id=uuid4(), method="ai", status="accepted")
    projected = NewsService.materialize_context(
        _materialization_context(direct, manual, ai)
    ).payload["articles"][0]["securities"]

    assert {item["method"]: item["effective"] for item in projected} == {
        "direct": True,
        "manual": True,
        "ai": False,
    }
    assert all(item["status"] == "accepted" for item in projected)
    assert all(item["pending_topic_publication"] is False for item in projected)


def test_reviewed_topic_stays_pending_for_snapshot_not_formed_after_review():
    from topic_factories import topic_batch

    from dragon_radar.news.service import NewsService

    reviewed_at = NOW
    reviewed = association(
        id=ASSOCIATION_TOPIC,
        target_type="topic",
        target_id="topic-a",
        method="ai",
        status="unreviewed",
    )
    context = _materialization_context(
        reviewed,
        reviews={
            ASSOCIATION_TOPIC: {
                "decision": "accept",
                "reviewed_at": reviewed_at,
            }
        },
    )

    projected = NewsService.materialize_context(
        context,
        topic_context=(
            {"id": uuid4(), "published_at": reviewed_at},
            {},
            topic_batch(),
        ),
    ).payload["articles"][0]["topics"][0]

    assert projected["effective"] is False
    assert projected["pending_topic_publication"] is True


def test_reviewed_topic_activates_only_in_new_snapshot_containing_target():
    from topic_factories import topic_batch

    from dragon_radar.news.service import NewsService

    reviewed_at = NOW
    reviewed = association(
        id=ASSOCIATION_TOPIC,
        target_type="topic",
        target_id="topic-a",
        method="ai",
        status="unreviewed",
    )
    context = _materialization_context(
        reviewed,
        reviews={
            ASSOCIATION_TOPIC: {
                "decision": "accept",
                "reviewed_at": reviewed_at,
            }
        },
    )
    new_snapshot = {
        "id": uuid4(),
        "published_at": reviewed_at + timedelta(microseconds=1),
    }

    effective = NewsService.materialize_context(
        context,
        topic_context=(new_snapshot, {}, topic_batch()),
    ).payload["articles"][0]["topics"][0]
    missing_target = NewsService.materialize_context(
        context,
        topic_context=(
            new_snapshot,
            {},
            topic_batch().model_copy(update={"topics": (), "memberships": ()}),
        ),
    ).payload["articles"][0]["topics"][0]

    assert effective["effective"] is True
    assert effective["pending_topic_publication"] is False
    assert missing_target["effective"] is False
    assert missing_target["pending_topic_publication"] is True


def test_snapshot_cutoff_uses_available_at_and_pins_exact_revision(
    database, repository
):
    service = _news_service(database, repository)
    _ingest_base(service)
    service.ingest_article(late_revision(), key="news-a2")

    historical = service.create_snapshot(as_of=NOW, key="news-snapshot-historical")
    current = service.create_snapshot(
        as_of=NOW + timedelta(hours=2),
        key="news-snapshot-current",
    )

    assert [
        (item.article_id, item.article_revision_id, item.revision)
        for item in historical.items
    ] == [(ARTICLE_A, REVISION_A1, 1)]
    assert [
        (item.article_id, item.article_revision_id, item.revision)
        for item in current.items
    ] == [(ARTICLE_A, REVISION_A2, 2)]
    with database.engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT is_novel_event, supporting_evidence, opposing_evidence, "
                "(SELECT count(*) FROM news_article_revisions) "
                "FROM news_article_revisions WHERE id = :revision_id"
            ),
            {"revision_id": REVISION_A1},
        ).one()
    assert stored == (
        True,
        ["Synthetic filing reports a new order."],
        ["Order value remains subject to closing conditions."],
        2,
    )
    round_trip = _ingest_base(service)
    assert round_trip.supporting_evidence == ("Synthetic filing reports a new order.",)
    assert round_trip.opposing_evidence == (
        "Order value remains subject to closing conditions.",
    )


def test_ingest_command_replays_complete_payload_and_rejects_conflicts(
    database, repository
):
    from dragon_radar.news.tables import news_ingest_commands
    from dragon_radar.storage.repository import Conflict

    service = _news_service(database, repository)
    revision = article_revision()
    evidence = event_evidence()
    associations = (association(),)

    first = service.ingest_article(
        revision,
        key="news-command",
        event_evidence=evidence,
        associations=associations,
    )
    replay = service.ingest_article(
        revision,
        key="news-command",
        event_evidence=evidence,
        associations=associations,
    )

    assert replay == first
    with pytest.raises(Conflict):
        service.ingest_article(
            revision,
            key="news-command",
            event_evidence=event_evidence(value="synthetic:event:changed"),
            associations=associations,
        )
    with pytest.raises(Conflict):
        service.ingest_article(
            revision,
            key="news-command-for-same-revision",
            event_evidence=evidence,
            associations=(
                association(
                    evidence_snippets=("A different complete command.",),
                ),
            ),
        )
    with pytest.raises(Conflict):
        service.ingest_article(
            revision,
            key="news-command-alias",
            event_evidence=evidence,
            associations=associations,
        )
    with database.engine.connect() as connection:
        commands = connection.execute(select(news_ingest_commands)).mappings().all()
    assert len(commands) == 1
    assert commands[0]["idempotency_key"] == "news-command"
    assert commands[0]["article_revision_id"] == REVISION_A1


def test_concurrent_articles_with_same_event_share_event(database, repository):
    from dragon_radar.news.tables import news_event_articles

    second_grant = grant(source_id="synthetic-b")
    repository.register_source(second_grant)
    service = _news_service(database, repository)
    rendezvous = Barrier(2)
    shared_event = event_evidence(kind="canonical_url")

    def synchronize_event_insert(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "INSERT INTO news_events" in statement:
            rendezvous.wait(timeout=5)

    sqlalchemy_event.listen(
        database.engine,
        "before_cursor_execute",
        synchronize_event_insert,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(
                    service.ingest_article,
                    revision,
                    key=key,
                    event_evidence=shared_event,
                )
                for revision, key in (
                    (article_revision(), "news-concurrent-a"),
                    (
                        second_article(source_id="synthetic-b", grant=second_grant),
                        "news-concurrent-b",
                    ),
                )
            )
            results = [future.result(timeout=10) for future in futures]
    finally:
        sqlalchemy_event.remove(
            database.engine,
            "before_cursor_execute",
            synchronize_event_insert,
        )

    with database.engine.connect() as connection:
        links = (
            connection.execute(
                select(news_event_articles).where(
                    news_event_articles.c.article_revision_id.in_(
                        [result.id for result in results]
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(links) == 2
    assert len({row["event_id"] for row in links}) == 1


def test_early_published_at_never_bypasses_late_receipt_cutoff(database, repository):
    service = _news_service(database, repository)
    service.ingest_article(
        article_revision(
            published_at=NOW - timedelta(days=7),
            received_at=NOW + timedelta(minutes=1),
            available_at=NOW + timedelta(minutes=1),
        ),
        key="news-late-arrival",
    )

    historical = service.create_snapshot(as_of=NOW, key="news-before-arrival")

    assert historical.items == ()


def test_events_keep_multiple_articles_and_never_merge_by_similar_title(
    database, repository
):
    service = _news_service(database, repository)
    _ingest_base(service)
    service.ingest_article(
        second_article(),
        key="news-b1-same-event",
        event_evidence=event_evidence(),
    )
    service.ingest_article(
        second_article(
            id=uuid4(),
            article_id=uuid4(),
            source_article_id="source-article-c",
            url="https://example.test/news/c",
        ),
        key="news-c1-same-title-only",
    )

    snapshot = service.create_snapshot(as_of=NOW, key="news-event-snapshot")
    events = {item.article_id: item.event_id for item in snapshot.items}

    assert len(snapshot.items) == 3
    assert events[ARTICLE_A] == events[ARTICLE_B]
    assert len(set(events.values())) == 2


def test_unreviewed_and_rejected_associations_do_not_change_topic_membership(
    database, repository
):
    from topic_factories import topic_batch

    from dragon_radar.topics.repository import TopicRepository

    topics = TopicRepository(database, clock=lambda: NOW)
    topic_snapshot = topics.publish(
        topics.import_batch(topic_batch(), key="news-topic-before-review").id
    )
    before = topic_snapshot.batch.memberships
    service = _news_service(database, repository)
    _ingest_base(service)

    rejected = service.review_for_owner(
        owner_id=_owner_id(database),
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="reject",
        expected_revision=0,
        key="reject-ai-topic",
    )
    after = topics.get_snapshot(topic_snapshot.id).batch.memberships

    assert rejected.decision == "reject"
    assert rejected.effective is False
    assert rejected.pending_topic_publication is False
    assert after == before


def test_accept_is_append_only_pending_and_requires_new_topic_snapshot(
    database, repository
):
    service = _news_service(database, repository)
    _ingest_base(service)

    accepted = service.review_for_owner(
        owner_id=_owner_id(database),
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="accept",
        expected_revision=0,
        key="accept-ai-topic",
    )

    assert accepted.revision == 1
    assert accepted.effective is False
    assert accepted.pending_topic_publication is True
    with database.engine.connect() as connection:
        review = connection.execute(
            text(
                "SELECT decision, expected_revision FROM news_association_reviews "
                "WHERE association_id = :association_id"
            ),
            {"association_id": ASSOCIATION_TOPIC},
        ).one()
        audit = connection.execute(
            text(
                "SELECT action, details FROM news_review_audit_events "
                "WHERE object_id = :association_id"
            ),
            {"association_id": str(ASSOCIATION_TOPIC)},
        ).one()
    assert review == ("accept", 0)
    assert audit[0] == "news_association_reviewed"
    assert audit[1]["effective"] is False


def test_review_idempotency_conflict_revision_and_reauthorization(database, repository):
    from dragon_radar.storage.repository import Conflict, Forbidden

    service = _news_service(database, repository)
    _ingest_base(service)
    owner_id = _owner_id(database)
    first = service.review_for_owner(
        owner_id=owner_id,
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="accept",
        expected_revision=0,
        key="review-idempotency",
    )
    assert (
        service.review_for_owner(
            owner_id=owner_id,
            article_id=ARTICLE_A,
            association_id=ASSOCIATION_TOPIC,
            decision="accept",
            expected_revision=0,
            key="review-idempotency",
        )
        == first
    )
    with pytest.raises(Conflict):
        service.review_for_owner(
            owner_id=owner_id,
            article_id=ARTICLE_A,
            association_id=ASSOCIATION_TOPIC,
            decision="reject",
            expected_revision=0,
            key="review-idempotency",
        )
    with pytest.raises(Conflict):
        service.review_for_owner(
            owner_id=owner_id,
            article_id=ARTICLE_A,
            association_id=ASSOCIATION_TOPIC,
            decision="reject",
            expected_revision=0,
            key="stale-review",
        )
    repository.register_source(grant(local_display=False))
    with pytest.raises(Forbidden):
        service.review_for_owner(
            owner_id=owner_id,
            article_id=ARTICLE_A,
            association_id=ASSOCIATION_TOPIC,
            decision="reject",
            expected_revision=1,
            key="review-after-revoke",
        )


def test_publication_materializes_news_without_override_and_records_lineage(
    database, repository
):
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.tables import source_dependencies

    service = _news_service(database, repository)
    _ingest_base(service)
    service.ingest_article(
        second_article(),
        key="news-b1-publication",
        event_evidence=event_evidence(),
    )
    snapshot = service.create_snapshot(as_of=NOW, key="news-publication-input")

    publication = _publish_news(database, repository, snapshot)
    news = next(
        item
        for item in PublicationRepository(database, repository).list_results(
            publication.id
        )
        if item.domain == "news"
    )
    with database.engine.connect() as connection:
        dependencies = (
            connection.execute(
                select(source_dependencies).where(
                    source_dependencies.c.publication_id == publication.id,
                    source_dependencies.c.derived_type == "news",
                )
            )
            .mappings()
            .all()
        )

    assert news.status == "ready"
    assert news.input_id == snapshot.id
    assert news.payload["event_count"] == 1
    assert len(news.payload["articles"]) == 2
    assert news.payload["articles"][0]["is_novel_event"] is True
    assert news.payload["articles"][0]["supporting_evidence"] == [
        "Synthetic filing reports a new order."
    ]
    assert news.payload["articles"][0]["opposing_evidence"] == [
        "Order value remains subject to closing conditions."
    ]
    assert news.payload["articles"][0]["ai_summary"] is None
    assert {row["archive_type"] for row in dependencies} == {"news_article_revision"}
    assert {row["archive_id"] for row in dependencies} == {
        item.article_revision_id for item in snapshot.items
    }


def test_publication_activates_reviewed_topic_only_with_post_review_snapshot(
    database, repository
):
    from topic_factories import topic_batch

    from dragon_radar.news.service import NewsService
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.topics.repository import TopicRepository

    old_topics = TopicRepository(database, clock=lambda: NOW)
    old_snapshot = old_topics.publish(
        old_topics.import_batch(topic_batch(), key="news-topic-old").id
    )
    reviewed_at = NOW + timedelta(minutes=1)
    service = NewsService(
        database,
        repository,
        clock=lambda: reviewed_at,
    )
    _ingest_base(service)
    service.review_for_owner(
        owner_id=_owner_id(database),
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="accept",
        expected_revision=0,
        key="activate-reviewed-topic",
    )
    news_snapshot = service.create_snapshot(
        as_of=reviewed_at,
        key="news-reviewed-topic-snapshot",
    )
    old_publication = _publish_news(
        database,
        repository,
        news_snapshot,
        topic_snapshot=old_snapshot,
        key_suffix="-old-topic",
        clock=lambda: reviewed_at + timedelta(minutes=1),
    )

    new_time = reviewed_at + timedelta(minutes=2)
    new_topics = TopicRepository(database, clock=lambda: new_time)
    new_snapshot = new_topics.publish(
        new_topics.import_batch(
            topic_batch(source_version="s2", membership_version="m2"),
            key="news-topic-new",
        ).id
    )
    new_publication = _publish_news(
        database,
        repository,
        news_snapshot,
        topic_snapshot=new_snapshot,
        key_suffix="-new-topic",
        clock=lambda: new_time,
    )
    publications = PublicationRepository(database, repository, clock=lambda: new_time)

    old_association = (
        publications.get_snapshot(old_publication.id)
        .result("news")
        .payload["articles"][0]["topics"][0]
    )
    new_association = (
        publications.get_snapshot(new_publication.id)
        .result("news")
        .payload["articles"][0]["topics"][0]
    )

    assert old_association["effective"] is False
    assert old_association["pending_topic_publication"] is True
    assert new_association["effective"] is True
    assert new_association["pending_topic_publication"] is False


def test_publication_read_revalidates_news_lineage_and_withdrawal(database, repository):
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.retention import RetentionService
    from dragon_radar.storage.repository import Forbidden

    service = _news_service(database, repository)
    _ingest_base(service)
    snapshot = service.create_snapshot(as_of=NOW, key="news-authorized-input")
    publication = _publish_news(database, repository, snapshot)
    published = PublicationRepository(database, repository, clock=lambda: NOW)
    assert published.get_snapshot(publication.id).result("news").status == "ready"

    RetentionService(database, repository, clock=lambda: NOW).withdraw_display(
        "synthetic"
    )
    with pytest.raises(Forbidden):
        published.get_snapshot(publication.id)


def test_migration_adds_news_tables_fk_guards_and_readiness(database):
    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0008_news"
    assert database.ready()
    inspector = inspect(database.engine)
    expected = {
        "news_articles",
        "news_article_revisions",
        "news_events",
        "news_event_articles",
        "news_associations",
        "news_association_reviews",
        "news_ingest_commands",
        "news_review_commands",
        "news_review_audit_events",
        "news_snapshots",
        "news_snapshot_items",
    }
    assert expected <= set(inspector.get_table_names())
    assert any(
        fk["referred_table"] == "news_snapshots"
        and fk["constrained_columns"] == ["news_input_id"]
        for fk in inspector.get_foreign_keys("publications")
    )
    article_type_checks = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("news_article_revisions")
        if "news_type" in constraint["sqltext"]
    )
    assert "'news'" in article_type_checks
    assert "'announcement'" in article_type_checks
    assert "'research'" in article_type_checks
    assert "'company'" not in article_type_checks
    revision_columns = {
        column["name"] for column in inspector.get_columns("news_article_revisions")
    }
    assert {
        "is_novel_event",
        "supporting_evidence",
        "opposing_evidence",
    } <= revision_columns
    command_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("news_ingest_commands")
    }
    assert ("idempotency_key",) in command_uniques
    assert ("article_revision_id",) in command_uniques
    with database.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0008_news"
        )


@pytest.fixture
def published_news(database, repository):
    service = _news_service(database, repository)
    _ingest_base(service)
    service.ingest_article(
        second_article(
            news_type="research",
            content_scope="none",
            summary=None,
            content=None,
        ),
        key="news-b1-http",
        event_evidence=event_evidence(),
    )
    snapshot = service.create_snapshot(as_of=NOW, key="news-http-input")
    publication = _publish_news(database, repository, snapshot)
    from dragon_radar.publication.repository import PublicationRepository

    return publication, PublicationRepository(database, repository, clock=lambda: NOW)


async def test_public_get_requires_date_filters_pages_and_reads_only_publication_result(
    published_news, monkeypatch
):
    from dragon_radar.http.app import create_workbench_app

    publication, publications = published_news
    app = create_workbench_app(repository=publications)
    monkeypatch.setattr(
        "dragon_radar.news.repository.NewsRepository.get_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("latest news read")
        ),
    )
    common = {"date": str(publication.trade_date), "snapshot_id": str(publication.id)}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        missing_date = await client.get(
            "/api/news", params={"snapshot_id": str(publication.id)}
        )
        listed = await client.get(
            "/api/news",
            params={
                **common,
                "type": "announcement",
                "source": "synthetic",
                "q": "announcement",
                "security_id": "A",
                "page": 1,
                "size": 1,
            },
        )
        legacy_type = await client.get(
            "/api/news",
            params={**common, "type": "company"},
        )
        detail = await client.get(f"/api/news/{ARTICLE_A}", params=common)

    assert missing_date.status_code == 400
    assert legacy_type.status_code == 400
    assert listed.status_code == 200
    list_data = listed.json()["data"]
    assert "items" not in list_data
    assert list_data["page"] == 1
    assert list_data["size"] == 1
    assert list_data["total"] == 1
    assert list_data["sources"] == ["synthetic"]
    assert list_data["event_count"] == 1
    assert list_data["articles"] == [
        {
            "article_id": str(ARTICLE_A),
            "title": "Synthetic announcement",
            "source": "synthetic",
            "source_version": "news-v1",
            "published_at": (NOW - timedelta(hours=4)).isoformat(),
            "available_at": (NOW - timedelta(hours=1)).isoformat(),
            "article_type": "announcement",
            "summary": "Synthetic licensed summary.",
            "event_id": list_data["articles"][0]["event_id"],
            "revision": 1,
        }
    ]
    assert detail.status_code == 200
    assert detail.json()["snapshot_id"] == str(publication.id)
    detail_data = detail.json()["data"]
    assert detail_data["article_id"] == str(ARTICLE_A)
    assert detail_data["source"] == "synthetic"
    assert detail_data["article_type"] == "announcement"
    assert detail_data["content_scope"] == "full"
    assert detail_data["content"] == "Synthetic licensed article body."
    assert detail_data["summary"] == "Synthetic licensed summary."
    assert detail_data["ai_summary"] is None
    assert detail_data["related_security_ids"] == ["A"]
    assert detail_data["is_novel_event"] is True
    assert detail_data["supporting_evidence"] == [
        "Synthetic filing reports a new order."
    ]
    assert detail_data["opposing_evidence"] == [
        "Order value remains subject to closing conditions."
    ]
    assert detail_data["securities"][0]["association_id"] == str(ASSOCIATION_SECURITY)
    assert detail_data["topics"][0]["status"] == "unreviewed"


async def test_public_news_hides_unlicensed_content_and_stable_sort_is_snapshot_bound(
    published_news,
):
    from dragon_radar.http.app import create_workbench_app

    publication, publications = published_news
    app = create_workbench_app(repository=publications)
    common = {"date": str(publication.trade_date), "snapshot_id": str(publication.id)}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        listed = await client.get("/api/news", params=common)
        unlicensed = await client.get(f"/api/news/{ARTICLE_B}", params=common)
        unknown = await client.get(f"/api/news/{uuid4()}", params=common)

    articles = listed.json()["data"]["articles"]
    assert [item["article_id"] for item in articles] == sorted(
        [item["article_id"] for item in articles]
    )
    assert listed.json()["data"]["event_count"] == 1
    assert unlicensed.status_code == 200
    assert unlicensed.json()["data"]["content_scope"] == "none"
    assert unlicensed.json()["data"]["summary"] is None
    assert unlicensed.json()["data"]["ai_summary"] is None
    assert unlicensed.json()["data"]["content"] is None
    assert unknown.status_code == 404


async def test_public_app_has_no_review_route_and_readonly_middleware_returns_403(
    published_news,
):
    from dragon_radar.http.app import create_workbench_app

    publication, publications = published_news
    app = create_workbench_app(repository=publications)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json={},
        )

    assert response.status_code == 403
    assert not any(
        getattr(route, "path", None) == "/api/news/{article_id}/associations/review"
        and "POST" in (getattr(route, "methods", None) or ())
        for route in app.routes
    )


async def test_owner_review_requires_owner_write_csrf_and_maps_workbench_errors(
    database, repository
):
    from dragon_radar.news.repository import NewsRepository
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(
        owners.issue_pairing(),
        "https://localhost:8030",
    )
    service = _news_service(database, repository, owners=owners)
    _ingest_base(service)
    news = NewsRepository(
        database,
        repository,
        owner_repository=owners,
        clock=lambda: NOW,
    )
    app = create_owner_app(
        repository=repository,
        owner_repository=owners,
        publication_repository=PublicationRepository(database, repository),
        news_repository=news,
    )
    body = {
        "association_id": str(ASSOCIATION_TOPIC),
        "decision": "accept",
        "expected_revision": 0,
        "idempotency_key": "owner-review",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://localhost:8030",
    ) as client:
        no_csrf = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json=body,
            headers={
                "Origin": "https://localhost:8030",
                "Cookie": (
                    f"__Host-dragon-private-session={session.token}; "
                    f"__Host-dragon-private-csrf={session.csrf}"
                ),
            },
        )
        accepted = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json=body,
            headers=_owner_headers(session),
        )
        replay = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json=body,
            headers=_owner_headers(session),
        )
        conflict = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json={**body, "decision": "reject"},
            headers=_owner_headers(session),
        )
        malformed = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json={**body, "unexpected": True},
            headers=_owner_headers(session),
        )
        missing = await client.post(
            f"/api/news/{uuid4()}/associations/review",
            json={**body, "idempotency_key": "missing-article"},
            headers=_owner_headers(session),
        )
    with database.engine.connect() as connection:
        audit_id = connection.execute(
            text(
                "SELECT id FROM news_review_audit_events "
                "WHERE object_id = :association_id"
            ),
            {"association_id": str(ASSOCIATION_TOPIC)},
        ).scalar_one()

    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "FORBIDDEN"
    assert accepted.status_code == replay.status_code == 200
    assert (
        accepted.json()
        == replay.json()
        == {
            "association_id": str(ASSOCIATION_TOPIC),
            "decision": "accept",
            "revision": 1,
            "audit_id": str(audit_id),
            "effective": False,
            "pending_topic_publication": True,
        }
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "INVALID_PARAM"
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


async def test_owner_review_storage_error_is_sanitized(
    database, repository, monkeypatch
):
    from sqlalchemy.exc import SQLAlchemyError

    from dragon_radar.news.repository import NewsRepository
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(
        owners.issue_pairing(),
        "https://localhost:8030",
    )
    news = NewsRepository(database, repository, owner_repository=owners)
    monkeypatch.setattr(
        news,
        "review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SQLAlchemyError("secret-storage-detail")
        ),
    )
    app = create_owner_app(
        repository=repository,
        owner_repository=owners,
        publication_repository=PublicationRepository(database, repository),
        news_repository=news,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://localhost:8030",
    ) as client:
        response = await client.post(
            f"/api/news/{ARTICLE_A}/associations/review",
            json={
                "association_id": str(ASSOCIATION_TOPIC),
                "decision": "accept",
                "expected_revision": 0,
                "idempotency_key": "storage-error",
            },
            headers=_owner_headers(session),
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "STORAGE_UNAVAILABLE",
        "message": "Storage unavailable",
    }
    assert "secret-storage-detail" not in response.text


def test_news_tables_reject_mutation_and_snapshot_item_revision_mismatch(
    database, repository
):
    from sqlalchemy import update
    from sqlalchemy.exc import DBAPIError

    from dragon_radar.news.tables import news_article_revisions, news_snapshot_items

    service = _news_service(database, repository)
    _ingest_base(service)
    snapshot = service.create_snapshot(as_of=NOW, key="news-db-guards")
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            update(news_article_revisions)
            .where(news_article_revisions.c.id == REVISION_A1)
            .values(title="mutated")
        )
    with pytest.raises(DBAPIError), database.engine.begin() as connection:
        connection.execute(
            news_snapshot_items.insert().values(
                snapshot_id=snapshot.id,
                article_id=ARTICLE_A,
                article_revision_id=REVISION_A1,
                revision=99,
                event_id=uuid4(),
                position=2,
            )
        )
