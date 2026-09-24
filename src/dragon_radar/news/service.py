"""News ingestion, immutable snapshot creation, and publication projection."""

from collections.abc import Callable
from datetime import datetime

from dragon_radar.news.models import (
    NewsArticleRevision,
    NewsAssociation,
    NewsEventEvidence,
)
from dragon_radar.news.repository import NewsRepository
from dragon_radar.publication.models import DomainMaterialization
from dragon_radar.storage.repository import Repository

NEWS_SCHEMA = "dragon-radar.news"
NEWS_SCHEMA_VERSION = 1
NEWS_RULE_VERSION = "news-v1"


class NewsService:
    def __init__(
        self,
        database,
        market_repository: Repository | None = None,
        *,
        owner_repository=None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = NewsRepository(
            database,
            market_repository,
            owner_repository=owner_repository,
            clock=clock,
        )

    def ingest_article(
        self,
        revision: NewsArticleRevision,
        *,
        key: str,
        event_evidence: NewsEventEvidence | None = None,
        associations: tuple[NewsAssociation, ...] = (),
    ) -> NewsArticleRevision:
        return self.repository.ingest_revision(
            revision,
            key=key,
            event_evidence=event_evidence,
            associations=associations,
        )

    def create_snapshot(self, *, as_of: datetime, key: str):
        return self.repository.create_snapshot(as_of=as_of, key=key)

    def review_for_owner(self, **values):
        return self.repository.review_for_owner(**values)

    @staticmethod
    def materialize_context(context: dict) -> DomainMaterialization:
        associations_by_revision: dict = {}
        reviews = context["reviews"]
        for association in context["associations"]:
            review = reviews.get(association["id"])
            status = (
                review["decision"] + "ed"
                if review is not None and review["decision"] == "reject"
                else "accepted"
                if review is not None
                else association["status"]
            )
            accepted_by_review = review is not None and review["decision"] == "accept"
            target_type = association["target_type"]
            projected = {
                "association_id": str(association["id"]),
                f"{target_type}_id": association["target_id"],
                "method": association["method"],
                "status": status,
                "evidence_snippets": list(association["evidence_snippets"]),
                "effective": (
                    association["status"] == "accepted" and not accepted_by_review
                ),
                "pending_topic_publication": (
                    accepted_by_review and target_type == "topic"
                ),
            }
            associations_by_revision.setdefault(
                association["article_revision_id"], []
            ).append(projected)

        articles = []
        for row in context["rows"]:
            associations = associations_by_revision.get(row["article_revision_id"], [])
            securities = sorted(
                (item for item in associations if "security_id" in item),
                key=lambda item: (item["security_id"], item["association_id"]),
            )
            topics = sorted(
                (item for item in associations if "topic_id" in item),
                key=lambda item: (item["topic_id"], item["association_id"]),
            )
            articles.append(
                {
                    "article_id": str(row["article_id"]),
                    "revision_id": str(row["article_revision_id"]),
                    "revision": row["revision"],
                    "event_id": str(row["event_id"]),
                    "type": row["news_type"],
                    "source": {
                        "id": row["source_id"],
                        "version": row["source_version"],
                    },
                    "title": row["title"],
                    "url": row["url"],
                    "published_at": row["published_at"].isoformat(),
                    "received_at": row["received_at"].isoformat(),
                    "available_at": row["available_at"].isoformat(),
                    "content_scope": row["content_scope"],
                    "summary": row["summary"],
                    "content": row["content"],
                    "securities": securities,
                    "topics": topics,
                }
            )
        event_count = len({article["event_id"] for article in articles})
        expected = len(articles)
        return DomainMaterialization(
            payload={
                "schema": NEWS_SCHEMA,
                "schema_version": NEWS_SCHEMA_VERSION,
                "rule_version": NEWS_RULE_VERSION,
                "as_of": context["root"]["as_of"].isoformat(),
                "articles": articles,
                "sources": sorted({article["source"]["id"] for article in articles}),
                "event_count": event_count,
            },
            coverage={
                "expected": expected,
                "valid": expected,
                "legal_excluded": 0,
                "missing": 0,
            },
        )

    @staticmethod
    def dependencies(context: dict) -> tuple[tuple[str, str, object, str], ...]:
        return tuple(
            (
                row["source_id"],
                "news_article_revision",
                row["article_revision_id"],
                "news",
            )
            for row in context["rows"]
        )
