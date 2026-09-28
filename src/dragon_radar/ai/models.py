"""Strict immutable contracts for asynchronous AI tasks."""

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    StrictStr,
    field_validator,
)

from dragon_radar.domain.models import FrozenModel, UTCDateTime
from dragon_radar.topics.models import ImmutableJsonObject

TaskType = Literal["summarize", "topic_suggest", "explain_stock", "daily_review"]
TaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
VerificationStatus = Literal["pending", "passed", "failed", "needs_human"]
HumanReviewStatus = Literal["unreviewed", "accepted", "edited", "rejected"]
DraftSource = Literal["deterministic_template", "provider", "human_edit"]
SafeVersion = Annotated[
    StrictStr,
    Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]

MAX_PROVIDER_CONTENT_BYTES = 32768
MAX_NOTES_CONTEXT_CHARS = 20000
AI_INPUT_DOMAINS = {
    "summarize": frozenset({"news"}),
    "topic_suggest": frozenset({"news"}),
    "explain_stock": frozenset({"market"}),
    "daily_review": frozenset({"market"}),
}


def _plain_json(value):
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _bounded_json(value: JsonValue, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("JSON nesting is too deep")
    if isinstance(value, str) and len(value) > MAX_PROVIDER_CONTENT_BYTES:
        raise ValueError("JSON string is too long")
    if isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("JSON array is too long")
        for item in value:
            _bounded_json(item, depth=depth + 1)
    elif isinstance(value, dict):
        if len(value) > 1000:
            raise ValueError("JSON object is too large")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 200:
                raise ValueError("JSON key is invalid")
            _bounded_json(item, depth=depth + 1)


def canonical_object(value: dict) -> str:
    value = _plain_json(value)
    _bounded_json(value)
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_PROVIDER_CONTENT_BYTES:
        raise ValueError("JSON object exceeds the size limit")
    return encoded


class _StrictFrozenModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class AISubmission(_StrictFrozenModel):
    task_type: TaskType
    snapshot_id: UUID
    as_of: UTCDateTime
    version: SafeVersion
    task_input: ImmutableJsonObject

    @field_validator("task_input")
    @classmethod
    def validate_task_input(cls, value):
        stored = dict(value)
        canonical_object(stored)
        notes = stored.get("notes_context")
        if notes is not None and (
            not isinstance(notes, str) or len(notes) > MAX_NOTES_CONTEXT_CHARS
        ):
            raise ValueError("notes_context is invalid")
        return value

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def request_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("ascii")).hexdigest()


class AIProviderResult(_StrictFrozenModel):
    content: ImmutableJsonObject
    token_usage: Annotated[int, Field(ge=0, le=10_000_000)]
    cost: Annotated[Decimal, Field(ge=0, le=1_000_000)]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        canonical_object(dict(value))
        return value

    @field_validator("cost", mode="before")
    @classmethod
    def exact_cost(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("cost must be an exact decimal")
        return Decimal(value)


class AIDraft(_StrictFrozenModel):
    revision: Annotated[int, Field(ge=1)]
    source: DraftSource
    content: ImmutableJsonObject
    ai_generated: bool
    created_at: datetime


class AITaskRecord(_StrictFrozenModel):
    id: UUID
    owner_id: UUID
    task_type: TaskType
    snapshot_id: UUID
    as_of: datetime
    version: SafeVersion
    input_manifest: ImmutableJsonObject
    status: TaskStatus
    verification_status: VerificationStatus
    human_review_status: HumanReviewStatus
    attempt_count: Annotated[int, Field(ge=0)]
    content_draft: AIDraft | None = None
    processing_overdue: bool = False
    model_version: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    token_usage: int | None = None
    cost: Decimal | None = None
    failure: ImmutableJsonObject | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AITaskLease(_StrictFrozenModel):
    job: AITaskRecord
    token: UUID
    worker_id: SafeVersion
    expires_at: datetime


__all__ = [
    "AIDraft",
    "AIProviderResult",
    "AI_INPUT_DOMAINS",
    "AISubmission",
    "AITaskLease",
    "AITaskRecord",
    "DraftSource",
    "HumanReviewStatus",
    "MAX_NOTES_CONTEXT_CHARS",
    "MAX_PROVIDER_CONTENT_BYTES",
    "TaskStatus",
    "TaskType",
    "VerificationStatus",
    "canonical_object",
]
