"""Owner-scoped, bounded asynchronous AI task contracts."""

import json
import os
import secrets
import subprocess
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from factories import NOW, batch, grant
from news_factories import ARTICLE_A, article_revision, event_evidence
from pydantic import ValidationError
from sqlalchemy import insert, inspect, select, text


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
    columns = {
        column["name"] for column in inspect(database.engine).get_columns("ai_tasks")
    }
    assert {
        "owner_id",
        "input_manifest",
        "task_type",
        "status",
        "verification_status",
        "human_review_status",
        "model_version",
        "prompt_version",
        "provider",
        "token_usage",
        "cost",
        "lease_token",
        "lease_expires_at",
    } <= columns


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
    queued = _submit(service, session, publication, key="before-ai-revocation")
    lease = tasks.claim("permission-worker")

    repository.register_source(grant(ai=False))

    assert service.publications.get_publication(publication.id) is not None
    with pytest.raises(Forbidden):
        _submit(service, session, publication, key="after-ai-revocation")
    with pytest.raises(Forbidden):
        tasks.load_input(lease)
    assert tasks.get(session.token, queued.id).status == "running"


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
    assert task.status == "queued"
    assert task.verification_status == "pending"
    assert task.human_review_status == "unreviewed"


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

    class CapturingProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def generate(self, task_type, manifest, limits):
            self.limits = limits
            return {"content": {"text": "done"}, "token_usage": 1, "cost": "0"}

    tasks, service, _owners, session, publication = ai_setup
    _submit(service, session, publication)
    provider = CapturingProvider()
    started = datetime.now(UTC)

    completed = AIWorker(
        tasks,
        provider,
        worker_id="limits-worker",
        provider_timeout=2,
        max_tokens=123,
        max_cost=Decimal("4.50"),
    ).run_once()

    assert completed.status == "succeeded"
    assert isinstance(provider.limits, AIProviderLimits)
    assert provider.limits.timeout_seconds == 2
    assert provider.limits.max_tokens == 123
    assert provider.limits.max_cost == Decimal("4.50")
    assert provider.limits.deadline >= started + timedelta(seconds=1.9)
    with pytest.raises(FrozenInstanceError):
        provider.limits.max_tokens = 1


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
            return SimpleNamespace(status="succeeded")

    class CapturingProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def generate(self, task_type, manifest, limits):
            self.limits = limits
            return {"content": {"text": "done"}, "token_usage": 1, "cost": "0"}

    repository = LeaseRepository()
    provider = CapturingProvider()
    completed = AIWorker(
        repository,
        provider,
        worker_id="renew-before-call",
        lease_for=timedelta(seconds=2),
        provider_timeout=1,
    ).run_once()

    assert completed.status == "succeeded"
    assert repository.renewed.expires_at >= (
        provider.limits.deadline + AI_LEASE_COMPLETION_MARGIN
    )


def test_worker_slow_call_does_not_overlap_retry_or_persist_late_result(ai_setup):
    from threading import Lock
    from time import monotonic, sleep

    from dragon_radar.ai.worker import AIWorker

    class SlowProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = Lock()

        def generate(self, task_type, manifest, limits):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                sleep(0.15)
                return {
                    "content": {"text": "too late"},
                    "token_usage": 1,
                    "cost": "0",
                }
            finally:
                with self.lock:
                    self.active -= 1

    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)
    provider = SlowProvider()
    started = monotonic()
    worker = AIWorker(
        tasks,
        provider,
        worker_id="timeout-worker",
        lease_for=timedelta(seconds=2),
        provider_timeout=0.1,
        max_attempts=2,
        max_tokens=10,
        max_cost=Decimal("1"),
    )

    assert worker.run_once().status == "queued"
    assert worker.run_once().status == "failed"
    assert monotonic() - started >= 0.25
    assert provider.max_active == 1
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


def test_worker_enforces_provider_result_caps_and_sanitizes_errors(ai_setup):
    from dragon_radar.ai.worker import AIWorker

    class UnsafeProvider:
        name = "fake"
        model_version = "fake-v1"
        prompt_version = "prompt-v1"

        def generate(self, task_type, manifest, limits):
            raise RuntimeError("Bearer secret-token raw provider stack")

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


def test_provider_config_is_server_only_and_missing_token_is_unavailable(monkeypatch):
    from dragon_radar.ai.provider import (
        AIProviderLimits,
        ProviderUnavailable,
        load_provider_config,
        provider_from_environment,
    )

    monkeypatch.delenv("DRAGON_AI_TOKEN", raising=False)
    monkeypatch.setenv("DRAGON_AI_PROVIDER", "configured-provider")
    monkeypatch.setenv("DRAGON_AI_MODEL_VERSION", "secret-safe-model")
    config = load_provider_config()
    provider = provider_from_environment(config)

    assert config.token is None
    assert config.concurrency >= 1
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


def test_get_adds_processing_signal_and_deterministic_template(ai_setup):
    tasks, service, _owners, session, publication = ai_setup
    task = _submit(service, session, publication)

    tasks.clock = lambda: NOW + timedelta(seconds=60)
    processing = tasks.get(session.token, task.id)
    assert processing.processing_overdue is True
    assert processing.content_draft is None

    tasks.clock = lambda: NOW + timedelta(seconds=120)
    templated = tasks.get(session.token, task.id)
    assert templated.status == "queued"
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
    assert revised.content_draft.revision == 4
    assert revised.content_draft.content == {
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

    assert guest.status_code == 403
    assert no_csrf.status_code == 403
    assert created.status_code == 200
    assert set(created.json()) >= {"task_id", "status"}
    assert fetched.status_code == 200
    assert "owner_id" not in fetched.text
    assert bad.status_code == 400
    assert "secret-marker" not in bad.text
    assert tasks.get(session.token, created.json()["task_id"]).status == "queued"


def test_ai_worker_cli_is_bounded_and_compose_is_internal():
    from dragon_radar.ai.worker import AI_LEASE_COMPLETION_MARGIN
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
    assert defaults.lease_seconds >= (
        defaults.provider_timeout_seconds + AI_LEASE_COMPLETION_MARGIN.total_seconds()
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
