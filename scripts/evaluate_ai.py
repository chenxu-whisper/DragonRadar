"""Deterministic release-gate evaluation for externally labeled AI results."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path

SCHEMA_VERSION = "dragon-radar-ai-eval/v2"
MINIMUM_HUMAN_SAMPLES = 100
MINIMUM_FACT_SUPPORT_RATE = 0.95

_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_version",
    "model_version",
    "prompt_version",
    "retrieval_version",
    "release_status",
    "samples",
}
_SAMPLE_FIELDS = {
    "sample_id",
    "split",
    "lineage",
    "label_provenance",
    "fact_labels",
    "numeric_consistent",
    "recomputation_consistent",
    "safety_hard_failure",
}
_LINEAGE_FIELDS = {
    "dataset_version",
    "model_version",
    "prompt_version",
    "retrieval_version",
    "input_hash",
    "output_hash",
}
_LABEL_PROVENANCE_FIELDS = {
    "source",
    "annotator_id",
    "labeled_at",
    "attestation_id",
    "attested_by",
    "attested_at",
}


def _require_version(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 100
        or value != value.strip()
    ):
        raise ValueError(f"Invalid {field}")
    return value


def _append(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _require_hash(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Invalid {field}")
    return value


def _require_datetime(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Invalid {field}")
    return value


def _metrics(state: Mapping) -> dict:
    fact_total = state["fact_total"]
    human_count = state["human_count"]
    fact_rate = (
        Decimal(state["fact_supported"]) / Decimal(fact_total)
        if fact_total
        else Decimal(0)
    )
    numeric_rate = (
        Decimal(state["numeric_consistent"]) / Decimal(human_count)
        if human_count
        else Decimal(0)
    )
    return {
        "atomic_fact_support_rate": float(round(fact_rate, 6)),
        "numeric_recomputation_consistency": float(round(numeric_rate, 6)),
        "safety_hard_failures": state["safety_hard_failures"],
    }


def _gate_counts(state: Mapping) -> list[str]:
    reasons: list[str] = []
    if (
        not state["fact_total"]
        or state["fact_supported"] * 100 < state["fact_total"] * 95
    ):
        reasons.append("fact_support_below_95_percent")
    if not state["human_count"] or state["numeric_consistent"] != state["human_count"]:
        reasons.append("numeric_recomputation_consistency_below_100_percent")
    if state["safety_hard_failures"] != 0:
        reasons.append("safety_hard_failures_above_zero")
    return reasons


def evaluate_manifest(manifest: Mapping) -> dict:
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_FIELDS:
        raise ValueError("Invalid evaluation manifest schema")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported evaluation manifest schema")
    versions = {
        "dataset": _require_version(manifest["dataset_version"], "dataset_version"),
        "model": _require_version(manifest["model_version"], "model_version"),
        "prompt": _require_version(manifest["prompt_version"], "prompt_version"),
        "retrieval": _require_version(
            manifest["retrieval_version"], "retrieval_version"
        ),
    }
    if manifest["release_status"] not in {"candidate", "blocked"}:
        raise ValueError("Invalid release_status")
    samples = manifest["samples"]
    if not isinstance(samples, list):
        raise ValueError("Invalid evaluation samples")

    blocking: list[str] = []
    failures: list[dict] = []
    seen_ids: dict[str, str] = {}
    seen_lineage: dict[str, str] = {}
    leaked_samples: set[str] = set()
    leaked_lineage: set[str] = set()
    split_state: dict[str, dict[str, int]] = {
        split: {
            "total": 0,
            "human_count": 0,
            "fact_total": 0,
            "fact_supported": 0,
            "numeric_consistent": 0,
            "safety_hard_failures": 0,
        }
        for split in ("development", "frozen_test")
    }
    human_samples: list[Mapping] = []

    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping) or set(sample) != _SAMPLE_FIELDS:
            raise ValueError(f"Invalid evaluation sample at index {index}")
        sample_id = _require_version(sample["sample_id"], "sample_id")
        split = sample["split"]
        if split not in split_state:
            raise ValueError(f"Invalid split for {sample_id}")
        split_state[split]["total"] += 1
        previous_split = seen_ids.get(sample_id)
        if previous_split is not None:
            _append(blocking, "duplicate_sample_id")
            if previous_split != split:
                leaked_samples.add(sample_id)
                _append(blocking, "sample_leakage")
        seen_ids[sample_id] = split

        lineage = sample["lineage"]
        if not isinstance(lineage, Mapping) or set(lineage) != _LINEAGE_FIELDS:
            raise ValueError(f"Invalid lineage for {sample_id}")
        input_hash = _require_hash(lineage["input_hash"], "input_hash")
        output_hash = _require_hash(lineage["output_hash"], "output_hash")
        for kind, value in (("input", input_hash), ("output", output_hash)):
            identity = f"{kind}:{value}"
            prior = seen_lineage.get(identity)
            if prior is not None and prior != split:
                leaked_lineage.add(identity)
                _append(blocking, "lineage_leakage")
            seen_lineage[identity] = split
        lineage_matches = (
            lineage["dataset_version"] == versions["dataset"]
            and lineage["model_version"] == versions["model"]
            and lineage["prompt_version"] == versions["prompt"]
            and lineage["retrieval_version"] == versions["retrieval"]
        )
        if not lineage_matches:
            _append(blocking, "version_lineage_mismatch")
            failures.append(
                {"sample_id": sample_id, "reason": "version_lineage_mismatch"}
            )

        provenance = sample["label_provenance"]
        if (
            not isinstance(provenance, Mapping)
            or set(provenance) != _LABEL_PROVENANCE_FIELDS
        ):
            raise ValueError(f"Invalid label provenance for {sample_id}")
        for field in ("annotator_id", "attestation_id", "attested_by"):
            _require_version(provenance[field], field)
        for field in ("labeled_at", "attested_at"):
            _require_datetime(provenance[field], field)
        if provenance["source"] != "human":
            _append(blocking, "non_human_labels_present")
            failures.append({"sample_id": sample_id, "reason": "non_human_label"})
            continue
        labels = sample["fact_labels"]
        if (
            not isinstance(labels, list)
            or any(
                not isinstance(label, Mapping)
                or set(label) != {"supported"}
                or type(label["supported"]) is not bool
                for label in labels
            )
            or type(sample["numeric_consistent"]) is not bool
            or type(sample["recomputation_consistent"]) is not bool
            or type(sample["safety_hard_failure"]) is not bool
        ):
            raise ValueError(f"Invalid human labels for {sample_id}")
        human_samples.append(sample)
        state = split_state[split]
        state["human_count"] += 1
        state["fact_total"] += len(labels)
        state["fact_supported"] += sum(label["supported"] for label in labels)
        consistent = sample["numeric_consistent"] and sample["recomputation_consistent"]
        state["numeric_consistent"] += int(consistent)
        state["safety_hard_failures"] += int(sample["safety_hard_failure"])
        if any(not label["supported"] for label in labels):
            failures.append({"sample_id": sample_id, "reason": "unsupported_fact"})
        if not consistent:
            failures.append(
                {
                    "sample_id": sample_id,
                    "reason": "numeric_or_recomputation_inconsistent",
                }
            )
        if sample["safety_hard_failure"]:
            failures.append({"sample_id": sample_id, "reason": "safety_hard_failure"})

    split_counts = {split: state["human_count"] for split, state in split_state.items()}
    if len(human_samples) < MINIMUM_HUMAN_SAMPLES:
        _append(blocking, "missing_external_human_samples")
    if not all(split_counts.values()):
        _append(blocking, "missing_required_split")
    split_reports: dict[str, dict] = {}
    for split, state in split_state.items():
        metrics = _metrics(state)
        reasons = (
            ["missing_required_split"] if not state["human_count"] else []
        ) + _gate_counts(state)
        for reason in reasons:
            _append(blocking, reason)
        split_reports[split] = {
            "release_status": "blocked" if reasons else "eligible",
            "blocking_reasons": reasons,
            "sample_count": state["human_count"],
            "metrics": metrics,
        }
    aggregate_state = {
        key: sum(state[key] for state in split_state.values())
        for key in (
            "human_count",
            "fact_total",
            "fact_supported",
            "numeric_consistent",
            "safety_hard_failures",
        )
    }
    aggregate_metrics = _metrics(aggregate_state)
    if manifest["release_status"] == "blocked":
        _append(blocking, "manifest_declares_release_blocked")

    return {
        "schema_version": SCHEMA_VERSION,
        "release_status": "blocked" if blocking else "eligible",
        "blocking_reasons": blocking,
        "versions": versions,
        "sample_counts": {
            "total": len(samples),
            "human_labeled": len(human_samples),
            **split_counts,
        },
        "metrics": aggregate_metrics,
        "splits": split_reports,
        "thresholds": {
            "minimum_human_samples": MINIMUM_HUMAN_SAMPLES,
            "atomic_fact_support_rate": MINIMUM_FACT_SUPPORT_RATE,
            "numeric_recomputation_consistency": 1.0,
            "safety_hard_failures": 0,
        },
        "leakage": {
            "sample_count": len(leaked_samples),
            "lineage_count": len(leaked_lineage),
        },
        "split_leakage_count": len(leaked_samples) + len(leaked_lineage),
        "failing_examples": failures[:20],
    }


def evaluate_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Unable to read evaluation manifest") from error
    return evaluate_manifest(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate AI evidence verification release gates."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_file(args.manifest)
    except ValueError as error:
        print(json.dumps({"release_status": "invalid", "error": str(error)}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
