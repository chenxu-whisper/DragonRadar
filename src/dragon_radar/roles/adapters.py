"""Reset-facing role names and conservative evidence approval gates."""

from collections.abc import Set
from typing import Literal

from pydantic import ConfigDict

from dragon_radar.domain.models import FrozenModel, Identifier
from dragon_radar.roles.models import RoleCapability, RoleEvaluation
from dragon_radar.roles.rules import RESET_APPROVED_RULE_VERSIONS, ROLE_NAMES

ExternalRoleName = Literal[
    "emotion_leader",
    "trend_leader",
    "zhongjun",
    "genfeng",
    "buZhang",
    "jieli",
]
ExternalEvidenceStatus = Literal["candidate", "insufficient", "waiting_verify"]


class ResetRoleEvidence(FrozenModel):
    """One reset projection without changing the historical role result."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    role: ExternalRoleName
    security_id: Identifier | None = None
    status: ExternalEvidenceStatus
    capability: RoleCapability
    internal_status: Identifier
    rule_version: Identifier
    rank: int | None = None
    reasons: tuple[str, ...] = ()


def adapt_role_evaluation(
    evaluation: RoleEvaluation,
    *,
    rule_version: str,
    approved_rule_versions: Set[str] = RESET_APPROVED_RULE_VERSIONS,
) -> tuple[ResetRoleEvidence, ...]:
    """Project every historical result row through the reset approval boundary."""
    if not isinstance(approved_rule_versions, Set) or any(
        not isinstance(version, str) for version in approved_rule_versions
    ):
        raise TypeError("approved_rule_versions must be a set of strings")
    topic_status = evaluation.topic_status
    rows = evaluation.candidates or (None,)
    result = []
    for row in rows:
        internal_status = row.status if row is not None else topic_status.status
        reasons = (*topic_status.reasons, *(row.reasons if row is not None else ()))
        common = {
            "role": ROLE_NAMES[topic_status.role],
            "security_id": row.security_id if row is not None else None,
            "capability": topic_status.capability,
            "internal_status": internal_status,
            "rule_version": rule_version,
        }
        if rule_version not in approved_rule_versions:
            evidence = ResetRoleEvidence(
                **common,
                status="insufficient",
                reasons=(
                    *reasons,
                    "rule_version_not_approved",
                    "historical_research_only",
                ),
            )
        elif (
            topic_status.status == "pending_validation"
            and row is not None
            and row.status == "pending_validation"
        ):
            evidence = ResetRoleEvidence(
                **common,
                status="waiting_verify",
                reasons=(*reasons, "pending_validation"),
            )
        elif (
            topic_status.capability == "ready"
            and row is not None
            and row.status == "candidate"
        ):
            evidence = ResetRoleEvidence(
                **common,
                status="candidate",
                rank=row.tie_rank,
                reasons=reasons,
            )
        else:
            evidence = ResetRoleEvidence(
                **common,
                status="insufficient",
                reasons=(*reasons, "capability_not_formal_candidate"),
            )
        result.append(evidence)
    return tuple(result)


__all__ = [
    "ROLE_NAMES",
    "ExternalEvidenceStatus",
    "ExternalRoleName",
    "ResetRoleEvidence",
    "adapt_role_evaluation",
]
