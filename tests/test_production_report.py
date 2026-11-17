import importlib
import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from test_production_trust import (  # noqa: F401
    CANDIDATE,
    CONTEXT,
    rsa_keys,
)
from test_production_trust import (
    bundle as proof_bundle,
)

bundle = proof_bundle


def module():
    assert importlib.util.find_spec("dragon_radar.production.report"), (
        "independent production report missing"
    )
    return importlib.import_module("dragon_radar.production.report")


NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


def test_missing_proofs_produce_five_independent_blocks(tmp_path):
    from dragon_radar.production.trust import TrustPolicy

    report = module().evaluate_production(
        tmp_path,
        TrustPolicy(),
        CANDIDATE,
        CONTEXT,
        {},
        NOW,
        expected_code=CANDIDATE["code"],
    )
    assert len(report["gates"]) == 5
    assert all(x["status"] == "blocked" for x in report["gates"].values())
    assert report["production_release"] == "blocked"
    assert report["automated_acceptance"] == "unverified"


def test_one_signed_permit_passes_independently_without_release_authority(bundle):
    root, record, policy, save = bundle
    record["gate_id"] = "public_sharing_permit"
    record["payload"] = {
        **CONTEXT,
        "content": ["overview"],
        "data": ["market"],
        "redistribution_allowed": True,
        "restrictions": [],
    }
    save()
    report = module().evaluate_production(
        root,
        policy,
        CANDIDATE,
        {**CONTEXT, "content": ["overview"], "data": ["market"]},
        {"public_sharing_permit": "record.json"},
        NOW,
        expected_code=CANDIDATE["code"],
    )
    assert report["gates"]["public_sharing_permit"]["status"] == "pass"
    assert report["gates"]["real_input"]["status"] == "blocked"
    assert report["production_release"] == "blocked"
    assert "proof.txt" not in str(report)
    assert "test-only proof" not in str(report)


def test_signed_semantic_failure_is_fail_not_missing(bundle):
    root, record, policy, save = bundle
    record["gate_id"] = "public_sharing_permit"
    record["payload"] = {"approved": True}
    save()
    report = module().evaluate_production(
        root,
        policy,
        CANDIDATE,
        CONTEXT,
        {"public_sharing_permit": "record.json"},
        NOW,
        expected_code=CANDIDATE["code"],
    )
    assert report["gates"]["public_sharing_permit"]["status"] == "fail"
    assert report["gates"]["real_input"]["status"] == "blocked"


def test_candidate_current_source_mismatch_fails_before_authentication(tmp_path):
    from dragon_radar.production.trust import TrustPolicy

    report = module().evaluate_production(
        tmp_path,
        TrustPolicy(),
        CANDIDATE,
        CONTEXT,
        {},
        NOW,
        expected_code="new-source",
    )
    assert all(x["status"] == "fail" for x in report["gates"].values())
    assert report["overall_status"] == "fail"


def test_supplied_report_cannot_certify_external_gates(tmp_path):
    with pytest.raises(ValueError):
        module().evaluate_production(
            tmp_path,
            None,
            CANDIDATE,
            CONTEXT,
            {"public_sharing_permit": {"status": "pass"}},
            NOW,
            expected_code=CANDIDATE["code"],
        )


def test_semantic_metadata_cannot_override_signed_versions(tmp_path):
    from dragon_radar.production.trust import TrustPolicy

    with pytest.raises(ValueError, match="override"):
        module().evaluate_production(
            tmp_path,
            TrustPolicy(),
            CANDIDATE,
            CONTEXT,
            {},
            NOW,
            expected_code=CANDIDATE["code"],
            semantic_candidate={"model": "m2"},
        )


def test_material_bound_can_hold_100_human_samples_and_raw_outputs():
    from dragon_radar.production.trust import TrustPolicy

    assert TrustPolicy().max_materials >= 100 * 3 + 1


def test_real_signature_with_full_100_sample_materials_passes_only_model_gate(bundle):
    from test_production_gates import VERSIONS, ai_materials

    root, record, policy, save = bundle
    raw, _ = ai_materials()
    versions = {
        **CANDIDATE,
        **{key: VERSIONS[key] for key in ("dataset", "model", "prompt", "retrieval")},
    }
    record["gate_id"] = "real_model_evaluation"
    record["candidate"] = versions
    record["payload"] = {
        "manifest": "evaluation",
        "model_identity": "provider",
        "human_attestations": {f"a{i}": f"a{i}" for i in range(100)},
    }
    record["materials"] = []
    for identity, content in raw.items():
        name = f"{identity}.txt"
        (root / name).write_bytes(content)
        record["materials"].append(
            {
                "material_id": identity,
                "path": name,
                "sha256": sha256(content).hexdigest(),
            }
        )
    save()
    report = module().evaluate_production(
        root,
        policy,
        versions,
        CONTEXT,
        {"real_model_evaluation": "record.json"},
        NOW,
        expected_code=versions["code"],
    )
    assert report["gates"]["real_model_evaluation"]["status"] == "pass"
    assert (
        report["gates"]["real_model_evaluation"]["sample_counts"]["human_labeled"]
        == 100
    )
    assert report["production_release"] == "blocked"
    assert "human-reviewer" not in json.dumps(report)


@pytest.mark.parametrize("malformed", ["null_record", "numeric_time"])
def test_authenticated_malformed_lifecycle_returns_invalid_evidence(bundle, malformed):
    from test_production_gates import rule_case

    root, record, policy, save = bundle
    _, semantic, payload, raw = rule_case()
    lifecycle = json.loads(raw["lifecycle"])
    if malformed == "null_record":
        lifecycle["records"] = [None]
    else:
        lifecycle["records"][0]["recorded_at"] = 123
    raw["lifecycle"] = json.dumps(lifecycle).encode()
    record["gate_id"] = "official_rules"
    record["payload"] = payload
    record["materials"] = []
    for identity, content in raw.items():
        name = f"{identity}.json"
        (root / name).write_bytes(content)
        record["materials"].append(
            {
                "material_id": identity,
                "path": name,
                "sha256": sha256(content).hexdigest(),
            }
        )
    save()
    report = module().evaluate_production(
        root,
        policy,
        CANDIDATE,
        CONTEXT,
        {"official_rules": "record.json"},
        NOW,
        expected_code=CANDIDATE["code"],
        semantic_candidate={
            key: semantic[key]
            for key in (
                "methods",
                "regulation_rules",
                "rule_scope",
                "official_source_hosts",
            )
        },
    )
    assert report["gates"]["official_rules"]["status"] == "fail"
    assert report["gates"]["official_rules"]["code"] == "invalid_evidence"
    assert report["overall_status"] == "fail"
