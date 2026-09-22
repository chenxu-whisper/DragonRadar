"""Explicit materialization of complete immutable role evidence trees."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from dragon_radar.roles.engine import (
    RelayPredecessorRun,
    _PreparedContext,
    _state_eligibility,
    evaluate_catchup,
    evaluate_core,
    evaluate_emotion,
    evaluate_follower,
    evaluate_relay,
    evaluate_trend,
)
from dragon_radar.roles.factors import adjusted_price
from dragon_radar.roles.history import (
    _comparison_reasons,
    build_limit_ladder,
    compare_runs,
)
from dragon_radar.roles.models import (
    PendingRelayValidation,
    RelayBaselinePrice,
    RelayObservationEligibility,
    RoleContext,
    RoleRunEvidence,
)
from dragon_radar.roles.repository import RoleRepository, RoleRunRecord, _record
from dragon_radar.roles.research_factors import research_role_factors
from dragon_radar.roles.rules import (
    RULE_DEFINITIONS,
    canonical_rule_json,
    validate_rule_definition,
)
from dragon_radar.roles.tables import role_materialization_attempts, role_runs
from dragon_radar.storage.repository import Conflict, Repository, _identifier, _uuid

ROLE_ORDER = ("trend", "core", "emotion", "catchup", "follower", "relay")


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _visible_topics(context: RoleContext):
    cutoff = context.knowledge_cutoff
    day = context.market.trade_date
    return tuple(
        sorted(
            (
                definition
                for definition in context.topic.topics
                if definition.available_at <= cutoff
                and definition.effective_from <= day
                and (definition.effective_to is None or day < definition.effective_to)
            ),
            key=lambda item: item.topic_id,
        )
    )


def _members(context: RoleContext, topic_id: str) -> tuple[str, ...]:
    cutoff = context.knowledge_cutoff
    day = context.market.trade_date
    return tuple(
        sorted(
            {
                membership.security_id
                for membership in context.topic.memberships
                if membership.topic_id == topic_id
                and membership.available_at <= cutoff
                and membership.effective_from <= day
                and (membership.effective_to is None or day < membership.effective_to)
            }
        )
    )


class RoleMaterializer:
    """Loads published snapshots, computes once, and atomically stores the tree."""

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
        self.repository = RoleRepository(
            database,
            self.market,
            clock=self.clock,
        )

    @staticmethod
    def _command_json(
        market_snapshot_id: UUID,
        topic_snapshot_id: UUID,
        rule_version: str,
        benchmark_id: str,
    ) -> str:
        return json.dumps(
            {
                "benchmark_id": benchmark_id,
                "market_snapshot_id": str(market_snapshot_id),
                "rule_version": rule_version,
                "topic_snapshot_id": str(topic_snapshot_id),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def materialize(
        self,
        *,
        market_snapshot_id: UUID | str,
        topic_snapshot_id: UUID | str,
        rule_version: str,
        benchmark_id: str,
        key: str,
    ) -> RoleRunRecord:
        market_snapshot_id = _uuid(market_snapshot_id)
        topic_snapshot_id = _uuid(topic_snapshot_id)
        _identifier(rule_version)
        _identifier(benchmark_id)
        _identifier(key)
        command_json = self._command_json(
            market_snapshot_id,
            topic_snapshot_id,
            rule_version,
            benchmark_id,
        )
        payload_hash = hashlib.sha256(command_json.encode()).hexdigest()
        stage = "validate"
        try:
            with self.database.engine.begin() as connection:
                self.repository._lock(
                    connection,
                    "role-materialization-key:" + key,
                )
                existing = (
                    connection.execute(
                        select(role_runs).where(role_runs.c.idempotency_key == key)
                    )
                    .mappings()
                    .first()
                )
                if existing is not None:
                    if existing["payload_hash"] != payload_hash:
                        raise Conflict(
                            "Idempotency key already belongs to another payload"
                        )
                    self.repository.authorized_row(connection, existing["id"])
                    return _record(existing)

                provisional = {
                    "market_snapshot_id": market_snapshot_id,
                    "topic_snapshot_id": topic_snapshot_id,
                }
                (
                    _market_row,
                    market_batch,
                    _topic_row,
                    topic_batch,
                ) = self.repository._context(connection, provisional)
                if market_batch.trade_date != topic_batch.trade_date:
                    raise ValueError("Role snapshots must have the same trade date")
                context = RoleContext(
                    market=market_batch,
                    topic=topic_batch,
                    rule_version=rule_version,
                )
                current_grants = tuple(
                    self.market._source(connection, source_id)
                    for source_id in sorted(
                        {market_batch.source_id, topic_batch.source_id}
                    )
                )
                rule_json = canonical_rule_json(rule_version)
                validate_rule_definition(rule_version, rule_json)
                rule_hash = hashlib.sha256(rule_json.encode()).hexdigest()
                run_id = uuid4()
                now = self.clock()
                previous_binding = self.repository.previous_published(
                    connection,
                    context,
                )
                previous_by_topic = {}
                previous_run_id = None
                if previous_binding is not None:
                    previous_row, activation_event_id = previous_binding
                    previous_run_id = previous_row["id"]
                    previous_by_topic = {
                        definition.topic_id: evidence
                        for definition in _visible_topics(context)
                        if (
                            evidence := self.repository.evidence(
                                connection,
                                previous_row,
                                definition.topic_id,
                                activation_event_id=activation_event_id,
                            )
                        )
                        is not None
                    }
                tree = self._tree(
                    run_id=run_id,
                    key=key,
                    payload_hash=payload_hash,
                    market_snapshot_id=market_snapshot_id,
                    topic_snapshot_id=topic_snapshot_id,
                    benchmark_id=benchmark_id,
                    rule_json=rule_json,
                    rule_hash=rule_hash,
                    context=context,
                    now=now,
                    previous_run_id=previous_run_id,
                    previous_by_topic=previous_by_topic,
                    current_grants=current_grants,
                )
                stage = "persist"
                self.repository.insert_tree(connection, tree)
                connection.execute(
                    insert(role_materialization_attempts).values(
                        id=uuid4(),
                        idempotency_key=key,
                        payload_hash=payload_hash,
                        role_run_id=run_id,
                        status="materialized",
                        stage="materialized",
                        details={},
                        created_at=now,
                    )
                )
                return RoleRunRecord(
                    id=run_id,
                    market_snapshot_id=market_snapshot_id,
                    topic_snapshot_id=topic_snapshot_id,
                    trade_date=context.market.trade_date,
                    rule_version=rule_version,
                    benchmark_id=benchmark_id,
                    status="materialized",
                    materialized_at=now,
                )
        except Conflict:
            raise
        except Exception as exc:
            with self.database.engine.begin() as connection:
                self.repository._lock(
                    connection,
                    "role-materialization-key:" + key,
                )
                connection.execute(
                    insert(role_materialization_attempts).values(
                        id=uuid4(),
                        idempotency_key=key,
                        payload_hash=payload_hash,
                        role_run_id=None,
                        status="failed",
                        stage=stage,
                        details={"error_type": type(exc).__name__},
                        created_at=self.clock(),
                    )
                )
            raise

    @staticmethod
    def _tree(
        *,
        run_id,
        key,
        payload_hash,
        market_snapshot_id,
        topic_snapshot_id,
        benchmark_id,
        rule_json,
        rule_hash,
        context,
        now,
        previous_run_id,
        previous_by_topic,
        current_grants,
    ):
        visibility = (
            "retrospective"
            if "retrospective" in {context.market.provenance, context.topic.provenance}
            else "contemporaneous"
        )
        tree = {
            "run": {
                "id": run_id,
                "idempotency_key": key,
                "payload_hash": payload_hash,
                "market_snapshot_id": market_snapshot_id,
                "topic_snapshot_id": topic_snapshot_id,
                "trade_date": context.market.trade_date,
                "knowledge_cutoff": context.knowledge_cutoff,
                "rule_version": context.rule_version,
                "rule_json": rule_json,
                "rule_hash": rule_hash,
                "benchmark_id": benchmark_id,
                "visibility_mode": visibility,
                "market_source_id": context.market.source_id,
                "market_source_version": context.market.source_version,
                "market_pool_version": context.market.pool_version,
                "adjustment_version": context.market.adjustment_version,
                "calendar_version": context.market.calendar_version,
                "topic_source_id": context.topic.source_id,
                "topic_source_version": context.topic.source_version,
                "taxonomy_version": context.topic.taxonomy_version,
                "membership_version": context.topic.membership_version,
                "previous_comparable_run_id": previous_run_id,
                "status": "draft",
                "created_at": now,
                "materialized_at": None,
            },
            "topics": [],
            "statuses": [],
            "results": [],
            "factors": [],
            "ladders": [],
            "change_summaries": [],
            "changes": [],
            "pending_validations": [],
        }
        evaluators = (
            ("trend", evaluate_trend),
            ("core", evaluate_core),
            ("emotion", evaluate_emotion),
            ("catchup", evaluate_catchup),
            ("follower", evaluate_follower),
        )
        for definition in _visible_topics(context):
            topic_id = definition.topic_id
            member_ids = _members(context, topic_id)
            member_identity = hashlib.sha256(
                json.dumps(
                    member_ids,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode()
            ).hexdigest()
            tree["topics"].append(
                {
                    "role_run_id": run_id,
                    "topic_id": topic_id,
                    "member_ids": list(member_ids),
                    "member_identity": member_identity,
                }
            )
            previous = previous_by_topic.get(topic_id)
            relay_predecessor = None
            if previous is not None:
                trend_evaluation = next(
                    (
                        evaluation
                        for evaluation in previous.evaluations
                        if evaluation.topic_status.role == "trend"
                    ),
                    None,
                )
                if trend_evaluation is not None:
                    relay_predecessor = RelayPredecessorRun(
                        role_run_id=previous.role_run_id,
                        activation_event_id=previous.activation_event_id,
                        trade_date=previous.trade_date,
                        active_run_count=1,
                        rule_version=previous.rule_version,
                        visibility_mode=previous.visibility_mode,
                        market_benchmark_definition=(
                            previous.market_benchmark_definition
                        ),
                        topic_id=previous.topic_id,
                        topic_source_id=previous.topic_source_id,
                        topic_source_version=previous.topic_source_version,
                        taxonomy_version=previous.taxonomy_version,
                        membership_version=previous.membership_version,
                        member_ids=previous.member_ids,
                        trend_evaluation=trend_evaluation,
                    )
            evaluations = [
                evaluator(context, topic_id) for _role, evaluator in evaluators
            ]
            evaluations.append(evaluate_relay(context, topic_id, relay_predecessor))
            result_ids = {}
            for evaluation in evaluations:
                status = evaluation.topic_status
                tree["statuses"].append(
                    {
                        "role_run_id": run_id,
                        "topic_id": topic_id,
                        "role": status.role,
                        "capability": status.capability,
                        "status": status.status,
                        "expected": status.expected,
                        "valid": status.valid,
                        "legal_excluded": status.legal_excluded,
                        "missing": status.missing,
                        "reasons": list(status.reasons),
                    }
                )
                for candidate in evaluation.candidates:
                    result_id = uuid4()
                    result_ids[(candidate.role, candidate.security_id)] = result_id
                    tree["results"].append(
                        {
                            "id": result_id,
                            "role_run_id": run_id,
                            "topic_id": topic_id,
                            "security_id": candidate.security_id,
                            "observation_at": context.knowledge_cutoff,
                            "role": candidate.role,
                            "rule_version": context.rule_version,
                            "internal_status": candidate.status,
                            "tie_rank": candidate.tie_rank,
                            "factors": _json_value(candidate.factors),
                            "reasons": list(candidate.reasons),
                        }
                    )
            RoleMaterializer._append_pending_validation(
                tree,
                run_id=run_id,
                context=context,
                topic_id=topic_id,
                member_ids=member_ids,
                evaluations=tuple(evaluations),
                result_ids=result_ids,
                previous=previous,
                now=now,
                current_grants=current_grants,
            )
            for security_id in member_ids:
                for observation in research_role_factors(
                    context.market,
                    security_id,
                    benchmark_id,
                    topic=context.topic,
                    topic_id=topic_id,
                ):
                    dumped = observation.model_dump(mode="python")
                    tree["factors"].append(
                        {
                            "role_run_id": run_id,
                            "topic_id": topic_id,
                            "security_id": security_id,
                            "factor_id": observation.factor_id,
                            "metric": observation.metric,
                            "window": observation.window,
                            "value": _json_value(observation.value),
                            "unit": observation.unit,
                            "coverage": _json_value(dumped["coverage"]),
                            "reasons": list(observation.reasons),
                            "metadata": _json_value(dict(observation.metadata)),
                        }
                    )
            for row in build_limit_ladder(context, topic_id):
                tree["ladders"].append(
                    {
                        "role_run_id": run_id,
                        "topic_id": topic_id,
                        "security_id": row.security_id,
                        "trade_date": row.trade_date,
                        "closing_limit_streak": row.closing_limit_streak,
                        "limit_hits_10": row.limit_hits_10,
                        "broken_board": row.broken_board,
                        "touched_not_sealed": row.touched_not_sealed,
                        "reasons": list(row.reasons),
                    }
                )
            RoleMaterializer._append_changes(
                tree,
                run_id=run_id,
                context=context,
                topic_id=topic_id,
                member_ids=member_ids,
                evaluations=tuple(evaluations),
                previous=previous,
            )
        return tree

    @staticmethod
    def _append_pending_validation(
        tree,
        *,
        run_id,
        context,
        topic_id,
        member_ids,
        evaluations,
        result_ids,
        previous,
        now,
        current_grants,
    ):
        relay = next(
            evaluation
            for evaluation in evaluations
            if evaluation.topic_status.role == "relay"
        )
        pending_rows = tuple(
            candidate
            for candidate in relay.candidates
            if candidate.status == "pending_validation"
        )
        if not pending_rows:
            return
        if len(pending_rows) != 1 or previous is None:
            raise ValueError("Relay pending result requires one published predecessor")
        previous_trend = next(
            evaluation
            for evaluation in previous.evaluations
            if evaluation.topic_status.role == "trend"
        )
        current_trend = next(
            evaluation
            for evaluation in evaluations
            if evaluation.topic_status.role == "trend"
        )
        old_candidates = tuple(
            candidate
            for candidate in previous_trend.candidates
            if candidate.status == "candidate" and candidate.tie_rank == 1
        )
        new_candidates = tuple(
            candidate
            for candidate in current_trend.candidates
            if candidate.status == "candidate" and candidate.tie_rank == 1
        )
        if len(old_candidates) != 1 or len(new_candidates) != 1:
            raise ValueError("Relay pending result requires unique trend cores")
        sessions = context.market.calendar.sessions
        observation_index = sessions.index(context.market.trade_date)
        validation_sessions = sessions[observation_index + 1 : observation_index + 6]
        if len(validation_sessions) != 5:
            raise ValueError("Relay pending result requires five future sessions")
        prepared = _PreparedContext.build(context, topic_id)
        eligibility = []
        baseline_prices = []
        baseline_member_ids = []
        for security_id in member_ids:
            state = prepared.state_on(security_id, context.market.trade_date)
            classification, reason = _state_eligibility(
                prepared,
                security_id,
                context.market.trade_date,
                check_listing=True,
            )
            if state is None or any(
                value is None
                for value in (
                    getattr(state, "security_type", None),
                    getattr(state, "board_category", None),
                    getattr(state, "listing_date", None),
                    getattr(state, "suspended", None),
                    getattr(state, "is_st", None),
                    getattr(state, "is_delisting", None),
                )
            ):
                eligibility_reason = "missing_identity_evidence"
            elif classification == "valid":
                eligibility_reason = None
            elif reason and "listing history" in reason:
                eligibility_reason = "insufficient_listing_history"
            else:
                eligibility_reason = "ineligible_security_state"
            eligibility.append(
                RelayObservationEligibility(
                    security_id=security_id,
                    eligible=classification == "valid",
                    eligibility_reason=eligibility_reason,
                    security_type=getattr(state, "security_type", None),
                    board_category=getattr(state, "board_category", None),
                    listing_date=getattr(state, "listing_date", None),
                    suspended=getattr(state, "suspended", None),
                    is_st=getattr(state, "is_st", None),
                    is_delisting=getattr(state, "is_delisting", None),
                    exchange=(
                        state.exchange
                        if state is not None
                        else context.market.calendar.exchange
                    ),
                    source_id=(
                        state.source_id
                        if state is not None
                        else context.market.source_id
                    ),
                    pool_version=(
                        state.pool_version
                        if state is not None
                        else context.market.pool_version
                    ),
                )
            )
            if classification != "valid":
                continue
            bar = prepared.bar_on(security_id, context.market.trade_date)
            if bar is None or state.suspended is not False:
                raise ValueError("Relay baseline price evidence is unavailable")
            baseline_member_ids.append(security_id)
            baseline_prices.append(
                RelayBaselinePrice(
                    security_id=security_id,
                    adjusted_price_raw=adjusted_price(
                        bar.close,
                        bar.adjustment_factor,
                    ),
                    observation_suspended=False,
                )
            )
        old_security_id = old_candidates[0].security_id
        new_security_id = new_candidates[0].security_id
        if not {old_security_id, new_security_id}.issubset(baseline_member_ids):
            raise ValueError("Relay core identities must be eligible baseline members")
        benchmark_definition = RULE_DEFINITIONS[context.rule_version]["roles"]["trend"][
            "required_inputs"
        ]["market_benchmark"]["definition"]
        observation_run = RoleRunEvidence(
            role_run_id=str(run_id),
            previous_comparable_run_id=previous.role_run_id,
            context=context,
            topic_id=topic_id,
            visibility_mode=(
                "retrospective"
                if "retrospective"
                in {context.market.provenance, context.topic.provenance}
                else "contemporaneous"
            ),
            market_benchmark_definition=benchmark_definition,
            topic_source_id=context.topic.source_id,
            topic_source_version=context.topic.source_version,
            taxonomy_version=context.topic.taxonomy_version,
            membership_version=context.topic.membership_version,
            member_ids=member_ids,
            evaluations=evaluations,
            verification_status="passed",
            activation_event_id=str(run_id),
            active_run_count=1,
        )
        pending_result_id = result_ids[("relay", pending_rows[0].security_id)]
        pending = PendingRelayValidation(
            pending_result_id=str(pending_result_id),
            observation_run_id=str(run_id),
            observation_run=observation_run,
            observation_date=context.market.trade_date,
            topic_id=topic_id,
            old_security_id=old_security_id,
            new_security_id=new_security_id,
            observation_member_ids=member_ids,
            observation_eligibility=tuple(eligibility),
            baseline_member_ids=tuple(baseline_member_ids),
            baseline_prices=tuple(baseline_prices),
            membership_version=context.topic.membership_version,
            market_source_id=context.market.source_id,
            adjustment_version=context.market.adjustment_version,
            pool_version=context.market.pool_version,
            expected_security_ids=tuple(sorted(context.market.expected_security_ids)),
            validation_sessions=validation_sessions,
            calendar_sessions=context.market.calendar.sessions,
            rule_version=context.rule_version,
            visibility_mode=observation_run.visibility_mode,
            market_benchmark_definition=benchmark_definition,
            topic_source_id=context.topic.source_id,
            topic_source_version=context.topic.source_version,
            taxonomy_version=context.topic.taxonomy_version,
            exchange=context.market.calendar.exchange,
            calendar_version=context.market.calendar_version,
            operation_at=now,
            current_grants=current_grants,
        )
        tree["pending_validations"].append(
            {
                "id": uuid4(),
                "pending_result_id": pending_result_id,
                "observation_run_id": run_id,
                "topic_id": topic_id,
                "observation_date": context.market.trade_date,
                "validation_sessions": [
                    session.isoformat() for session in validation_sessions
                ],
                "baseline_evidence": pending.model_dump(mode="json"),
                "created_at": now,
            }
        )

    @staticmethod
    def _append_changes(
        tree,
        *,
        run_id,
        context,
        topic_id,
        member_ids,
        evaluations,
        previous,
    ):
        if previous is None:
            tree["change_summaries"].extend(
                {
                    "role_run_id": run_id,
                    "topic_id": topic_id,
                    "role": role,
                    "previous_role_run_id": None,
                    "comparison_status": "not_comparable",
                    "membership_changed": False,
                    "reasons": ["previous_comparable_run_unavailable"],
                }
                for role in ROLE_ORDER
            )
            return
        benchmark_definition = RULE_DEFINITIONS[context.rule_version]["roles"]["trend"][
            "required_inputs"
        ]["market_benchmark"]["definition"]
        current = RoleRunEvidence(
            role_run_id=str(run_id),
            previous_comparable_run_id=previous.role_run_id,
            context=context,
            topic_id=topic_id,
            visibility_mode=(
                "retrospective"
                if "retrospective"
                in {context.market.provenance, context.topic.provenance}
                else "contemporaneous"
            ),
            market_benchmark_definition=benchmark_definition,
            topic_source_id=context.topic.source_id,
            topic_source_version=context.topic.source_version,
            taxonomy_version=context.topic.taxonomy_version,
            membership_version=context.topic.membership_version,
            member_ids=member_ids,
            evaluations=evaluations,
            verification_status="pending",
        )
        changes = compare_runs(current, previous)
        global_reasons = _comparison_reasons(current, previous)
        membership_changed = (
            current.membership_version != previous.membership_version
            or frozenset(current.member_ids) != frozenset(previous.member_ids)
        )
        for change in changes:
            tree["changes"].append(
                {
                    "id": uuid4(),
                    "role_run_id": run_id,
                    "topic_id": change.topic_id,
                    "security_id": change.security_id,
                    "role": change.role,
                    "comparison_status": change.comparison_status,
                    "change_type": change.change_type,
                    "membership_changed": change.membership_changed,
                    "reasons": list(change.reasons),
                }
            )
        evaluations_by_role = {
            evaluation.topic_status.role: evaluation for evaluation in evaluations
        }
        previous_by_role = {
            evaluation.topic_status.role: evaluation
            for evaluation in previous.evaluations
        }
        for role in ROLE_ORDER:
            role_changes = tuple(change for change in changes if change.role == role)
            reasons = global_reasons
            if any(
                evaluation is None or evaluation.topic_status.status == "insufficient"
                for evaluation in (
                    evaluations_by_role.get(role),
                    previous_by_role.get(role),
                )
            ):
                reasons = tuple(dict.fromkeys((*reasons, "role_input_insufficient")))
            if role_changes:
                comparison_status = role_changes[0].comparison_status
                reasons = tuple(
                    dict.fromkeys(
                        reason for change in role_changes for reason in change.reasons
                    )
                )
            else:
                comparison_status = "not_comparable" if reasons else "comparable"
            tree["change_summaries"].append(
                {
                    "role_run_id": run_id,
                    "topic_id": topic_id,
                    "role": role,
                    "previous_role_run_id": UUID(previous.role_run_id),
                    "comparison_status": comparison_status,
                    "membership_changed": membership_changed,
                    "reasons": list(reasons),
                }
            )


__all__ = ["RoleMaterializer"]
