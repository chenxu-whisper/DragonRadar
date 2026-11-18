"""IG08/IG10 frozen private read contracts. DB execution requires parent approval."""

import re
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from factories import NOW, grant
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from news_factories import (
    ARTICLE_A,
    ASSOCIATION_TOPIC,
    REVISION_A1,
    association,
    late_revision,
)
from sqlalchemy import insert, select, update
from test_reset_ai_tasks import (
    _headers,
    _second_session,
)
from test_reset_ai_tasks import (
    ai_setup as _ai_setup,
)
from test_reset_news import _ingest_base, _news_service, _publish_news

from dragon_radar.ai.models import AISubmission
from dragon_radar.ai.tables import ai_task_drafts, ai_tasks
from dragon_radar.http.routes.ai import install_ai_routes
from dragon_radar.http.routes.news import install_news_review_routes
from dragon_radar.news.repository import NewsRepository
from dragon_radar.news.tables import news_review_audit_events, news_review_commands
from dragon_radar.owner.app import create_owner_app
from dragon_radar.owner.repository import OwnerRepository
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.retention import RetentionService

pytestmark = pytest.mark.anyio
ai_setup = _ai_setup


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    ("install", "path"),
    [
        (
            install_news_review_routes,
            "/api/news/{article_id}/associations/review-state",
        ),
        (install_ai_routes, "/api/ai/task/{task_id}/evidence/{evidence_id}"),
    ],
)
def test_contract_routes_install_private_get_without_database(install, path):
    app = FastAPI()
    install(app)
    assert any(
        route.path == path and "GET" in route.methods
        for route in app.routes
        if hasattr(route, "methods")
    ), f"Missing owner-only GET contract: {path}"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/api/news/{ARTICLE_A}/associations/review-state"),
        ("POST", f"/api/news/{ARTICLE_A}/associations/review"),
        ("GET", f"/api/ai/task/{ARTICLE_A}/evidence/ev-summary"),
    ],
)
def test_contract_owner_gateway_whitelists_exact_private_paths(method, path):
    config = (Path(__file__).parents[1] / "containers/owner.Caddyfile").read_text()
    blocks = re.findall(r"@\w+\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", config)
    patterns = []
    for block in blocks:
        methods = re.search(r"\bmethod ([^\n]+)", block)
        pattern = re.search(r"\bpath_regexp \w+ ([^\n]+)", block)
        if methods and pattern and method in methods[1].split():
            patterns.append(pattern[1].strip())
    assert any(re.fullmatch(pattern, path) for pattern in patterns), (
        f"Owner gateway must proxy {method} {path}"
    )
    assert not any(re.fullmatch(pattern, path + "/extra") for pattern in patterns)


@pytest.fixture
def review_reads(database, repository):
    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(owners.issue_pairing(), "https://localhost:8030")
    news = _news_service(database, repository, owners=owners)
    _ingest_base(news)
    publication = _publish_news(
        database, repository, news.create_snapshot(as_of=NOW, key="ig08-input")
    )
    publications = PublicationRepository(database, repository, clock=lambda: NOW)
    reviews = NewsRepository(
        database, repository, owner_repository=owners, clock=lambda: NOW
    )
    app = create_owner_app(
        repository=repository,
        owner_repository=owners,
        publication_repository=publications,
        news_repository=reviews,
    )
    return app, session, publication, reviews, publications


def review_query(publication):
    return {
        "date": publication.trade_date.isoformat(),
        "snapshot_id": str(publication.id),
    }


def publication_facts(snapshot):
    return deepcopy(
        {
            "publication": snapshot.publication.model_dump(mode="json"),
            "market_snapshot": {
                **vars(snapshot.market_snapshot),
                "batch": snapshot.market_snapshot.batch.model_dump(mode="json"),
            },
            "results": [item.model_dump(mode="json") for item in snapshot.results],
        }
    )


async def read_review(app, session, publication, *, article_id=ARTICLE_A, params=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://localhost:8030"
    ) as client:
        return await client.get(
            f"/api/news/{article_id}/associations/review-state",
            params=review_query(publication) if params is None else params,
            headers=_headers(session, csrf=False),
        )


async def test_review_state_uses_global_revision_but_only_callers_audit(
    review_reads, database
):
    app, session, publication, reviews, publications = review_reads
    before = publication_facts(publications.get_snapshot(publication.id))
    own = reviews.review(
        session.token,
        session.csrf,
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="accept",
        expected_revision=0,
        key="ig08-own",
    )
    other = _second_session(database)
    foreign = reviews.review(
        other.token,
        other.csrf,
        article_id=ARTICLE_A,
        association_id=ASSOCIATION_TOPIC,
        decision="reject",
        expected_revision=1,
        key="ig08-foreign",
    )
    response = await read_review(app, session, publication)
    assert response.status_code == 200
    value = response.json()
    assert set(value) == {
        "owner_id",
        "article_id",
        "article_revision_id",
        "article_revision",
        "snapshot_id",
        "trade_date",
        "associations",
    }
    assert value | {"associations": []} == {
        "owner_id": str(session.owner_id),
        "article_id": str(ARTICLE_A),
        "article_revision_id": str(REVISION_A1),
        "article_revision": 1,
        "snapshot_id": str(publication.id),
        "trade_date": publication.trade_date.isoformat(),
        "associations": [],
    }
    association = next(
        item
        for item in value["associations"]
        if item["association_id"] == str(ASSOCIATION_TOPIC)
    )
    assert association == {
        "association_id": str(ASSOCIATION_TOPIC),
        "revision": 2,
        "status": "rejected",
        "decision": "reject",
        "own_audit": [
            {
                "audit_id": str(own.audit_id),
                "decision": "accept",
                "revision": 1,
                "reviewed_at": own.reviewed_at.isoformat(),
            }
        ],
    }
    assert str(foreign.audit_id) not in response.text
    assert str(other.owner_id) not in response.text
    assert "ig08-own" not in response.text and "ig08-foreign" not in response.text
    assert publication_facts(publications.get_snapshot(publication.id)) == before
    with database.engine.connect() as connection:
        assert len(connection.execute(select(news_review_commands)).all()) == 2
        assert len(connection.execute(select(news_review_audit_events)).all()) == 2


async def test_unreviewed_association_has_revision_zero_not_article_revision(
    review_reads,
):
    app, session, publication, _reviews, _publications = review_reads
    response = await read_review(app, session, publication)
    assert response.status_code == 200
    association = next(
        item
        for item in response.json()["associations"]
        if item["association_id"] == str(ASSOCIATION_TOPIC)
    )
    assert association == {
        "association_id": str(ASSOCIATION_TOPIC),
        "revision": 0,
        "status": "unreviewed",
        "decision": None,
        "own_audit": [],
    }


@pytest.mark.parametrize(
    "change", ["missing_date", "missing_pub", "extra", "duplicate"]
)
async def test_review_state_rejects_ambiguous_selection(review_reads, change):
    app, session, publication, *_ = review_reads
    query = review_query(publication)
    if change == "missing_date":
        query.pop("date")
    elif change == "missing_pub":
        query.pop("snapshot_id")
    elif change == "extra":
        query["owner_id"] = str(uuid4())
    else:
        query = [*query.items(), ("date", query["date"])]
    response = await read_review(app, session, publication, params=query)
    assert response.status_code == 400


async def test_review_state_article_must_exist_in_exact_pub(review_reads):
    app, session, publication, *_ = review_reads
    response = await read_review(app, session, publication, article_id=uuid4())
    assert response.status_code == 404


async def test_review_state_excludes_newer_unpublished_revision_associations(
    review_reads, database, repository
):
    app, session, publication, reviews, _publications = review_reads
    new_revision = late_revision()
    new_id = uuid4()
    _news_service(database, repository).ingest_article(
        new_revision,
        key="ig08-unpublished-revision",
        associations=(
            association(
                id=new_id,
                article_revision_id=new_revision.id,
                target_type="topic",
                target_id="new-unpublished-topic",
                method="ai",
                status="unreviewed",
            ),
        ),
    )
    reviews.review(
        session.token,
        session.csrf,
        article_id=ARTICLE_A,
        association_id=new_id,
        decision="accept",
        expected_revision=0,
        key="ig08-unpublished-review",
    )
    response = await read_review(app, session, publication)
    assert response.status_code == 200
    assert response.json()["article_revision_id"] == str(REVISION_A1)
    assert str(new_id) not in response.text


@pytest.mark.parametrize("withdraw", [False, True])
async def test_review_state_reauthorizes_each_read(
    review_reads, repository, database, withdraw
):
    app, session, publication, *_ = review_reads
    assert (await read_review(app, session, publication)).status_code == 200
    if withdraw:
        RetentionService(database, repository, clock=lambda: NOW).withdraw_display(
            "synthetic"
        )
    else:
        repository.register_source(grant(local_display=False))
    response = await read_review(app, session, publication)
    assert response.status_code == 403
    assert "associations" not in response.json()


def evidence_task(setup, *, manifest_change=None, citation_change=None):
    tasks, service, _owners, session, publication = setup
    submission = AISubmission(
        task_type="summarize",
        snapshot_id=publication.id,
        as_of=publication.published_at,
        version=f"v{publication.version}",
        task_input={"article_id": str(ARTICLE_A)},
    )
    snapshot = service.publications.get_snapshot(publication.id)
    manifest = deepcopy(service._task_manifest(snapshot, submission))
    entry = next(
        item
        for item in manifest["evidence"]
        if item["evidence_position"] == "/input/article/summary"
    )
    if manifest_change:
        manifest_change(manifest, entry)
    citation = deepcopy(entry)
    if citation_change:
        citation_change(citation)
    # Create intentionally malformed legacy inputs at submission, never rewrite a task.
    with tasks.database.engine.begin() as connection:
        record = tasks.submit_for_owner(
            connection, session.owner_id, submission, manifest, key=f"ig10-{uuid4()}"
        )
        connection.execute(
            insert(ai_task_drafts).values(
                id=uuid4(),
                task_id=record.id,
                revision=1,
                source="human_edit",
                content={
                    "facts": [],
                    "inferences": [],
                    "missing_evidence": [],
                    "citations": [citation],
                },
                created_at=NOW,
            )
        )
        connection.execute(
            update(ai_tasks)
            .where(ai_tasks.c.id == record.id)
            .values(
                selected_revision=1,
            )
        )
    app = create_owner_app(ai_service=service)
    return app, session, publication, record, citation, manifest


async def read_evidence(
    data, *, session=None, revision=1, snapshot_id=None, evidence_id=None, params=None
):
    app, original_session, publication, record, citation, _manifest = data
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://localhost:8030"
    ) as client:
        requested_id = evidence_id or citation["evidence_id"]
        return await client.get(
            f"/api/ai/task/{record.id}/evidence/{requested_id}",
            params=params
            if params is not None
            else {
                "revision": revision,
                "snapshot_id": str(snapshot_id or publication.id),
            },
            headers=_headers(session or original_session, csrf=False),
        )


async def test_evidence_returns_exact_manifest_field_and_leaves_manifest_unchanged(
    ai_setup,
):
    data = evidence_task(ai_setup)
    _app, _session, publication, record, citation, manifest = data
    response = await read_evidence(data)
    assert response.status_code == 200
    assert response.json() == {
        "owner_id": str(_session.owner_id),
        "task_id": str(record.id),
        "revision": 1,
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        **citation,
        "status": "ready",
        "reason": None,
        "locator": {
            "kind": "news_article_field",
            "article_id": str(ARTICLE_A),
            "article_revision_id": str(REVISION_A1),
            "article_revision": 1,
            "field": "summary",
            "index": None,
        },
    }
    tasks, _service, _owners, session, _publication = ai_setup
    assert (
        tasks.get(session.token, record.id).model_dump(mode="json")["input_manifest"]
        == manifest
    )
    assert "input_manifest" not in response.json()
    assert "url" not in response.json()


@pytest.mark.parametrize(
    ("case", "status"),
    [("owner", 404), ("revision", 404), ("evidence", 404), ("pub", 409)],
)
async def test_evidence_checks_all_identity_dimensions(
    ai_setup, database, case, status
):
    data = evidence_task(ai_setup)
    values = {}
    if case == "owner":
        values["session"] = _second_session(database)
    elif case == "revision":
        values["revision"] = 2
    elif case == "evidence":
        values["evidence_id"] = "ev-not-cited"
    else:
        values["snapshot_id"] = uuid4()
    response = await read_evidence(data, **values)
    assert response.status_code == status
    assert "value" not in response.json()


async def test_evidence_present_in_manifest_but_not_cited_by_requested_draft_is_hidden(
    ai_setup,
):
    data = evidence_task(ai_setup)
    uncited = next(
        entry
        for entry in data[-1]["evidence"]
        if entry["evidence_position"] == "/input/article/title"
    )
    response = await read_evidence(data, evidence_id=uncited["evidence_id"])
    assert response.status_code == 404
    assert "value" not in response.json()


@pytest.mark.parametrize("change", ["pointer", "value", "source", "late", "citation"])
async def test_evidence_rejects_unverified_manifest_or_citation(ai_setup, change):
    def corrupt(manifest, entry):
        if change == "pointer":
            entry["evidence_position"] = "/input/article/nonexistent"
        elif change == "value":
            entry["value"] = "not the immutable summary"
        elif change == "source":
            entry["source_identity"] = "news:wrong-source:v1:guessed"
        elif change == "late":
            entry["available_at"] = "2099-01-01T00:00:00Z"

    data = evidence_task(
        ai_setup,
        manifest_change=corrupt,
        citation_change=(lambda item: item.update(value="fabricated"))
        if change == "citation"
        else None,
    )
    response = await read_evidence(data)
    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"
    assert "locator" not in response.json() and "value" not in response.json()


async def test_legacy_unresolvable_article_is_explicitly_unavailable_without_guess(
    ai_setup,
):
    def legacy(manifest, _entry):
        manifest["input"]["article"].pop("article_id")
        manifest["input"]["article"].pop("revision_id")

    data = evidence_task(ai_setup, manifest_change=legacy)
    response = await read_evidence(data)
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "unavailable"
    assert value["reason"] == "legacy_locator_unavailable"
    assert value["locator"] is None and value["value"] is None
    assert str(ARTICLE_A) not in response.text


@pytest.mark.parametrize("change", ["display", "withdraw"])
async def test_evidence_rechecks_grants_even_after_task_get_succeeded(
    ai_setup, database, repository, change
):
    data = evidence_task(ai_setup)
    tasks, _service, _owners, session, _publication = ai_setup
    assert tasks.get(session.token, data[3].id).content_draft is not None
    assert (await read_evidence(data)).status_code == 200
    if change == "withdraw":
        RetentionService(database, repository, clock=lambda: NOW).withdraw_display(
            "synthetic"
        )
    else:
        repository.register_source(grant(ai=True, local_display=False))
    response = await read_evidence(data)
    assert response.status_code == 403
    assert "value" not in response.json()


async def test_evidence_display_is_not_denied_by_ai_processing_permission(
    ai_setup, repository
):
    data = evidence_task(ai_setup)
    repository.register_source(grant(ai=False, local_display=True))
    response = await read_evidence(data)
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize("endpoint", ["review", "evidence"])
async def test_owner_reads_require_a_live_session(endpoint, request):
    if endpoint == "review":
        app, session, publication, *_ = request.getfixturevalue("review_reads")
    else:
        data = evidence_task(request.getfixturevalue("ai_setup"))
        app, session, publication, *_ = data
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://localhost:8030"
    ) as client:
        logout = await client.request(
            "DELETE", "/api/owner/v1/session", json={}, headers=_headers(session)
        )
    assert logout.status_code in {200, 204}
    response = (
        await read_review(app, session, publication)
        if endpoint == "review"
        else await read_evidence(data)
    )
    assert response.status_code in {401, 403}
    assert "value" not in response.json() and "associations" not in response.json()


async def test_evidence_embedded_display_expiry_denies_even_with_renewed_current_grant(
    ai_setup, database, repository
):
    tasks, service, owners, session, publication = ai_setup
    # Existing immutable archives expire in 2027; renew only the current grant.
    repository.register_source(grant(expires_at=NOW + timedelta(days=1000), ai=True))
    data = evidence_task(ai_setup)
    later = NOW + timedelta(days=200)
    tasks.clock = owners.clock = service.publications.clock = lambda: later
    # Display policy owns its clock separately from the repository.
    service.publications.access_policy.clock = lambda: later
    from dragon_radar.owner.models import owner_sessions

    with database.engine.begin() as connection:
        connection.execute(
            update(owner_sessions)
            .where(owner_sessions.c.owner_id == session.owner_id)
            .values(expires_at=later + timedelta(hours=1))
        )
    response = await read_evidence(data)
    assert response.status_code == 403
    assert "value" not in response.json()


async def test_rejected_draft_cannot_restore_previously_displayed_evidence(ai_setup):
    data = evidence_task(ai_setup)
    assert (await read_evidence(data)).status_code == 200
    tasks, _service, _owners, session, _publication = ai_setup
    tasks.review(
        session.token,
        session.csrf,
        data[3].id,
        decision="reject",
        revision=1,
        key="ig10-reject-displayed",
    )
    response = await read_evidence(data)
    assert response.status_code == 403
    assert "value" not in response.json() and "locator" not in response.json()


async def test_withdrawn_archive_stays_denied_after_current_grant_is_renewed(
    ai_setup, database, repository
):
    data = evidence_task(ai_setup)
    assert (await read_evidence(data)).status_code == 200
    RetentionService(database, repository, clock=lambda: NOW).withdraw_display(
        "synthetic"
    )
    repository.register_source(grant(ai=True, local_display=True))
    response = await read_evidence(data)
    assert response.status_code == 403
    assert "value" not in response.json()


@pytest.mark.parametrize(
    "change",
    [
        "bad_pub",
        "bad_revision",
        "missing_pub",
        "missing_revision",
        "extra",
        "duplicate",
    ],
)
async def test_evidence_malformed_query_is_sanitized_400(ai_setup, change):
    data = evidence_task(ai_setup)
    params = {"revision": "1", "snapshot_id": str(data[2].id)}
    if change == "bad_pub":
        params["snapshot_id"] = "secret-malformed-uuid"
    elif change == "bad_revision":
        params["revision"] = "not-an-integer"
    elif change == "missing_pub":
        params.pop("snapshot_id")
    elif change == "missing_revision":
        params.pop("revision")
    elif change == "extra":
        params["owner_id"] = "secret-override"
    else:
        params = [*params.items(), ("snapshot_id", str(data[2].id))]
    response = await read_evidence(data, params=params)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert "secret" not in response.text and "value" not in response.json()
