"""Production semantics never upgrade a successful process exit to approval."""

import importlib
import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest


def test_source_identity_binds_container_edge_configuration(tmp_path):
    from dragon_radar.production.cli import source_identity

    folder = tmp_path / "containers"
    folder.mkdir()
    edge = folder / "owner.Caddyfile"
    edge.write_text("respond 403\n", encoding="utf-8")
    original = source_identity(tmp_path)
    edge.write_text("respond 404\n", encoding="utf-8")
    assert source_identity(tmp_path) != original


def module():
    name = "dragon_radar.production.gates"
    assert importlib.util.find_spec("dragon_radar.production") is not None, (
        "production gate implementation is missing"
    )
    assert importlib.util.find_spec(name) is not None, "gate semantics are missing"
    return importlib.import_module(name)


NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
VERSIONS = dict(dataset="d1", model="m1", prompt="p1", retrieval="r1", rules="v1")
CONTEXT = dict(
    subject="owner",
    purpose="research",
    target="public-site",
    audience="public",
    content=["overview"],
    data=["licensed-market"],
)


def evaluate(gate, payload, materials=None):
    return module().validate_gate(
        gate, payload, materials or {}, VERSIONS, CONTEXT, NOW
    )


def test_public_permit_requires_redistribution_for_exact_contents():
    payload = {
        "subject": "owner",
        "purpose": "research",
        "target": "public-site",
        "audience": "public",
        "content": ["overview"],
        "data": ["licensed-market"],
        "redistribution_allowed": True,
        "restrictions": [],
    }
    assert evaluate("public_sharing_permit", payload)["status"] == "pass"
    payload["redistribution_allowed"] = False
    with pytest.raises(ValueError, match="redistribution"):
        evaluate("public_sharing_permit", payload)


@pytest.mark.parametrize("field", ["target", "subject", "purpose", "audience"])
def test_internal_license_cannot_satisfy_public_permit(field):
    payload = {
        **CONTEXT,
        "redistribution_allowed": True,
        "restrictions": [],
    }
    payload[field] = "internal"
    with pytest.raises(ValueError, match="scope"):
        evaluate("public_sharing_permit", payload)


def test_unknown_fields_fail_closed():
    with pytest.raises(ValueError, match="schema"):
        evaluate("public_sharing_permit", {"approved": True})


def ai_materials(count=100):
    samples, raw = [], {}
    for i in range(count):
        inp, out = f"input-{i}".encode(), f"output-{i}".encode()
        ih, oh = sha256(inp).hexdigest(), sha256(out).hexdigest()
        raw[f"i{i}"], raw[f"o{i}"] = inp, out
        samples.append(
            {
                "sample_id": f"s{i}",
                "split": "development" if i < 50 else "frozen_test",
                "lineage": {
                    **{
                        f"{key}_version": VERSIONS[key]
                        for key in ("dataset", "model", "prompt", "retrieval")
                    },
                    "input_hash": ih,
                    "output_hash": oh,
                },
                "label_provenance": {
                    "source": "human",
                    "annotator_id": "human-reviewer",
                    "labeled_at": "2026-11-01T12:00:00Z",
                    "attestation_id": f"a{i}",
                    "attested_by": "independent",
                    "attested_at": "2026-11-02T12:00:00Z",
                },
                "fact_labels": [{"supported": True}],
                "numeric_consistent": True,
                "recomputation_consistent": True,
                "safety_hard_failure": False,
            }
        )
        raw[f"a{i}"] = json.dumps(
            {
                "schema": "dragon-radar-human-attestation/v1",
                "sample_id": f"s{i}",
                **samples[-1]["label_provenance"],
                "sample_sha256": sha256(
                    json.dumps(
                        samples[-1],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
            }
        ).encode()
    manifest = {
        "schema_version": "dragon-radar-ai-eval/v2",
        **{
            f"{key}_version": VERSIONS[key]
            for key in ("dataset", "model", "prompt", "retrieval")
        },
        "release_status": "candidate",
        "samples": samples,
    }
    raw["evaluation"] = json.dumps(manifest).encode()
    return raw, manifest


def test_ai_evaluation_executes_existing_semantics_and_checks_raw_hashes():
    raw, manifest = ai_materials()
    payload = {
        "manifest": "evaluation",
        "model_identity": "model-provider",
        "human_attestations": {f"a{i}": f"a{i}" for i in range(100)},
    }
    assert evaluate("real_model_evaluation", payload, raw)["status"] == "pass"
    manifest["samples"][-1]["fact_labels"] = [{"supported": False}] * 20
    raw["evaluation"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="evaluation"):
        evaluate("real_model_evaluation", payload, raw)


def test_zero_sample_report_cannot_pass_even_if_evaluator_exits_zero():
    raw, _ = ai_materials(0)
    with pytest.raises(ValueError, match="evaluation"):
        evaluate(
            "real_model_evaluation",
            {
                "manifest": "evaluation",
                "model_identity": "provider",
                "human_attestations": {},
            },
            raw,
        )


def test_ai_self_labels_and_missing_raw_output_cannot_pass():
    raw, manifest = ai_materials()
    payload = {
        "manifest": "evaluation",
        "model_identity": "model-provider",
        "human_attestations": {f"a{i}": f"a{i}" for i in range(100)},
    }
    del raw["o0"]
    with pytest.raises(ValueError, match="raw"):
        evaluate("real_model_evaluation", payload, raw)
    raw, manifest = ai_materials()
    manifest["samples"][0]["label_provenance"]["annotator_id"] = "model-provider"
    raw["evaluation"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="human"):
        evaluate("real_model_evaluation", payload, raw)


def test_human_attestation_is_a_bound_asset_not_an_identity_string():
    raw, _ = ai_materials()
    payload = {
        "manifest": "evaluation",
        "model_identity": "provider",
        "human_attestations": {f"a{i}": f"a{i}" for i in range(100)},
    }
    del raw["a0"]
    with pytest.raises(ValueError, match="material"):
        evaluate("real_model_evaluation", payload, raw)


def test_missing_real_input_material_cannot_pass_on_flags():
    with pytest.raises(ValueError, match="material"):
        evaluate(
            "real_input",
            {
                "bundle": "bundle",
                "run": "run",
                "supplier": "supplier",
            },
        )


def test_official_rules_cannot_pass_generic_approval():
    with pytest.raises(ValueError, match="schema"):
        evaluate("official_rules", {"approved": True})


def test_shadow_days_cannot_pass_count_declaration():
    with pytest.raises(ValueError, match="schema"):
        evaluate("shadow_run_20_trading_days", {"days": 20})


def rule_case():
    from test_reset_regulation import _rule

    rule = _rule()
    identity = f"{rule.rule_id}:{rule.rule_version}"
    candidate = {
        **VERSIONS,
        "methods": {"roles": "v1", "research": "v1"},
        "regulation_rules": [identity],
        "official_source_hosts": ["exchange.example.test"],
        "rule_scope": {
            "exchange": "TEST",
            "board": "synthetic_test",
            "security_status": "regular",
            "trade_date": "2026-11-16",
        },
    }
    payload = {
        "rules": "rules",
        "lifecycle": "lifecycle",
        "official_archives": {identity: "archive"},
        "methods": candidate["methods"],
    }
    materials = {
        "rules": json.dumps({"rules": [rule.model_dump(mode="json")]}).encode(),
        "lifecycle": json.dumps(
            {
                "records": [
                    {
                        "rule_id": rule.rule_id,
                        "rule_version": rule.rule_version,
                        "status": "active",
                        "recorded_at": "2026-11-01T12:00:00Z",
                    }
                ]
            }
        ).encode(),
        "source": b"test-only official text",
        "archive": json.dumps(
            {
                "schema": "dragon-radar-official-rule-archive/v1",
                "rule_id": rule.rule_id,
                "rule_version": rule.rule_version,
                "url": rule.official_source_url,
                "content_material": "source",
                "content_sha256": sha256(b"test-only official text").hexdigest(),
                "rule_sha256": sha256(
                    json.dumps(
                        rule.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
                "scope": candidate["rule_scope"],
            }
        ).encode(),
    }
    return rule, candidate, payload, materials


def test_rule_gate_reruns_frozen_examples_and_latest_lifecycle():
    rule, candidate, payload, materials = rule_case()
    assert (
        module().validate_gate(
            "official_rules", payload, materials, candidate, CONTEXT, NOW
        )["status"]
        == "pass"
    )
    materials["source"] = b"unrelated archive"
    with pytest.raises(ValueError, match="archive"):
        module().validate_gate(
            "official_rules", payload, materials, candidate, CONTEXT, NOW
        )
    materials["source"] = b"test-only official text"
    candidate["rule_scope"]["exchange"] = "OTHER"
    with pytest.raises(ValueError, match="scope"):
        module().validate_gate(
            "official_rules", payload, materials, candidate, CONTEXT, NOW
        )
    candidate["rule_scope"]["exchange"] = "TEST"
    changed = rule.model_dump(mode="json")
    changed["test_cases"][0]["expected_state"] = "triggered"
    materials["rules"] = json.dumps({"rules": [changed]}).encode()
    with pytest.raises(ValueError, match="regression"):
        module().validate_gate(
            "official_rules", payload, materials, candidate, CONTEXT, NOW
        )


def test_shadow_aggregate_requires_independently_signed_daily_records():
    from test_production_shadow import history

    calendar, entries = history()
    raw = {
        "calendar": json.dumps(calendar).encode(),
        "entries": json.dumps(entries).encode(),
    }
    with pytest.raises(ValueError, match="independent"):
        evaluate(
            "shadow_run_20_trading_days",
            {
                "calendar": "calendar",
                "entries": "entries",
                "calendar_evidence": "calendar-proof",
                "daily_evidence": [],
            },
            raw,
        )
