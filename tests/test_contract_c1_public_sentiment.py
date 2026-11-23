"""C1 regression for the public publication-bound research read route."""

import pytest
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from test_reset_sentiment_research import _in_memory_selected, _market_batch

from dragon_radar.http.public_app import create_public_app
from dragon_radar.readmodels.market import materialize_market


class EmptyRepository:
    database = None

    def list_snapshots(self, **_kwargs):
        return []

    def get_snapshot(self, _snapshot_id):
        return None


class PublishedRepository(EmptyRepository):
    def __init__(self, selected):
        self.selected = selected

    def get_snapshot(self, snapshot_id):
        return self.selected if snapshot_id == self.selected.id else None


class EmptyRequestStream(AsyncByteStream):
    async def __aiter__(self):
        if False:
            yield b""


@pytest.mark.anyio
async def test_public_sentiment_research_reaches_existing_read_handler():
    app = create_public_app(repository=EmptyRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://public.example"
    ) as client:
        request = client.build_request(
            "GET",
            "/api/market/sentiment/research?date=2026-09-08&days=20",
            content=EmptyRequestStream(),
        )
        request.headers.pop("transfer-encoding", None)
        response = await client.send(request)
    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["trade_date"] == "2026-09-08"


@pytest.mark.anyio
@pytest.mark.parametrize("archive_state", ["ready", "legacy", "corrupt"])
async def test_public_research_is_publication_bound_and_unknown_fails_closed(
    archive_state, monkeypatch
):
    from uuid import uuid4

    payload = materialize_market(_market_batch(), None)
    if archive_state == "legacy":
        del payload["sentiment_research"]
    elif archive_state == "corrupt":
        payload["sentiment_research"]["method_version"] = "invalid-contract-fixture"
    selected = _in_memory_selected(payload)
    archived = repr(selected.result("market").payload)

    def forbid_recompute(*_args, **_kwargs):
        raise AssertionError("Public reads must not recompute archived research")

    monkeypatch.setattr(
        "dragon_radar.readmodels.market.materialize_market", forbid_recompute
    )
    app = create_public_app(repository=PublishedRepository(selected))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://public.example"
    ) as client:

        async def read(snapshot_id):
            request = client.build_request(
                "GET",
                f"/api/market/sentiment/research?snapshot_id={snapshot_id}"
                "&date=2026-09-08&days=20",
                content=EmptyRequestStream(),
            )
            request.headers.pop("transfer-encoding", None)
            return await client.send(request)

        response = await read(selected.id)
        unknown = await read(uuid4())
    assert response.status_code == (503 if archive_state == "corrupt" else 200)
    body = response.json()
    if archive_state == "corrupt":
        assert body["code"] == "DATA_UNAVAILABLE"
    else:
        assert body["snapshot_id"] == str(selected.id)
        assert body["trade_date"] == "2026-09-08"
    if archive_state == "legacy":
        assert body["data"] is None
        assert "materialization_unavailable" in body["quality_flags"]
    elif archive_state == "ready":
        assert body["data"]["trade_date"] == "2026-09-08"
        assert len(body["data"]["series"]) == 20
    assert repr(selected.result("market").payload) == archived
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "NOT_FOUND"


@pytest.mark.anyio
@pytest.mark.parametrize("surface", ["public", "owner"])
async def test_wordcloud_literal_route_keeps_publication_identity(surface):
    from contextlib import nullcontext
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from test_reset_wordcloud import _valid_archived_block

    from dragon_radar.news.service import (
        NEWS_RULE_VERSION,
        NEWS_SCHEMA,
        NEWS_SCHEMA_VERSION,
    )
    from dragon_radar.publication.models import PublicationResult, PublishedSnapshot

    selected = _in_memory_selected(materialize_market(_market_batch(), None))
    block = _valid_archived_block()
    news = PublicationResult(
        publication_id=selected.id,
        domain="news",
        input_id=uuid4(),
        status="ready",
        payload={
            "schema": NEWS_SCHEMA,
            "schema_version": NEWS_SCHEMA_VERSION,
            "rule_version": NEWS_RULE_VERSION,
            "articles": [],
            "sources": ["synthetic-contract-c1"],
            "event_count": 0,
            "wordcloud": block,
        },
        coverage=selected.quality_report["coverage"],
        quality_flags=(),
    )
    selected = PublishedSnapshot(
        selected.publication.model_copy(
            update={"domains": {**selected.publication.domains, "news": "ready"}}
        ),
        selected.market_snapshot,
        (*selected.results, news),
    )
    before = repr(news.payload)
    repository = PublishedRepository(selected)
    if surface == "owner":
        from dragon_radar.owner.app import create_owner_app
        from dragon_radar.owner.errors import SessionUnauthorized
        from dragon_radar.owner.models import OwnerIdentity

        origin = "https://localhost:8443"
        identity = OwnerIdentity(
            uuid4(), uuid4(), datetime.now(UTC) + timedelta(hours=1), origin
        )

        def authorize(_connection, token, csrf=None):
            if token != "synthetic-c1-test-token" or csrf is not None:
                raise SessionUnauthorized()
            return identity

        owners = SimpleNamespace(
            database=SimpleNamespace(
                engine=SimpleNamespace(begin=lambda: nullcontext(None))
            ),
            authorize_connection=authorize,
        )
        app = create_owner_app(
            repository=repository,
            publication_repository=repository,
            owner_repository=owners,
            origin=origin,
        )
        headers = {"Cookie": "__Host-dragon-private-session=synthetic-c1-test-token"}
    else:
        origin = "http://public.example"
        app = create_public_app(repository=repository)
        headers = {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=origin, headers=headers
    ) as client:

        async def read(snapshot_id):
            request = client.build_request(
                "GET",
                f"/api/news/wordcloud?snapshot_id={snapshot_id}"
                "&date=2026-09-08&top_n=40&type=all",
                content=EmptyRequestStream(),
            )
            request.headers.pop("transfer-encoding", None)
            return await client.send(request)

        response = await read(selected.id)
        unknown = await read(uuid4())
        if surface == "owner":
            client.headers.pop("Cookie")
            unauthenticated = await read(selected.id)
            client.headers["Cookie"] = "__Host-dragon-private-session=wrong-token"
            unauthorized = await read(selected.id)
            for denied in (unauthenticated, unauthorized):
                assert denied.status_code == 401
                assert denied.json() == {
                    "code": "session_unauthorized",
                    "message": "Owner session required",
                }
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == str(selected.id)
    assert body["trade_date"] == "2026-09-08"
    assert body["data"]["words"] == block["groups"]["all"]["words"]
    assert repr(news.payload) == before
    assert unknown.status_code == 404
    assert unknown.json() == {
        "code": "snapshot_not_found" if surface == "owner" else "NOT_FOUND",
        "message": "Snapshot not found" if surface == "owner" else "Resource not found",
    }
