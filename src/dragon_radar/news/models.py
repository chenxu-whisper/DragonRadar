"""Strict immutable contracts for point-in-time news."""

import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from dragon_radar.domain.models import (
    FrozenModel,
    Identifier,
    SourceGrant,
    UTCDateTime,
)

NewsType = Literal["news", "announcement", "research"]
ContentScope = Literal["none", "summary", "full"]
AssociationMethod = Literal["direct", "manual", "ai"]
AssociationStatus = Literal["accepted", "unreviewed", "rejected"]
ReviewDecision = Literal["accept", "reject"]
EventEvidenceKind = Literal[
    "source_event_id",
    "canonical_url",
    "content_digest",
    "manual_confirmation",
]
SHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("News URL must be an absolute HTTPS URL")
    return value


class NewsArticleRevision(FrozenModel):
    id: UUID
    article_id: UUID
    revision: Annotated[int, Field(ge=1)]
    source_id: Identifier
    source_version: Identifier
    source_article_id: Identifier
    news_type: NewsType
    title: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    url: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    published_at: UTCDateTime
    received_at: UTCDateTime
    available_at: UTCDateTime
    content_scope: ContentScope
    summary: Annotated[StrictStr, Field(min_length=1)] | None = None
    content: Annotated[StrictStr, Field(min_length=1)] | None = None
    content_digest: SHA256
    grant: SourceGrant

    _validated_url = field_validator("url")(_safe_url)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("News title cannot be blank")
        return value

    @model_validator(mode="after")
    def valid_availability_and_scope(self):
        if self.available_at < self.received_at:
            raise ValueError("available_at cannot precede received_at")
        if self.content_scope == "none" and (
            self.summary is not None or self.content is not None
        ):
            raise ValueError("Unlicensed text cannot be retained for display")
        if self.content_scope == "summary" and (
            self.summary is None or self.content is not None
        ):
            raise ValueError("Summary scope requires only a summary")
        if self.content_scope == "full" and self.content is None:
            raise ValueError("Full scope requires content")
        return self


class NewsEventEvidence(FrozenModel):
    kind: EventEvidenceKind
    value: Annotated[StrictStr, Field(min_length=1, max_length=4000)]

    @model_validator(mode="after")
    def validate_kind_value(self):
        if self.kind == "content_digest" and not re.fullmatch(
            r"[0-9a-f]{64}", self.value
        ):
            raise ValueError("Content event evidence must be a SHA-256 digest")
        if self.kind == "canonical_url":
            _safe_url(self.value)
        return self


class NewsAssociation(FrozenModel):
    id: UUID
    article_revision_id: UUID
    target_type: Literal["security", "topic"]
    target_id: Identifier
    method: AssociationMethod
    status: AssociationStatus
    evidence_snippets: Annotated[tuple[StrictStr, ...], Field(min_length=1)]
    created_at: UTCDateTime

    @field_validator("evidence_snippets")
    @classmethod
    def nonblank_evidence(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Association evidence cannot be blank")
        return values


class NewsSnapshotItem(FrozenModel):
    article_id: UUID
    article_revision_id: UUID
    revision: Annotated[int, Field(ge=1)]
    event_id: UUID
    position: Annotated[int, Field(ge=0)]


class NewsSnapshot(FrozenModel):
    id: UUID
    as_of: UTCDateTime
    created_at: UTCDateTime
    items: tuple[NewsSnapshotItem, ...]


class NewsReviewOutcome(FrozenModel):
    association_id: UUID
    decision: ReviewDecision
    revision: Annotated[int, Field(ge=1)]
    effective: bool
    pending_topic_publication: bool
    reviewed_at: datetime
