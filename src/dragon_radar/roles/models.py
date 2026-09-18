"""Immutable contracts for deterministic role evaluation."""

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from dragon_radar.domain.models import (
    ExactDecimal,
    FrozenModel,
    Identifier,
    MarketBatch,
    UTCDateTime,
)
from dragon_radar.roles.rules import RULE_DEFINITIONS
from dragon_radar.topics.models import TopicBatch

RoleName = Literal["trend", "core", "emotion", "catchup", "follower", "relay"]
RoleCapability = Literal["ready", "simplified", "watch_only", "pending_validation"]
RoleRunStatus = Literal[
    "ready",
    "simplified",
    "watch_only",
    "pending_validation",
    "insufficient",
    "no_candidate",
]
CandidateStatus = Literal[
    "candidate",
    "simplified_candidate",
    "eligible_non_candidate",
    "watch",
    "pending_validation",
    "excluded",
]

ROLE_CAPABILITIES: Mapping[RoleName, RoleCapability] = MappingProxyType(
    {
        "trend": "ready",
        "core": "ready",
        "emotion": "simplified",
        "catchup": "simplified",
        "follower": "watch_only",
        "relay": "pending_validation",
    }
)
ALLOWED_RESULT_STATUSES: Mapping[RoleName, frozenset[CandidateStatus]] = (
    MappingProxyType(
        {
            "trend": frozenset({"candidate", "eligible_non_candidate", "excluded"}),
            "core": frozenset({"candidate", "eligible_non_candidate", "excluded"}),
            "emotion": frozenset(
                {"simplified_candidate", "eligible_non_candidate", "excluded"}
            ),
            "catchup": frozenset(
                {"simplified_candidate", "eligible_non_candidate", "excluded"}
            ),
            "follower": frozenset({"watch", "excluded"}),
            "relay": frozenset({"pending_validation", "excluded"}),
        }
    )
)
PRIMARY_RESULT_STATUS: Mapping[RoleCapability, CandidateStatus] = MappingProxyType(
    {
        "ready": "candidate",
        "simplified": "simplified_candidate",
        "watch_only": "watch",
        "pending_validation": "pending_validation",
    }
)


def _canonical_market_benchmark_definition(rule_version: str) -> str:
    return RULE_DEFINITIONS[rule_version]["roles"]["trend"]["required_inputs"][
        "market_benchmark"
    ]["definition"]


class _StrictFrozenRoleModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class RoleContext(_StrictFrozenRoleModel):
    """The two immutable snapshots and frozen rule selected for one evaluation."""

    market: MarketBatch
    topic: TopicBatch
    rule_version: Literal["role-v1", "role-test-v1"] = "role-v1"

    @model_validator(mode="after")
    def validate_trade_date_binding(self) -> Self:
        if self.market.trade_date != self.topic.trade_date:
            raise ValueError(
                "market.trade_date must equal topic.trade_date for role evaluation"
            )
        return self

    @property
    def knowledge_cutoff(self) -> UTCDateTime:
        return min(self.market.as_of, self.topic.as_of)


class RoleCandidate(_StrictFrozenRoleModel):
    """An evaluated security before dense ranking."""

    topic_id: Identifier
    security_id: Identifier
    role: RoleName
    status: CandidateStatus
    factors: tuple[ExactDecimal, ...]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_status_for_role(self) -> Self:
        if self.status not in ALLOWED_RESULT_STATUSES[self.role]:
            raise ValueError(
                f"status {self.status!r} is not allowed for role {self.role!r}"
            )
        return self


class RankedCandidate(RoleCandidate):
    """A candidate with its one-based dense business rank."""

    tie_rank: int = Field(ge=1)


class RoleTopicStatus(_StrictFrozenRoleModel):
    """Coverage and outcome for one topic-role pair, including empty results."""

    topic_id: Identifier
    role: RoleName
    capability: RoleCapability
    status: RoleRunStatus
    expected: int = Field(ge=0)
    valid: int = Field(ge=0)
    legal_excluded: int = Field(ge=0)
    missing: int = Field(ge=0)
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_count_partition(self) -> Self:
        if self.expected != self.valid + self.legal_excluded + self.missing:
            raise ValueError("expected must equal valid + legal_excluded + missing")
        expected_capability = ROLE_CAPABILITIES[self.role]
        if self.capability != expected_capability:
            raise ValueError(
                f"capability for role {self.role!r} must be {expected_capability!r}"
            )
        if self.status not in {
            expected_capability,
            "insufficient",
            "no_candidate",
        }:
            raise ValueError(
                f"status {self.status!r} is not allowed for role {self.role!r}"
            )
        if self.status == "no_candidate" and self.valid == 0:
            raise ValueError("no_candidate requires at least one valid member")
        return self


class RoleEvaluation(_StrictFrozenRoleModel):
    """One topic-role status and its immutable ranked result rows."""

    topic_status: RoleTopicStatus
    candidates: tuple[RankedCandidate, ...] = ()

    @model_validator(mode="after")
    def validate_candidate_context(self) -> Self:
        for candidate in self.candidates:
            if candidate.topic_id != self.topic_status.topic_id:
                raise ValueError("candidate topic_id must equal topic_status.topic_id")
            if candidate.role != self.topic_status.role:
                raise ValueError("candidate role must equal topic_status.role")

        security_ids = tuple(candidate.security_id for candidate in self.candidates)
        if len(set(security_ids)) != len(security_ids):
            raise ValueError("candidate security_id must be unique within evaluation")

        if len(self.candidates) > self.topic_status.expected:
            raise ValueError("result rows must not exceed topic_status.expected")

        excluded_rows = sum(
            candidate.status == "excluded" for candidate in self.candidates
        )
        if excluded_rows > self.topic_status.legal_excluded:
            raise ValueError(
                "excluded result rows must not exceed topic_status.legal_excluded"
            )

        calculable_rows = sum(
            candidate.status != "excluded" for candidate in self.candidates
        )
        if calculable_rows > self.topic_status.valid:
            raise ValueError(
                "calculable result rows must not exceed topic_status.valid"
            )

        result_statuses = {candidate.status for candidate in self.candidates}
        if self.topic_status.status == "insufficient":
            if result_statuses - {"excluded"}:
                raise ValueError(
                    "insufficient role evaluation allows only excluded evidence"
                )
            return self

        positive_status = PRIMARY_RESULT_STATUS[self.topic_status.capability]
        if self.topic_status.status == "no_candidate":
            if positive_status in result_statuses:
                raise ValueError(
                    "no_candidate role evaluation cannot contain positive output"
                )
            return self

        if positive_status not in result_statuses:
            raise ValueError(
                "successful role evaluation must contain matching positive output"
            )
        return self


class LimitRow(_StrictFrozenRoleModel):
    """One materialized security row in a topic's daily limit ladder."""

    trade_date: date
    topic_id: Identifier
    security_id: Identifier
    closing_limit_streak: int | None = Field(default=None, ge=0)
    limit_hits_10: int | None = Field(default=None, ge=0, le=10)
    broken_board: bool | None
    touched_not_sealed: bool | None
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_metric_consistency(self) -> Self:
        streak = self.closing_limit_streak
        hits = self.limit_hits_10
        if streak is not None and hits is not None and min(streak, 10) > hits:
            raise ValueError(
                "closing_limit_streak must be consistent with limit_hits_10"
            )
        if streak is not None and hits == 10 and streak < 10:
            raise ValueError(
                "ten limit hits require at least a ten-session closing streak"
            )
        if self.broken_board is True:
            if streak != 0:
                raise ValueError("broken_board requires a zero closing_limit_streak")
            if hits is not None and hits < 1:
                raise ValueError("broken_board requires a known recent limit hit")
        if self.touched_not_sealed is True and streak != 0:
            raise ValueError("touched_not_sealed requires a zero closing_limit_streak")
        return self


class RoleChange(_StrictFrozenRoleModel):
    """One role assignment comparison against the selected comparable run."""

    current_run_id: Identifier
    previous_comparable_run_id: Identifier | None
    topic_id: Identifier
    security_id: Identifier
    role: RoleName
    comparison_status: Literal["comparable", "not_comparable"]
    change_type: Literal["added", "exited", "tie_changed", "unchanged"] | None
    membership_changed: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_comparison_result(self) -> Self:
        if (
            self.comparison_status == "comparable"
            and self.previous_comparable_run_id is None
        ):
            raise ValueError("comparable previous_comparable_run_id is required")
        if (
            self.comparison_status == "comparable"
            and self.previous_comparable_run_id == self.current_run_id
        ):
            raise ValueError(
                "comparable previous_comparable_run_id must differ from current_run_id"
            )
        if self.comparison_status == "not_comparable" and self.change_type is not None:
            raise ValueError("not_comparable change_type must be None")
        if self.comparison_status == "not_comparable" and not self.reasons:
            raise ValueError("not_comparable reasons must not be empty")
        if self.comparison_status == "comparable" and self.change_type is None:
            raise ValueError("comparable change_type is required")
        return self


class RoleValidation(_StrictFrozenRoleModel):
    """Immutable post-window validation of one pending relay result."""

    pending_result_id: Identifier
    observation_run_id: Identifier
    topic_id: Identifier
    outcome: Literal["supported", "weakened", "refuted", "unavailable"]
    window_start: date
    window_end: date
    return_start: date
    return_end: date
    old_security_id: Identifier
    new_security_id: Identifier
    old_return_raw: ExactDecimal | None = None
    new_return_raw: ExactDecimal | None = None
    topic_median_raw: ExactDecimal | None = None
    input_run_ids: tuple[Identifier, ...]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_window_and_values(self) -> Self:
        if not (
            self.return_start < self.window_start < self.window_end
            and self.return_end == self.window_end
        ):
            raise ValueError("validation endpoints must describe T and T+1..T+5")
        if self.old_security_id == self.new_security_id:
            raise ValueError("validation old and new security IDs must differ")
        if len(set(self.input_run_ids)) != len(self.input_run_ids):
            raise ValueError("validation input_run_ids must not contain duplicates")
        values = (
            self.old_return_raw,
            self.new_return_raw,
            self.topic_median_raw,
        )
        if self.outcome == "unavailable":
            if any(value is not None for value in values):
                raise ValueError("unavailable validation requires all raw returns null")
            if not self.reasons or any(
                reason.startswith("outcome:") for reason in self.reasons
            ):
                raise ValueError(
                    "unavailable validation requires specific failure reasons"
                )
        elif any(value is None for value in values):
            raise ValueError("available validation outcomes require all raw returns")
        if self.outcome != "unavailable" and len(self.input_run_ids) != 5:
            raise ValueError("relay validation requires five input run IDs")
        if self.outcome != "unavailable":
            outcome_reasons = tuple(
                reason for reason in self.reasons if reason.startswith("outcome:")
            )
            if outcome_reasons != (f"outcome:{self.outcome}",):
                raise ValueError(
                    "available validation requires exactly one matching outcome reason"
                )
            if self.new_return_raw <= self.old_return_raw:
                expected_outcome = "refuted"
            elif self.new_return_raw > self.topic_median_raw:
                expected_outcome = "supported"
            else:
                expected_outcome = "weakened"
            if self.outcome != expected_outcome:
                raise ValueError(
                    "outcome must match old_return_raw, new_return_raw, "
                    "and topic_median_raw"
                )
        return self


class RoleRunEvidence(_StrictFrozenRoleModel):
    """In-memory role run projection used by history and validation logic."""

    role_run_id: Identifier
    previous_comparable_run_id: Identifier | None = None
    context: RoleContext
    topic_id: Identifier
    visibility_mode: Literal["contemporaneous", "retrospective"]
    market_benchmark_definition: Identifier
    topic_source_id: Identifier
    topic_source_version: Identifier
    taxonomy_version: Identifier
    membership_version: Identifier
    member_ids: tuple[Identifier, ...]
    evaluations: tuple[RoleEvaluation, ...] = ()
    verification_status: Literal["passed"]
    activation_event_id: Identifier
    active_run_count: Literal[1] = 1

    @property
    def trade_date(self) -> date:
        return self.context.market.trade_date

    @property
    def rule_version(self) -> Literal["role-v1", "role-test-v1"]:
        return self.context.rule_version

    @model_validator(mode="after")
    def validate_run_identity(self) -> Self:
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("member_ids must not contain duplicates")
        if self.market_benchmark_definition != _canonical_market_benchmark_definition(
            self.rule_version
        ):
            raise ValueError(
                "market_benchmark_definition must match the frozen rule definition"
            )
        topic = self.context.topic
        cutoff = self.context.knowledge_cutoff
        expected_visibility = (
            "retrospective"
            if "retrospective" in {self.context.market.provenance, topic.provenance}
            else "contemporaneous"
        )
        projections = (
            (
                self.topic_source_id,
                topic.source_id,
                "topic_source_id must match context.topic.source_id",
            ),
            (
                self.topic_source_version,
                topic.source_version,
                "topic_source_version must match context.topic.source_version",
            ),
            (
                self.taxonomy_version,
                topic.taxonomy_version,
                "taxonomy_version must match context.topic.taxonomy_version",
            ),
            (
                self.membership_version,
                topic.membership_version,
                "membership_version must match context.topic.membership_version",
            ),
            (
                self.visibility_mode,
                expected_visibility,
                "visibility_mode must match context provenance",
            ),
        )
        for actual, expected, message in projections:
            if actual != expected:
                raise ValueError(message)
        market = self.context.market
        if (
            not market.calendar.exchange == topic.calendar.exchange == topic.exchange
            or market.calendar_version != topic.calendar_version
            or market.calendar_version != market.calendar.version
            or topic.calendar_version != topic.calendar.version
            or market.calendar.sessions != topic.calendar.sessions
        ):
            raise ValueError("context calendar identity must be consistent")
        definitions = tuple(
            definition
            for definition in topic.topics
            if definition.topic_id == self.topic_id
            and definition.available_at <= cutoff
            and definition.effective_from <= self.trade_date
            and (
                definition.effective_to is None
                or self.trade_date < definition.effective_to
            )
        )
        if len(definitions) != 1:
            raise ValueError(
                "topic_id must have exactly one visible effective definition"
            )
        context_member_ids = tuple(
            membership.security_id
            for membership in topic.memberships
            if membership.topic_id == self.topic_id
            and membership.available_at <= cutoff
            and membership.effective_from <= self.trade_date
            and (
                membership.effective_to is None
                or self.trade_date < membership.effective_to
            )
        )
        if set(self.member_ids) != set(context_member_ids) or len(
            context_member_ids
        ) != len(set(context_member_ids)):
            raise ValueError(
                "member_ids must match visible effective topic memberships"
            )
        evaluated_roles: set[RoleName] = set()
        for evaluation in self.evaluations:
            if evaluation.topic_status.topic_id != self.topic_id:
                raise ValueError("evaluation topic_id must match run topic_id")
            role = evaluation.topic_status.role
            if role in evaluated_roles:
                raise ValueError("each role may have at most one evaluation")
            evaluated_roles.add(role)
            if any(
                candidate.security_id not in self.member_ids
                for candidate in evaluation.candidates
            ):
                raise ValueError("evaluation candidates must belong to member_ids")
        return self


class RelayBaselinePrice(_StrictFrozenRoleModel):
    """One observation-time adjusted price frozen for relay validation."""

    security_id: Identifier
    adjusted_price_raw: ExactDecimal = Field(gt=0)
    observation_suspended: Literal[False]


class PendingRelayValidation(_StrictFrozenRoleModel):
    """The observation-time identity and fixed denominator for relay validation."""

    pending_result_id: Identifier
    observation_run_id: Identifier
    observation_date: date
    topic_id: Identifier
    old_security_id: Identifier
    new_security_id: Identifier
    observation_eligible_member_ids: tuple[Identifier, ...]
    baseline_member_ids: tuple[Identifier, ...]
    baseline_prices: tuple[RelayBaselinePrice, ...]
    membership_version: Identifier
    market_source_id: Identifier
    adjustment_version: Identifier
    pool_version: Identifier
    expected_security_ids: tuple[Identifier, ...]
    validation_sessions: tuple[date, date, date, date, date]
    calendar_sessions: tuple[date, ...]
    rule_version: Literal["role-v1", "role-test-v1"]
    visibility_mode: Literal["contemporaneous", "retrospective"]
    market_benchmark_definition: Identifier
    topic_source_id: Identifier
    topic_source_version: Identifier
    taxonomy_version: Identifier
    exchange: Identifier
    calendar_version: Identifier
    current_validation: RoleValidation | None = None

    @model_validator(mode="after")
    def validate_pending_identity(self) -> Self:
        if self.market_benchmark_definition != _canonical_market_benchmark_definition(
            self.rule_version
        ):
            raise ValueError(
                "market_benchmark_definition must match the frozen rule definition"
            )
        if (
            not self.expected_security_ids
            or tuple(sorted(set(self.expected_security_ids)))
            != self.expected_security_ids
        ):
            raise ValueError(
                "expected_security_ids must be nonempty, sorted, and unique"
            )
        if (
            not self.calendar_sessions
            or tuple(sorted(set(self.calendar_sessions))) != self.calendar_sessions
        ):
            raise ValueError("calendar_sessions must be nonempty, sorted, and unique")
        try:
            observation_index = self.calendar_sessions.index(self.observation_date)
        except ValueError as error:
            raise ValueError(
                "calendar_sessions must contain observation_date"
            ) from error
        expected_sessions = self.calendar_sessions[
            observation_index + 1 : observation_index + 6
        ]
        if len(expected_sessions) != 5 or self.validation_sessions != expected_sessions:
            raise ValueError(
                "validation_sessions must be the next five calendar_sessions "
                "after observation_date"
            )
        if len(set(self.baseline_member_ids)) != len(self.baseline_member_ids):
            raise ValueError("baseline_member_ids must not contain duplicates")
        if not self.baseline_member_ids:
            raise ValueError("baseline_member_ids must not be empty")
        if not self.observation_eligible_member_ids or len(
            set(self.observation_eligible_member_ids)
        ) != len(self.observation_eligible_member_ids):
            raise ValueError(
                "observation_eligible_member_ids must be nonempty and unique"
            )
        if set(self.observation_eligible_member_ids) != set(self.baseline_member_ids):
            raise ValueError(
                "observation_eligible_member_ids security IDs must match "
                "baseline_member_ids"
            )
        if not set(self.baseline_member_ids).issubset(self.expected_security_ids):
            raise ValueError(
                "baseline_member_ids must be a subset of expected_security_ids"
            )
        price_ids = tuple(price.security_id for price in self.baseline_prices)
        if len(set(price_ids)) != len(price_ids):
            raise ValueError("baseline price security IDs must not contain duplicates")
        if set(price_ids) != set(self.baseline_member_ids) or len(price_ids) != len(
            self.baseline_member_ids
        ):
            raise ValueError(
                "baseline price security IDs must correspond one-to-one "
                "with baseline members"
            )
        if self.old_security_id == self.new_security_id:
            raise ValueError("old and new security IDs must differ")
        if not {
            self.old_security_id,
            self.new_security_id,
        }.issubset(self.baseline_member_ids):
            raise ValueError("old and new security IDs must be baseline members")
        if self.current_validation is not None:
            validation = RoleValidation.model_validate(
                self.current_validation.model_dump()
            )
            expected = (
                (
                    validation.pending_result_id,
                    self.pending_result_id,
                ),
                (
                    validation.observation_run_id,
                    self.observation_run_id,
                ),
                (validation.topic_id, self.topic_id),
                (validation.old_security_id, self.old_security_id),
                (validation.new_security_id, self.new_security_id),
                (validation.return_start, self.observation_date),
                (validation.window_start, self.validation_sessions[0]),
                (validation.window_end, self.validation_sessions[-1]),
                (validation.return_end, self.validation_sessions[-1]),
            )
            if any(actual != required for actual, required in expected):
                raise ValueError(
                    "current_validation identity and endpoints must match pending"
                )
        return self
