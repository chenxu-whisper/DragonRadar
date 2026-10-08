"""Transactional owner-scoped persistence for asynchronous AI tasks."""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.engine import Connection

from dragon_radar.ai.models import (
    AI_INPUT_DOMAINS,
    MAX_PROVIDER_LATENCY_MS,
    AICancellationReceipt,
    AIDraft,
    AIProviderResult,
    AIReviewReceipt,
    AISubmission,
    AITaskLease,
    AITaskRecord,
    canonical_object,
)
from dragon_radar.ai.provider import validate_provider_metadata
from dragon_radar.ai.tables import (
    ai_task_cancellations,
    ai_task_drafts,
    ai_task_reviews,
    ai_tasks,
)
from dragon_radar.ai.verification import verify_output
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound, _identifier

MAX_ACTIVE_AI_TASKS_PER_OWNER = 8


class AITaskRateLimited(Exception):
    """Raised when an owner already has the maximum active AI task count."""


class AIProviderResultLimitExceeded(ValueError):
    """Raised when provider output exceeds a configured persistence limit."""


def _stored_object(value: Mapping) -> dict:
    canonical = canonical_object(dict(value))
    return json.loads(canonical)


def _digest(value: Mapping) -> str:
    return hashlib.sha256(canonical_object(dict(value)).encode("ascii")).hexdigest()


def _review_note(decision: str, revision: int) -> str:
    action = {
        "accept": "已接受",
        "edit": "人工编辑后对",
        "reject": "已拒绝",
    }[decision]
    verification = " 重新触发校验" if decision == "edit" else ""
    revision_note = f"{action} revision={revision}{verification}；"
    return revision_note + "与服务端 revision 不一致返回 409"


def _validate_latency(
    latency_ms: int | None,
    *,
    required: bool = False,
    result_limit: bool = False,
) -> None:
    if latency_ms is None:
        if required:
            raise ValueError("Invalid AI provider latency")
        return
    if type(latency_ms) is not int:
        raise ValueError("Invalid AI provider latency")
    if not 0 <= latency_ms <= MAX_PROVIDER_LATENCY_MS:
        error = AIProviderResultLimitExceeded if result_limit else ValueError
        raise error("AI provider latency exceeds configured limits")


class AITaskRepository:
    def __init__(
        self,
        database,
        owner_repository,
        publication_repository=None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.owners = owner_repository
        self.publications = publication_repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("AI task clock must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _lock(connection: Connection, key: str) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    @staticmethod
    def _task_row(
        connection: Connection,
        task_id: UUID | str,
        *,
        owner_id: UUID | None = None,
        lock: bool = False,
    ):
        try:
            task_id = UUID(str(task_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid AI task ID") from None
        query = select(ai_tasks).where(ai_tasks.c.id == task_id)
        if owner_id is not None:
            query = query.where(ai_tasks.c.owner_id == owner_id)
        if lock:
            query = query.with_for_update()
        row = connection.execute(query).mappings().first()
        if row is None:
            raise NotFound("AI task not found")
        return row

    @staticmethod
    def _next_revision(connection: Connection, task_id: UUID) -> int:
        return (
            connection.scalar(
                select(func.coalesce(func.max(ai_task_drafts.c.revision), 0)).where(
                    ai_task_drafts.c.task_id == task_id
                )
            )
            + 1
        )

    @staticmethod
    def _template(row) -> dict:
        task_type = row["task_type"].replace("_", " ")
        publication = row["input_manifest"]["publication"]
        return {
            "facts": [],
            "inferences": [],
            "missing_evidence": [
                {
                    "text": (
                        f"AI {task_type} output was not selected within 120 seconds."
                    ),
                    "reason": "deterministic_template_selected",
                },
                {
                    "text": (
                        "Complete an owner review against the published snapshot "
                        f"{publication['snapshot_id']} at {publication['as_of']}."
                    ),
                    "reason": "owner_review_required",
                },
            ],
            "citations": [],
        }

    def _ensure_template(self, connection: Connection, row, now: datetime):
        deadline = row["created_at"] + timedelta(seconds=120)
        if (
            row["status"] == "cancelled"
            or now < deadline
            or (
                row["status"] == "succeeded"
                and row["completed_at"] is not None
                and row["completed_at"] < deadline
            )
        ):
            return row
        template_revision = connection.scalar(
            select(ai_task_drafts.c.revision)
            .where(
                ai_task_drafts.c.task_id == row["id"],
                ai_task_drafts.c.source == "deterministic_template",
            )
            .limit(1)
        )
        if template_revision is None:
            template_revision = self._next_revision(connection, row["id"])
            connection.execute(
                insert(ai_task_drafts).values(
                    id=uuid4(),
                    task_id=row["id"],
                    revision=template_revision,
                    source="deterministic_template",
                    content=self._template(row),
                    model_version=None,
                    prompt_version=None,
                    provider=None,
                    token_usage=None,
                    cost=None,
                    created_at=now,
                )
            )
        if (
            row["human_review_status"] == "unreviewed"
            and row["selected_revision"] != template_revision
        ):
            row = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(selected_revision=template_revision)
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
        return row

    @staticmethod
    def _draft(connection: Connection, row) -> AIDraft | None:
        if row["selected_revision"] is None:
            return None
        draft = (
            connection.execute(
                select(ai_task_drafts).where(
                    ai_task_drafts.c.task_id == row["id"],
                    ai_task_drafts.c.revision == row["selected_revision"],
                )
            )
            .mappings()
            .one()
        )
        return AIDraft(
            revision=draft["revision"],
            source=draft["source"],
            content=draft["content"],
            ai_generated=draft["source"] == "provider",
            created_at=draft["created_at"],
        )

    def _record(
        self,
        connection: Connection,
        row,
        *,
        now: datetime | None = None,
        ensure_template: bool = False,
    ) -> AITaskRecord:
        now = now or self._now()
        if ensure_template:
            row = self._ensure_template(connection, row, now)
        return AITaskRecord(
            id=row["id"],
            owner_id=row["owner_id"],
            task_type=row["task_type"],
            snapshot_id=row["snapshot_id"],
            as_of=row["as_of"],
            version=row["version"],
            input_manifest=row["input_manifest"],
            status=row["status"],
            verification_status=row["verification_status"],
            human_review_status=row["human_review_status"],
            attempt_count=row["attempt_count"],
            content_draft=self._draft(connection, row),
            processing_overdue=(
                row["status"] in {"pending", "running"}
                and now - row["created_at"] >= timedelta(seconds=60)
            ),
            model_version=row["model_version"],
            prompt_version=row["prompt_version"],
            retrieval_version=row["retrieval_version"],
            provider=row["provider"],
            token_usage=row["token_usage"],
            cost=row["cost"],
            latency_ms=row["latency_ms"],
            failure=row["failure"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def submit_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        submission: AISubmission,
        manifest: Mapping,
        *,
        key: str,
    ) -> AITaskRecord:
        if not connection.in_transaction():
            raise ValueError("AI submission requires a transaction")
        submission = AISubmission.model_validate(submission.model_dump())
        existing = self.replay_for_owner(
            connection,
            owner_id,
            submission,
            key=key,
        )
        if existing is not None:
            return existing
        self._lock(connection, f"ai-owner-capacity:{owner_id}")
        active_count = connection.scalar(
            select(func.count())
            .select_from(ai_tasks)
            .where(
                ai_tasks.c.owner_id == owner_id,
                ai_tasks.c.status.in_(("pending", "running")),
            )
        )
        if active_count >= MAX_ACTIVE_AI_TASKS_PER_OWNER:
            raise AITaskRateLimited
        stored_manifest = _stored_object(manifest)

        now = self._now()
        row = (
            connection.execute(
                insert(ai_tasks)
                .values(
                    id=uuid4(),
                    owner_id=owner_id,
                    idempotency_key=key,
                    request_hash=submission.request_hash,
                    task_type=submission.task_type,
                    snapshot_id=submission.snapshot_id,
                    as_of=submission.as_of,
                    version=submission.version,
                    input_manifest=stored_manifest,
                    status="pending",
                    verification_status="pending",
                    human_review_status="unreviewed",
                    attempt_count=0,
                    available_at=now,
                    lease_token=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    selected_revision=None,
                    model_version=None,
                    prompt_version=None,
                    provider=None,
                    token_usage=None,
                    cost=None,
                    latency_ms=None,
                    failure=None,
                    created_at=now,
                    started_at=None,
                    completed_at=None,
                )
                .returning(ai_tasks)
            )
            .mappings()
            .one()
        )
        return self._record(connection, row)

    def replay_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        submission: AISubmission,
        *,
        key: str,
    ) -> AITaskRecord | None:
        if not connection.in_transaction():
            raise ValueError("AI submission replay requires a transaction")
        _identifier(key)
        submission = AISubmission.model_validate(submission.model_dump())
        self._lock(connection, f"ai-submit:{owner_id}:{key}")
        existing = (
            connection.execute(
                select(ai_tasks).where(
                    ai_tasks.c.owner_id == owner_id,
                    ai_tasks.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if existing["request_hash"] != submission.request_hash:
                raise Conflict("AI idempotency key belongs to another payload")
            return self._record(connection, existing)
        return None

    def _authorize(self, connection: Connection, token: str, csrf: str | None = None):
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        return self.owners.authorize_connection(connection, token, csrf)

    def get(self, token: str, task_id: UUID | str) -> AITaskRecord:
        with self.database.engine.begin() as connection:
            owner = self._authorize(connection, token)
            row = self._task_row(
                connection,
                task_id,
                owner_id=owner.owner_id,
                lock=True,
            )
            return self._record(connection, row, ensure_template=True)

    def cancel(
        self,
        token: str,
        csrf: str,
        task_id: UUID | str,
        *,
        key: str,
    ) -> AICancellationReceipt:
        _identifier(key)
        try:
            normalized_id = UUID(str(task_id))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Invalid AI task ID") from None
        payload_hash = _digest({"task_id": str(normalized_id)})
        with self.database.engine.begin() as connection:
            owner = self._authorize(connection, token, csrf)
            self._lock(connection, f"ai-cancel:{owner.owner_id}:{key}")
            command = (
                connection.execute(
                    select(ai_task_cancellations).where(
                        ai_task_cancellations.c.owner_id == owner.owner_id,
                        ai_task_cancellations.c.idempotency_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if command is not None:
                if command["payload_hash"] != payload_hash:
                    raise Conflict("Cancellation key belongs to another payload")
                self._task_row(
                    connection,
                    command["task_id"],
                    owner_id=owner.owner_id,
                )
                return AICancellationReceipt(
                    task_id=command["task_id"],
                    status="cancelled",
                    idempotency_key=command["idempotency_key"],
                    accepted_at=command["accepted_at"],
                )
            row = self._task_row(
                connection,
                normalized_id,
                owner_id=owner.owner_id,
                lock=True,
            )
            if row["status"] not in {"pending", "running", "cancelled"}:
                raise Conflict("AI task can no longer be cancelled")
            now = self._now()
            connection.execute(
                insert(ai_task_cancellations).values(
                    id=uuid4(),
                    task_id=row["id"],
                    owner_id=owner.owner_id,
                    idempotency_key=key,
                    payload_hash=payload_hash,
                    accepted_at=now,
                )
            )
            if row["status"] != "cancelled":
                row = (
                    connection.execute(
                        update(ai_tasks)
                        .where(ai_tasks.c.id == row["id"])
                        .values(
                            status="cancelled",
                            lease_token=None,
                            lease_owner=None,
                            lease_expires_at=None,
                            completed_at=now,
                        )
                        .returning(ai_tasks)
                    )
                    .mappings()
                    .one()
                )
            return AICancellationReceipt(
                task_id=row["id"],
                status="cancelled",
                idempotency_key=key,
                accepted_at=now,
            )

    def review(
        self,
        token: str,
        csrf: str,
        task_id: UUID | str,
        *,
        decision: str,
        revision: int,
        key: str,
        edited_content: Mapping | None = None,
    ) -> AIReviewReceipt:
        _identifier(key)
        if decision not in {"accept", "edit", "reject"}:
            raise ValueError("Invalid AI review decision")
        if type(revision) is not int or revision < 1:
            raise ValueError("Invalid AI draft revision")
        if (decision == "edit") != (edited_content is not None):
            raise ValueError("Edited content is required only for edit decisions")
        stored_edit = (
            _stored_object(edited_content) if edited_content is not None else None
        )
        payload = {
            "task_id": str(task_id),
            "decision": decision,
            "revision": revision,
            "edited_content": stored_edit,
        }
        payload_hash = _digest(payload)
        with self.database.engine.begin() as connection:
            owner = self._authorize(connection, token, csrf)
            self._lock(connection, f"ai-review:{owner.owner_id}:{key}")
            command = (
                connection.execute(
                    select(ai_task_reviews).where(
                        ai_task_reviews.c.owner_id == owner.owner_id,
                        ai_task_reviews.c.idempotency_key == key,
                    )
                )
                .mappings()
                .first()
            )
            if command is not None:
                if command["payload_hash"] != payload_hash:
                    raise Conflict("Review key belongs to another payload")
                self._task_row(
                    connection,
                    command["task_id"],
                    owner_id=owner.owner_id,
                )
                return AIReviewReceipt(
                    task_id=command["task_id"],
                    human_review_status=command["human_review_status"],
                    revision=command["resulting_revision"],
                    idempotency_key=command["idempotency_key"],
                    verification_status=command["verification_status"],
                    note=_review_note(
                        command["decision"],
                        command["resulting_revision"],
                    ),
                )
            row = self._task_row(
                connection,
                task_id,
                owner_id=owner.owner_id,
                lock=True,
            )
            if row["status"] == "cancelled" or row["selected_revision"] != revision:
                raise Conflict("AI draft revision changed")
            draft_id = None
            resulting_revision = revision
            now = self._now()
            human_review_status = {
                "accept": "accepted",
                "edit": "edited",
                "reject": "rejected",
            }[decision]
            verification_status = row["verification_status"]
            if decision == "edit":
                resulting_revision = self._next_revision(connection, row["id"])
                verification = verify_output(row["input_manifest"], stored_edit)
                structured = isinstance(stored_edit, Mapping) and set(stored_edit) == {
                    "facts",
                    "inferences",
                    "missing_evidence",
                    "citations",
                }
                verification_status = (
                    verification.status
                    if structured or verification.safety_failure
                    else "needs_human"
                )
                draft_id = uuid4()
                connection.execute(
                    insert(ai_task_drafts).values(
                        id=draft_id,
                        task_id=row["id"],
                        revision=resulting_revision,
                        source="human_edit",
                        content=stored_edit,
                        model_version=None,
                        prompt_version=None,
                        provider=None,
                        token_usage=None,
                        cost=None,
                        created_at=now,
                    )
                )
            connection.execute(
                insert(ai_task_reviews).values(
                    id=uuid4(),
                    task_id=row["id"],
                    owner_id=owner.owner_id,
                    idempotency_key=key,
                    payload_hash=payload_hash,
                    decision=decision,
                    expected_revision=revision,
                    resulting_revision=resulting_revision,
                    human_review_status=human_review_status,
                    verification_status=verification_status,
                    draft_id=draft_id,
                    created_at=now,
                )
            )
            values = {
                "human_review_status": human_review_status,
            }
            if decision == "edit":
                values["selected_revision"] = resulting_revision
                values["verification_status"] = verification_status
            elif decision == "accept" and row["verification_status"] == "failed":
                raise Conflict("Failed verification cannot be accepted")
            row = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(**values)
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return AIReviewReceipt(
                task_id=row["id"],
                human_review_status=human_review_status,
                revision=resulting_revision,
                idempotency_key=key,
                verification_status=verification_status,
                note=_review_note(decision, resulting_revision),
            )

    def claim(
        self,
        worker_id: str,
        *,
        lease_for: timedelta = timedelta(seconds=30),
        max_attempts: int = 3,
    ) -> AITaskLease | None:
        _identifier(worker_id)
        if not isinstance(lease_for, timedelta) or not (
            timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
        ):
            raise ValueError("Invalid AI lease duration")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("Invalid AI max attempts")
        now = self._now()
        with self.database.engine.begin() as connection:
            while True:
                row = (
                    connection.execute(
                        select(ai_tasks)
                        .where(
                            ai_tasks.c.available_at <= now,
                            or_(
                                ai_tasks.c.status == "pending",
                                and_(
                                    ai_tasks.c.status == "running",
                                    ai_tasks.c.lease_expires_at <= now,
                                ),
                            ),
                        )
                        .order_by(ai_tasks.c.available_at, ai_tasks.c.created_at)
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                if row["attempt_count"] >= max_attempts:
                    connection.execute(
                        update(ai_tasks)
                        .where(ai_tasks.c.id == row["id"])
                        .values(
                            status="failed",
                            verification_status="failed",
                            lease_token=None,
                            lease_owner=None,
                            lease_expires_at=None,
                            failure={"code": "attempts_exhausted"},
                            completed_at=now,
                        )
                    )
                    continue
                break
            token = uuid4()
            expires_at = now + lease_for
            claimed = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(
                        status="running",
                        attempt_count=row["attempt_count"] + 1,
                        lease_token=token,
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        started_at=row["started_at"] or now,
                        failure=None,
                    )
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return AITaskLease(
                job=self._record(connection, claimed),
                token=token,
                worker_id=worker_id,
                expires_at=expires_at,
            )

    def _active_lease(self, connection: Connection, lease: AITaskLease):
        row = self._task_row(connection, lease.job.id, lock=True)
        if (
            row["status"] != "running"
            or row["lease_token"] != lease.token
            or row["lease_owner"] != lease.worker_id
            or row["lease_expires_at"] <= self._now()
        ):
            raise Conflict("AI task lease is no longer active")
        return row

    def renew(
        self,
        lease: AITaskLease,
        *,
        lease_for: timedelta = timedelta(seconds=30),
    ) -> AITaskLease:
        if not isinstance(lease_for, timedelta) or not (
            timedelta(seconds=1) <= lease_for <= timedelta(hours=1)
        ):
            raise ValueError("Invalid AI lease duration")
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            expires_at = self._now() + lease_for
            renewed = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(lease_expires_at=expires_at)
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return AITaskLease(
                job=self._record(connection, renewed),
                token=lease.token,
                worker_id=lease.worker_id,
                expires_at=expires_at,
            )

    def load_input(self, lease: AITaskLease) -> Mapping:
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            if self.publications is None:
                raise Forbidden("Publication authorization is unavailable")
            snapshot = self.publications.get_ai_snapshot(
                connection,
                row["snapshot_id"],
                ai_domains=AI_INPUT_DOMAINS[row["task_type"]],
            )
            if snapshot is None:
                raise NotFound("Publication not found")
            return row["input_manifest"]

    def succeed(
        self,
        lease: AITaskLease,
        result: AIProviderResult,
        *,
        provider: str,
        model_version: str,
        prompt_version: str,
        latency_ms: int,
        max_tokens: int,
        max_cost: Decimal,
    ) -> AITaskRecord:
        result = AIProviderResult.model_validate(result.model_dump())
        for value in (provider, model_version, prompt_version):
            validate_provider_metadata(value)
        _validate_latency(latency_ms, required=True, result_limit=True)
        if type(max_tokens) is not int or max_tokens < 0:
            raise ValueError("Invalid AI token limit")
        if (
            not isinstance(max_cost, Decimal)
            or not max_cost.is_finite()
            or max_cost < 0
        ):
            raise ValueError("Invalid AI cost limit")
        if result.token_usage > max_tokens or result.cost > max_cost:
            raise AIProviderResultLimitExceeded(
                "AI provider result exceeds configured limits"
            )
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            now = self._now()
            row = self._ensure_template(connection, row, now)
            verification = verify_output(row["input_manifest"], result.content)
            revision = self._next_revision(connection, row["id"])
            connection.execute(
                insert(ai_task_drafts).values(
                    id=uuid4(),
                    task_id=row["id"],
                    revision=revision,
                    source="provider",
                    content=result.model_dump(mode="json")["content"],
                    model_version=model_version,
                    prompt_version=prompt_version,
                    provider=provider,
                    token_usage=result.token_usage,
                    cost=result.cost,
                    created_at=now,
                )
            )
            selected_revision = (
                revision
                if row["human_review_status"] == "unreviewed"
                and row["selected_revision"] is None
                else row["selected_revision"]
            )
            selected_verification = (
                verification.status
                if selected_revision == revision
                else row["verification_status"]
            )
            completed = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(
                        status="succeeded",
                        verification_status=selected_verification,
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        selected_revision=selected_revision,
                        model_version=model_version,
                        prompt_version=prompt_version,
                        provider=provider,
                        token_usage=result.token_usage,
                        cost=result.cost,
                        latency_ms=latency_ms,
                        failure=None,
                        completed_at=now,
                    )
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return self._record(connection, completed)

    def fail(
        self,
        lease: AITaskLease,
        *,
        code: str,
        latency_ms: int | None = None,
    ) -> AITaskRecord:
        if code not in {
            "provider_failed",
            "provider_timeout",
            "provider_unavailable",
            "input_forbidden",
            "input_unavailable",
            "result_limit_exceeded",
        }:
            raise ValueError("Invalid sanitized AI failure code")
        _validate_latency(latency_ms)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            completed = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(
                        status="failed",
                        verification_status="failed",
                        lease_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        latency_ms=latency_ms,
                        failure={"code": code},
                        completed_at=self._now(),
                    )
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return self._record(connection, completed)

    def retry_or_fail(
        self,
        lease: AITaskLease,
        *,
        code: str,
        max_attempts: int,
        latency_ms: int,
    ) -> AITaskRecord:
        if code not in {"provider_timeout", "provider_failed"}:
            raise ValueError("Invalid retry failure code")
        _validate_latency(latency_ms, required=True)
        with self.database.engine.begin() as connection:
            row = self._active_lease(connection, lease)
            terminal = row["attempt_count"] >= max_attempts
            values = {
                "status": "failed" if terminal else "pending",
                "verification_status": "failed" if terminal else "pending",
                "lease_token": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "available_at": self._now(),
                "latency_ms": latency_ms,
                "failure": {"code": code},
                "completed_at": self._now() if terminal else None,
            }
            updated = (
                connection.execute(
                    update(ai_tasks)
                    .where(ai_tasks.c.id == row["id"])
                    .values(**values)
                    .returning(ai_tasks)
                )
                .mappings()
                .one()
            )
            return self._record(connection, updated)


__all__ = [
    "MAX_ACTIVE_AI_TASKS_PER_OWNER",
    "AIProviderResultLimitExceeded",
    "AITaskRateLimited",
    "AITaskRepository",
]
