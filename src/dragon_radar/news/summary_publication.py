"""Exact promoted summary artifacts and explicitly selected news v3 archival."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from dragon_radar.ai.models import SafeVersion
from dragon_radar.public_daily_ai.models import (
    SHA256,
    Closed,
    Positive,
    Reference,
    digest,
)
from dragon_radar.storage.repository import Conflict


class NewsSummaryArtifact(Closed):
    schema_: Literal["dragon-radar.news-summary/v1"] = Field(alias="schema")
    summary_id: UUID
    summary_revision: Positive
    article_id: UUID
    article_revision_id: UUID
    article_revision: Positive
    source_id: Reference
    source_version: SafeVersion
    text: str = Field(strict=True, min_length=1)
    provenance: Literal["ai", "ai_human_edited"]
    task_type: Literal["summarize"]
    task_snapshot_id: UUID
    draft_revision: Positive
    verification_status: Literal["passed"]
    human_review_status: Literal["accepted"]
    input_sha256: SHA256
    output_sha256: SHA256
    model_version: SafeVersion
    prompt_version: SafeVersion
    retrieval_version: SafeVersion
    verified_at: datetime
    reviewed_at: datetime
    available_at: datetime

    @model_validator(mode="after")
    def validate_times_and_text(self):
        if not self.text.strip() or len(self.text.encode("utf-8")) > 32768:
            raise ValueError("Summary text invalid")
        if any(
            t.tzinfo is None or t.utcoffset() is None
            for t in (
                self.verified_at,
                self.reviewed_at,
                self.available_at,
            )
        ):
            raise ValueError("Summary timestamps require timezones")
        if max(self.verified_at, self.reviewed_at) > self.available_at:
            raise ValueError("Summary unavailable before verification/review")
        return self

    def bind(self, article):
        if (
            str(self.article_id) != article["article_id"]
            or str(self.article_revision_id) != article["revision_id"]
            or self.article_revision != article["revision"]
            or (self.source_id, self.source_version)
            != (
                article["source"]["id"],
                article["source"]["version"],
            )
        ):
            raise Conflict("Summary article binding mismatch")


class SummarySelection(Closed):
    summary_id: UUID
    summary_revision: Positive
    article_id: UUID
    article_revision_id: UUID
    article_revision: Positive


def selection_identity(values):
    selections = [
        SummarySelection.model_validate(value).model_dump(mode="json")
        for value in values
    ]
    selections.sort(
        key=lambda item: (
            item["article_id"],
            item["article_revision_id"],
            item["summary_id"],
        )
    )
    if len({item["article_id"] for item in selections}) != len(selections):
        raise ValueError("Duplicate article summary selection")
    return digest({"summary_selections": selections})


def summary_preview(artifact=None, *, reason="summary_not_archived"):
    if artifact is None:
        return {
            "status": "unavailable",
            "text": None,
            "truncated": False,
            "reason": reason,
            "summary_id": None,
            "summary_revision": None,
            "provenance": None,
        }
    text = artifact.text[:100]
    return {
        "status": "ready",
        "text": text,
        "truncated": len(artifact.text) > 100,
        "reason": None,
        "summary_id": str(artifact.summary_id),
        "summary_revision": artifact.summary_revision,
        "provenance": artifact.provenance,
    }


class PromotedSummaryMaterializer:
    """Delegate news rules, then attach only explicitly selected registry records."""

    def __init__(self, news_service, registry, selections=()):
        self.news_service = news_service
        self.registry = registry
        self.selections = tuple(
            SummarySelection.model_validate(value) for value in selections
        )
        self.selection_hash = selection_identity(
            [item.model_dump(mode="json") for item in self.selections]
        )

    def source_ids(self, connection):
        return self.registry.summary_source_ids(connection, self.selections)

    def materialize_context(self, context, *, connection, topic_context=None):
        materialization = self.news_service.materialize_context(
            context, topic_context=topic_context
        )
        result = materialization.model_dump(mode="json")["payload"]
        # Caller owns this transaction; no arbitrary-task access or implicit promotion.
        selected = {str(item.article_id): item for item in self.selections}
        matched = set()
        for article in result["articles"]:
            article["summary_artifact"] = None
            choice = selected.get(article["article_id"])
            if choice is None:
                continue
            if (
                str(choice.article_revision_id) != article["revision_id"]
                or choice.article_revision != article["revision"]
            ):
                raise Conflict("Selected article revision mismatch")
            article["summary_artifact"] = self.registry.summary(
                connection,
                choice.summary_id,
                choice.summary_revision,
                article,
            )
            matched.add(article["article_id"])
        if matched != set(selected):
            raise Conflict("Selected article absent from news input")
        result["schema_version"] = 3
        from dragon_radar.publication.models import DomainMaterialization

        return DomainMaterialization(
            payload=result,
            coverage=materialization.coverage,
            quality_flags=materialization.quality_flags,
        )

    def dependencies(self, context, *, topic_context=None):
        dependencies = list(self.news_service.dependencies(context))
        for selection in self.selections:
            # Original article dependency plus summary eligibility dependency.
            articles = context["rows"]
            articles = [
                item
                for item in articles
                if str(item["article_id"]) == str(selection.article_id)
            ]
            if len(articles) != 1:
                raise Conflict("Selected article absent or duplicated in news input")
            article = articles[0]
            if (
                str(article["article_revision_id"])
                != str(selection.article_revision_id)
                or article["revision"] != selection.article_revision
            ):
                raise Conflict("Selected article revision mismatch")
            source = article["source_id"]
            dependencies.append(
                (source, "promoted_news_summary", selection.summary_id, "news")
            )
        return tuple(dependencies)
