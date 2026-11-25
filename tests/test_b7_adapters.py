"""Packages are boundaries, never invented formulas or provider scores."""

import copy
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest


def test_missing_packages_disable_all_extensions():
    from test_b7_result_contracts import actual_result

    from dragon_radar.research.capabilities import disabled_capabilities

    cfg, _ = actual_result()
    result = disabled_capabilities(cfg.snapshot_id, cfg.period)
    assert set(result) == {
        "snapshot_id",
        "period",
        "factors",
        "walk_forward",
        "ai_variant",
    }
    assert all(item["status"] == "disabled" for item in result["factors"])
    assert result["ai_variant"]["reason"] == "real_evaluation_inputs_unavailable"


@pytest.mark.parametrize(
    "module,model",
    [
        ("factor_adapters", "FactorAdapterApproval"),
        ("phase_adapter", "PhaseSeriesArchive"),
        ("ai_score_adapter", "AIScoreAdapterApproval"),
        ("ai_score_adapter", "AIScoreArchive"),
    ],
)
def test_unknown_descriptor_fields_fail(module, model):
    import importlib

    target = getattr(importlib.import_module("dragon_radar.research." + module), model)
    with pytest.raises(ValueError):
        target.model_validate_json('{"schema":"invented","prose_score":7}')


def test_phase_empty_is_not_real_phase_series():
    from dragon_radar.research.phase_adapter import adapt_phase_series

    with pytest.raises(ValueError):
        adapt_phase_series(
            {
                "schema": "dragon-radar.research-phase-input/v1",
                "method_version": "actual-v1",
                "approval_evidence_sha256": "a" * 64,
                "anchor_sequence": 10,
                "entries": [],
            },
            anchor_sequence=10,
            verify=lambda *args: True,
            publications={},
        )


def test_ai_evaluation_cannot_be_replaced_by_approval_hash():
    from dragon_radar.research.ai_score_adapter import evaluation_ready

    assert not evaluation_ready(None)
    assert not evaluation_ready(
        {
            "unique_labeled_items": 99,
            "fact_support": "0.95",
            "numeric_recompute": "1",
            "hard_failures": 0,
            "development_frozen_separated": True,
            "release_rollback_approved": True,
            "provider_use_permitted": True,
        }
    )


def factor_inputs():
    from test_reset_backtest import ENG_VALUES_BY_DAY, _factor_frames

    from dragon_radar.research.result_contracts import canonical

    frame = _factor_frames(ENG_VALUES_BY_DAY)[0]
    raw = frame.model_dump(mode="json")
    cutoff = datetime.combine(frame.trade_date, datetime.min.time(), UTC)
    approval = {
        "factor_id": frame.factor_id,
        "method_version": "test-approved-v1",
        "metric": frame.metric,
        "window": frame.window,
        "unit": frame.unit,
        "required_domains": ["market"],
        "formula_sha256": "a" * 64,
        "approval_evidence_sha256": "b" * 64,
        "input_schema": "test-factor-archive-v1",
    }
    archive = {
        "schema": approval["input_schema"],
        "method_version": approval["method_version"],
        "frames": [raw],
        "lineage": [
            {
                "trade_date": frame.trade_date.isoformat(),
                "available_at": cutoff.isoformat(),
                "input_sha256": "c" * 64,
                "frame_sha256": sha256(canonical(raw)).hexdigest(),
            }
        ],
    }
    decision = {
        "trade_date": frame.trade_date,
        "cutoff": cutoff,
        "input_sha256": "c" * 64,
        "security_ids": [p.security_id for p in frame.points],
    }
    return approval, archive, decision


def test_factor_golden_uses_approved_specific_recomputation():
    from dragon_radar.research.factor_adapters import adapt_factor_stream

    approval, archive, decision = factor_inputs()
    calls = []

    def recompute(descriptor, actual):
        calls.append((descriptor.factor_id, actual["input_sha256"]))
        return archive["frames"][0]

    frames = adapt_factor_stream(
        approval,
        archive,
        decisions=[decision],
        available_domains=["market"],
        verify=lambda digest, context: digest in {"a" * 64, "b" * 64, "c" * 64},
        recompute=recompute,
    )
    assert frames[0].trade_date == decision["trade_date"]
    assert calls == [(approval["factor_id"], "c" * 64)]


@pytest.mark.parametrize(
    "case", ["late", "naive", "domain", "denied", "formula", "unit", "digest"]
)
def test_factor_boundary_rejects(case):
    from dragon_radar.research.factor_adapters import adapt_factor_stream

    approval, archive, decision = factor_inputs()
    domains = ["market"]
    if case == "late":
        archive["lineage"][0]["available_at"] = (
            decision["cutoff"] + timedelta(seconds=1)
        ).isoformat()
    elif case == "naive":
        archive["lineage"][0]["available_at"] = (
            decision["cutoff"].replace(tzinfo=None).isoformat()
        )
    elif case == "domain":
        domains = []
    elif case == "unit":
        approval["unit"] = "wrong"
    elif case == "digest":
        archive["lineage"][0]["frame_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        adapt_factor_stream(
            approval,
            archive,
            decisions=[decision],
            available_domains=domains,
            verify=lambda *args: case != "denied",
            recompute=lambda *args: {} if case == "formula" else archive["frames"][0],
        )


def phase_inputs():
    identity, missing_id = uuid4(), uuid4()
    cutoff = datetime(2025, 1, 7, tzinfo=UTC)
    entries = [
        {
            "publication_id": str(pub),
            "publication_sequence": index + 1,
            "trade_date": f"2025-01-0{index + 6}",
            "available_at": cutoff.isoformat(),
            "phase": "ice" if index == 0 else None,
            "sentiment_method_version": "test-ex16-v1",
            "input_sha256": "a" * 64,
        }
        for index, pub in enumerate((identity, missing_id))
    ]
    archive = {
        "schema": "dragon-radar.research-phase-input/v1",
        "method_version": "test-phase-adapter-v1",
        "approval_evidence_sha256": "b" * 64,
        "anchor_sequence": 2,
        "entries": entries,
    }
    pubs = {
        pub: {
            "publication_sequence": entry["publication_sequence"],
            "trade_date": date.fromisoformat(entry["trade_date"]),
            "cutoff": cutoff,
            "phase": entry["phase"],
            "sentiment_method_version": entry["sentiment_method_version"],
            "input_sha256": entry["input_sha256"],
        }
        for pub, entry in zip((identity, missing_id), entries, strict=True)
    }
    return archive, pubs


def test_actual_phase_binding_and_null_disclosure():
    from dragon_radar.research.phase_adapter import adapt_phase_series

    archive, pubs = phase_inputs()
    phases, missing = adapt_phase_series(
        archive, anchor_sequence=2, verify=lambda *args: True, publications=pubs
    )
    assert phases[0].phase == "ice"
    assert missing == (date(2025, 1, 7),)


@pytest.mark.parametrize(
    "case", ["future", "late", "phase", "method", "denied", "unknown"]
)
def test_phase_boundary_rejects(case):
    from dragon_radar.research.phase_adapter import adapt_phase_series

    archive, pubs = phase_inputs()
    entry = archive["entries"][0]
    if case == "future":
        entry["publication_sequence"] = 3
    elif case == "late":
        entry["available_at"] = "2025-01-08T00:00:00Z"
    elif case == "phase":
        entry["phase"] = "climax"
    elif case == "method":
        entry["sentiment_method_version"] = "wrong"
    elif case == "unknown":
        pubs.clear()
    with pytest.raises(ValueError):
        adapt_phase_series(
            archive,
            anchor_sequence=2,
            verify=lambda *args: case != "denied",
            publications=pubs,
        )


def ai_inputs():
    approval = {
        "adapter_id": "test-overlay",
        "method_version": "test-order-v1",
        "base_factor_id": "FAC-R01",
        "score_unit": "score",
        "score_formula_sha256": "a" * 64,
        "evaluation_manifest_sha256": "b" * 64,
        "approval_evidence_sha256": "c" * 64,
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
        "retrieval_version": "retrieval-v1",
    }
    entry = {
        "trade_date": "2025-01-06",
        "security_id": "security-1",
        "base_factor_input_sha256": "d" * 64,
        "score": "0.1234567890123456789",
        "unit": "score",
        "available_at": "2025-01-06T00:00:00Z",
        "source_evidence_sha256": "e" * 64,
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
        "retrieval_version": "retrieval-v1",
        "reason": None,
    }
    archive = {
        "schema": "dragon-radar.research-ai-scores/v1",
        "adapter_id": "test-overlay",
        "method_version": "test-order-v1",
        "entries": [entry],
    }
    decisions = {
        (date(2025, 1, 6), "security-1"): {
            "cutoff": datetime(2025, 1, 6, tzinfo=UTC),
            "base_factor_input_sha256": "d" * 64,
        }
    }
    evaluation = {
        "unique_labeled_items": 100,
        "fact_support": "0.95",
        "numeric_recompute": "1",
        "hard_failures": 0,
        "development_frozen_separated": True,
        "release_rollback_approved": True,
        "provider_use_permitted": True,
    }
    return approval, archive, decisions, evaluation


def test_ai_golden_preserves_exact_score_and_approved_null():
    from decimal import Decimal

    from dragon_radar.research.ai_score_adapter import adapt_ai_scores

    approval, archive, decisions, evaluation = ai_inputs()
    for score, reason in (("0.1234567890123456789", None), (None, "source_missing")):
        archive["entries"][0].update(score=score, reason=reason)
        entries = adapt_ai_scores(
            approval,
            archive,
            decisions=decisions,
            verify=lambda *args: True,
            evaluation=evaluation,
            approved_missing_reasons={"source_missing"},
        )
        assert entries[0].score == (Decimal(score) if score else None)


@pytest.mark.parametrize(
    "case",
    [
        "late",
        "identity",
        "version",
        "denied",
        "null",
        "duplicate",
        "numeric_reason",
        "hard_failure",
    ],
)
def test_ai_boundary_rejects(case):
    from dragon_radar.research.ai_score_adapter import adapt_ai_scores

    approval, archive, decisions, evaluation = ai_inputs()
    entry = archive["entries"][0]
    if case == "late":
        entry["available_at"] = "2025-01-07T00:00:00Z"
    elif case == "identity":
        entry["base_factor_input_sha256"] = "f" * 64
    elif case == "version":
        entry["prompt_version"] = "wrong"
    elif case == "null":
        entry["score"] = None
    elif case == "duplicate":
        archive["entries"].append(copy.deepcopy(entry))
    elif case == "numeric_reason":
        entry["reason"] = "source_missing"
    elif case == "hard_failure":
        evaluation["hard_failures"] = 1
    with pytest.raises(ValueError):
        adapt_ai_scores(
            approval,
            archive,
            decisions=decisions,
            verify=lambda *args: case != "denied",
            evaluation=evaluation,
            approved_missing_reasons={None, "source_missing"},
        )
