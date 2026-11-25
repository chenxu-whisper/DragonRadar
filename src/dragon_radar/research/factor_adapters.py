"""Explicit approved stream boundary; deliberately no generic factor formula."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field

from dragon_radar.domain.models import Identifier
from dragon_radar.research.engine import FactorFrame
from dragon_radar.research.models import ResearchFactorId, StrictFrozenModel
from dragon_radar.research.result_contracts import _wire_shape, canonical, require

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class FactorAdapterApproval(StrictFrozenModel):
    factor_id: ResearchFactorId
    method_version: Identifier
    metric: Identifier
    window: Identifier
    unit: Identifier
    required_domains: tuple[Literal["market", "topic", "role"], ...]
    formula_sha256: Digest
    approval_evidence_sha256: Digest
    input_schema: Identifier


def exact_model(model, raw):
    typed = model.model_validate_json(canonical(raw))
    _wire_shape(raw, typed)
    return typed


def adapt_factor_stream(
    approval,
    archive,
    *,
    decisions,
    available_domains,
    verify,
    recompute,
):
    """verify resolves actual method/formula/grants; recompute is method-specific."""
    descriptor = exact_model(FactorAdapterApproval, approval)
    require(
        descriptor.required_domains
        and len(set(descriptor.required_domains)) == len(descriptor.required_domains)
        and set(descriptor.required_domains) <= set(available_domains),
        "factor_adapter_unavailable",
    )
    require(
        verify(descriptor.approval_evidence_sha256, approval)
        and verify(descriptor.formula_sha256, approval),
        "approved_method_unavailable",
    )
    require(
        set(archive) == {"schema", "method_version", "frames", "lineage"}
        and archive["schema"] == descriptor.input_schema
        and archive["method_version"] == descriptor.method_version
        and len(archive["frames"]) == len(archive["lineage"]) == len(decisions) > 0
    )
    result = []
    for raw, line, decision in zip(
        archive["frames"], archive["lineage"], decisions, strict=True
    ):
        frame = exact_model(FactorFrame, raw)
        require(
            set(line) == {"trade_date", "available_at", "input_sha256", "frame_sha256"}
        )
        require(isinstance(line["available_at"], str))
        available = datetime.fromisoformat(line["available_at"].replace("Z", "+00:00"))
        require(
            available.utcoffset() is not None
            and isinstance(decision["cutoff"], datetime)
            and decision["cutoff"].utcoffset() is not None,
            "factor availability requires an aware cutoff",
        )
        require(
            frame.trade_date == decision["trade_date"]
            and line["trade_date"] == frame.trade_date.isoformat()
            and available <= decision["cutoff"]
            and (frame.factor_id, frame.metric, frame.window, frame.unit)
            == (
                descriptor.factor_id,
                descriptor.metric,
                descriptor.window,
                descriptor.unit,
            )
            and line["frame_sha256"] == sha256(canonical(raw)).hexdigest()
            and line["input_sha256"] == decision["input_sha256"]
        )
        require(
            {p.security_id for p in frame.points} == set(decision["security_ids"])
            and verify(line["input_sha256"], decision)
            and canonical(recompute(descriptor, decision)) == canonical(raw),
            "factor archived formula mismatch",
        )
        result.append(frame)
    return tuple(result)
