"""Owner-scoped, bounded asynchronous AI task contracts."""

import json
import os
import secrets
import subprocess
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import NOW, batch, grant
from news_factories import ARTICLE_A, article_revision, event_evidence
from pydantic import ValidationError
from sqlalchemy import insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


class SlowMutatingProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def __init__(self, started_marker: str, late_marker: str):
        self.started_marker = started_marker
        self.late_marker = late_marker

    def generate(self, task_type, manifest, limits):
        Path(self.started_marker).write_text("provider started", encoding="utf-8")
        time.sleep(5)
        Path(self.late_marker).write_text("late provider mutation", encoding="utf-8")
        return {"content": {"text": "too late"}, "token_usage": 1, "cost": "0"}


class LimitsEchoProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def generate(self, task_type, manifest, limits):
        return {
            "content": {
                "deadline": limits.deadline.isoformat(),
                "timeout_seconds": str(limits.timeout_seconds),
                "max_tokens": limits.max_tokens,
                "max_cost": str(limits.max_cost),
            },
            "token_usage": 1,
            "cost": "0",
        }


class ShortSlowProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def generate(self, task_type, manifest, limits):
        time.sleep(0.3)
        return {"content": {"text": "too late"}, "token_usage": 1, "cost": "0"}


class UnsafeProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def generate(self, task_type, manifest, limits):
        raise RuntimeError("Bearer secret-token raw provider stack")


class InvalidResultProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def __init__(self, result):
        self.result = result

    def generate(self, task_type, manifest, limits):
        return self.result


class InvalidCostProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def __init__(self, cost):
        self.cost = cost

    def generate(self, task_type, manifest, limits):
        return {"content": {"text": "draft"}, "token_usage": 1, "cost": self.cost}


class ValidationExceptionProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def generate(self, task_type, manifest, limits):
        from dragon_radar.ai.models import AIProviderResult

        AIProviderResult.model_validate({})


class MetadataProvider:
    def __init__(
        self,
        *,
        name="Azure OpenAI",
        model_version="openai/gpt 4.1",
        prompt_version="daily review/v2",
    ):
        self.name = name
        self.model_version = model_version
        self.prompt_version = prompt_version

    def generate(self, task_type, manifest, limits):
        return {"content": {"text": "draft"}, "token_usage": 1, "cost": "0"}


class UnavailableCallProvider:
    name = "fake"
    model_version = "fake-v1"
    prompt_version = "prompt-v1"

    def generate(self, task_type, manifest, limits):
        from dragon_radar.ai.provider import ProviderUnavailable

        raise ProviderUnavailable


class HandshakeProcess:
    pid = None

    def __init__(self, events=None, *, start_delay=0):
        self.events = events
        self.start_delay = start_delay
        self.daemon = False
        self.alive = False
        self.started = False
        self.stopped = False
        self.closed = False
        self.join_timeouts = []

    def start(self):
        time.sleep(self.start_delay)
        self.pid = 123
        self.alive = True
        self.started = True
        if self.events is not None:
            self.events.append("start")

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False
        self.stopped = True

    def kill(self):
        self.alive = False
        self.stopped = True

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        self.alive = False

    def close(self):
        self.closed = True


class ClosableEndpoint:
    def close(self):
        pass


def _handshake_context(parent, process):
    return SimpleNamespace(
        Pipe=lambda duplex: (parent, ClosableEndpoint()),
        Process=lambda **kwargs: process,
    )


def _session(owners):
    code = owners.issue_pairing()
    return owners.exchange_pairing(code, "https://localhost:8030")


def _headers(session, *, csrf=True):
    headers = {
        "Origin": "https://localhost:8030",
        "Cookie": (
            f"__Host-dragon-private-session={session.token}; "
            f"__Host-dragon-private-csrf={session.csrf}"
        ),
    }
    if csrf:
        headers["X-CSRF-Token"] = session.csrf
    return headers


def _second_session(database):
    from dragon_radar.owner.models import owner_principals, owner_sessions
    from dragon_radar.owner.repository import _hash

    owner_id = uuid4()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(
                id=owner_id,
                local_key=None,
                created_at=NOW,
            )
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=owner_id,
                token_hash=_hash(token),
                csrf_hash=_hash(csrf),
                origin="https://localhost:8030",
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
    return SimpleNamespace(owner_id=owner_id, token=token, csrf=csrf)


@pytest.fixture
def ai_setup(database, repository):
    from dragon_radar.ai.repository import AITaskRepository
    from dragon_radar.ai.service import AIService
    from dragon_radar.news.service import NewsService
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = _session(owners)
    repository.register_source(grant(ai=True))
    market = repository.publish(
        repository.import_batch(
            batch(grant=grant(ai=True)),
            key="ai-market",
        ).id
    )
    news = NewsService(database, repository, clock=lambda: NOW)
    news.ingest_article(
        article_revision(grant=grant(ai=True)),
        key="ai-news",
        event_evidence=event_evidence(),
    )
    news_snapshot = news.create_snapshot(as_of=NOW, key="ai-news-snapshot")
    publication = PublicationService(
        database,
        repository,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            news_input_id=news_snapshot.id,
            disabled_domains=("topic", "regulation", "role"),
        ),
        key="ai-publication",
    )
    publications = PublicationRepository(
        database,
        repository,
        clock=lambda: NOW,
    )
    tasks = AITaskRepository(database, owners, clock=lambda: NOW)
    service = AIService(tasks, publications)
    return tasks, service, owners, session, publication


def _submit(service, session, publication, **changes):
    values = {
        "task_type": "daily_review",
        "snapshot_id": publication.id,
        "as_of": publication.published_at,
        "version": f"v{publication.version}",
        "task_input": {
            "date": publication.trade_date.isoformat(),
            "notes_context": "Synthetic owner notes.",
        },
        "key": "ai-submit",
    }
    values.update(changes)
    return service.submit(session.token, session.csrf, **values)


def test_0010_migration_creates_ai_schema_and_updates_readiness(database):
    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0010_ai_tasks"
    assert database.ready()
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "ai_tasks",
        "ai_task_drafts",
        "ai_task_cancellations",
        "ai_task_reviews",
    } <= tables
    inspector = inspect(database.engine)
    reflected_columns = inspector.get_columns("ai_tasks")
    columns = {column["name"] for column in reflected_columns}
    assert {
        "owner_id",
        "input_manifest",
        "task_type",
        "status",
        "verification_status",
        "human_review_status",
        "model_version",
        "prompt_version",
        "retrieval_version",
        "provider",
        "token_usage",
        "cost",
        "latency_ms",
        "lease_token",
        "lease_expires_at",
    } <= columns
    status_column = next(
        column for column in reflected_columns if column["name"] == "status"
    )
    assert "pending" in str(status_column["default"])
    claim_index = next(
        index
        for index in inspector.get_indexes("ai_tasks")
        if index["name"] == "ai_tasks_claim"
    )
    assert "pending" in str(claim_index["dialect_options"]["postgresql_where"])
    assert "queued" not in str(claim_index["dialect_options"]["postgresql_where"])
    snapshot_index = next(
        index
        for index in inspector.get_indexes("ai_tasks")
        if index["name"] == "ai_tasks_snapshot_id"
    )
    assert snapshot_index["column_names"] == ["snapshot_id"]
    retrieval_column = next(
        column for column in reflected_columns if column["name"] == "retrieval_version"
    )
    assert retrieval_column["nullable"] is False
    assert "r1" in str(retrieval_column["default"])
    review_columns = {
        column["name"] for column in inspector.get_columns("ai_task_reviews")
    }
    assert {"human_review_status", "verification_status"} <= review_columns


def test_0010_ai_task_status_check_accepts_pending_and_rejects_queued(ai_setup):
    from dragon_radar.ai.tables import ai_tasks

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="status-check")

    assert task.status == "pending"
    with pytest.raises(IntegrityError):
        with tasks.database.engine.begin() as connection:
            connection.execute(
                update(ai_tasks).where(ai_tasks.c.id == task.id).values(status="queued")
            )

    assert tasks.get(session.token, task.id).status == "pending"


def test_ai_models_are_strict_bounded_and_json_finite():
    from dragon_radar.ai.models import AIProviderResult, AISubmission

    command = AISubmission(
        task_type="daily_review",
        snapshot_id=uuid4(),
        as_of=NOW,
        version="v1",
        task_input={"date": NOW.date().isoformat(), "notes_context": "notes"},
    )
    assert command.request_hash == command.request_hash
    assert command.task_type == "daily_review"

    with pytest.raises(ValidationError):
        AISubmission(
            task_type="daily_review",
            snapshot_id=uuid4(),
            as_of=NOW,
            version="v1",
            task_input={"date": NOW.date().isoformat(), "notes_context": "x" * 20001},
        )
    with pytest.raises(ValidationError):
        AIProviderResult(content={"score": float("nan")}, token_usage=1, cost="0")
    with pytest.raises(ValidationError):
        AIProviderResult(content={"text": "x" * 32769}, token_usage=1, cost="0")
    with pytest.raises(ValidationError):
        AIProviderResult(content={}, token_usage=-1, cost="-0.01")


def test_submit_replays_same_payload_and_conflicts_on_key_reuse(ai_setup):
    from dragon_radar.storage.repository import Conflict

    _tasks, service, _owners, session, publication = ai_setup
    first = _submit(service, session, publication)
    replay = _submit(service, session, publication)

    assert replay == first
    assert first.status == "pending"
    assert replay.status == "pending"
    with pytest.raises(Conflict):
        _submit(
            service,
            session,
            publication,
            task_input={
                "date": publication.trade_date.isoformat(),
                "notes_context": "different",
            },
        )


def test_submit_replays_same_payload_after_ai_permission_withdrawal(
    ai_setup, repository
):
    _tasks, service, _owners, session, publication = ai_setup
    first = _submit(service, session, publication)

    repository.register_source(grant(ai=False))
    replay = _submit(service, session, publication)

    assert replay == first


def test_submit_limits_active_tasks_after_allowing_replay(ai_setup):
    from dragon_radar.ai.repository import (
        MAX_ACTIVE_AI_TASKS_PER_OWNER,
        AITaskRateLimited,
    )

    tasks, service, _owners, session, publication = ai_setup
    first = _submit(service, session, publication, key="capacity-0")
    for index in range(1, MAX_ACTIVE_AI_TASKS_PER_OWNER):
        _submit(service, session, publication, key=f"capacity-{index}")

    assert _submit(service, session, publication, key="capacity-0") == first
    with pytest.raises(AITaskRateLimited):
        _submit(service, session, publication, key="capacity-overflow")

    tasks.cancel(session.token, session.csrf, first.id, key="capacity-cancel")
    accepted = _submit(service, session, publication, key="capacity-after-cancel")
    assert accepted.status == "pending"


def test_concurrent_distinct_keys_serialize_owner_capacity_check(ai_setup):
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    from dragon_radar.ai.repository import (
        MAX_ACTIVE_AI_TASKS_PER_OWNER,
        AITaskRateLimited,
    )

    tasks, service, _owners, session, publication = ai_setup
    for index in range(MAX_ACTIVE_AI_TASKS_PER_OWNER - 1):
        _submit(service, session, publication, key=f"concurrent-capacity-{index}")

    def submit(key):
        try:
            return _submit(service, session, publication, key=key).status
        except AITaskRateLimited:
            return "rate_limited"

    with ThreadPoolExecutor(max_workers=2) as pool:
        with tasks.database.engine.begin() as connection:
            tasks._lock(connection, f"ai-owner-capacity:{session.owner_id}")
            futures = [
                pool.submit(submit, "concurrent-capacity-a"),
                pool.submit(submit, "concurrent-capacity-b"),
            ]
            for future in futures:
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.2)
        outcomes = sorted(future.result(timeout=3) for future in futures)

    assert outcomes == ["pending", "rate_limited"]


@pytest.mark.anyio
async def test_ai_route_maps_active_task_limit_to_rate_limited(ai_setup):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.ai.repository import MAX_ACTIVE_AI_TASKS_PER_OWNER
    from dragon_radar.owner.app import create_owner_app

    _tasks, service, owners, session, publication = ai_setup
    for index in range(MAX_ACTIVE_AI_TASKS_PER_OWNER):
        _submit(service, session, publication, key=f"route-capacity-{index}")
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    body = {
        "date": publication.trade_date.isoformat(),
        "notes_context": "new task beyond capacity",
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": "route-capacity-overflow",
    }

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        response = await client.post(
            "/api/ai/daily-review",
            json=body,
            headers=_headers(session),
        )

    assert response.status_code == 429
    assert response.json() == {
        "code": "RATE_LIMITED",
        "message": "Too many requests",
    }


@pytest.mark.anyio
async def test_submit_changed_payload_same_key_is_409_after_ai_permission_withdrawal(
    ai_setup, repository
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    _tasks, service, owners, session, publication = ai_setup
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    body = {
        "date": publication.trade_date.isoformat(),
        "notes_context": "original",
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": "withdrawn-ai-key",
    }
    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        created = await client.post(
            "/api/ai/daily-review",
            json=body,
            headers=_headers(session),
        )
        repository.register_source(grant(ai=False))
        conflict = await client.post(
            "/api/ai/daily-review",
            json={**body, "notes_context": "changed"},
            headers=_headers(session),
        )

    assert created.status_code == 200
    assert conflict.status_code == 409


def test_manifest_is_owner_bound_and_uses_published_snapshot_data(ai_setup):
    tasks, service, _owners, session, publication = ai_setup

    summary = _submit(
        service,
        session,
        publication,
        task_type="summarize",
        task_input={"article_id": str(ARTICLE_A)},
        key="summary",
    )
    stock = _submit(
        service,
        session,
        publication,
        task_type="explain_stock",
        task_input={
            "security_id": "A",
            "date": publication.trade_date.isoformat(),
        },
        key="stock",
    )

    assert summary.owner_id == session.owner_id
    assert summary.input_manifest["publication"] == {
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "trade_date": publication.trade_date.isoformat(),
    }
    assert summary.input_manifest["input"]["article"]["article_id"] == str(ARTICLE_A)
    assert "Synthetic announcement" in summary.model_dump_json()
    assert stock.input_manifest["input"]["stock"]["security_id"] == "A"
    assert "local_profile_id" not in summary.model_dump_json()
    assert "local_profile_id" not in stock.model_dump_json()
    with tasks.database.engine.connect() as connection:
        stored = connection.scalar(
            select(text("input_manifest")).select_from(text("ai_tasks")).limit(1)
        )
    assert stored


def test_ai_permission_is_required_at_submit_and_worker_read(ai_setup, repository):
    from dragon_radar.storage.repository import Forbidden

    tasks, service, _owners, session, publication = ai_setup
    pending = _submit(service, session, publication, key="before-ai-revocation")
    lease = tasks.claim("permission-worker")

    repository.register_source(grant(ai=False))

    assert service.publications.get_publication(publication.id) is not None
    with pytest.raises(Forbidden):
        _submit(service, session, publication, key="after-ai-revocation")
    with pytest.raises(Forbidden):
        tasks.load_input(lease)
    assert tasks.get(session.token, pending.id).status == "running"


def test_ai_uses_public_transaction_aware_publication_snapshot_boundary(ai_setup):
    tasks, service, _owners, session, publication = ai_setup

    with tasks.database.engine.begin() as connection:
        snapshot = service.publications.get_ai_snapshot(
            connection,
            publication.id,
            ai_domains={"market"},
        )

    assert snapshot.publication.id == publication.id
    for relative in Path("src/dragon_radar/ai").glob("*.py"):
        source = relative.read_text(encoding="utf-8")
        assert "publication.tables" not in source
        assert "._authorized_records" not in source


@pytest.mark.parametrize(
    ("task_type", "task_input"),
    (
        ("summarize", {"article_id": str(ARTICLE_A)}),
        ("topic_suggest", {"date": NOW.date().isoformat()}),
        ("explain_stock", {"security_id": "A", "date": NOW.date().isoformat()}),
        (
            "daily_review",
            {"date": NOW.date().isoformat(), "notes_context": "notes"},
        ),
    ),
)
def test_four_task_types_are_accepted(ai_setup, task_type, task_input):
    _tasks, service, _owners, session, publication = ai_setup
    task_input = {
        **task_input,
        **(
            {"date": publication.trade_date.isoformat()} if "date" in task_input else {}
        ),
    }

    task = _submit(
        service,
        session,
        publication,
        task_type=task_type,
        task_input=task_input,
        key=f"type-{task_type}",
    )

    assert task.task_type == task_type
    assert task.status == "pending"
    assert task.verification_status == "pending"
    assert task.human_review_status == "unreviewed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "task_input", "missing_domain"),
    (
        ("/api/ai/summarize", {"article_id": str(ARTICLE_A)}, "news"),
        ("/api/ai/topic-suggest", {"date": NOW.date().isoformat()}, "news"),
        (
            "/api/ai/explain-stock",
            {"security_id": "A", "date": NOW.date().isoformat()},
            "market",
        ),
        (
            "/api/ai/daily-review",
            {"date": NOW.date().isoformat(), "notes_context": "notes"},
            "market",
        ),
    ),
)
async def test_ai_submission_route_returns_404_when_required_domain_is_absent(
    ai_setup,
    monkeypatch,
    path,
    task_input,
    missing_domain,
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app
    from dragon_radar.publication.models import PublishedSnapshot

    _tasks, service, owners, session, publication = ai_setup
    original_get = service.publications.get_ai_snapshot

    def without_required_domain(connection, snapshot_id, *, ai_domains):
        snapshot = original_get(connection, snapshot_id, ai_domains=ai_domains)
        return PublishedSnapshot(
            snapshot.publication,
            snapshot.market_snapshot,
            tuple(
                result for result in snapshot.results if result.domain != missing_domain
            ),
        )

    monkeypatch.setattr(
        service.publications,
        "get_ai_snapshot",
        without_required_domain,
    )
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    body = {
        **task_input,
        **(
            {"date": publication.trade_date.isoformat()} if "date" in task_input else {}
        ),
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": f"missing-{missing_domain}-{path.rsplit('/', 1)[-1]}",
    }

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        response = await client.post(path, json=body, headers=_headers(session))

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Resource not found"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "task_input"),
    (
        ("/api/ai/summarize", {"article_id": str(uuid4())}),
        (
            "/api/ai/explain-stock",
            {"security_id": "MISSING", "date": NOW.date().isoformat()},
        ),
    ),
)
async def test_ai_submission_route_returns_404_when_requested_entry_is_absent(
    ai_setup,
    path,
    task_input,
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    _tasks, service, owners, session, publication = ai_setup
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    body = {
        **task_input,
        **(
            {"date": publication.trade_date.isoformat()} if "date" in task_input else {}
        ),
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": f"missing-entry-{path.rsplit('/', 1)[-1]}",
    }

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        response = await client.post(path, json=body, headers=_headers(session))

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "Resource not found"}


def test_submission_rejects_mismatched_publication_coordinates(ai_setup):
    from dragon_radar.storage.repository import Conflict

    _tasks, service, _owners, session, publication = ai_setup
    with pytest.raises(Conflict):
        _submit(
            service,
            session,
            publication,
            version="v999",
            key="wrong-version",
        )
    with pytest.raises(Conflict):
        _submit(
            service,
            session,
            publication,
            as_of=publication.published_at + timedelta(seconds=1),
            key="wrong-as-of",
        )


def test_owner_scope_hides_get_cancel_and_review(ai_setup):
    from dragon_radar.storage.repository import NotFound

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    other = _second_session(tasks.database)

    with pytest.raises(NotFound):
        tasks.get(other.token, task.id)
    with pytest.raises(NotFound):
        tasks.cancel(other.token, other.csrf, task.id, key="other-cancel")
    with pytest.raises(NotFound):
        tasks.review(
            other.token,
            other.csrf,
            task.id,
            decision="reject",
            revision=1,
            key="other-review",
        )


def test_cancel_is_idempotent_and_terminal_against_late_success(ai_setup):
    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.storage.repository import Conflict

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    lease = tasks.claim("worker-1", lease_for=timedelta(seconds=30))
    first = tasks.cancel(session.token, session.csrf, task.id, key="cancel")
    replay = tasks.cancel(session.token, session.csrf, task.id, key="cancel")

    assert replay == first
    assert first.status == "cancelled"
    with pytest.raises(Conflict):
        tasks.succeed(
            lease,
            AIProviderResult(content={"text": "late"}, token_usage=1, cost="0"),
            provider="fake",
            model_version="fake-v1",
            prompt_version="prompt-v1",
            latency_ms=1,
            max_tokens=10,
            max_cost=Decimal("1"),
        )
    with tasks.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ai_task_drafts")) == 0


def test_cancel_key_reuse_with_another_task_conflicts(ai_setup):
    from dragon_radar.storage.repository import Conflict

    tasks, service, _owners, session, publication = ai_setup
    first = _submit(service, session, publication, key="first")
    second = _submit(service, session, publication, key="second")
    tasks.cancel(session.token, session.csrf, first.id, key="shared-cancel")

    with pytest.raises(Conflict):
        tasks.cancel(session.token, session.csrf, second.id, key="shared-cancel")


def test_expired_lease_recovers_and_stale_lease_cannot_mutate(ai_setup):
    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.storage.repository import Conflict

    tasks, service, _owners, session, publication = ai_setup
    _submit(service, session, publication)
    first = tasks.claim("worker-1", lease_for=timedelta(seconds=10))
    tasks.clock = lambda: NOW + timedelta(seconds=11)
    second = tasks.claim("worker-2", lease_for=timedelta(seconds=10))

    assert second.job.id == first.job.id
    assert second.job.attempt_count == 2
    for operation in (
        lambda: tasks.renew(first, lease_for=timedelta(seconds=10)),
        lambda: tasks.succeed(
            first,
            AIProviderResult(content={"text": "stale"}, token_usage=1, cost="0"),
            provider="fake",
            model_version="fake-v1",
            prompt_version="prompt-v1",
            latency_ms=1,
            max_tokens=10,
            max_cost=Decimal("1"),
        ),
        lambda: tasks.fail(first, code="provider_failed"),
    ):
        with pytest.raises(Conflict):
            operation()


def test_claim_is_skip_locked_and_restart_resumes(ai_setup):
    from concurrent.futures import ThreadPoolExecutor

    tasks, service, _owners, session, publication = ai_setup
    first = _submit(service, session, publication, key="locked")
    second = _submit(service, session, publication, key="available")
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with tasks.database.engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM ai_tasks WHERE id = :id FOR UPDATE"),
                {"id": first.id},
            )
            claimed = pool.submit(tasks.claim, "worker").result(timeout=2)
    finally:
        pool.shutdown(wait=True)
    assert claimed.job.id == second.id

    tasks.cancel(session.token, session.csrf, first.id, key="cancel-locked")
    tasks.clock = lambda: NOW + timedelta(seconds=31)
    recovered = tasks.claim("restarted", lease_for=timedelta(seconds=30))
    assert recovered.job.id == second.id


def test_worker_supplies_immutable_provider_limits(ai_setup):
    from dataclasses import FrozenInstanceError
    from datetime import UTC, datetime

    from dragon_radar.ai.provider import AIProviderLimits
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    _submit(service, session, publication)
    started = datetime.now(UTC)

    completed = AIWorker(
        tasks,
        LimitsEchoProvider(),
        worker_id="limits-worker",
        provider_timeout=2,
        max_tokens=123,
        max_cost=Decimal("4.50"),
    ).run_once()

    assert completed.status == "succeeded"
    limits = AIProviderLimits(
        deadline=datetime.fromisoformat(completed.content_draft.content["deadline"]),
        timeout_seconds=float(completed.content_draft.content["timeout_seconds"]),
        max_tokens=completed.content_draft.content["max_tokens"],
        max_cost=Decimal(completed.content_draft.content["max_cost"]),
    )
    assert limits.timeout_seconds == 2
    assert limits.max_tokens == 123
    assert limits.max_cost == Decimal("4.50")
    assert limits.deadline >= started + timedelta(seconds=1.9)
    with pytest.raises(FrozenInstanceError):
        limits.max_tokens = 1


@pytest.mark.parametrize("limit", ("token", "cost", "latency"))
def test_repository_classifies_only_provider_result_limit_violations(
    ai_setup,
    limit,
):
    from dragon_radar.ai.models import MAX_PROVIDER_LATENCY_MS, AIProviderResult
    from dragon_radar.ai.repository import AIProviderResultLimitExceeded

    tasks, service, _owners, session, publication = ai_setup
    _submit(service, session, publication, key=f"{limit}-limit")
    lease = tasks.claim(f"{limit}-limit-worker")
    result = AIProviderResult(
        content={"text": "over limit"},
        token_usage=2 if limit == "token" else 1,
        cost="2" if limit == "cost" else "0",
    )

    with pytest.raises(AIProviderResultLimitExceeded):
        tasks.succeed(
            lease,
            result,
            provider="fake",
            model_version="fake-v1",
            prompt_version="prompt-v1",
            latency_ms=MAX_PROVIDER_LATENCY_MS + 1 if limit == "latency" else 1,
            max_tokens=1,
            max_cost=Decimal("1"),
        )


def test_worker_persists_bounded_success_and_failure_latency(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    succeeded_task = _submit(service, session, publication, key="latency-success")
    succeeded = AIWorker(
        tasks,
        LimitsEchoProvider(),
        worker_id="latency-success-worker",
        provider_timeout=2,
    ).run_once()

    assert succeeded.id == succeeded_task.id
    assert type(succeeded.latency_ms) is int
    assert 0 <= succeeded.latency_ms <= 2_147_483_647
    assert tasks.get(session.token, succeeded.id).latency_ms == succeeded.latency_ms

    failed_task = _submit(service, session, publication, key="latency-failure")
    failed = AIWorker(
        tasks,
        UnsafeProvider(),
        worker_id="latency-failure-worker",
        max_attempts=1,
    ).run_once()

    assert failed.id == failed_task.id
    assert type(failed.latency_ms) is int
    assert 0 <= failed.latency_ms <= 2_147_483_647
    assert tasks.get(session.token, failed.id).latency_ms == failed.latency_ms


def test_worker_persists_bounded_timeout_latency(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="latency-timeout")

    failed = AIWorker(
        tasks,
        ShortSlowProvider(),
        worker_id="latency-timeout-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
        max_attempts=1,
    ).run_once()

    assert failed.id == task.id
    assert failed.failure == {"code": "provider_timeout"}
    assert type(failed.latency_ms) is int
    assert 0 <= failed.latency_ms <= 2_147_483_647
    assert tasks.get(session.token, failed.id).latency_ms == failed.latency_ms


def test_worker_fail_path_persists_bounded_provider_latency(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="latency-unavailable")

    failed = AIWorker(
        tasks,
        UnavailableCallProvider(),
        worker_id="latency-unavailable-worker",
        max_attempts=1,
    ).run_once()

    assert failed.id == task.id
    assert failed.failure == {"code": "provider_unavailable"}
    assert type(failed.latency_ms) is int
    assert 0 <= failed.latency_ms <= 2_147_483_647
    assert tasks.get(session.token, failed.id).latency_ms == failed.latency_ms


def test_completed_provider_latency_is_immutable(ai_setup):
    from dragon_radar.ai.tables import ai_tasks
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    _submit(service, session, publication, key="immutable-latency")
    completed = AIWorker(
        tasks,
        LimitsEchoProvider(),
        worker_id="immutable-latency-worker",
        provider_timeout=2,
    ).run_once()

    with pytest.raises(SQLAlchemyError):
        with tasks.database.engine.begin() as connection:
            connection.execute(
                update(ai_tasks)
                .where(ai_tasks.c.id == completed.id)
                .values(latency_ms=completed.latency_ms + 1)
            )

    assert tasks.get(session.token, completed.id).latency_ms == completed.latency_ms


@pytest.mark.parametrize(
    ("lease_seconds", "provider_timeout"),
    [
        (30, 30),
        (29, 30),
        (30.5, 30),
    ],
)
def test_worker_rejects_lease_that_cannot_cover_provider_and_persistence(
    lease_seconds,
    provider_timeout,
):
    from dragon_radar.ai.worker import AIWorker

    class UntouchedRepository:
        def __init__(self):
            self.claims = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            self.claims += 1

    class UntouchedProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def __init__(self):
            self.calls = 0

        def generate(self, task_type, manifest, limits):
            self.calls += 1

    repository = UntouchedRepository()
    provider = UntouchedProvider()

    with pytest.raises(ValueError, match="lease"):
        AIWorker(
            repository,
            provider,
            worker_id="invalid-timing-worker",
            lease_for=timedelta(seconds=lease_seconds),
            provider_timeout=provider_timeout,
        )

    assert repository.claims == 0
    assert provider.calls == 0


def test_worker_renews_lease_to_cover_provider_deadline_and_persistence():
    from datetime import UTC, datetime

    from dragon_radar.ai.worker import AI_LEASE_COMPLETION_MARGIN, AIWorker

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        renewed = None

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            return {"input": {}}

        def renew(self, claimed, *, lease_for):
            self.renewed = SimpleNamespace(
                job=claimed.job,
                expires_at=datetime.now(UTC) + lease_for,
            )
            return self.renewed

        def succeed(self, claimed, result, **metadata):
            assert claimed is self.renewed
            self.result = result
            return SimpleNamespace(status="succeeded")

    repository = LeaseRepository()
    completed = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="renew-before-call",
        lease_for=timedelta(seconds=2),
        provider_timeout=1,
    ).run_once()

    assert completed.status == "succeeded"
    assert repository.renewed.expires_at >= (
        datetime.fromisoformat(repository.result.content["deadline"])
        + AI_LEASE_COMPLETION_MARGIN
    )


def test_provider_child_waits_for_go_before_starting_limits_and_provider():
    from datetime import UTC, datetime

    from dragon_radar.ai.worker import _provider_child

    class CapturingProvider:
        calls = 0
        invoked_at = None
        limits = None

        def generate(self, task_type, manifest, limits):
            self.calls += 1
            self.invoked_at = datetime.now(UTC)
            self.limits = limits
            return {"content": {"text": "draft"}, "token_usage": 1, "cost": "0"}

    class ChildConnection:
        def __init__(self, provider):
            self.provider = provider
            self.sent = []
            self.closed = False

        def send(self, message):
            self.sent.append(message)

        def recv(self):
            assert self.sent == [("ready", None)]
            assert self.provider.calls == 0
            time.sleep(0.05)
            return (
                "go",
                {
                    "timeout_seconds": 0.2,
                    "max_tokens": 123,
                    "max_cost": "4.50",
                },
            )

        def close(self):
            self.closed = True

    provider = CapturingProvider()
    connection = ChildConnection(provider)

    _provider_child(connection, provider, "daily_review", {"input": {}})

    assert provider.calls == 1
    assert provider.limits.deadline >= provider.invoked_at + timedelta(seconds=0.19)
    assert provider.limits.timeout_seconds == 0.2
    assert provider.limits.max_tokens == 123
    assert provider.limits.max_cost == Decimal("4.50")
    assert connection.sent[-1][0] == "result"
    assert connection.closed is True


def test_worker_waits_for_delayed_ready_before_renewing_and_starting_timeout(
    monkeypatch,
):
    from dragon_radar.ai.worker import (
        PROCESS_READY_TIMEOUT_SECONDS,
        AIWorker,
    )

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))
    events = []

    class LeaseRepository:
        renewed = None

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            return {"input": {}}

        def renew(self, claimed, *, lease_for):
            events.append("renew")
            self.renewed = SimpleNamespace(job=claimed.job)
            return self.renewed

        def succeed(self, claimed, result, **metadata):
            assert claimed is self.renewed
            return SimpleNamespace(status="succeeded")

    class ParentConnection:
        def __init__(self):
            self.poll_timeouts = []
            self.receive_count = 0
            self.go_messages = []

        def poll(self, timeout):
            self.poll_timeouts.append(timeout)
            if len(self.poll_timeouts) == 1:
                time.sleep(0.15)
            return True

        def recv(self):
            self.receive_count += 1
            if self.receive_count == 1:
                events.append("ready")
                return ("ready", None)
            return (
                "result",
                {"content": {"text": "draft"}, "token_usage": 1, "cost": "0"},
            )

        def send(self, message):
            events.append("go")
            self.go_messages.append(message)

        def close(self):
            pass

    parent = ParentConnection()
    process = HandshakeProcess(events, start_delay=0.15)
    context = _handshake_context(parent, process)
    monkeypatch.setattr(
        "dragon_radar.ai.worker.multiprocessing.get_context",
        lambda method: context,
    )

    completed = AIWorker(
        LeaseRepository(),
        LimitsEchoProvider(),
        worker_id="delayed-ready-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
    ).run_once()

    assert completed.status == "succeeded"
    assert events == ["start", "ready", "renew", "go"]
    assert parent.poll_timeouts == [PROCESS_READY_TIMEOUT_SECONDS, 0.1]
    assert parent.go_messages == [
        (
            "go",
            {
                "timeout_seconds": 0.1,
                "max_tokens": 4096,
                "max_cost": "10",
            },
        )
    ]
    assert process.closed is True


def test_worker_stops_child_when_readiness_times_out(monkeypatch):
    from dragon_radar.ai.worker import AIWorker

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        renewals = 0

        def renew(self, claimed, *, lease_for):
            self.renewals += 1
            return claimed

    class ParentConnection:
        sends = 0

        def poll(self, timeout):
            return False

        def send(self, message):
            self.sends += 1

        def close(self):
            pass

    repository = LeaseRepository()
    parent = ParentConnection()
    process = HandshakeProcess()
    context = _handshake_context(parent, process)
    monkeypatch.setattr(
        "dragon_radar.ai.worker.multiprocessing.get_context",
        lambda method: context,
    )
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="readiness-timeout-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
    )

    with pytest.raises(RuntimeError, match="provider_failed"):
        worker._invoke(lease, {"input": {}})

    assert repository.renewals == 0
    assert parent.sends == 0
    assert process.stopped is True
    assert process.join_timeouts == [0.25]
    assert process.closed is True


def test_worker_stops_child_without_go_when_lease_is_lost_after_ready(monkeypatch):
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.storage.repository import Conflict

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        def renew(self, claimed, *, lease_for):
            assert process.started is True
            raise Conflict("lease lost")

    class ParentConnection:
        sends = 0

        def poll(self, timeout):
            return True

        def recv(self):
            return ("ready", None)

        def send(self, message):
            self.sends += 1

        def close(self):
            pass

    parent = ParentConnection()
    process = HandshakeProcess()
    context = _handshake_context(parent, process)
    monkeypatch.setattr(
        "dragon_radar.ai.worker.multiprocessing.get_context",
        lambda method: context,
    )
    worker = AIWorker(
        LeaseRepository(),
        LimitsEchoProvider(),
        worker_id="lease-lost-before-go-worker",
    )

    with pytest.raises(Conflict, match="lease lost"):
        worker._invoke(lease, {"input": {}})

    assert parent.sends == 0
    assert process.stopped is True
    assert process.join_timeouts == [0.25]
    assert process.closed is True


@pytest.mark.parametrize("conflict_at", ("load", "renew"))
def test_worker_returns_none_when_lease_is_lost_before_provider_call(conflict_at):
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.storage.repository import Conflict

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        writes = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            if conflict_at == "load":
                raise Conflict("cancelled")
            return {"input": {}}

        def renew(self, claimed, *, lease_for):
            raise Conflict("lease lost")

        def fail(self, *args, **kwargs):
            self.writes += 1

        def retry_or_fail(self, *args, **kwargs):
            self.writes += 1

    repository = LeaseRepository()
    result = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id=f"conflict-{conflict_at}",
    ).run_once()

    assert result is None
    assert repository.writes == 0


@pytest.mark.parametrize(
    "outcome",
    (
        "input_forbidden",
        "input_unavailable",
        "provider_unavailable",
        "provider_timeout",
        "provider_failed",
        "succeed",
        "result_limit_exceeded",
    ),
)
def test_worker_returns_none_when_lease_is_lost_at_write_boundary(
    monkeypatch,
    outcome,
):
    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.provider import ProviderUnavailable
    from dragon_radar.ai.repository import AIProviderResultLimitExceeded
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        def __init__(self):
            self.writes = []

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            if outcome == "input_forbidden":
                raise Forbidden
            if outcome == "input_unavailable":
                raise NotFound
            return {"input": {}}

        def fail(self, claimed, *, code, **metadata):
            self.writes.append(("fail", code))
            raise Conflict("lease lost")

        def retry_or_fail(self, claimed, *, code, max_attempts, **metadata):
            self.writes.append(("retry_or_fail", code))
            raise Conflict("lease lost")

        def succeed(self, claimed, result, **metadata):
            self.writes.append(("succeed", None))
            if outcome == "result_limit_exceeded":
                raise AIProviderResultLimitExceeded("result limit")
            raise Conflict("lease lost")

    def invoke(claimed, manifest):
        if outcome == "provider_unavailable":
            raise ProviderUnavailable
        if outcome == "provider_timeout":
            raise TimeoutError
        if outcome == "provider_failed":
            raise RuntimeError("provider_failed")
        return (
            AIProviderResult(
                content={"text": "draft"},
                token_usage=1,
                cost="0",
            ),
            claimed,
        )

    repository = LeaseRepository()
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id=f"write-conflict-{outcome}",
    )
    monkeypatch.setattr(worker, "_invoke", invoke)

    assert worker.run_once() is None
    if outcome == "result_limit_exceeded":
        assert repository.writes == [
            ("succeed", None),
            ("fail", "result_limit_exceeded"),
        ]
    else:
        assert len(repository.writes) == 1


def test_worker_does_not_convert_programming_errors_to_provider_failures(monkeypatch):
    from dragon_radar.ai.worker import AIWorker

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        retries = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            return {"input": {}}

        def retry_or_fail(self, *args, **kwargs):
            self.retries += 1

    repository = LeaseRepository()
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="programming-error-worker",
    )

    def programming_error(claimed, manifest):
        raise KeyError("implementation defect")

    monkeypatch.setattr(worker, "_invoke", programming_error)

    with pytest.raises(KeyError, match="implementation defect"):
        worker.run_once()
    assert repository.retries == 0


def test_worker_batch_does_not_mask_persistence_value_error(monkeypatch):
    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.worker import AIWorker

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class InvalidMetadataRepository:
        failures = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            return {"input": {}}

        def succeed(self, claimed, result, **metadata):
            raise ValueError("Invalid AI provider metadata")

        def fail(self, *args, **kwargs):
            self.failures += 1

    repository = InvalidMetadataRepository()
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="invalid-metadata-worker",
    )
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda claimed, manifest: (
            AIProviderResult(content={"text": "draft"}, token_usage=1, cost="0"),
            claimed,
        ),
    )

    with pytest.raises(ValueError, match="Invalid AI provider metadata"):
        worker.run_batch()
    assert repository.failures == 0


def test_worker_batch_keeps_concurrent_result_when_another_lease_is_lost(monkeypatch):
    from threading import Lock

    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.storage.repository import Conflict

    leases = [
        SimpleNamespace(job=SimpleNamespace(id=1, task_type="daily_review")),
        SimpleNamespace(job=SimpleNamespace(id=2, task_type="daily_review")),
    ]

    class LeaseRepository:
        def __init__(self):
            self.lock = Lock()

        def claim(self, worker_id, *, lease_for, max_attempts):
            with self.lock:
                return leases.pop(0) if leases else None

        def load_input(self, claimed):
            if claimed.job.id == 1:
                raise Conflict("cancelled")
            return {"input": {}}

        def succeed(self, claimed, result, **metadata):
            return SimpleNamespace(id=claimed.job.id, status="succeeded")

    repository = LeaseRepository()
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="concurrent-conflict-worker",
        concurrency=2,
    )
    monkeypatch.setattr(
        worker,
        "_invoke",
        lambda claimed, manifest: (
            AIProviderResult(content={"text": "draft"}, token_usage=1, cost="0"),
            claimed,
        ),
    )

    completed = worker.run_batch()

    assert [record.id for record in completed] == [2]


def test_worker_loop_continues_polling_after_lease_loss():
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.storage.repository import Conflict

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        def __init__(self):
            self.claims = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            self.claims += 1
            return lease if self.claims == 1 else None

        def load_input(self, claimed):
            raise Conflict("cancelled")

    repository = LeaseRepository()
    sleeps = []
    AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="loop-conflict-worker",
    ).run_forever(
        max_iterations=2,
        poll_seconds=0.1,
        sleep=sleeps.append,
    )

    assert repository.claims == 2
    assert sleeps == [0.1, 0.1]


def test_worker_slow_call_does_not_overlap_retry_or_persist_late_result(ai_setup):
    from time import monotonic

    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    started = monotonic()
    worker = AIWorker(
        tasks,
        ShortSlowProvider(),
        worker_id="timeout-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
        max_attempts=2,
        max_tokens=10,
        max_cost=Decimal("1"),
    )

    assert worker.run_once().status == "pending"
    assert worker.run_once().status == "failed"
    assert monotonic() - started < 4
    failed = tasks.get(session.token, task.id)
    assert failed.attempt_count == 2
    assert failed.failure == {"code": "provider_timeout"}
    with tasks.database.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM ai_task_drafts "
                    "WHERE task_id = :task_id AND source = 'provider'"
                ),
                {"task_id": task.id},
            )
            == 0
        )


def test_worker_timeout_is_wall_clock_bounded_and_child_cannot_mutate_late(
    ai_setup,
    tmp_path,
):
    from time import monotonic

    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="hard-timeout")
    started_marker = tmp_path / "provider-started"
    late_marker = tmp_path / "late-provider-mutation"
    started = monotonic()

    result = AIWorker(
        tasks,
        SlowMutatingProvider(str(started_marker), str(late_marker)),
        worker_id="hard-timeout-worker",
        lease_for=timedelta(seconds=4),
        provider_timeout=2,
        max_attempts=1,
        max_tokens=10,
        max_cost=Decimal("1"),
    ).run_once()
    elapsed = monotonic() - started
    time.sleep(0.5)

    assert elapsed < 4
    assert result.status == "failed"
    assert result.failure == {"code": "provider_timeout"}
    assert started_marker.exists()
    assert not late_marker.exists()
    assert tasks.get(session.token, task.id).content_draft is None


def test_worker_cleanup_escalates_without_unbounded_join():
    from time import monotonic, sleep

    from dragon_radar.ai.worker import _stop_process

    class TerminationResistantProcess:
        pid = 123

        def __init__(self):
            self.alive = True
            self.terminated = False
            self.killed = False
            self.closed = False
            self.join_timeouts = []

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.alive = False

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)
            sleep(0.3 if timeout is None else min(timeout, 0.01))

        def close(self):
            self.closed = True

    process = TerminationResistantProcess()
    started = monotonic()

    stopped = _stop_process(process)

    assert monotonic() - started < 0.5
    assert stopped is True
    assert process.terminated is True
    assert process.killed is True
    assert process.closed is True
    assert len(process.join_timeouts) == 2
    assert all(timeout is not None for timeout in process.join_timeouts)


def test_worker_cleanup_failure_is_fatal_and_leaves_lease_running(monkeypatch):
    from dragon_radar.ai.worker import AIWorker, _ProviderCleanupError

    lease = SimpleNamespace(job=SimpleNamespace(task_type="daily_review"))

    class LeaseRepository:
        def __init__(self):
            self.failures = []
            self.retries = []

        def claim(self, worker_id, *, lease_for, max_attempts):
            return lease

        def load_input(self, claimed):
            return {"input": {}}

        def renew(self, claimed, *, lease_for):
            return claimed

        def fail(self, claimed, *, code, **metadata):
            self.failures.append((claimed, code, metadata))
            return SimpleNamespace(status="failed")

        def retry_or_fail(self, claimed, *, code, max_attempts, **metadata):
            self.retries.append((claimed, code, max_attempts, metadata))
            return SimpleNamespace(status="pending")

    class PipeEnd:
        def poll(self, timeout):
            return False

        def close(self):
            pass

    class StubbornProcess:
        pid = 123

        def __init__(self):
            self.daemon = False

        def start(self):
            pass

        def is_alive(self):
            return True

        def terminate(self):
            pass

        def kill(self):
            pass

        def join(self, timeout=None):
            pass

    process = StubbornProcess()
    context = SimpleNamespace(
        Pipe=lambda duplex: (PipeEnd(), PipeEnd()),
        Process=lambda **kwargs: process,
    )
    monkeypatch.setattr(
        "dragon_radar.ai.worker.multiprocessing.get_context",
        lambda method: context,
    )
    repository = LeaseRepository()
    worker = AIWorker(
        repository,
        LimitsEchoProvider(),
        worker_id="stubborn-cleanup-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
    )

    with pytest.raises(_ProviderCleanupError):
        worker.run_once()

    assert process.daemon is True
    assert repository.failures == []
    assert repository.retries == []


def test_worker_enforces_provider_result_caps_and_sanitizes_errors(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    failed = AIWorker(
        tasks,
        UnsafeProvider(),
        worker_id="unsafe-worker",
        max_attempts=1,
    ).run_once()

    assert failed.status == "failed"
    serialized = json.dumps(tasks.get(session.token, task.id).model_dump(mode="json"))
    assert "secret-token" not in serialized
    assert "RuntimeError" not in serialized
    assert failed.failure == {"code": "provider_failed"}


@pytest.mark.parametrize(
    "result",
    (
        {"content": {"text": "x" * 32769}, "token_usage": 1, "cost": "0"},
        {"content": {"text": "missing usage and cost"}},
    ),
)
def test_worker_does_not_retry_invalid_provider_result(ai_setup, result):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="invalid-provider-result")

    failed = AIWorker(
        tasks,
        InvalidResultProvider(result),
        worker_id="invalid-result-worker",
        max_attempts=3,
    ).run_once()

    assert failed.id == task.id
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.failure == {"code": "result_limit_exceeded"}
    assert tasks.claim("retry-worker") is None


@pytest.mark.parametrize("cost", (None, {}, object()))
def test_worker_does_not_retry_non_coercible_provider_cost(ai_setup, cost):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(
        service,
        session,
        publication,
        key=f"invalid-provider-cost-{type(cost).__name__}",
    )

    failed = AIWorker(
        tasks,
        InvalidCostProvider(cost),
        worker_id="invalid-cost-worker",
        max_attempts=3,
    ).run_once()

    assert failed.id == task.id
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.failure == {"code": "result_limit_exceeded"}
    assert tasks.claim("retry-worker") is None


def test_provider_thrown_validation_error_remains_retryable_provider_failure(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="provider-validation-error")

    pending = AIWorker(
        tasks,
        ValidationExceptionProvider(),
        worker_id="provider-validation-error-worker",
        max_attempts=3,
    ).run_once()

    assert pending.id == task.id
    assert pending.status == "pending"
    assert pending.attempt_count == 1
    assert pending.failure == {"code": "provider_failed"}


def test_worker_rejects_unpicklable_provider_before_claim_with_sanitized_error():
    from dragon_radar.ai.worker import AIWorker

    class UnpicklableProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def __reduce__(self):
            raise RuntimeError("provider-secret-value")

        def __repr__(self):
            return "UnpicklableProvider(provider-secret-value)"

    class UntouchedRepository:
        claims = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            self.claims += 1

    repository = UntouchedRepository()
    with pytest.raises(
        ValueError,
        match="^AI provider must support Windows spawn serialization$",
    ) as raised:
        AIWorker(
            repository,
            UnpicklableProvider(),
            worker_id="unpicklable-worker",
        )

    assert repository.claims == 0
    assert "provider-secret-value" not in str(raised.value)


def test_provider_config_is_server_only_and_missing_token_is_unavailable(monkeypatch):
    from dragon_radar.ai.provider import (
        AIProviderLimits,
        ProviderUnavailable,
        load_provider_config,
        provider_from_environment,
    )

    monkeypatch.delenv("DRAGON_AI_TOKEN", raising=False)
    monkeypatch.setenv("DRAGON_AI_PROVIDER", "Azure OpenAI")
    monkeypatch.setenv("DRAGON_AI_MODEL_VERSION", "openai/gpt 4.1")
    monkeypatch.setenv("DRAGON_AI_PROMPT_VERSION", "daily review/v2")
    config = load_provider_config()
    provider = provider_from_environment(config)

    assert config.token is None
    assert config.concurrency >= 1
    assert (
        config.provider,
        config.model_version,
        config.prompt_version,
    ) == ("Azure OpenAI", "openai/gpt 4.1", "daily review/v2")
    with pytest.raises(ProviderUnavailable):
        provider.generate(
            "daily_review",
            {"input": {}},
            AIProviderLimits(
                deadline=NOW + timedelta(seconds=1),
                timeout_seconds=1,
                max_tokens=1,
                max_cost=Decimal("1"),
            ),
        )

    monkeypatch.setenv("DRAGON_AI_TOKEN", "secret-token-value")
    configured = load_provider_config()
    assert configured.token == "secret-token-value"
    assert "secret-token-value" not in repr(configured)


@pytest.mark.parametrize(
    ("environment_name", "value"),
    (
        ("DRAGON_AI_PROVIDER", "unsafe\nprovider"),
        ("DRAGON_AI_MODEL_VERSION", "模型/v1"),
        ("DRAGON_AI_PROMPT_VERSION", "prompt\tv1"),
    ),
)
def test_provider_config_rejects_unsafe_metadata_with_sanitized_error(
    monkeypatch,
    environment_name,
    value,
):
    from dragon_radar.ai.provider import load_provider_config

    monkeypatch.setenv(environment_name, value)

    with pytest.raises(ValueError, match=f"^Invalid {environment_name}$") as raised:
        load_provider_config()

    assert value not in str(raised.value)


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("name", "unsafe\nprovider"),
        ("model_version", object()),
        ("prompt_version", "prompt\tv1"),
    ),
)
def test_worker_rejects_invalid_direct_provider_metadata_before_claim(
    attribute,
    value,
):
    from dragon_radar.ai.worker import AIWorker

    class UntouchedRepository:
        claims = 0

        def claim(self, worker_id, *, lease_for, max_attempts):
            self.claims += 1

    provider = MetadataProvider()
    setattr(provider, attribute, value)
    repository = UntouchedRepository()

    with pytest.raises(
        ValueError,
        match="^Invalid AI provider metadata$",
    ) as raised:
        AIWorker(repository, provider, worker_id="invalid-provider-metadata")

    assert repository.claims == 0
    assert str(value) not in str(raised.value)


def test_realistic_provider_metadata_persists(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="realistic-provider-metadata")

    completed = AIWorker(
        tasks,
        MetadataProvider(),
        worker_id="realistic-metadata-worker",
    ).run_once()

    assert completed.id == task.id
    assert completed.status == "succeeded"
    assert completed.provider == "Azure OpenAI"
    assert completed.model_version == "openai/gpt 4.1"
    assert completed.prompt_version == "daily review/v2"


def test_get_adds_processing_signal_and_deterministic_template(ai_setup):
    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)

    tasks.clock = lambda: NOW + timedelta(seconds=60)
    processing = tasks.get(session.token, task.id)
    assert processing.processing_overdue is True
    assert processing.content_draft is None

    tasks.clock = lambda: NOW + timedelta(seconds=120)
    templated = tasks.get(session.token, task.id)
    assert templated.status == "pending"
    assert templated.content_draft.source == "deterministic_template"
    assert templated.content_draft.ai_generated is False
    assert "AI generated" not in templated.content_draft.model_dump_json()
    assert tasks.get(session.token, task.id).content_draft == templated.content_draft


def test_failed_task_gets_deterministic_template_at_deadline(ai_setup):
    tasks, service, _owners, session, publication = ai_setup
    failed_task = _submit(service, session, publication, key="failed-template")
    lease = tasks.claim("failed-template-worker")
    failed = tasks.fail(lease, code="provider_unavailable")
    assert failed.id == failed_task.id
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    failed_with_template = tasks.get(session.token, failed_task.id)
    assert failed_with_template.status == "failed"
    assert failed_with_template.content_draft.source == "deterministic_template"
    assert failed_with_template.content_draft.ai_generated is False


def test_late_provider_draft_appends_and_does_not_override_human_edit(ai_setup):
    from dragon_radar.ai.models import AIProviderResult

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    lease = tasks.claim("late-worker", lease_for=timedelta(seconds=300))
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    template = tasks.get(session.token, task.id).content_draft
    edited = tasks.review(
        session.token,
        session.csrf,
        task.id,
        decision="edit",
        revision=template.revision,
        edited_content={"text": "Owner edited draft."},
        key="edit-template",
    )
    completed = tasks.succeed(
        lease,
        AIProviderResult(
            content={"text": "Late provider draft."},
            token_usage=4,
            cost="0.25",
        ),
        provider="fake",
        model_version="fake-v1",
        prompt_version="prompt-v1",
        latency_ms=120_000,
        max_tokens=10,
        max_cost=Decimal("1"),
    )

    assert edited.human_review_status == "edited"
    assert completed.status == "succeeded"
    fetched = tasks.get(session.token, task.id)
    assert fetched.content_draft.content == {"text": "Owner edited draft."}
    revised = tasks.review(
        session.token,
        session.csrf,
        task.id,
        decision="edit",
        revision=fetched.content_draft.revision,
        edited_content={"text": "Owner revised after provider completion."},
        key="edit-after-provider",
    )
    assert revised.revision == 4
    revised_task = tasks.get(session.token, task.id)
    assert revised_task.content_draft.content == {
        "text": "Owner revised after provider completion."
    }
    with tasks.database.engine.connect() as connection:
        drafts = (
            connection.execute(
                text(
                    "SELECT revision, source, content FROM ai_task_drafts "
                    "WHERE task_id = :task_id ORDER BY revision"
                ),
                {"task_id": task.id},
            )
            .mappings()
            .all()
        )
    assert [row["source"] for row in drafts] == [
        "deterministic_template",
        "human_edit",
        "provider",
        "human_edit",
    ]


def test_late_provider_draft_does_not_replace_delivered_template_without_review(
    ai_setup,
):
    from dragon_radar.ai.models import AIProviderResult

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="late-without-review")
    lease = tasks.claim("late-without-review-worker", lease_for=timedelta(seconds=300))
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    template = tasks.get(session.token, task.id).content_draft

    completed = tasks.succeed(
        lease,
        AIProviderResult(
            content={"text": "Late provider draft."},
            token_usage=4,
            cost="0.25",
        ),
        provider="fake",
        model_version="fake-v1",
        prompt_version="prompt-v1",
        latency_ms=120_000,
        max_tokens=10,
        max_cost=Decimal("1"),
    )

    assert completed.status == "succeeded"
    assert completed.content_draft == template
    fetched = tasks.get(session.token, task.id)
    assert fetched.content_draft == template
    with tasks.database.engine.connect() as connection:
        drafts = (
            connection.execute(
                text(
                    "SELECT revision, source, content FROM ai_task_drafts "
                    "WHERE task_id = :task_id ORDER BY revision"
                ),
                {"task_id": task.id},
            )
            .mappings()
            .all()
        )
    assert [row["source"] for row in drafts] == [
        "deterministic_template",
        "provider",
    ]
    assert drafts[1]["content"] == {"text": "Late provider draft."}


def test_late_provider_completion_selects_template_without_prior_poll(ai_setup):
    from dragon_radar.ai.models import AIProviderResult

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="late-without-poll")
    lease = tasks.claim("late-without-poll-worker", lease_for=timedelta(seconds=300))
    tasks.clock = lambda: NOW + timedelta(seconds=120)

    completed = tasks.succeed(
        lease,
        AIProviderResult(
            content={"text": "Late provider draft."},
            token_usage=4,
            cost="0.25",
        ),
        provider="fake",
        model_version="fake-v1",
        prompt_version="prompt-v1",
        latency_ms=120_000,
        max_tokens=10,
        max_cost=Decimal("1"),
    )

    assert completed.status == "succeeded"
    assert completed.content_draft.source == "deterministic_template"
    assert completed.content_draft.ai_generated is False
    assert tasks.get(session.token, task.id).content_draft == completed.content_draft
    with tasks.database.engine.connect() as connection:
        drafts = (
            connection.execute(
                text(
                    "SELECT revision, source, content FROM ai_task_drafts "
                    "WHERE task_id = :task_id ORDER BY revision"
                ),
                {"task_id": task.id},
            )
            .mappings()
            .all()
        )
    assert [row["source"] for row in drafts] == [
        "deterministic_template",
        "provider",
    ]
    assert drafts[1]["content"] == {"text": "Late provider draft."}


def test_on_time_provider_completion_survives_poll_after_template_deadline(ai_setup):
    from dragon_radar.ai.models import AIProviderResult

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="on-time-before-late-poll")
    lease = tasks.claim(
        "on-time-before-late-poll-worker",
        lease_for=timedelta(seconds=300),
    )
    tasks.clock = lambda: NOW + timedelta(seconds=119)

    completed = tasks.succeed(
        lease,
        AIProviderResult(
            content={"text": "On-time provider draft."},
            token_usage=4,
            cost="0.25",
        ),
        provider="fake",
        model_version="fake-v1",
        prompt_version="prompt-v1",
        latency_ms=119_000,
        max_tokens=10,
        max_cost=Decimal("1"),
    )
    assert completed.content_draft.source == "provider"

    tasks.clock = lambda: NOW + timedelta(seconds=180)
    fetched = tasks.get(session.token, task.id)

    assert fetched.content_draft == completed.content_draft
    with tasks.database.engine.connect() as connection:
        drafts = (
            connection.execute(
                text(
                    "SELECT revision, source, content FROM ai_task_drafts "
                    "WHERE task_id = :task_id ORDER BY revision"
                ),
                {"task_id": task.id},
            )
            .mappings()
            .all()
        )
    assert [row["source"] for row in drafts] == ["provider"]


def test_review_is_append_only_idempotent_and_revision_checked(ai_setup):
    from dragon_radar.storage.repository import Conflict

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    draft = tasks.get(session.token, task.id).content_draft
    first = tasks.review(
        session.token,
        session.csrf,
        task.id,
        decision="accept",
        revision=draft.revision,
        key="review",
    )
    replay = tasks.review(
        session.token,
        session.csrf,
        task.id,
        decision="accept",
        revision=draft.revision,
        key="review",
    )
    assert replay == first
    assert first.human_review_status == "accepted"

    with pytest.raises(Conflict):
        tasks.review(
            session.token,
            session.csrf,
            task.id,
            decision="reject",
            revision=draft.revision,
            key="review",
        )
    with pytest.raises(Conflict):
        tasks.review(
            session.token,
            session.csrf,
            task.id,
            decision="reject",
            revision=draft.revision + 1,
            key="stale-review",
        )
    with tasks.database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ai_task_reviews")) == 1


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_revision", "expected_note"),
    (
        (
            "accept",
            "accepted",
            1,
            "已接受 revision=1；与服务端 revision 不一致返回 409",
        ),
        (
            "edit",
            "edited",
            2,
            "人工编辑后对 revision=2 重新触发校验；与服务端 revision 不一致返回 409",
        ),
        (
            "reject",
            "rejected",
            1,
            "已拒绝 revision=1；与服务端 revision 不一致返回 409",
        ),
    ),
)
def test_review_receipt_note_matches_decision_and_replays_identically(
    ai_setup,
    decision,
    expected_status,
    expected_revision,
    expected_note,
):
    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication, key=f"{decision}-note-task")
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    draft = tasks.get(session.token, task.id).content_draft
    values = {
        "decision": decision,
        "revision": draft.revision,
        "key": f"{decision}-note",
    }
    if decision == "edit":
        values["edited_content"] = {"text": "Owner edit."}

    receipt = tasks.review(
        session.token,
        session.csrf,
        task.id,
        **values,
    )
    replay = tasks.review(
        session.token,
        session.csrf,
        task.id,
        **values,
    )

    assert receipt.human_review_status == expected_status
    assert receipt.revision == expected_revision
    assert receipt.note == expected_note
    assert replay == receipt


@pytest.mark.anyio
async def test_ai_routes_are_owner_only_csrf_protected_and_sanitized(ai_setup):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.owner.app import create_owner_app

    tasks, service, owners, session, publication = ai_setup
    public = create_workbench_app(repository=service.publications)
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    public_paths = {
        route.path
        for route in public.routes
        if getattr(route, "path", "").startswith("/api/ai/")
    }
    owner_paths = {
        route.path
        for route in owner.routes
        if getattr(route, "path", "").startswith("/api/ai/")
    }
    assert public_paths == set()
    assert owner_paths == {
        "/api/ai/summarize",
        "/api/ai/topic-suggest",
        "/api/ai/explain-stock",
        "/api/ai/daily-review",
        "/api/ai/task/{task_id}",
        "/api/ai/task/{task_id}/cancel",
        "/api/ai/task/{task_id}/review",
    }
    body = {
        "date": publication.trade_date.isoformat(),
        "notes_context": "notes",
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": "http-ai",
    }
    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        guest = await client.post("/api/ai/daily-review", json=body)
        no_csrf = await client.post(
            "/api/ai/daily-review",
            json=body,
            headers=_headers(session, csrf=False),
        )
        created = await client.post(
            "/api/ai/daily-review",
            json=body,
            headers=_headers(session),
        )
        fetched = await client.get(
            f"/api/ai/task/{created.json()['task_id']}",
            headers=_headers(session),
        )
        bad = await client.post(
            "/api/ai/daily-review",
            json={**body, "notes_context": "secret-marker", "extra": "secret-marker"},
            headers=_headers(session),
        )
        attempted_override = await client.post(
            "/api/ai/daily-review",
            json={**body, "retrieval_version": "client-controlled"},
            headers=_headers(session),
        )

    assert guest.status_code == 403
    assert no_csrf.status_code == 403
    assert created.status_code == 200
    assert set(created.json()) >= {"task_id", "status"}
    assert fetched.status_code == 200
    assert "owner_id" not in fetched.text
    assert fetched.json()["retrieval_version"] == "r1"
    assert fetched.json()["latency_ms"] is None
    assert bad.status_code == 400
    assert attempted_override.status_code == 400
    assert "secret-marker" not in bad.text
    assert created.json()["status"] == "pending"
    assert tasks.get(session.token, created.json()["task_id"]).status == "pending"


@pytest.mark.anyio
async def test_ai_task_route_returns_content_draft_payload_directly(ai_setup):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    tasks, service, owners, session, publication = ai_setup
    task = _submit(service, session, publication, key="http-draft-payload")
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        fetched = await client.get(
            f"/api/ai/task/{task.id}",
            headers=_headers(session),
        )

    assert fetched.status_code == 200
    draft = tasks.get(session.token, task.id).content_draft
    expected = draft.model_dump(mode="json")["content"]
    payload = fetched.json()
    assert payload["content_draft"] == expected
    assert payload["revision"] == draft.revision
    assert payload["content_draft_source"] == draft.source
    assert (
        payload["content_draft_created_at"]
        == draft.model_dump(mode="json")["created_at"]
    )
    assert payload["ai_generated"] is draft.ai_generated
    assert "revision" not in payload["content_draft"]
    assert "source" not in payload["content_draft"]
    assert "owner_id" not in payload
    assert "input_manifest" not in payload


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "task_input"),
    (
        ("/api/ai/summarize", {"article_id": str(ARTICLE_A)}),
        ("/api/ai/topic-suggest", {"date": NOW.date().isoformat()}),
        (
            "/api/ai/explain-stock",
            {"security_id": "A", "date": NOW.date().isoformat()},
        ),
        (
            "/api/ai/daily-review",
            {"date": NOW.date().isoformat(), "notes_context": "notes"},
        ),
    ),
)
async def test_all_ai_submission_routes_and_replays_return_pending(
    ai_setup, path, task_input
):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    _tasks, service, owners, session, publication = ai_setup
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )
    if "date" in task_input:
        task_input = {
            **task_input,
            "date": publication.trade_date.isoformat(),
        }
    body = {
        **task_input,
        "snapshot_id": str(publication.id),
        "as_of": publication.published_at.isoformat(),
        "version": f"v{publication.version}",
        "idempotency_key": f"http-{path.rsplit('/', 1)[-1]}",
    }

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        created = await client.post(path, json=body, headers=_headers(session))
        replay = await client.post(path, json=body, headers=_headers(session))

    assert created.status_code == 200
    assert created.json()["status"] == "pending"
    assert created.json()["content_draft"] is None
    assert created.json()["revision"] is None
    assert created.json()["content_draft_source"] is None
    assert created.json()["content_draft_created_at"] is None
    assert created.json()["ai_generated"] is None
    assert replay.status_code == 200
    assert replay.json()["task_id"] == created.json()["task_id"]
    assert replay.json()["status"] == "pending"


@pytest.mark.anyio
async def test_cancel_and_review_routes_return_original_command_receipts(ai_setup):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.owner.app import create_owner_app

    tasks, service, owners, session, publication = ai_setup
    cancelled_task = _submit(service, session, publication, key="receipt-cancel-task")
    reviewed_task = _submit(service, session, publication, key="receipt-review-task")
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    review_draft = tasks.get(session.token, reviewed_task.id).content_draft
    owner = create_owner_app(
        repository=service.publications.market,
        owner_repository=owners,
        publication_repository=service.publications,
        ai_service=service,
    )

    async with AsyncClient(
        transport=ASGITransport(app=owner),
        base_url="https://localhost:8030",
    ) as client:
        cancel_path = f"/api/ai/task/{cancelled_task.id}/cancel"
        cancel_body = {"idempotency_key": "cancel-receipt"}
        cancelled = await client.post(
            cancel_path,
            json=cancel_body,
            headers=_headers(session),
        )
        tasks.clock = lambda: NOW + timedelta(seconds=180)
        cancel_replay = await client.post(
            cancel_path,
            json=cancel_body,
            headers=_headers(session),
        )

        review_path = f"/api/ai/task/{reviewed_task.id}/review"
        edit_body = {
            "decision": "edit",
            "revision": review_draft.revision,
            "edited_content": {"text": "owner edit"},
            "idempotency_key": "edit-receipt",
        }
        edited = await client.post(
            review_path,
            json=edit_body,
            headers=_headers(session),
        )
        accepted = await client.post(
            review_path,
            json={
                "decision": "accept",
                "revision": 2,
                "idempotency_key": "accept-receipt",
            },
            headers=_headers(session),
        )
        edit_replay = await client.post(
            review_path,
            json=edit_body,
            headers=_headers(session),
        )

    assert cancelled.status_code == 200
    assert set(cancelled.json()) == {
        "task_id",
        "status",
        "idempotency_key",
        "accepted_at",
    }
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["idempotency_key"] == "cancel-receipt"
    assert cancel_replay.json() == cancelled.json()
    assert accepted.json()["human_review_status"] == "accepted"
    assert edited.status_code == 200
    assert set(edited.json()) == {
        "task_id",
        "human_review_status",
        "revision",
        "idempotency_key",
        "verification_status",
        "note",
    }
    assert edited.json() == {
        "task_id": str(reviewed_task.id),
        "human_review_status": "edited",
        "revision": 2,
        "idempotency_key": "edit-receipt",
        "verification_status": "needs_human",
        "note": (
            "人工编辑后对 revision=2 重新触发校验；与服务端 revision 不一致返回 409"
        ),
    }
    assert edit_replay.json() == edited.json()


def test_ai_worker_cli_is_bounded_and_compose_is_internal():
    from dragon_radar.ai.worker import (
        AI_LEASE_COMPLETION_MARGIN,
        DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS,
    )
    from dragon_radar.cli import _parser

    defaults = _parser().parse_args(["ai-worker"])
    configured = _parser().parse_args(
        [
            "ai-worker",
            "--worker-id",
            "ai-worker.prod:1",
            "--poll-seconds",
            "0.1",
            "--lease-seconds",
            "3600",
            "--max-attempts",
            "10",
            "--provider-timeout-seconds",
            "300",
            "--concurrency",
            "8",
        ]
    )
    assert defaults.worker_id == "ai-worker-1"
    assert defaults.provider_timeout_seconds is None
    assert defaults.concurrency is None
    assert defaults.lease_seconds >= (
        DEFAULT_AI_PROVIDER_TIMEOUT_SECONDS + AI_LEASE_COMPLETION_MARGIN.total_seconds()
    )
    assert vars(configured) == {
        "command": "ai-worker",
        "worker_id": "ai-worker.prod:1",
        "poll_seconds": 0.1,
        "lease_seconds": 3600,
        "max_attempts": 10,
        "provider_timeout_seconds": 300.0,
        "concurrency": 8,
    }

    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DRAGON_DB_PASSWORD"] = "compose-validation-only"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(root / "compose.yaml"),
            "config",
            "--format",
            "json",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    worker = json.loads(result.stdout)["services"]["ai-worker"]
    assert worker["command"][0] == "ai-worker"
    assert worker["depends_on"]["db"]["condition"] == "service_healthy"
    assert set(worker["networks"]) == {"backend"}
    assert "ports" not in worker
    assert all("TOKEN" not in item for item in worker["command"])
    command = worker["command"]
    lease_seconds = float(command[command.index("--lease-seconds") + 1])
    timeout_seconds = float(command[command.index("--provider-timeout-seconds") + 1])
    assert lease_seconds >= (
        timeout_seconds + AI_LEASE_COMPLETION_MARGIN.total_seconds()
    )


def _capture_ai_worker_cli(monkeypatch, argv):
    from dragon_radar.cli import main

    captured = {}
    database = SimpleNamespace(close=lambda: None)

    class Worker:
        def __init__(self, repository, provider, **options):
            captured.update(options)

        def run_forever(self, *, poll_seconds):
            captured["poll_seconds"] = poll_seconds

    monkeypatch.setenv("DRAGON_DATABASE_URL", "unused-test-database")
    monkeypatch.setattr("dragon_radar.cli.Database", lambda: database)
    monkeypatch.setattr("dragon_radar.cli.Repository", lambda value: object())
    monkeypatch.setattr(
        "dragon_radar.publication.repository.PublicationRepository",
        lambda *args: object(),
    )
    monkeypatch.setattr(
        "dragon_radar.owner.repository.OwnerRepository",
        lambda *args: object(),
    )
    monkeypatch.setattr(
        "dragon_radar.ai.repository.AITaskRepository",
        lambda *args: object(),
    )
    monkeypatch.setattr(
        "dragon_radar.ai.provider.provider_from_environment",
        lambda config: object(),
    )
    monkeypatch.setattr("dragon_radar.ai.worker.AIWorker", Worker)

    assert main(["ai-worker", *argv]) == 0
    return captured


def test_ai_worker_cli_uses_provider_config_when_flags_are_absent(monkeypatch):
    monkeypatch.setenv("DRAGON_AI_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("DRAGON_AI_CONCURRENCY", "4")

    captured = _capture_ai_worker_cli(monkeypatch, [])

    assert captured["provider_timeout"] == 7.5
    assert captured["concurrency"] == 4


def test_ai_worker_cli_flags_override_provider_config(monkeypatch):
    monkeypatch.setenv("DRAGON_AI_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("DRAGON_AI_CONCURRENCY", "4")

    captured = _capture_ai_worker_cli(
        monkeypatch,
        ["--provider-timeout-seconds", "9", "--concurrency", "6"],
    )

    assert captured["provider_timeout"] == 9
    assert captured["concurrency"] == 6


def test_ai_worker_cli_rejects_unsafe_timing_before_database_with_sanitized_error(
    monkeypatch,
    capsys,
):
    from dragon_radar.cli import main

    secret = "secret-ai-provider-token"
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    monkeypatch.setenv("DRAGON_AI_TOKEN", secret)

    result = main(
        [
            "ai-worker",
            "--lease-seconds",
            "30",
            "--provider-timeout-seconds",
            "30",
        ]
    )
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert output.err.strip() == "Invalid configuration or input"
    assert secret not in output.err
    assert "Traceback" not in output.err
