"""Deterministic role formulas, rule definitions, and immutable contracts."""

from dragon_radar.roles.factors import (
    AnalysisUnavailable,
    adjusted_price,
    decimal_mean,
    decimal_median,
    knowledge_cutoff,
    maximum_drawdown,
    mean_absolute_deviation_rate,
    period_return,
    relative_return,
    topic_amount_share,
)
from dragon_radar.roles.models import (
    CandidateStatus,
    RankedCandidate,
    RoleCandidate,
    RoleCapability,
    RoleContext,
    RoleEvaluation,
    RoleName,
    RoleRunStatus,
    RoleTopicStatus,
)
from dragon_radar.roles.rules import (
    ROLE_RULE_SHA256,
    ROLE_RULE_VERSION,
    RULE_DEFINITIONS,
    canonical_rule_json,
    validate_rule_definition,
)

__all__ = [
    "ROLE_RULE_SHA256",
    "ROLE_RULE_VERSION",
    "RULE_DEFINITIONS",
    "AnalysisUnavailable",
    "CandidateStatus",
    "RankedCandidate",
    "RoleCandidate",
    "RoleCapability",
    "RoleContext",
    "RoleEvaluation",
    "RoleName",
    "RoleRunStatus",
    "RoleTopicStatus",
    "adjusted_price",
    "canonical_rule_json",
    "decimal_mean",
    "decimal_median",
    "knowledge_cutoff",
    "maximum_drawdown",
    "mean_absolute_deviation_rate",
    "period_return",
    "relative_return",
    "topic_amount_share",
    "validate_rule_definition",
]
