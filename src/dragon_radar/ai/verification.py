"""Pure verification of AI drafts against immutable task input manifests."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Literal

VerificationOutcome = Literal["passed", "failed"]

_OUTPUT_FIELDS = {"facts", "inferences", "missing_evidence", "citations"}
_FACT_FIELDS = {
    "text",
    "evidence_id",
    "evidence_position",
    "value",
    "recomputation",
}
_INFERENCE_FIELDS = {"text", "confidence", "evidence_ids"}
_MISSING_FIELDS = {"text", "reason"}
_CITATION_FIELDS = {
    "evidence_id",
    "evidence_position",
    "source_identity",
    "available_at",
    "value",
}
_RECOMPUTATION_FIELDS = {"function", "evidence_ids", "result"}
_DANGEROUS_FIELD_PARTS = {
    "action",
    "database",
    "instruction",
    "network",
    "policy_override",
    "regulatory_conclusion",
    "tool",
    "write_note",
}
_FORGED_VALIDATION_FIELDS = {
    "is_valid",
    "validated",
    "validation_passed",
    "verification",
    "verification_passed",
    "verified",
}
_DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.(\d+))?$")


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationOutcome
    error_codes: tuple[str, ...]
    safety_failure: bool
    checked_facts: int
    checked_citations: int


def _append(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def _structured_field_failures(value: object) -> tuple[bool, bool]:
    """Inspect field names only, so quoted source text is never instruction-scanned."""
    dangerous = False
    forged = False
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                normalized = key.lower().replace("-", "_")
                forged = forged or normalized in _FORGED_VALIDATION_FIELDS
                dangerous = dangerous or any(
                    part in normalized for part in _DANGEROUS_FIELD_PARTS
                )
                if normalized == "value":
                    continue
            child_dangerous, child_forged = _structured_field_failures(child)
            dangerous = dangerous or child_dangerous
            forged = forged or child_forged
    elif isinstance(value, (list, tuple)):
        for child in value:
            child_dangerous, child_forged = _structured_field_failures(child)
            dangerous = dangerous or child_dangerous
            forged = forged or child_forged
    return dangerous, forged


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _pointer(document: object, pointer: object) -> tuple[bool, object]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False, None
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif (
            isinstance(current, list) and token.isdigit() and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def _equal_json(left: object, right: object) -> bool:
    try:
        return json.dumps(
            left,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ) == json.dumps(
            right,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value)
    if not _DECIMAL.fullmatch(text):
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _precision(value: object) -> int | None:
    if not isinstance(value, str):
        return 0 if type(value) is int else None
    match = _DECIMAL.fullmatch(value)
    return len(match.group(1) or "") if match else None


def recompute_claim(
    function: str,
    values: Sequence[Decimal],
    source_precisions: Sequence[int],
) -> tuple[str, int]:
    """Run a small deterministic computation whitelist at server-owned precision."""
    if (
        not values
        or len(values) != len(source_precisions)
        or any(
            type(item) is not int or not 0 <= item <= 12 for item in source_precisions
        )
    ):
        raise ValueError("Invalid recomputation inputs")
    if function == "identity" and len(values) == 1:
        result, precision = values[0], source_precisions[0]
    elif function == "sum":
        result, precision = sum(values, Decimal(0)), max(source_precisions)
    elif function == "difference" and len(values) == 2:
        result, precision = values[0] - values[1], max(source_precisions)
    elif function == "ratio" and len(values) == 2 and values[1] != 0:
        result, precision = values[0] / values[1], 6
    elif function == "percent_change" and len(values) == 2 and values[1] != 0:
        result, precision = (values[0] - values[1]) / values[1], 6
    else:
        raise ValueError("Recomputation function is not allowed")
    quantum = Decimal(1).scaleb(-precision)
    exact = result.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return f"{exact:.{precision}f}", precision


def _evidence_index(
    manifest: Mapping,
    errors: list[str],
) -> dict[str, Mapping]:
    entries = manifest.get("evidence")
    if not isinstance(entries, list):
        _append(errors, "manifest_evidence_absent")
        return {}
    index: dict[str, Mapping] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            _append(errors, "manifest_evidence_invalid")
            continue
        evidence_id = entry.get("evidence_id")
        position = entry.get("evidence_position")
        found, value = _pointer(manifest, position)
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or evidence_id in index
            or not isinstance(entry.get("source_identity"), str)
            or not entry.get("source_identity")
            or _datetime(entry.get("available_at")) is None
            or not found
            or not _equal_json(value, entry.get("value"))
        ):
            _append(errors, "manifest_evidence_invalid")
            continue
        index[evidence_id] = entry
    return index


def _valid_object_list(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) <= 1000
        and all(isinstance(item, Mapping) for item in value)
    )


def verify_output(manifest: Mapping, output: Mapping) -> VerificationResult:
    """Verify provider or human-edited output without trusting provider flags."""
    errors: list[str] = []
    dangerous, forged = _structured_field_failures(output)
    if forged:
        _append(errors, "forged_validation")
    if dangerous:
        _append(errors, "unauthorized_output_field")
    if not isinstance(manifest, Mapping) or not isinstance(output, Mapping):
        return VerificationResult("failed", ("invalid_schema",), False, 0, 0)
    if set(output) != _OUTPUT_FIELDS:
        _append(errors, "invalid_schema")
    for field in _OUTPUT_FIELDS:
        if not _valid_object_list(output.get(field)):
            _append(errors, "invalid_schema")
    if "invalid_schema" in errors:
        return VerificationResult(
            "failed",
            tuple(errors),
            dangerous or forged,
            0,
            0,
        )

    evidence = _evidence_index(manifest, errors)
    task_as_of = _datetime(
        manifest.get("publication", {}).get("as_of")
        if isinstance(manifest.get("publication"), Mapping)
        else None
    )
    if task_as_of is None:
        _append(errors, "manifest_as_of_invalid")

    citations_by_reference: dict[tuple[str, str], Mapping] = {}
    for citation in output["citations"]:
        if set(citation) != _CITATION_FIELDS:
            _append(errors, "citation_invalid")
            continue
        evidence_id = citation.get("evidence_id")
        position = citation.get("evidence_position")
        if not isinstance(evidence_id, str) or not isinstance(position, str):
            _append(errors, "citation_invalid")
            continue
        entry = evidence.get(evidence_id)
        if entry is None:
            _append(errors, "citation_evidence_unknown")
            continue
        if position != entry["evidence_position"]:
            _append(errors, "citation_position_mismatch")
        if citation.get("source_identity") != entry["source_identity"]:
            _append(errors, "citation_source_mismatch")
        if citation.get("available_at") != entry["available_at"]:
            _append(errors, "citation_time_mismatch")
        available_at = _datetime(entry["available_at"])
        if task_as_of is not None and available_at is not None:
            if available_at > task_as_of:
                _append(errors, "future_evidence")
        if not _equal_json(citation.get("value"), entry["value"]):
            _append(errors, "citation_value_mismatch")
        citations_by_reference[(evidence_id, position)] = citation

    for fact in output["facts"]:
        if set(fact) - _FACT_FIELDS:
            _append(errors, "fact_invalid")
            continue
        if "evidence_position" not in fact:
            _append(errors, "evidence_position_absent")
            continue
        evidence_id = fact.get("evidence_id")
        position = fact.get("evidence_position")
        text = fact.get("text")
        if not isinstance(evidence_id, str) or not isinstance(position, str):
            _append(errors, "fact_invalid")
            continue
        entry = evidence.get(evidence_id)
        if entry is None:
            _append(errors, "fact_evidence_unknown")
            continue
        if position != entry["evidence_position"]:
            _append(errors, "evidence_position_mismatch")
        if (evidence_id, position) not in citations_by_reference:
            _append(errors, "citation_absent")
        if not _equal_json(fact.get("value"), entry["value"]):
            _append(errors, "cited_value_mismatch")
        if not isinstance(text, str) or not text.strip():
            _append(errors, "fact_invalid")
        elif (
            fact.get("recomputation") is None
            and isinstance(entry["value"], (str, int))
            and str(entry["value"]) not in text
        ):
            _append(errors, "fact_text_unsupported")
        recomputation = fact.get("recomputation")
        if recomputation is not None:
            if (
                not isinstance(recomputation, Mapping)
                or set(recomputation) != _RECOMPUTATION_FIELDS
            ):
                _append(errors, "recomputation_invalid")
                continue
            values: list[Decimal] = []
            precisions: list[int] = []
            ids = recomputation.get("evidence_ids")
            if not isinstance(ids, (list, tuple)) or not ids:
                _append(errors, "recomputation_invalid")
                continue
            if any(
                not any(reference[0] == item for reference in citations_by_reference)
                for item in ids
            ):
                _append(errors, "recomputation_citation_absent")
            for argument_id in ids:
                argument = (
                    evidence.get(argument_id) if isinstance(argument_id, str) else None
                )
                number = _decimal(argument.get("value")) if argument else None
                precision = (
                    argument.get("precision")
                    if argument and type(argument.get("precision")) is int
                    else _precision(argument.get("value"))
                    if argument
                    else None
                )
                if number is None or precision is None:
                    _append(errors, "recomputation_invalid")
                    break
                values.append(number)
                precisions.append(precision)
            else:
                try:
                    result, _ = recompute_claim(
                        recomputation.get("function"),
                        values,
                        precisions,
                    )
                except (TypeError, ValueError, InvalidOperation):
                    _append(errors, "recomputation_invalid")
                else:
                    if recomputation.get("result") != result:
                        _append(errors, "recomputation_mismatch")
                    if isinstance(text, str) and result not in text:
                        _append(errors, "fact_text_unsupported")

    for inference in output["inferences"]:
        if set(inference) != _INFERENCE_FIELDS:
            _append(errors, "inference_invalid")
            continue
        confidence = _decimal(inference.get("confidence"))
        ids = inference.get("evidence_ids")
        if (
            not isinstance(inference.get("text"), str)
            or not inference["text"].strip()
            or confidence is None
            or not Decimal(0) <= confidence <= Decimal(1)
            or not isinstance(ids, (list, tuple))
            or any(item not in evidence for item in ids)
        ):
            _append(errors, "inference_invalid")
        elif any(
            not any(reference[0] == item for reference in citations_by_reference)
            for item in ids
        ):
            _append(errors, "inference_citation_absent")

    for missing in output["missing_evidence"]:
        if (
            not {"text"} <= set(missing) <= _MISSING_FIELDS
            or not isinstance(missing.get("text"), str)
            or not missing["text"].strip()
            or (
                "reason" in missing
                and (
                    not isinstance(missing["reason"], str)
                    or not missing["reason"].strip()
                )
            )
        ):
            _append(errors, "missing_evidence_invalid")

    safety = dangerous or forged or "future_evidence" in errors
    return VerificationResult(
        "failed" if errors else "passed",
        tuple(errors),
        safety,
        len(output["facts"]),
        len(output["citations"]),
    )


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def build_manifest_evidence(
    task_type: str,
    publication: Mapping,
    selected_input: Mapping,
) -> list[dict]:
    """Create stable evidence coordinates for immutable selected input scalars."""
    snapshot_id = publication["snapshot_id"]
    default_available = publication["as_of"]
    default_identity = f"{task_type}:{snapshot_id}"
    entries: list[dict] = []

    def visit(
        value: object,
        path: list[str],
        available_at: str,
        source_identity: str,
    ) -> None:
        if isinstance(value, Mapping):
            local_available = next(
                (
                    item
                    for key in ("available_at", "published_at", "received_at")
                    if isinstance((item := value.get(key)), str)
                ),
                available_at,
            )
            local_identity = source_identity
            if isinstance(value.get("source"), str) and isinstance(
                value.get("source_version"), str
            ):
                source_revision = value.get("revision_id") or value.get("article_id")
                local_identity = (
                    f"news:{value['source']}:{value['source_version']}:"
                    f"{source_revision or 'unknown-revision'}"
                )
            elif isinstance(value.get("security_id"), str):
                local_identity = (
                    f"market:{snapshot_id}:{value['security_id']}:"
                    f"{publication['version']}"
                )
            for key in sorted(value):
                if key == "notes_context":
                    continue
                visit(
                    value[key],
                    [*path, str(key)],
                    local_available,
                    local_identity,
                )
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(
                    item,
                    [*path, str(index)],
                    available_at,
                    source_identity,
                )
            return
        if value is None or isinstance(value, (dict, list)):
            return
        pointer = "/" + "/".join(_escape_pointer(part) for part in path)
        digest = hashlib.sha256(f"{source_identity}:{pointer}".encode()).hexdigest()[
            :24
        ]
        entry = {
            "evidence_id": f"ev-{digest}",
            "evidence_position": pointer,
            "source_identity": source_identity,
            "available_at": available_at,
            "value": value,
        }
        precision = _precision(value)
        if precision is not None:
            entry["precision"] = precision
        entries.append(entry)

    visit(
        selected_input,
        ["input"],
        default_available,
        default_identity,
    )
    return entries


__all__ = [
    "VerificationResult",
    "build_manifest_evidence",
    "recompute_claim",
    "verify_output",
]
