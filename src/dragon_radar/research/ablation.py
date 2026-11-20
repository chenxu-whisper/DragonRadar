"""Baseline/AI-uplift ablation comparison frame (Task 9).

Assembles same-pool, same-window, same-cost, same-seed legs. Without frozen
real evaluation inputs the AI variant is fixed to
``status=disabled, reason=real_evaluation_inputs_unavailable``; model output
is never fabricated into a return label.

The frame is intentionally inert: it freezes the *controlled comparison
contract* (identical pool/window/cost/seed on both legs) and records the
unchanged baseline run, but the ``ai_variant`` leg can never carry metrics,
equity, trades or an uplift number until a separate evaluation gate supplies
frozen real evaluation samples.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from dragon_radar.research.ledger import Metrics
from dragon_radar.research.models import (
    BacktestConfig,
    BacktestPeriod,
    CostModel,
    InputManifests,
    StrictFrozenModel,
)

RESEARCH_ABLATION_SCHEMA_VERSION = "dragon-radar.research-ablation/v1"

# The only admissible disabled reason: no frozen human-labelled evaluation
# set has passed the independent evaluation gate yet.
ABLATION_AI_DISABLED_REASON = "real_evaluation_inputs_unavailable"

AblationLegStatus = Literal["completed", "disabled"]
AblationDisabledReason = Literal["real_evaluation_inputs_unavailable"]
AblationLegName = Literal["baseline", "ai_variant"]


class AblationLegInputs(StrictFrozenModel):
    """The pool/window/cost/seed contract shared identically by both legs."""

    snapshot_id: UUID
    input_manifests: InputManifests
    period: BacktestPeriod
    cost_model: CostModel
    random_seed: Annotated[int, Field(ge=0, le=0xFFFFFFFF)]


class AblationLeg(StrictFrozenModel):
    """One ablation leg. A disabled leg structurally cannot carry numbers."""

    leg: AblationLegName
    status: AblationLegStatus
    inputs: AblationLegInputs
    metrics: Metrics | None = None
    reason: AblationDisabledReason | None = None

    @model_validator(mode="after")
    def _status_metrics_and_reason_agree(self) -> Self:
        if self.status == "completed":
            if self.metrics is None:
                raise ValueError("a completed ablation leg requires metrics")
            if self.reason is not None:
                raise ValueError("a completed ablation leg must not carry a reason")
        else:
            if self.metrics is not None:
                raise ValueError("a disabled ablation leg must not carry metrics")
            if self.reason != ABLATION_AI_DISABLED_REASON:
                raise ValueError(
                    "a disabled ablation leg must carry the fixed structured reason"
                )
        return self


class AblationReport(StrictFrozenModel):
    """The baseline run paired with the fixed-disabled AI variant."""

    schema_version: Literal[RESEARCH_ABLATION_SCHEMA_VERSION] = (
        RESEARCH_ABLATION_SCHEMA_VERSION
    )
    research_only: Literal[True] = True
    baseline: AblationLeg
    ai_variant: AblationLeg

    @model_validator(mode="after")
    def _legs_well_formed_and_inputs_identical(self) -> Self:
        if self.baseline.leg != "baseline" or self.baseline.status != "completed":
            raise ValueError("the ablation baseline leg must be completed")
        if self.ai_variant.leg != "ai_variant" or self.ai_variant.status != "disabled":
            raise ValueError("the ablation ai_variant leg must be disabled")
        if self.baseline.inputs != self.ai_variant.inputs:
            raise ValueError("ablation legs must share identical frozen inputs")
        return self


def build_ablation_report(
    config: BacktestConfig, baseline_metrics: Metrics
) -> AblationReport:
    """Freeze identical inputs on both legs; the AI leg stays disabled."""
    inputs = AblationLegInputs(
        snapshot_id=config.snapshot_id,
        input_manifests=config.input_manifests,
        period=config.period,
        cost_model=config.cost_model,
        random_seed=config.experiment.random_seed,
    )
    return AblationReport(
        baseline=AblationLeg(
            leg="baseline",
            status="completed",
            inputs=inputs,
            metrics=baseline_metrics,
        ),
        ai_variant=AblationLeg(
            leg="ai_variant",
            status="disabled",
            inputs=inputs,
            reason=ABLATION_AI_DISABLED_REASON,
        ),
    )


__all__ = [
    "ABLATION_AI_DISABLED_REASON",
    "RESEARCH_ABLATION_SCHEMA_VERSION",
    "AblationLeg",
    "AblationLegInputs",
    "AblationReport",
    "build_ablation_report",
]
