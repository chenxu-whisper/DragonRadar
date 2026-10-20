"""Persistence and authorized reads for immutable role evidence."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from dragon_radar.domain.models import FrozenModel
from dragon_radar.publication.repository import (
    archive_was_withdrawn,
    source_has_active_withdrawal,
)
from dragon_radar.publication.tables import publications
from dragon_radar.roles.history import validate_relay
from dragon_radar.roles.models import (
    PendingRelayValidation,
    RankedCandidate,
    RoleContext,
    RoleEvaluation,
    RoleRunEvidence,
    RoleTopicStatus,
    RoleValidation,
)
from dragon_radar.roles.rotation import materialize_run_rotation
from dragon_radar.roles.rules import RULE_DEFINITIONS
from dragon_radar.roles.tables import (
    role_change_summaries,
    role_changes,
    role_factor_observations,
    role_limit_rows,
    role_pending_validations,
    role_results,
    role_run_topics,
    role_runs,
    role_topic_statuses,
    role_validation_commands,
    role_validation_inputs,
    role_validations,
)
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    Repository,
    _identifier,
    _uuid,
)
from dragon_radar.topics.models import TopicBatch
from dragon_radar.topics.repository import TopicRepository
from dragon_radar.topics.tables import topic_import_batches, topic_snapshots


class RoleRunRecord(FrozenModel):
    id: UUID
    market_snapshot_id: UUID
    topic_snapshot_id: UUID
    trade_date: date
    rule_version: str
    benchmark_id: str
    status: Literal["draft", "materialized"]
    previous_comparable_run_id: UUID | None = None
    materialized_at: datetime | None = None


class RoleValidationBatch(FrozenModel):
    through_market_snapshot_id: UUID
    validation_ids: tuple[UUID, ...]
    mature_count: int
    immature_count: int


def _record(row: Mapping) -> RoleRunRecord:
    return RoleRunRecord(
        id=row["id"],
        market_snapshot_id=row["market_snapshot_id"],
        topic_snapshot_id=row["topic_snapshot_id"],
        trade_date=row["trade_date"],
        rule_version=row["rule_version"],
        benchmark_id=row["benchmark_id"],
        status=row["status"],
        previous_comparable_run_id=row["previous_comparable_run_id"],
        materialized_at=row["materialized_at"],
    )


class RoleRepository:
    """Stores complete role trees and never computes role decisions."""

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

    def _lock(self, connection, key: str) -> None:
        self.market._lock(connection, key)

    def _context_inputs(self, connection, row):
        market_row = (
            connection.execute(
                select(snapshots).where(snapshots.c.id == row["market_snapshot_id"])
            )
            .mappings()
            .first()
        )
        topic_row = (
            connection.execute(
                select(topic_snapshots).where(
                    topic_snapshots.c.id == row["topic_snapshot_id"]
                )
            )
            .mappings()
            .first()
        )
        if market_row is None or topic_row is None:
            raise Forbidden("Role run snapshot identity is invalid")
        market_archive = (
            connection.execute(
                select(import_batches).where(
                    import_batches.c.id == market_row["batch_id"]
                )
            )
            .mappings()
            .first()
        )
        topic_archive = (
            connection.execute(
                select(topic_import_batches).where(
                    topic_import_batches.c.id == topic_row["batch_id"]
                )
            )
            .mappings()
            .first()
        )
        if market_archive is None or topic_archive is None:
            raise Forbidden("Role run snapshot identity is invalid")
        market_batch = self.market._snapshot(market_row, market_archive).batch
        topic_batch = TopicBatch.model_validate_json(topic_archive["canonical_json"])
        return market_row, market_batch, topic_row, topic_batch

    def _lock_sources(self, connection, source_ids) -> dict:
        return {
            source_id: self.market._source(connection, source_id)
            for source_id in sorted(set(source_ids))
        }

    def _authorize_context(self, connection, context, grants) -> None:
        market_row, market_batch, topic_row, topic_batch = context
        self.market._authorize(grants[market_row["source_id"]], market_batch)
        self.topics._authorize(grants[topic_row["source_id"]], topic_batch)
        if (
            any(
                source_has_active_withdrawal(connection, source_id)
                for source_id in grants
            )
            or archive_was_withdrawn(
                connection,
                market_row["source_id"],
                "market_import_batch",
                market_row["batch_id"],
            )
            or archive_was_withdrawn(
                connection,
                topic_row["source_id"],
                "topic_import_batch",
                topic_row["batch_id"],
            )
        ):
            raise Forbidden("Role run inputs were withdrawn")

    def _context(self, connection, row):
        context = self._context_inputs(connection, row)
        grants = self._lock_sources(
            connection,
            (context[0]["source_id"], context[2]["source_id"]),
        )
        self._authorize_context(connection, context, grants)
        return context

    def _role_rows(self, connection, role_run_ids):
        run_ids = {_uuid(run_id) for run_id in role_run_ids}
        if not run_ids:
            return {}
        rows = {
            row["id"]: row
            for row in connection.execute(
                select(role_runs).where(role_runs.c.id.in_(run_ids))
            )
            .mappings()
            .all()
        }
        if set(rows) != run_ids:
            raise NotFound("Role run not found")
        if any(row["status"] != "materialized" for row in rows.values()):
            raise Forbidden("Role run is not materialized")
        return rows

    def _prepare_role_authorization(self, connection, role_run_ids):
        rows = self._role_rows(connection, role_run_ids)
        contexts = {
            run_id: self._context_inputs(connection, row)
            for run_id, row in rows.items()
        }
        source_ids = {
            source_id
            for context in contexts.values()
            for source_id in (context[0]["source_id"], context[2]["source_id"])
        }
        return rows, contexts, source_ids

    def _authorize_prepared_roles(self, connection, contexts, grants) -> None:
        for run_id in sorted(contexts, key=str):
            self._authorize_context(connection, contexts[run_id], grants)

    def authorized_row(self, connection, role_run_id: UUID | str):
        role_run_id = _uuid(role_run_id)
        rows, contexts, source_ids = self._prepare_role_authorization(
            connection, (role_run_id,)
        )
        grants = self._lock_sources(connection, source_ids)
        self._authorize_prepared_roles(connection, contexts, grants)
        return rows[role_run_id]

    def _market_snapshot_input(self, connection, snapshot_id: UUID):
        snapshot_row = (
            connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id))
            .mappings()
            .first()
        )
        if snapshot_row is None:
            raise NotFound("Market snapshot not found")
        archive = (
            connection.execute(
                select(import_batches).where(
                    import_batches.c.id == snapshot_row["batch_id"]
                )
            )
            .mappings()
            .first()
        )
        if archive is None:
            raise Forbidden("Market snapshot identity is invalid")
        target = self.market._snapshot(snapshot_row, archive).batch
        return snapshot_row, archive, target

    def _authorize_market_snapshot_input(self, connection, context, grants) -> None:
        snapshot_row, _archive, target = context
        self.market._authorize(grants[snapshot_row["source_id"]], target)
        if source_has_active_withdrawal(
            connection, snapshot_row["source_id"]
        ) or archive_was_withdrawn(
            connection,
            snapshot_row["source_id"],
            "market_import_batch",
            snapshot_row["batch_id"],
        ):
            raise Forbidden("Market snapshot input was withdrawn")

    def _authorized_market_snapshot_input(self, connection, snapshot_id: UUID):
        context = self._market_snapshot_input(connection, snapshot_id)
        grants = self._lock_sources(connection, (context[0]["source_id"],))
        self._authorize_market_snapshot_input(connection, context, grants)
        return context[2]

    def _validation_run_ids(
        self,
        connection,
        validation_ids: tuple[UUID, ...],
    ) -> tuple[set[UUID], set[UUID]]:
        if not validation_ids:
            return set(), set()
        validation_rows = (
            connection.execute(
                select(
                    role_validations.c.id,
                    role_validations.c.outcome,
                    role_pending_validations.c.observation_run_id,
                )
                .join(
                    role_pending_validations,
                    role_pending_validations.c.id
                    == role_validations.c.pending_validation_id,
                )
                .where(role_validations.c.id.in_(validation_ids))
            )
            .mappings()
            .all()
        )
        if {row["id"] for row in validation_rows} != set(validation_ids):
            raise Forbidden("Role validation identity is invalid")
        input_rows = (
            connection.execute(
                select(role_validation_inputs).where(
                    role_validation_inputs.c.validation_id.in_(validation_ids)
                )
            )
            .mappings()
            .all()
        )
        self._validate_validation_input_rows(validation_rows, input_rows)
        input_run_ids = {row["input_role_run_id"] for row in input_rows}
        observation_run_ids = {row["observation_run_id"] for row in validation_rows}
        return observation_run_ids, input_run_ids

    @staticmethod
    def _validate_validation_input_rows(validation_rows, input_rows) -> None:
        inputs_by_validation = defaultdict(list)
        validation_ids = {row["id"] for row in validation_rows}
        for row in input_rows:
            if row["validation_id"] not in validation_ids:
                raise Forbidden("Role validation inputs are invalid")
            inputs_by_validation[row["validation_id"]].append(row)
        for validation in validation_rows:
            inputs = inputs_by_validation[validation["id"]]
            positions = {row["position"] for row in inputs}
            run_ids = {row["input_role_run_id"] for row in inputs}
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
                raise Forbidden("Role validation inputs are invalid")

    def payload_source_ids(self, connection, role_run_id: UUID | str) -> set[str]:
        role_run_id = _uuid(role_run_id)
        self._role_rows(connection, (role_run_id,))
        validation_ids = tuple(
            connection.execute(
                select(role_validations.c.id)
                .join(
                    role_pending_validations,
                    role_pending_validations.c.id
                    == role_validations.c.pending_validation_id,
                )
                .where(role_pending_validations.c.observation_run_id == role_run_id)
            ).scalars()
        )
        observation_run_ids, input_run_ids = self._validation_run_ids(
            connection, validation_ids
        )
        _rows, _contexts, source_ids = self._prepare_role_authorization(
            connection,
            {role_run_id, *observation_run_ids, *input_run_ids},
        )
        return source_ids

    def _authorize_validation_replay(
        self,
        connection,
        validation_ids: tuple[UUID, ...],
        *,
        prepared=None,
        grants=None,
    ) -> None:
        if not validation_ids:
            return
        observation_run_ids, input_run_ids = self._validation_run_ids(
            connection, validation_ids
        )
        if prepared is None:
            _rows, contexts, source_ids = self._prepare_role_authorization(
                connection, observation_run_ids | input_run_ids
            )
            grants = self._lock_sources(connection, source_ids)
        else:
            contexts = prepared
        self._authorize_prepared_roles(
            connection,
            {
                run_id: contexts[run_id]
                for run_id in observation_run_ids | input_run_ids
            },
            grants,
        )

    def get(self, role_run_id: UUID | str) -> RoleRunRecord:
        with self.database.engine.begin() as connection:
            return _record(self.authorized_row(connection, role_run_id))

    def for_publication(self, publication_id: UUID | str) -> RoleRunRecord:
        publication_id = _uuid(publication_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(publications.c.status, publications.c.role_input_id).where(
                        publications.c.id == publication_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Publication not found")
            if row["status"] != "published" or row["role_input_id"] is None:
                raise Forbidden("Publication role evidence is not available")
            return _record(self.authorized_row(connection, row["role_input_id"]))

    def previous_published(
        self,
        connection,
        context: RoleContext,
    ) -> tuple[Mapping, UUID] | None:
        sessions = context.market.calendar.sessions
        try:
            current_index = sessions.index(context.market.trade_date)
        except ValueError:
            return None
        if current_index == 0:
            return None
        previous_date = sessions[current_index - 1]
        bindings = (
            connection.execute(
                select(publications.c.id, publications.c.role_input_id)
                .where(
                    publications.c.trade_date == previous_date,
                    publications.c.status == "published",
                    publications.c.role_input_id.is_not(None),
                )
                .order_by(publications.c.version.desc())
            )
            .mappings()
            .all()
        )
        for binding in bindings:
            try:
                row = self.authorized_row(connection, binding["role_input_id"])
            except Forbidden:
                continue
            if row["trade_date"] != previous_date:
                raise Forbidden("Published predecessor role date is invalid")
            return row, binding["id"]
        return None

    def evidence(
        self,
        connection,
        row: Mapping,
        topic_id: str,
        *,
        activation_event_id: UUID | None,
        previous_comparable_run_id: UUID | None = None,
    ) -> RoleRunEvidence | None:
        _market_row, market, _topic_row, topic = self._context(connection, row)
        topic_projection = (
            connection.execute(
                select(role_run_topics).where(
                    role_run_topics.c.role_run_id == row["id"],
                    role_run_topics.c.topic_id == topic_id,
                )
            )
            .mappings()
            .first()
        )
        if topic_projection is None:
            return None
        status_rows = (
            connection.execute(
                select(role_topic_statuses)
                .where(
                    role_topic_statuses.c.role_run_id == row["id"],
                    role_topic_statuses.c.topic_id == topic_id,
                )
                .order_by(role_topic_statuses.c.role)
            )
            .mappings()
            .all()
        )
        result_rows = (
            connection.execute(
                select(role_results)
                .where(
                    role_results.c.role_run_id == row["id"],
                    role_results.c.topic_id == topic_id,
                )
                .order_by(
                    role_results.c.role,
                    role_results.c.tie_rank,
                    role_results.c.security_id,
                )
            )
            .mappings()
            .all()
        )
        evaluations = tuple(
            RoleEvaluation(
                topic_status=RoleTopicStatus(
                    topic_id=status["topic_id"],
                    role=status["role"],
                    capability=status["capability"],
                    status=status["status"],
                    expected=status["expected"],
                    valid=status["valid"],
                    legal_excluded=status["legal_excluded"],
                    missing=status["missing"],
                    reasons=tuple(status["reasons"]),
                ),
                candidates=tuple(
                    RankedCandidate(
                        topic_id=result["topic_id"],
                        security_id=result["security_id"],
                        role=result["role"],
                        status=result["internal_status"],
                        factors=tuple(Decimal(value) for value in result["factors"]),
                        reasons=tuple(result["reasons"]),
                        tie_rank=result["tie_rank"],
                    )
                    for result in result_rows
                    if result["role"] == status["role"]
                ),
            )
            for status in status_rows
        )
        benchmark_definition = RULE_DEFINITIONS[row["rule_version"]]["roles"]["trend"][
            "required_inputs"
        ]["market_benchmark"]["definition"]
        return RoleRunEvidence(
            role_run_id=str(row["id"]),
            previous_comparable_run_id=(
                str(previous_comparable_run_id)
                if previous_comparable_run_id is not None
                else None
            ),
            context=RoleContext(
                market=market,
                topic=topic,
                rule_version=row["rule_version"],
            ),
            topic_id=topic_id,
            visibility_mode=row["visibility_mode"],
            market_benchmark_definition=benchmark_definition,
            topic_source_id=row["topic_source_id"],
            topic_source_version=row["topic_source_version"],
            taxonomy_version=row["taxonomy_version"],
            membership_version=row["membership_version"],
            member_ids=tuple(topic_projection["member_ids"]),
            evaluations=evaluations,
            verification_status="passed",
            activation_event_id=(
                str(activation_event_id) if activation_event_id is not None else None
            ),
            active_run_count=1 if activation_event_id is not None else 0,
        )

    def payload(self, role_run_id: UUID | str, *, connection=None) -> dict:
        payload, _dependencies = self.payload_with_dependencies(
            role_run_id, connection=connection
        )
        return payload

    def payload_with_dependencies(
        self,
        role_run_id: UUID | str,
        *,
        connection=None,
        grants=None,
    ) -> tuple[dict, tuple[tuple[str, str, UUID], ...]]:
        if connection is None:
            if grants is not None:
                raise ValueError("Preloaded grants require an existing connection")
            with self.database.engine.begin() as owned:
                return self.payload_with_dependencies(role_run_id, connection=owned)
        role_run_id = _uuid(role_run_id)
        row = self._role_rows(connection, (role_run_id,))[role_run_id]
        run_id = row["id"]
        topics = (
            connection.execute(
                select(role_run_topics)
                .where(role_run_topics.c.role_run_id == run_id)
                .order_by(role_run_topics.c.topic_id)
            )
            .mappings()
            .all()
        )
        statuses = (
            connection.execute(
                select(role_topic_statuses)
                .where(role_topic_statuses.c.role_run_id == run_id)
                .order_by(
                    role_topic_statuses.c.topic_id,
                    role_topic_statuses.c.role,
                )
            )
            .mappings()
            .all()
        )
        results = (
            connection.execute(
                select(role_results)
                .where(role_results.c.role_run_id == run_id)
                .order_by(
                    role_results.c.topic_id,
                    role_results.c.role,
                    role_results.c.tie_rank,
                    role_results.c.security_id,
                )
            )
            .mappings()
            .all()
        )
        factors = (
            connection.execute(
                select(role_factor_observations)
                .where(role_factor_observations.c.role_run_id == run_id)
                .order_by(
                    role_factor_observations.c.topic_id,
                    role_factor_observations.c.security_id,
                    role_factor_observations.c.factor_id,
                    role_factor_observations.c.metric,
                )
            )
            .mappings()
            .all()
        )
        ladders = (
            connection.execute(
                select(role_limit_rows)
                .where(role_limit_rows.c.role_run_id == run_id)
                .order_by(
                    role_limit_rows.c.topic_id,
                    role_limit_rows.c.security_id,
                )
            )
            .mappings()
            .all()
        )
        summaries = (
            connection.execute(
                select(role_change_summaries)
                .where(role_change_summaries.c.role_run_id == run_id)
                .order_by(
                    role_change_summaries.c.topic_id,
                    role_change_summaries.c.role,
                )
            )
            .mappings()
            .all()
        )
        change_rows = (
            connection.execute(
                select(role_changes)
                .where(role_changes.c.role_run_id == run_id)
                .order_by(
                    role_changes.c.topic_id,
                    role_changes.c.role,
                    role_changes.c.security_id,
                )
            )
            .mappings()
            .all()
        )
        pending_rows = (
            connection.execute(
                select(role_pending_validations)
                .where(role_pending_validations.c.observation_run_id == run_id)
                .order_by(
                    role_pending_validations.c.topic_id,
                    role_pending_validations.c.id,
                )
            )
            .mappings()
            .all()
        )
        pending_ids = tuple(item["id"] for item in pending_rows)
        validation_rows = (
            connection.execute(
                select(role_validations)
                .where(role_validations.c.pending_validation_id.in_(pending_ids))
                .order_by(
                    role_validations.c.pending_validation_id,
                    role_validations.c.revision,
                )
            )
            .mappings()
            .all()
            if pending_ids
            else []
        )
        validation_ids = tuple(item["id"] for item in validation_rows)
        validation_input_rows = (
            connection.execute(
                select(role_validation_inputs)
                .where(role_validation_inputs.c.validation_id.in_(validation_ids))
                .order_by(
                    role_validation_inputs.c.validation_id,
                    role_validation_inputs.c.position,
                )
            )
            .mappings()
            .all()
            if validation_ids
            else []
        )
        observation_run_ids, input_run_ids = self._validation_run_ids(
            connection, validation_ids
        )
        _rows, contexts, source_ids = self._prepare_role_authorization(
            connection,
            {run_id, *observation_run_ids, *input_run_ids},
        )
        if grants is None:
            grants = self._lock_sources(connection, source_ids)
        elif not source_ids <= set(grants):
            raise Forbidden("Role run source authorization is incomplete")
        self._authorize_prepared_roles(connection, contexts, grants)
        sessions = contexts[run_id][1].calendar.sessions
        predecessor_id = row["previous_comparable_run_id"]
        predecessor_row = None
        predecessor_topics: list = []
        predecessor_results: list = []
        if predecessor_id is not None:
            # The predecessor is the archived run explicitly named by the
            # materializer; loading it through _role_rows fails closed when it
            # is missing or not materialized. Only trend evidence feeds the
            # core-set comparison, the other five roles never enter rotation.
            predecessor_row = self._role_rows(connection, {predecessor_id})[
                predecessor_id
            ]
            predecessor_topics = (
                connection.execute(
                    select(role_run_topics)
                    .where(role_run_topics.c.role_run_id == predecessor_id)
                    .order_by(role_run_topics.c.topic_id)
                )
                .mappings()
                .all()
            )
            predecessor_results = (
                connection.execute(
                    select(role_results)
                    .where(
                        role_results.c.role_run_id == predecessor_id,
                        role_results.c.role == "trend",
                    )
                    .order_by(
                        role_results.c.topic_id,
                        role_results.c.tie_rank,
                        role_results.c.security_id,
                    )
                )
                .mappings()
                .all()
            )
        rotation_blocks = materialize_run_rotation(
            run_row=row,
            topic_rows=topics,
            result_rows=results,
            sessions=sessions,
            predecessor_row=predecessor_row,
            predecessor_topic_rows=predecessor_topics,
            predecessor_result_rows=predecessor_results,
        )
        pending_topics = {
            pending["id"]: pending["topic_id"] for pending in pending_rows
        }
        validation_topics = {
            validation["id"]: pending_topics[validation["pending_validation_id"]]
            for validation in validation_rows
        }
        validation_inputs_by_id = defaultdict(list)
        validation_inputs_by_topic = defaultdict(list)
        for item in validation_input_rows:
            validation_inputs_by_id[item["validation_id"]].append(item)
            validation_inputs_by_topic[validation_topics[item["validation_id"]]].append(
                item
            )
        validations_by_topic = defaultdict(list)
        for validation in validation_rows:
            validations_by_topic[validation_topics[validation["id"]]].append(validation)
        dependencies = {
            (
                context[0]["source_id"],
                "market_import_batch",
                context[0]["batch_id"],
            )
            for run_id, context in contexts.items()
            if run_id in input_run_ids
        }
        dependencies.update(
            (
                context[2]["source_id"],
                "topic_import_batch",
                context[2]["batch_id"],
            )
            for run_id, context in contexts.items()
            if run_id in input_run_ids
        )

        def public(item, excluded):
            return {
                key: (
                    value.isoformat()
                    if isinstance(value, (date, datetime))
                    else str(value)
                    if isinstance(value, Decimal)
                    else str(value)
                    if isinstance(value, UUID)
                    else value
                )
                for key, value in item.items()
                if key not in excluded
            }

        payload = {
            "role_run_id": str(run_id),
            "trade_date": row["trade_date"].isoformat(),
            "rule_version": row["rule_version"],
            "benchmark_id": row["benchmark_id"],
            "rotation_version": 1,
            "topics": [
                {
                    "topic_id": topic["topic_id"],
                    "member_ids": topic["member_ids"],
                    "rotation": rotation_blocks[topic["topic_id"]],
                    "statuses": [
                        public(status, {"role_run_id", "topic_id"})
                        for status in statuses
                        if status["topic_id"] == topic["topic_id"]
                    ],
                    "results": [
                        public(result, {"role_run_id", "observation_at"})
                        for result in results
                        if result["topic_id"] == topic["topic_id"]
                    ],
                    "factors": [
                        public(factor, {"role_run_id", "topic_id"})
                        for factor in factors
                        if factor["topic_id"] == topic["topic_id"]
                    ],
                    "ladder": [
                        public(ladder, {"role_run_id", "topic_id"})
                        for ladder in ladders
                        if ladder["topic_id"] == topic["topic_id"]
                    ],
                    "change_summaries": [
                        public(summary, {"role_run_id", "topic_id"})
                        for summary in summaries
                        if summary["topic_id"] == topic["topic_id"]
                    ],
                    "role_changes": [
                        public(change, {"role_run_id", "topic_id"})
                        for change in change_rows
                        if change["topic_id"] == topic["topic_id"]
                    ],
                    "pending_validations": [
                        public(
                            pending,
                            {
                                "baseline_evidence",
                                "observation_run_id",
                                "topic_id",
                            },
                        )
                        for pending in pending_rows
                        if pending["topic_id"] == topic["topic_id"]
                    ],
                    "role_validations": [
                        {
                            **public(validation, set()),
                            "validation_inputs": [
                                public(item, {"validation_id"})
                                for item in validation_inputs_by_id[validation["id"]]
                            ],
                        }
                        for validation in validations_by_topic[topic["topic_id"]]
                    ],
                    "role_validation_inputs": [
                        public(item, set())
                        for item in validation_inputs_by_topic[topic["topic_id"]]
                    ],
                }
                for topic in topics
            ],
        }
        return payload, tuple(
            sorted(dependencies, key=lambda item: tuple(map(str, item)))
        )

    @staticmethod
    def insert_tree(connection, tree: Mapping) -> None:
        connection.execute(insert(role_runs).values(**tree["run"]))
        for table, key in (
            (role_run_topics, "topics"),
            (role_topic_statuses, "statuses"),
            (role_results, "results"),
            (role_factor_observations, "factors"),
            (role_limit_rows, "ladders"),
            (role_change_summaries, "change_summaries"),
            (role_changes, "changes"),
            (role_pending_validations, "pending_validations"),
        ):
            values = tree.get(key, ())
            if values:
                connection.execute(insert(table), values)
        connection.execute(
            update(role_runs)
            .where(role_runs.c.id == tree["run"]["id"])
            .values(
                status="materialized",
                materialized_at=tree["run"]["created_at"],
            )
        )

    def append_validation(
        self,
        pending_validation_id: UUID | str,
        *,
        outcome: str,
        window_start: date,
        window_end: date,
        return_start: date,
        return_end: date,
        old_return_raw,
        new_return_raw,
        topic_median_raw,
        input_run_ids: tuple[UUID, ...],
        reasons: tuple[str, ...],
        supersedes: UUID | None = None,
    ) -> UUID:
        pending_validation_id = _uuid(pending_validation_id)
        input_run_ids = tuple(_uuid(run_id) for run_id in input_run_ids)
        if len(set(input_run_ids)) != len(input_run_ids):
            raise ValueError("Validation input run IDs must be unique")
        returns = (old_return_raw, new_return_raw, topic_median_raw)
        if outcome == "unavailable":
            if len(input_run_ids) > 5 or any(value is not None for value in returns):
                raise ValueError("Unavailable validation evidence is invalid")
        elif len(input_run_ids) != 5 or any(value is None for value in returns):
            raise ValueError("Available validation requires five complete inputs")
        with self.database.engine.begin() as connection:
            pending = (
                connection.execute(
                    select(role_pending_validations).where(
                        role_pending_validations.c.id == pending_validation_id
                    )
                )
                .mappings()
                .first()
            )
            if pending is None:
                raise NotFound("Pending role validation not found")
            _rows, contexts, source_ids = self._prepare_role_authorization(
                connection,
                {pending["observation_run_id"], *input_run_ids},
            )
            grants = self._lock_sources(connection, source_ids)
            self._lock(
                connection,
                f"role-validation:{pending_validation_id}",
            )
            self._authorize_prepared_roles(connection, contexts, grants)
            baseline = PendingRelayValidation.model_validate_json(
                json.dumps(pending["baseline_evidence"]),
                strict=False,
            )
            RoleValidation(
                pending_result_id=baseline.pending_result_id,
                observation_run_id=baseline.observation_run_id,
                topic_id=baseline.topic_id,
                outcome=outcome,
                window_start=window_start,
                window_end=window_end,
                return_start=return_start,
                return_end=return_end,
                old_security_id=baseline.old_security_id,
                new_security_id=baseline.new_security_id,
                old_return_raw=old_return_raw,
                new_return_raw=new_return_raw,
                topic_median_raw=topic_median_raw,
                input_run_ids=tuple(str(run_id) for run_id in input_run_ids),
                reasons=reasons,
            )
            revisions = (
                connection.execute(
                    select(role_validations)
                    .where(
                        role_validations.c.pending_validation_id
                        == pending_validation_id
                    )
                    .order_by(role_validations.c.revision)
                )
                .mappings()
                .all()
            )
            if supersedes is not None and (
                not revisions or revisions[-1]["id"] != supersedes
            ):
                raise ValueError("Validation correction must supersede latest revision")
            if supersedes is None and revisions:
                latest = revisions[-1]
                bound = tuple(
                    connection.execute(
                        select(role_validation_inputs.c.input_role_run_id)
                        .where(role_validation_inputs.c.validation_id == latest["id"])
                        .order_by(role_validation_inputs.c.position)
                    ).scalars()
                )
                if bound == input_run_ids:
                    repeated = {
                        "outcome": outcome,
                        "window_start": window_start,
                        "window_end": window_end,
                        "return_start": return_start,
                        "return_end": return_end,
                        "old_return_raw": old_return_raw,
                        "new_return_raw": new_return_raw,
                        "topic_median_raw": topic_median_raw,
                        "reasons": list(reasons),
                    }
                    if all(latest[key] == value for key, value in repeated.items()):
                        return latest["id"]
                    raise ValueError(
                        "Validation payload conflicts with existing binding"
                    )
                raise ValueError("Validation correction requires supersedes")
            validation_id = uuid4()
            connection.execute(
                insert(role_validations).values(
                    id=validation_id,
                    pending_validation_id=pending_validation_id,
                    revision=len(revisions) + 1,
                    supersedes=supersedes,
                    outcome=outcome,
                    window_start=window_start,
                    window_end=window_end,
                    return_start=return_start,
                    return_end=return_end,
                    old_return_raw=old_return_raw,
                    new_return_raw=new_return_raw,
                    topic_median_raw=topic_median_raw,
                    reasons=list(reasons),
                    created_at=self.clock(),
                )
            )
            if input_run_ids:
                connection.execute(
                    insert(role_validation_inputs),
                    [
                        {
                            "validation_id": validation_id,
                            "position": position,
                            "input_role_run_id": run_id,
                        }
                        for position, run_id in enumerate(input_run_ids)
                    ],
                )
            return validation_id

    def validate_pending(
        self,
        through_market_snapshot_id: UUID | str,
        *,
        key: str,
    ) -> RoleValidationBatch:
        through_market_snapshot_id = _uuid(through_market_snapshot_id)
        _identifier(key)
        payload_hash = hashlib.sha256(
            json.dumps(
                {"through_market_snapshot_id": str(through_market_snapshot_id)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.database.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(role_validation_commands).where(
                        role_validation_commands.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise Conflict("Idempotency key already belongs to another payload")
                validation_ids = tuple(
                    UUID(value) for value in existing["validation_ids"]
                )
                target_context = self._market_snapshot_input(
                    connection, existing["through_market_snapshot_id"]
                )
                observation_ids, input_ids = self._validation_run_ids(
                    connection, validation_ids
                )
                _rows, contexts, source_ids = self._prepare_role_authorization(
                    connection, observation_ids | input_ids
                )
                source_ids.add(target_context[0]["source_id"])
                grants = self._lock_sources(connection, source_ids)
                self._lock(connection, f"role-validation-key:{key}")
                self._authorize_market_snapshot_input(
                    connection, target_context, grants
                )
                self._authorize_validation_replay(
                    connection,
                    validation_ids,
                    prepared=contexts,
                    grants=grants,
                )
                return RoleValidationBatch(
                    through_market_snapshot_id=existing["through_market_snapshot_id"],
                    validation_ids=validation_ids,
                    mature_count=existing["mature_count"],
                    immature_count=existing["immature_count"],
                )
            target_context = self._market_snapshot_input(
                connection, through_market_snapshot_id
            )
            target = target_context[2]
            pending_rows = (
                connection.execute(
                    select(role_pending_validations).order_by(
                        role_pending_validations.c.observation_date,
                        role_pending_validations.c.id,
                    )
                )
                .mappings()
                .all()
            )
            bindings = (
                connection.execute(
                    select(
                        publications.c.id,
                        publications.c.version,
                        publications.c.role_input_id,
                        publications.c.trade_date,
                    )
                    .where(
                        publications.c.status == "published",
                        publications.c.role_input_id.is_not(None),
                        publications.c.trade_date <= target.trade_date,
                    )
                    .order_by(
                        publications.c.trade_date,
                        publications.c.version.desc(),
                    )
                )
                .mappings()
                .all()
            )
            pending_entries = []
            role_run_ids = set()
            latest_by_pending = {}
            for pending_row in pending_rows:
                baseline = PendingRelayValidation.model_validate_json(
                    json.dumps(pending_row["baseline_evidence"]),
                    strict=False,
                )
                mature = not (
                    target.calendar.exchange != baseline.exchange
                    or target.calendar_version != baseline.calendar_version
                    or baseline.validation_sessions[-1] not in target.calendar.sessions
                    or target.trade_date not in target.calendar.sessions
                    or target.calendar.sessions.index(target.trade_date)
                    < target.calendar.sessions.index(baseline.validation_sessions[-1])
                )
                pending_entries.append((pending_row, baseline, mature))
                if not mature:
                    continue
                role_run_ids.add(pending_row["observation_run_id"])
                latest = (
                    connection.execute(
                        select(role_validations)
                        .where(
                            role_validations.c.pending_validation_id
                            == pending_row["id"]
                        )
                        .order_by(role_validations.c.revision.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                latest_by_pending[pending_row["id"]] = latest
                if latest is not None:
                    observations, inputs = self._validation_run_ids(
                        connection, (latest["id"],)
                    )
                    role_run_ids.update(observations | inputs)
                    continue
                role_run_ids.update(
                    binding["role_input_id"]
                    for binding in bindings
                    if binding["trade_date"] > baseline.observation_date
                )
            prepared_rows, contexts, source_ids = self._prepare_role_authorization(
                connection, role_run_ids
            )
            source_ids.add(target_context[0]["source_id"])
            source_ids.update(
                source_id
                for _pending_row, baseline, mature in pending_entries
                if mature
                for source_id in (
                    baseline.observation_run.context.market.source_id,
                    baseline.observation_run.context.topic.source_id,
                )
            )
            grants = self._lock_sources(connection, source_ids)
            self._lock(connection, f"role-validation-key:{key}")
            existing = (
                connection.execute(
                    select(role_validation_commands).where(
                        role_validation_commands.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise Conflict("Idempotency key already belongs to another payload")
                validation_ids = tuple(
                    UUID(value) for value in existing["validation_ids"]
                )
                self._authorize_market_snapshot_input(
                    connection, target_context, grants
                )
                self._authorize_validation_replay(
                    connection,
                    validation_ids,
                    prepared=contexts,
                    grants=grants,
                )
                return RoleValidationBatch(
                    through_market_snapshot_id=existing["through_market_snapshot_id"],
                    validation_ids=validation_ids,
                    mature_count=existing["mature_count"],
                    immature_count=existing["immature_count"],
                )
            self._authorize_market_snapshot_input(connection, target_context, grants)

            validation_ids: list[UUID] = []
            mature_count = 0
            immature_count = 0
            for pending_row, baseline, mature in pending_entries:
                if not mature:
                    immature_count += 1
                    continue
                mature_count += 1
                self._authorize_context(
                    connection,
                    contexts[pending_row["observation_run_id"]],
                    grants,
                )
                self._lock(
                    connection,
                    f"role-validation:{pending_row['id']}",
                )
                latest = (
                    connection.execute(
                        select(role_validations)
                        .where(
                            role_validations.c.pending_validation_id
                            == pending_row["id"]
                        )
                        .order_by(role_validations.c.revision.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if latest is not None:
                    prepared_latest = latest_by_pending[pending_row["id"]]
                    if prepared_latest is None or prepared_latest["id"] != latest["id"]:
                        raise Conflict(
                            "Role validation changed while acquiring source locks"
                        )
                    self._authorize_validation_replay(
                        connection,
                        (latest["id"],),
                        prepared=contexts,
                        grants=grants,
                    )
                    validation_ids.append(latest["id"])
                    continue

                selected_dates: set[date] = set()
                future_runs: list[RoleRunEvidence] = []
                authorization_failed = False
                for binding in bindings:
                    if (
                        binding["trade_date"] <= baseline.observation_date
                        or binding["trade_date"] in selected_dates
                    ):
                        continue
                    try:
                        run_row = prepared_rows[binding["role_input_id"]]
                        self._authorize_context(
                            connection,
                            contexts[binding["role_input_id"]],
                            grants,
                        )
                        evidence = self.evidence(
                            connection,
                            run_row,
                            baseline.topic_id,
                            activation_event_id=binding["id"],
                        )
                    except Forbidden:
                        authorization_failed = True
                        continue
                    if evidence is not None:
                        selected_dates.add(binding["trade_date"])
                        future_runs.append(evidence)

                source_ids = {
                    baseline.observation_run.context.market.source_id,
                    baseline.observation_run.context.topic.source_id,
                    *(run.context.market.source_id for run in future_runs),
                    *(run.context.topic.source_id for run in future_runs),
                }
                current_grants = []
                for source_id in sorted(source_ids):
                    current = grants[source_id]
                    if source_has_active_withdrawal(connection, source_id):
                        current = current.model_copy(update={"approved": False})
                    current_grants.append(current)
                current_pending = baseline.model_copy(
                    update={
                        "operation_at": self.clock(),
                        "current_grants": tuple(current_grants),
                    }
                )
                computed = validate_relay(current_pending, tuple(future_runs))
                if computed:
                    validation = computed[0]
                else:
                    validation = RoleValidation(
                        pending_result_id=baseline.pending_result_id,
                        observation_run_id=baseline.observation_run_id,
                        topic_id=baseline.topic_id,
                        outcome="unavailable",
                        window_start=baseline.validation_sessions[0],
                        window_end=baseline.validation_sessions[-1],
                        return_start=baseline.observation_date,
                        return_end=baseline.validation_sessions[-1],
                        old_security_id=baseline.old_security_id,
                        new_security_id=baseline.new_security_id,
                        input_run_ids=tuple(run.role_run_id for run in future_runs),
                        reasons=(
                            "authorization_unavailable"
                            if authorization_failed
                            else "validation_runs_missing",
                        ),
                    )
                validation_ids.append(
                    self._append_validation_record(
                        connection,
                        pending_row["id"],
                        validation,
                    )
                )

            command_id = uuid4()
            connection.execute(
                insert(role_validation_commands).values(
                    id=command_id,
                    idempotency_key=key,
                    payload_hash=payload_hash,
                    through_market_snapshot_id=through_market_snapshot_id,
                    validation_ids=[str(value) for value in validation_ids],
                    mature_count=mature_count,
                    immature_count=immature_count,
                    created_at=self.clock(),
                )
            )
            return RoleValidationBatch(
                through_market_snapshot_id=through_market_snapshot_id,
                validation_ids=tuple(validation_ids),
                mature_count=mature_count,
                immature_count=immature_count,
            )

    def _append_validation_record(
        self,
        connection,
        pending_validation_id: UUID,
        validation: RoleValidation,
        *,
        supersedes: UUID | None = None,
    ) -> UUID:
        revisions = (
            connection.execute(
                select(role_validations)
                .where(
                    role_validations.c.pending_validation_id == pending_validation_id
                )
                .order_by(role_validations.c.revision)
            )
            .mappings()
            .all()
        )
        if supersedes is not None and (
            not revisions or revisions[-1]["id"] != supersedes
        ):
            raise ValueError("Validation correction must supersede latest revision")
        if supersedes is None and revisions:
            raise ValueError("Validation correction requires supersedes")
        validation_id = uuid4()
        connection.execute(
            insert(role_validations).values(
                id=validation_id,
                pending_validation_id=pending_validation_id,
                revision=len(revisions) + 1,
                supersedes=supersedes,
                outcome=validation.outcome,
                window_start=validation.window_start,
                window_end=validation.window_end,
                return_start=validation.return_start,
                return_end=validation.return_end,
                old_return_raw=validation.old_return_raw,
                new_return_raw=validation.new_return_raw,
                topic_median_raw=validation.topic_median_raw,
                reasons=list(validation.reasons),
                created_at=self.clock(),
            )
        )
        input_run_ids = tuple(UUID(value) for value in validation.input_run_ids)
        if input_run_ids:
            connection.execute(
                insert(role_validation_inputs),
                [
                    {
                        "validation_id": validation_id,
                        "position": position,
                        "input_role_run_id": run_id,
                    }
                    for position, run_id in enumerate(input_run_ids)
                ],
            )
        return validation_id


__all__ = ["RoleRepository", "RoleRunRecord", "RoleValidationBatch"]
