"""Transactional persistence for immutable news and owner review history."""

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Connection

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import grant_allows_display, grant_allows_storage
from dragon_radar.news.models import (
    NewsArticleRevision,
    NewsAssociation,
    NewsEventEvidence,
    NewsReviewOutcome,
    NewsSnapshot,
    NewsSnapshotItem,
)
from dragon_radar.news.tables import (
    news_article_revisions,
    news_articles,
    news_association_reviews,
    news_associations,
    news_event_articles,
    news_events,
    news_review_audit_events,
    news_review_commands,
    news_snapshot_items,
    news_snapshots,
)
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    Repository,
    _identifier,
    _uuid,
)


def _canonical(value: Mapping) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    )


def _revision(row: Mapping) -> NewsArticleRevision:
    return NewsArticleRevision(
        id=row["id"],
        article_id=row["article_id"],
        revision=row["revision"],
        source_id=row["source_id"],
        source_version=row["source_version"],
        source_article_id=row["source_article_id"],
        news_type=row["news_type"],
        title=row["title"],
        url=row["url"],
        published_at=row["published_at"],
        received_at=row["received_at"],
        available_at=row["available_at"],
        content_scope=row["content_scope"],
        summary=row["summary"],
        content=row["content"],
        content_digest=row["content_digest"],
        grant=SourceGrant.model_validate(row["embedded_grant"]),
    )


def _snapshot(root: Mapping, items: Iterable[Mapping]) -> NewsSnapshot:
    return NewsSnapshot(
        id=root["id"],
        as_of=root["as_of"],
        created_at=root["created_at"],
        items=tuple(
            NewsSnapshotItem(
                article_id=item["article_id"],
                article_revision_id=item["article_revision_id"],
                revision=item["revision"],
                event_id=item["event_id"],
                position=item["position"],
            )
            for item in items
        ),
    )


class NewsRepository:
    def __init__(
        self,
        database,
        market_repository: Repository | None = None,
        *,
        owner_repository=None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.market = market_repository or Repository(database, clock=clock)
        self.owners = owner_repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("News clock must be timezone-aware")
        return value.astimezone(UTC)

    def _authorize_grant(
        self,
        connection: Connection,
        source_id: str,
        embedded: SourceGrant,
        *,
        storage: bool,
    ) -> SourceGrant:
        current = self.market._source(connection, source_id)
        allowed = grant_allows_storage if storage else grant_allows_display
        if not all(
            allowed(grant, source_id=source_id, now=self._now())
            for grant in (current, embedded)
        ):
            raise Forbidden("Source is not locally authorized")
        return current

    @staticmethod
    def _stored_revision(revision: NewsArticleRevision, ingested_at: datetime) -> dict:
        values = revision.model_dump(mode="json")
        values["embedded_grant"] = values.pop("grant")
        values["ingested_at"] = ingested_at
        return values

    @staticmethod
    def _same_revision(row: Mapping, revision: NewsArticleRevision) -> bool:
        return _canonical(_revision(row).model_dump(mode="json")) == _canonical(
            revision.model_dump(mode="json")
        )

    def ingest_revision(
        self,
        revision: NewsArticleRevision,
        *,
        key: str,
        event_evidence: NewsEventEvidence | None = None,
        associations: tuple[NewsAssociation, ...] = (),
    ) -> NewsArticleRevision:
        _identifier(key)
        revision = NewsArticleRevision.model_validate(revision.model_dump())
        event_evidence = (
            NewsEventEvidence.model_validate(event_evidence.model_dump())
            if event_evidence is not None
            else None
        )
        associations = tuple(
            NewsAssociation.model_validate(item.model_dump()) for item in associations
        )
        if any(item.article_revision_id != revision.id for item in associations):
            raise ValueError("Association must bind the exact article revision")
        now = self._now()
        with self.database.engine.begin() as connection:
            self._authorize_grant(
                connection,
                revision.source_id,
                revision.grant,
                storage=True,
            )
            self.market._lock(connection, f"news-article:{revision.article_id}")
            article = (
                connection.execute(
                    select(news_articles).where(
                        news_articles.c.id == revision.article_id
                    )
                )
                .mappings()
                .first()
            )
            if article is None:
                connection.execute(
                    insert(news_articles).values(
                        id=revision.article_id,
                        source_id=revision.source_id,
                        source_article_id=revision.source_article_id,
                        created_at=now,
                    )
                )
            elif (
                article["source_id"] != revision.source_id
                or article["source_article_id"] != revision.source_article_id
            ):
                raise Conflict("Article identity belongs to another source record")

            existing = (
                connection.execute(
                    select(news_article_revisions).where(
                        news_article_revisions.c.id == revision.id
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if not self._same_revision(existing, revision):
                    raise Conflict("Revision ID belongs to another payload")
                return _revision(existing)
            duplicate = (
                connection.execute(
                    select(news_article_revisions).where(
                        news_article_revisions.c.article_id == revision.article_id,
                        news_article_revisions.c.revision == revision.revision,
                    )
                )
                .mappings()
                .first()
            )
            if duplicate is not None:
                if not self._same_revision(duplicate, revision):
                    raise Conflict("Article revision already exists")
                return _revision(duplicate)
            connection.execute(
                insert(news_article_revisions).values(
                    **self._stored_revision(revision, now)
                )
            )

            kind = event_evidence.kind if event_evidence else "isolated_article"
            value = (
                f"{revision.source_id}:{event_evidence.value}"
                if event_evidence is not None
                and event_evidence.kind == "source_event_id"
                else event_evidence.value
                if event_evidence is not None
                else str(revision.article_id)
            )
            event = (
                connection.execute(
                    select(news_events).where(
                        news_events.c.evidence_kind == kind,
                        news_events.c.evidence_value == value,
                    )
                )
                .mappings()
                .first()
            )
            event_id = event["id"] if event is not None else uuid4()
            if event is None:
                connection.execute(
                    insert(news_events).values(
                        id=event_id,
                        evidence_kind=kind,
                        evidence_value=value,
                        created_at=now,
                    )
                )
            connection.execute(
                insert(news_event_articles).values(
                    event_id=event_id,
                    article_revision_id=revision.id,
                    created_at=now,
                )
            )
            for candidate in associations:
                connection.execute(
                    insert(news_associations).values(
                        **candidate.model_dump(mode="json")
                    )
                )
        return revision

    def create_snapshot(self, *, as_of: datetime, key: str) -> NewsSnapshot:
        _identifier(key)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("News snapshot cutoff must be timezone-aware")
        as_of = as_of.astimezone(UTC)
        command_hash = hashlib.sha256(as_of.isoformat().encode("ascii")).hexdigest()
        with self.database.engine.begin() as connection:
            self.market._lock(connection, "news-snapshot-key:" + key)
            existing = (
                connection.execute(
                    select(news_snapshots).where(
                        news_snapshots.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["command_hash"] != command_hash:
                    raise Conflict("News snapshot key belongs to another cutoff")
                items = (
                    connection.execute(
                        select(news_snapshot_items)
                        .where(news_snapshot_items.c.snapshot_id == existing["id"])
                        .order_by(news_snapshot_items.c.position)
                    )
                    .mappings()
                    .all()
                )
                return _snapshot(existing, items)

            rows = (
                connection.execute(
                    select(news_article_revisions)
                    .where(news_article_revisions.c.available_at <= as_of)
                    .order_by(
                        news_article_revisions.c.article_id,
                        news_article_revisions.c.revision.desc(),
                        news_article_revisions.c.id,
                    )
                )
                .mappings()
                .all()
            )
            selected: dict[UUID, Mapping] = {}
            for row in rows:
                selected.setdefault(row["article_id"], row)
            source_ids = sorted({row["source_id"] for row in selected.values()})
            for source_id in source_ids:
                for row in selected.values():
                    if row["source_id"] == source_id:
                        self._authorize_grant(
                            connection,
                            source_id,
                            SourceGrant.model_validate(row["embedded_grant"]),
                            storage=False,
                        )
            ordered = sorted(
                selected.values(),
                key=lambda row: (
                    -row["available_at"].timestamp(),
                    str(row["article_id"]),
                ),
            )
            event_by_revision = {
                row["article_revision_id"]: row["event_id"]
                for row in connection.execute(
                    select(news_event_articles).where(
                        news_event_articles.c.article_revision_id.in_(
                            [row["id"] for row in ordered]
                        )
                    )
                )
                .mappings()
                .all()
            }
            if len(event_by_revision) != len(ordered):
                raise Forbidden("News event lineage is incomplete")
            now = self._now()
            root = {
                "id": uuid4(),
                "idempotency_key": key,
                "command_hash": command_hash,
                "as_of": as_of,
                "created_at": now,
            }
            connection.execute(insert(news_snapshots).values(**root))
            item_rows = []
            for position, row in enumerate(ordered):
                item = {
                    "snapshot_id": root["id"],
                    "article_id": row["article_id"],
                    "article_revision_id": row["id"],
                    "revision": row["revision"],
                    "event_id": event_by_revision[row["id"]],
                    "position": position,
                }
                connection.execute(insert(news_snapshot_items).values(**item))
                item_rows.append(item)
            return _snapshot(root, item_rows)

    def snapshot_context(
        self,
        connection: Connection,
        snapshot_id: UUID | str,
        *,
        authorize: bool = True,
    ) -> dict:
        snapshot_id = _uuid(snapshot_id)
        root = (
            connection.execute(
                select(news_snapshots).where(news_snapshots.c.id == snapshot_id)
            )
            .mappings()
            .first()
        )
        if root is None:
            raise NotFound("News snapshot not found")
        rows = (
            connection.execute(
                select(
                    news_snapshot_items,
                    *(
                        column
                        for column in news_article_revisions.c
                        if column.name
                        not in {
                            "id",
                            "article_id",
                            "revision",
                        }
                    ),
                )
                .join(
                    news_article_revisions,
                    news_article_revisions.c.id
                    == news_snapshot_items.c.article_revision_id,
                )
                .where(news_snapshot_items.c.snapshot_id == snapshot_id)
                .order_by(news_snapshot_items.c.position)
            )
            .mappings()
            .all()
        )
        if authorize:
            for source_id in sorted({row["source_id"] for row in rows}):
                row = next(item for item in rows if item["source_id"] == source_id)
                self._authorize_grant(
                    connection,
                    source_id,
                    SourceGrant.model_validate(row["embedded_grant"]),
                    storage=False,
                )
        revision_ids = [row["article_revision_id"] for row in rows]
        association_rows = (
            connection.execute(
                select(news_associations)
                .where(
                    news_associations.c.article_revision_id.in_(revision_ids),
                    news_associations.c.created_at <= root["as_of"],
                )
                .order_by(news_associations.c.id)
            )
            .mappings()
            .all()
            if revision_ids
            else []
        )
        association_ids = [row["id"] for row in association_rows]
        review_rows = (
            connection.execute(
                select(news_association_reviews)
                .where(
                    news_association_reviews.c.association_id.in_(association_ids),
                    news_association_reviews.c.reviewed_at <= root["as_of"],
                )
                .order_by(
                    news_association_reviews.c.association_id,
                    news_association_reviews.c.revision.desc(),
                )
            )
            .mappings()
            .all()
            if association_ids
            else []
        )
        latest_reviews = {}
        for review in review_rows:
            latest_reviews.setdefault(review["association_id"], review)
        return {
            "snapshot": _snapshot(root, rows),
            "root": root,
            "rows": rows,
            "associations": association_rows,
            "reviews": latest_reviews,
        }

    def get_snapshot(self, snapshot_id: UUID | str) -> NewsSnapshot:
        with self.database.engine.begin() as connection:
            return self.snapshot_context(connection, snapshot_id)["snapshot"]

    def _review_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        *,
        article_id: UUID,
        association_id: UUID,
        decision: str,
        expected_revision: int,
        key: str,
    ) -> NewsReviewOutcome:
        _identifier(key)
        if decision not in {"accept", "reject"}:
            raise ValueError("Invalid review decision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid expected revision")
        association_row = (
            connection.execute(
                select(
                    news_associations,
                    news_article_revisions.c.article_id,
                    news_article_revisions.c.source_id,
                    news_article_revisions.c.embedded_grant,
                )
                .join(
                    news_article_revisions,
                    news_article_revisions.c.id
                    == news_associations.c.article_revision_id,
                )
                .where(news_associations.c.id == association_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if association_row is None or association_row["article_id"] != article_id:
            raise NotFound("News association not found")
        self._authorize_grant(
            connection,
            association_row["source_id"],
            SourceGrant.model_validate(association_row["embedded_grant"]),
            storage=False,
        )
        payload = {
            "article_id": str(article_id),
            "association_id": str(association_id),
            "decision": decision,
            "expected_revision": expected_revision,
        }
        payload_hash = hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()
        self.market._lock(connection, f"news-review:{owner_id}:{key}")
        command = (
            connection.execute(
                select(news_review_commands).where(
                    news_review_commands.c.owner_id == owner_id,
                    news_review_commands.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        if command is not None:
            if command["payload_hash"] != payload_hash:
                raise Conflict("Review key belongs to another payload")
            review = (
                connection.execute(
                    select(news_association_reviews).where(
                        news_association_reviews.c.id == command["review_id"]
                    )
                )
                .mappings()
                .one()
            )
            audit_id = connection.execute(
                select(news_review_audit_events.c.id).where(
                    news_review_audit_events.c.owner_id == owner_id,
                    news_review_audit_events.c.action == "news_association_reviewed",
                    news_review_audit_events.c.object_id == str(association_id),
                    news_review_audit_events.c.details["revision"].as_integer()
                    == review["revision"],
                )
            ).scalar_one()
            return self._review_outcome(
                review,
                association_row["target_type"],
                audit_id,
            )

        current_revision = connection.scalar(
            select(
                func.coalesce(func.max(news_association_reviews.c.revision), 0)
            ).where(news_association_reviews.c.association_id == association_id)
        )
        if current_revision != expected_revision:
            raise Conflict("Association revision changed")
        now = self._now()
        review = (
            connection.execute(
                insert(news_association_reviews)
                .values(
                    id=uuid4(),
                    association_id=association_id,
                    revision=current_revision + 1,
                    expected_revision=expected_revision,
                    owner_id=owner_id,
                    decision=decision,
                    reviewed_at=now,
                )
                .returning(news_association_reviews)
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(news_review_commands).values(
                id=uuid4(),
                owner_id=owner_id,
                idempotency_key=key,
                payload_hash=payload_hash,
                review_id=review["id"],
                created_at=now,
            )
        )
        accepted_topic = (
            review["decision"] == "accept" and association_row["target_type"] == "topic"
        )
        audit_id = connection.execute(
            insert(news_review_audit_events)
            .values(
                id=uuid4(),
                owner_id=owner_id,
                action="news_association_reviewed",
                object_id=str(association_id),
                details={
                    "decision": decision,
                    "revision": review["revision"],
                    "effective": False,
                    "pending_topic_publication": accepted_topic,
                },
                occurred_at=now,
            )
            .returning(news_review_audit_events.c.id)
        ).scalar_one()
        return self._review_outcome(
            review,
            association_row["target_type"],
            audit_id,
        )

    @staticmethod
    def _review_outcome(
        review: Mapping,
        target_type: str,
        audit_id: UUID,
    ) -> NewsReviewOutcome:
        accepted_topic = review["decision"] == "accept" and target_type == "topic"
        return NewsReviewOutcome(
            association_id=review["association_id"],
            decision=review["decision"],
            revision=review["revision"],
            audit_id=audit_id,
            effective=False,
            pending_topic_publication=accepted_topic,
            reviewed_at=review["reviewed_at"],
        )

    def review_for_owner(self, owner_id: UUID, **values) -> NewsReviewOutcome:
        with self.database.engine.begin() as connection:
            return self._review_for_owner(connection, owner_id, **values)

    def review(
        self,
        token: str,
        csrf: str,
        *,
        article_id: UUID,
        association_id: UUID,
        decision: str,
        expected_revision: int,
        key: str,
    ) -> NewsReviewOutcome:
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        with self.database.engine.begin() as connection:
            owner = self.owners.authorize_connection(connection, token, csrf)
            return self._review_for_owner(
                connection,
                owner.owner_id,
                article_id=article_id,
                association_id=association_id,
                decision=decision,
                expected_revision=expected_revision,
                key=key,
            )
