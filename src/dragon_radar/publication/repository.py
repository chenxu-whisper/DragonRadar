"""Authorized reads for immutable unified publications."""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from dragon_radar.domain.models import MarketBatch
from dragon_radar.publication.models import (
    Publication,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.publication.tables import (
    publication_results,
    publications,
    source_dependencies,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import Forbidden, NotFound, Repository, _uuid
from dragon_radar.topics.models import TopicBatch
from dragon_radar.topics.repository import TopicRepository
from dragon_radar.topics.tables import topic_import_batches, topic_snapshots


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
                withdrawal_dependencies,
                withdrawal_dependencies.c.withdrawal_id == withdrawal_records.c.id,
            )
            .outerjoin(
                source_dependencies,
                source_dependencies.c.id
                == withdrawal_dependencies.c.source_dependency_id,
            )
            .where(
                withdrawal_records.c.source_id == source_id,
                withdrawal_records.c.action.in_(("deidentified", "deleted")),
                or_(
                    and_(
                        source_dependencies.c.archive_type == archive_type,
                        source_dependencies.c.archive_id == archive_id,
                    ),
                    and_(
                        withdrawal_dependencies.c.archive_type == archive_type,
                        withdrawal_dependencies.c.archive_id == archive_id,
                    ),
                ),
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

    @staticmethod
    def _result(row: Mapping) -> PublicationResult:
        return PublicationResult(
            publication_id=row["publication_id"],
            domain=row["domain"],
            input_id=row["input_id"],
            status=row["status"],
            payload=row["payload"],
            coverage=row["coverage"],
            quality_flags=tuple(row["quality_flags"]),
        )

    def _lock_sources(self, connection, source_ids) -> dict:
        return {
            source_id: self.market._source(connection, source_id)
            for source_id in sorted(set(source_ids))
        }

    def _authorized_snapshot(
        self,
        record: Publication,
        dependencies,
        grants,
        *,
        market_rows,
        market_archives,
        topic_rows,
        topic_archives,
        result_rows,
        active_withdrawal_sources,
        withdrawn_publications,
    ) -> PublishedSnapshot:
        source_ids = {row["source_id"] for row in dependencies}
        if source_ids & active_withdrawal_sources:
            raise Forbidden("Publication inputs were withdrawn")
        if record.id in withdrawn_publications:
            raise Forbidden("Publication inputs were withdrawn")

        market_row = market_rows.get(record.market_snapshot_id)
        market_dependencies = [
            row
            for row in dependencies
            if row["derived_type"] == "market"
            or row["archive_type"] == "market_import_batch"
        ]
        if (
            market_row is None
            or len(market_dependencies) != 1
            or market_dependencies[0]["source_id"] != market_row["source_id"]
            or market_dependencies[0]["archive_type"] != "market_import_batch"
            or market_dependencies[0]["archive_id"] != market_row["batch_id"]
            or market_dependencies[0]["derived_type"] != "market"
            or market_dependencies[0]["derived_id"] != record.id
            or market_dependencies[0]["publication_id"] != record.id
        ):
            raise Forbidden("Publication market identity is invalid")
        market_archive = market_archives.get(market_row["batch_id"])
        if market_archive is None:
            raise Forbidden("Publication market identity is invalid")
        market_batch = MarketBatch.model_validate_json(market_archive["canonical_json"])
        self.market._authorize(grants[market_row["source_id"]], market_batch)
        market = self.market._snapshot(market_row, market_archive)

        if record.topic_snapshot_id is not None:
            topic_row = topic_rows.get(record.topic_snapshot_id)
            topic_dependencies = [
                row
                for row in dependencies
                if row["derived_type"] == "topic"
                or row["archive_type"] == "topic_import_batch"
            ]
            if (
                topic_row is None
                or len(topic_dependencies) != 1
                or topic_dependencies[0]["source_id"] != topic_row["source_id"]
                or topic_dependencies[0]["archive_type"] != "topic_import_batch"
                or topic_dependencies[0]["archive_id"] != topic_row["batch_id"]
                or topic_dependencies[0]["derived_type"] != "topic"
                or topic_dependencies[0]["derived_id"] != record.id
                or topic_dependencies[0]["publication_id"] != record.id
            ):
                raise Forbidden("Publication topic identity is invalid")
            topic_archive = topic_archives.get(topic_row["batch_id"])
            if topic_archive is None:
                raise Forbidden("Publication topic identity is invalid")
            topic_batch = TopicBatch.model_validate_json(
                topic_archive["canonical_json"]
            )
            if not TopicRepository._authorized(
                grants[topic_row["source_id"]],
                topic_batch.grant,
                topic_row["source_id"],
                self.clock(),
            ):
                raise Forbidden("Source is not locally authorized")
        elif any(
            row["derived_type"] == "topic"
            or row["archive_type"] == "topic_import_batch"
            for row in dependencies
        ):
            raise Forbidden("Publication topic identity is invalid")

        return PublishedSnapshot(
            record,
            market,
            tuple(self._result(row) for row in result_rows.get(record.id, ())),
        )

    def _authorized_records(self, connection, rows, *, omit_forbidden=False):
        records = [_publication(row) for row in rows]
        publication_ids = [record.id for record in records]
        dependencies = (
            connection.execute(
                select(source_dependencies)
                .where(source_dependencies.c.publication_id.in_(publication_ids))
                .order_by(
                    source_dependencies.c.source_id,
                    source_dependencies.c.archive_type,
                    source_dependencies.c.archive_id,
                )
            )
            .mappings()
            .all()
            if publication_ids
            else []
        )
        market_rows = {
            row["id"]: row
            for row in connection.execute(
                select(snapshots).where(
                    snapshots.c.id.in_(record.market_snapshot_id for record in records)
                )
            )
            .mappings()
            .all()
        }
        market_archives = {
            row["id"]: row
            for row in connection.execute(
                select(import_batches).where(
                    import_batches.c.id.in_(
                        row["batch_id"] for row in market_rows.values()
                    )
                )
            )
            .mappings()
            .all()
        }
        topic_rows = {
            row["id"]: row
            for row in connection.execute(
                select(topic_snapshots).where(
                    topic_snapshots.c.id.in_(
                        record.topic_snapshot_id
                        for record in records
                        if record.topic_snapshot_id is not None
                    )
                )
            )
            .mappings()
            .all()
        }
        topic_archives = {
            row["id"]: row
            for row in connection.execute(
                select(topic_import_batches).where(
                    topic_import_batches.c.id.in_(
                        row["batch_id"] for row in topic_rows.values()
                    )
                )
            )
            .mappings()
            .all()
        }
        source_ids = {
            *(row["source_id"] for row in dependencies),
            *(row["source_id"] for row in market_rows.values()),
            *(row["source_id"] for row in topic_rows.values()),
        }
        grants = self._lock_sources(
            connection,
            source_ids,
        )
        results = (
            connection.execute(
                select(publication_results)
                .where(publication_results.c.publication_id.in_(publication_ids))
                .order_by(
                    publication_results.c.publication_id,
                    publication_results.c.domain,
                )
            )
            .mappings()
            .all()
            if publication_ids
            else []
        )
        result_rows = {
            publication_id: [
                row for row in results if row["publication_id"] == publication_id
            ]
            for publication_id in publication_ids
        }
        ranked_withdrawals = (
            select(
                withdrawal_records.c.source_id,
                withdrawal_records.c.action,
                func.row_number()
                .over(
                    partition_by=withdrawal_records.c.source_id,
                    order_by=withdrawal_records.c.sequence.desc(),
                )
                .label("rank"),
            )
            .where(withdrawal_records.c.source_id.in_(source_ids))
            .subquery()
        )
        active_withdrawal_sources = set(
            connection.execute(
                select(ranked_withdrawals.c.source_id).where(
                    ranked_withdrawals.c.rank == 1,
                    ranked_withdrawals.c.action != "restored",
                )
            ).scalars()
        )
        withdrawn_publications = set(
            connection.execute(
                select(source_dependencies.c.publication_id)
                .join(
                    withdrawal_dependencies,
                    withdrawal_dependencies.c.source_dependency_id
                    == source_dependencies.c.id,
                )
                .join(
                    withdrawal_records,
                    withdrawal_records.c.id == withdrawal_dependencies.c.withdrawal_id,
                )
                .where(
                    source_dependencies.c.publication_id.in_(publication_ids),
                    withdrawal_records.c.action.in_(("deidentified", "deleted")),
                )
                .distinct()
            ).scalars()
        )
        by_publication = {
            publication_id: [
                row for row in dependencies if row["publication_id"] == publication_id
            ]
            for publication_id in publication_ids
        }
        authorized = []
        for record in records:
            try:
                snapshot = self._authorized_snapshot(
                    record,
                    by_publication[record.id],
                    grants,
                    market_rows=market_rows,
                    market_archives=market_archives,
                    topic_rows=topic_rows,
                    topic_archives=topic_archives,
                    result_rows=result_rows,
                    active_withdrawal_sources=active_withdrawal_sources,
                    withdrawn_publications=withdrawn_publications,
                )
            except Forbidden:
                if omit_forbidden:
                    continue
                raise
            authorized.append(snapshot)
        return authorized

    def get_publication(self, publication_id: UUID | str) -> Publication | None:
        publication_id = _uuid(publication_id)
        with self.database.engine.begin() as connection:
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
            self._authorized_records(connection, [row])
            return record

    def get_snapshot(self, snapshot_id: UUID | str):
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
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
                return self._authorized_records(connection, [row])[0]
            legacy = (
                connection.execute(
                    select(snapshots).where(snapshots.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if legacy is None:
                return None
            current = self.market._source(connection, legacy["source_id"])
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
            archive = (
                connection.execute(
                    select(import_batches).where(
                        import_batches.c.id == legacy["batch_id"]
                    )
                )
                .mappings()
                .one()
            )
            result = self.market._snapshot(legacy, archive)
            self.market._authorize(current, result.batch)
            return result

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
        batch_size = max(10, min(100, limit + offset))
        last_candidate = None
        authorized_offset = 0
        visible = []
        while len(visible) < limit:
            candidate_query = query
            if last_candidate is not None:
                candidate_query = candidate_query.where(
                    or_(
                        publications.c.trade_date < last_candidate["trade_date"],
                        and_(
                            publications.c.trade_date == last_candidate["trade_date"],
                            publications.c.version < last_candidate["version"],
                        ),
                        and_(
                            publications.c.trade_date == last_candidate["trade_date"],
                            publications.c.version == last_candidate["version"],
                            publications.c.id > last_candidate["id"],
                        ),
                    )
                )
            with self.database.engine.begin() as connection:
                rows = (
                    connection.execute(candidate_query.limit(batch_size))
                    .mappings()
                    .all()
                )
                authorized = self._authorized_records(
                    connection,
                    rows,
                    omit_forbidden=True,
                )
            for snapshot in authorized:
                if authorized_offset < offset:
                    authorized_offset += 1
                else:
                    visible.append(snapshot)
                    if len(visible) == limit:
                        break
            if len(rows) < batch_size:
                break
            last_candidate = rows[-1]
        return visible

    def list_results(self, publication_id: UUID | str) -> tuple[PublicationResult, ...]:
        publication_id = _uuid(publication_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(publications).where(publications.c.id == publication_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Publication not found")
            if row["status"] != "published":
                raise Forbidden("Publication is not available")
            snapshot = self._authorized_records(connection, [row])[0]
            return snapshot.results
