"""Authorized reads for immutable unified publications."""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.domain.quality import grant_allows_ai, grant_allows_display
from dragon_radar.news.tables import (
    news_article_revisions,
    news_event_articles,
    news_snapshot_items,
    news_snapshots,
)
from dragon_radar.publication.models import (
    Publication,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.publication.tables import (
    PUBLICATION_SEQUENCE_LOCK,
    publication_results,
    publications,
    source_dependencies,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.regulation.repository import RegulationRepository
from dragon_radar.roles.tables import role_runs
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


def _role_validation_input_ids(payload) -> set[UUID]:
    try:
        topics = payload["topics"]
        input_ids = set()
        for topic in topics:
            nested_inputs = []
            for validation in topic["role_validations"]:
                validation_id = UUID(validation["id"])
                inputs = validation["validation_inputs"]
                positions = set()
                run_ids = set()
                for item in inputs:
                    item_validation_id = UUID(
                        item.get("validation_id", str(validation_id))
                    )
                    position = item["position"]
                    run_id = UUID(item["input_role_run_id"])
                    if item_validation_id != validation_id or type(position) is not int:
                        raise ValueError
                    positions.add(position)
                    run_ids.add(run_id)
                    nested_inputs.append((item_validation_id, position, run_id))
                    input_ids.add(run_id)
                if (
                    validation["outcome"]
                    not in {"supported", "weakened", "refuted", "unavailable"}
                    or len(inputs) > 5
                    or positions - set(range(5))
                    or len(positions) != len(inputs)
                    or len(run_ids) != len(inputs)
                    or (
                        validation["outcome"] != "unavailable"
                        and positions != set(range(5))
                    )
                ):
                    raise ValueError
            flat_inputs = [
                (
                    UUID(item["validation_id"]),
                    item["position"],
                    UUID(item["input_role_run_id"]),
                )
                for item in topic["role_validation_inputs"]
            ]
            if sorted(nested_inputs, key=str) != sorted(flat_inputs, key=str):
                raise ValueError
        return input_ids
    except (KeyError, TypeError, ValueError):
        raise Forbidden("Publication role payload is invalid") from None


def _news_items_by_revision(items) -> dict:
    return {item["article_revision_id"]: item for item in items}


def _news_items_by_snapshot(snapshot_ids, items) -> dict:
    grouped = {snapshot_id: [] for snapshot_id in snapshot_ids}
    for item in items:
        if item["snapshot_id"] in grouped:
            grouped[item["snapshot_id"]].append(item)
    return grouped


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
        self.regulations = RegulationRepository(
            database,
            self.market,
            clock=self.clock,
        )

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
        ai_domains,
        market_rows,
        market_archives,
        topic_rows,
        topic_archives,
        news_snapshot_rows,
        news_items_by_snapshot,
        news_revisions,
        news_event_links,
        regulation_snapshot_rows,
        regulation_contexts,
        expected_role_dependencies,
        result_rows,
        active_withdrawal_sources,
        withdrawn_publications,
    ) -> PublishedSnapshot:
        source_ids = {row["source_id"] for row in dependencies}
        if source_ids & active_withdrawal_sources:
            raise Forbidden("Publication inputs were withdrawn")
        if record.id in withdrawn_publications:
            raise Forbidden("Publication inputs were withdrawn")
        ai_source_ids = {
            row["source_id"]
            for row in dependencies
            if row["derived_type"] in ai_domains
        }
        if not all(
            grant_allows_ai(
                grants[source_id],
                source_id=source_id,
                now=self.clock(),
            )
            for source_id in ai_source_ids
        ):
            raise Forbidden("Source is not authorized for AI processing")

        market_row = market_rows.get(record.market_snapshot_id)
        market_dependencies = [
            row for row in dependencies if row["derived_type"] == "market"
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
        if "market" in ai_domains and not grant_allows_ai(
            market_batch.grant,
            source_id=market_row["source_id"],
            now=self.clock(),
        ):
            raise Forbidden("Source is not authorized for AI processing")
        market = self.market._snapshot(market_row, market_archive)

        if record.topic_snapshot_id is not None:
            topic_row = topic_rows.get(record.topic_snapshot_id)
            topic_dependencies = [
                row for row in dependencies if row["derived_type"] == "topic"
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
            if "topic" in ai_domains and not grant_allows_ai(
                topic_batch.grant,
                source_id=topic_row["source_id"],
                now=self.clock(),
            ):
                raise Forbidden("Source is not authorized for AI processing")
        elif any(row["derived_type"] == "topic" for row in dependencies):
            raise Forbidden("Publication topic identity is invalid")

        news_dependencies = [
            row for row in dependencies if row["derived_type"] == "news"
        ]
        if record.news_input_id is not None:
            root = news_snapshot_rows.get(record.news_input_id)
            items = news_items_by_snapshot.get(record.news_input_id, ())
            items_by_revision = _news_items_by_revision(items)
            expected = {
                (
                    revision["source_id"],
                    "news_article_revision",
                    item["article_revision_id"],
                )
                for item in items
                if (revision := news_revisions.get(item["article_revision_id"]))
                is not None
            }
            actual = {
                (row["source_id"], row["archive_type"], row["archive_id"])
                for row in news_dependencies
            }
            news_result = next(
                (
                    row
                    for row in result_rows.get(record.id, ())
                    if row["domain"] == "news"
                ),
                None,
            )
            try:
                payload_identity = {
                    (
                        UUID(article["article_id"]),
                        UUID(article["revision_id"]),
                        article["revision"],
                        UUID(article["event_id"]),
                    )
                    for article in news_result["payload"]["articles"]
                }
            except (KeyError, TypeError, ValueError):
                raise Forbidden("Publication news lineage is invalid") from None
            item_identity = {
                (
                    item["article_id"],
                    item["article_revision_id"],
                    item["revision"],
                    item["event_id"],
                )
                for item in items
            }
            if (
                root is None
                or news_result is None
                or news_result["status"] != "ready"
                or news_result["input_id"] != record.news_input_id
                or len(expected) != len(items)
                or actual != expected
                or payload_identity != item_identity
            ):
                raise Forbidden("Publication news lineage is invalid")
            for dependency in news_dependencies:
                revision = news_revisions.get(dependency["archive_id"])
                if (
                    revision is None
                    or dependency["derived_id"] != record.id
                    or dependency["publication_id"] != record.id
                    or revision["available_at"] > root["as_of"]
                ):
                    raise Forbidden("Publication news lineage is invalid")
                item = items_by_revision.get(revision["id"])
                if (
                    item is None
                    or item["article_id"] != revision["article_id"]
                    or item["revision"] != revision["revision"]
                    or news_event_links.get(revision["id"]) != item["event_id"]
                ):
                    raise Forbidden("Publication news lineage is invalid")
                embedded = SourceGrant.model_validate(revision["embedded_grant"])
                if not all(
                    grant_allows_display(
                        grant,
                        source_id=revision["source_id"],
                        now=self.clock(),
                    )
                    for grant in (grants[revision["source_id"]], embedded)
                ):
                    raise Forbidden("Source is not locally authorized")
                if "news" in ai_domains and not grant_allows_ai(
                    embedded,
                    source_id=revision["source_id"],
                    now=self.clock(),
                ):
                    raise Forbidden("Source is not authorized for AI processing")
        elif news_dependencies:
            raise Forbidden("Publication news lineage is invalid")

        regulation_dependencies = [
            row for row in dependencies if row["derived_type"] == "regulation"
        ]
        if record.regulation_input_id is not None:
            regulation_root = regulation_snapshot_rows.get(record.regulation_input_id)
            regulation_result = next(
                (
                    row
                    for row in result_rows.get(record.id, ())
                    if row["domain"] == "regulation"
                ),
                None,
            )
            try:
                payload_snapshot_id = UUID(regulation_result["payload"]["snapshot_id"])
                payload_market_id = UUID(
                    regulation_result["payload"]["market_snapshot_id"]
                )
            except (KeyError, TypeError, ValueError):
                raise Forbidden("Publication regulation lineage is invalid") from None
            if (
                regulation_root is None
                or regulation_result is None
                or regulation_result["status"] != "ready"
                or regulation_result["input_id"] != record.regulation_input_id
                or regulation_root["market_snapshot_id"] != record.market_snapshot_id
                or regulation_root["trade_date"] != record.trade_date
                or payload_snapshot_id != record.regulation_input_id
                or payload_market_id != record.market_snapshot_id
            ):
                raise Forbidden("Publication regulation lineage is invalid")
            regulation_context = regulation_contexts[record.regulation_input_id]
            expected = RegulationRepository.materialize_context(regulation_context)
            if (
                regulation_result["payload"]
                != expected.model_dump(mode="json")["payload"]
                or regulation_result["coverage"]
                != expected.model_dump(mode="json")["coverage"]
                or tuple(regulation_result["quality_flags"]) != expected.quality_flags
            ):
                raise Forbidden("Publication regulation payload is invalid")
            expected_dependencies = {
                (source_id, archive_type, archive_id)
                for source_id, archive_type, archive_id, _domain in (
                    RegulationRepository.dependencies(regulation_context)
                )
            }
            actual_dependencies = {
                (
                    dependency["source_id"],
                    dependency["archive_type"],
                    dependency["archive_id"],
                )
                for dependency in regulation_dependencies
            }
            if actual_dependencies != expected_dependencies or any(
                dependency["derived_id"] != record.id
                or dependency["publication_id"] != record.id
                for dependency in regulation_dependencies
            ):
                raise Forbidden("Publication regulation lineage is invalid")
            for dependency in regulation_dependencies:
                if dependency["archive_type"] != "regulation_event_snapshot":
                    continue
                if not grant_allows_display(
                    grants[dependency["source_id"]],
                    source_id=dependency["source_id"],
                    now=self.clock(),
                ):
                    raise Forbidden("Regulation event source is not locally authorized")
        elif regulation_dependencies:
            raise Forbidden("Publication regulation lineage is invalid")

        role_dependencies = [
            row for row in dependencies if row["derived_type"] == "role"
        ]
        actual_role_dependencies = {
            (row["source_id"], row["archive_type"], row["archive_id"])
            for row in role_dependencies
        }
        if actual_role_dependencies != expected_role_dependencies:
            raise Forbidden("Publication role lineage is invalid")
        for dependency in role_dependencies:
            if (
                dependency["derived_id"] != record.id
                or dependency["publication_id"] != record.id
            ):
                raise Forbidden("Publication role lineage is invalid")
            if dependency["archive_type"] == "market_import_batch":
                archive = market_archives.get(dependency["archive_id"])
                if archive is None or archive["source_id"] != dependency["source_id"]:
                    raise Forbidden("Publication role lineage is invalid")
                batch = MarketBatch.model_validate_json(archive["canonical_json"])
                self.market._authorize(grants[dependency["source_id"]], batch)
                if "role" in ai_domains and not grant_allows_ai(
                    batch.grant,
                    source_id=dependency["source_id"],
                    now=self.clock(),
                ):
                    raise Forbidden("Source is not authorized for AI processing")
            elif dependency["archive_type"] == "topic_import_batch":
                archive = topic_archives.get(dependency["archive_id"])
                if archive is None or archive["source_id"] != dependency["source_id"]:
                    raise Forbidden("Publication role lineage is invalid")
                batch = TopicBatch.model_validate_json(archive["canonical_json"])
                if not TopicRepository._authorized(
                    grants[dependency["source_id"]],
                    batch.grant,
                    dependency["source_id"],
                    self.clock(),
                ):
                    raise Forbidden("Source is not locally authorized")
                if "role" in ai_domains and not grant_allows_ai(
                    batch.grant,
                    source_id=dependency["source_id"],
                    now=self.clock(),
                ):
                    raise Forbidden("Source is not authorized for AI processing")
            else:
                raise Forbidden("Publication role lineage is invalid")
        if any(
            row["derived_type"] not in {"market", "topic", "news", "regulation", "role"}
            for row in dependencies
        ):
            raise Forbidden("Publication dependency type is invalid")

        return PublishedSnapshot(
            record,
            market,
            tuple(self._result(row) for row in result_rows.get(record.id, ())),
        )

    def _authorized_records(
        self,
        connection,
        rows,
        *,
        omit_forbidden=False,
        ai_domains=frozenset(),
    ):
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
                        {
                            *(row["batch_id"] for row in market_rows.values()),
                            *(
                                row["archive_id"]
                                for row in dependencies
                                if row["archive_type"] == "market_import_batch"
                            ),
                        }
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
                        {
                            *(row["batch_id"] for row in topic_rows.values()),
                            *(
                                row["archive_id"]
                                for row in dependencies
                                if row["archive_type"] == "topic_import_batch"
                            ),
                        }
                    )
                )
            )
            .mappings()
            .all()
        }
        news_snapshot_ids = {
            record.news_input_id
            for record in records
            if record.news_input_id is not None
        }
        news_snapshot_rows = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(news_snapshots).where(
                        news_snapshots.c.id.in_(news_snapshot_ids)
                    )
                )
                .mappings()
                .all()
            }
            if news_snapshot_ids
            else {}
        )
        news_items = (
            connection.execute(
                select(news_snapshot_items).where(
                    news_snapshot_items.c.snapshot_id.in_(news_snapshot_rows)
                )
            )
            .mappings()
            .all()
            if news_snapshot_rows
            else []
        )
        news_items_by_snapshot = _news_items_by_snapshot(
            news_snapshot_rows,
            news_items,
        )
        news_revision_ids = {
            *(row["article_revision_id"] for row in news_items),
            *(
                row["archive_id"]
                for row in dependencies
                if row["archive_type"] == "news_article_revision"
            ),
        }
        news_revisions = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(news_article_revisions).where(
                        news_article_revisions.c.id.in_(news_revision_ids)
                    )
                )
                .mappings()
                .all()
            }
            if news_revision_ids
            else {}
        )
        news_event_links = (
            {
                row["article_revision_id"]: row["event_id"]
                for row in connection.execute(
                    select(news_event_articles).where(
                        news_event_articles.c.article_revision_id.in_(news_revision_ids)
                    )
                )
                .mappings()
                .all()
            }
            if news_revision_ids
            else {}
        )
        regulation_snapshot_ids = {
            record.regulation_input_id
            for record in records
            if record.regulation_input_id is not None
        }
        regulation_contexts = (
            self.regulations.snapshot_contexts(
                connection,
                regulation_snapshot_ids,
            )
            if regulation_snapshot_ids
            else {}
        )
        regulation_snapshot_rows = {
            snapshot_id: context["root"]
            for snapshot_id, context in regulation_contexts.items()
        }
        source_ids = {
            *(row["source_id"] for row in dependencies),
            *(row["source_id"] for row in market_rows.values()),
            *(row["source_id"] for row in topic_rows.values()),
            *(row["source_id"] for row in news_revisions.values()),
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
        result_rows = {publication_id: [] for publication_id in publication_ids}
        for row in results:
            result_rows[row["publication_id"]].append(row)
        role_input_ids_by_publication = {}
        all_role_input_ids = set()
        for publication_id, rows_for_publication in result_rows.items():
            role_result = next(
                (
                    row
                    for row in rows_for_publication
                    if row["domain"] == "role" and row["status"] == "ready"
                ),
                None,
            )
            input_ids = set()
            if role_result is not None:
                input_ids = _role_validation_input_ids(role_result["payload"])
            role_input_ids_by_publication[publication_id] = input_ids
            all_role_input_ids.update(input_ids)
        validation_input_runs = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(role_runs).where(role_runs.c.id.in_(all_role_input_ids))
                )
                .mappings()
                .all()
            }
            if all_role_input_ids
            else {}
        )
        if set(validation_input_runs) != all_role_input_ids or any(
            row["status"] != "materialized" for row in validation_input_runs.values()
        ):
            raise Forbidden("Publication role lineage is invalid")
        role_market_snapshot_ids = {
            run["market_snapshot_id"] for run in validation_input_runs.values()
        }
        role_topic_snapshot_ids = {
            run["topic_snapshot_id"] for run in validation_input_runs.values()
        }
        role_market_rows = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(snapshots).where(
                        snapshots.c.id.in_(role_market_snapshot_ids)
                    )
                )
                .mappings()
                .all()
            }
            if role_market_snapshot_ids
            else {}
        )
        role_topic_rows = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(topic_snapshots).where(
                        topic_snapshots.c.id.in_(role_topic_snapshot_ids)
                    )
                )
                .mappings()
                .all()
            }
            if role_topic_snapshot_ids
            else {}
        )
        expected_role_dependencies = {}
        for publication_id, input_ids in role_input_ids_by_publication.items():
            expected = set()
            for run_id in input_ids:
                run = validation_input_runs[run_id]
                market_row = role_market_rows.get(run["market_snapshot_id"])
                topic_row = role_topic_rows.get(run["topic_snapshot_id"])
                if market_row is None or topic_row is None:
                    raise Forbidden("Publication role lineage is invalid")
                expected.add(
                    (
                        market_row["source_id"],
                        "market_import_batch",
                        market_row["batch_id"],
                    )
                )
                expected.add(
                    (
                        topic_row["source_id"],
                        "topic_import_batch",
                        topic_row["batch_id"],
                    )
                )
            expected_role_dependencies[publication_id] = expected
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
        by_publication = {publication_id: [] for publication_id in publication_ids}
        for row in dependencies:
            by_publication[row["publication_id"]].append(row)
        authorized = []
        for record in records:
            try:
                snapshot = self._authorized_snapshot(
                    record,
                    by_publication[record.id],
                    grants,
                    ai_domains=ai_domains,
                    market_rows=market_rows,
                    market_archives=market_archives,
                    topic_rows=topic_rows,
                    topic_archives=topic_archives,
                    news_snapshot_rows=news_snapshot_rows,
                    news_items_by_snapshot=news_items_by_snapshot,
                    news_revisions=news_revisions,
                    news_event_links=news_event_links,
                    regulation_snapshot_rows=regulation_snapshot_rows,
                    regulation_contexts=regulation_contexts,
                    expected_role_dependencies=expected_role_dependencies[record.id],
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
        self,
        *,
        trade_date: date | None = None,
        through_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PublishedSnapshot]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be from 1 to 100")
        if type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError("offset must be from 0 to 10000")
        query = select(publications).where(publications.c.status == "published")
        if trade_date is not None:
            query = query.where(publications.c.trade_date == trade_date)
        if through_date is not None:
            query = query.where(publications.c.trade_date <= through_date)
        query = query.order_by(
            publications.c.trade_date.desc(),
            publications.c.version.desc(),
            publications.c.id,
        )
        batch_size = max(10, min(100, limit + offset))
        last_candidate = None
        watermark = None
        authorized_offset = 0
        visible = []
        while len(visible) < limit:
            with self.database.engine.begin() as connection:
                if watermark is None:
                    self.market._lock(connection, PUBLICATION_SEQUENCE_LOCK)
                    watermark = connection.scalar(
                        select(func.max(publications.c.publication_sequence))
                    )
                    if watermark is None:
                        return []
                candidate_query = query.where(
                    publications.c.publication_sequence <= watermark
                )
                if last_candidate is not None:
                    candidate_query = candidate_query.where(
                        or_(
                            publications.c.trade_date < last_candidate["trade_date"],
                            and_(
                                publications.c.trade_date
                                == last_candidate["trade_date"],
                                publications.c.version < last_candidate["version"],
                            ),
                            and_(
                                publications.c.trade_date
                                == last_candidate["trade_date"],
                                publications.c.version == last_candidate["version"],
                                publications.c.id > last_candidate["id"],
                            ),
                        )
                    )
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
