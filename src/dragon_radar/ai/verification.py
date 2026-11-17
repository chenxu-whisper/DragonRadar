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
    "unit",
    "precision",
    "recomputation",
}
_INFERENCE_FIELDS = {"text", "claim", "confidence", "evidence_ids"}
_CLAIM_FIELDS = {"kind"}
_CONFIDENCE_FIELDS = {"kind", "value"}
_MISSING_FIELDS = {"text", "reason"}
_CITATION_FIELDS = {
    "evidence_id",
    "evidence_position",
    "source_identity",
    "available_at",
    "value",
    "unit",
    "precision",
}
_CITATION_REQUIRED_FIELDS = _CITATION_FIELDS - {"unit", "precision"}
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
_DISPLAYED_DECIMAL = re.compile(
    r"(?<![\w.-])-?(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)"
    r"(?:\.(\d+))?(?![\w-]|\.\d)"
)
_PROSE_UNIT = re.compile(
    r"(?<![\w.-])-?(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)"
    r"(?:\.\d+)?\s*(currency/share|percentage|percent|shares?|ratio|fraction|"
    r"counts?|days?|yuan|%|元|股|次|天|倍)(?![\w/])",
    re.IGNORECASE,
)
_UNIT_ALIASES = {
    "counts": "count",
    "day": "days",
    "percent": "percent",
    "percentage": "percent",
    "share": "shares",
    "yuan": "元",
}


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


def _displayed_decimals(text: str) -> list[tuple[Decimal, int]]:
    values: list[tuple[Decimal, int]] = []
    for match in _DISPLAYED_DECIMAL.finditer(text):
        value = _decimal(match.group(0).replace(",", ""))
        if value is not None:
            values.append((value, len(match.group(1) or "")))
    return values


def _normalized_unit(value: str) -> str:
    normalized = value.strip().lower()
    return _UNIT_ALIASES.get(normalized, normalized)


def _has_contradictory_prose_unit(text: str, expected: str) -> bool:
    expected_unit = _normalized_unit(expected)
    return any(
        _normalized_unit(match.group(1)) != expected_unit
        for match in _PROSE_UNIT.finditer(text)
    )


def _numeric_metadata(value: Mapping) -> tuple[Decimal, int, str] | None:
    number = _decimal(value.get("value"))
    if number is None:
        return None
    precision = value.get("precision")
    unit = value.get("unit")
    if (
        type(precision) is not int
        or not 0 <= precision <= 12
        or not isinstance(unit, str)
        or not unit
    ):
        return None
    return number, precision, unit


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
        if not _CITATION_REQUIRED_FIELDS <= set(citation) <= _CITATION_FIELDS:
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
        entry_numeric = _numeric_metadata(entry)
        if entry_numeric is not None and (
            citation.get("precision") != entry_numeric[1]
            or citation.get("unit") != entry_numeric[2]
        ):
            _append(errors, "citation_numeric_metadata_mismatch")
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
        recomputation = fact.get("recomputation")
        numeric_support: tuple[Decimal, int, str] | None = None
        if recomputation is not None:
            if (
                not isinstance(recomputation, Mapping)
                or set(recomputation) != _RECOMPUTATION_FIELDS
            ):
                _append(errors, "recomputation_invalid")
                continue
            values: list[Decimal] = []
            precisions: list[int] = []
            units: list[str] = []
            ids = recomputation.get("evidence_ids")
            if not isinstance(ids, (list, tuple)) or not ids:
                _append(errors, "recomputation_invalid")
                continue
            if all(isinstance(item, str) for item in ids) and len(set(ids)) != len(ids):
                _append(errors, "recomputation_duplicate_operand")
            if any(
                not any(reference[0] == item for reference in citations_by_reference)
                for item in ids
            ):
                _append(errors, "recomputation_citation_absent")
            for argument_id in ids:
                argument = (
                    evidence.get(argument_id) if isinstance(argument_id, str) else None
                )
                metadata = _numeric_metadata(argument) if argument else None
                if metadata is None:
                    _append(errors, "recomputation_invalid")
                    break
                number, precision, unit = metadata
                values.append(number)
                precisions.append(precision)
                units.append(unit)
            else:
                function = recomputation.get("function")
                compatible_units = (
                    len(units) == len(ids) and bool(units) and len(set(units)) == 1
                )
                if (
                    function
                    in {
                        "identity",
                        "sum",
                        "difference",
                        "ratio",
                        "percent_change",
                    }
                    and not compatible_units
                ):
                    _append(errors, "recomputation_unit_mismatch")
                try:
                    result, _ = recompute_claim(
                        function,
                        values,
                        precisions,
                    )
                except (TypeError, ValueError, InvalidOperation):
                    _append(errors, "recomputation_invalid")
                else:
                    if recomputation.get("result") != result:
                        _append(errors, "recomputation_mismatch")
                    if (
                        function in {"identity", "sum", "difference"}
                        and compatible_units
                    ):
                        result_unit = units[0]
                    elif function == "ratio" and compatible_units:
                        result_unit = "ratio"
                    elif function == "percent_change" and compatible_units:
                        result_unit = "fraction"
                    else:
                        result_unit = None
                    result_number = _decimal(result)
                    if (
                        result_number is None
                        or not isinstance(result_unit, str)
                        or not result_unit
                    ):
                        _append(errors, "recomputation_invalid")
                    else:
                        numeric_support = (
                            result_number,
                            _precision(result) or 0,
                            result_unit,
                        )
        else:
            numeric_support = _numeric_metadata(entry)
            if _decimal(entry.get("value")) is not None and numeric_support is None:
                _append(errors, "manifest_numeric_metadata_invalid")
        if numeric_support is not None:
            if (
                fact.get("precision") != numeric_support[1]
                or fact.get("unit") != numeric_support[2]
            ):
                _append(errors, "numeric_claim_metadata_mismatch")
            if isinstance(text, str) and any(
                token != numeric_support[:2] for token in _displayed_decimals(text)
            ):
                _append(errors, "fact_text_unsupported")
            elif isinstance(text, str) and not _displayed_decimals(text):
                _append(errors, "fact_text_unsupported")
            if isinstance(text, str) and _has_contradictory_prose_unit(
                text,
                numeric_support[2],
            ):
                _append(errors, "fact_text_unit_mismatch")
        elif isinstance(text, str) and isinstance(entry.get("value"), str):
            evidence_tokens = set(_displayed_decimals(entry["value"]))
            if any(token not in evidence_tokens for token in _displayed_decimals(text)):
                _append(errors, "fact_text_unsupported")

    for inference in output["inferences"]:
        if set(inference) != _INFERENCE_FIELDS:
            _append(errors, "inference_invalid")
            continue
        claim = inference.get("claim")
        confidence_metadata = inference.get("confidence")
        claim_kind = (
            claim.get("kind")
            if isinstance(claim, Mapping) and set(claim) == _CLAIM_FIELDS
            else None
        )
        confidence_kind = (
            confidence_metadata.get("kind")
            if isinstance(confidence_metadata, Mapping)
            and set(confidence_metadata) == _CONFIDENCE_FIELDS
            else None
        )
        confidence = (
            _decimal(confidence_metadata.get("value"))
            if isinstance(confidence_metadata, Mapping)
            else None
        )
        ids = inference.get("evidence_ids")
        if isinstance(ids, (list, tuple)) and not ids:
            _append(errors, "inference_evidence_absent")
        if confidence_kind == "probability":
            _append(errors, "probability_confidence_forbidden")
        valid_ids = (
            isinstance(ids, (list, tuple))
            and bool(ids)
            and all(isinstance(item, str) and item for item in ids)
            and len(set(ids)) == len(ids)
            and all(item in evidence for item in ids)
        )
        if (
            not isinstance(inference.get("text"), str)
            or not inference["text"].strip()
            or claim_kind not in {"interpretation", "regulatory_conclusion"}
            or confidence_kind != "explanatory"
            or confidence is None
            or not Decimal(0) <= confidence <= Decimal(1)
            or not valid_ids
        ):
            _append(errors, "inference_invalid")
        elif valid_ids and any(
            not any(reference[0] == item for reference in citations_by_reference)
            for item in ids
        ):
            _append(errors, "inference_citation_absent")
        if isinstance(inference.get("text"), str) and _displayed_decimals(
            inference["text"]
        ):
            _append(errors, "inference_numeric_claim_forbidden")
        if claim_kind == "regulatory_conclusion" and (
            not valid_ids
            or not any(
                evidence[item]["source_identity"].startswith("regulation:")
                for item in ids
            )
        ):
            _append(errors, "unsupported_regulatory_conclusion")

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

    def scalar_unit(key: str, parent: Mapping | None) -> str:
        if parent is not None:
            direct = parent.get(f"{key}_unit")
            if isinstance(direct, str) and direct:
                return direct
            if key in {"open", "high", "low", "close", "last", "previous_close"}:
                price_unit = parent.get("price_unit")
                if isinstance(price_unit, str) and price_unit:
                    return price_unit
            if key == "volume":
                volume_unit = parent.get("volume_unit")
                if isinstance(volume_unit, str) and volume_unit:
                    return volume_unit
            if key in {"amount", "market_cap", "seal_amount"}:
                amount_unit = parent.get("amount_unit")
                if isinstance(amount_unit, str) and amount_unit:
                    return amount_unit
            if key.endswith(("ratio", "_rate", "_share")):
                return "fraction"
        return "count" if key.endswith(("count", "_days")) else "dimensionless"

    def visit(
        value: object,
        path: list[str],
        available_at: str,
        source_identity: str,
        parent: Mapping | None = None,
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
            elif "regulation" in path:
                local_identity = f"regulation:{snapshot_id}:{publication['version']}"
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
                    value,
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
            entry["unit"] = scalar_unit(path[-1], parent)
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
