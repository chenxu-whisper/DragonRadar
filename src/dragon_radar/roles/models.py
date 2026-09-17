"""Immutable contracts for deterministic role evaluation."""

from typing import Literal

from pydantic import ConfigDict, Field

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


class RoleContext(FrozenModel):
    """The two immutable snapshots and frozen rule selected for one evaluation."""

    market: MarketBatch
    topic: TopicBatch
    rule_version: Literal["role-v1", "role-test-v1"] = "role-v1"

    @property
    def knowledge_cutoff(self) -> UTCDateTime:
        return min(self.market.as_of, self.topic.as_of)


class RoleCandidate(FrozenModel):
    """An evaluated security before dense ranking."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    topic_id: Identifier
    security_id: Identifier
    role: RoleName
    status: CandidateStatus
    factors: tuple[ExactDecimal, ...]
    reasons: tuple[str, ...] = ()


class RankedCandidate(RoleCandidate):
    """A candidate with its one-based dense business rank."""

    tie_rank: int = Field(ge=1)


class RoleTopicStatus(FrozenModel):
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


class RoleEvaluation(FrozenModel):
    """One topic-role status and its immutable ranked result rows."""

    topic_status: RoleTopicStatus
    candidates: tuple[RankedCandidate, ...] = ()
