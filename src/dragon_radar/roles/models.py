"""Immutable contracts for deterministic role evaluation."""

from collections.abc import Mapping
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
