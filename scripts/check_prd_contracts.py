"""Repeatable structural and arithmetic checks for the reset PRD."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRD = ROOT / "docs" / "DragonRadar_开发需求PRD.md"
EXPECTED_REQS = {
    *(f"G{number:02d}" for number in range(1, 5)),
    *(f"M{number:02d}" for number in range(1, 3)),
    *(f"N{number:02d}" for number in range(1, 4)),
    *(f"L{number:02d}" for number in range(1, 4)),
    *(f"P{number:02d}" for number in range(1, 3)),
    "S01",
    *(f"U{number:02d}" for number in range(1, 5)),
    *(f"R{number:02d}" for number in range(1, 5)),
    *(f"Q{number:02d}" for number in range(1, 5)),
    *(f"D{number:02d}" for number in range(1, 5)),
    *(f"A{number:02d}" for number in range(1, 6)),
    *(f"E{number:02d}" for number in range(1, 6)),
    *(f"B{number:02d}" for number in range(1, 3)),
    *(f"X{number:02d}" for number in range(1, 4)),
    *(f"V{number:02d}" for number in range(1, 12)),
}


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_blocks(document: str) -> list[tuple[int, dict[str, Any]]]:
    blocks = []
    for number, match in enumerate(
        re.finditer(r"```json\s*\n(.*?)\n```", document, flags=re.S), start=1
    ):
        value = json.loads(
            match.group(1),
            object_pairs_hook=_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"nonfinite JSON token: {token}")
            ),
        )
        if not isinstance(value, dict):
            raise ValueError(f"JSON block {number} must contain an object")
        blocks.append((number, value))
    return blocks


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _endpoint_rows(document: str) -> list[tuple[str, str, str]]:
    rows = []
    pattern = re.compile(r"^\|\s*(\d{2})\s*\|\s*(GET|POST|Repo)\s*\|\s*([^|]+)\|", re.M)
    for number, method, raw_path in pattern.findall(document):
        path = raw_path.strip().strip("`")
        rows.append((number, method, path))
    return rows


def _examples(document: str) -> list[tuple[str, str, str, str]]:
    headings = list(
        re.finditer(
            r"^####\s+端点(\d{2})\s+`?(GET|POST|listNotes|putNote|listWatchlist|putWatchlist)?\s*([^`\n（]*)",
            document,
            flags=re.M,
        )
    )
    result = []
    for index, heading in enumerate(headings):
        end = (
            headings[index + 1].start() if index + 1 < len(headings) else len(document)
        )
        result.append(
            (
                heading.group(1),
                (heading.group(2) or "").strip(),
                heading.group(3).strip(),
                document[heading.end() : end],
            )
        )
    return result


def _example_payload(examples: list[tuple[str, str, str, str]], number: str) -> dict:
    section = next(section for item, _, _, section in examples if item == number)
    match = re.search(r"```json\s*\n(.*?)\n```", section, flags=re.S)
    if not match:
        raise ValueError(f"example {number} has no JSON block")
    return json.loads(match.group(1), object_pairs_hook=_pairs)


def _path_matches(template: str, example: str) -> bool:
    route = example.split("?", 1)[0]
    pattern = re.sub(r"\\{[^/{}]+\\}", r"[^/]+", re.escape(template))
    pattern = pattern.replace(r"\[\^/\]\+", "[^/]+")
    return re.fullmatch(pattern, route) is not None


def _ratio(errors: list[str], label: str, value: Any) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    numerator = value.get(
        "numerator", value.get("closed_limit_up", value.get("today_sealed"))
    )
    denominator = value.get("denominator", value.get("touched", value.get("tradable")))
    ratio = value.get("value", value.get("rate"))
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        errors.append(f"{label} numerator/denominator invalid")
        return
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        errors.append(f"{label} value invalid")
        return
    if round(numerator / denominator, 4) != ratio:
        errors.append(f"{label} value does not match numerator/denominator")


def check_prd(document: str) -> dict[str, Any]:
    errors: list[str] = []
    reqs = re.findall(r"^\|\s*([A-Z]\d{2})\s*\|", document, flags=re.M)
    if len(reqs) != 57 or set(reqs) != EXPECTED_REQS:
        missing = sorted(EXPECTED_REQS - set(reqs))
        extra = sorted(set(reqs) - EXPECTED_REQS)
        errors.append(
            "REQ rows must contain the 57 expected unique IDs; "
            f"missing={missing}, extra={extra}"
        )

    endpoints = _endpoint_rows(document)
    endpoint_numbers = [number for number, _, _ in endpoints]
    if endpoint_numbers != [f"{number:02d}" for number in range(1, 30)]:
        errors.append("endpoint rows must be numbered 01..29 exactly once")

    examples = _examples(document)
    example_numbers = [number for number, _, _, _ in examples]
    if example_numbers != [f"{number:02d}" for number in range(1, 30)]:
        errors.append("example sections must be numbered 01..29 exactly once")

    for number, method, path in endpoints:
        matches = [example for example in examples if example[0] == number]
        if len(matches) != 1:
            continue
        _, example_method, example_path, section = matches[0]
        expected_method = method if method != "Repo" else path.split("（", 1)[0].strip()
        if example_method and example_method != expected_method:
            errors.append(f"endpoint {number} method differs from its example")
        route = path.split(" ", 1)[0].split("（", 1)[0]
        if method in {"GET", "POST"} and not _path_matches(route, example_path):
            errors.append(f"endpoint {number} path differs from its example")
        if not re.search(r"```json\s*\n.*?\n```", section, flags=re.S):
            errors.append(f"example {number} has no JSON block")

    try:
        blocks = _json_blocks(document)
    except (json.JSONDecodeError, ValueError) as error:
        errors.append(f"JSON parse failed: {error}")
        blocks = []

    for number, payload in blocks:
        for value in _walk(payload):
            coverage_keys = {"expected", "valid", "excluded", "missing"}
            if not coverage_keys.intersection(value):
                continue
            if not coverage_keys <= value.keys():
                errors.append(f"coverage in JSON block {number} lacks required fields")
                continue
            counts = tuple(
                value[key] for key in ("expected", "valid", "excluded", "missing")
            )
            if any(type(count) is not int or count < 0 for count in counts):
                errors.append(
                    f"coverage in JSON block {number} must use nonnegative integers"
                )
            elif counts[0] != sum(counts[1:]):
                errors.append(f"coverage identity failed in JSON block {number}")

    try:
        overview = _example_payload(examples, "01")["data"]
        breadth = overview["breadth"]
        if breadth["up"] + breadth["down"] + breadth["flat"] != breadth["valid_total"]:
            errors.append("breadth up+down+flat must equal valid_total")
        _ratio(errors, "seal_rate", breadth["seal_rate"])
        _ratio(errors, "promote_rate", overview["sentiment"]["promote_rate"])

        heatmap = _example_payload(examples, "04")["data"]
        heatmap_total = sum(
            cell["count"]
            for cell in heatmap["cells"]
            if cell["trade_date"] == overview.get("trade_date", "2026-09-18")
        )
        if heatmap_total != overview["sentiment"]["limit_up_count"]:
            errors.append("heatmap latest-day count must equal overview limit_up_count")

        ladder = _example_payload(examples, "05")["data"]
        if sum(cell["count"] for cell in ladder["cells"]) != ladder["total"]:
            errors.append("ladder cell counts must equal total")
        if any(cell["count"] != len(cell["stocks"]) for cell in ladder["cells"]):
            errors.append("ladder cell count must equal its stock list")
        if sorted(cell["boards"] for cell in ladder["cells"]) != sorted(
            ladder["columns"]
        ):
            errors.append("ladder boards must match columns")
        _ratio(errors, "promote_rate", ladder["promote_rate"])

        candidates = _example_payload(examples, "07")["data"]
        if candidates["total"] != len(candidates["candidates"]):
            errors.append("candidates total must equal candidate rows")
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        errors.append(f"example arithmetic could not be checked: {error}")

    return {
        "errors": errors,
        "req_count": len(reqs),
        "endpoint_count": len(endpoints),
        "example_count": len(examples),
        "json_count": len(blocks),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PRD)
    parser.add_argument("--stdin", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = (
            sys.stdin.read() if args.stdin else args.path.read_text(encoding="utf-8")
        )
        report = check_prd(document)
    except (OSError, UnicodeError) as error:
        print(f"Could not read PRD: {error}", file=sys.stderr)
        return 1
    if report["errors"]:
        print("\n".join(report["errors"]), file=sys.stderr)
        return 1
    print(
        f"REQ={report['req_count']} endpoints={report['endpoint_count']} "
        f"examples={report['example_count']} JSON={report['json_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
