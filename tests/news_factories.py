"""Synthetic, offline news fixtures. No provider content."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from factories import NOW, grant

ARTICLE_A = UUID("00000000-0000-4000-8000-000000000141")
ARTICLE_B = UUID("00000000-0000-4000-8000-000000000142")
REVISION_A1 = UUID("00000000-0000-4000-8000-000000000151")
REVISION_A2 = UUID("00000000-0000-4000-8000-000000000152")
REVISION_B1 = UUID("00000000-0000-4000-8000-000000000153")
ASSOCIATION_SECURITY = UUID("00000000-0000-4000-8000-000000000161")
ASSOCIATION_TOPIC = UUID("00000000-0000-4000-8000-000000000162")


def article_revision(**changes):
    from dragon_radar.news.models import NewsArticleRevision

    content = changes.pop("content", "Synthetic licensed article body.")
    digest_source = changes.pop("_digest_source", content or "Synthetic source body.")
    values = {
        "id": REVISION_A1,
        "article_id": ARTICLE_A,
        "revision": 1,
        "source_id": "synthetic",
        "source_version": "news-v1",
        "source_article_id": "source-article-a",
        "news_type": "company",
        "title": "Synthetic company announcement",
        "url": "https://example.test/news/a",
        "published_at": NOW - timedelta(hours=4),
        "received_at": NOW - timedelta(hours=2),
        "available_at": NOW - timedelta(hours=1),
        "content_scope": "full",
        "summary": "Synthetic licensed summary.",
        "content": content,
        "content_digest": sha256(digest_source.encode()).hexdigest(),
        "grant": grant(),
    }
    values.update(changes)
    return NewsArticleRevision(**values)


def event_evidence(**changes):
    from dragon_radar.news.models import NewsEventEvidence

    kind = changes.get("kind", "source_event_id")
    defaults = {
        "source_event_id": "synthetic:event:1",
        "canonical_url": "https://example.test/events/1",
        "content_digest": "a" * 64,
        "manual_confirmation": "review:synthetic-event-1",
    }
    values = {
        "kind": kind,
        "value": defaults[kind],
    }
    values.update(changes)
    return NewsEventEvidence(**values)


def association(**changes):
    from dragon_radar.news.models import NewsAssociation

    values = {
        "id": ASSOCIATION_SECURITY,
        "article_revision_id": REVISION_A1,
        "target_type": "security",
        "target_id": "A",
        "method": "direct",
        "status": "accepted",
        "evidence_snippets": ("Synthetic issuer identifier A.",),
        "created_at": NOW - timedelta(minutes=30),
    }
    values.update(changes)
    return NewsAssociation(**values)


def late_revision(**changes):
    return article_revision(
        id=REVISION_A2,
        revision=2,
        source_version="news-v2",
        title="Synthetic company announcement corrected",
        received_at=NOW + timedelta(hours=1),
        available_at=NOW + timedelta(hours=1),
        **changes,
    )


def second_article(**changes):
    return article_revision(
        id=REVISION_B1,
        article_id=ARTICLE_B,
        source_article_id="source-article-b",
        title="Synthetic company announcement",
        url="https://example.test/news/b",
        content="A second licensed report about the same event.",
        content_digest=sha256(
            b"A second licensed report about the same event."
        ).hexdigest(),
        **changes,
    )


def aware(hour: int) -> datetime:
    return datetime(2026, 9, 9, hour, tzinfo=UTC)
