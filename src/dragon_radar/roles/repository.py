"""Persistence and authorized reads for immutable role evidence."""

import json
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
from dragon_radar.roles.models import (
    PendingRelayValidation,
    RankedCandidate,
    RoleContext,
    RoleEvaluation,
    RoleRunEvidence,
    RoleTopicStatus,
    RoleValidation,
)
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
    role_validation_inputs,
    role_validations,
)
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import Forbidden, NotFound, Repository, _uuid
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

    def _context(self, connection, row):
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
        grants = {
            source_id: self.market._source(connection, source_id)
            for source_id in sorted({market_row["source_id"], topic_row["source_id"]})
        }
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
        return market_row, market_batch, topic_row, topic_batch

    def authorized_row(self, connection, role_run_id: UUID | str):
        role_run_id = _uuid(role_run_id)
        row = (
            connection.execute(select(role_runs).where(role_runs.c.id == role_run_id))
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Role run not found")
        if row["status"] != "materialized":
            raise Forbidden("Role run is not materialized")
        self._context(connection, row)
        return row

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
                .order_by(publications.c.version)
            )
            .mappings()
            .all()
        )
        role_run_ids = {binding["role_input_id"] for binding in bindings}
        if len(role_run_ids) != 1:
            return None
        role_run_id = role_run_ids.pop()
        row = self.authorized_row(connection, role_run_id)
        if row["trade_date"] != previous_date:
            raise Forbidden("Published predecessor role date is invalid")
        publication_id = bindings[-1]["id"]
        return row, publication_id

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
        if connection is None:
            with self.database.engine.begin() as owned:
                return self.payload(role_run_id, connection=owned)
        row = self.authorized_row(connection, role_run_id)
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

        def public(item, excluded):
            return {
                key: (
                    value.isoformat()
                    if isinstance(value, (date, datetime))
                    else str(value)
                    if isinstance(value, UUID)
                    else value
                )
                for key, value in item.items()
                if key not in excluded
            }

        return {
            "role_run_id": str(run_id),
            "trade_date": row["trade_date"].isoformat(),
            "rule_version": row["rule_version"],
            "benchmark_id": row["benchmark_id"],
            "topics": [
                {
                    "topic_id": topic["topic_id"],
                    "member_ids": topic["member_ids"],
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
                    "changes": [
                        public(summary, {"role_run_id", "topic_id"})
                        for summary in summaries
                        if summary["topic_id"] == topic["topic_id"]
                    ],
                }
                for topic in topics
            ],
        }

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
            self._lock(
                connection,
                f"role-validation:{pending_validation_id}",
            )
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
            self.authorized_row(connection, pending["observation_run_id"])
            for input_run_id in input_run_ids:
                self.authorized_row(connection, input_run_id)
            baseline = PendingRelayValidation.model_validate_json(
                json.dumps(pending["baseline_evidence"])
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


__all__ = ["RoleRepository", "RoleRunRecord"]
