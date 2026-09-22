"""Reset-facing role names and conservative evidence approval gates."""

from collections.abc import Collection
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
    approved_rule_versions: Collection[str] = RESET_APPROVED_RULE_VERSIONS,
) -> ResetRoleEvidence:
    """Project one historical evaluation through the reset approval boundary."""
    topic_status = evaluation.topic_status
    row = evaluation.candidates[0] if evaluation.candidates else None
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
        return ResetRoleEvidence(
            **common,
            status="insufficient",
            reasons=(*reasons, "rule_version_not_approved", "historical_research_only"),
        )
    if (
        topic_status.status == "pending_validation"
        and row is not None
        and row.status == "pending_validation"
    ):
        return ResetRoleEvidence(
            **common,
            status="waiting_verify",
            reasons=(*reasons, "pending_validation"),
        )
    if (
        topic_status.capability == "ready"
        and row is not None
        and row.status == "candidate"
    ):
        return ResetRoleEvidence(
            **common,
            status="candidate",
            rank=row.tie_rank,
            reasons=reasons,
        )
    return ResetRoleEvidence(
        **common,
        status="insufficient",
        reasons=(*reasons, "capability_not_formal_candidate"),
    )


__all__ = [
    "ROLE_NAMES",
    "ExternalEvidenceStatus",
    "ExternalRoleName",
    "ResetRoleEvidence",
    "adapt_role_evaluation",
]
