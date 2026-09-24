"""Transactional append-only persistence for regulation evidence."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select

from dragon_radar.domain.models import MarketBatch
from dragon_radar.regulation.engine import (
    evaluate_rule,
    rule_is_applicable,
    run_regression_cases,
)
from dragon_radar.regulation.models import (
    RegulationCalculation,
    RegulationEvent,
    RegulationObservation,
    RegulationReviewRecord,
    RegulationRule,
    RegulationRuleStatus,
    RegulationSnapshot,
    RegulationTestCase,
    event_is_effective,
    production_gate_reasons,
)
from dragon_radar.regulation.tables import (
    regulation_calculations,
    regulation_commands,
    regulation_events,
    regulation_rule_statuses,
    regulation_rules,
    regulation_snapshot_events,
    regulation_snapshot_inputs,
    regulation_snapshot_rules,
    regulation_snapshots,
)
from dragon_radar.storage.models import import_batches, snapshots
from dragon_radar.storage.repository import (
    Conflict,
    NotFound,
    Repository,
    _identifier,
    _uuid,
)

REGULATION_SCHEMA = "dragon-radar.regulation"
REGULATION_SCHEMA_VERSION = 1


def _canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=str,
    )


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _rule(row: Mapping) -> RegulationRule:
    return RegulationRule(
        rule_id=row["rule_id"],
        rule_version=row["rule_version"],
        applicable_exchange=row["applicable_exchange"],
        applicable_board=row["applicable_board"],
        security_status=row["security_status"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        window_days=row["window_days"],
        direction=row["direction"],
        threshold=row["threshold"],
        warning_threshold=row["warning_threshold"],
        benchmark_index=row["benchmark_index"],
        normal_anomaly_count_mode=row["normal_anomaly_count_mode"],
        recompute_condition=row["recompute_condition"],
        calculation_method=row["calculation_method"],
        official_source_url=row["official_source_url"],
        review_switch=row["review_switch"],
        review_record=(
            RegulationReviewRecord.model_validate(row["review_record"])
            if row["review_record"] is not None
            else None
        ),
        test_cases=tuple(
            RegulationTestCase.model_validate(item) for item in row["test_cases"]
        ),
        regression_passed=row["regression_passed"],
        active=row["active"],
    )


def _event(row: Mapping) -> RegulationEvent:
    return RegulationEvent(
        event_id=row["event_id"],
        rule_id=row["rule_id"],
        rule_version=row["rule_version"],
        security_id=row["security_id"],
        event_type=row["event_type"],
        announcement_id=row["announcement_id"],
        announcement_url=row["announcement_url"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
    )


def _snapshot(row: Mapping) -> RegulationSnapshot:
    return RegulationSnapshot(
        id=row["id"],
        market_snapshot_id=row["market_snapshot_id"],
        trade_date=row["trade_date"],
        as_of=row["as_of"],
        created_at=row["created_at"],
    )


def _security_status(state) -> str:
    if state.suspended is True:
        return "suspended"
    if state.is_st is True:
        return "st"
    if state.is_delisting is True:
        return "delisting"
    if state.suspended is None or state.is_st is None or state.is_delisting is None:
        return "unknown"
    return "regular"


class RegulationRepository:
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

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Regulation clock must be timezone-aware")
        return value.astimezone(UTC)

    def _existing_command(self, connection, key: str, payload_hash: str):
        existing = (
            connection.execute(
                select(regulation_commands).where(
                    regulation_commands.c.idempotency_key == key
                )
            )
            .mappings()
            .first()
        )
        if existing is not None and existing["payload_hash"] != payload_hash:
            raise Conflict("Regulation key belongs to another payload")
        return existing

    def _record_command(
        self,
        connection,
        *,
        key: str,
        payload_hash: str,
        result_type: str,
        result_id,
        now: datetime,
    ) -> None:
        connection.execute(
            insert(regulation_commands).values(
                id=uuid4(),
                idempotency_key=key,
                payload_hash=payload_hash,
                result_type=result_type,
                result_id=str(result_id),
                created_at=now,
            )
        )

    @staticmethod
    def _stored_rule(rule: RegulationRule, record_id: UUID, now: datetime) -> dict:
        values = rule.model_dump(mode="json")
        values["id"] = record_id
        values["review_record"] = values["review_record"]
        values["test_cases"] = values["test_cases"]
        values["created_at"] = now
        return values

    def register_rule(self, rule: RegulationRule, *, key: str) -> RegulationRule:
        _identifier(key)
        rule = RegulationRule.model_validate(rule.model_dump())
        regression_failures = run_regression_cases(rule)
        if rule.active and (production_gate_reasons(rule) or regression_failures):
            raise ValueError("Active regulation rule is not production eligible")
        payload_hash = _digest(rule.model_dump(mode="json"))
        with self.database.engine.begin() as connection:
            self.market._lock(connection, f"regulation-command:{key}")
            self.market._lock(connection, "regulation-rules")
            now = self._now()
            existing = self._existing_command(connection, key, payload_hash)
            if existing is not None:
                if existing["result_type"] != "rule":
                    raise Conflict("Regulation key belongs to another operation")
                row = (
                    connection.execute(
                        select(regulation_rules).where(
                            regulation_rules.c.id == UUID(existing["result_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                return _rule(row)
            if connection.scalar(
                select(regulation_rules.c.id).where(
                    regulation_rules.c.rule_id == rule.rule_id,
                    regulation_rules.c.rule_version == rule.rule_version,
                )
            ):
                raise Conflict("Regulation rule version already exists")
            record_id = uuid4()
            connection.execute(
                insert(regulation_rules).values(
                    **self._stored_rule(rule, record_id, now)
                )
            )
            if rule.active:
                connection.execute(
                    insert(regulation_rule_statuses).values(
                        id=uuid4(),
                        rule_record_id=record_id,
                        status="active",
                        reason="initial_reviewed_release",
                        recorded_at=now,
                    )
                )
            self._record_command(
                connection,
                key=key,
                payload_hash=payload_hash,
                result_type="rule",
                result_id=record_id,
                now=now,
            )
        return rule

    def _rule_row(self, connection, rule_id: str, rule_version: str):
        row = (
            connection.execute(
                select(regulation_rules).where(
                    regulation_rules.c.rule_id == rule_id,
                    regulation_rules.c.rule_version == rule_version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Regulation rule not found")
        return row

    def _latest_status(self, connection, record_id: UUID):
        return (
            connection.execute(
                select(regulation_rule_statuses)
                .where(regulation_rule_statuses.c.rule_record_id == record_id)
                .order_by(regulation_rule_statuses.c.sequence.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )

    def _append_status(
        self,
        rule_id: str,
        rule_version: str,
        *,
        status: str,
        reason: str,
        key: str,
    ) -> RegulationRuleStatus:
        _identifier(rule_id)
        _identifier(rule_version)
        _identifier(reason)
        _identifier(key)
        payload = {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "status": status,
            "reason": reason,
        }
        payload_hash = _digest(payload)
        with self.database.engine.begin() as connection:
            self.market._lock(connection, f"regulation-command:{key}")
            self.market._lock(connection, "regulation-rules")
            now = self._now()
            existing = self._existing_command(connection, key, payload_hash)
            if existing is not None:
                if existing["result_type"] != "status":
                    raise Conflict("Regulation key belongs to another operation")
                row = (
                    connection.execute(
                        select(regulation_rule_statuses).where(
                            regulation_rule_statuses.c.id == UUID(existing["result_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                return RegulationRuleStatus(
                    id=row["id"],
                    rule_id=rule_id,
                    rule_version=rule_version,
                    status=row["status"],
                    reason=row["reason"],
                    recorded_at=row["recorded_at"],
                )
            rule_row = self._rule_row(connection, rule_id, rule_version)
            rule = _rule(rule_row)
            if status == "active":
                gate_reasons = tuple(
                    reason
                    for reason in production_gate_reasons(rule)
                    if reason != "rule_inactive"
                )
                if gate_reasons or run_regression_cases(rule):
                    raise ValueError("Regulation rule is not production eligible")
            status_id = uuid4()
            connection.execute(
                insert(regulation_rule_statuses).values(
                    id=status_id,
                    rule_record_id=rule_row["id"],
                    status=status,
                    reason=reason,
                    recorded_at=now,
                )
            )
            self._record_command(
                connection,
                key=key,
                payload_hash=payload_hash,
                result_type="status",
                result_id=status_id,
                now=now,
            )
        return RegulationRuleStatus(
            id=status_id,
            rule_id=rule_id,
            rule_version=rule_version,
            status=status,
            reason=reason,
            recorded_at=now,
        )

    def activate_rule(
        self,
        rule_id: str,
        rule_version: str,
        *,
        key: str,
    ) -> RegulationRuleStatus:
        return self._append_status(
            rule_id,
            rule_version,
            status="active",
            reason="reviewed_release",
            key=key,
        )

    def withdraw_rule(
        self,
        rule_id: str,
        rule_version: str,
        *,
        reason: str,
        key: str,
    ) -> RegulationRuleStatus:
        return self._append_status(
            rule_id,
            rule_version,
            status="withdrawn",
            reason=reason,
            key=key,
        )

    def append_event(self, event: RegulationEvent, *, key: str) -> RegulationEvent:
        _identifier(key)
        event = RegulationEvent.model_validate(event.model_dump())
        payload_hash = _digest(event.model_dump(mode="json"))
        with self.database.engine.begin() as connection:
            self.market._lock(connection, f"regulation-command:{key}")
            self.market._lock(connection, "regulation-rules")
            now = self._now()
            existing = self._existing_command(connection, key, payload_hash)
            if existing is not None:
                if (
                    existing["result_type"] != "event"
                    or existing["result_id"] != event.event_id
                ):
                    raise Conflict("Regulation key belongs to another operation")
                row = (
                    connection.execute(
                        select(regulation_events).where(
                            regulation_events.c.event_id == event.event_id
                        )
                    )
                    .mappings()
                    .one()
                )
                return _event(row)
            self._rule_row(connection, event.rule_id, event.rule_version)
            if connection.scalar(
                select(regulation_events.c.event_id).where(
                    regulation_events.c.event_id == event.event_id
                )
            ):
                raise Conflict("Regulation event already exists")
            connection.execute(
                insert(regulation_events).values(
                    **event.model_dump(mode="json"),
                    created_at=now,
                )
            )
            self._record_command(
                connection,
                key=key,
                payload_hash=payload_hash,
                result_type="event",
                result_id=event.event_id,
                now=now,
            )
        return event

    def _market_context(self, connection, snapshot_id: UUID):
        row = (
            connection.execute(select(snapshots).where(snapshots.c.id == snapshot_id))
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Market snapshot not found")
        archive = (
            connection.execute(
                select(import_batches).where(import_batches.c.id == row["batch_id"])
            )
            .mappings()
            .one()
        )
        market = MarketBatch.model_validate_json(archive["canonical_json"])
        current = self.market._source(connection, row["source_id"])
        self.market._authorize(current, market)
        return row, archive, market

    @staticmethod
    def _market_states_by_security(market: MarketBatch) -> dict:
        states_by_security = {}
        conflicted = set()
        for state in market.security_states:
            if (
                state.available_at > market.as_of
                or state.effective_from > market.trade_date
                or (
                    state.effective_to is not None
                    and market.trade_date >= state.effective_to
                )
            ):
                continue
            if state.security_id in states_by_security:
                conflicted.add(state.security_id)
            else:
                states_by_security[state.security_id] = state
        for security_id in conflicted:
            states_by_security[security_id] = None
        return states_by_security

    @staticmethod
    def _normalize_observation(
        market: MarketBatch,
        observation: RegulationObservation,
        states_by_security: Mapping,
    ) -> RegulationObservation:
        if observation.trade_date != market.trade_date:
            raise ValueError("Regulation observation date mismatch")
        state = states_by_security.get(observation.security_id)
        expected = observation.security_id in market.expected_security_ids
        identity_matches = (
            state is not None
            and state.exchange == observation.exchange
            and (state.board_category or "unknown") == observation.board
            and _security_status(state) == observation.security_status
        )
        if expected and identity_matches:
            return observation
        return RegulationObservation(
            **observation.model_dump(exclude={"computed_value", "input_state"}),
            computed_value=None,
            input_state="conflict",
        )

    def materialize(
        self,
        *,
        market_snapshot_id: UUID | str,
        observations: tuple[RegulationObservation, ...],
        key: str,
    ) -> RegulationSnapshot:
        market_snapshot_id = _uuid(market_snapshot_id)
        _identifier(key)
        observations = tuple(
            RegulationObservation.model_validate(item.model_dump())
            for item in observations
        )
        identities = tuple(
            (item.security_id, item.rule_id, item.rule_version) for item in observations
        )
        if len(set(identities)) != len(identities):
            raise ValueError(
                "Regulation observations must have unique security and rule identities"
            )
        command = {
            "market_snapshot_id": str(market_snapshot_id),
            "observations": sorted(
                (item.model_dump(mode="json") for item in observations),
                key=lambda item: (
                    item["security_id"],
                    item["rule_id"],
                    item["rule_version"],
                ),
            ),
        }
        payload_hash = _digest(command)
        with self.database.engine.begin() as connection:
            self.market._lock(connection, f"regulation-command:{key}")
            self.market._lock(connection, "regulation-rules")
            now = self._now()
            existing = self._existing_command(connection, key, payload_hash)
            if existing is not None:
                if existing["result_type"] != "snapshot":
                    raise Conflict("Regulation key belongs to another operation")
                row = (
                    connection.execute(
                        select(regulation_snapshots).where(
                            regulation_snapshots.c.id == UUID(existing["result_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                return _snapshot(row)
            market_row, _archive, market = self._market_context(
                connection, market_snapshot_id
            )
            states_by_security = self._market_states_by_security(market)
            supplied = tuple(
                self._normalize_observation(market, item, states_by_security)
                for item in observations
            )
            status_sequence_watermark = connection.scalar(
                select(func.coalesce(func.max(regulation_rule_statuses.c.sequence), 0))
            )
            rule_rows = (
                connection.execute(
                    select(regulation_rules)
                    .where(regulation_rules.c.created_at <= market.as_of)
                    .order_by(
                        regulation_rules.c.rule_id,
                        regulation_rules.c.rule_version,
                    )
                )
                .mappings()
                .all()
            )
            ranked_statuses = (
                select(
                    regulation_rule_statuses.c.rule_record_id,
                    regulation_rule_statuses.c.status,
                    func.row_number()
                    .over(
                        partition_by=regulation_rule_statuses.c.rule_record_id,
                        order_by=regulation_rule_statuses.c.sequence.desc(),
                    )
                    .label("rank"),
                )
                .where(
                    regulation_rule_statuses.c.sequence <= status_sequence_watermark,
                )
                .subquery()
            )
            latest_status_by_rule = {
                row["rule_record_id"]: row["status"]
                for row in connection.execute(
                    select(
                        ranked_statuses.c.rule_record_id,
                        ranked_statuses.c.status,
                    ).where(ranked_statuses.c.rank == 1)
                )
                .mappings()
                .all()
            }
            selected = []
            for row in rule_rows:
                latest_status = latest_status_by_rule.get(row["id"])
                if latest_status == "withdrawn":
                    continue
                selected.append((row, latest_status))
            supplied_by_identity = {
                (item.security_id, item.rule_id, item.rule_version): item
                for item in supplied
            }
            rule_rows_by_identity = {
                (row["rule_id"], row["rule_version"]): row for row in rule_rows
            }
            unknown_rule_inputs = {
                identity
                for identity in supplied_by_identity
                if identity[1:] not in rule_rows_by_identity
            }
            if unknown_rule_inputs:
                raise ValueError(
                    "Regulation observations must match known snapshot rules"
                )
            materialized_inputs_by_identity = {
                identity: (
                    rule_rows_by_identity[identity[1:]]["id"],
                    observation,
                )
                for identity, observation in supplied_by_identity.items()
            }
            expected_input_identities = set()
            for row, _latest in selected:
                rule = _rule(row)
                for security_id in market.expected_security_ids:
                    state = states_by_security.get(security_id)
                    expected = RegulationObservation(
                        rule_id=rule.rule_id,
                        rule_version=rule.rule_version,
                        security_id=security_id,
                        trade_date=market.trade_date,
                        exchange=state.exchange if state is not None else "unknown",
                        board=(
                            (state.board_category or "unknown")
                            if state is not None
                            else "unknown"
                        ),
                        security_status=(
                            _security_status(state) if state is not None else "unknown"
                        ),
                        computed_value=None,
                        input_state="missing",
                    )
                    if state is not None and not rule_is_applicable(rule, expected):
                        continue
                    identity = (security_id, rule.rule_id, rule.rule_version)
                    expected_input_identities.add(identity)
                    materialized_inputs_by_identity.setdefault(
                        identity,
                        (row["id"], expected),
                    )
            materialized_inputs = tuple(
                materialized_inputs_by_identity[identity]
                for identity in sorted(materialized_inputs_by_identity)
            )
            inputs_by_rule_record_id = defaultdict(list)
            for rule_record_id, observation in materialized_inputs:
                inputs_by_rule_record_id[rule_record_id].append(observation)
            event_rows = (
                connection.execute(
                    select(regulation_events)
                    .where(regulation_events.c.created_at <= market.as_of)
                    .order_by(
                        regulation_events.c.effective_from,
                        regulation_events.c.event_id,
                    )
                )
                .mappings()
                .all()
            )
            effective_events = [
                row
                for row in event_rows
                if event_is_effective(_event(row), market.trade_date)
            ]
            root = {
                "id": uuid4(),
                "idempotency_key": key,
                "command_hash": payload_hash,
                "market_snapshot_id": market_snapshot_id,
                "trade_date": market.trade_date,
                "as_of": market.as_of,
                "status_sequence_watermark": status_sequence_watermark,
                "created_at": now,
            }
            connection.execute(insert(regulation_snapshots).values(**root))
            rule_bindings = []
            for position, (row, latest_status) in enumerate(selected):
                active = (
                    latest_status == "active"
                    if latest_status is not None
                    else row["active"]
                )
                rule_bindings.append(
                    {
                        "snapshot_id": root["id"],
                        "rule_record_id": row["id"],
                        "position": position,
                        "active": active,
                    }
                )
            if rule_bindings:
                connection.execute(
                    insert(regulation_snapshot_rules),
                    rule_bindings,
                )
            input_values = []
            for rule_record_id, observation in materialized_inputs:
                values = observation.model_dump(
                    mode="json",
                    exclude={"rule_id", "rule_version"},
                )
                input_values.append(
                    {
                        "snapshot_id": root["id"],
                        "rule_record_id": rule_record_id,
                        **values,
                    }
                )
            if input_values:
                connection.execute(
                    insert(regulation_snapshot_inputs),
                    input_values,
                )
            calculation_values = []
            for row, latest_status in selected:
                active = (
                    latest_status == "active"
                    if latest_status is not None
                    else row["active"]
                )
                rule = _rule(row)
                for observation in inputs_by_rule_record_id[row["id"]]:
                    identity = (
                        observation.security_id,
                        observation.rule_id,
                        observation.rule_version,
                    )
                    if identity not in expected_input_identities:
                        continue
                    calculation = evaluate_rule(rule, observation, active=active)
                    calculation_values.append(
                        {
                            "id": uuid4(),
                            "snapshot_id": root["id"],
                            "rule_record_id": row["id"],
                            "security_id": observation.security_id,
                            "trade_date": observation.trade_date,
                            "computed_value": observation.computed_value,
                            "input_state": observation.input_state,
                            "state": calculation.state,
                            "reasons": list(calculation.reasons),
                        }
                    )
            if calculation_values:
                connection.execute(
                    insert(regulation_calculations),
                    calculation_values,
                )
            event_bindings = [
                {
                    "snapshot_id": root["id"],
                    "event_id": row["event_id"],
                    "position": position,
                }
                for position, row in enumerate(effective_events)
            ]
            if event_bindings:
                connection.execute(
                    insert(regulation_snapshot_events),
                    event_bindings,
                )
            self._record_command(
                connection,
                key=key,
                payload_hash=payload_hash,
                result_type="snapshot",
                result_id=root["id"],
                now=now,
            )
            root["market_source_id"] = market_row["source_id"]
            root["market_batch_id"] = market_row["batch_id"]
            return _snapshot(root)

    def snapshot_contexts(self, connection, snapshot_ids) -> dict[UUID, dict]:
        snapshot_ids = {_uuid(snapshot_id) for snapshot_id in snapshot_ids}
        if not snapshot_ids:
            return {}
        roots = {
            row["id"]: row
            for row in connection.execute(
                select(regulation_snapshots).where(
                    regulation_snapshots.c.id.in_(snapshot_ids)
                )
            )
            .mappings()
            .all()
        }
        if set(roots) != snapshot_ids:
            raise NotFound("Regulation snapshot not found")

        market_snapshot_ids = {row["market_snapshot_id"] for row in roots.values()}
        market_rows = {
            row["id"]: row
            for row in connection.execute(
                select(snapshots).where(snapshots.c.id.in_(market_snapshot_ids))
            )
            .mappings()
            .all()
        }
        market_batch_ids = {row["batch_id"] for row in market_rows.values()}
        market_archives = {
            row["id"]: row
            for row in connection.execute(
                select(import_batches).where(import_batches.c.id.in_(market_batch_ids))
            )
            .mappings()
            .all()
        }
        rule_bindings = (
            connection.execute(
                select(regulation_snapshot_rules)
                .where(regulation_snapshot_rules.c.snapshot_id.in_(snapshot_ids))
                .order_by(
                    regulation_snapshot_rules.c.snapshot_id,
                    regulation_snapshot_rules.c.position,
                )
            )
            .mappings()
            .all()
        )
        calculation_rows = (
            connection.execute(
                select(regulation_calculations)
                .where(regulation_calculations.c.snapshot_id.in_(snapshot_ids))
                .order_by(
                    regulation_calculations.c.snapshot_id,
                    regulation_calculations.c.security_id,
                    regulation_calculations.c.rule_record_id,
                )
            )
            .mappings()
            .all()
        )
        input_rows = (
            connection.execute(
                select(regulation_snapshot_inputs)
                .where(regulation_snapshot_inputs.c.snapshot_id.in_(snapshot_ids))
                .order_by(
                    regulation_snapshot_inputs.c.snapshot_id,
                    regulation_snapshot_inputs.c.security_id,
                    regulation_snapshot_inputs.c.rule_record_id,
                )
            )
            .mappings()
            .all()
        )
        rule_record_ids = {
            *(row["rule_record_id"] for row in rule_bindings),
            *(row["rule_record_id"] for row in calculation_rows),
            *(row["rule_record_id"] for row in input_rows),
        }
        rule_rows = (
            {
                row["id"]: row
                for row in connection.execute(
                    select(regulation_rules).where(
                        regulation_rules.c.id.in_(rule_record_ids)
                    )
                )
                .mappings()
                .all()
            }
            if rule_record_ids
            else {}
        )
        rules = {record_id: _rule(row) for record_id, row in rule_rows.items()}
        event_rows = (
            connection.execute(
                select(
                    regulation_snapshot_events.c.snapshot_id,
                    regulation_events,
                )
                .join(
                    regulation_events,
                    regulation_events.c.event_id
                    == regulation_snapshot_events.c.event_id,
                )
                .where(regulation_snapshot_events.c.snapshot_id.in_(snapshot_ids))
                .order_by(
                    regulation_snapshot_events.c.snapshot_id,
                    regulation_snapshot_events.c.position,
                )
            )
            .mappings()
            .all()
        )

        bindings_by_snapshot = defaultdict(list)
        calculations_by_snapshot = defaultdict(list)
        calculation_rows_by_snapshot = defaultdict(list)
        inputs_by_snapshot = defaultdict(list)
        events_by_snapshot = defaultdict(list)
        for row in rule_bindings:
            bindings_by_snapshot[row["snapshot_id"]].append(row)
        for row in calculation_rows:
            rule = rules[row["rule_record_id"]]
            calculation_rows_by_snapshot[row["snapshot_id"]].append(row)
            calculations_by_snapshot[row["snapshot_id"]].append(
                RegulationCalculation(
                    rule_id=rule.rule_id,
                    rule_version=rule.rule_version,
                    security_id=row["security_id"],
                    trade_date=row["trade_date"],
                    window_days=rule.window_days,
                    direction=rule.direction,
                    threshold=rule.threshold,
                    warning_threshold=rule.warning_threshold,
                    review_switch=rule.review_switch,
                    benchmark_index=rule.benchmark_index,
                    recompute_condition=rule.recompute_condition,
                    computed_value=row["computed_value"],
                    state=row["state"],
                    reasons=tuple(row["reasons"]),
                )
            )
        for row in input_rows:
            rule = rules[row["rule_record_id"]]
            inputs_by_snapshot[row["snapshot_id"]].append(
                RegulationObservation(
                    rule_id=rule.rule_id,
                    rule_version=rule.rule_version,
                    security_id=row["security_id"],
                    trade_date=row["trade_date"],
                    exchange=row["exchange"],
                    board=row["board"],
                    security_status=row["security_status"],
                    computed_value=row["computed_value"],
                    input_state=row["input_state"],
                )
            )
        for row in event_rows:
            events_by_snapshot[row["snapshot_id"]].append(_event(row))

        contexts = {}
        for snapshot_id, root in roots.items():
            market_row = market_rows[root["market_snapshot_id"]]
            market_archive = market_archives[market_row["batch_id"]]
            market = MarketBatch.model_validate_json(market_archive["canonical_json"])
            bindings = tuple(bindings_by_snapshot[snapshot_id])
            contexts[snapshot_id] = {
                "snapshot": _snapshot(root),
                "root": root,
                "market_source_id": market_row["source_id"],
                "market_batch_id": market_row["batch_id"],
                "expected_security_ids": market.expected_security_ids,
                "rule_bindings": bindings,
                "rules": tuple(
                    rules[binding["rule_record_id"]] for binding in bindings
                ),
                "calculations": tuple(calculations_by_snapshot[snapshot_id]),
                "calculation_rows": tuple(calculation_rows_by_snapshot[snapshot_id]),
                "inputs": tuple(inputs_by_snapshot[snapshot_id]),
                "events": tuple(events_by_snapshot[snapshot_id]),
            }
        return contexts

    def snapshot_context(self, snapshot_id: UUID | str) -> dict:
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            return self.snapshot_contexts(connection, (snapshot_id,))[snapshot_id]

    @staticmethod
    def materialize_context(context: dict):
        from dragon_radar.publication.models import DomainMaterialization

        calculations = [
            item.model_dump(mode="json") for item in context["calculations"]
        ]
        events = [item.model_dump(mode="json") for item in context["events"]]
        expected_security_ids = tuple(context["expected_security_ids"])
        calculation_identities = {
            (item.security_id, item.rule_id, item.rule_version)
            for item in context["calculations"]
        }
        inputs_by_security = {
            security_id: tuple(
                item
                for item in context["inputs"]
                if item.security_id == security_id
                and (
                    item.security_id,
                    item.rule_id,
                    item.rule_version,
                )
                in calculation_identities
            )
            for security_id in expected_security_ids
        }
        expected = len(expected_security_ids)
        legal_excluded = sum(not items for items in inputs_by_security.values())
        valid = sum(
            bool(items) and all(item.input_state == "complete" for item in items)
            for items in inputs_by_security.values()
        )
        completeness = {
            "expected": expected,
            "valid": valid,
            "legal_excluded": legal_excluded,
            "missing": expected - valid - legal_excluded,
        }
        rules = []
        for binding, rule in zip(
            context["rule_bindings"], context["rules"], strict=True
        ):
            gate_reasons = list(production_gate_reasons(rule))
            if binding["active"]:
                gate_reasons = [
                    reason for reason in gate_reasons if reason != "rule_inactive"
                ]
            else:
                gate_reasons.append("snapshot_binding_inactive")
            gate_reasons = list(dict.fromkeys(gate_reasons))
            rules.append(
                {
                    **rule.model_dump(mode="json"),
                    "production_enabled": not gate_reasons,
                    "production_gate_reasons": gate_reasons,
                }
            )
        return DomainMaterialization(
            payload={
                "schema": REGULATION_SCHEMA,
                "schema_version": REGULATION_SCHEMA_VERSION,
                "snapshot_id": str(context["snapshot"].id),
                "market_snapshot_id": str(context["snapshot"].market_snapshot_id),
                "trade_date": context["snapshot"].trade_date.isoformat(),
                "as_of": context["snapshot"].as_of.isoformat(),
                "completeness": completeness,
                "rules": rules,
                "calculations": calculations,
                "events": events,
            },
            coverage=completeness,
            quality_flags=(
                ("regulation_input_incomplete",) if completeness["missing"] else ()
            ),
        )

    @staticmethod
    def dependencies(context: dict) -> tuple[tuple[str, str, UUID, str], ...]:
        return (
            (
                context["market_source_id"],
                "market_import_batch",
                context["market_batch_id"],
                "regulation",
            ),
        )
