"""Independent production gates; never claims CI execution or release authority."""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from dragon_radar.production.gates import validate_gate
from dragon_radar.production.trust import (
    CANDIDATE_FIELDS,
    GATE_IDS,
    SCOPE_FIELDS,
    InvalidEvidence,
    MissingEvidence,
    TrustPolicy,
    VerifierUnavailable,
    verify_evidence,
)

GATES = (
    "real_input",
    "official_rules",
    "real_model_evaluation",
    "public_sharing_permit",
    "shadow_run_20_trading_days",
)
REASONS = {
    "real_input": "Licensed frozen inputs and a production kernel run are required.",
    "official_rules": "Official archives and independent rule approval are required.",
    "real_model_evaluation": "Real human labels, model outputs and approval required.",
    "public_sharing_permit": "A permit for the exact public target is required.",
    "shadow_run_20_trading_days": "20 genuine consecutive trading days required.",
}


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def blocked_report(code="missing_evidence"):
    return {
        "schema": "dragon-radar-production-readiness/v1",
        "trust": "EXTERNAL_SIGNATURES",
        "overall_status": "blocked",
        "production_release": "blocked",
        "automated_acceptance": "unverified",
        "gates": {
            gate: {"status": "blocked", "code": code, "reason": REASONS[gate]}
            for gate in GATES
        },
    }


def evaluate_production(
    asset_root: Path,
    policy: TrustPolicy,
    candidate: Mapping,
    context: Mapping,
    evidence_paths: Mapping[str, str],
    now: datetime,
    *,
    expected_code: str,
    semantic_candidate: Mapping | None = None,
) -> dict:
    """Reverify every supplied record in this process; reports are never input."""
    if (
        not isinstance(policy, TrustPolicy)
        or not isinstance(evidence_paths, Mapping)
        or set(evidence_paths) - GATE_IDS
        or any(
            not isinstance(path, str) or not path for path in evidence_paths.values()
        )
        or set(candidate) != CANDIDATE_FIELDS
        or not SCOPE_FIELDS <= set(context)
    ):
        raise ValueError("production request schema invalid")
    if set(semantic_candidate or {}) & CANDIDATE_FIELDS:
        raise ValueError("semantic configuration cannot override signed versions")
    result = blocked_report()
    if candidate.get("code") != expected_code:
        for gate in GATES:
            result["gates"][gate] = {
                "status": "fail",
                "code": "candidate_source_mismatch",
                "reason": "Proof candidate differs from the current source.",
            }
        result["overall_status"] = "fail"
        return result
    for gate in GATES:
        path = evidence_paths.get(gate)
        if path is None:
            continue
        try:
            verified = verify_evidence(
                path,
                asset_root,
                policy,
                candidate,
                {key: context[key] for key in SCOPE_FIELDS},
                now,
            )
            if verified.record.gate_id != gate:
                raise InvalidEvidence()
            summary = validate_gate(
                gate,
                thaw(verified.record.payload),
                verified.materials,
                dict(candidate) | dict(semantic_candidate or {}),
                context,
                now,
                verify_related=lambda related_path: verify_evidence(
                    related_path,
                    asset_root,
                    policy,
                    candidate,
                    {key: context[key] for key in SCOPE_FIELDS},
                    now,
                ),
            )
            result["gates"][gate] = {
                **summary,
                "code": "verified",
                "evidence_sha256": verified.record_sha256,
                "reason": "Applicable independent signature and content checks passed.",
            }
        except (MissingEvidence, VerifierUnavailable) as error:
            result["gates"][gate] = {
                "status": "blocked",
                "code": error.code,
                "reason": REASONS[gate],
            }
        except (InvalidEvidence, ValueError, TypeError, KeyError, OverflowError):
            result["gates"][gate] = {
                "status": "fail",
                "code": "invalid_evidence",
                "reason": "Supplied proof is invalid, inapplicable or failed metrics.",
            }
    states = {item["status"] for item in result["gates"].values()}
    result["overall_status"] = (
        "fail" if "fail" in states else "pass" if states == {"pass"} else "blocked"
    )
    # Even five external passes do not authenticate current CI or authorize release.
    return result


def attach_external(legacy: dict, external: dict) -> None:
    """Attach current external verification without promoting automation trust."""
    for gate in GATES:
        legacy[gate] = dict(external["gates"][gate])
    legacy["production_external_verification"] = external
