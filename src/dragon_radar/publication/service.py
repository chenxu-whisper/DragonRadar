"""Atomic publication orchestration."""

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.domain.quality import grant_allows_display, validate_batch
from dragon_radar.news.repository import NewsRepository
from dragon_radar.news.service import NewsService
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
    PUBLICATION_SEQUENCE_LOCK,
    publication_attempts,
    publication_results,
    publications,
    source_dependencies,
)
from dragon_radar.readmodels.analysis import materialize_analysis
from dragon_radar.readmodels.market import materialize_market
from dragon_radar.readmodels.stock import materialize_stocks
from dragon_radar.regulation.repository import RegulationRepository
from dragon_radar.roles.repository import RoleRepository
from dragon_radar.roles.rules import validate_rule_definition
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
    _identifier,
)
from dragon_radar.topics.models import TopicBatch
from dragon_radar.topics.repository import TopicRepository
from dragon_radar.topics.tables import topic_import_batches, topic_snapshots

Materializer = Callable[[object, PublicationCommand], DomainMaterialization]


def _stored_json(value):
    """Store exact decimals as strings; routes restore numeric tokens."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _stored_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stored_json(item) for item in value]
    return value


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
        self.news = NewsRepository(database, self.market, clock=self.clock)
        self.regulations = RegulationRepository(database, self.market, clock=self.clock)
        self.roles = RoleRepository(database, self.market, clock=self.clock)
        self.repository = PublicationRepository(database, self.market, clock=self.clock)

    def _prepare_inputs(self, connection, command):
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
        news_context = None
        if command.news_input_id is not None:
            news_context = self.news.snapshot_context(
                connection,
                command.news_input_id,
                authorize=False,
            )
            dependencies.extend(NewsService.dependencies(news_context))
        regulation_context = None
        if command.regulation_input_id is not None:
            regulation_context = self.regulations.snapshot_contexts(
                connection,
                (command.regulation_input_id,),
                lock_snapshots=True,
            )[command.regulation_input_id]
            if (
                regulation_context["snapshot"].market_snapshot_id
                != command.market_snapshot_id
                or regulation_context["snapshot"].trade_date != command.trade_date
            ):
                raise Forbidden("Regulation snapshot does not match publication inputs")
            dependencies.extend(RegulationRepository.dependencies(regulation_context))
        return (
            (market_row, market_archive, market_batch),
            topic_context,
            news_context,
            regulation_context,
            dependencies,
        )

    def _authorize_inputs(
        self,
        connection,
        market_context,
        topic_context,
        news_context,
        grants,
    ) -> None:
        market_row, market_archive, market_batch = market_context
        market_now = self.market._authorize(
            grants[market_row["source_id"]], market_batch
        )
        if (
            not market_archive["quality_report"]["publishable"]
            or not validate_batch(market_batch, now=market_now).publishable
        ):
            raise QualityRejected("Market batch failed the quality gate")
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
        if news_context is not None:
            for row in news_context["rows"]:
                embedded = row["embedded_grant"]
                if not all(
                    grant_allows_display(
                        grant,
                        source_id=row["source_id"],
                        now=self.clock(),
                    )
                    for grant in (
                        grants[row["source_id"]],
                        SourceGrant.model_validate(embedded),
                    )
                ):
                    raise Forbidden("News source is not locally authorized")
                if source_has_active_withdrawal(
                    connection, row["source_id"]
                ) or archive_was_withdrawn(
                    connection,
                    row["source_id"],
                    "news_article_revision",
                    row["article_revision_id"],
                ):
                    raise Forbidden("News revision was withdrawn")

    @staticmethod
    def _market_materialization(context, topic_context=None) -> DomainMaterialization:
        _row, archive, batch = context
        topic_batch = topic_context[2] if topic_context is not None else None
        payload = materialize_market(batch, topic_batch)
        payload["analysis"] = materialize_analysis(batch, topic_batch)
        payload["stocks"] = materialize_stocks(batch)
        wire_coverage = payload["overview"]["breadth"]["coverage"]
        coverage = {
            "expected": wire_coverage["expected"],
            "valid": wire_coverage["valid"],
            "legal_excluded": wire_coverage["excluded"],
            "missing": wire_coverage["missing"],
        }
        return DomainMaterialization(
            payload=_stored_json(payload),
            coverage=coverage,
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

    def _role_materialization(
        self,
        connection,
        command: PublicationCommand,
        grants,
    ) -> tuple[DomainMaterialization, tuple[tuple[str, str, UUID, str], ...]]:
        row = self.roles._role_rows(connection, (command.role_input_id,))[
            command.role_input_id
        ]
        if (
            row["market_snapshot_id"] != command.market_snapshot_id
            or row["topic_snapshot_id"] != command.topic_snapshot_id
            or row["trade_date"] != command.trade_date
        ):
            raise Forbidden("Role run does not match publication inputs")
        validate_rule_definition(row["rule_version"], row["rule_json"])
        if hashlib.sha256(row["rule_json"].encode()).hexdigest() != row["rule_hash"]:
            raise Forbidden("Role run rule identity is invalid")
        payload, role_dependencies = self.roles.payload_with_dependencies(
            row["id"],
            connection=connection,
            grants=grants,
        )
        statuses = [
            status for topic in payload["topics"] for status in topic["statuses"]
        ]
        coverage = {
            "expected": sum(status["expected"] for status in statuses),
            "valid": sum(status["valid"] for status in statuses),
            "legal_excluded": sum(status["legal_excluded"] for status in statuses),
            "missing": sum(status["missing"] for status in statuses),
        }
        return DomainMaterialization(payload=payload, coverage=coverage), tuple(
            (*dependency, "role") for dependency in role_dependencies
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
        result_record = None
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
                    authoritative_publication = (
                        connection.execute(
                            select(
                                publications.c.id,
                                publications.c.status,
                                publications.c.command_hash,
                            ).where(
                                publications.c.id == published_attempt["publication_id"]
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if (
                        published_attempt["command_hash"] != digest
                        or authoritative_publication is None
                        or authoritative_publication["status"] != "published"
                        or authoritative_publication["command_hash"]
                        != published_attempt["command_hash"]
                    ):
                        raise Conflict(
                            "Idempotency key already belongs to another command"
                        )
                    result_id = published_attempt["publication_id"]
                else:
                    if any(attempt["command_hash"] != digest for attempt in attempts):
                        raise Conflict(
                            "Idempotency key already belongs to another command"
                        )
                    self.market._lock(connection, PUBLICATION_SEQUENCE_LOCK)
                    self.market._lock(
                        connection,
                        "publication-date:" + command.trade_date.isoformat(),
                    )
                    (
                        market_context,
                        topic_context,
                        news_context,
                        regulation_context,
                        dependencies,
                    ) = self._prepare_inputs(connection, command)
                    overrides = dict(materializers or {})
                    references = command.input_references()
                    source_ids = {dependency[0] for dependency in dependencies}
                    if (
                        command.role_input_id is not None
                        and "role" not in command.disabled_domains
                    ):
                        source_ids.update(
                            self.roles.payload_source_ids(
                                connection,
                                command.role_input_id,
                            )
                        )
                    grants = self.roles._lock_sources(connection, source_ids)
                    self._authorize_inputs(
                        connection,
                        market_context,
                        topic_context,
                        news_context,
                        grants,
                    )
                    results = {}
                    role_dependencies = ()
                    for domain in DOMAIN_NAMES:
                        current_stage = domain
                        if domain in command.disabled_domains:
                            result = DomainMaterialization(status="disabled")
                        elif references[domain] is None:
                            result = DomainMaterialization(status="missing")
                        elif domain in overrides:
                            result = overrides[domain](connection, command)
                        elif domain == "market":
                            result = self._market_materialization(
                                market_context, topic_context
                            )
                        elif domain == "topic":
                            result = self._topic_materialization(topic_context)
                        elif domain == "news":
                            result = NewsService.materialize_context(
                                news_context,
                                topic_context=topic_context,
                            )
                        elif domain == "regulation":
                            result = RegulationRepository.materialize_context(
                                regulation_context
                            )
                        elif domain == "role":
                            result, role_dependencies = self._role_materialization(
                                connection,
                                command,
                                grants,
                            )
                        else:
                            raise ValueError(f"No materializer registered for {domain}")
                        results[domain] = result
                    dependencies.extend(role_dependencies)

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
                    result_record = Publication(
                        id=publication_id,
                        trade_date=command.trade_date,
                        version=values["version"],
                        supersedes=values["supersedes"],
                        status="published",
                        market_snapshot_id=command.market_snapshot_id,
                        topic_snapshot_id=command.topic_snapshot_id,
                        news_input_id=command.news_input_id,
                        regulation_input_id=command.regulation_input_id,
                        role_input_id=command.role_input_id,
                        domains=domains,
                        coverage=coverage,
                        quality_flags=tuple(quality_flags),
                        command_json=canonical,
                        published_at=now,
                    )
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
        if result_record is not None:
            return result_record
        record = self.repository.get_publication(result_id)
        assert record is not None
        return record
