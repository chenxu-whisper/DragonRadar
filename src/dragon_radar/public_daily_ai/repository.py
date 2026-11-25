"""Public artifact reads with fresh source/output authorization, no task access."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import (
    grant_allows_ai,
    grant_allows_display,
    grant_allows_storage,
)
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.public_daily_ai.entitlements import OutputEntitlementRepository
from dragon_radar.public_daily_ai.models import digest, utc_time, validate_safe_payload
from dragon_radar.public_daily_ai.tables import (
    ai_artifact_revocations,
    promoted_news_summaries,
    public_daily_ai_archives,
)
from dragon_radar.publication.access import grant_allows_public_sharing
from dragon_radar.publication.repository import (
    archive_was_withdrawn,
    artifact_was_display_withdrawn,
    source_has_active_withdrawal,
)
from dragon_radar.publication.tables import (
    source_dependencies,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.repository import Forbidden, NotFound


class PublicDailyAIRepository:
    def __init__(self, database, publications, *, clock=None, public=True):
        self.database = database
        self.publications = publications
        raw_clock = clock or (lambda: datetime.now(UTC))
        self.clock = lambda: utc_time(raw_clock())
        self.public = public
        self.entitlements = OutputEntitlementRepository(database, clock=self.clock)

    def snapshot(self, connection, snapshot_id, trade_date, *, kind="daily_ai"):
        snapshot = self.publications.get_ai_snapshot(
            connection,
            snapshot_id,
            ai_domains=("market",) if kind == "daily_ai" else ("news",),
        )
        if snapshot is None:
            raise NotFound("Publication not found")
        if snapshot.trade_date != trade_date:
            raise ValueError("Publication date mismatch")
        return snapshot

    def authorize(self, connection, row, *, public=None):
        public = self.public if public is None else public
        if connection.scalar(
            select(ai_artifact_revocations.c.id)
            .where(
                ai_artifact_revocations.c.artifact_id == row["id"],
            )
            .limit(1)
        ):
            raise Forbidden("Artifact withdrawn")
        try:
            rights = row["rights_manifest"]
            if set(rights) != {"sources", "output", "provider", "model_version"}:
                raise ValueError
            if not rights["sources"]:
                raise ValueError
            for source in sorted(rights["sources"], key=lambda item: item["source_id"]):
                current = self.publications.get_ai_source_grant(
                    connection, source["source_id"]
                )
                archive_type = (
                    "public_daily_ai"
                    if row["article_revision_id"] is None
                    else "promoted_news_summary"
                )
                if (
                    source_has_active_withdrawal(connection, source["source_id"])
                    or archive_was_withdrawn(
                        connection,
                        source["source_id"],
                        archive_type,
                        row["id"],
                    )
                    or artifact_was_display_withdrawn(
                        connection, source["source_id"], archive_type, row["id"]
                    )
                ):
                    raise Forbidden("Source withdrawal denies artifact")
                historical = connection.scalar(
                    select(withdrawal_records.c.id)
                    .join(
                        withdrawal_dependencies,
                        withdrawal_dependencies.c.withdrawal_id
                        == withdrawal_records.c.id,
                    )
                    .join(
                        source_dependencies,
                        source_dependencies.c.id
                        == withdrawal_dependencies.c.source_dependency_id,
                    )
                    .where(
                        source_dependencies.c.derived_id == row["id"],
                        withdrawal_records.c.source_id == source["source_id"],
                        withdrawal_records.c.action.in_(
                            ("blocked", "deidentified", "deleted")
                        ),
                    )
                    .limit(1)
                )
                if historical:
                    raise Forbidden(
                        "Withdrawn artifacts require explicit republication"
                    )
                embedded = SourceGrant.model_validate(source["grant"])
                predicates = [
                    grant_allows_storage,
                    grant_allows_display,
                    grant_allows_ai,
                ]
                if public:
                    predicates.append(grant_allows_public_sharing)
                if not all(
                    predicate(grant, source_id=source["source_id"], now=self.clock())
                    for grant in (current, embedded)
                    for predicate in predicates
                ):
                    raise Forbidden("Source rights denied")
            self.entitlements.authorize(
                connection,
                rights["output"]["reference"],
                provider=rights["provider"],
                model_version=rights["model_version"],
                frozen=rights["output"],
                public=public,
            )
            if row["content_hash"] != digest(row["safe_payload"]):
                raise ValueError
            if row["article_revision_id"] is None:
                validate_safe_payload(row["safe_payload"], row["base_publication_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataUnavailable from exc

    def get(self, snapshot_id, trade_date, *, archive_id=None):
        with self.database.engine.begin() as connection:
            snapshot = self.snapshot(connection, UUID(str(snapshot_id)), trade_date)
            query = select(public_daily_ai_archives).where(
                public_daily_ai_archives.c.base_publication_id == snapshot.id,
            )
            if archive_id is not None:
                query = select(public_daily_ai_archives).where(
                    public_daily_ai_archives.c.id == UUID(str(archive_id))
                )
            rows = (
                connection.execute(
                    query.order_by(public_daily_ai_archives.c.archive_version.desc())
                )
                .mappings()
                .all()
            )
            if archive_id is not None and not rows:
                raise NotFound("Archive not found")
            for row in rows:
                if (
                    row["base_publication_id"] != snapshot.id
                    or row["trade_date"] != trade_date
                ):
                    raise ValueError("Archive identity mismatch")
                try:
                    self.authorize(connection, row)
                except Forbidden:
                    if archive_id is not None:
                        raise
                    continue
                return snapshot, dict(row)
            return snapshot, None

    def discover(self, snapshot_id, trade_date, *, watermark=None):
        with self.database.engine.begin() as connection:
            snapshot = self.snapshot(connection, snapshot_id, trade_date)
            if watermark is None:
                watermark = connection.scalar(
                    select(
                        func.coalesce(
                            func.max(source_dependencies.c.dependency_sequence), 0
                        )
                    )
                )
            rows = (
                connection.execute(
                    select(public_daily_ai_archives)
                    .where(
                        public_daily_ai_archives.c.base_publication_id == snapshot.id,
                        public_daily_ai_archives.c.publication_sequence <= watermark,
                    )
                    .order_by(
                        public_daily_ai_archives.c.archive_version.desc(),
                        public_daily_ai_archives.c.id,
                    )
                )
                .mappings()
                .all()
            )
            visible = []
            for row in rows:
                try:
                    self.authorize(connection, row)
                except Forbidden:
                    continue
                visible.append(dict(row))
            return snapshot, watermark, visible

    def summary_source_ids(self, connection, selections):
        """Resolve every origin and frozen-rights source before caller locking."""
        origins = set()
        sources = set()
        for choice in selections:
            row = (
                connection.execute(
                    select(promoted_news_summaries).where(
                        promoted_news_summaries.c.id == choice.summary_id,
                        promoted_news_summaries.c.archive_version
                        == choice.summary_revision,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Promoted summary not found")
            origins.add(row["base_publication_id"])
            sources.update(
                item["source_id"] for item in row["rights_manifest"]["sources"]
            )
        sources.update(self.publications.get_ai_source_ids(connection, origins))
        return sources

    def summary(self, connection, summary_id, revision, article):
        row = (
            connection.execute(
                select(promoted_news_summaries).where(
                    promoted_news_summaries.c.id == UUID(str(summary_id)),
                    promoted_news_summaries.c.archive_version == revision,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Promoted summary not found")
        self.snapshot(
            connection, row["base_publication_id"], row["trade_date"], kind="summary"
        )
        self.authorize(connection, row)
        from dragon_radar.news.summary_publication import NewsSummaryArtifact

        artifact = NewsSummaryArtifact.model_validate(row["safe_payload"])
        artifact.bind(article)
        return artifact.model_dump(mode="json", by_alias=True)
