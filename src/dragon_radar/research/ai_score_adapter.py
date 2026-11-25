"""PIT-frozen independently approved replacement ordering, not prose scoring."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from dragon_radar.domain.models import Identifier
from dragon_radar.research.factor_adapters import Digest, exact_model
from dragon_radar.research.models import ResearchFactorId, StrictFrozenModel
from dragon_radar.research.result_contracts import decimal_string, require


class AIScoreAdapterApproval(StrictFrozenModel):
    adapter_id: Identifier
    method_version: Identifier
    base_factor_id: ResearchFactorId
    score_unit: Identifier
    score_formula_sha256: Digest
    evaluation_manifest_sha256: Digest
    approval_evidence_sha256: Digest
    model_version: Identifier
    prompt_version: Identifier
    retrieval_version: Identifier


class AIScoreEntry(StrictFrozenModel):
    trade_date: date
    security_id: Identifier
    base_factor_input_sha256: Digest
    score: Decimal | None
    unit: Identifier
    available_at: datetime
    source_evidence_sha256: Digest
    model_version: Identifier
    prompt_version: Identifier
    retrieval_version: Identifier
    reason: Identifier | None


class AIScoreArchive(StrictFrozenModel):
    schema_tag: Literal["dragon-radar.research-ai-scores/v1"] = Field(alias="schema")
    adapter_id: Identifier
    method_version: Identifier
    entries: tuple[AIScoreEntry, ...]


def evaluation_ready(value):
    fields = {
        "unique_labeled_items",
        "fact_support",
        "numeric_recompute",
        "hard_failures",
        "development_frozen_separated",
        "release_rollback_approved",
        "provider_use_permitted",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return False
    try:
        return (
            type(value["unique_labeled_items"]) is int
            and value["unique_labeled_items"] >= 100
            and Decimal("0.95") <= decimal_string(value["fact_support"]) <= 1
            and decimal_string(value["numeric_recompute"]) == 1
            and type(value["hard_failures"]) is int
            and value["hard_failures"] == 0
            and all(
                value[k] is True
                for k in (
                    "development_frozen_separated",
                    "release_rollback_approved",
                    "provider_use_permitted",
                )
            )
        )
    except (ValueError, TypeError):
        return False


def adapt_ai_scores(
    approval, raw, *, decisions, verify, evaluation, approved_missing_reasons
):
    descriptor = exact_model(AIScoreAdapterApproval, approval)
    archive = exact_model(AIScoreArchive, raw)
    require(
        evaluation_ready(evaluation)
        and verify(descriptor.evaluation_manifest_sha256, evaluation)
        and verify(descriptor.approval_evidence_sha256, approval)
        and verify(descriptor.score_formula_sha256, approval),
        "real_evaluation_inputs_unavailable",
    )
    require(
        archive.adapter_id == descriptor.adapter_id
        and archive.method_version == descriptor.method_version
        and len(archive.entries) == len(decisions) > 0,
        "ai_score_inputs_unavailable",
    )
    seen = set()
    for entry in archive.entries:
        key = (entry.trade_date, entry.security_id)
        decision = decisions.get(key)
        require(
            key not in seen
            and decision is not None
            and entry.available_at.tzinfo is not None
            and entry.available_at <= decision["cutoff"]
            and entry.base_factor_input_sha256 == decision["base_factor_input_sha256"]
            and entry.unit == descriptor.score_unit
            and (entry.model_version, entry.prompt_version, entry.retrieval_version)
            == (
                descriptor.model_version,
                descriptor.prompt_version,
                descriptor.retrieval_version,
            )
            and verify(entry.source_evidence_sha256, entry.model_dump(mode="json")),
            "ai_score_inputs_unavailable",
        )
        require(
            (
                entry.score is not None
                and entry.score.is_finite()
                and entry.reason is None
            )
            or (
                entry.score is None
                and entry.reason is not None
                and entry.reason in approved_missing_reasons
            )
        )
        seen.add(key)
    return archive.entries
