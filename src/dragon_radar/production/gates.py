"""Semantic checks for cryptographically verified external evidence.

This module alone confers no trust. The report boundary first verifies the
issuer, scope, candidate, signature and all referenced raw material digests.
Only safe summaries leave that boundary.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlsplit

from dragon_radar.ai.evaluation import evaluate_manifest
from dragon_radar.regulation.engine import rule_is_applicable, run_regression_cases
from dragon_radar.regulation.models import (
    RegulationObservation,
    RegulationRule,
    production_gate_reasons,
    rule_is_effective,
)


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("gate payload schema invalid")
    return value


def material_json(materials, name):
    from dragon_radar.production.trust import strict_json

    if not isinstance(name, str) or name not in materials:
        raise ValueError("required material unavailable")
    return strict_json(materials[name])


def _permit(payload, context):
    exact(
        payload,
        {
            "subject",
            "purpose",
            "target",
            "audience",
            "content",
            "data",
            "redistribution_allowed",
            "restrictions",
        },
    )
    for field in ("subject", "purpose", "target", "audience"):
        if not context.get(field) or payload[field] != context[field]:
            raise ValueError("permit scope mismatch")
    for field in ("content", "data"):
        allowed, required = payload[field], context.get(field)
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(not isinstance(x, str) or not x for x in allowed)
            or not isinstance(required, list)
            or not required
            or not set(required).issubset(allowed)
        ):
            raise ValueError("permit scope mismatch")
    if payload["redistribution_allowed"] is not True:
        raise ValueError("redistribution permission absent")
    # Unsupported conditional permits cannot be silently treated as unrestricted.
    if payload["restrictions"] != []:
        raise ValueError("permit restrictions require approved adapter")
    return {}


def _evaluation(payload, materials, candidate, now):
    exact(payload, {"manifest", "model_identity", "human_attestations"})
    manifest = material_json(materials, payload["manifest"])
    report = evaluate_manifest(manifest)
    if report["release_status"] != "eligible":
        raise ValueError("real evaluation thresholds not met")
    for key in ("dataset", "model", "prompt", "retrieval"):
        if report["versions"][key] != candidate.get(key):
            raise ValueError("evaluation candidate version mismatch")
    identity = payload["model_identity"]
    attestations = payload["human_attestations"]
    if not isinstance(identity, str) or not identity:
        raise ValueError("model identity missing")
    if (
        not isinstance(attestations, dict)
        or any(not isinstance(x, str) or not x for x in attestations)
        or any(not isinstance(x, str) or not x for x in attestations.values())
    ):
        raise ValueError("human attestations invalid")
    raw_hashes = {sha256(content).hexdigest() for content in materials.values()}
    for sample in manifest["samples"]:
        provenance = sample["label_provenance"]
        if (
            provenance["source"] != "human"
            or provenance["annotator_id"] == identity
            or provenance["attested_by"] in {identity, provenance["annotator_id"]}
            or provenance["attestation_id"] not in attestations
        ):
            raise ValueError("independent human attestation missing")
        labeled = datetime.fromisoformat(
            provenance["labeled_at"].replace("Z", "+00:00")
        )
        attested = datetime.fromisoformat(
            provenance["attested_at"].replace("Z", "+00:00")
        )
        if not labeled <= attested <= now:
            raise ValueError("human attestation time invalid")
        if any(
            sample["lineage"][key] not in raw_hashes
            for key in ("input_hash", "output_hash")
        ):
            raise ValueError("real raw input or output missing")
        proof = material_json(materials, attestations[provenance["attestation_id"]])
        exact(proof, {"schema", "sample_id", "sample_sha256", *provenance})
        from dragon_radar.production.trust import canonical_json

        if (
            proof["schema"] != "dragon-radar-human-attestation/v1"
            or proof["sample_id"] != sample["sample_id"]
            or proof["sample_sha256"] != sha256(canonical_json(sample)).hexdigest()
            or any(proof[key] != value for key, value in provenance.items())
        ):
            raise ValueError("human sample attestation binding invalid")
    if set(attestations) != {
        sample["label_provenance"]["attestation_id"] for sample in manifest["samples"]
    }:
        raise ValueError("human attestation coverage invalid")
    return {"sample_counts": report["sample_counts"], "metrics": report["metrics"]}


def _rules(payload, materials, candidate, now):
    exact(payload, {"rules", "lifecycle", "official_archives", "methods"})
    rules = material_json(materials, payload["rules"])
    lifecycle = material_json(materials, payload["lifecycle"])
    archives = payload["official_archives"]
    methods = payload["methods"]
    if not isinstance(rules, dict) or set(rules) != {"rules"}:
        raise ValueError("rule material schema invalid")
    if not isinstance(lifecycle, dict) or set(lifecycle) != {"records"}:
        raise ValueError("rule lifecycle schema invalid")
    if not isinstance(lifecycle["records"], list):
        raise ValueError("rule lifecycle records invalid")
    for item in lifecycle["records"]:
        exact(item, {"rule_id", "rule_version", "status", "recorded_at"})
        if any(not isinstance(value, str) or not value for value in item.values()):
            raise ValueError("rule lifecycle record fields invalid")
    if not rules["rules"] or not isinstance(archives, dict):
        raise ValueError("official rules or archives missing")
    if not isinstance(methods, dict) or methods != candidate.get("methods"):
        raise ValueError("approved method versions mismatch")
    expected = candidate.get("regulation_rules")
    scope = exact(
        candidate.get("rule_scope"),
        {
            "exchange",
            "board",
            "security_status",
            "trade_date",
        },
    )
    hosts = candidate.get("official_source_hosts")
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("official source authority missing")
    actual = {}
    for raw in rules["rules"]:
        rule = RegulationRule.model_validate(raw)
        if production_gate_reasons(rule) or run_regression_cases(rule):
            raise ValueError("official rule production regression failed")
        if not rule_is_effective(rule, now.date()):
            raise ValueError("official rule not effective")
        if rule.review_record.published_at > now:
            raise ValueError("official rule release time invalid")
        key = f"{rule.rule_id}:{rule.rule_version}"
        if key in actual:
            raise ValueError("duplicate rule identity")
        actual[key] = rule.official_source_url
        archive = archives.get(key)
        if archive not in materials or not materials[archive]:
            raise ValueError("official source archive missing")
        source = material_json(materials, archive)
        exact(
            source,
            {
                "schema",
                "rule_id",
                "rule_version",
                "url",
                "content_material",
                "content_sha256",
                "rule_sha256",
                "scope",
            },
        )
        from dragon_radar.production.trust import canonical_json

        text = materials.get(source["content_material"])
        if (
            source["schema"] != "dragon-radar-official-rule-archive/v1"
            or source["rule_id"] != rule.rule_id
            or source["rule_version"] != rule.rule_version
            or source["url"] != rule.official_source_url
            or urlsplit(source["url"]).hostname not in hosts
            or not text
            or sha256(text).hexdigest() != source["content_sha256"]
            or sha256(canonical_json(raw)).hexdigest() != source["rule_sha256"]
        ):
            raise ValueError("official archive origin or rule binding invalid")
        observation = RegulationObservation(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            security_id="scope-probe",
            computed_value="0",
            input_state="complete",
            **scope,
        )
        if source["scope"] != scope or not rule_is_applicable(rule, observation):
            raise ValueError("official rule scope mismatch")
        records = [
            item
            for item in lifecycle["records"]
            if item.get("rule_id") == rule.rule_id
            and item.get("rule_version") == rule.rule_version
        ]
        times = [
            datetime.fromisoformat(x["recorded_at"].replace("Z", "+00:00"))
            for x in records
        ]
        if (
            not records
            or records[-1]["status"] != "active"
            or any(t.tzinfo is None or t > now for t in times)
            or any(a >= b for a, b in zip(times, times[1:], strict=False))
        ):
            raise ValueError("rule latest lifecycle inactive or invalid")
    if expected != sorted(actual):
        raise ValueError("candidate regulation rule coverage mismatch")
    if set(archives) != set(actual):
        raise ValueError("official archive coverage mismatch")
    return {"rule_count": len(actual)}


def validate_gate(
    gate: str,
    payload: dict,
    materials: Mapping[str, bytes],
    candidate: Mapping,
    context: Mapping,
    now: datetime,
    *,
    verify_related=None,
) -> dict:
    """Validate content only; caller MUST authenticate its complete envelope."""
    if gate == "public_sharing_permit":
        summary = _permit(payload, context)
    elif gate == "real_model_evaluation":
        summary = _evaluation(payload, materials, candidate, now)
    elif gate == "official_rules":
        summary = _rules(payload, materials, candidate, now)
    elif gate == "real_input":
        exact(payload, {"bundle", "run", "supplier"})
        bundle = material_json(materials, payload["bundle"])
        run = material_json(materials, payload["run"])
        supplier = material_json(materials, payload["supplier"])
        from dragon_radar.research.production import validate_input_run

        summary = validate_input_run(bundle, run, supplier, candidate, now)
    elif gate == "shadow_run_20_trading_days":
        exact(payload, {"calendar", "entries", "calendar_evidence", "daily_evidence"})
        if verify_related is None:
            raise ValueError("independent calendar and daily signatures required")
        calendar = material_json(materials, payload["calendar"])
        entries = material_json(materials, payload["entries"])
        from dragon_radar.production.shadow import validate_shadow

        summary = validate_shadow(calendar, entries, candidate, now)
        _shadow_proofs(payload, materials, candidate, now, verify_related)
    else:
        raise ValueError("unknown production gate")
    return {"status": "pass", **summary}


def _shadow_proofs(payload, materials, candidate, now, verify_related):
    """Authenticate the original daily envelopes, not a fresh aggregate claim."""
    from dragon_radar.production.report import thaw
    from dragon_radar.production.shadow import SHANGHAI, timestamp
    from dragon_radar.production.trust import canonical_json
    from dragon_radar.research.production import validate_input_run

    calendar_proof = verify_related(payload["calendar_evidence"])
    calendar_payload = thaw(calendar_proof.record.payload)
    exact(
        calendar_payload,
        {
            "type",
            "calendar",
            "exchange",
            "window_start",
            "window_end",
            "complete",
        },
    )
    calendar = material_json(materials, payload["calendar"])
    entries = material_json(materials, payload["entries"])["entries"]
    bound_calendar = material_json(
        calendar_proof.materials, calendar_payload["calendar"]
    )
    if (
        calendar_proof.record.gate_id != "official_rules"
        or calendar_payload["type"] != "trading_calendar"
        or calendar_payload["complete"] is not True
        or calendar_payload["exchange"]
        != candidate.get("rule_scope", {}).get("exchange")
        or bound_calendar != calendar
        or calendar_payload["window_start"] > entries[0]["trade_date"]
        or calendar_payload["window_end"] < entries[-1]["trade_date"]
        or calendar_proof.record.approved_at
        >= datetime.fromisoformat(entries[0]["trade_date"])
        .replace(tzinfo=SHANGHAI)
        .astimezone(UTC)
    ):
        raise ValueError("independent complete applicable calendar proof invalid")
    daily_paths = payload["daily_evidence"]
    if (
        not isinstance(daily_paths, list)
        or len(daily_paths) != len(entries)
        or len(set(daily_paths)) != len(daily_paths)
    ):
        raise ValueError("independent daily proof coverage missing")
    for path, entry in zip(daily_paths, entries, strict=True):
        proof = verify_related(path)
        data = thaw(proof.record.payload)
        exact(data, {"type", "entry", "bundle", "run", "supplier"})
        signed_entry = material_json(proof.materials, data["entry"])
        bundle = material_json(proof.materials, data["bundle"])
        run = material_json(proof.materials, data["run"])
        supplier = material_json(proof.materials, data["supplier"])
        if (
            proof.record.gate_id != "shadow_run_20_trading_days"
            or data["type"] != "shadow_day"
            or signed_entry != entry
            or proof.record.approver != entry["reviewer"]
            or proof.record.issued_at.astimezone(SHANGHAI).date().isoformat()
            != entry["trade_date"]
            or proof.record.approved_at.astimezone(SHANGHAI).date().isoformat()
            != entry["trade_date"]
            or sha256(canonical_json(bundle)).hexdigest() != entry["input_sha256"]
        ):
            raise ValueError("original daily proof date or dependency binding invalid")
        summary = validate_input_run(bundle, run, supplier, candidate, now)
        period = bundle["config"]["period"]
        lower = max(period["start"], calendar_payload["window_start"])
        upper = min(period["end"], calendar_payload["window_end"])
        expected_sessions = [
            session["trade_date"]
            for session in calendar["sessions"]
            if lower <= session["trade_date"] <= upper
        ]
        for frame in bundle["frames"]:
            actual_calendar = frame["market"]["calendar"]
            if (
                actual_calendar["exchange"] != calendar_payload["exchange"]
                or actual_calendar["version"] != calendar["version"]
                or [day for day in actual_calendar["sessions"] if lower <= day <= upper]
                != expected_sessions
            ):
                raise ValueError("shadow execution calendar differs from approval")
        if (
            any(
                summary[field] != entry[field]
                for field in (
                    "publication_sequence",
                    "started_at",
                    "finished_at",
                )
            )
            or summary["snapshot_id"] != entry["publication_id"]
            or bundle["anchor"]["trade_date"] != entry["trade_date"]
            or proof.record.issued_at < timestamp(entry["finished_at"])
            or proof.record.approved_at != timestamp(entry["reviewed_at"])
            or max(timestamp(item["received_at"]) for item in bundle["receipts"])
            != timestamp(entry["source_received_at"])
            or timestamp(bundle["factor_archive"]["lineage"][-1]["available_at"])
            != timestamp(entry["available_at"])
        ):
            raise ValueError("shadow original execution and receipt lineage mismatch")
