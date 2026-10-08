"""Additive PostgreSQL contracts for the Task19 public sharing boundary."""

import pytest
from factories import DAYS, NOW, batch, grant
from httpx import ASGITransport, AsyncClient
from news_factories import article_revision, event_evidence, second_article
from topic_factories import topic_batch

from dragon_radar.http.access import public_publication_repository
from dragon_radar.http.public_app import create_public_app
from dragon_radar.news.service import NewsService
from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.service import PublicationService
from dragon_radar.roles.materialize import RoleMaterializer
from dragon_radar.topics.repository import TopicRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _shared_grant(source_id: str = "synthetic", **changes):
    return grant(
        **{
            "source_id": source_id,
            "reference": f"{source_id}-contract-fixture",
            "sharing": True,
            **changes,
        }
    )


def _register_shared(repository, *source_ids: str):
    for source_id in source_ids:
        repository.register_source(_shared_grant(source_id))


def _market(repository, *, key: str, embedded=None):
    imported = repository.import_batch(
        batch(grant=embedded or _shared_grant()),
        key=f"{key}-market",
    )
    return repository.publish(imported.id)


def _topic(database, repository, *, key: str, embedded=None):
    source_id = "synthetic-topic"
    _register_shared(repository, source_id)
    topics = TopicRepository(database, clock=lambda: NOW)
    imported = topics.import_batch(
        topic_batch(
            source_id=source_id,
            grant=embedded or _shared_grant(source_id),
        ),
        key=f"{key}-topic",
    )
    return topics.publish(imported.id)


def _publish(database, repository, *, key: str, **inputs):
    disabled = tuple(
        domain
        for domain, field in (
            ("topic", "topic_snapshot_id"),
            ("news", "news_input_id"),
            ("regulation", "regulation_input_id"),
            ("role", "role_input_id"),
        )
        if field not in inputs
    )
    return PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            disabled_domains=disabled,
            **inputs,
        ),
        key=key,
    )


async def _public_get(database, repository, path: str):
    public = public_publication_repository(
        database,
        repository,
        clock=lambda: NOW,
    )
    app = create_public_app(repository=public)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://public.example",
    ) as client:
        response = await client.get(path)
    return response, public


@pytest.mark.parametrize("denied_grant", ["current", "embedded"])
async def test_topic_requires_current_and_embedded_sharing_through_public_http(
    database,
    repository,
    denied_grant,
):
    key = f"task19-topic-{denied_grant}"
    market = _market(repository, key=key)
    topic = _topic(
        database,
        repository,
        key=key,
        embedded=(
            _shared_grant("synthetic-topic", sharing=False)
            if denied_grant == "embedded"
            else None
        ),
    )
    _register_shared(repository, "synthetic")
    if denied_grant == "current":
        repository.register_source(_shared_grant("synthetic-topic", sharing=False))
    publication = _publish(
        database,
        repository,
        key=f"{key}-publication",
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
    )

    response, public = await _public_get(
        database,
        repository,
        f"/api/snapshots/{publication.id}",
    )

    assert response.status_code == 403
    assert public.list_snapshots() == []


async def test_topic_with_current_and_embedded_sharing_is_public(database, repository):
    market = _market(repository, key="task19-topic-shared")
    topic = _topic(database, repository, key="task19-topic-shared")
    _register_shared(repository, "synthetic")
    publication = _publish(
        database,
        repository,
        key="task19-topic-shared-publication",
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
    )

    response, public = await _public_get(
        database,
        repository,
        f"/api/snapshots/{publication.id}",
    )

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == str(publication.id)
    assert [item.id for item in public.list_snapshots()] == [publication.id]


def _news_snapshot(
    database,
    repository,
    *,
    key: str,
    second_embedded_sharing: bool,
):
    source_ids = ("synthetic-news-a", "synthetic-news-b")
    _register_shared(repository, *source_ids)
    news = NewsService(database, repository, clock=lambda: NOW)
    first = article_revision(
        source_id=source_ids[0],
        grant=_shared_grant(source_ids[0]),
    )
    second = second_article(
        source_id=source_ids[1],
        grant=_shared_grant(
            source_ids[1],
            sharing=second_embedded_sharing,
        ),
    )
    news.ingest_article(
        first,
        key=f"{key}-first",
        event_evidence=event_evidence(),
    )
    news.ingest_article(
        second,
        key=f"{key}-second",
        event_evidence=event_evidence(),
    )
    return news.create_snapshot(as_of=NOW, key=f"{key}-snapshot")


@pytest.mark.parametrize(
    "denial", ["first-current", "second-current", "second-embedded"]
)
async def test_every_news_revision_requires_current_and_embedded_sharing(
    database,
    repository,
    denial,
):
    key = f"task19-news-{denial}"
    market = _market(repository, key=key)
    news = _news_snapshot(
        database,
        repository,
        key=key,
        second_embedded_sharing=denial != "second-embedded",
    )
    _register_shared(repository, "synthetic")
    if denial.endswith("current"):
        source_id = (
            "synthetic-news-a" if denial.startswith("first") else "synthetic-news-b"
        )
        repository.register_source(_shared_grant(source_id, sharing=False))
    publication = _publish(
        database,
        repository,
        key=f"{key}-publication",
        market_snapshot_id=market.id,
        news_input_id=news.id,
    )

    response, public = await _public_get(
        database,
        repository,
        f"/api/news?date={DAYS[-1]}&snapshot_id={publication.id}",
    )

    assert response.status_code == 403
    assert public.list_snapshots() == []


async def test_all_news_revisions_shared_are_public(database, repository):
    market = _market(repository, key="task19-news-shared")
    news = _news_snapshot(
        database,
        repository,
        key="task19-news-shared",
        second_embedded_sharing=True,
    )
    _register_shared(repository, "synthetic")
    publication = _publish(
        database,
        repository,
        key="task19-news-shared-publication",
        market_snapshot_id=market.id,
        news_input_id=news.id,
    )

    response, public = await _public_get(
        database,
        repository,
        f"/api/news?date={DAYS[-1]}&snapshot_id={publication.id}",
    )

    assert response.status_code == 200
    assert len(response.json()["data"]["articles"]) == 2
    assert [item.id for item in public.list_snapshots()] == [publication.id]


@pytest.mark.parametrize("denied_lineage", ["market", "topic"])
async def test_role_rechecks_market_and_topic_embedded_lineage(
    database,
    repository,
    denied_lineage,
):
    market = _market(
        repository,
        key=f"task19-role-{denied_lineage}",
        embedded=_shared_grant(sharing=denied_lineage != "market"),
    )
    topic = _topic(
        database,
        repository,
        key=f"task19-role-{denied_lineage}",
        embedded=_shared_grant(
            "synthetic-topic",
            sharing=denied_lineage != "topic",
        ),
    )
    _register_shared(repository, "synthetic", "synthetic-topic")
    role = RoleMaterializer(database, repository, clock=lambda: NOW).materialize(
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        rule_version="role-test-v1",
        benchmark_id="CSI300",
        key=f"task19-role-{denied_lineage}-run",
    )
    publication = _publish(
        database,
        repository,
        key=f"task19-role-{denied_lineage}-publication",
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        role_input_id=role.id,
    )

    response, public = await _public_get(
        database,
        repository,
        (
            f"/api/role/candidates?date={DAYS[-1]}"
            f"&snapshot_id={publication.id}&role=trend_leader"
        ),
    )

    assert response.status_code == 403
    assert public.list_snapshots() == []


async def test_role_with_shared_market_and_topic_lineage_is_public(
    database,
    repository,
):
    market = _market(repository, key="task19-role-shared")
    topic = _topic(database, repository, key="task19-role-shared")
    _register_shared(repository, "synthetic", "synthetic-topic")
    role = RoleMaterializer(database, repository, clock=lambda: NOW).materialize(
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        rule_version="role-test-v1",
        benchmark_id="CSI300",
        key="task19-role-shared-run",
    )
    publication = _publish(
        database,
        repository,
        key="task19-role-shared-publication",
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        role_input_id=role.id,
    )

    response, public = await _public_get(
        database,
        repository,
        (
            f"/api/role/candidates?date={DAYS[-1]}"
            f"&snapshot_id={publication.id}&role=trend_leader"
        ),
    )

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == str(publication.id)
    assert [item.id for item in public.list_snapshots()] == [publication.id]


async def test_mixed_domain_publication_is_all_or_nothing(database, repository):
    market = _market(repository, key="task19-mixed")
    topic = _topic(database, repository, key="task19-mixed")
    news = _news_snapshot(
        database,
        repository,
        key="task19-mixed",
        second_embedded_sharing=True,
    )
    _register_shared(
        repository,
        "synthetic",
        "synthetic-topic",
        "synthetic-news-a",
    )
    repository.register_source(_shared_grant("synthetic-news-b", sharing=False))
    publication = _publish(
        database,
        repository,
        key="task19-mixed-publication",
        market_snapshot_id=market.id,
        topic_snapshot_id=topic.id,
        news_input_id=news.id,
    )

    summary, public = await _public_get(
        database,
        repository,
        f"/api/snapshots/{publication.id}",
    )
    market_response, _ = await _public_get(
        database,
        repository,
        f"/api/market/overview?date={DAYS[-1]}&snapshot_id={publication.id}",
    )

    assert summary.status_code == 403
    assert market_response.status_code == 403
    assert public.list_snapshots() == []


async def test_regulation_is_fail_closed_through_public_facade(database, repository):
    from test_reset_regulation_api import _observation, _rule

    from dragon_radar.regulation.models import RegulationEvent
    from dragon_radar.regulation.repository import RegulationRepository

    _register_shared(repository, "synthetic", "synthetic-event-feed")
    market = _market(repository, key="task19-regulation")
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    regulations.register_rule(
        _rule("synthetic-regulation"),
        key="task19-regulation-rule",
    )
    regulations.append_event(
        RegulationEvent(
            event_id="task19-formal-event",
            rule_id="synthetic-regulation",
            rule_version="synthetic-v1",
            security_id="A",
            event_type="monitoring",
            announcement_id="task19-notice",
            announcement_url="https://exchange.example.test/notices/task19",
            effective_from=DAYS[-1],
            effective_to=None,
            source_id="synthetic-event-feed",
            source_version="fixture-v1",
        ),
        key="task19-regulation-event",
    )
    regulation = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(_observation("synthetic-regulation", "0.68"),),
        event_source_completeness="complete",
        event_source_id="synthetic-event-feed",
        event_source_version="fixture-v1",
        key="task19-regulation-snapshot",
    )
    publication = _publish(
        database,
        repository,
        key="task19-regulation-publication",
        market_snapshot_id=market.id,
        regulation_input_id=regulation.id,
    )

    response, public = await _public_get(
        database,
        repository,
        f"/api/regulation/status?date={DAYS[-1]}&snapshot_id={publication.id}",
    )

    assert response.status_code == 403
    assert public.list_snapshots() == []
