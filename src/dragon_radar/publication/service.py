"""Atomic publication orchestration."""

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update

from dragon_radar.analytics.metrics import market_breadth
from dragon_radar.domain.models import MarketBatch
from dragon_radar.publication.models import (
    DOMAIN_NAMES,
    DomainMaterialization,
    Publication,
    PublicationCommand,
)
from dragon_radar.publication.repository import (
    PublicationRepository,
    archive_was_withdrawn,
    source_has_active_withdrawal,
)
from dragon_radar.publication.tables import (
    publication_attempts,
    publication_results,
    publications,
    source_dependencies,
)
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    Repository,
    _identifier,
)
from dragon_radar.topics.models import TopicBatch
from dragon_radar.topics.repository import TopicRepository
from dragon_radar.topics.tables import topic_import_batches, topic_snapshots

Materializer = Callable[[object, PublicationCommand], DomainMaterialization]


class PublicationService:
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
        self.repository = PublicationRepository(database, self.market, clock=self.clock)

    def _validate_inputs(self, connection, command):
        market_row = (
            connection.execute(
                select(snapshots).where(snapshots.c.id == command.market_snapshot_id)
            )
            .mappings()
            .first()
        )
        if market_row is None:
            raise NotFound("Market snapshot not found")
        market_archive = (
            connection.execute(
                select(import_batches).where(
                    import_batches.c.id == market_row["batch_id"]
                )
            )
            .mappings()
            .one()
        )
        market_batch = MarketBatch.model_validate_json(market_archive["canonical_json"])
        if market_row["trade_date"] != command.trade_date:
            raise ValueError("Market snapshot date mismatch")

        dependencies = [
            (
                market_row["source_id"],
                "market_import_batch",
                market_row["batch_id"],
                "market",
            )
        ]
        topic_context = None
        if command.topic_snapshot_id is not None:
            topic_row = (
                connection.execute(
                    select(topic_snapshots).where(
                        topic_snapshots.c.id == command.topic_snapshot_id
                    )
                )
                .mappings()
                .first()
            )
            if topic_row is None:
                raise NotFound("Topic snapshot not found")
            topic_archive = (
                connection.execute(
                    select(topic_import_batches).where(
                        topic_import_batches.c.id == topic_row["batch_id"]
                    )
                )
                .mappings()
                .one()
            )
            topic_batch = TopicBatch.model_validate_json(
                topic_archive["canonical_json"]
            )
            if topic_row["trade_date"] != command.trade_date:
                raise ValueError("Topic snapshot date mismatch")
            topic_context = (topic_row, topic_archive, topic_batch)
            dependencies.append(
                (
                    topic_row["source_id"],
                    "topic_import_batch",
                    topic_row["batch_id"],
                    "topic",
                )
            )
        grants = {
            source_id: self.market._source(connection, source_id)
            for source_id in sorted({dependency[0] for dependency in dependencies})
        }
        self.market._authorize(grants[market_row["source_id"]], market_batch)
        if source_has_active_withdrawal(
            connection, market_row["source_id"]
        ) or archive_was_withdrawn(
            connection,
            market_row["source_id"],
            "market_import_batch",
            market_row["batch_id"],
        ):
            raise Forbidden("Market archive was withdrawn")
        if topic_context is not None:
            topic_row, _topic_archive, topic_batch = topic_context
            self.topics._authorize(grants[topic_row["source_id"]], topic_batch)
            if source_has_active_withdrawal(
                connection, topic_row["source_id"]
            ) or archive_was_withdrawn(
                connection,
                topic_row["source_id"],
                "topic_import_batch",
                topic_row["batch_id"],
            ):
                raise Forbidden("Topic archive was withdrawn")
        return (market_row, market_archive, market_batch), topic_context, dependencies

    @staticmethod
    def _market_materialization(context) -> DomainMaterialization:
        _row, archive, batch = context
        overview = market_breadth(batch)
        return DomainMaterialization(
            payload=overview.model_dump(
                mode="json",
                include={
                    "advancing",
                    "declining",
                    "unchanged",
                    "returns",
                    "exclusions",
                },
            ),
            coverage=overview.coverage.model_dump(mode="json"),
            quality_flags=tuple(
                issue["code"] for issue in archive["quality_report"]["issues"]
            ),
        )

    @staticmethod
    def _topic_materialization(context) -> DomainMaterialization:
        row, archive, batch = context
        return DomainMaterialization(
            payload={
                "snapshot_id": str(row["id"]),
                "batch_id": str(row["batch_id"]),
                "source_version": batch.source_version,
            },
            coverage=archive["quality_report"].get("coverage", {}),
            quality_flags=tuple(
                issue["code"] for issue in archive["quality_report"]["issues"]
            ),
        )

    def publish(
        self,
        command: PublicationCommand,
        *,
        key: str,
        materializers: Mapping[str, Materializer] | None = None,
    ) -> Publication:
        _identifier(key)
        command = PublicationCommand.model_validate(command.model_dump())
        canonical = command.canonical_json()
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        current_stage = "validate"
        result_id = None
        try:
            with self.database.engine.begin() as connection:
                self.market._lock(connection, "publication-key:" + key)
                attempts = (
                    connection.execute(
                        select(publication_attempts)
                        .where(publication_attempts.c.idempotency_key == key)
                        .order_by(publication_attempts.c.created_at)
                    )
                    .mappings()
                    .all()
                )
                published_attempt = next(
                    (
                        attempt
                        for attempt in attempts
                        if attempt["status"] == "published"
                    ),
                    None,
                )
                if published_attempt is not None:
                    if published_attempt["command_hash"] != digest:
                        raise Conflict(
                            "Idempotency key already belongs to another command"
                        )
                    result_id = published_attempt["publication_id"]
                else:
                    if any(attempt["command_hash"] != digest for attempt in attempts):
                        raise Conflict(
                            "Idempotency key already belongs to another command"
                        )
                    self.market._lock(
                        connection,
                        "publication-date:" + command.trade_date.isoformat(),
                    )
                    market_context, topic_context, dependencies = self._validate_inputs(
                        connection, command
                    )
                    overrides = dict(materializers or {})
                    references = command.input_references()
                    results = {}
                    for domain in DOMAIN_NAMES:
                        current_stage = domain
                        if domain in command.disabled_domains:
                            result = DomainMaterialization(status="disabled")
                        elif references[domain] is None:
                            result = DomainMaterialization(status="missing")
                        elif domain in overrides:
                            result = overrides[domain](connection, command)
                        elif domain == "market":
                            result = self._market_materialization(market_context)
                        elif domain == "topic":
                            result = self._topic_materialization(topic_context)
                        else:
                            raise ValueError(f"No materializer registered for {domain}")
                        results[domain] = result

                    previous = (
                        connection.execute(
                            select(publications)
                            .where(
                                publications.c.trade_date == command.trade_date,
                                publications.c.status == "published",
                            )
                            .order_by(publications.c.version.desc())
                            .limit(1)
                        )
                        .mappings()
                        .first()
                    )
                    now = self.clock()
                    publication_id = uuid4()
                    domains = {
                        domain: results[domain].status for domain in DOMAIN_NAMES
                    }
                    coverage = {
                        domain: dict(results[domain].coverage)
                        for domain in DOMAIN_NAMES
                    }
                    quality_flags = sorted(
                        {
                            flag
                            for result in results.values()
                            for flag in result.quality_flags
                        }
                    )
                    values = {
                        **command.model_dump(exclude={"disabled_domains"}),
                        "id": publication_id,
                        "version": previous["version"] + 1 if previous else 1,
                        "supersedes": previous["id"] if previous else None,
                        "status": "draft",
                        "domains": domains,
                        "coverage": coverage,
                        "quality_flags": quality_flags,
                        "command_json": canonical,
                        "command_hash": digest,
                        "created_at": now,
                        "published_at": None,
                    }
                    connection.execute(insert(publications).values(**values))
                    for domain in DOMAIN_NAMES:
                        result = results[domain]
                        connection.execute(
                            insert(publication_results).values(
                                publication_id=publication_id,
                                domain=domain,
                                input_id=references[domain],
                                status=result.status,
                                payload=result.model_dump(mode="json")["payload"],
                                coverage=result.model_dump(mode="json")["coverage"],
                                quality_flags=list(result.quality_flags),
                            )
                        )
                    for source_id, archive_type, archive_id, domain in dependencies:
                        connection.execute(
                            insert(source_dependencies).values(
                                id=uuid4(),
                                source_id=source_id,
                                archive_type=archive_type,
                                archive_id=archive_id,
                                derived_type=domain,
                                derived_id=publication_id,
                                publication_id=publication_id,
                                created_at=now,
                            )
                        )
                    current_stage = "publish"
                    connection.execute(
                        update(publications)
                        .where(publications.c.id == publication_id)
                        .values(status="published", published_at=now)
                    )
                    connection.execute(
                        insert(publication_attempts).values(
                            id=uuid4(),
                            idempotency_key=key,
                            command_hash=digest,
                            publication_id=publication_id,
                            status="published",
                            stage="published",
                            details={},
                            created_at=now,
                        )
                    )
                    result_id = publication_id
        except Conflict:
            raise
        except Exception as exc:
            with self.database.engine.begin() as connection:
                self.market._lock(connection, "publication-key:" + key)
                connection.execute(
                    insert(publication_attempts).values(
                        id=uuid4(),
                        idempotency_key=key,
                        command_hash=digest,
                        publication_id=None,
                        status="failed",
                        stage=current_stage,
                        details={"error_type": type(exc).__name__},
                        created_at=self.clock(),
                    )
                )
            raise
        assert result_id is not None
        record = self.repository.get_publication(result_id)
        assert record is not None
        return record
