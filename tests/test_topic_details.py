"""Publication-bound topic detail contracts, using synthetic inputs only."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch, grant, state
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from topic_factories import (
    coverage_cell,
    topic_batch,
    topic_definition,
    topic_membership,
)

from dragon_radar.http.app import create_workbench_app
from dragon_radar.http.public_app import PUBLIC_READ_PATHS, create_public_app
from dragon_radar.publication.models import (
    Publication,
    PublicationCommand,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.publication.service import PublicationService
from dragon_radar.storage.models import Snapshot
from dragon_radar.storage.repository import Forbidden


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _inputs(*, coverage=True):
    cutoff = NOW - timedelta(hours=2)
    market = batch(
        security_states=(
            state(code="000001"),
            state("B", code="000002", source_version="wrong"),
            state("C", code="000003", available_at=cutoff + timedelta(minutes=1)),
        ),
        expected_security_ids=("A", "B", "C"),
    )
    topics = topic_batch(
        as_of=cutoff,
        source_time=cutoff,
        topics=(
            topic_definition(),
            topic_definition("future", available_at=NOW),
            topic_definition("ended", effective_to=DAYS[-1]),
            topic_definition("next", effective_from=DAYS[-1] + timedelta(days=1)),
        ),
        memberships=(
            topic_membership(),
            topic_membership(security_id="B"),
            topic_membership(security_id="C"),
            topic_membership(security_id="UNKNOWN"),
            topic_membership(security_id="late", available_at=NOW),
            topic_membership(security_id="ended", effective_to=DAYS[-1]),
            topic_membership(
                security_id="next", effective_from=DAYS[-1] + timedelta(days=1)
            ),
        ),
        coverage_cells=(
            coverage_cell(
                expected_member_count=7,
                received_member_count=4,
                status="unavailable",
                reason="partial_page",
            ),
        )
        if coverage
        else (),
    )
    return market, topics


def _materialized(market=None, topics=None):
    if market is None:
        market, topics = _inputs()
    market_id, topic_id = uuid4(), uuid4()
    result = PublicationService._market_materialization(
        ({"id": market_id}, {"quality_report": {"issues": []}}, market),
        ({"id": topic_id}, {}, topics) if topics is not None else None,
    )
    return result.model_dump(mode="json")["payload"], market_id, topic_id, market


def _selected(payload, market_id, topic_id, market):
    coverage = {"expected": 3, "valid": 1, "legal_excluded": 0, "missing": 2}
    publication = Publication(
        id=uuid4(),
        trade_date=market.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market_id,
        topic_snapshot_id=topic_id,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
        domains={
            "market": "ready",
            "topic": "ready",
            "news": "disabled",
            "regulation": "disabled",
            "role": "disabled",
        },
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=NOW,
    )
    snapshot = Snapshot(
        id=market_id,
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest={"rule_version": "synthetic-v1"},
        batch=market,
        quality_report={"issues": [], "coverage": coverage},
    )
    result = PublicationResult(
        publication_id=publication.id,
        domain="market",
        input_id=market_id,
        status="ready",
        payload=payload,
        coverage=coverage,
        quality_flags=(),
    )
    topic_result = PublicationResult(
        publication_id=publication.id,
        domain="topic",
        input_id=topic_id,
        status="ready",
        payload={
            "snapshot_id": str(topic_id),
            "batch_id": str(uuid4()),
            "source_version": "s1",
            "taxonomy_version": "t1",
            "membership_version": "m1",
        },
        coverage={},
        quality_flags=(),
    )
    return PublishedSnapshot(publication, snapshot, (result, topic_result))


class _ArchiveRepository:
    database = type("Database", (), {"ready": lambda self: True})()

    def __init__(self, selected):
        self.selected = selected
        self.denied = False
        self.calls = []

    def get_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        if self.denied:
            raise Forbidden("Source display was revoked")
        return self.selected if self.selected.id == snapshot_id else None

    def list_snapshots(self, **_kwargs):
        return [self.selected]


class _EmptyStream(AsyncByteStream):
    async def __aiter__(self):
        if False:
            yield b""


async def _get(
    app,
    selected,
    sector="topic-a",
    *,
    base_url="http://testserver",
    headers=None,
    **params,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=base_url
    ) as client:
        request = client.build_request(
            "GET",
            f"/api/topics/{sector}",
            params={
                "snapshot_id": str(selected.id),
                "date": str(selected.trade_date),
                **params,
            },
            content=_EmptyStream(),
            headers=headers,
        )
        request.headers.pop("transfer-encoding", None)
        return await client.send(request)


def test_publication_archives_visible_members_independent_of_role_readiness():
    payload, market_id, topic_id, market = _materialized()
    assert "topic_details" in payload
    block = payload["topic_details"]
    assert block["schema"] == "dragon-radar.topic-details"
    assert block["schema_version"] == 1
    assert block["trade_date"] == str(market.trade_date)
    assert list(block["sectors"]) == ["topic-a"]
    detail = block["sectors"]["topic-a"]
    assert detail["sector"] == payload["sector_flow"]["sectors"][0]
    assert detail["topic_basis"] == {
        "source": "synthetic",
        "source_version": "s1",
        "as_of": (NOW - timedelta(hours=2)).isoformat(),
        "source_time": (NOW - timedelta(hours=2)).isoformat(),
        "cutoff": (NOW - timedelta(hours=2)).isoformat(),
        "market_snapshot_id": str(market_id),
        "topic_snapshot_id": str(topic_id),
        "taxonomy_version": "t1",
        "membership_version": "m1",
    }
    assert detail["membership_coverage"] == {
        "expected": 7,
        "received": 4,
        "known": 4,
        "status": "unavailable",
        "reason": "partial_page",
        "complete_as_of": "2026-09-08T17:00:00+00:00",
    }
    assert detail["total"] == 4
    assert [m["security_id"] for m in detail["members"]] == ["A", "B", "C", "UNKNOWN"]
    assert detail["members"][0]["code"] == "000001"
    assert detail["members"][0]["name"] == "Synthetic A"
    assert detail["members"][0]["evidence_refs"] == ["synthetic:membership-a"]
    assert all(m["code"] is None and m["name"] is None for m in detail["members"][1:])


def test_missing_coverage_is_not_inferred_from_known_members():
    market, topics = _inputs(coverage=False)
    payload, *_ = _materialized(market, topics)
    assert "topic_details" in payload
    coverage = payload["topic_details"]["sectors"]["topic-a"]["membership_coverage"]
    assert coverage == {
        "expected": None,
        "received": None,
        "known": 4,
        "status": "unavailable",
        "reason": "topic_coverage_unavailable",
        "complete_as_of": None,
    }


def test_identity_must_exist_in_matching_archived_market_identity_list():
    market, topics = _inputs()
    market = market.model_copy(
        update={
            "security_states": (
                *market.security_states,
                state("UNKNOWN", code="099999"),
            ),
        }
    )
    payload, *_ = _materialized(market, topics)
    member = payload["topic_details"]["sectors"]["topic-a"]["members"][-1]
    assert member["code"] is None
    assert member["name"] is None


def test_market_cutoff_and_effective_start_boundaries_are_inclusive():
    market, topics = _inputs()
    cutoff = NOW - timedelta(hours=3)
    market = market.model_copy(update={"as_of": cutoff})
    topics = topics.model_copy(
        update={
            "topics": (topics.topics[0].model_copy(update={"available_at": cutoff}),),
            "memberships": (
                topics.memberships[0].model_copy(
                    update={
                        "available_at": cutoff,
                        "effective_from": market.trade_date,
                    }
                ),
                topics.memberships[1].model_copy(
                    update={
                        "available_at": cutoff + timedelta(microseconds=1),
                    }
                ),
            ),
            "coverage_cells": (),
        }
    )
    payload, *_ = _materialized(market, topics)
    detail = payload["topic_details"]["sectors"]["topic-a"]
    assert [member["security_id"] for member in detail["members"]] == ["A"]
    assert detail["topic_basis"]["cutoff"] == cutoff.isoformat()


@pytest.mark.anyio
async def test_detail_paginates_frozen_members_and_keeps_unified_envelope(monkeypatch):
    selected = _selected(*_materialized())
    repository = _ArchiveRepository(selected)
    app = create_workbench_app(repository=repository)

    def no_rebuild(*_args, **_kwargs):
        raise AssertionError("GET must not rebuild topic facts")

    monkeypatch.setattr(
        "dragon_radar.publication.service.materialize_market", no_rebuild
    )
    monkeypatch.setattr("dragon_radar.readmodels.market.materialize_market", no_rebuild)
    response = await _get(app, selected, page=2, size=2)
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == str(selected.id)
    assert body["data"]["topic_basis"]["market_snapshot_id"] != body["snapshot_id"]
    assert body["data"]["topic_basis"]["topic_snapshot_id"] != body["snapshot_id"]
    assert set(body["data"]) == {
        "sector",
        "topic_basis",
        "definition",
        "membership_coverage",
        "members",
        "total",
        "page",
        "size",
    }
    assert [m["security_id"] for m in body["data"]["members"]] == ["C", "UNKNOWN"]
    assert (body["data"]["total"], body["data"]["page"], body["data"]["size"]) == (
        4,
        2,
        2,
    )
    empty = await _get(app, selected, page=3, size=2)
    assert empty.status_code == 200
    assert empty.json()["data"]["members"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("legacy", ["no_block", "no_topic", "legacy_overview"])
async def test_legacy_and_no_topic_archives_are_honestly_unavailable(legacy):
    args = list(_materialized())
    if legacy == "no_topic":
        args = list(_materialized(batch(), None))
    else:
        args[0].pop("topic_details", None)
    if legacy == "legacy_overview":
        args[0] = {
            "advancing": 0,
            "declining": 0,
            "unchanged": 1,
            "returns": [],
            "exclusions": [],
        }
    selected = _selected(*args)
    response = await _get(
        create_workbench_app(repository=_ArchiveRepository(selected)), selected
    )
    assert response.status_code == 200
    assert response.json()["data"] is None
    assert "materialization_unavailable" in response.json()["quality_flags"]


@pytest.mark.anyio
async def test_unknown_sector_and_date_and_revocation_use_existing_selection():
    selected = _selected(*_materialized())
    repository = _ArchiveRepository(selected)
    app = create_workbench_app(repository=repository)
    assert (await _get(app, selected, sector="absent")).status_code == 404
    assert (await _get(app, selected, date="2026-09-07")).status_code == 400
    assert (await _get(app, selected, snapshot_id=str(uuid4()))).json()["data"] is None
    repository.denied = True
    assert (await _get(app, selected)).status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "version",
        "date",
        "market_id",
        "topic_id",
        "sector_id",
        "total",
        "known",
        "received",
        "duplicate",
        "future_member",
        "expired_member",
        "future_definition",
        "future_coverage",
        "missing_field",
        "bad_identity",
        "sector_aggregate",
        "extra_field",
        "boolean_count",
        "identity_name",
        "identity_code",
        "identity_unknown",
        "false_complete",
        "null_block",
        "boolean_version",
        "unsafe_evidence",
        "unknown_sector_key",
        "taxonomy_version",
        "membership_version",
    ],
)
async def test_corrupt_archived_details_fail_closed(change):
    args = list(_materialized())
    payload = deepcopy(args[0])
    assert "topic_details" in payload
    block = payload["topic_details"]
    detail = block["sectors"]["topic-a"]
    if change == "schema":
        block["schema"] = "wrong"
    elif change == "version":
        block["schema_version"] = 2
    elif change == "date":
        block["trade_date"] = "2026-09-07"
    elif change in {"market_id", "topic_id"}:
        detail["topic_basis"][change.replace("_id", "_snapshot_id")] = str(uuid4())
    elif change == "sector_id":
        detail["sector"]["sector_id"] = "other"
    elif change == "total":
        detail["total"] = 8
    elif change in {"known", "received"}:
        detail["membership_coverage"][change] = 8
    elif change == "duplicate":
        detail["members"][1] = deepcopy(detail["members"][0])
    elif change == "future_member":
        detail["members"][0]["available_at"] = NOW.isoformat()
    elif change == "expired_member":
        detail["members"][0]["effective_to"] = str(DAYS[-1])
    elif change == "future_definition":
        detail["definition"]["available_at"] = NOW.isoformat()
    elif change == "future_coverage":
        detail["membership_coverage"]["complete_as_of"] = NOW.isoformat()
    elif change == "missing_field":
        del detail["definition"]
    elif change == "bad_identity":
        detail["members"][0]["code"] = 1
    elif change == "sector_aggregate":
        detail["sector"]["name"] = "Altered"
    elif change == "extra_field":
        detail["members"][0]["untrusted"] = True
    elif change == "boolean_count":
        detail["total"] = True
    elif change == "identity_name":
        detail["members"][0]["name"] = "Tampered identity"
    elif change == "identity_code":
        detail["members"][0]["code"] = "999999"
    elif change == "identity_unknown":
        detail["members"][-1]["name"] = "Invented identity"
    elif change == "false_complete":
        detail["membership_coverage"]["status"] = "complete"
        detail["membership_coverage"]["reason"] = None
    elif change == "null_block":
        payload["topic_details"] = None
    elif change == "boolean_version":
        block["schema_version"] = True
    elif change == "unsafe_evidence":
        detail["members"][0]["evidence_refs"] = ["javascript:alert(1)"]
    elif change == "unknown_sector_key":
        block["sectors"]["other"] = block["sectors"].pop("topic-a")
    elif change in {"taxonomy_version", "membership_version"}:
        detail["topic_basis"][change] = "tampered-version"
    args[0] = payload
    selected = _selected(*args)
    response = await _get(
        create_workbench_app(repository=_ArchiveRepository(selected)), selected
    )
    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"size": 0},
        {"size": 101},
        {"page": 0},
        {"page": "1.5"},
        {"x": 1},
    ],
)
async def test_bad_detail_parameters_fail_before_archive_access(params):
    selected = _selected(*_materialized())
    repository = _ArchiveRepository(selected)
    response = await _get(
        create_workbench_app(repository=repository), selected, **params
    )
    assert response.status_code == 400
    assert repository.calls == []


@pytest.mark.anyio
async def test_public_allowlist_only_adds_detail_get_and_does_not_shadow_rotation():
    selected = _selected(*_materialized())
    repository = _ArchiveRepository(selected)
    assert "/api/topics/{sector_id}" in PUBLIC_READ_PATHS
    app = create_public_app(repository=repository)
    response = await _get(app, selected)
    assert response.status_code == 200
    repository.denied = True
    assert (await _get(app, selected)).status_code == 403
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert (await client.post("/api/topics/topic-a")).status_code == 403
    assert (await _get(app, selected, sector="rotation")).status_code == 404
    assert (await _get(app, selected, sector="topic-a/history")).status_code == 404
    workbench = create_workbench_app(repository=repository)
    async with AsyncClient(
        transport=ASGITransport(app=workbench), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/topics/rotation")).status_code == 400


@pytest.mark.anyio
async def test_database_publication_freezes_detail_and_rechecks_public_grant(
    database,
    repository,
):
    from dragon_radar.http.access import public_publication_repository
    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.topics.repository import TopicRepository

    sharing_grant = grant(sharing=True)
    repository.register_source(sharing_grant)
    market = repository.publish(
        repository.import_batch(batch(grant=sharing_grant), key="detail-market").id
    )
    topics = TopicRepository(database, clock=lambda: NOW)
    topic = topics.publish(
        topics.import_batch(topic_batch(grant=sharing_grant), key="detail-topic").id
    )
    record = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=topic.id,
            disabled_domains=("news", "regulation", "role"),
        ),
        key="detail-publication",
    )
    archived = PublicationRepository(database, repository, clock=lambda: NOW)
    selected = archived.get_published_snapshot(record.id)
    response = await _get(create_workbench_app(repository=archived), selected)
    assert response.status_code == 200
    assert response.json()["data"]["membership_coverage"]["status"] == "complete"
    assert response.json()["data"]["topic_basis"]["topic_snapshot_id"] == str(topic.id)
    public = public_publication_repository(database, repository, clock=lambda: NOW)
    app = create_public_app(repository=public)
    assert (await _get(app, selected)).status_code == 200
    repository.register_source(grant(sharing=False))
    assert (await _get(app, selected)).status_code == 403

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    origin = "https://localhost:8030"
    session = owners.exchange_pairing(owners.issue_pairing(), origin)
    owner_app = create_owner_app(
        repository=repository,
        owner_repository=owners,
        publication_repository=archived,
    )
    assert any(
        getattr(route, "path", None) == "/api/topics/{sector_id}"
        and getattr(route, "methods", None) == {"GET"}
        for route in owner_app.routes
    )
    assert (await _get(owner_app, selected, base_url=origin)).status_code == 401
    owner_response = await _get(
        owner_app,
        selected,
        base_url=origin,
        headers={"Cookie": f"__Host-dragon-private-session={session.token}"},
    )
    assert owner_response.status_code == 200
    assert owner_response.json()["snapshot_id"] == str(record.id)
