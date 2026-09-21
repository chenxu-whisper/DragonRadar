"""Authorized reads for immutable unified publications."""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select

from dragon_radar.publication.models import (
    Publication,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.publication.tables import (
    publication_results,
    publications,
    source_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.models import snapshots
from dragon_radar.storage.repository import Forbidden, NotFound, Repository, _uuid
from dragon_radar.topics.repository import TopicRepository


def _publication(row: Mapping) -> Publication:
    return Publication(
        id=row["id"],
        trade_date=row["trade_date"],
        version=row["version"],
        supersedes=row["supersedes"],
        status=row["status"],
        market_snapshot_id=row["market_snapshot_id"],
        topic_snapshot_id=row["topic_snapshot_id"],
        news_input_id=row["news_input_id"],
        regulation_input_id=row["regulation_input_id"],
        role_input_id=row["role_input_id"],
        domains=row["domains"],
        coverage=row["coverage"],
        quality_flags=tuple(row["quality_flags"]),
        command_json=row["command_json"],
        published_at=row["published_at"],
    )


def archive_was_withdrawn(
    connection, source_id: str, archive_type: str, archive_id: UUID
) -> bool:
    return (
        connection.scalar(
            select(withdrawal_records.c.id)
            .join(
                source_dependencies,
                source_dependencies.c.source_id == withdrawal_records.c.source_id,
            )
            .where(
                withdrawal_records.c.source_id == source_id,
                withdrawal_records.c.action.in_(("deidentified", "deleted")),
                source_dependencies.c.archive_type == archive_type,
                source_dependencies.c.archive_id == archive_id,
            )
            .limit(1)
        )
        is not None
    )


def source_has_active_withdrawal(connection, source_id: str) -> bool:
    action = connection.scalar(
        select(withdrawal_records.c.action)
        .where(withdrawal_records.c.source_id == source_id)
        .order_by(withdrawal_records.c.sequence.desc())
        .limit(1)
    )
    return action is not None and action != "restored"


class PublicationRepository:
    """Select publication IDs while preserving legacy market snapshot reads."""

    def __init__(
        self,
        database,
        market_repository: Repository | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))
        self.market = market_repository or Repository(database, clock=self.clock)
        self.topics = TopicRepository(database, clock=self.clock)

    def _publication_was_withdrawn(self, publication_id: UUID) -> bool:
        with self.database.engine.connect() as connection:
            source_ids = tuple(
                connection.execute(
                    select(source_dependencies.c.source_id)
                    .where(source_dependencies.c.publication_id == publication_id)
                    .distinct()
                ).scalars()
            )
            if any(
                source_has_active_withdrawal(connection, source_id)
                for source_id in source_ids
            ):
                return True
            return (
                connection.scalar(
                    select(withdrawal_records.c.id)
                    .join(
                        source_dependencies,
                        source_dependencies.c.source_id
                        == withdrawal_records.c.source_id,
                    )
                    .where(
                        source_dependencies.c.publication_id == publication_id,
                        withdrawal_records.c.action.in_(("deidentified", "deleted")),
                    )
                    .limit(1)
                )
                is not None
            )

    def _authorized_snapshot(self, record: Publication) -> PublishedSnapshot:
        if self._publication_was_withdrawn(record.id):
            raise Forbidden("Publication inputs were withdrawn")
        market = self.market.get_snapshot(record.market_snapshot_id)
        if record.topic_snapshot_id is not None:
            self.topics.get_snapshot(record.topic_snapshot_id)
        return PublishedSnapshot(record, market)

    def get_publication(self, publication_id: UUID | str) -> Publication | None:
        publication_id = _uuid(publication_id)
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(publications).where(publications.c.id == publication_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        record = _publication(row)
        if record.status != "published":
            raise Forbidden("Publication is not available")
        self._authorized_snapshot(record)
        return record

    def get_snapshot(self, snapshot_id: UUID | str):
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.connect() as connection:
            row = (
                connection.execute(
                    select(publications).where(publications.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
        if row is not None:
            record = _publication(row)
            if record.status != "published":
                raise Forbidden("Publication is not available")
            return self._authorized_snapshot(record)
        with self.database.engine.connect() as connection:
            legacy = (
                connection.execute(
                    select(
                        snapshots.c.source_id,
                        snapshots.c.batch_id,
                    ).where(snapshots.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if legacy is not None and archive_was_withdrawn(
                connection,
                legacy["source_id"],
                "market_import_batch",
                legacy["batch_id"],
            ):
                raise Forbidden("Snapshot input was withdrawn")
            if legacy is not None and source_has_active_withdrawal(
                connection, legacy["source_id"]
            ):
                raise Forbidden("Snapshot source has an active withdrawal")
        try:
            return self.market.get_snapshot(snapshot_id)
        except NotFound:
            return None

    def list_snapshots(
        self, *, trade_date: date | None = None, limit: int = 50, offset: int = 0
    ) -> list[PublishedSnapshot]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError("offset must be from 0 to 10000")
        query = select(publications).where(publications.c.status == "published")
        if trade_date is not None:
            query = query.where(publications.c.trade_date == trade_date)
        query = query.order_by(
            publications.c.trade_date.desc(),
            publications.c.version.desc(),
            publications.c.id,
        )
        with self.database.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        visible = []
        for row in rows:
            record = _publication(row)
            try:
                visible.append(self._authorized_snapshot(record))
            except Forbidden:
                continue
        return visible[offset : offset + limit]

    def list_results(self, publication_id: UUID | str) -> tuple[PublicationResult, ...]:
        publication_id = _uuid(publication_id)
        record = self.get_publication(publication_id)
        if record is None:
            raise NotFound("Publication not found")
        with self.database.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(publication_results)
                    .where(publication_results.c.publication_id == publication_id)
                    .order_by(publication_results.c.domain)
                )
                .mappings()
                .all()
            )
        return tuple(
            PublicationResult(
                publication_id=row["publication_id"],
                domain=row["domain"],
                input_id=row["input_id"],
                status=row["status"],
                payload=row["payload"],
                coverage=row["coverage"],
                quality_flags=tuple(row["quality_flags"]),
            )
            for row in rows
        )
